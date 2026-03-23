"""
state.py — State file management for transaction-inbox.

Manages three JSON state files:
  - settings.json    → user configuration (Gmail creds, allowed senders, etc.)
  - processed_emails.json → checkpoint of processed email IDs and results
  - pending_transactions.json → staging area for batch summary

All state lives under the skill's state/ directory. Old records are pruned
after a configurable number of days (default 60). Email data in Gmail is
never modified by pruning — only local JSON records are cleaned up.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"

DEFAULT_SETTINGS = {
    "version": 1,
    "gmail": {
        "host": "imap.gmail.com",
        "email": "",
        "app_password": "",
    },
    "allowed_senders": [
        "alerts@hdfcbank.net",
        "transaction@icicibank.com",
        "transaction.alert@icicibank.com",
        "alerts@axisbank.com",
        "alerts@axis.bank.in",
        "donotreply@sbi.co.in",
        "noreply@equitasbank.com",
        "esfb-alerts@equitas.bank.in",
        "noreply@swiggy.in",
        "no-reply@swiggy.in",
        "scapiacards@federalbank.co.in",
        "onlinesbicard@sbicard.com",
        "noreply@zomato.com",
        "auto-confirm@amazon.in",
        "noreply@flipkart.com",
        "noreply@uber.com",
        "no-reply@amazonpay.in",
        "noreply@olacabs.com",
        "no-reply@paytm.com",
        "noreply@phonepe.com",
        "alerts@hdfcbank.bank.in",
        "credit_cards@icicibank.com",
    ],
    "openclaw": {
        "target_args": ["--session-id", "main", "--to", "12345678"]
    },
    "parsing": {
        "custom_patterns": {},
    },
    "dedup": {
        "time_window_minutes": 30,
        "amount_tolerance": 1.0,
    },
    "schedule": {
        "cron": "0 22 * * *",
    },
    "state": {
        "prune_after_days": 60,
    },
    "finance_tracker": {
        "skill_dir": "../finance-tracker",
        "tracker_script": "tracker.py",
    },
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_state_dir():
    """Idempotent creation of state directory and default files."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    _create_if_missing(
        STATE_DIR / "settings.json",
        json.dumps(DEFAULT_SETTINGS, indent=2),
    )
    _create_if_missing(
        STATE_DIR / "processed_emails.json",
        json.dumps({"version": 1, "last_processed_at": None, "emails": []}, indent=2),
    )
    _create_if_missing(
        STATE_DIR / "pending_transactions.json",
        json.dumps({
            "version": 1,
            "generated_at": None,
            "candidates": [],
            "summary": {},
        }, indent=2),
    )
    logger.info("State directory ready at %s", STATE_DIR)


def _create_if_missing(file_path: Path, content: str):
    if not file_path.exists():
        file_path.write_text(content)
        logger.info("Created default state file: %s", file_path.name)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_settings():
    """Load user settings. Returns defaults if file is missing or corrupt."""
    path = STATE_DIR / "settings.json"
    if not path.exists():
        logger.warning("Settings file missing — using defaults")
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(path.read_text())
        logger.debug("Loaded settings (version %s)", data.get("version"))
        return data
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt settings file, using defaults: %s", e)
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    """Write settings back to disk."""
    path = STATE_DIR / "settings.json"
    path.write_text(json.dumps(settings, indent=2))
    logger.debug("Settings saved")


# ---------------------------------------------------------------------------
# Processed emails
# ---------------------------------------------------------------------------

def load_processed():
    """Load the processed emails checkpoint."""
    path = STATE_DIR / "processed_emails.json"
    if not path.exists():
        return {"version": 1, "last_processed_at": None, "emails": []}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt processed_emails file: %s", e)
        return {"version": 1, "last_processed_at": None, "emails": []}


def save_processed(data: dict):
    """Write processed emails checkpoint to disk."""
    path = STATE_DIR / "processed_emails.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.debug("Processed emails state saved (%d records)", len(data.get("emails", [])))


