"""
WAT Layer 3 - Tools: OCR Document Field Extractor
==================================================
Uses a hybrid approach for PDFs (pdfplumber fast path → Claude Vision fallback)
and Claude Vision for images. Extracts structured fields from PoA documents.

Supported document types:
  standard         — utility bills, bank statements, etc.
  lease_agreement  — rental contracts, tenancy agreements

Supported input formats:
    .jpg / .jpeg    → image/jpeg  (Claude Vision)
    .png            → image/png   (Claude Vision)
    .pdf            → pdfplumber if machine-readable, else Claude Vision
    .json           → read as UTF-8 text, passed inline

Output schema:

  Standard document:
    { doc_type, customer_name, customer_address, issue_date,
      is_po_box, address_transliterated, address_original, ocr_error }

  Lease agreement:
    { doc_type, landlord_name, tenant_name, both_signed, lease_duration,
      customer_address, issue_date, is_po_box, address_transliterated,
      address_original, ocr_error }

Usage (smoke test):
    python tools/ocr_tools.py path/to/document.jpg
"""

import base64
import json
import os
import re
import sys
import time
import io

import anthropic

# ---------------------------------------------------------------------------
# Graceful pdfplumber import (hybrid PDF fast path)
# ---------------------------------------------------------------------------
try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    pdfplumber = None
    _PDFPLUMBER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OCR_MODEL         = "claude-haiku-4-5-20251001"
OCR_MAX_TOKENS    = 512
OCR_MAX_RETRIES   = 4
OCR_RETRY_BACKOFF = 15   # base seconds; doubles each attempt
PDF_TEXT_MIN_CHARS = 30  # min chars from pdfplumber to skip Vision path

OCR_SYSTEM_PROMPT = """You are a KYC document field extractor for a fintech compliance team.
A human agent has already verified the document is authentic and an accepted document type.
Your job: extract structured fields and return a single JSON object on one line.

STEP 1 — Classify the document:
- If it is a Lease Agreement (rental contract, tenancy agreement), set "doc_type": "lease_agreement"
- Otherwise, set "doc_type": "standard"

STEP 2 — Extract fields based on doc_type:

For "standard" documents (utility bills, bank statements, etc.):
{"doc_type": "standard", "customer_name": "<account holder / billed-to name>", "customer_address": "<residential/billing address of the customer>", "issue_date": "<invoice/statement/bill date>", "address_transliterated": false, "address_original": null}

For "lease_agreement" documents:
{"doc_type": "lease_agreement", "landlord_name": "<landlord / lessor name>", "tenant_name": "<tenant / lessee name>", "both_signed": <true if signatures from BOTH landlord AND tenant are present, false otherwise>, "lease_duration": "<stay period or lease term, e.g. '12 months (01 Jan 2026 - 31 Dec 2026)'>", "customer_address": "<the leased property address>", "issue_date": "<lease start date or signing date>", "address_transliterated": false, "address_original": null}

STEP 3 — Transliteration:
If customer_address is written in a non-Latin script (Japanese, Chinese, Korean, Cyrillic, Arabic, Hindi, Urdu, Persian, or any other non-Latin writing system):
- Transliterate and translate the address to English/Latin script
- Put the English version in customer_address
- Set "address_transliterated": true
- Set "address_original": "<original non-Latin address>"

Rules:
- Return empty string "" for any field that is not visible or cannot be read.
- Return ONLY the JSON object. No prose, no markdown fences, no explanation."""

# ---------------------------------------------------------------------------
# Media type detection
# ---------------------------------------------------------------------------
EXTENSION_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".pdf":  "application/pdf",
    ".json": "text/plain",
}


def _detect_media_type(filename: str) -> str:
    ext = os.path.splitext(filename.lower())[1]
    media_type = EXTENSION_MAP.get(ext)
    if not media_type:
        raise ValueError(
            f"[ocr_tools] Unsupported file type '{ext}'. "
            f"Supported: {', '.join(EXTENSION_MAP.keys())}"
        )
    return media_type


# ---------------------------------------------------------------------------
# PO Box detection
# ---------------------------------------------------------------------------
_PO_BOX_RE = re.compile(r'\bP\.?\s*O\.?\s*B(?:OX|ox)\b', re.IGNORECASE)


def _is_po_box(address: str) -> bool:
    return bool(_PO_BOX_RE.search(address or ""))


