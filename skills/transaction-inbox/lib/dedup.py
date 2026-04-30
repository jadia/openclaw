"""
dedup.py — Two-stage duplicate detection for transaction candidates.

Stage 1 (Hard match): Reference ID comparison (UTR, order_id, ref_no, etc.)
Stage 2 (Soft match): Amount + merchant + time window heuristic with
                       date-gap penalties to prevent false positives.

Also handles within-batch dedup (multiple emails for same transaction in
one processing run) and against-ledger dedup (comparing with recent
finance-tracker entries).

Scoring system (v2):
  Amount match (±₹1):       +2.0
  Merchant/desc fuzzy:      +1.0
  Direction match:          +0.5  (reduced — debit is almost always the case)
  Time within window:       +1.5  (increased — strongest non-ID signal)
  Date gap >2h:             −1.0  (penalty)
  Date gap >24h:            −2.0  (penalty — replaces the −1.0)
  Date gap >hard_cutoff:    immediate reject (configurable, default 48h)

Thresholds:
  auto_merged:              score ≥ 4.5
  probable_duplicate:       score ≥ 3.5
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

# Default scoring weights
_SCORE_AMOUNT_MATCH = 2.0
_SCORE_MERCHANT_MATCH = 1.0
_SCORE_DIRECTION_MATCH = 0.5
_SCORE_TIME_MATCH = 1.5
_PENALTY_DATE_GAP_2H = -1.0
_PENALTY_DATE_GAP_24H = -2.0

# Default thresholds
_THRESHOLD_AUTO_MERGE = 4.5
_THRESHOLD_PROBABLE = 3.5

# Default hard cutoff (hours) — configurable via settings
_DEFAULT_MAX_DATE_GAP_HOURS = 48


def _soft_match(
    candidate: dict,
    existing: dict,
    time_window_minutes: int = 30,
    amount_tolerance: float = 1.0,
    max_date_gap_hours: int = _DEFAULT_MAX_DATE_GAP_HOURS,
) -> str:
    """
    Heuristic duplicate check using amount, merchant, direction, and time.

    Scoring (v2):
      Amount match (±tolerance):    +2.0
      Merchant fuzzy match:         +1.0
      Direction match:              +0.5
      Time within window:           +1.5
      Date gap >2h:                 −1.0  (penalty)
      Date gap >24h:                −2.0  (replaces −1.0)
      Date gap >max_date_gap_hours: immediate reject

    Thresholds:
      auto_merged:       score ≥ 4.5
      probable_duplicate: score ≥ 3.5

    Examples:
      ₹80 + same merchant + same direction + same time  = 2+1+0.5+1.5 = 5.0 → auto_merged ✓
      ₹80 + same merchant + same direction + 3 days gap = 2+1+0.5−2   = 1.5 → None ✓
      ₹80 + same merchant + same direction + 5h gap     = 2+1+0.5−1   = 2.5 → None ✓
      ₹80 + diff merchant + same direction + same time  = 2+0+0.5+1.5 = 4.0 → probable_duplicate
      Same UTR → handled by hard match, never reaches here

    Args:
        candidate: Parsed transaction dict
        existing: Existing transaction/expense dict to compare against
        time_window_minutes: Time tolerance for matching (default ±30 min)
        amount_tolerance: Amount tolerance in ₹ (default ±1)
        max_date_gap_hours: Hard cutoff — reject if dates are further apart

    Returns:
        STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE, or None (no match)
    """
    score = 0.0
    reasons = []

    # --- Parse times first for early rejection ---
    cand_time = _parse_time(candidate.get("transaction_date"))
    exist_time = _parse_time(existing.get("transaction_date"))

    if cand_time and exist_time:
        delta_minutes = abs((cand_time - exist_time).total_seconds()) / 60
        delta_hours = delta_minutes / 60

        # Hard cutoff: reject immediately if dates are too far apart
        if delta_hours > max_date_gap_hours:
            logger.debug(
                "DEDUP_SOFT_REJECT reason=date_gap_exceeds_%dh "
                "gap=%.1fh candidate_date=%s existing_date=%s",
                max_date_gap_hours, delta_hours,
                candidate.get("transaction_date"),
                existing.get("transaction_date"),
            )
            return None

        # Time proximity scoring
        if delta_minutes <= time_window_minutes:
            score += _SCORE_TIME_MATCH
            reasons.append(f"time_within_{int(delta_minutes)}min")
        elif delta_hours > 24:
            score += _PENALTY_DATE_GAP_24H
            reasons.append(f"date_gap_{delta_hours:.0f}h_PENALTY")
        elif delta_hours > 2:
            score += _PENALTY_DATE_GAP_2H
            reasons.append(f"date_gap_{delta_hours:.1f}h_penalty")
        # else: between 30min and 2h — no bonus, no penalty
    else:
        # If we can't parse dates, we can't trust the match at all.
        # Apply a moderate penalty instead of silently ignoring.
        score += _PENALTY_DATE_GAP_2H
        reasons.append("date_unknown_penalty")

    # --- Amount comparison ---
    cand_amount = candidate.get("amount", 0)
    exist_amount = existing.get("amount", 0)
    if abs(cand_amount - exist_amount) <= amount_tolerance:
        score += _SCORE_AMOUNT_MATCH
        reasons.append(f"amount_match({cand_amount}≈{exist_amount})")

    # --- Merchant comparison (fuzzy) ---
    cand_merchant = (candidate.get("merchant") or "").lower().strip()
    exist_merchant = (existing.get("merchant") or existing.get("description") or "").lower().strip()
    if cand_merchant and exist_merchant:
        if cand_merchant in exist_merchant or exist_merchant in cand_merchant:
            score += _SCORE_MERCHANT_MATCH
            reasons.append(f"merchant_fuzzy({cand_merchant}~{exist_merchant})")

    # --- Direction comparison ---
    cand_dir = candidate.get("direction", "debit")
    exist_dir = existing.get("direction", "debit")
    if cand_dir == exist_dir:
        score += _SCORE_DIRECTION_MATCH
        reasons.append("direction_match")

    # --- Determine result ---
    reason_str = ", ".join(reasons) if reasons else "no_criteria_met"

    if score >= _THRESHOLD_AUTO_MERGE:
        logger.info(
            "DEDUP_SOFT_MATCH result=auto_merged score=%.1f reasons=[%s]",
            score, reason_str,
        )
        return STATUS_AUTO_MERGED

    if score >= _THRESHOLD_PROBABLE:
        logger.info(
            "DEDUP_SOFT_MATCH result=probable_duplicate score=%.1f reasons=[%s]",
            score, reason_str,
        )
        return STATUS_PROBABLE_DUPLICATE

    logger.debug(
        "DEDUP_SOFT_NO_MATCH score=%.1f reasons=[%s]", score, reason_str,
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
        f"SELECT id, amount, category, description, transaction_date, inserted_on "
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
    max_date_gap_hours = dedup_config.get("max_date_gap_hours", _DEFAULT_MAX_DATE_GAP_HOURS)

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
            status = _soft_match(
                txn, entry, time_window, amount_tolerance, max_date_gap_hours,
            )
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
            status = _soft_match(
                txn, prev_txn, time_window, amount_tolerance, max_date_gap_hours,
            )
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
