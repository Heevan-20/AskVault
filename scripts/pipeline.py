# pipeline.py
import os
import time
import shutil
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from extract import extract_pdf
from chunking import chunk_file
from ingestion import ingest_file, get_connection

# ----------------------------------------
# Logging
# ----------------------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------------------
# Paths
# ----------------------------------------
PDF_FOLDER = "pdfs"
PROCESSED_FOLDER = "pdfs/processed"
FAILED_FOLDER = "pdfs/failed"

os.makedirs(PROCESSED_FOLDER, exist_ok=True)
os.makedirs(FAILED_FOLDER, exist_ok=True)

# ----------------------------------------
# Check if a PDF's text is already ingested
# (avoids reprocessing on restart)
# ----------------------------------------
def is_already_ingested(text_filename, connection):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM pdf_chunks WHERE filename = :1",
        [text_filename]
    )
    count = cursor.fetchone()[0]
    return count > 0

# ----------------------------------------
# Full pipeline for ONE pdf:
# extract -> chunk -> ingest
# ----------------------------------------
def run_pipeline(pdf_path, filename):
    logger.info(f"\n{'='*50}")
    logger.info(f"NEW PDF DETECTED: {filename}")
    logger.info(f"{'='*50}")

    connection = None
    try:
        text_filename_check = filename.replace(".pdf", ".txt")
        connection = get_connection()
        logger.info("DB connection established")

        # skip if already in DB
        if is_already_ingested(text_filename_check, connection):
            logger.info(f"Already ingested, skipping: {filename}")
            processed_path = os.path.join(PROCESSED_FOLDER, filename)
            if os.path.exists(pdf_path):
                shutil.move(pdf_path, processed_path)
            return

        # Step 1 — Extract (from extract.py)
        text_path, text_filename = extract_pdf(pdf_path, filename)

        # Step 2 — Chunk (from chunk.py)
        chunk_path = chunk_file(text_path, text_filename)

        # Step 3 — Ingest (from ingestion.py)
        # reuse the same connection so this PDF's ingestion
        # commits as one transaction
        inserted, skipped, failed = ingest_file(chunk_path, connection=connection)
        connection.commit()
        logger.info(f"Ingested — Inserted: {inserted} | Skipped: {skipped} | Failed: {failed}")

        # Step 4 — Move to processed
        processed_path = os.path.join(PROCESSED_FOLDER, filename)
        shutil.move(pdf_path, processed_path)
        logger.info(f"PDF moved to processed: {processed_path}")

        logger.info(f"Pipeline complete for: {filename}")

    except Exception as e:
        logger.error(f"Pipeline failed for {filename}: {e}")
        if connection:
            connection.rollback()
        # move to failed folder so it doesn't loop forever
        try:
            if os.path.exists(pdf_path):
                failed_path = os.path.join(FAILED_FOLDER, filename)
                shutil.move(pdf_path, failed_path)
                logger.error(f"PDF moved to failed folder: {filename}")
        except Exception as move_error:
            logger.error(f"Could not move failed PDF: {move_error}")

    finally:
        if connection:
            connection.close()
            logger.info("DB connection closed")

# ----------------------------------------
# File watcher
# ----------------------------------------
class PDFHandler(FileSystemEventHandler):

    def __init__(self):
        self.processing = set()
        self.completed = set()

    def _handle_pdf(self, src_path):
        if not src_path.lower().endswith(".pdf"):
            return
        if src_path in self.processing or src_path in self.completed:
            return

        filename = os.path.basename(src_path)

        # wait briefly to ensure file is fully written to disk
        time.sleep(2)

        if not os.path.exists(src_path):
            return

        self.processing.add(src_path)
        logger.info(f"File detected: {filename}")

        try:
            run_pipeline(src_path, filename)
            self.completed.add(src_path)
        finally:
            self.processing.discard(src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_pdf(event.src_path)

    def on_modified(self, event):
        # Windows sometimes fires "modified" instead of "created"
        # when a file is copied/moved into the folder
        if event.is_directory:
            return
        self._handle_pdf(event.src_path)

# ----------------------------------------
# Main
# ----------------------------------------
if __name__ == "__main__":
    logger.info("Pipeline watcher started")
    logger.info(f"Watching folder: {os.path.abspath(PDF_FOLDER)}")

    # process any PDFs already sitting in folder on startup
    existing_pdfs = [f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf")]
    if existing_pdfs:
        logger.info(f"Found {len(existing_pdfs)} existing PDFs — processing now")
        for filename in existing_pdfs:
            pdf_path = os.path.join(PDF_FOLDER, filename)
            run_pipeline(pdf_path, filename)
    else:
        logger.info("No existing PDFs found in pdfs/ folder")

    # start watching for new PDFs
    handler = PDFHandler()
    observer = Observer()
    observer.schedule(handler, PDF_FOLDER, recursive=False)
    observer.start()

    logger.info("Watcher running — drop PDFs into the pdfs/ folder to process automatically")
    logger.info("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watcher...")
        observer.stop()

    observer.join()
    logger.info("Watcher stopped")