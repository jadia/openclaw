"""
Tests for the dedup scoring system (v2).

Validates that:
  - Same amount + same merchant + days apart → NOT a duplicate
  - Same amount + same merchant + same time → auto_merged
  - Same amount + different merchant + same time → probable_duplicate
  - Same amount + same merchant + hours apart → NOT auto_merged
  - Hard cutoff rejects matches beyond max_date_gap_hours
  - Date gap penalties kill false positives
"""

import sys
import os
import unittest
from datetime import datetime, timedelta

# Add lib/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from dedup import (
    _soft_match,
    _hard_match,
    STATUS_AUTO_MERGED,
    STATUS_PROBABLE_DUPLICATE,
)


class TestSoftMatchScoring(unittest.TestCase):
    """Tests for the _soft_match heuristic scoring system."""

    def _make_txn(self, amount=80.0, merchant="Equitas", direction="debit",
                  date=None, ref_ids=None):
        """Helper to build a transaction dict."""
        txn = {
            "amount": amount,
            "merchant": merchant,
            "direction": direction,
            "transaction_date": date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if ref_ids:
            txn["reference_ids"] = ref_ids
        return txn

    # ----- Core scenarios from the user's bug report -----

    def test_same_amount_same_merchant_3_days_apart_not_duplicate(self):
        """The exact bug: ₹80 Equitas on Apr 10 vs Apr 13 must NOT merge."""
        txn1 = self._make_txn(amount=80, merchant="report this transaction immediately",
                              date="2026-04-10 10:00:00")
        txn2 = self._make_txn(amount=80, merchant="report this transaction immediately",
                              date="2026-04-13 10:00:00")
        result = _soft_match(txn2, txn1)
        self.assertIsNone(result, "3 days apart should NOT be a duplicate")

    def test_same_amount_same_merchant_same_time_auto_merged(self):
        """Genuine duplicate: same email processed twice within minutes."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn1 = self._make_txn(amount=80, merchant="Equitas UPI", date=now)
        txn2 = self._make_txn(amount=80, merchant="Equitas UPI", date=now)
        result = _soft_match(txn2, txn1)
        self.assertEqual(result, STATUS_AUTO_MERGED)

    def test_same_amount_same_merchant_5_min_apart_auto_merged(self):
        """Two notifications for same transaction, 5 minutes apart."""
        base = datetime(2026, 4, 13, 10, 0, 0)
        txn1 = self._make_txn(amount=80, merchant="Equitas",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=80, merchant="Equitas",
                              date=(base + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1)
        self.assertEqual(result, STATUS_AUTO_MERGED)

    # ----- Date gap penalties -----

    def test_same_amount_same_merchant_25_hours_apart_not_duplicate(self):
        """25 hours apart: date gap penalty should prevent match."""
        base = datetime(2026, 4, 13, 10, 0, 0)
        txn1 = self._make_txn(amount=80, merchant="Equitas",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=80, merchant="Equitas",
                              date=(base + timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1)
        self.assertIsNone(result, "25 hours apart should NOT be a duplicate")

    def test_same_amount_same_merchant_5_hours_apart_not_auto_merged(self):
        """5 hours apart: moderate gap penalty, should not auto-merge."""
        base = datetime(2026, 4, 13, 10, 0, 0)
        txn1 = self._make_txn(amount=80, merchant="Equitas",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=80, merchant="Equitas",
                              date=(base + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1)
        # Score: amount(2) + merchant(1) + direction(0.5) + gap_penalty(-1) = 2.5
        self.assertIsNone(result, "5 hours apart should NOT be a duplicate")

    def test_hard_cutoff_rejects_beyond_48_hours(self):
        """Beyond the hard cutoff (default 48h), must be immediately rejected."""
        base = datetime(2026, 4, 10, 10, 0, 0)
        txn1 = self._make_txn(amount=80, merchant="Equitas",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=80, merchant="Equitas",
                              date=(base + timedelta(hours=49)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1, max_date_gap_hours=48)
        self.assertIsNone(result, "Beyond 48h hard cutoff must be rejected")

    def test_custom_hard_cutoff_72_hours(self):
        """Custom hard cutoff of 72h allows 49h gap but date penalty still applies."""
        base = datetime(2026, 4, 10, 10, 0, 0)
        txn1 = self._make_txn(amount=80, merchant="Equitas",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=80, merchant="Equitas",
                              date=(base + timedelta(hours=49)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1, max_date_gap_hours=72)
        # Not immediately rejected, but 24h+ penalty (-2) means score = 2+1+0.5-2 = 1.5
        self.assertIsNone(result, "49h gap with 72h cutoff: date penalty should still prevent match")

    # ----- Merchant mismatch scenarios -----

    def test_same_amount_different_merchant_same_time_probable(self):
        """Same amount, different merchant, same time → probable_duplicate."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn1 = self._make_txn(amount=500, merchant="Swiggy", date=now)
        txn2 = self._make_txn(amount=500, merchant="Zomato", date=now)
        result = _soft_match(txn2, txn1)
        # Score: amount(2) + merchant(0) + direction(0.5) + time(1.5) = 4.0
        self.assertEqual(result, STATUS_PROBABLE_DUPLICATE,
                         "Same amount, different merchant, same time → probable_duplicate")

    def test_different_amount_same_merchant_same_time_not_duplicate(self):
        """Different amounts are clearly different transactions."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn1 = self._make_txn(amount=450, merchant="HDFC", date=now)
        txn2 = self._make_txn(amount=800, merchant="HDFC", date=now)
        result = _soft_match(txn2, txn1)
        # Score: amount(0) + merchant(1) + direction(0.5) + time(1.5) = 3.0
        self.assertIsNone(result, "Different amounts should NOT be duplicates")

    # ----- Recurring subscription scenario -----

    def test_recurring_subscription_30_days_not_duplicate(self):
        """Monthly subscription: same amount, same merchant, 30 days apart."""
        base = datetime(2026, 3, 15, 10, 0, 0)
        txn1 = self._make_txn(amount=499, merchant="Netflix",
                              date=base.strftime("%Y-%m-%d %H:%M:%S"))
        txn2 = self._make_txn(amount=499, merchant="Netflix",
                              date=(base + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"))
        result = _soft_match(txn2, txn1)
        self.assertIsNone(result, "30 days apart (subscription) must NOT be duplicate")

    # ----- Edge cases -----

    def test_missing_dates_applies_penalty(self):
        """If dates can't be parsed, a penalty is applied instead of ignoring."""
        txn1 = self._make_txn(amount=80, merchant="Equitas")
        txn1["transaction_date"] = None
        txn2 = self._make_txn(amount=80, merchant="Equitas")
        txn2["transaction_date"] = None
        result = _soft_match(txn2, txn1)
        # Score: amount(2) + merchant(1) + direction(0.5) + unknown_penalty(-1) = 2.5
        self.assertIsNone(result, "Unknown dates should not auto-merge")

    def test_direction_mismatch_reduces_score(self):
        """Debit vs credit should reduce score by not getting the +0.5."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn1 = self._make_txn(amount=80, merchant="Equitas", direction="debit", date=now)
        txn2 = self._make_txn(amount=80, merchant="Equitas", direction="credit", date=now)
        result = _soft_match(txn2, txn1)
        # Score: amount(2) + merchant(1) + direction(0) + time(1.5) = 4.5
        # Still auto_merged but barely — direction mismatch matters less
        self.assertEqual(result, STATUS_AUTO_MERGED)


class TestHardMatch(unittest.TestCase):
    """Tests for hard match (reference ID comparison)."""

    def test_matching_utr(self):
        """Matching UTR should be a hard match."""
        candidate_refs = {"utr": "ABC123456"}
        known_refs = {"abc123456": {"message_id": "test@example.com"}}
        self.assertTrue(_hard_match(candidate_refs, known_refs))

    def test_no_matching_refs(self):
        """No matching refs should not be a hard match."""
        candidate_refs = {"utr": "ABC123456"}
        known_refs = {"xyz789": {"message_id": "test@example.com"}}
        self.assertFalse(_hard_match(candidate_refs, known_refs))

    def test_empty_refs(self):
        """Empty refs should not be a hard match."""
        self.assertFalse(_hard_match({}, {}))
        self.assertFalse(_hard_match({"utr": ""}, {"abc": {}}))
        self.assertFalse(_hard_match({"utr": None}, {"abc": {}}))


if __name__ == "__main__":
    unittest.main()
