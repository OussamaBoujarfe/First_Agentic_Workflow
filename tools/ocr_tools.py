"""
WAT Layer 3 - Tools: OCR Document Field Extractor
==================================================
Uses Anthropic Claude Vision to extract three specific fields from a
proof-of-address document (image, PDF, or JSON):

    name        — the account holder / billed-to name on the document
    address     — the residential/billing address on the document
    issue_date  — the document date (invoice date, statement date, etc.)

Nothing else is extracted. Always returns the same four-key dict.

Supported input formats:
    .jpg / .jpeg    → image/jpeg
    .png            → image/png
    .pdf            → application/pdf  (sent as document block)
    .json           → read as UTF-8 text, passed inline

Usage (smoke test):
    python tools/ocr_tools.py path/to/document.jpg
"""

import base64
import json
import os
import re
import sys
import time

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OCR_MODEL         = "claude-haiku-4-5-20251001"
OCR_MAX_TOKENS    = 256
OCR_MAX_RETRIES   = 4
OCR_RETRY_BACKOFF = 15   # base seconds; doubles each attempt

OCR_SYSTEM_PROMPT = """You are a document field extractor for KYC compliance.

Your ONLY job: look at the document provided and return exactly three fields as a single JSON object on one line.

Fields to extract:
- name        : the account holder, billed-to, or customer name shown on the document
- address     : the residential or billing address shown on the document
- issue_date  : the document date (invoice date, statement date, bill date, etc.)

Rules:
- If a field is not visible or cannot be read, return an empty string "" for that field.
- Return ONLY the JSON object. No prose, no explanation, no markdown fences.
- Example output: {"name": "Jane Smith", "address": "123 Baker St, London NW1 6XE", "issue_date": "15 Jan 2026"}
- Do not include any other fields."""

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
# Core extraction function
# ---------------------------------------------------------------------------

def extract_document_fields(file_bytes: bytes, filename: str, client: anthropic.Anthropic) -> dict:
    """
    WAT Layer 3 Tool: Extract name, address, and issue_date from a document.

    Uses Claude Vision for images/PDFs, and inline text for JSON files.
    Retries on rate-limit errors. Always returns the same four-key dict.

    Args:
        file_bytes : Raw bytes of the uploaded document.
        filename   : Original filename (used to detect media type).
        client     : Authenticated anthropic.Anthropic instance.

    Returns:
        {
            "name":       str,
            "address":    str,
            "issue_date": str,
            "ocr_error":  str|None
        }
    """
    media_type = _detect_media_type(filename)
    raw = ""

    # Build the content block once — reused across retry attempts
    if media_type == "text/plain":
        # JSON/text file — pass content inline as text
        text_content = file_bytes.decode("utf-8")
        content = [{"type": "text", "text": f"Document content:\n\n{text_content}"}]
    elif media_type == "application/pdf":
        # PDF — use document block
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        content = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {"type": "text", "text": "Extract the required fields from this document."},
        ]
    else:
        # Image (JPEG / PNG)
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

            return {
                "name":       str(parsed.get("name", "")),
                "address":    str(parsed.get("address", "")),
                "issue_date": str(parsed.get("issue_date", "")),
                "ocr_error":  None,
            }

        except json.JSONDecodeError as e:
            return {
                "name": "", "address": "", "issue_date": "",
                "ocr_error": f"JSON parse error: {e}. Raw: {raw[:120]}"
            }

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
            return {
                "name": "", "address": "", "issue_date": "",
                "ocr_error": f"Rate limit after {OCR_MAX_RETRIES} attempts: {last_error}"
            }

        except Exception as e:
            return {
                "name": "", "address": "", "issue_date": "",
                "ocr_error": str(e)
            }

    return {
        "name": "", "address": "", "issue_date": "",
        "ocr_error": f"OCR failed after {OCR_MAX_RETRIES} attempts. Last error: {last_error}"
    }


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
        print(f"  {k:<12}: {v}")