# ---------------------------------------------------------------------------
# PDF text extraction (pdfplumber fast path)
# ---------------------------------------------------------------------------
def _extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Attempt to extract plain text from a PDF using pdfplumber.

    Returns the concatenated text from all pages, or "" if:
      - pdfplumber is not installed
      - the PDF is scanned / image-only (no embedded text layer)
      - any exception occurs during extraction
    """
    if not _PDFPLUMBER_AVAILABLE:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            parts = [page.extract_text() for page in pdf.pages if page.extract_text()]
        return "\n".join(parts).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Error response helper
# ---------------------------------------------------------------------------
def _error_result(msg: str) -> dict:
    return {
        "doc_type":               "standard",
        "customer_name":          "",
        "customer_address":       "",
        "issue_date":             "",
        "is_po_box":              False,
        "address_transliterated": False,
        "address_original":       None,
        "ocr_error":              msg,
    }


# ---------------------------------------------------------------------------
# Core extraction function
# ---------------------------------------------------------------------------
def extract_document_fields(file_bytes: bytes, filename: str, client: anthropic.Anthropic) -> dict:
    """
    WAT Layer 3 Tool: Extract structured fields from a PoA document.

    Uses pdfplumber fast path for machine-readable PDFs, Claude Vision for
    images and scanned PDFs. Handles document type detection, transliteration
    of non-Latin addresses, and PO Box flagging.

    Args:
        file_bytes : Raw bytes of the uploaded document.
        filename   : Original filename (used to detect media type).
        client     : Authenticated anthropic.Anthropic instance.

    Returns:
        Standard document:
        { doc_type, customer_name, customer_address, issue_date,
          is_po_box, address_transliterated, address_original, ocr_error }

        Lease agreement:
        { doc_type, landlord_name, tenant_name, both_signed, lease_duration,
          customer_address, issue_date, is_po_box, address_transliterated,
          address_original, ocr_error }
    """
    media_type = _detect_media_type(filename)
    raw = ""

    # Build the content block — reused across retry attempts
    if media_type == "text/plain":
        text_content = file_bytes.decode("utf-8")
        content = [{"type": "text", "text": f"Document content:\n\n{text_content}"}]

    elif media_type == "application/pdf":
        # Hybrid PDF path: try pdfplumber first (free), fall back to Vision
        extracted_text = _extract_text_from_pdf(file_bytes)
        if len(extracted_text) >= PDF_TEXT_MIN_CHARS:
            content = [
                {"type": "text", "text": "Document content (extracted from PDF):\n\n" + extracted_text}
            ]
        else:
            b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
            content = [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                },
                {"type": "text", "text": "Extract the required fields from this document."},
            ]

    else:
        # Image (JPEG / PNG) — always use Claude Vision
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {"type": "text", "text": "Extract the required fields from this document."},
        ]

    last_error = None

    for attempt in range(OCR_MAX_RETRIES):
        try:
            response = client.messages.create(
                model=OCR_MODEL,
                max_tokens=OCR_MAX_TOKENS,
                system=OCR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )

            raw = response.content[0].text.strip()

            # Strip markdown fences if the model wraps the JSON despite instructions
            if raw.startswith("```"):
                lines = raw.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            parsed = json.loads(raw)

            doc_type = str(parsed.get("doc_type", "standard"))
            customer_addr = str(parsed.get("customer_address", ""))
            is_po_box = _is_po_box(customer_addr)

            if doc_type == "lease_agreement":
                return {
                    "doc_type":               "lease_agreement",
                    "landlord_name":          str(parsed.get("landlord_name", "")),
                    "tenant_name":            str(parsed.get("tenant_name", "")),
                    "both_signed":            bool(parsed.get("both_signed", False)),
                    "lease_duration":         str(parsed.get("lease_duration", "")),
                    "customer_address":       customer_addr,
                    "issue_date":             str(parsed.get("issue_date", "")),
                    "is_po_box":              is_po_box,
                    "address_transliterated": bool(parsed.get("address_transliterated", False)),
                    "address_original":       parsed.get("address_original") or None,
                    "ocr_error":              None,
                }
            else:
                return {
                    "doc_type":               "standard",
                    "customer_name":          str(parsed.get("customer_name", "")),
                    "customer_address":       customer_addr,
                    "issue_date":             str(parsed.get("issue_date", "")),
                    "is_po_box":              is_po_box,
                    "address_transliterated": bool(parsed.get("address_transliterated", False)),
                    "address_original":       parsed.get("address_original") or None,
                    "ocr_error":              None,
                }

        except json.JSONDecodeError as e:
            return _error_result(f"JSON parse error: {e}. Raw: {raw[:120]}")

        except anthropic.RateLimitError as e:
            last_error = str(e)
            if attempt < OCR_MAX_RETRIES - 1:
                wait = OCR_RETRY_BACKOFF * (2 ** attempt)
                print(
                    f"  [ocr_tools] Rate limit on attempt {attempt + 1}/{OCR_MAX_RETRIES} "
                    f"for '{filename}'. Waiting {wait}s…"
                )
                time.sleep(wait)
                continue
            return _error_result(f"Rate limit after {OCR_MAX_RETRIES} attempts: {last_error}")

        except Exception as e:
            return _error_result(str(e))

    return _error_result(f"OCR failed after {OCR_MAX_RETRIES} attempts. Last error: {last_error}")


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ocr_tools] ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python tools/ocr_tools.py <path/to/document>")
        print("Supported: .jpg, .jpeg, .png, .pdf, .json")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"[ocr_tools] File not found: {path}")
        sys.exit(1)

    with open(path, "rb") as f:
        raw_bytes = f.read()

    filename = os.path.basename(path)
    print(f"[ocr_tools] Extracting fields from '{filename}' ({len(raw_bytes):,} bytes)…")

    ocr_client = anthropic.Anthropic(api_key=api_key)
    result = extract_document_fields(raw_bytes, filename, ocr_client)

    print("\n=== OCR Result ===")
    for k, v in result.items():
        print(f"  {k:<24}: {v}")
