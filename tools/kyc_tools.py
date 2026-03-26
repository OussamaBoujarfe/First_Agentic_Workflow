"""
WAT Layer 3 - Tools: KYC I/O Utilities
=======================================
This module is Layer 3 of the WAT framework: pure, deterministic execution.

There is NO AI logic here. These functions do exactly one thing each:
read data in, or write data out. They are fast, testable, and reliable.

The agent script (Layer 2) calls these functions so that I/O never
mixes with AI reasoning — that separation is what makes the system trustworthy.

Usage (smoke test):
    python tools/kyc_tools.py
"""

import csv
import os

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# The required columns that must exist in the input CSV.
REQUIRED_COLUMNS = {
    "Customer_ID",
    "Registered_Name",
    "Registered_Address",
    "Phone",
    "Email",
    "Date_of_Birth",
    "IPs",
}

# The exact column order for the output CSV.
# extrasaction='ignore' in DictWriter means any extra keys (e.g. from Claude's
# JSON like name_match, address_match) are silently dropped — only these columns
# appear in the final file.
RESULT_COLUMNS = [
    "Customer_ID",
    "Registered_Name",
    "Registered_Address",
    "Document",
    "OCR_Name",
    "OCR_Address",
    "OCR_Issue_Date",
    "Agent_Decision",
    "Agent_Reasoning",
]


# ---------------------------------------------------------------------------
# Tool 1: read_kyc_data
# ---------------------------------------------------------------------------

def read_kyc_data(filepath: str) -> list:
    """
    WAT Layer 3 Tool: Read KYC customer records from a CSV file.

    Opens the CSV, validates that the four required columns are present,
    strips leading/trailing whitespace from all values, and returns every
    row as a list of dicts.

    Args:
        filepath: Path to the input CSV (absolute or relative to project root).

    Returns:
        List of dicts, one per customer row. Keys match the CSV header.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
        ValueError: If one or more required columns are absent from the header.
    """

    # Guard: make sure the file actually exists before trying to open it.
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"[kyc_tools] Input CSV not found: '{filepath}'\n"
            f"  Working directory: {os.getcwd()}\n"
            f"  Tip: run the script from the project root."
        )

    rows = []

    with open(filepath, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        # Validate columns immediately after reading the header.
        # DictReader exposes the header as reader.fieldnames.
        if reader.fieldnames is None:
            raise ValueError("[kyc_tools] CSV appears to be empty — no header found.")

        actual_columns = set(reader.fieldnames)
        missing = REQUIRED_COLUMNS - actual_columns
        if missing:
            raise ValueError(
                f"[kyc_tools] CSV is missing required column(s): {sorted(missing)}\n"
                f"  Found columns: {sorted(actual_columns)}"
            )

        # Read every row, stripping whitespace from all string values.
        # This handles any stray spaces that CSV exports sometimes introduce.
        for row in reader:
            cleaned = {key: (value.strip() if isinstance(value, str) else value)
                       for key, value in row.items()}
            rows.append(cleaned)

    print(f"[kyc_tools] Loaded {len(rows)} customer records from '{filepath}'.")
    return rows


# ---------------------------------------------------------------------------
# Tool 2: write_kyc_results
# ---------------------------------------------------------------------------

def write_kyc_results(rows: list, output_path: str) -> None:
    """
    WAT Layer 3 Tool: Write KYC decisions to an output CSV file.

    Writes a CSV containing the original four customer columns plus two new
    columns: Agent_Decision and Agent_Reasoning. The column order is fixed
    by RESULT_COLUMNS. Any extra keys in the row dicts (e.g. from Claude's
    detailed JSON) are silently ignored.

    Creates the output directory if it does not exist.

    Args:
        rows: List of dicts. Each dict must contain at least the keys in
              RESULT_COLUMNS. Extra keys are ignored.
        output_path: Destination file path (e.g. 'results/kyc_results.csv').

    Returns:
        None. Prints a confirmation line to stdout when done.
    """

    # Create the output directory if it doesn't exist yet.
    # os.makedirs with exist_ok=True is safe to call even if the dir exists.
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, mode="w", encoding="utf-8", newline="") as f:
        # extrasaction='ignore' means DictWriter silently drops any keys
        # in the row dicts that are not in RESULT_COLUMNS.
        # This lets us pass the full Claude response dict without filtering it.
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[kyc_tools] Results written to: '{output_path}' ({len(rows)} rows).")


# ---------------------------------------------------------------------------
# Smoke test — run directly to verify I/O without touching the API
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Run from the project root:  python tools/kyc_tools.py

    test_path = "payload/cst_poa - data_cst_poa.csv"
    print("=== kyc_tools smoke test ===")

    # Test read
    records = read_kyc_data(test_path)
    print(f"First record:")
    for key, value in list(records[0].items()):
        # Truncate long OCR text for readability
        display = value if len(value) < 80 else value[:77] + "..."
        print(f"  {key}: {display}")

    # Test write (writes a tiny dummy result to .tmp/)
    dummy_result = {
        **records[0],
        "Document": "sample_bill.jpg",
        "OCR_Name": "Jane Smith",
        "OCR_Address": "123 Baker Street, London",
        "OCR_Issue_Date": "15 Jan 2026",
        "Agent_Decision": "Pass",
        "Agent_Reasoning": "Smoke test — not a real decision.",
        "extra_field_that_should_be_ignored": "ignored",
    }
    write_kyc_results([dummy_result], ".tmp/smoke_test_output.csv")
    print("Smoke test passed. Check .tmp/smoke_test_output.csv to verify output.")
