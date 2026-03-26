"""
WAT Layer 2 - API: KYC PoA Matcher Backend
===========================================
FastAPI server powered by Anthropic Claude (claude-haiku-4-5-20251001).

Endpoints:
  POST /verify-ocr   — primary. Single-customer CSV + N document files.
                       OCR each doc via Claude Vision, then run PoA matching.
                       Streams results as Server-Sent Events.

  POST /verify       — legacy. CSV with pre-filled OCR_Extracted_Text column.

Run with:
    source .env && uvicorn api.main:app --reload --port 8000
"""

import os
import sys
import json
import time
import csv
import io
import asyncio
from typing import List

import anthropic
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from tools.ocr_tools import extract_document_fields

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL          = "claude-haiku-4-5-20251001"
MAX_TOKENS     = 600
WORKFLOW_FILE  = os.path.join(PROJECT_ROOT, "workflows", "poa_matcher.md")
MAX_RETRIES    = 3
RETRY_BACKOFF  = 10   # seconds

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="KYC PoA Matcher API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Load SOP once at startup
# ---------------------------------------------------------------------------
def _load_system_prompt() -> str:
    with open(WORKFLOW_FILE, encoding="utf-8") as f:
        return f.read()

try:
    SYSTEM_PROMPT = _load_system_prompt()
except FileNotFoundError:
    SYSTEM_PROMPT = ""
    print(f"[api] WARNING: Could not load {WORKFLOW_FILE}")

# ---------------------------------------------------------------------------
# Helper: get field supporting multiple column name conventions
# ---------------------------------------------------------------------------
def _get(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k, "")
        if v:
            return v
    return ""

# ---------------------------------------------------------------------------
# Message builder — includes all customer fields + OCR result
# ---------------------------------------------------------------------------
def _build_user_message(row: dict, ocr: dict, doc_filename: str) -> str:
    return (
        f"Customer ID: {row['Customer_ID']}\n"
        f"Registered Name: {row['Registered_Name']}\n"
        f"Registered Address: {row['Registered_Address']}\n"
        f"Phone: {_get(row, 'Phone', 'Phone_Number')}\n"
        f"Email: {_get(row, 'Email', 'Email_Address')}\n"
        f"Date of Birth: {_get(row, 'Date_of_Birth')}\n"
        f"IPs: {_get(row, 'IPs', 'IP_Address', 'IP_Addresses')}\n"
        f"\nOCR Extracted Text:\n"
        f"Name: {ocr.get('name', '')}\n"
        f"Address: {ocr.get('address', '')}\n"
        f"Issue Date: {ocr.get('issue_date', '')}\n"
        f"Document: {doc_filename}"
    )

# ---------------------------------------------------------------------------
# Claude matcher call
# ---------------------------------------------------------------------------
def _call_claude(client: anthropic.Anthropic, user_message: str) -> dict:
    """Send one KYC verification request to Claude. Returns parsed dict."""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                lines = raw.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            return json.loads(raw)

        except json.JSONDecodeError:
            return {"decision": "ERROR", "reasoning": "Model returned non-JSON output."}

        except anthropic.RateLimitError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue
            return {"decision": "ERROR", "reasoning": "Rate limit hit. Try again in a moment."}

        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                return {"decision": "ERROR", "reasoning": f"API error: {str(e)[:120]}"}
            time.sleep(RETRY_BACKOFF)

    return {"decision": "ERROR", "reasoning": "Unexpected failure."}

# ---------------------------------------------------------------------------
# CSV parsers
# ---------------------------------------------------------------------------
REQUIRED_COLS_OCR    = {"Customer_ID", "Registered_Name", "Registered_Address"}
REQUIRED_COLS_LEGACY = {"Customer_ID", "Registered_Name", "Registered_Address", "OCR_Extracted_Text"}

def _parse_csv(content: bytes, required: set) -> list:
    text = content.decode("utf-8")
    # Auto-detect delimiter: use ; if it appears more on header line than ,
    first_line = text.split("\n")[0]
    delim = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise ValueError("CSV is empty or has no header.")
    # Strip BOM / whitespace from field names
    reader.fieldnames = [f.strip().lstrip("\ufeff") for f in reader.fieldnames]
    missing = required - set(reader.fieldnames)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    return [{k: (v.strip() if v else "") for k, v in row.items()} for row in reader]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "api_key_set": api_key_set,
        "sop_loaded": bool(SYSTEM_PROMPT),
        "model": MODEL,
        "provider": "Anthropic Claude",
    }


