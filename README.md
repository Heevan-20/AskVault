### RAG-powered Chatbot | Built for NTPC Limited
Current Scope - Ask natural language questions over government policy documents and get accurate, sourced answers — fully offline, no external API calls, all data stays on your server.

Future Scope - Transform it into a "Enterprise Employee Knowledge System" ie users may query about any employee or group of employees, such that the Chatbot may have to return simple SQL query or have to go through employee pdfs ie RAG query or even a mix of both ie hybrid query

---

## Table of Contents

- What This Does
- Architecture
- Prerequisites
- Installation
- Configuration
- Project Structure
- How to Run
- API Reference
- How It Works
- Troubleshooting

---

## What This Does

This system lets users ask natural language questions over a collection of PDF documents and get accurate, grounded answers with source citations.

**Example:**
```
Question: "How are farmers protected from crop failure?"

Answer: "Farmers are protected through PM Fasal Bima Yojana (PMFBY),
which provides crop insurance coverage against yield losses due to
natural calamities, pests, and diseases. As of Kharif 2023, 2.61 crore
farmers were enrolled with claims of Rs. 15,910 crore paid."

Sources:
- MoAFW_KisanSchemes_AnnualReport_2024.txt — 2.2 PM Fasal Bima Yojana
```

**Key properties:**
- Works on **indirect and paraphrased questions** — not just keyword search
- **Fully offline** — embedding model and LLM run locally via Ollama
- **Auto-ingestion** — drop a PDF into a folder, it's searchable within seconds
- **Source citations** — every answer cites exactly which document and section it came from

---

## Architecture

```
PDF Documents
      ↓
pipeline.py  (file watcher — auto-detects new PDFs)
      ↓
extract.py   (converts PDF to clean text — handles digital + scanned)
      ↓
chunking.py  (splits text into section-based chunks)
      ↓
ingestion.py (embeds chunks using nomic-embed-text, stores in Oracle 23ai)
      ↓
Oracle 23ai  (vector store — pdf_chunks table)
      ↑
search.py    (hybrid vector + keyword search)
      ↓
augment.py   (builds structured prompt with context)
      ↓
generate.py  (sends prompt to Qwen via Ollama, returns answer)
      ↓
api.py       (Flask REST API — APEX frontend calls this)
```

---

## Prerequisites

### System Requirements
- Python 3.9+
- Oracle Database 23ai (with Vector support)
- Windows 10/11 or Linux
- Minimum 8GB RAM (16GB recommended)
- 10GB free disk space

### External Tools (must be installed separately)

**1. Ollama** — runs AI models locally
- Download: https://ollama.com/download
- After installing, pull the required models:
```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:1.5b
```

**2. Tesseract OCR** — for scanned PDF pages
- Windows: https://github.com/UB-Mannheim/tesseract/wiki
- Default install path: `C:\Program Files\Tesseract-OCR\`
- After installing, verify:
```bash
tesseract --version
```

**3. Poppler** — required by pdf2image for OCR
- Windows: https://github.com/oschwartz10612/poppler-windows/releases
- Download the latest release zip, extract it
- Note the path to the `bin` folder inside

---

## Installation

### Step 1 — Clone / download the project
```bash
cd C:\
mkdir Chatbot
cd Chatbot
# place all project files here
```

### Step 2 — Create and activate virtual environment
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### Step 3 — Install Python dependencies
```bash
pip install flask flask-cors python-dotenv oracledb ollama \
            pymupdf pdfplumber pytesseract pdf2image \
            nltk watchdog reportlab
```

### Step 4 — Download NLTK stopwords
```python
python -c "import nltk; nltk.download('stopwords')"
```

### Step 5 — Set up Oracle Database table
Run this SQL in Oracle SQL Developer or SQL*Plus:
```sql
CREATE TABLE pdf_chunks (
    id          NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename    VARCHAR2(200),
    chunk_text  CLOB,
    embedding   VECTOR(768, FLOAT32, DENSE),
    chunk_id    NUMBER,
    domain      VARCHAR2(50),
    section     VARCHAR2(200),
);

