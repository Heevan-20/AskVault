# chunk.py
import os
import re

TEXT_FOLDER = "texts"
CHUNK_FOLDER = "chunks"

os.makedirs(CHUNK_FOLDER, exist_ok=True)

MAX_CHUNK_SIZE = int(os.getenv("CHUNK_MAX_SIZE"))
OVERLAP_WORDS = int(os.getenv("CHUNK_OVERLAP"))
MIN_SECTION_LENGTH = int(os.getenv("CHUNK_MIN_SIZE"))  # chunks shorter than this get merged with the next one

# ----------------------------------------
# Heading pattern — matches both
# newline-separated AND inline headings
# e.g. "3.2 IFFCO Nano Urea" within a line
# ----------------------------------------
HEADING_PATTERN = re.compile(
    r"(?:^|\n)(\d+\.\d+\s+[A-Z][^\n]+|\d+\.\s+[A-Z][^\n]+)(?:\n|$)"
    r"|(?:^|\n)(FOREWORD|PREAMBLE|EXECUTIVE SUMMARY|INTRODUCTION|BACKGROUND"
    r"|RECOMMENDATIONS|WAY FORWARD|RECOMMENDATIONS AND WAY FORWARD)(?:\n|$)",
    re.MULTILINE
)

# ----------------------------------------
# Helper: check if text is a table row
# to avoid splitting mid-table
# ----------------------------------------
def is_table_line(line):
    return "|" in line and ":" in line

# ----------------------------------------
# Helper: split large section into chunks
# avoids splitting mid-table-row
# ----------------------------------------
def split_large_section(title, body, start_id):
    chunks = []
    lines = body.split("\n")
    current_lines = []
    current_len = 0
    part = 1

    for line in lines:
        line_len = len(line) + 1
        would_exceed = current_len + line_len > MAX_CHUNK_SIZE

        # never split mid-table — always finish the table block
        if would_exceed and current_lines and not is_table_line(line):
            chunk_text = f"{title}\n" + "\n".join(current_lines)
            chunks.append({
                "chunk_id": start_id + len(chunks),
                "title": f"{title} (part {part})",
                "text": chunk_text.strip()
            })

            # overlap — carry last N non-table lines
            overlap = [
                l for l in current_lines[-OVERLAP_WORDS:]
                if not is_table_line(l)
            ]
            current_lines = overlap
            current_len = sum(len(l) + 1 for l in current_lines)
            part += 1

        current_lines.append(line)
        current_len += line_len

    if current_lines:
        chunk_text = f"{title}\n" + "\n".join(current_lines)
        chunks.append({
            "chunk_id": start_id + len(chunks),
            "title": f"{title} (part {part})" if part > 1 else title,
            "text": chunk_text.strip()
        })

    return chunks

# ----------------------------------------
# Helper: preprocess text to ensure
# inline section headings get newlines
# so regex can detect them properly
# ----------------------------------------
def normalize_headings(text):
    # add newline before numbered headings that appear inline
    # e.g. "...some text. 3.2 Section Name..." → "...some text.\n3.2 Section Name..."
    text = re.sub(
        r"([.!?])\s+(\d+\.\d+\s+[A-Z])",
        r"\1\n\2",
        text
    )
    text = re.sub(
        r"([.!?])\s+(\d+\.\s+[A-Z])",
        r"\1\n\2",
        text
    )
    return text

# ----------------------------------------
# Helper: merge chunks that are too short
# to embed well on their own (e.g. one-line
# sections like "UPI transactions crossed 14
# billion per month.") into the next chunk
# ----------------------------------------
def merge_short_chunks(chunks):
    if not chunks:
        return chunks

    merged = [chunks[0]]

    for chunk in chunks[1:]:
        prev = merged[-1]
        # if previous chunk's body (excluding its own title line) is too short, merge forward
        prev_body_len = len(prev["text"]) - len(prev["title"])
        if prev_body_len < MIN_SECTION_LENGTH:
            prev["text"] += "\n\n" + chunk["text"]
            prev["title"] = f"{prev['title']} + {chunk['title']}"
        else:
            merged.append(chunk)

    return merged

