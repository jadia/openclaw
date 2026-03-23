#!/usr/bin/env python3
"""
main.py — CLI orchestrator for the transaction-inbox skill.

Modes:
  --setup                      Idempotent state directory setup
  --process                    Fetch new emails → parse → dedup → insert → summary
  --reprocess --from D --to D  Re-fetch by date range (ignores Seen status)

Follows the ai_brief pattern: Python does the heavy lifting, then triggers
OpenClaw via `openclaw chat --message` for Telegram delivery.

All operations are heavily logged to daily log files under state/logs/.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Ensure lib/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import state, gmail_client, parser, dedup

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "state" / "logs"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging():
    """Configure structured logging to both console and daily log files."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"run_{today}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler (INFO level)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))

    # File handler (DEBUG level — captures everything)
    file_handler = logging.FileHandler(log_file, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root.addHandler(console)
    root.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "=== RUN STARTED at %s === Log: %s",
        datetime.now().isoformat(), log_file,
    )


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Process: fetch → filter → parse → dedup → insert → summary
# ---------------------------------------------------------------------------

def process_emails(settings: dict):
    """
    Main processing pipeline for new/unseen emails.

    Steps:
      1. Connect to Gmail
      2. Fetch UNSEEN emails
      3. Filter by allowed senders
      4. Parse transaction details
      5. Deduplicate
      6. Insert into finance-tracker
      7. Record state
      8. Send Telegram summary via OpenClaw
    """
    gmail_cfg = settings.get("gmail", {})
    if not gmail_cfg.get("email") or not gmail_cfg.get("app_password"):
        logger.error("Gmail credentials not configured in state/settings.json")
        print(json.dumps({
            "status": "error",
            "message": "Gmail credentials not configured. Edit state/settings.json first.",
        }))
        return

    allowed_senders = [s.lower() for s in settings.get("allowed_senders", [])]
    logger.info("Allowed senders: %s", allowed_senders)

    # --- 1. Connect ---
    client = gmail_client.GmailClient(
        host=gmail_cfg.get("host", "imap.gmail.com"),
        email_address=gmail_cfg["email"],
        app_password=gmail_cfg["app_password"],
    )
    try:
        client.connect()
    except ConnectionError as e:
        logger.error("Gmail connection failed: %s", e)
        notify_error(str(e))
        return

    try:
        # --- 2. Fetch unseen ---
        emails = client.fetch_unseen()
        logger.info("Fetched %d unseen emails", len(emails))

        if not emails:
            logger.info("No new emails to process")
            client.disconnect()
            _trigger_summary([], settings)
            return

        # --- 3. Filter by allowed senders ---
        matching, non_matching = _filter_by_sender(emails, allowed_senders)
        logger.info(
            "FILTER sender_matched=%d non_matched=%d (left unseen)",
            len(matching), len(non_matching),
        )

        if not matching:
            logger.info("No emails matched allowed senders")
            client.disconnect()
            _trigger_summary([], settings)
            return

        # --- 4. Skip already-processed emails ---
        to_process = []
        for email_data in matching:
            msg_id = email_data.get("message_id", "")
            if msg_id and state.is_email_processed(msg_id):
                logger.info("SKIP already_processed message_id=%s", msg_id)
                continue
            to_process.append(email_data)

        logger.info("After dedup-by-message-id: %d emails to process", len(to_process))

        if not to_process:
            client.disconnect()
            _trigger_summary([], settings)
            return

        # --- 5. Parse ---
        candidates = []
        custom_patterns = settings.get("parsing", {}).get("custom_patterns", {})
        for email_data in to_process:
            parsed = parser.parse_email(email_data, custom_patterns)
            candidates.append(parsed)

        parse_stats = parser.get_parser_stats(candidates)

        # --- 6. Dedup ---
        known_refs = state.get_recent_reference_ids(days=7)
        candidates = dedup.deduplicate_batch(candidates, settings, known_refs)

        # --- 7. Insert new transactions into finance-tracker ---
        inserted_candidates = [
            c for c in candidates
            if c.get("dedup_status") == dedup.STATUS_NEW and c.get("transaction")
        ]
        ledger_results = _insert_into_ledger(inserted_candidates, settings)

        # --- 8. Record processed state + mark matching emails as Seen ---
        uids_to_mark_seen = []
        for candidate in candidates:
            result_status = candidate.get("dedup_status", "skipped")
            ledger_ids = ledger_results.get(candidate.get("email_uid"), [])
            state.record_processed_email(candidate, result_status, ledger_ids)
            uids_to_mark_seen.append(candidate.get("email_uid"))

        client.mark_seen(uids_to_mark_seen)

        # --- 9. Prune old records ---
        prune_days = settings.get("state", {}).get("prune_after_days", 60)
        state.prune_old_records(prune_days)

        # --- 10. Save pending + summary ---
        summary = _build_summary(candidates, parse_stats)
        state.save_pending({
            "version": 1,
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
            "summary": summary,
        })

        # --- 11. Trigger OpenClaw for Telegram summary ---
        _trigger_summary(candidates, settings)

    except Exception as e:
        logger.error("Processing failed: %s", e, exc_info=True)
        notify_error(str(e))
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# Reprocess: fetch by date range (ignores Seen status)
# ---------------------------------------------------------------------------

