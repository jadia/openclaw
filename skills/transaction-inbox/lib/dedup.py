"""
dedup.py — Two-stage duplicate detection for transaction candidates.

Stage 1 (Hard match): Reference ID comparison (UTR, order_id, ref_no, etc.)
Stage 2 (Soft match): Amount + merchant + time window heuristic

Also handles within-batch dedup (multiple emails for same transaction in
one processing run) and against-ledger dedup (comparing with recent
finance-tracker entries).
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dedup status constants
# ---------------------------------------------------------------------------

STATUS_NEW = "new"                           # No duplicates → insert
STATUS_AUTO_MERGED = "auto_merged"           # High-confidence merge with existing
STATUS_PROBABLE_DUPLICATE = "probable_duplicate"  # Flag for review
STATUS_DEFINITE_DUPLICATE = "definite_duplicate"  # Skip entirely


# ---------------------------------------------------------------------------
# Stage 1: Hard match (reference IDs)
# ---------------------------------------------------------------------------

def _hard_match(candidate_refs: dict, known_refs: dict) -> bool:
    """
    Check if any reference ID in the candidate matches a known reference.

    Args:
        candidate_refs: dict of {id_type: value} from the parsed email
        known_refs: dict mapping ref_value.lower() → processed email record

    Returns:
        True if a definite hard match is found.
    """
    for id_type, id_value in candidate_refs.items():
        if not id_value:
            continue
        key = str(id_value).lower()
        if key in known_refs:
            logger.info(
                "DEDUP_HARD_MATCH ref_type=%s ref_value=%s matched_email=%s",
                id_type, id_value,
                known_refs[key].get("message_id", "?"),
            )
            return True
    return False


# ---------------------------------------------------------------------------
# Stage 2: Soft match (heuristic)
# ---------------------------------------------------------------------------

def _soft_match(
    candidate: dict,
    existing: dict,
    time_window_minutes: int = 30,
    amount_tolerance: float = 1.0,
) -> str:
    """
    Heuristic duplicate check using amount, merchant, direction, and time.

    Args:
        candidate: Parsed transaction dict
        existing: Existing transaction/expense dict to compare against
        time_window_minutes: Time tolerance for matching (default ±30 min)
        amount_tolerance: Amount tolerance in ₹ (default ±1)

    Returns:
        STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE, or None (no match)
    """
    score = 0
    reasons = []

    # --- Amount comparison ---
    cand_amount = candidate.get("amount", 0)
    exist_amount = existing.get("amount", 0)
    if abs(cand_amount - exist_amount) <= amount_tolerance:
        score += 2
        reasons.append(f"amount_match({cand_amount}≈{exist_amount})")

    # --- Merchant comparison (fuzzy) ---
    cand_merchant = (candidate.get("merchant") or "").lower().strip()
    exist_merchant = (existing.get("merchant") or existing.get("description") or "").lower().strip()
    if cand_merchant and exist_merchant:
        if cand_merchant in exist_merchant or exist_merchant in cand_merchant:
            score += 1
            reasons.append(f"merchant_fuzzy({cand_merchant}~{exist_merchant})")

    # --- Direction comparison ---
    cand_dir = candidate.get("direction", "debit")
    exist_dir = existing.get("direction", "debit")
    if cand_dir == exist_dir:
        score += 1
        reasons.append("direction_match")

    # --- Time proximity ---
    cand_time = _parse_time(candidate.get("transaction_date"))
    exist_time = _parse_time(existing.get("transaction_date"))
    if cand_time and exist_time:
        delta = abs((cand_time - exist_time).total_seconds()) / 60
        if delta <= time_window_minutes:
            score += 1
            reasons.append(f"time_within_{int(delta)}min")

    # --- Determine result ---
    reason_str = ", ".join(reasons) if reasons else "no_criteria_met"

    if score >= 4:
        logger.info(
            "DEDUP_SOFT_MATCH result=auto_merged score=%d reasons=[%s]",
            score, reason_str,
        )
        return STATUS_AUTO_MERGED

    if score >= 3:
        logger.info(
            "DEDUP_SOFT_MATCH result=probable_duplicate score=%d reasons=[%s]",
            score, reason_str,
        )
        return STATUS_PROBABLE_DUPLICATE

    logger.debug(
        "DEDUP_SOFT_NO_MATCH score=%d reasons=[%s]", score, reason_str,
    )
    return None


def _parse_time(date_str):
    """Parse a date/datetime string to a datetime object."""
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str[:len(fmt.replace("%", "X"))], fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ledger query helper
# ---------------------------------------------------------------------------

def _query_recent_ledger(settings: dict, days: int = 7) -> list:
    """
    Query recent expenses from finance-tracker for dedup comparison.

    Uses tracker.py --query via subprocess for clean separation.
    """
    ft_settings = settings.get("finance_tracker", {})
    skill_dir = ft_settings.get("skill_dir", "../finance-tracker")
    tracker_script = ft_settings.get("tracker_script", "tracker.py")

    # Resolve relative path from transaction-inbox skill dir
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tracker_path = os.path.normpath(os.path.join(base_dir, skill_dir, tracker_script))

    if not os.path.exists(tracker_path):
        logger.warning(
            "Finance-tracker not found at %s — skipping ledger dedup", tracker_path,
        )
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    sql = (
        f"SELECT id, amount, category, description, transaction_date "
        f"FROM expenses WHERE transaction_date >= '{cutoff}' "
        f"AND deleted_at IS NULL ORDER BY transaction_date DESC"
    )

    try:
        result = subprocess.run(
            [sys.executable, tracker_path, "--query", sql],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(tracker_path),
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("tracker.py --query failed: %s", result.stderr.strip())
            return []

        data = json.loads(result.stdout.strip())
        if isinstance(data, list):
            logger.info("Loaded %d recent ledger entries for dedup", len(data))
            return data
        if isinstance(data, dict) and data.get("status") == "error":
            logger.warning("tracker.py query error: %s", data.get("message"))
            return []
        return []

    except subprocess.TimeoutExpired:
        logger.error("tracker.py --query timed out")
        return []
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Error querying ledger: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main dedup entry point
# ---------------------------------------------------------------------------

def deduplicate_batch(
    candidates: list,
    settings: dict,
    known_refs: dict,
) -> list:
    """
    Run dedup on a batch of parsed transaction candidates.

    Args:
        candidates: List of parsed email dicts (from parser.py)
        settings: User settings (for time_window, amount_tolerance)
        known_refs: Reference IDs from recently processed emails (from state.py)

    Returns:
        The same candidates list, each annotated with a "dedup_status" field.
    """
    dedup_config = settings.get("dedup", {})
    time_window = dedup_config.get("time_window_minutes", 30)
    amount_tolerance = dedup_config.get("amount_tolerance", 1.0)

    # Fetch recent ledger entries for cross-checking
    ledger_entries = _query_recent_ledger(settings)

    # Track within-batch for cross-email dedup
    batch_seen = []  # List of (candidate_index, transaction_dict)

    stats = {"new": 0, "auto_merged": 0, "probable_duplicate": 0, "definite_duplicate": 0}

    for i, candidate in enumerate(candidates):
        txn = candidate.get("transaction")
        if not txn:
            # LLM-needed items can't be deduped yet
            candidate["dedup_status"] = STATUS_NEW
            stats["new"] += 1
            logger.debug(
                "DEDUP_SKIP email_uid=%s reason=no_transaction_data",
                candidate.get("email_uid"),
            )
            continue

        ref_ids = txn.get("reference_ids", {})

        # --- Stage 1: Hard match against known processed emails ---
        if ref_ids and _hard_match(ref_ids, known_refs):
            candidate["dedup_status"] = STATUS_DEFINITE_DUPLICATE
            stats["definite_duplicate"] += 1
            continue

        # --- Stage 2a: Soft match against ledger ---
        matched_ledger = False
        for entry in ledger_entries:
            status = _soft_match(txn, entry, time_window, amount_tolerance)
            if status == STATUS_AUTO_MERGED:
                candidate["dedup_status"] = STATUS_AUTO_MERGED
                candidate["merged_with_ledger_id"] = entry.get("id")
                stats["auto_merged"] += 1
                matched_ledger = True
                break
            elif status == STATUS_PROBABLE_DUPLICATE:
                candidate["dedup_status"] = STATUS_PROBABLE_DUPLICATE
                candidate["probable_match_ledger_id"] = entry.get("id")
                stats["probable_duplicate"] += 1
                matched_ledger = True
                break

        if matched_ledger:
            continue

        # --- Stage 2b: Within-batch dedup ---
        matched_batch = False
        for j, (prev_idx, prev_txn) in enumerate(batch_seen):
            status = _soft_match(txn, prev_txn, time_window, amount_tolerance)
            if status in (STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE):
                # Keep the one with more reference IDs
                prev_refs = prev_txn.get("reference_ids", {})
                curr_refs = ref_ids
                if len(curr_refs) > len(prev_refs):
                    # Current is richer → mark previous as duplicate
                    candidates[prev_idx]["dedup_status"] = STATUS_AUTO_MERGED
                    candidate["dedup_status"] = STATUS_NEW
                    logger.info(
                        "DEDUP_BATCH_SWAP kept=email_%d replaced=email_%d",
                        i, prev_idx,
                    )
                else:
                    candidate["dedup_status"] = STATUS_AUTO_MERGED
                    logger.info(
                        "DEDUP_BATCH_MATCH candidate=%d matches_batch=%d",
                        i, prev_idx,
                    )
                stats["auto_merged"] += 1
                matched_batch = True
                break

        if matched_batch:
            continue

        # --- No match: it's new ---
        candidate["dedup_status"] = STATUS_NEW
        stats["new"] += 1
        batch_seen.append((i, txn))

    logger.info(
        "DEDUP_STATS total=%d new=%d auto_merged=%d probable_dup=%d definite_dup=%d",
        len(candidates), stats["new"], stats["auto_merged"],
        stats["probable_duplicate"], stats["definite_duplicate"],
    )
    return candidates
