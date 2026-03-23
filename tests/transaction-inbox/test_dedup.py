"""
test_dedup.py — Tests for the duplicate detection engine.

Tests hard match (reference IDs), soft match (amount + merchant + time),
within-batch dedup, and various edge cases.
"""

import pytest
from datetime import datetime, timedelta
from lib.dedup import (
    _hard_match, _soft_match, deduplicate_batch,
    STATUS_NEW, STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE,
    STATUS_DEFINITE_DUPLICATE,
)


# ---------------------------------------------------------------------------
# Hard match tests
# ---------------------------------------------------------------------------

class TestHardMatch:
    """Stage 1: Reference ID matching."""

    def test_matching_ref_no(self):
        candidate_refs = {"ref_no": "TXN987654321"}
        known_refs = {"txn987654321": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is True

    def test_matching_utr(self):
        candidate_refs = {"utr": "ABC123"}
        known_refs = {"abc123": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is True

    def test_no_match(self):
        candidate_refs = {"ref_no": "XYZ999"}
        known_refs = {"abc123": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is False

    def test_empty_candidate_refs(self):
        candidate_refs = {}
        known_refs = {"abc123": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is False

    def test_empty_known_refs(self):
        candidate_refs = {"ref_no": "TXN123"}
        known_refs = {}
        assert _hard_match(candidate_refs, known_refs) is False

    def test_case_insensitive(self):
        candidate_refs = {"order_id": "SWG-2026-12345"}
        known_refs = {"swg-2026-12345": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is True

    def test_none_ref_value_skipped(self):
        candidate_refs = {"ref_no": None, "utr": "ABC123"}
        known_refs = {"abc123": {"message_id": "old@email.com"}}
        assert _hard_match(candidate_refs, known_refs) is True


# ---------------------------------------------------------------------------
# Soft match tests
# ---------------------------------------------------------------------------

class TestSoftMatch:
    """Stage 2: Heuristic matching."""

    def test_exact_match_high_score(self):
        """Same amount, merchant, direction, and time → auto_merged."""
        candidate = {
            "amount": 450,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 450,
            "merchant": "Swiggy order",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        result = _soft_match(candidate, existing)
        assert result in (STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE)

    def test_amount_within_tolerance(self):
        """Amount within ±₹1 should still match."""
        candidate = {
            "amount": 449.50,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 450.00,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        result = _soft_match(candidate, existing, amount_tolerance=1.0)
        assert result in (STATUS_AUTO_MERGED, STATUS_PROBABLE_DUPLICATE)

    def test_amount_outside_tolerance(self):
        """Amount difference > tolerance should not match."""
        candidate = {
            "amount": 500,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 450,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        result = _soft_match(candidate, existing)
        # Score: merchant(1) + direction(1) + time(1) = 3 → probable_duplicate
        # But amount doesn't match (score only 3), so at most probable
        assert result != STATUS_AUTO_MERGED

    def test_different_merchants(self):
        """Different merchants should reduce score."""
        candidate = {
            "amount": 450,
            "merchant": "Zomato",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 450,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        result = _soft_match(candidate, existing)
        # Amount matches (2) + direction (1) + time (1) = 4 → auto_merged
        # But different merchant, so score = 2 + 0 + 1 + 1 = 4
        # This is actually the bank vs merchant email case
        assert result is not None  # Should flag something

    def test_outside_time_window(self):
        """Transactions 2 hours apart should not match on time."""
        candidate = {
            "amount": 450,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 450,
            "merchant": "Swiggy",
            "direction": "debit",
            "transaction_date": "2026-03-22",
        }
        result = _soft_match(candidate, existing, time_window_minutes=30)
        # Days apart → time doesn't match. Score: 2 + 1 + 1 = 4 → but only
        # because we parse dates without time, both become midnight
        # Actually dates are different days so time check fails
        assert result is not None  # Amount + merchant still flag it

    def test_completely_different(self):
        """Completely different transactions should return None."""
        candidate = {
            "amount": 100,
            "merchant": "Uber",
            "direction": "debit",
            "transaction_date": "2026-03-23",
        }
        existing = {
            "amount": 5000,
            "merchant": "Amazon",
            "direction": "debit",
            "transaction_date": "2026-03-20",
        }
        result = _soft_match(candidate, existing)
        assert result is None


# ---------------------------------------------------------------------------
# Batch dedup tests
# ---------------------------------------------------------------------------

class TestDeduplicateBatch:
    """Full batch deduplication pipeline."""

    def _make_candidate(self, uid, amount, merchant, date, ref_ids=None):
        return {
            "email_uid": uid,
            "message_id": f"<{uid}@test.com>",
            "parse_method": "regex",
            "transaction": {
                "amount": amount,
                "merchant": merchant,
                "direction": "debit",
                "transaction_date": date,
                "reference_ids": ref_ids or {},
            },
        }

    def test_all_new(self):
        """No duplicates → all marked as new."""
        candidates = [
            self._make_candidate("1", 100, "Uber", "2026-03-23"),
            self._make_candidate("2", 500, "Swiggy", "2026-03-23"),
            self._make_candidate("3", 2500, "BigBasket", "2026-03-22"),
        ]
        settings = {"dedup": {"time_window_minutes": 30, "amount_tolerance": 1.0}}
        result = deduplicate_batch(candidates, settings, {})
        statuses = [c["dedup_status"] for c in result]
        assert all(s == STATUS_NEW for s in statuses)

    def test_hard_duplicate_detected(self):
        """Same ref_no as a known processed email → definite duplicate."""
        candidates = [
            self._make_candidate("1", 450, "Swiggy", "2026-03-23", {"order_id": "SWG-123"}),
        ]
        known_refs = {"swg-123": {"message_id": "<old@test.com>"}}
        settings = {"dedup": {"time_window_minutes": 30, "amount_tolerance": 1.0}}
        result = deduplicate_batch(candidates, settings, known_refs)
        assert result[0]["dedup_status"] == STATUS_DEFINITE_DUPLICATE

    def test_within_batch_dedup(self):
        """Two emails in same batch for same transaction → one gets merged."""
        candidates = [
            self._make_candidate("1", 450, "Swiggy", "2026-03-23", {"order_id": "SWG-123"}),
            self._make_candidate("2", 450, "Swiggy order", "2026-03-23"),
        ]
        settings = {"dedup": {"time_window_minutes": 30, "amount_tolerance": 1.0}}
        result = deduplicate_batch(candidates, settings, {})
        statuses = [c["dedup_status"] for c in result]
        # One should be new, one should be merged
        assert STATUS_NEW in statuses
        assert STATUS_AUTO_MERGED in statuses or STATUS_PROBABLE_DUPLICATE in statuses

    def test_llm_needed_treated_as_new(self):
        """Candidates without transaction data (LLM needed) are marked new."""
        candidates = [{
            "email_uid": "1",
            "message_id": "<1@test.com>",
            "parse_method": "llm_needed",
            "transaction": None,
        }]
        settings = {"dedup": {"time_window_minutes": 30, "amount_tolerance": 1.0}}
        result = deduplicate_batch(candidates, settings, {})
        assert result[0]["dedup_status"] == STATUS_NEW

    def test_empty_batch(self):
        """Empty candidates list should not crash."""
        settings = {"dedup": {"time_window_minutes": 30, "amount_tolerance": 1.0}}
        result = deduplicate_batch([], settings, {})
        assert result == []
