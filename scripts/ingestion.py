# ingestion.py
import os
import array
import logging
import oracledb
import ollama
from dotenv import load_dotenv

# -----------------------------
# Logging setup
# -----------------------------
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/ingestion.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------
# Load credentials from .env
# -----------------------------
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")

if not all([DB_USER, DB_PASSWORD, DB_DSN]):
    logger.error("Missing DB credentials in .env file")
    raise EnvironmentError("Missing DB credentials")

CHUNK_FOLDER = "chunks"

# -----------------------------
# Embedding function with error handling
# -----------------------------
def get_embedding(text):
    try:
        response = ollama.embed(
            model="nomic-embed-text",
            input=text
        )
        return response["embeddings"][0]
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise

# -----------------------------
# Parse a chunk file robustly
# uses field names not line positions
# -----------------------------
def parse_chunks(content):
    chunks = []
    pieces = content.split("===== CHUNK")[1:]

    for piece in pieces:
        try:
            lines = piece.strip().split("\n")

            # parse by field name, not position
            fields = {}
            text_lines = []
            in_text = False

            for line in lines:
                if line.startswith("DOCUMENT:"):
                    fields["document"] = line.replace("DOCUMENT:", "").strip()
                elif line.startswith("CHUNK_ID:"):
                    fields["chunk_id"] = int(line.replace("CHUNK_ID:", "").strip())
                elif line.startswith("SECTION:"):
                    fields["section"] = line.replace("SECTION:", "").strip()
                    in_text = False
                elif in_text:
                    text_lines.append(line)
                elif line.strip() == "" and "chunk_id" in fields:
                    # blank line after headers = text starts
                    in_text = True

            fields["chunk_text"] = "\n".join(text_lines).strip()

            if "document" not in fields or "chunk_id" not in fields:
                logger.warning(f"Skipping malformed chunk: {piece[:50]}")
                continue

            if not fields["chunk_text"]:
                logger.warning(f"Skipping empty chunk: {fields.get('document')} - {fields.get('chunk_id')}")
                continue

            chunks.append(fields)

        except Exception as e:
            logger.warning(f"Failed to parse chunk: {e}")
            continue

    return chunks

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
# Ingest ONE chunk file into the DB
# this is what pipeline.py will import and call
# -----------------------------
def ingest_file(chunk_path, connection=None):
    """
    Ingests a single chunk file (output of chunk_file()) into pdf_chunks.
    If a connection is passed in, reuses it (caller commits/closes).
    If not, opens, commits, and closes its own connection.
    Returns (inserted, skipped, failed) counts.
    """
    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()
        logger.info("DB connection established")

    try:
        cursor = connection.cursor()

        # load existing for deduplication
        cursor.execute("SELECT filename, chunk_id FROM pdf_chunks")
        existing = set((r[0], r[1]) for r in cursor.fetchall())

        filename = os.path.basename(chunk_path)
        logger.info(f"Processing {filename}")

        with open(chunk_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = parse_chunks(content)

        inserted = 0
        skipped = 0
        failed = 0

        for chunk in chunks:

            document = chunk["document"]
            chunk_id = chunk["chunk_id"]
            section = chunk.get("section", "")
            chunk_text = chunk["chunk_text"]

            # deduplication
            if (document, chunk_id) in existing:
                logger.debug(f"Skipping duplicate: {document} - {chunk_id}")
                skipped += 1
                continue

            try:
                # embed
                embedding = get_embedding(chunk_text)
                vector = array.array("f", embedding)

                # insert — now also stores section
                cursor.execute(
                    """
                    INSERT INTO pdf_chunks
                    (filename, chunk_text, embedding, chunk_id, section)
                    VALUES (:1, :2, :3, :4, :5)
                    """,
                    [document, chunk_text, vector, chunk_id, section]
                )

                existing.add((document, chunk_id))
                inserted += 1
                logger.info(f"Inserted: {document} - Chunk {chunk_id} - {section}")

            except Exception as e:
                logger.error(f"Failed to insert {document} chunk {chunk_id}: {e}")
                failed += 1
                continue  # skip this chunk, continue with others

        if owns_connection:
            connection.commit()

        logger.info(f"Ingestion complete for {filename} — Inserted: {inserted} | Skipped: {skipped} | Failed: {failed}")
        return inserted, skipped, failed

    except Exception as e:
        logger.error(f"Fatal error during ingestion: {e}")
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()
            logger.info("DB connection closed")

# -----------------------------
# Standalone mode — ingest every
# chunk file in chunks/ folder
# (same behaviour as before, single
# connection + single commit for all files)
# -----------------------------
def ingest_all():
    connection = None
    try:
        connection = get_connection()
        logger.info("DB connection established")

        total_inserted = 0
        total_skipped = 0
        total_failed = 0

        for filename in os.listdir(CHUNK_FOLDER):
            if not filename.endswith(".txt"):
                continue
            chunk_path = os.path.join(CHUNK_FOLDER, filename)
            inserted, skipped, failed = ingest_file(chunk_path, connection=connection)
            total_inserted += inserted
            total_skipped += skipped
            total_failed += failed

        connection.commit()
        logger.info(
            f"\nAll files ingested — Inserted: {total_inserted} | "
            f"Skipped: {total_skipped} | Failed: {total_failed}"
        )

    except Exception as e:
        logger.error(f"Fatal error during ingestion: {e}")
        if connection:
            connection.rollback()
        raise

    finally:
        if connection:
            connection.close()
            logger.info("DB connection closed")

if __name__ == "__main__":
    ingest_all()