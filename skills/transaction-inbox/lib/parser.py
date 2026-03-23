"""
parser.py — Two-tier transaction email parser.

Tier 1: Regex-based extraction for known senders (Indian banks, merchants).
Tier 2: Structured extraction prompt for OpenClaw LLM fallback.

Every parsing attempt is logged with hit/miss status so log files can be
fed back to AI for pattern improvement.
"""

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier 1: Regex pattern registry
# ---------------------------------------------------------------------------
# Each entry maps a sender-email-substring to a list of pattern dicts.
# Patterns are tried in order; first match wins.
# Each pattern dict has:
#   "name"    — human label for logging
#   "regex"   — compiled regex with named groups
#   "post"    — optional post-processing function

PATTERN_REGISTRY = {}


def _register(sender_key: str, name: str, pattern: str, flags=re.IGNORECASE | re.DOTALL):
    """Register a regex pattern for a sender."""
    if sender_key not in PATTERN_REGISTRY:
        PATTERN_REGISTRY[sender_key] = []
    PATTERN_REGISTRY[sender_key].append({
        "name": name,
        "regex": re.compile(pattern, flags),
    })


# --- HDFC Bank ---
_register(
    "hdfcbank",
    "HDFC debit alert",
    r"(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:has been|was)\s*debited"
    r".*?(?:to\s+(?P<merchant>.+?)(?:\s+on|\s+at|\s*\.|\s+Avl))"
    r"(?:.*?(?:on|dated?)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r"(?:.*?(?:UPI[:\s]*(?P<upi_ref>\d+)|Ref\.?\s*(?:No\.?)?\s*:?\s*(?P<ref_no>\w+)))?"
)
_register(
    "hdfcbank",
    "HDFC UPI debit",
    r"(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:has been|was)\s*debited.*?"
    r"(?:VPA|UPI)\s*(?P<upi_id>[\w.@]+)"
    r"(?:.*?Ref\.?\s*(?:No\.?)?\s*:?\s*(?P<ref_no>\w+))?"
)

# --- SBI ---
_register(
    "sbi",
    "SBI debit alert",
    r"(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:debited|withdrawn)"
    r".*?(?:(?:to|towards|at)\s+(?P<merchant>.+?)(?:\s+on|\s+Dt|\s*\.|\s+Avl|$))"
    r".*?(?:(?:on|Dt\.?)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r".*?(?:Ref\.?\s*(?:No\.?\s*)?:?\s*(?P<ref_no>\w+))?"
)

# --- ICICI ---
_register(
    "icicibank",
    "ICICI debit alert",
    r"(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:has been|was)\s*debited"
    r".*?(?:(?:to|at)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+Avl|$))"
    r".*?(?:(?:on)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r".*?(?:Ref\.?\s*(?:No\.?\s*)?:?\s*(?P<ref_no>\w+))?"
)

# --- Axis Bank ---
_register(
    "axisbank",
    "Axis debit alert",
    r"(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:has been|was)\s*debited"
    r".*?(?:(?:to|at)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+Avl|$))"
    r".*?(?:(?:on)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r".*?(?:Ref\.?\s*(?:No\.?\s*)?:?\s*(?P<ref_no>\w+)|UTR[:\s]*(?P<utr>\w+))?"
)

# --- Equitas ---
_register(
    "equitas",
    "Equitas debit alert",
    r"(?:Rs\.?|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:has been|was)\s*debited"
    r".*?(?:(?:to|at)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+Avl|$))"
    r".*?(?:(?:on|dated?)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r".*?(?:Ref\.?\s*(?:No\.?\s*)?:?\s*(?P<ref_no>\w+))?"
)