def reprocess_emails(settings: dict, from_date: str, to_date: str):
    """
    Re-fetch and reprocess emails in a date range.

    Unlike --process, this ignores Seen flags and re-parses all matching
    emails. Dedup against the ledger prevents double-insertion.
    """
    logger.info("REPROCESS from=%s to=%s", from_date, to_date)

    gmail_cfg = settings.get("gmail", {})
    if not gmail_cfg.get("email") or not gmail_cfg.get("app_password"):
        logger.error("Gmail credentials not configured")
        return

    allowed_senders = [s.lower() for s in settings.get("allowed_senders", [])]
    client = gmail_client.GmailClient(
        host=gmail_cfg.get("host", "imap.gmail.com"),
        email_address=gmail_cfg["email"],
        app_password=gmail_cfg["app_password"],
    )

    try:
        client.connect()
        emails = client.fetch_by_date_range(from_date, to_date)
        logger.info("Reprocess: fetched %d emails in date range", len(emails))

        matching, _ = _filter_by_sender(emails, allowed_senders)
        logger.info("Reprocess: %d matched allowed senders", len(matching))

        if not matching:
            logger.info("Reprocess: no matching emails found")
            client.disconnect()
            return

        # Parse all (skip already-processed check for reprocessing)
        custom_patterns = settings.get("parsing", {}).get("custom_patterns", {})
        candidates = [parser.parse_email(e, custom_patterns) for e in matching]
        parse_stats = parser.get_parser_stats(candidates)

        # Dedup against ledger (this is what prevents double-insertion)
        known_refs = state.get_recent_reference_ids(days=90)
        candidates = dedup.deduplicate_batch(candidates, settings, known_refs)

        # Insert only genuinely new items
        new_candidates = [
            c for c in candidates
            if c.get("dedup_status") == dedup.STATUS_NEW and c.get("transaction")
        ]
        ledger_results = _insert_into_ledger(new_candidates, settings)

        # Record state for new items
        for c in new_candidates:
            ledger_ids = ledger_results.get(c.get("email_uid"), [])
            state.record_processed_email(c, "inserted", ledger_ids)

        # Summary
        summary = _build_summary(candidates, parse_stats)
        state.save_pending({
            "version": 1,
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
            "summary": summary,
        })
        _trigger_summary(candidates, settings)

    except Exception as e:
        logger.error("Reprocessing failed: %s", e, exc_info=True)
        notify_error(str(e))
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_by_sender(emails: list, allowed_senders: list) -> tuple:
    """
    Split emails into matching and non-matching based on allowed sender list.

    Non-matching emails are NOT marked as Seen — they stay available for
    other skills to ingest.
    """
    matching = []
    non_matching = []
    for email_data in emails:
        sender = email_data.get("sender_email", "").lower()
        if any(allowed in sender for allowed in allowed_senders):
            matching.append(email_data)
            logger.debug("SENDER_MATCH email_uid=%s sender=%s", email_data.get("email_uid"), sender)
        else:
            non_matching.append(email_data)
            logger.debug("SENDER_SKIP email_uid=%s sender=%s", email_data.get("email_uid"), sender)
    return matching, non_matching


