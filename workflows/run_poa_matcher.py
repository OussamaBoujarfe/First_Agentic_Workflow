"""
WAT Layer 2 - Agent: KYC Proof of Address Matcher
==================================================
This script IS the agent. It sits between the workflow (Layer 1) and the
tools (Layer 3), making intelligent decisions about how to coordinate them.

What this script does, in order:
  1. Loads workflows/poa_matcher.md as Claude's system prompt      ← Layer 1
  2. Reads customer records via read_kyc_data()                    ← Layer 3
  3. For each customer, asks Claude to verify the PoA              ← AI reasoning
  4. Parses Claude's structured JSON response
  5. Saves all decisions via write_kyc_results()                   ← Layer 3

Why this separation matters:
  Each tool call is deterministic and testable in isolation.
  Claude only handles the fuzzy, judgment-intensive comparison step.
  Errors in one row never abort the whole run — every row gets a result.

How to run (from the project root):
    source .env && python workflows/run_poa_matcher.py

Requirements:
    pip install -r requirements.txt
    ANTHROPIC_API_KEY must be set in .env
"""

import os
import sys
import json
import time

import anthropic
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# sys.path fix
# ---------------------------------------------------------------------------
# This script lives in workflows/ but needs to import from tools/.
# We resolve the project root from this file's own absolute path so the
# import works regardless of what directory you run the script from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Layer 3 tools — deterministic I/O only, no AI calls inside these
from tools.kyc_tools import read_kyc_data, write_kyc_results

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Change these values here to tune the run — no hunting through the code.

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 512             # Enough for one JSON object per row
TEMPERATURE = 0.0            # Fully deterministic — compliance needs consistency

DELAY_BETWEEN_CALLS = 0              # Seconds to pause between API calls
                                     # Haiku rate limits are generous enough for 30 rows
MAX_ROWS = 4                         # Set to None to process all rows

MAX_RETRIES = 3                      # Retry attempts on rate limit / server errors
RETRY_BACKOFF_BASE = 10              # Initial backoff in seconds; doubles each retry

# File paths — all relative to the project root
INPUT_CSV = os.path.join(PROJECT_ROOT, "payload", "cst_poa - data_cst_poa.csv")
WORKFLOW_FILE = os.path.join(PROJECT_ROOT, "workflows", "poa_matcher.md")
OUTPUT_CSV = os.path.join(PROJECT_ROOT, "results", "kyc_results.csv")
ERROR_LOG = os.path.join(PROJECT_ROOT, ".tmp", "error_log.txt")


# ---------------------------------------------------------------------------
# Helper: load the workflow SOP as the system prompt
# ---------------------------------------------------------------------------