CREATE INDEX idx_pdf_chunks_status ON pdf_chunks(status);
```

---

## Configuration

Create a `.env` file in the project root with these values:

```env
# Oracle Database
DB_USER=your_oracle_username
DB_PASSWORD=your_oracle_password
DB_DSN=your_oracle_connection_string

# Flask API
API_HOST=0.0.0.0
API_PORT=5000
API_DEBUG=false

# Tesseract OCR path (Windows)
EXTRACT_TESSERACT=C:\Program Files\Tesseract-OCR\tesseract.exe

# Poppler path (Windows)
EXTRACT_POPPLEER_PATH=C:\path\to\poppler\Library\bin

# Chunking settings
CHUNK_MAX_SIZE=1000
CHUNK_OVERLAP=20
CHUNK_MIN_SIZE=100

# Generation settings
GENERATE_MIN_ANS_LEN=1
GENERATE_MAX_ANS_LEN=5000
```

> **Never commit `.env` to version control.** Add it to `.gitignore`.

---

## Project Structure

```
Chatbot/
│
├── scripts/                    # All Python source files
│   ├── extract.py              # PDF text extraction
│   ├── chunking.py             # Section-based document chunking
│   ├── ingestion.py            # Embedding + Oracle ingestion
│   ├── search.py               # Hybrid vector + keyword search
│   ├── augment.py              # Prompt building
│   ├── generate.py             # LLM answer generation
│   ├── pipeline.py             # Auto-ingestion file watcher
│   └── api.py                  # Flask REST API
│
├── pdfs/                       # Drop new PDFs here for auto-ingestion
│   ├── processed/              # Successfully ingested PDFs move here
│   └── failed/                 # Failed PDFs move here
│
├── texts/                      # Extracted text files (auto-created)
├── chunks/                     # Chunked text files (auto-created)
├── logs/                       # Log files (auto-created)
│   ├── pipeline.log
│   ├── ingestion.log
│   ├── search.log
│   ├── augment.log
│   ├── generation.log
│   └── api.log
│
├── .env                        # Your credentials (never commit this)
└── README.md                   # This file
```

---

## How to Run

### Option A — Automatic (recommended for production)

**Terminal 1 — Start Ollama**
```bash
ollama serve
```

**Terminal 2 — Start the pipeline watcher**
```bash
cd C:\Chatbot
venv\Scripts\activate
python scripts\pipeline.py
```
The watcher will:
- Process any PDFs already in `pdfs/` folder on startup
- Watch for new PDFs dropped into `pdfs/` folder
- Auto extract → chunk → ingest each one
- Move processed PDFs to `pdfs/processed/`

**Terminal 3 — Start the API**
```bash
cd C:\Chatbot
venv\Scripts\activate
python scripts\api.py
```

Now drop any PDF into the `pdfs/` folder — it will be searchable within seconds.

---

### Option B — Manual (step by step, useful for debugging)

Run each step individually:

```bash
# Step 1 — Extract text from PDFs
python scripts\extract.py

# Step 2 — Chunk the extracted text
python scripts\chunking.py

# Step 3 — Embed and ingest into Oracle
python scripts\ingestion.py

# Step 4 — Test search
python scripts\search.py

# Step 5 — Test augmentation
python scripts\augment.py

# Step 6 — Test full pipeline (search + augment + generate)
python scripts\generate.py

# Step 7 — Start the API
python scripts\api.py
```

---

## API Reference

Base URL: `http://localhost:5000`

### GET /health
Check if the API is running.

**Response:**
```json
{
  "status": "ok",
  "service": "RAG API"
}
```

---

### POST /ask
Ask a natural language question.

