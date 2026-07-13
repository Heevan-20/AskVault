# generation.py
import os
import time
import logging
import ollama

# -----------------------------
# Logging
# -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/generation.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------
# Config
# -----------------------------
MODEL_NAME = "qwen2.5:1.5b"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 60
MIN_ANSWER_LENGTH = int(os.getenv("GENERATE_MIN_ANS_LEN"))
MAX_ANSWER_LENGTH = int(os.getenv("GENERATE_MAX_ANS_LEN"))  # sanity cap — flags suspiciously long outputs

# -----------------------------
# Main generation function
# this is what an API layer (later) will import and call
# -----------------------------
def generate_answer(prompt, model=MODEL_NAME, think=False):
    """
    Sends a prompt to Qwen via Ollama and returns the generated answer.

    Args:
        prompt: the full augmented prompt string (from augment.py's build_prompt)
        model: Ollama model name to use
        think: whether to enable Qwen's internal reasoning mode
               (False = faster, recommended for RAG Q&A)
               Falls back automatically if the installed ollama client
               doesn't support this parameter.

    Returns:
        dict with:
            "answer": the generated text (str)
            "model": model name used
            "success": bool
    """
    if not prompt or not prompt.strip():
        logger.error("Empty prompt passed to generate_answer")
        raise ValueError("Prompt cannot be empty")

    logger.debug(f"Prompt sent to model (first 300 chars): {prompt[:300]}...")

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Generation attempt {attempt}/{MAX_RETRIES} using model: {model}")

            # try with `think` param first; some ollama client versions
            # don't support it, so fall back gracefully if it errors
            try:
                response = ollama.chat(
                    model=model,
                    think=think,
                    messages=[{"role": "user", "content": prompt}],
                    options={"timeout": REQUEST_TIMEOUT_SECONDS}
                )
            except TypeError:
                logger.warning("'think' parameter not supported by this ollama client — retrying without it")
                response = ollama.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"timeout": REQUEST_TIMEOUT_SECONDS}
                )

            answer = response["message"]["content"].strip()

            # sanity checks
            if len(answer) < MIN_ANSWER_LENGTH:
                logger.warning("Model returned an empty or near-empty answer")
                raise ValueError("Empty answer received from model")

            if len(answer) > MAX_ANSWER_LENGTH:
                logger.warning(f"Answer unusually long ({len(answer)} chars) — truncating for safety")
                answer = answer[:MAX_ANSWER_LENGTH] + " [TRUNCATED]"

            logger.info(f"Generation successful — answer length: {len(answer)} chars")

            return {
                "answer": answer,
                "model": model,
                "success": True
            }

        except Exception as e:
            last_error = e
            logger.warning(f"Generation attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    logger.error(f"Generation failed after {MAX_RETRIES} attempts: {last_error}")
    return {
        "answer": "Sorry, I was unable to generate a response right now. Please try again.",
        "model": model,
        "success": False
    }

# -----------------------------
# Standalone test mode
# runs the FULL pipeline: retrieve -> augment -> generate
# no hardcoded data, every run reflects the live DB
# -----------------------------
if __name__ == "__main__":
    from search import retrieve
    from augment import build_prompt

    question = input("Enter your question: ").strip()
    final_k = int(input("How many chunks to retrieve: "))

    print("\nRetrieving relevant chunks...")
    top_results = retrieve(question, final_k)

    if not top_results:
        print("No relevant chunks found in the database.")
    else:
        print(f"Retrieved {len(top_results)} chunks.")

        print("Building augmented prompt...")
        augmented = build_prompt(question, top_results)

        print(f"Generating answer using {MODEL_NAME}...")
        result = generate_answer(augmented["prompt"])

        print("\n===== ANSWER =====\n")
        print(result["answer"])

        print("\n===== SOURCES =====")
        for filename, section in augmented["sources"]:
            print(f"- {filename} — {section}")

        print(f"\nModel: {result['model']} | Success: {result['success']}")