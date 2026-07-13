# api.py
import os
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_cors import CORS

from search import retrieve
from augment import build_prompt
from generate import generate_answer

# -----------------------------
# Logging
# -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------
# Load env
# -----------------------------
load_dotenv()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "5000"))
API_DEBUG = os.getenv("API_DEBUG", "false").lower() == "true"

# -----------------------------
# Flask app
# -----------------------------
app = Flask(__name__)
CORS(app)

# -----------------------------
# Helper: standard error response
# -----------------------------
def error_response(message, status_code=400):
    logger.warning(f"Error response {status_code}: {message}")
    return jsonify({
        "success": False,
        "error": message,
        "answer": None,
        "sources": []
    }), status_code

# -----------------------------
# Health check endpoint
# lets APEX / ops team verify API is alive
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    logger.info("Health check called")
    return jsonify({
        "status": "ok",
        "service": "RAG API"
    }), 200

# -----------------------------
# Main Q&A endpoint
# APEX will call this with a question
# -----------------------------
@app.route("/ask", methods=["POST"])
def ask():
    # -----------------------------
    # Parse and validate request
    # -----------------------------
    data = request.get_json(silent=True)

    if not data:
        return error_response("Request body must be valid JSON")

    question = data.get("question", "").strip()
    top_k = data.get("top_k", 5)

    if not question:
        return error_response("'question' field is required and cannot be empty")

    if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
        return error_response("'top_k' must be an integer between 1 and 20")

    logger.info(f"Received question: '{question}' | top_k: {top_k}")

    # -----------------------------
    # Step 1 — Retrieve
    # -----------------------------
    try:
        top_results = retrieve(question, top_k)
    except Exception as e:
        logger.error(f"Retrieval failed: {e}")
        return error_response("Retrieval step failed — please try again", 500)

    if not top_results:
        logger.warning(f"No results found for: '{question}'")
        return jsonify({
            "success": True,
            "answer": "I could not find any relevant information in the documents for your question.",
            "sources": [],
            "chunk_count": 0
        }), 200

    # -----------------------------
    # Step 2 — Augment
    # -----------------------------
    try:
        augmented = build_prompt(question, top_results)
    except Exception as e:
        logger.error(f"Augmentation failed: {e}")
        return error_response("Augmentation step failed — please try again", 500)

    # -----------------------------
    # Step 3 — Generate
    # -----------------------------
    try:
        result = generate_answer(augmented["prompt"])
    except Exception as e:
        logger.error(f"Generation failed: {e}")
        return error_response("Generation step failed — please try again", 500)

    if not result["success"]:
        logger.error(f"Generation returned failure for: '{question}'")
        return error_response("Model failed to generate a response — please try again", 500)

    # -----------------------------
    # Build response
    # -----------------------------
    sources = [
        {"filename": filename, "section": section}
        for filename, section in augmented["sources"]
    ]

    response = {
        "success": True,
        "answer": result["answer"],
        "sources": sources,
        "chunk_count": augmented["chunk_count"],
        "model": result["model"],
        "truncated": augmented["truncated"]
    }

    logger.info(f"Successfully answered: '{question}' | Sources: {len(sources)}")

    return jsonify(response), 200

# -----------------------------
# 404 handler
# -----------------------------
@app.errorhandler(404)
def not_found(e):
    return error_response("Endpoint not found", 404)

# -----------------------------
# 405 handler
# -----------------------------
@app.errorhandler(405)
def method_not_allowed(e):
    return error_response("Method not allowed on this endpoint", 405)

# -----------------------------
# 500 handler
# -----------------------------
@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Unhandled server error: {e}")
    return error_response("Internal server error", 500)

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    logger.info(f"Starting RAG API on {API_HOST}:{API_PORT}")
    app.run(
        host=API_HOST,
        port=API_PORT,
        debug=API_DEBUG
    )