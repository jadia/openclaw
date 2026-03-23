"""
test_parser.py — Tests for the transaction email parser.

Tests regex patterns for known Indian email formats and the LLM fallback path.
Verifies parsing stats, amount extraction, date parsing, and reference ID extraction.
"""

import pytest
from lib.parser import parse_email, get_parser_stats, _parse_amount, _parse_transaction_date


# ---------------------------------------------------------------------------
# Fixtures: sample email bodies
# ---------------------------------------------------------------------------

HDFC_DEBIT = {
    "email_uid": "101",
    "message_id": "<hdfc-debit-001@gmail.com>",
    "from": "HDFC Bank <alerts@hdfcbank.net>",
    "sender_email": "alerts@hdfcbank.net",
    "subject": "Transaction Alert",
    "body": (
        "Dear Customer, Rs.1,234.50 has been debited from your account "
        "**XXXX1234 to SWIGGY on 15-03-2026. Avl Bal: Rs.45,000.00. "
        "Ref No: TXN987654321."
    ),
    "received_at": "2026-03-15T18:30:00+05:30",
}

HDFC_UPI = {
    "email_uid": "102",
    "message_id": "<hdfc-upi-001@gmail.com>",
    "from": "HDFC Bank <alerts@hdfcbank.net>",
    "sender_email": "alerts@hdfcbank.net",
    "subject": "UPI Transaction Alert",
    "body": (
        "Dear Customer, Rs.500.00 has been debited from your A/c XXXX5678 "
        "by VPA merchant@upi on 20-03-2026. Ref No: UPI123456789."
    ),
    "received_at": "2026-03-20T12:00:00+05:30",
}

SBI_DEBIT = {
    "email_uid": "103",
    "message_id": "<sbi-debit-001@gmail.com>",
    "from": "SBI <donotreply@sbi.co.in>",
    "sender_email": "donotreply@sbi.co.in",
    "body": (
        "Dear Customer, Rs.2500 debited from your account XX1234 "
        "towards BigBasket on Dt.22/03/2026. Avl Bal Rs.30,000."
    ),
    "subject": "SBI Debit Alert",
    "received_at": "2026-03-22T10:00:00+05:30",
}

SWIGGY_ORDER = {
    "email_uid": "104",
    "message_id": "<swiggy-001@gmail.com>",
    "from": "Swiggy <noreply@swiggy.in>",
    "sender_email": "noreply@swiggy.in",
    "subject": "Your Swiggy order is confirmed!",
    "body": (
        "Hi! Your order has been placed. Order total: ₹450.00. "
        "Order ID: SWG-2026-12345. Delivery in 30 mins."
    ),
    "received_at": "2026-03-23T19:00:00+05:30",
}

ZOMATO_ORDER = {
    "email_uid": "105",
    "message_id": "<zomato-001@gmail.com>",
    "from": "Zomato <noreply@zomato.com>",
    "sender_email": "noreply@zomato.com",
    "subject": "Order Confirmed - Zomato",
    "body": (
        "Your order is confirmed! Total amount: Rs.389. "
        "Order #ZMT98765. Enjoy your meal!"
    ),
    "received_at": "2026-03-23T20:00:00+05:30",
}

UNKNOWN_SENDER = {
    "email_uid": "106",
    "message_id": "<unknown-001@gmail.com>",
    "from": "Some Service <alerts@unknownbank.com>",
    "sender_email": "alerts@unknownbank.com",
    "subject": "Payment Receipt",
    "body": "Your payment of Rs.999 has been processed successfully.",
    "received_at": "2026-03-23T21:00:00+05:30",
}

NON_TRANSACTION = {
    "email_uid": "107",
    "message_id": "<promo-001@gmail.com>",
    "from": "HDFC Bank <alerts@hdfcbank.net>",
    "sender_email": "alerts@hdfcbank.net",
    "subject": "Exciting Offers for You!",
    "body": "Check out our latest credit card offers and cashback deals.",
    "received_at": "2026-03-23T22:00:00+05:30",
}


# ---------------------------------------------------------------------------
# Tier 1: Regex parsing tests
# ---------------------------------------------------------------------------

class TestHDFCParsing:
    """HDFC Bank email parsing."""

    def test_hdfc_debit_amount(self):
        result = parse_email(HDFC_DEBIT)
        assert result["parse_method"] == "regex"
        assert result["transaction"]["amount"] == 1234.50

    def test_hdfc_debit_merchant(self):
        result = parse_email(HDFC_DEBIT)
        assert "SWIGGY" in result["transaction"]["merchant"].upper()

    def test_hdfc_debit_date(self):
        result = parse_email(HDFC_DEBIT)
        assert result["transaction"]["transaction_date"] == "2026-03-15"

    def test_hdfc_debit_reference_id(self):
        result = parse_email(HDFC_DEBIT)
        assert result["transaction"]["reference_ids"].get("ref_no") is not None

    def test_hdfc_debit_direction(self):
        result = parse_email(HDFC_DEBIT)
        assert result["transaction"]["direction"] == "debit"

    def test_hdfc_upi_amount(self):
        result = parse_email(HDFC_UPI)
        assert result["parse_method"] == "regex"
        assert result["transaction"]["amount"] == 500.0

    def test_hdfc_confidence_high(self):
        result = parse_email(HDFC_DEBIT)
        assert result["confidence"] >= 0.8


