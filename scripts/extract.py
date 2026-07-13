# extract.py
import fitz
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
import os
import re

pytesseract.pytesseract.tesseract_cmd = os.getenv("EXTRACT_TESSERACT")
POPPLER_PATH = os.getenv("EXTRACT_POPPLEER_PATH")

PDF_FOLDER = "pdfs"
TEXT_FOLDER = "texts"

os.makedirs(TEXT_FOLDER, exist_ok=True)

SCANNED_TEXT_THRESHOLD = 20
MIN_TABLE_ROWS = 2

# ----------------------------------------
# Helper: check if page is scanned
# ----------------------------------------
def is_scanned_page(page):
    return len(page.get_text().strip()) < SCANNED_TEXT_THRESHOLD

# ----------------------------------------
# Helper: OCR a single page
# ----------------------------------------
def ocr_page(pdf_path, page_number):
    images = convert_from_path(
        pdf_path,
        first_page=page_number + 1,
        last_page=page_number + 1,
        poppler_path=POPPLER_PATH,
        dpi=300
    )
    if not images:
        return ""
    return pytesseract.image_to_string(images[0], lang="eng")

# ----------------------------------------
# Helper: clean text
# ----------------------------------------
def clean_text(text):
    text = re.sub(r"\(cid:\d+\)", "•", text)
    text = re.sub(r"-\n(\w)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # kept rupee symbol so it doesn't get stripped to a stray "n"
    text = re.sub(r"[^\x20-\x7E\n•₹]", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()

# ----------------------------------------
# Helper: detect section heading from text
# searches from bottom up — finds nearest heading
# ----------------------------------------
def detect_section(text):
    lines = text.split("\n")
    for line in reversed(lines):
        line = line.strip()
        if re.match(r"^\d+\.\d+\s+\w+", line):
            return line
        if re.match(r"^\d+\.\s+[A-Z]", line):
            return line
        if line in (
            "FOREWORD", "PREAMBLE",
            "EXECUTIVE SUMMARY",
            "RECOMMENDATIONS",
            "WAY FORWARD",
            "RECOMMENDATIONS AND WAY FORWARD"
        ):
            return line
    return ""

# ----------------------------------------
# Helper: format table as readable text
# ----------------------------------------
def format_table(table, section_context=""):
    if not table or len(table) < MIN_TABLE_ROWS:
        return ""

    # validate — skip tables where header row is empty or single cell
    headers = [
        str(cell).strip() if cell else ""
        for cell in table[0]
    ]
    non_empty_headers = [h for h in headers if h]
    if len(non_empty_headers) < 2:
        return ""

    lines = []
    if section_context:
        lines.append(f"[Table from: {section_context}]")

    for row in table[1:]:
        parts = []
        for i, cell in enumerate(row):
            cell_text = str(cell).strip() if cell else ""
            if cell_text:
                header = headers[i] if i < len(headers) else f"Col{i}"
                parts.append(f"{header}: {cell_text}")
        if parts:
            lines.append(" | ".join(parts))

    # return nothing if only the label line was added
    if len(lines) <= 1:
        return ""

    return "\n".join(lines)

# ----------------------------------------
# Helper: get table bounding boxes
# ----------------------------------------
def get_table_bboxes(plumber_page):
    return [t.bbox for t in plumber_page.find_tables()]

# ----------------------------------------
# Helper: extract text excluding table areas
# preserving line breaks via word positions
# ----------------------------------------
def extract_text_without_tables(plumber_page):
    table_bboxes = get_table_bboxes(plumber_page)

    # use extract_text with layout preservation
    if not table_bboxes:
        return plumber_page.extract_text(
            x_tolerance=3,
            y_tolerance=3,
            layout=True  # preserves line breaks
        ) or ""

    words = plumber_page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False
    )

    # group words by their vertical position (line)
    lines = {}
    for word in words:
        # check if word is inside any table
        in_table = False
        for bbox in table_bboxes:
            x0, top, x1, bottom = bbox
            wx = (word["x0"] + word["x1"]) / 2
            wy = (word["top"] + word["bottom"]) / 2
            if x0 <= wx <= x1 and top <= wy <= bottom:
                in_table = True
                break

        if not in_table:
            # group by rounded vertical position
            line_key = round(word["top"], 0)
            if line_key not in lines:
                lines[line_key] = []
            lines[line_key].append(word["text"])

    # reconstruct text line by line
    sorted_lines = [
        " ".join(lines[k])
        for k in sorted(lines.keys())
    ]
    return "\n".join(sorted_lines)

# ----------------------------------------
# Helper: extract one digital page
# ----------------------------------------
def extract_page_pdfplumber(plumber_page, last_section=""):
    # text without tables
    page_text = extract_text_without_tables(plumber_page)

    # detect section from current page text first
    # then fall back to last known section
    current_section = detect_section(page_text) or last_section

    # extract and format tables
    tables = plumber_page.extract_tables()
    table_texts = []
    for table in tables:
        formatted = format_table(table, current_section)
        if formatted:
            table_texts.append(formatted)

    result = page_text.strip()
    if table_texts:
        result += "\n\n" + "\n\n".join(table_texts)

    return result, current_section

# ----------------------------------------
# Main extraction function for ONE pdf
# this is what pipeline.py will import and call
# ----------------------------------------
def extract_pdf(pdf_path, filename):
    """
    Extracts text from a single PDF (digital + scanned pages, tables included).
    Saves the result to TEXT_FOLDER and returns (text_path, text_filename).
    """
    print(f"\nProcessing: {filename}")

    fitz_doc = fitz.open(pdf_path)
    full_text = ""
    last_section = ""

    scanned_pages = []
    digital_pages = []

    for page_num in range(len(fitz_doc)):
        page = fitz_doc[page_num]
        if is_scanned_page(page):
            scanned_pages.append(page_num)
        else:
            digital_pages.append(page_num)

    print(f"  Digital pages : {len(digital_pages)}")
    print(f"  Scanned pages : {len(scanned_pages)}")

    with pdfplumber.open(pdf_path) as plumber_doc:

        for page_num in range(len(fitz_doc)):

            if page_num in scanned_pages:
                print(f"  Page {page_num + 1}: OCR")
                raw = ocr_page(pdf_path, page_num)
                cleaned = clean_text(raw)
                last_section = detect_section(cleaned) or last_section
                full_text += cleaned + "\n\n"

            else:
                print(f"  Page {page_num + 1}: Digital")
                plumber_page = plumber_doc.pages[page_num]
                page_text, last_section = extract_page_pdfplumber(
                    plumber_page,
                    last_section
                )
                full_text += page_text + "\n\n"

    fitz_doc.close()

    full_text = clean_text(full_text)

    text_filename = filename.replace(".pdf", ".txt")
    text_path = os.path.join(TEXT_FOLDER, text_filename)

    with open(text_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    print(f"  Saved -> {text_filename} ({len(full_text)} chars)")

    return text_path, text_filename

# ----------------------------------------
# Standalone mode — process every PDF in
# pdfs/ folder (same behaviour as before)
# ----------------------------------------
def extract_all():
    for filename in os.listdir(PDF_FOLDER):
        if not filename.endswith(".pdf"):
            continue
        pdf_path = os.path.join(PDF_FOLDER, filename)
        extract_pdf(pdf_path, filename)
    print("\nExtraction completed.")

if __name__ == "__main__":
    extract_all()