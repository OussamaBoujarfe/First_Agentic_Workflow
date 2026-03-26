"""
WAT Layer 2 - API: KYC PoA Extraction Backend
==============================================
FastAPI server. The AI's role is extraction + translation only.
Human agents make all Pass/Failed/Needs More Information decisions in the UI.

Endpoints:
  POST /verify-ocr   — primary. Single-customer CSV + N document files.
                       OCR each doc via Claude Vision (or pdfplumber fast path).
                       Streams structured extraction results as Server-Sent Events.

  POST /verify       — legacy. CSV with pre-filled OCR_Extracted_Text column.

Run with:
    source .env && uvicorn api.main:app --reload --port 8000
"""

import os
import sys
import json
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
MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="KYC PoA Extraction API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
# CSV parsers
# ---------------------------------------------------------------------------
REQUIRED_COLS_OCR    = {"Customer_ID", "Registered_Name", "Registered_Address"}
REQUIRED_COLS_LEGACY = {"Customer_ID", "Registered_Name", "Registered_Address", "OCR_Extracted_Text"}

def _parse_csv(content: bytes, required: set) -> list:
    text = content.decode("utf-8")
    first_line = text.split("\n")[0]
    delim = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        raise ValueError("CSV is empty or has no header.")
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
        "status":      "ok",
        "api_key_set": api_key_set,
        "model":       MODEL,
        "provider":    "Anthropic Claude",
        "version":     "3.0.0 — extraction only, human-agent decisions",
    }


@app.post("/verify-ocr")
async def verify_ocr(
    csv_file:  UploadFile = File(...),
    documents: List[UploadFile] = File(...),
):
    """
    Primary endpoint — extraction only.

    Accepts:
      csv_file   — single-customer CSV (required: Customer_ID, Registered_Name, Registered_Address)
      documents  — one or more PoA document files (jpg/png/pdf/json)

    For each document:
      1. OCR via pdfplumber (machine-readable PDF) or Claude Vision → extract fields
      2. Stream SSE event with extracted data — NO AI matching decision
         Human agent decides Pass/Failed/Needs More Information in the UI.
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

    customer = rows[0]

    if not documents:
        raise HTTPException(status_code=422, detail="At least one document file is required.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set in .env")

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

            ocr_result = await asyncio.get_event_loop().run_in_executor(
                None, extract_document_fields, file_bytes, filename, client
            )

            event_data = {
                "index":      i,
                "total":      total,
                "doc_filename": filename,
                "row":        customer,
                "doc_type":   ocr_result.get("doc_type", "standard"),
                # Standard fields
                "ocr_customer_name":    ocr_result.get("customer_name", ""),
                "ocr_customer_address": ocr_result.get("customer_address", ""),
                "ocr_issue_date":       ocr_result.get("issue_date", ""),
                "is_po_box":            ocr_result.get("is_po_box", False),
                "address_transliterated": ocr_result.get("address_transliterated", False),
                "address_original":     ocr_result.get("address_original", None),
                # Lease-specific fields
                "ocr_landlord_name":  ocr_result.get("landlord_name", ""),
                "ocr_tenant_name":    ocr_result.get("tenant_name", ""),
                "ocr_both_signed":    ocr_result.get("both_signed", None),
                "ocr_lease_duration": ocr_result.get("lease_duration", ""),
                # Error passthrough
                "ocr_error": ocr_result.get("ocr_error"),
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

    total = len(rows)

    async def event_stream():
        for i, row in enumerate(rows, start=1):
            event_data = {
                "index":        i,
                "total":        total,
                "row":          row,
                "doc_type":     "standard",
                "ocr_customer_name":    row.get("OCR_Extracted_Text", ""),
                "ocr_customer_address": "",
                "ocr_issue_date":       "",
                "is_po_box":            False,
                "address_transliterated": False,
                "address_original":     None,
                "ocr_landlord_name":  "",
                "ocr_tenant_name":    "",
                "ocr_both_signed":    None,
                "ocr_lease_duration": "",
                "ocr_error":          None,
            }
            yield f"data: {json.dumps(event_data)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