def is_email_processed(message_id: str) -> bool:
    """Check if an email has already been processed by its Message-ID header."""
    processed = load_processed()
    for entry in processed.get("emails", []):
        if entry.get("message_id") == message_id:
            return True
    return False


def record_processed_email(email_data: dict, result: str, ledger_ids: list = None):
    """
    Record a processed email in state.

    Args:
        email_data: Dict with keys: email_uid, message_id, from, subject
        result: One of 'inserted', 'duplicate', 'skipped', 'llm_parsed'
        ledger_ids: List of finance-tracker expense IDs created for this email
    """
    processed = load_processed()
    now = datetime.now().isoformat()

    record = {
        "email_uid": email_data.get("email_uid"),
        "message_id": email_data.get("message_id"),
        "from": email_data.get("from"),
        "subject": email_data.get("subject", ""),
        "processed_at": now,
        "result": result,
        "ledger_ids": ledger_ids or [],
        "reference_ids": email_data.get("reference_ids", {}),
    }

    processed["emails"].append(record)
    processed["last_processed_at"] = now
    save_processed(processed)
    logger.info(
        "Recorded email [%s] result=%s ledger_ids=%s",
        email_data.get("message_id", "?"), result, ledger_ids or [],
    )


# ---------------------------------------------------------------------------
# Pending transactions (staging)
# ---------------------------------------------------------------------------

def load_pending():
    """Load the pending transactions staging file."""
    path = STATE_DIR / "pending_transactions.json"
    if not path.exists():
        return {"version": 1, "generated_at": None, "candidates": [], "summary": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt pending_transactions file: %s", e)
        return {"version": 1, "generated_at": None, "candidates": [], "summary": {}}


def save_pending(data: dict):
    """Write pending transactions staging file."""
    path = STATE_DIR / "pending_transactions.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.debug("Pending transactions saved (%d candidates)", len(data.get("candidates", [])))


def clear_pending():
    """Reset the pending transactions file after a successful run."""
    save_pending({
        "version": 1,
        "generated_at": None,
        "candidates": [],
        "summary": {},
    })
    logger.debug("Pending transactions cleared")


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def prune_old_records(days: int = 60):
    """
    Remove processed email records older than N days from local state.

    Only affects the local JSON file — emails in Gmail are never modified.

    Args:
        days: Number of days to keep records (default: 60)
    """
    processed = load_processed()
    cutoff = datetime.now() - timedelta(days=days)
    original_count = len(processed.get("emails", []))

    processed["emails"] = [
        e for e in processed.get("emails", [])
        if _parse_datetime(e.get("processed_at")) >= cutoff
    ]

    pruned_count = original_count - len(processed["emails"])
    if pruned_count > 0:
        save_processed(processed)
        logger.info(
            "Pruned %d processed email records older than %d days",
            pruned_count, days,
        )
    else:
        logger.debug("No records to prune (all within %d days)", days)

    return pruned_count


def _parse_datetime(dt_string):
    """Parse an ISO datetime string, returning epoch if unparseable."""
    if not dt_string:
        return datetime.min
    try:
        return datetime.fromisoformat(dt_string)
    except (ValueError, TypeError):
        return datetime.min


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_recent_reference_ids(days: int = 7) -> dict:
    """
    Get reference IDs from recently processed emails for dedup lookups.

    Returns a dict mapping each reference_id value to its processed email record.
    """
    processed = load_processed()
    cutoff = datetime.now() - timedelta(days=days)
    ref_map = {}

    for entry in processed.get("emails", []):
        if _parse_datetime(entry.get("processed_at")) < cutoff:
            continue
        for id_type, id_value in entry.get("reference_ids", {}).items():
            if id_value:
                ref_map[str(id_value).lower()] = entry

    logger.debug("Loaded %d recent reference IDs for dedup", len(ref_map))
    return ref_map
