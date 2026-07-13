# search.py
import os
import array
import logging
import re
import oracledb
import ollama
from nltk.corpus import stopwords
from dotenv import load_dotenv

# -----------------------------
# Logging
# -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/search.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------
# Load credentials
# -----------------------------
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")

if not all([DB_USER, DB_PASSWORD, DB_DSN]):
    logger.error("Missing DB credentials in .env file")
    raise EnvironmentError("Missing DB credentials")

# -----------------------------
# Config
# -----------------------------
stop_words = set(stopwords.words("english"))

# -----------------------------
# Get a DB connection
# -----------------------------
def get_connection():
    return oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=DB_DSN
    )

# -----------------------------
# Main retrieval function
# this is what augment.py (and later, an API) will import and call
# -----------------------------
def retrieve(question, final_k=5):
    """
    Runs hybrid (vector + keyword) search against pdf_chunks.
    Uses normalized vector distances so genuinely relevant chunks
    pull clearly ahead of generic/noise chunks (headers, forewords, etc).

    Args:
        question: the user's question (str)
        final_k: number of top results to return

    Returns:
        list of dicts, each with:
            filename, chunk_id, section, text,
            vector, keyword, hybrid (scores)
        sorted by hybrid score descending, highest first.
        Returns [] if no results or on empty question.
    """
    question = (question or "").strip()

    if not question:
        logger.error("Empty question provided")
        raise ValueError("Question cannot be empty")

    candidate_k = max(10, final_k * 3)

    logger.info(f"Question: {question}")
    logger.info(f"Requested results: {final_k} | Candidate pool: {candidate_k}")

    connection = None

    try:
        # -----------------------------
        # Embed question
        # -----------------------------
        try:
            response = ollama.embed(
                model="nomic-embed-text",
                input=question
            )
            query_vector = array.array("f", response["embeddings"][0])
            logger.info("Embedding generated successfully")
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise

        # -----------------------------
        # DB connection
        # -----------------------------
        connection = get_connection()
        cursor = connection.cursor()
        logger.info("DB connection established")

        # -----------------------------
        # Vector search
        # -----------------------------
        cursor.execute(
            f"""
            SELECT
                filename,
                chunk_id,
                section,
                chunk_text,
                VECTOR_DISTANCE(
                    embedding,
                    :1,
                    COSINE
                ) AS distance
            FROM pdf_chunks
            WHERE DBMS_LOB.GETLENGTH(chunk_text) >= 150
            ORDER BY distance
            FETCH FIRST {candidate_k} ROWS ONLY
            """,
            [query_vector]
        )

        rows = cursor.fetchall()
        logger.info(f"Retrieved {len(rows)} candidates from DB")

        # -----------------------------
        # Keyword extraction
        # -----------------------------
        question_words = [
            re.sub(r"[^\w\-]", "", word)
            for word in question.lower().split()
        ]
        question_words = [
            word for word in question_words
            if word and word not in stop_words
        ]

        if not question_words:
            logger.warning("No meaningful keywords after stopword removal — using vector only")

        # -----------------------------
        # Collect raw results first (before scoring)
        # -----------------------------
        raw_results = []

        for row in rows:
            filename = row[0]
            chunk_id = row[1]
            section = row[2] or ""
            chunk_text = row[3].read()
            distance = row[4]

            raw_results.append({
                "filename": filename,
                "chunk_id": chunk_id,
                "section": section,
                "text": chunk_text,
                "distance": distance
            })

        if not raw_results:
            logger.warning("No results returned for query")
            return []

        # -----------------------------
        # Normalize distances across this result set
        # so genuinely relevant chunks pull clearly ahead
        # of generic/noise chunks (headers, forewords, etc)
        # -----------------------------
        distances = [r["distance"] for r in raw_results]
        min_d = min(distances)
        max_d = max(distances)
        d_range = max_d - min_d if max_d != min_d else 1.0

        # -----------------------------
        # Hybrid scoring (normalized vector + keyword)
        # -----------------------------
        results = []

        for r in raw_results:
            normalized_distance = (r["distance"] - min_d) / d_range
            vector_similarity = 1 - normalized_distance

            text_lower = r["text"].lower()

            if question_words:
                keyword_hits = sum(
                    1 for word in question_words
                    if word in text_lower
                )
                keyword_score = keyword_hits / len(question_words)
            else:
                keyword_score = 0

            hybrid_score = (
                0.7 * vector_similarity +
                0.3 * keyword_score
            )

            results.append({
                "filename": r["filename"],
                "chunk_id": r["chunk_id"],
                "section": r["section"],
                "text": r["text"],
                "vector": vector_similarity,
                "keyword": keyword_score,
                "hybrid": hybrid_score
            })

        results.sort(key=lambda x: x["hybrid"], reverse=True)

        top_results = results[:final_k]

        logger.info(f"Returned {len(top_results)} results for: '{question}'")

        return top_results

    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise

    finally:
        if connection:
            connection.close()
            logger.info("DB connection closed")

# -----------------------------
# Standalone mode — same behaviour as before:
# ask for input, run retrieve(), print results
# -----------------------------
def run_interactive():
    question = input("Enter your question: ").strip()
    final_k = int(input("How many results: "))

    top_results = retrieve(question, final_k)

    if not top_results:
        print("No results found.")
    else:
        for i, result in enumerate(top_results, 1):
            print(f"\n===== RESULT {i} =====")
            print("Document:", result["filename"])
            print("Section: ", result["section"])
            print("Chunk ID:", result["chunk_id"])
            print("Vector Score:", round(result["vector"], 4))
            print("Keyword Score:", round(result["keyword"], 4))
            print("Hybrid Score:", round(result["hybrid"], 4))
            print("\nText:\n")
            print(result["text"])
            print("-" * 50)

if __name__ == "__main__":
    run_interactive()