# ----------------------------------------
# Helper: chunk text by section headings
# ----------------------------------------
def chunk_by_sections(text):
    chunks = []
    chunk_id = 1

    # normalize first so inline headings are detectable
    text = normalize_headings(text)

    matches = list(HEADING_PATTERN.finditer(text))

    if not matches:
        print("  WARNING: No headings detected, falling back to size-based chunking")
        words = text.split()
        current = []
        current_len = 0

        for word in words:
            current.append(word)
            current_len += len(word) + 1
            if current_len >= MAX_CHUNK_SIZE:
                chunks.append({
                    "chunk_id": chunk_id,
                    "title": f"Section {chunk_id}",
                    "text": " ".join(current).strip()
                })
                chunk_id += 1
                current = current[-OVERLAP_WORDS:]
                current_len = sum(len(w) + 1 for w in current)

        if current:
            chunks.append({
                "chunk_id": chunk_id,
                "title": f"Section {chunk_id}",
                "text": " ".join(current).strip()
            })

        return chunks

    # text before first heading = intro chunk
    intro = text[:matches[0].start()].strip()
    if intro:
        chunks.append({
            "chunk_id": chunk_id,
            "title": "Introduction",
            "text": intro
        })
        chunk_id += 1

    # process each section
    for i, match in enumerate(matches):
        # get the actual heading text from whichever group matched
        section_title = (match.group(1) or match.group(2) or "").strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_body = text[start:end].strip()

        if not section_body:
            continue

        full_section = f"{section_title}\n{section_body}"

        if len(full_section) <= MAX_CHUNK_SIZE:
            chunks.append({
                "chunk_id": chunk_id,
                "title": section_title,
                "text": full_section
            })
            chunk_id += 1
        else:
            sub_chunks = split_large_section(
                section_title,
                section_body,
                chunk_id
            )
            chunks.extend(sub_chunks)
            chunk_id += len(sub_chunks)

    # merge sections that are too thin to embed well on their own
    chunks = merge_short_chunks(chunks)

    # re-number chunk_ids sequentially after merge
    for i, chunk in enumerate(chunks, 1):
        chunk["chunk_id"] = i

    return chunks

# ----------------------------------------
# Save chunks for one document to disk
# ----------------------------------------
def save_chunks(chunks, filename):
    output_name = filename.replace(".txt", "_chunks.txt")
    output_path = os.path.join(CHUNK_FOLDER, output_name)

    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(f"===== CHUNK {chunk['chunk_id']} =====\n")
            f.write(f"DOCUMENT: {filename}\n")
            f.write(f"CHUNK_ID: {chunk['chunk_id']}\n")
            f.write(f"SECTION: {chunk['title']}\n\n")
            f.write(chunk["text"])
            f.write("\n\n")

    print(f"  -> {len(chunks)} chunks")
    return output_path

# ----------------------------------------
# Main chunking function for ONE text file
# this is what pipeline.py will import and call
# ----------------------------------------
def chunk_file(text_path, filename):
    """
    Chunks a single extracted .txt file by section.
    Saves the result to CHUNK_FOLDER and returns chunk_path.
    """
    with open(text_path, "r", encoding="utf-8") as f:
        text = f.read()

    print(f"\nChunking: {filename}")
    chunks = chunk_by_sections(text)
    chunk_path = save_chunks(chunks, filename)
    return chunk_path

# ----------------------------------------
# Standalone mode — chunk every .txt in
# texts/ folder (same behaviour as before)
# ----------------------------------------
def chunk_all():
    for filename in os.listdir(TEXT_FOLDER):
        if not filename.endswith(".txt"):
            continue
        text_path = os.path.join(TEXT_FOLDER, filename)
        chunk_file(text_path, filename)
    print("\nChunking completed.")

if __name__ == "__main__":
    chunk_all()