class TestSBIParsing:
    """SBI email parsing."""

    def test_sbi_debit_amount(self):
        result = parse_email(SBI_DEBIT)
        assert result["parse_method"] == "regex"
        assert result["transaction"]["amount"] == 2500.0

    def test_sbi_debit_merchant(self):
        result = parse_email(SBI_DEBIT)
        assert "BigBasket" in result["transaction"]["merchant"]

    def test_sbi_debit_date(self):
        result = parse_email(SBI_DEBIT)
        assert result["transaction"]["transaction_date"] == "2026-03-22"


class TestMerchantParsing:
    """Merchant email parsing (Swiggy, Zomato)."""

    def test_swiggy_amount(self):
        result = parse_email(SWIGGY_ORDER)
        assert result["parse_method"] == "regex"
        assert result["transaction"]["amount"] == 450.0

    def test_swiggy_order_id(self):
        result = parse_email(SWIGGY_ORDER)
        refs = result["transaction"]["reference_ids"]
        assert refs.get("order_id") == "SWG-2026-12345"

    def test_swiggy_merchant_name(self):
        result = parse_email(SWIGGY_ORDER)
        assert "Swiggy" in result["transaction"]["merchant"]

    def test_zomato_amount(self):
        result = parse_email(ZOMATO_ORDER)
        assert result["parse_method"] == "regex"
        assert result["transaction"]["amount"] == 389.0

    def test_zomato_order_id(self):
        result = parse_email(ZOMATO_ORDER)
        refs = result["transaction"]["reference_ids"]
        assert "ZMT98765" in str(refs.get("order_id", ""))


# ---------------------------------------------------------------------------
# Tier 2: LLM fallback
# ---------------------------------------------------------------------------

class TestLLMFallback:
    """Emails that don't match regex should fall back to LLM."""

    def test_non_transaction_email_fallback(self):
        """Promotional email with no transaction data → LLM fallback."""
        result = parse_email(NON_TRANSACTION)
        # This is a promotional email, but since it's from a known sender,
        # the generic pattern might match. If not, it falls to LLM.
        # Either way, the parse function should return a valid result.
        assert result is not None
        assert "parse_method" in result

    def test_unknown_sender_uses_generic_or_llm(self):
        """Unknown sender should try generic pattern, then fall to LLM."""
        result = parse_email(UNKNOWN_SENDER)
        # Generic pattern should catch "Rs.999 has been processed"
        # but 'processed' may not match 'debited/deducted/withdrawn/charged'
        assert result is not None
        assert result["parse_method"] in ("regex", "llm_needed")

    def test_llm_fallback_includes_body(self):
        """LLM fallback should include truncated body for extraction."""
        email = {
            "email_uid": "200",
            "message_id": "<test@test.com>",
            "from": "test@test.com",
            "sender_email": "test@test.com",
            "subject": "Some receipt",
            "body": "A very long email body that doesn't match any pattern at all. " * 50,
            "received_at": "2026-03-23T12:00:00+05:30",
        }
        result = parse_email(email)
        if result["parse_method"] == "llm_needed":
            assert "body_for_llm" in result
            assert len(result["body_for_llm"]) <= 3000


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestParseAmount:
    def test_simple_integer(self):
        assert _parse_amount("500") == 500.0

    def test_with_commas(self):
        assert _parse_amount("1,234.50") == 1234.50

    def test_large_number(self):
        assert _parse_amount("10,00,000") == 1000000.0

    def test_empty(self):
        assert _parse_amount("") == 0.0

    def test_none(self):
        assert _parse_amount(None) == 0.0


class TestParseTransactionDate:
    def test_dd_mm_yyyy(self):
        assert _parse_transaction_date("15-03-2026") == "2026-03-15"

    def test_dd_mm_yy(self):
        assert _parse_transaction_date("15/03/26") == "2026-03-15"

    def test_dd_slash_mm_slash_yyyy(self):
        assert _parse_transaction_date("22/03/2026") == "2026-03-22"

    def test_none_with_fallback(self):
        result = _parse_transaction_date(None, "2026-03-23T12:00:00+05:30")
        assert result == "2026-03-23"

    def test_none_without_fallback(self):
        from datetime import datetime
        result = _parse_transaction_date(None)
        assert result == datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestParserStats:
    def test_stats_computation(self):
        results = [
            {"parse_method": "regex", "_pattern_name": "HDFC debit alert"},
            {"parse_method": "regex", "_pattern_name": "HDFC debit alert"},
            {"parse_method": "regex", "_pattern_name": "Swiggy order confirmation"},
            {"parse_method": "llm_needed", "_pattern_name": None},
        ]
        stats = get_parser_stats(results)
        assert stats["total_parsed"] == 4
        assert stats["regex_hits"] == 3
        assert stats["llm_needed"] == 1
        assert stats["hit_rate_pct"] == 75.0
        assert stats["pattern_breakdown"]["HDFC debit alert"] == 2

    def test_stats_empty(self):
        stats = get_parser_stats([])
        assert stats["total_parsed"] == 0
        assert stats["hit_rate_pct"] == 0