def load_system_prompt(path: str) -> str:
    """
    WAT Layer 1 → Layer 2 connection.

    The Markdown SOP in workflows/poa_matcher.md IS the agent's instructions.
    By loading it here as the system prompt, we make Claude behave exactly as
    the workflow specifies — the SOP is authoritative, not this Python code.

    This also means you can improve the agent's behaviour by editing the .md
    file alone, without touching any Python.

    Args:
        path: Absolute path to the workflow Markdown file.

    Returns:
        The full contents of the file as a string.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()
    print(f"[agent] Loaded system prompt from '{os.path.relpath(path, PROJECT_ROOT)}' "
          f"({len(content)} chars).")
    return content


# ---------------------------------------------------------------------------
# Helper: build the user-turn message for one row
# ---------------------------------------------------------------------------

def build_user_message(row: dict) -> str:
    """
    Format a single customer record into a clear, structured user message.

    Keeping this function separate means we can change the prompt format
    without touching the API call logic.

    Args:
        row: A dict with keys Customer_ID, Registered_Name,
             Registered_Address, OCR_Extracted_Text.

    Returns:
        A formatted string ready to send as the user turn to Claude.
    """
    return (
        f"Customer ID: {row['Customer_ID']}\n"
        f"Registered Name: {row['Registered_Name']}\n"
        f"Registered Address: {row['Registered_Address']}\n"
        f"\nOCR Extracted Text:\n{row['OCR_Extracted_Text']}"
    )


# ---------------------------------------------------------------------------
# Helper: call Claude with retry logic
# ---------------------------------------------------------------------------

def call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_message: str,
    row_id: str,
) -> dict:
    """
    Send one KYC verification request to Claude and return the parsed result.

    Handles failure modes gracefully so a single bad row never stops the run:
      - Rate limit → exponential backoff, then retry
      - JSON parse failure → log raw response, return ERROR decision

    Args:
        client:        Authenticated anthropic.Anthropic instance.
        system_prompt: The full SOP loaded from poa_matcher.md.
        user_message:  The formatted customer record string.
        row_id:        Customer_ID string, used only for log messages.

    Returns:
        A dict with at minimum 'decision' and 'reasoning' keys.
        On unrecoverable error, returns {"decision": "ERROR", "reasoning": "..."}.
    """
    raw_text = ""
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            raw_text = response.content[0].text.strip()

            # Strip markdown fences if the model wraps the JSON
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw_text = "\n".join(lines).strip()

            result = json.loads(raw_text)
            return result  # Success

        except json.JSONDecodeError as e:
            _log_error(row_id, raw_text, str(e))
            return {
                "decision": "ERROR",
                "reasoning": (
                    f"Claude returned non-JSON output for {row_id}. "
                    f"Raw response logged to {os.path.relpath(ERROR_LOG, PROJECT_ROOT)}."
                ),
            }

        except anthropic.RateLimitError as e:
            last_error = e
            backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
            print(f"  [agent] Rate limit for {row_id} "
                  f"(attempt {attempt + 1}/{MAX_RETRIES}). "
                  f"Waiting {backoff}s before retry...")
            time.sleep(backoff)

        except Exception as e:
            last_error = e
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(RETRY_BACKOFF_BASE)

    return {
        "decision": "ERROR",
        "reasoning": (
            f"API call failed after {MAX_RETRIES} attempts for {row_id}. "
            f"Last error: {last_error}"
        ),
    }


# ---------------------------------------------------------------------------
# Helper: log errors to .tmp/
# ---------------------------------------------------------------------------

def _log_error(row_id: str, raw_response: str, error_detail: str) -> None:
    """
    Append a failed Claude response to the error log for later inspection.
    Creates the .tmp/ directory if it doesn't exist.
    """
    os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
    with open(ERROR_LOG, mode="a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Row: {row_id}\n")
        f.write(f"Error: {error_detail}\n")
        f.write(f"Raw Claude response:\n{raw_response}\n")
    print(f"  [agent] Error logged to '{os.path.relpath(ERROR_LOG, PROJECT_ROOT)}'.")


# ---------------------------------------------------------------------------
# Main orchestration function
# ---------------------------------------------------------------------------

def run_matcher() -> None:
    """
    WAT Layer 2 (Agent) — the main orchestration loop.

    This function connects all three WAT layers:
      - Reads the workflow SOP (Layer 1) to load the system prompt
      - Calls read_kyc_data / write_kyc_results (Layer 3) for I/O
      - Calls Claude (AI) for each row's judgment call

    Error isolation: if any single row fails (even after retries), it receives
    an ERROR decision and the loop continues. The final CSV always has all rows.
    """

    # ------------------------------------------------------------------
    # 0. Load environment variables (.env → ANTHROPIC_API_KEY)
    # ------------------------------------------------------------------
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[agent] ERROR: ANTHROPIC_API_KEY is not set.")
        print("  Get a key at: https://console.anthropic.com/")
        print("  Add to .env:  ANTHROPIC_API_KEY=your-key-here")
        print("  Then run:  source .env && python workflows/run_poa_matcher.py")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 1. Layer 1 → Layer 2: Load the workflow SOP as the system prompt
    #    The Markdown file IS the agent's instructions. Changing the .md
    #    file changes the agent's behaviour without touching this code.
    # ------------------------------------------------------------------
    system_prompt = load_system_prompt(WORKFLOW_FILE)

    # ------------------------------------------------------------------
    # 2. Initialise the Gemini client
    # ------------------------------------------------------------------
    client = anthropic.Anthropic(api_key=api_key)
    print(f"[agent] Using model: {MODEL} (Anthropic Claude)")

    # ------------------------------------------------------------------
    # 3. Layer 3 tool call: read the input data
    # ------------------------------------------------------------------
    rows = read_kyc_data(INPUT_CSV)
    if MAX_ROWS is not None:
        rows = rows[:MAX_ROWS]
    total = len(rows)
    print(f"[agent] Starting PoA verification for {total} customers...\n")

    # ------------------------------------------------------------------
    # 4. Main loop: one API call per customer row
    # ------------------------------------------------------------------
    results = []

    for i, row in enumerate(rows, start=1):
        customer_id = row["Customer_ID"]
        registered_name = row["Registered_Name"]

        print(f"[{i:02d}/{total}] {customer_id} — {registered_name}")

        # Build the user-turn message for this specific customer
        user_message = build_user_message(row)

        # Ask Claude to verify the PoA — this is the AI judgment step
        response_dict = call_claude(client, system_prompt, user_message, customer_id)

        # Extract the two columns we care about for the output CSV.
        # Use .get() with fallbacks so a partial JSON response doesn't crash us.
        decision = response_dict.get("decision", "ERROR")
        reasoning = response_dict.get("reasoning", "No reasoning returned by model.")

        print(f"         → {decision}: {reasoning[:90]}{'...' if len(reasoning) > 90 else ''}")

        # Combine the original row with the agent's decision.
        # The write tool's extrasaction='ignore' will drop any extra keys
        # from response_dict (like name_match, address_match) — they won't
        # appear in the output CSV unless we add them to RESULT_COLUMNS.
        result_row = {
            **row,                          # Original four columns
            **response_dict,                # All of Claude's JSON fields
            "Agent_Decision": decision,     # Explicit column name for the CSV
            "Agent_Reasoning": reasoning,   # Explicit column name for the CSV
        }
        results.append(result_row)

        # Polite pacing between API calls to stay within rate limits.
        # Skip the sleep after the very last row.
        if i < total:
            time.sleep(DELAY_BETWEEN_CALLS)

    # ------------------------------------------------------------------
    # 5. Layer 3 tool call: write all results to the output CSV
    # ------------------------------------------------------------------
    print()
    write_kyc_results(results, OUTPUT_CSV)

    # ------------------------------------------------------------------
    # 6. Print a summary so you can spot-check the run at a glance
    # ------------------------------------------------------------------
    from collections import Counter
    counts = Counter(r["Agent_Decision"] for r in results)

    print("\n=== KYC PoA Matcher — Run Summary ===")
    for label in ["Pass", "Fail", "Escalate", "ERROR"]:
        count = counts.get(label, 0)
        bar = "█" * count
        print(f"  {label:<10} {count:>3}  {bar}")
    print(f"  {'TOTAL':<10} {total:>3}")
    print(f"\nResults saved to: {os.path.relpath(OUTPUT_CSV, PROJECT_ROOT)}")
    if counts.get("ERROR", 0) > 0:
        print(f"Error details:    {os.path.relpath(ERROR_LOG, PROJECT_ROOT)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_matcher()