@app.post("/verify-ocr")
async def verify_ocr(
    csv_file: UploadFile = File(...),
    documents: List[UploadFile] = File(...),
):
    """
    Primary endpoint.

    Accepts:
      csv_file   — single-customer CSV (required: Customer_ID, Registered_Name, Registered_Address)
      documents  — one or more PoA document files (jpg/png/pdf/json)

    For each document:
      1. OCR via Claude Vision → extract name, address, issue_date
      2. Match via Claude + poa_matcher.md → decision + reasoning with jurisdiction check
      3. Stream SSE event
    """
    if not csv_file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="csv_file must be a .csv file.")

    csv_content = await csv_file.read()
    try:
        rows = _parse_csv(csv_content, REQUIRED_COLS_OCR)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not rows:
        raise HTTPException(status_code=422, detail="CSV has no data rows.")

    customer = rows[0]  # single-customer flow

    if not documents:
        raise HTTPException(status_code=422, detail="At least one document file is required.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set in .env")

    # Read all document bytes before entering the async generator
    doc_data = []
    for doc in documents:
        file_bytes = await doc.read()
        doc_data.append({"filename": doc.filename, "bytes": file_bytes})

    total = len(doc_data)
    client = anthropic.Anthropic(api_key=api_key)

    async def event_stream():
        for i, doc in enumerate(doc_data, start=1):
            filename   = doc["filename"]
            file_bytes = doc["bytes"]

            # Step 1: OCR
            ocr_result = await asyncio.get_event_loop().run_in_executor(
                None, extract_document_fields, file_bytes, filename, client
            )

            # Step 2: Match
            if ocr_result.get("ocr_error"):
                match_result = {
                    "decision": "ERROR",
                    "reasoning": f"OCR failed: {ocr_result['ocr_error']}",
                }
            else:
                user_msg = _build_user_message(customer, ocr_result, filename)
                match_result = await asyncio.get_event_loop().run_in_executor(
                    None, _call_claude, client, user_msg
                )

            event_data = {
                "index":                 i,
                "total":                 total,
                "doc_filename":          filename,
                "row":                   customer,
                "decision":              match_result.get("decision", "ERROR"),
                "reasoning":             match_result.get("reasoning", ""),
                "name_match":            match_result.get("name_match", ""),
                "address_match":         match_result.get("address_match", ""),
                "ocr_name_extracted":    ocr_result.get("name", ""),
                "ocr_address_extracted": ocr_result.get("address", ""),
                "ocr_issue_date":        ocr_result.get("issue_date", ""),
                "ocr_error":             ocr_result.get("ocr_error"),
            }

            yield f"data: {json.dumps(event_data)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/verify")
async def verify(file: UploadFile = File(...)):
    """Legacy endpoint — CSV with pre-filled OCR_Extracted_Text column."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted.")

    content = await file.read()
    try:
        rows = _parse_csv(content, REQUIRED_COLS_LEGACY)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not rows:
        raise HTTPException(status_code=422, detail="CSV has no data rows.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set in .env")

    total = len(rows)
    client = anthropic.Anthropic(api_key=api_key)

    async def event_stream():
        for i, row in enumerate(rows, start=1):
            user_message = (
                f"Customer ID: {row['Customer_ID']}\n"
                f"Registered Name: {row['Registered_Name']}\n"
                f"Registered Address: {row['Registered_Address']}\n"
                f"\nOCR Extracted Text:\n{row['OCR_Extracted_Text']}"
            )
            result = await asyncio.get_event_loop().run_in_executor(
                None, _call_claude, client, user_message
            )
            event_data = {
                "index":                 i,
                "total":                 total,
                "row":                   row,
                "decision":              result.get("decision", "ERROR"),
                "reasoning":             result.get("reasoning", ""),
                "name_match":            result.get("name_match", ""),
                "address_match":         result.get("address_match", ""),
                "ocr_name_extracted":    result.get("ocr_name_extracted", ""),
                "ocr_address_extracted": result.get("ocr_address_extracted", ""),
            }
            yield f"data: {json.dumps(event_data)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