# --- Swiggy ---
_register(
    "swiggy",
    "Swiggy order confirmation",
    r"(?:order|total|paid|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r"(?:.*?(?:order\s*(?:id|#|no\.?)[:\s]*(?P<order_id>[\w-]+)))?"
)

# --- Zomato ---
_register(
    "zomato",
    "Zomato order confirmation",
    r"(?:order|total|paid|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r"(?:.*?(?:order\s*(?:id|#|no\.?)[:\s]*(?P<order_id>[\w-]+)))?"
)

# --- Amazon ---
_register(
    "amazon",
    "Amazon order confirmation",
    r"(?:order|total|grand\s+total|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r"(?:.*?(?:order\s*(?:id|#|no\.?)[:\s]*(?P<order_id>[\w-]+)))?"
)

# --- Flipkart ---
_register(
    "flipkart",
    "Flipkart order confirmation",
    r"(?:order|total|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r"(?:.*?(?:order\s*(?:id|#|no\.?)[:\s]*(?P<order_id>[\w-]+)))?"
)

# --- Uber ---
_register(
    "uber",
    "Uber trip receipt",
    r"(?:total|fare|charged|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r".*?(?:trip\s*(?:id)?[:\s]*(?P<trip_id>[\w-]+))?"
)

# --- Ola ---
_register(
    "ola",
    "Ola ride receipt",
    r"(?:total|fare|charged|amount)[:\s]*(?:Rs\.?|₹|INR)?\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)"
    r".*?(?:booking\s*(?:id)?[:\s]*(?P<booking_id>[\w-]+))?"
)

# --- PhonePe ---
_register(
    "phonepe",
    "PhonePe transaction",
    r"(?:Rs\.?|₹|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:paid|debited|sent)"
    r".*?(?:(?:to)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+via|$))"
    r".*?(?:(?:transaction\s*id|UTR)[:\s]*(?P<ref_no>\w+))?"
)

# --- Paytm ---
_register(
    "paytm",
    "Paytm transaction",
    r"(?:Rs\.?|₹|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*(?:paid|debited|sent)"
    r".*?(?:(?:to)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+via|$))"
    r".*?(?:(?:order\s*id|transaction\s*id)[:\s]*(?P<ref_no>\w+))?"
)

# --- Generic debit pattern (fallback for unknown banks) ---
_register(
    "__generic__",
    "Generic debit pattern",
    r"(?:Rs\.?|₹|INR)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*"
    r"(?:has been|was|is)\s*(?:debited|deducted|withdrawn|charged)"
    r".*?(?:(?:to|at|from|towards)\s+(?P<merchant>.+?)(?:\s+on|\s*\.|\s+Avl|$))?"
    r".*?(?:(?:on|dated?)\s*(?P<date>\d{2}[-/]\d{2}[-/]\d{2,4}))?"
    r".*?(?:Ref\.?\s*(?:No\.?\s*)?:?\s*(?P<ref_no>\w+)|UTR[:\s]*(?P<utr>\w+))?"
)


# ---------------------------------------------------------------------------
# Tier 1: Regex parsing
# ---------------------------------------------------------------------------

def _find_sender_key(sender_email: str) -> str:
    """Map a sender email address to a pattern registry key."""
    sender_lower = sender_email.lower()
    for key in PATTERN_REGISTRY:
        if key == "__generic__":
            continue
        if key in sender_lower:
            return key
    return None


def _parse_amount(raw: str) -> float:
    """Parse amount string like '1,234.50' or '1234' to float."""
    if not raw:
        return 0.0
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_transaction_date(raw: str, fallback: str = None) -> str:
    """Parse date from regex match to YYYY-MM-DD format."""
    if not raw:
        if fallback:
            try:
                dt = datetime.fromisoformat(fallback)
                return dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        return datetime.now().strftime("%Y-%m-%d")

    # Try common Indian date formats
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return datetime.now().strftime("%Y-%m-%d")


def _extract_reference_ids(match_dict: dict) -> dict:
    """Build reference_ids dict from named groups in regex match."""
    refs = {}
    ref_fields = ["ref_no", "upi_ref", "upi_id", "utr", "order_id", "trip_id", "booking_id"]
    for field in ref_fields:
        val = match_dict.get(field)
        if val and val.strip():
            refs[field] = val.strip()
    return refs


def _determine_merchant(match_dict: dict, sender_email: str, subject: str) -> str:
    """Determine merchant name from regex match or sender/subject fallback."""
    merchant = (match_dict.get("merchant") or "").strip()
    if merchant:
        # Clean up common trailing noise
        merchant = re.sub(r"\s*(Avl|Bal|Balance|Available|Ref).*$", "", merchant, flags=re.IGNORECASE)
        merchant = merchant.strip(" .")
        if merchant:
            return merchant

    # Fallback: derive from sender
    sender_lower = sender_email.lower()
    if "swiggy" in sender_lower:
        return "Swiggy"
    if "zomato" in sender_lower:
        return "Zomato"
    if "amazon" in sender_lower:
        return "Amazon"
    if "flipkart" in sender_lower:
        return "Flipkart"
    if "uber" in sender_lower:
        return "Uber"
    if "ola" in sender_lower:
        return "Ola"
    if "phonepe" in sender_lower:
        return "PhonePe"
    if "paytm" in sender_lower:
        return "Paytm"

    # Last resort: clean subject
    return subject[:60] if subject else "Unknown"


# ---------------------------------------------------------------------------
# Main parsing entry point
# ---------------------------------------------------------------------------

def parse_email(email_data: dict, custom_patterns: dict = None) -> dict:
    """
    Parse a single email for transaction data using the two-tier approach.

    Args:
        email_data: Dict from GmailClient with keys:
            email_uid, message_id, sender_email, subject, body, received_at
        custom_patterns: Optional user-defined patterns from settings

    Returns:
        Dict with parsed transaction data and metadata. Key field is
        "parse_method" which is either "regex" or "llm_needed".
    """
    sender = email_data.get("sender_email", "")
    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    received_at = email_data.get("received_at", "")
    text_to_parse = f"{subject}\n{body}"

    logger.info(
        "PARSE_START email_uid=%s sender=%s subject=%.80s",
        email_data.get("email_uid"), sender, subject,
    )

    # --- Tier 1: Try regex patterns ---
    result = _try_regex_parse(sender, text_to_parse, email_data)
    if result:
        logger.info(
            "PARSE_HIT tier=regex pattern=%s email_uid=%s amount=%.2f merchant=%s",
            result.get("_pattern_name", "?"),
            email_data.get("email_uid"),
            result["transaction"].get("amount", 0),
            result["transaction"].get("merchant", "?"),
        )
        return result

    # --- Tier 2: Prepare for LLM extraction ---
    logger.info(
        "PARSE_MISS tier=regex email_uid=%s sender=%s — falling back to LLM",
        email_data.get("email_uid"), sender,
    )
    return _build_llm_fallback(email_data)


def _try_regex_parse(sender: str, text: str, email_data: dict) -> dict:
    """Attempt tier-1 regex parsing. Returns parsed dict or None."""
    sender_key = _find_sender_key(sender)
    patterns_to_try = []

    # Add sender-specific patterns first
    if sender_key and sender_key in PATTERN_REGISTRY:
        patterns_to_try.extend(PATTERN_REGISTRY[sender_key])

    # Always try generic patterns as fallback
    if "__generic__" in PATTERN_REGISTRY:
        patterns_to_try.extend(PATTERN_REGISTRY["__generic__"])

    for pattern in patterns_to_try:
        match = pattern["regex"].search(text)
        if match:
            groups = match.groupdict()
            amount = _parse_amount(groups.get("amount"))
            if amount <= 0:
                logger.debug(
                    "PARSE_SKIP pattern=%s reason=zero_amount email_uid=%s",
                    pattern["name"], email_data.get("email_uid"),
                )
                continue

            merchant = _determine_merchant(groups, sender, email_data.get("subject", ""))
            txn_date = _parse_transaction_date(groups.get("date"), email_data.get("received_at"))
            ref_ids = _extract_reference_ids(groups)

            # Build description
            desc_parts = [merchant]
            for id_type, id_val in ref_ids.items():
                desc_parts.append(f"[{id_type}: {id_val}]")
            description = " ".join(desc_parts)

            return {
                "email_id": email_data.get("message_id"),
                "email_uid": email_data.get("email_uid"),
                "from": email_data.get("from", sender),
                "subject": email_data.get("subject", ""),
                "received_at": email_data.get("received_at", ""),
                "parse_method": "regex",
                "confidence": 0.9 if sender_key else 0.7,
                "_pattern_name": pattern["name"],
                "transaction": {
                    "amount": amount,
                    "merchant": merchant,
                    "direction": "debit",
                    "transaction_date": txn_date,
                    "description": description,
                    "reference_ids": ref_ids,
                },
            }
        else:
            logger.debug(
                "PARSE_TRY pattern=%s result=no_match email_uid=%s",
                pattern["name"], email_data.get("email_uid"),
            )

    return None


def _build_llm_fallback(email_data: dict) -> dict:
    """
    Build a structured dict for LLM fallback parsing.

    The body is included so OpenClaw can extract transaction details
    from it via its LLM capabilities.
    """
    return {
        "email_id": email_data.get("message_id"),
        "email_uid": email_data.get("email_uid"),
        "from": email_data.get("from", ""),
        "subject": email_data.get("subject", ""),
        "received_at": email_data.get("received_at", ""),
        "parse_method": "llm_needed",
        "confidence": 0.0,
        "_pattern_name": None,
        "body_for_llm": email_data.get("body", "")[:3000],  # Truncate to avoid huge prompts
        "transaction": None,
    }


# ---------------------------------------------------------------------------
# Stats helper for logging
# ---------------------------------------------------------------------------

def get_parser_stats(results: list) -> dict:
    """
    Compute parsing statistics for a batch of results.

    Useful for logging and debugging regex hit rates.
    """
    total = len(results)
    regex_hits = sum(1 for r in results if r.get("parse_method") == "regex")
    llm_needed = sum(1 for r in results if r.get("parse_method") == "llm_needed")

    # Per-pattern breakdown
    pattern_counts = {}
    for r in results:
        name = r.get("_pattern_name", "llm_fallback")
        pattern_counts[name] = pattern_counts.get(name, 0) + 1

    stats = {
        "total_parsed": total,
        "regex_hits": regex_hits,
        "llm_needed": llm_needed,
        "hit_rate_pct": round((regex_hits / total) * 100, 1) if total > 0 else 0,
        "pattern_breakdown": pattern_counts,
    }

    logger.info(
        "PARSE_STATS total=%d regex_hits=%d llm_needed=%d hit_rate=%.1f%%",
        total, regex_hits, llm_needed, stats["hit_rate_pct"],
    )
    for pattern_name, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        logger.info("  PATTERN %s: %d hits", pattern_name, count)

    return stats