**Request:**
```json
{
  "question": "What is PM-KISAN?",
  "top_k": 5
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `question` | string | Yes | The question to ask |
| `top_k` | integer | No | Number of chunks to retrieve (1–20, default 5) |

**Success Response:**
```json
{
  "success": true,
  "answer": "PM-KISAN (Pradhan Mantri Kisan Samman Nidhi) provides income support of Rs. 6,000 per year...",
  "sources": [
    {
      "filename": "MoAFW_KisanSchemes_AnnualReport_2024.txt",
      "section": "2.1 PM-KISAN (Pradhan Mantri Kisan Samman Nidhi)"
    }
  ],
  "chunk_count": 3,
  "model": "qwen2.5:1.5b",
  "truncated": false
}
```

**Error Response:**
```json
{
  "success": false,
  "error": "'question' field is required and cannot be empty",
  "answer": null,
  "sources": []
}
```

**Test with PowerShell:**
```powershell
Invoke-WebRequest -Uri "http://localhost:5000/ask" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"question": "what is PM-KISAN", "top_k": 3}' `
  -UseBasicParsing | Select-Object -ExpandProperty Content
```

**Test with curl:**
```bash
curl -X POST http://localhost:5000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "what is PM-KISAN", "top_k": 3}'
```

---

## How It Works

### 1. PDF Extraction (`extract.py`)
- Detects each PDF page as digital or scanned
- Digital pages → extracted using `pdfplumber` with layout preservation
- Scanned pages → converted to images, then OCR'd using Tesseract at 300 DPI
- Tables extracted as structured `Header: Value` pairs with section context labels
- Bullet points, rupee symbols, and encoding artifacts cleaned automatically

### 2. Section-Based Chunking (`chunking.py`)
- Detects government document headings (e.g. `2.1 PM-KISAN`, `FOREWORD`)
- Each section becomes one chunk — no content bleeding across sections
- Sections too large are split carefully, never mid-table
- Very short sections (< 100 chars) merged with adjacent sections for better embeddings
- Every chunk tagged with: `DOCUMENT`, `CHUNK_ID`, `SECTION`

### 3. Embedding + Ingestion (`ingestion.py`)
- Each chunk embedded using `nomic-embed-text` (768-dimensional vectors) via Ollama
- Stored in Oracle 23ai's native vector store
- Deduplication prevents re-inserting existing chunks
- Field-name based chunk parsing (robust to format changes)

### 4. Hybrid Search (`search.py`)
- User question embedded using same `nomic-embed-text` model
- Oracle returns top-N chunks by cosine vector similarity
- Keyword scoring applied: meaningful question words checked against chunk text
- Scores normalized across result set so relevant chunks pull ahead of noise
- Final ranking: `70% vector similarity + 30% keyword score`

### 5. Augmentation (`augment.py`)
- Retrieved chunks validated and deduplicated
- Context budget managed (max 6,000 chars by default)
- Each chunk labeled with source: `[Source: filename — section]`
- System instructions added: answer only from context, cite sources, flag conflicts
- Returns structured prompt ready for LLM

### 6. Generation (`generate.py`)
- Augmented prompt sent to `qwen2.5:1.5b` via Ollama
- Retry logic: up to 3 attempts with 2-second delays
- Answer sanity checks: not empty, not excessively long
- Returns answer, model name, and success flag

---

## Tech Stack

| Component | Technology |
|---|---|
| PDF Extraction | PyMuPDF (fitz), pdfplumber |
| OCR | Tesseract, pdf2image |
| Embedding Model | nomic-embed-text (via Ollama) |
| Vector Database | Oracle 23ai |
| LLM | Qwen 2.5 1.5B (via Ollama) |
| API Framework | Flask |
| File Watching | watchdog |
| Oracle Driver | python-oracledb |

---

## Logs

All components write to `logs/` folder:

| Log File | What it tracks |
|---|---|
| `pipeline.log` | PDF detection, extraction, chunking, ingestion events |
| `ingestion.log` | Per-chunk insert/skip/fail counts |
| `search.log` | Every search query, candidate count, results returned |
| `augment.log` | Prompt building, context length, truncation events |
| `generation.log` | Model calls, retry attempts, answer length |
| `api.log` | Every API request, response status, errors |

---

## Built By

**Heevan Razdan**
AI Intern — NTPC Limited