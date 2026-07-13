# augment.py
import os
import logging

# ----------------------------------------
# Logging
# ----------------------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/augment.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------------------
# Config
# ----------------------------------------
MAX_CONTEXT_CHARS = 6000

SYSTEM_INSTRUCTIONS = """You are an assistant answering questions using ONLY the context provided below, which comes from official government documents.

Rules:
- Answer only using information present in the context. Do not use outside knowledge.
- If the context does not contain enough information to answer the question, say so clearly — do not guess or fabricate details.
- When relevant, mention which document and section the information came from.
- Be concise and factual. Avoid repeating the context verbatim — summarize in your own words where possible.
- If multiple sources give conflicting information, point out the conflict rather than picking one silently."""

# ----------------------------------------
# Helper: validate a single retrieved chunk
# ----------------------------------------
def _is_valid_chunk(chunk):
    required_keys = ("filename", "section", "text")
    if not all(k in chunk for k in required_keys):
        return False
    if not chunk["text"] or not chunk["text"].strip():
        return False
    return True

# ----------------------------------------
# Helper: deduplicate chunks
# ----------------------------------------
def _deduplicate_chunks(chunks):
    seen = set()
    deduped = []
    for chunk in chunks:
        key = (chunk["filename"], chunk["section"], chunk["text"][:100])
        if key in seen:
            logger.debug(f"Skipping duplicate chunk: {chunk['filename']} - {chunk['section']}")
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped

# ----------------------------------------
# Helper: group chunks by document
# ----------------------------------------
def _group_by_document(chunks):
    grouped = {}
    order = []
    for chunk in chunks:
        doc = chunk["filename"]
        if doc not in grouped:
            grouped[doc] = []
            order.append(doc)
        grouped[doc].append(chunk)
    return [(doc, grouped[doc]) for doc in order]

# ----------------------------------------
# Helper: format one chunk as a labeled block
# ----------------------------------------
def _format_chunk(chunk):
    section = chunk["section"].strip() if chunk["section"] else "Unknown Section"
    return f"[Source: {chunk['filename']} — {section}]\n{chunk['text'].strip()}"

# ----------------------------------------
# Helper: trim chunks to fit within budget
# ----------------------------------------
def _trim_to_budget(chunks, max_chars):
    kept = []
    total_len = 0

    for chunk in chunks:
        block = _format_chunk(chunk)
        block_len = len(block) + 2

        if total_len + block_len > max_chars:
            if not kept:
                logger.warning("Single chunk exceeds context budget — truncating")
                available = max_chars - total_len - 50
                truncated_text = chunk["text"][:max(available, 0)].strip()
                truncated_chunk = dict(chunk)
                truncated_chunk["text"] = truncated_text + " [TRUNCATED]"
                kept.append(truncated_chunk)
            else:
                logger.info(f"Dropping {len(chunks) - len(kept)} lower-priority chunk(s) to stay within context budget")
            break

        kept.append(chunk)
        total_len += block_len

    return kept

# ----------------------------------------
# Main augmentation function
# ----------------------------------------
def build_prompt(question, retrieved_chunks, max_context_chars=MAX_CONTEXT_CHARS):
    """
    Builds a grounded, structured prompt for the generation step.
    """
    if not question or not question.strip():
        logger.error("Empty question passed to build_prompt")
        raise ValueError("Question cannot be empty")

    if not retrieved_chunks:
        logger.warning("No retrieved chunks passed — building a no-context prompt")
        prompt = (
            f"{SYSTEM_INSTRUCTIONS}\n\n"
            f"Context:\n(No relevant context was found for this question.)\n\n"
            f"Question: {question.strip()}\n"
            f"Answer:"
        )
        return {
            "prompt": prompt,
            "sources": [],
            "chunk_count": 0,
            "truncated": False
        }

    valid_chunks = [c for c in retrieved_chunks if _is_valid_chunk(c)]
    invalid_count = len(retrieved_chunks) - len(valid_chunks)
    if invalid_count > 0:
        logger.warning(f"Skipped {invalid_count} malformed chunk(s) missing required fields")

    if not valid_chunks:
        logger.error("No valid chunks after validation")
        prompt = (
            f"{SYSTEM_INSTRUCTIONS}\n\n"
            f"Context:\n(No relevant context was found for this question.)\n\n"
            f"Question: {question.strip()}\n"
            f"Answer:"
        )
        return {
            "prompt": prompt,
            "sources": [],
            "chunk_count": 0,
            "truncated": False
        }

    deduped_chunks = _deduplicate_chunks(valid_chunks)
    final_chunks = _trim_to_budget(deduped_chunks, max_context_chars)
    truncated = len(final_chunks) < len(deduped_chunks) or any("[TRUNCATED]" in c["text"] for c in final_chunks)

    grouped = _group_by_document(final_chunks)

    context_sections = []
    for doc, doc_chunks in grouped:
        for chunk in doc_chunks:
            context_sections.append(_format_chunk(chunk))

    context_block = "\n\n".join(context_sections)

    prompt = (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question.strip()}\n"
        f"Answer:"
    )

    sources = [(c["filename"], c["section"].strip() if c["section"] else "Unknown Section") for c in final_chunks]

    logger.info(
        f"Built prompt for question: '{question.strip()}' | "
        f"Chunks used: {len(final_chunks)} | "
        f"Context length: {len(context_block)} chars | "
        f"Truncated: {truncated}"
    )

    return {
        "prompt": prompt,
        "sources": sources,
        "chunk_count": len(final_chunks),
        "truncated": truncated
    }

# ----------------------------------------
# Standalone test mode
# runs REAL retrieval (via search.py's retrieve())
# then builds a prompt from the actual results
# no hardcoded data — every run reflects the
# current state of the database
# ----------------------------------------
if __name__ == "__main__":
    from search import retrieve

    question = input("Enter your question: ").strip()
    final_k = int(input("How many results to retrieve: "))

    top_results = retrieve(question, final_k)

    if not top_results:
        print("\nNo results retrieved for this question.")
    else:
        result = build_prompt(question, top_results)

        print("\n===== GENERATED PROMPT =====\n")
        print(result["prompt"])
        print("\n===== METADATA =====")
        print("Sources used:", result["sources"])
        print("Chunk count:", result["chunk_count"])
        print("Truncated:", result["truncated"])