def _insert_into_ledger(candidates: list, settings: dict) -> dict:
    """
    Insert parsed transactions into finance-tracker via tracker.py --bulk-add.

    Returns a dict mapping email_uid → list of inserted ledger IDs.
    """
    if not candidates:
        return {}

    ft_settings = settings.get("finance_tracker", {})
    skill_dir = ft_settings.get("skill_dir", "../finance-tracker")
    tracker_script = ft_settings.get("tracker_script", "tracker.py")
    tracker_path = os.path.normpath(
        os.path.join(str(BASE_DIR), skill_dir, tracker_script)
    )

    if not os.path.exists(tracker_path):
        logger.error("Finance-tracker not found at %s", tracker_path)
        return {}

    # Build bulk-add payload
    expenses = []
    uid_index = {}  # Maps index in expenses list → email_uid
    for candidate in candidates:
        txn = candidate.get("transaction", {})
        if not txn or not txn.get("amount"):
            continue
        expense = {
            "amount": txn["amount"],
            "description": txn.get("description", ""),
            "date": txn.get("transaction_date", datetime.now().strftime("%Y-%m-%d")),
        }
        uid_index[len(expenses)] = candidate.get("email_uid")
        expenses.append(expense)

    if not expenses:
        logger.info("No expenses to insert into ledger")
        return {}

    logger.info("Inserting %d expenses into finance-tracker", len(expenses))
    try:
        result = subprocess.run(
            [sys.executable, tracker_path, "--bulk-add", json.dumps(expenses)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(tracker_path),
            timeout=30,
        )

        if result.returncode != 0:
            logger.error("tracker.py --bulk-add failed: %s", result.stderr.strip())
            return {}

        data = json.loads(result.stdout.strip())
        if data.get("status") == "success":
            inserted_ids = data.get("ids", [])
            logger.info("Successfully inserted %d expenses, IDs: %s", len(inserted_ids), inserted_ids)

            # Map UIDs to their ledger IDs
            uid_to_ids = {}
            for i, lid in enumerate(inserted_ids):
                uid = uid_index.get(i)
                if uid:
                    uid_to_ids.setdefault(uid, []).append(lid)
            return uid_to_ids
        else:
            logger.error("tracker.py returned error: %s", data.get("message", "unknown"))
            return {}

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Ledger insertion failed: %s", e)
        return {}


def _build_summary(candidates: list, parse_stats: dict) -> dict:
    """Build a summary dict for the run."""
    total = len(candidates)
    inserted = sum(1 for c in candidates if c.get("dedup_status") == dedup.STATUS_NEW and c.get("transaction"))
    duplicates = sum(1 for c in candidates if c.get("dedup_status") in (
        dedup.STATUS_DEFINITE_DUPLICATE, dedup.STATUS_AUTO_MERGED,
    ))
    probable_dups = sum(1 for c in candidates if c.get("dedup_status") == dedup.STATUS_PROBABLE_DUPLICATE)
    llm_needed = sum(1 for c in candidates if c.get("parse_method") == "llm_needed")

    summary = {
        "total_emails_processed": total,
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "probable_duplicates_flagged": probable_dups,
        "llm_parsing_needed": llm_needed,
        "parse_stats": parse_stats,
        "timestamp": datetime.now().isoformat(),
    }
    logger.info(
        "RUN_SUMMARY total=%d inserted=%d dups=%d probable=%d llm=%d",
        total, inserted, duplicates, probable_dups, llm_needed,
    )
    return summary


def _trigger_summary(candidates: list, settings: dict):
    """
    Trigger OpenClaw to send a Telegram summary of processed transactions.

    Uses `openclaw chat --message` to deliver the summary.
    """
    pending = state.load_pending()
    summary = pending.get("summary", {})

    # Build human-readable transaction list
    txn_lines = []
    for i, c in enumerate(candidates, 1):
        txn = c.get("transaction", {})
        status = c.get("dedup_status", "?")
        if txn:
            amount = txn.get("amount", 0)
            merchant = txn.get("merchant", "Unknown")
            date = txn.get("transaction_date", "?")
            desc = txn.get("description", "")
            txn_lines.append(
                f"  #{i} ₹{amount:.2f} — {merchant} ({date}) [{status}]"
                f"\n      {desc}"
            )
        elif c.get("parse_method") == "llm_needed":
            txn_lines.append(
                f"  #{i} [LLM PARSING NEEDED] from={c.get('from', '?')} "
                f"subject={c.get('subject', '?')[:60]}"
            )

    # Build prompt for OpenClaw
    if not candidates:
        prompt = (
            "Use the transaction-inbox skill.\n\n"
            "The nightly email processing found **no new transaction emails** to process.\n"
            "Report this to me on Telegram."
        )
    else:
        txn_list = "\n".join(txn_lines) if txn_lines else "  (none)"
        prompt = f"""Use the transaction-inbox skill.

The nightly email processing has completed. Here is the summary:

**Processed:** {summary.get('total_emails_processed', 0)} emails
**Inserted:** {summary.get('inserted', 0)} new transactions
**Duplicates skipped:** {summary.get('duplicates_skipped', 0)}
**Probable duplicates (flagged):** {summary.get('probable_duplicates_flagged', 0)}
**LLM parsing needed:** {summary.get('llm_parsing_needed', 0)}

**Transactions:**
{txn_list}

Regex hit rate: {summary.get('parse_stats', {}).get('hit_rate_pct', 0)}%

Please send this summary to me on Telegram. Format it as a clean, readable message.
If any items are flagged as probable duplicates, highlight them and ask me to confirm.
If any items need LLM parsing, read the pending_transactions.json file at {state.STATE_DIR}/pending_transactions.json, extract the transaction details from the email body, and tell me what you found.

I can reply with corrections like:
- "delete #3" → use finance-tracker --remove
- "change #5 amount to 450" → use finance-tracker --query-write
- "recategorise #2 to Junk" → use finance-tracker --update-category
- "merge #4 and #6" → remove one and keep the other
- "confirm all" → no action needed
"""

    logger.info("Triggering OpenClaw for Telegram summary")
    try:
        subprocess.run(
            ["openclaw", "chat", "--message", prompt],
            check=True,
            timeout=120,
        )
        logger.info("OpenClaw summary triggered successfully")
    except FileNotFoundError:
        logger.warning("openclaw CLI not found — summary written to pending_transactions.json only")
    except subprocess.TimeoutExpired:
        logger.error("OpenClaw summary timed out")
    except subprocess.CalledProcessError as e:
        logger.error("OpenClaw summary failed: %s", e)


def notify_error(error_msg: str):
    """Send error notification via OpenClaw if available."""
    prompt = f"""The transaction-inbox skill encountered an error during processing.

Error: {error_msg}

Please notify me on Telegram about this failure and suggest how to fix it."""

    try:
        subprocess.run(["openclaw", "chat", "--message", prompt], timeout=60)
    except Exception as e:
        logger.error("Failed to send error notification: %s", e)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Transaction Inbox — Email transaction ingestion for finance-tracker",
    )
    p.add_argument(
        "--setup", action="store_true",
        help="Initialize state directory and default settings",
    )
    p.add_argument(
        "--process", action="store_true",
        help="Fetch new emails, parse, dedup, insert, and send summary",
    )
    p.add_argument(
        "--reprocess", action="store_true",
        help="Reprocess emails in a date range (use with --from and --to)",
    )
    p.add_argument(
        "--from", dest="from_date", type=str, metavar="YYYY-MM-DD",
        help="Start date for --reprocess",
    )
    p.add_argument(
        "--to", dest="to_date", type=str, metavar="YYYY-MM-DD",
        help="End date for --reprocess",
    )

    args = p.parse_args()

    # Always setup state dir first
    state.setup_state_dir()

    if args.setup:
        setup_logging()
        logger.info("Setup complete")
        print("transaction-inbox setup complete. Edit state/settings.json to configure Gmail credentials.")
        return

    # All other modes need logging
    setup_logging()
    settings = state.load_settings()

    if args.process:
        process_emails(settings)
    elif args.reprocess:
        if not args.from_date or not args.to_date:
            p.error("--reprocess requires --from YYYY-MM-DD and --to YYYY-MM-DD")
        reprocess_emails(settings, args.from_date, args.to_date)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
