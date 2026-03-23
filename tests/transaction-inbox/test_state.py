"""
test_state.py — Tests for state file management.

Tests CRUD operations on state files, pruning, and helper functions.
"""

import json
import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lib import state


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect state directory to a temp path for isolated tests."""
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "BASE_DIR", tmp_path.parent)
    state.setup_state_dir()
    return tmp_path


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

class TestSetup:
    def test_creates_state_dir(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        monkeypatch.setattr(state, "STATE_DIR", state_dir)
        state.setup_state_dir()
        assert state_dir.exists()

    def test_creates_default_files(self, tmp_state):
        assert (tmp_state / "settings.json").exists()
        assert (tmp_state / "processed_emails.json").exists()
        assert (tmp_state / "pending_transactions.json").exists()

    def test_idempotent(self, tmp_state):
        """Running setup twice should not overwrite existing files."""
        settings_path = tmp_state / "settings.json"
        original = settings_path.read_text()
        state.setup_state_dir()
        assert settings_path.read_text() == original


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_load_returns_defaults(self, tmp_state):
        settings = state.load_settings()
        assert settings["version"] == 1
        assert "gmail" in settings
        assert "allowed_senders" in settings

    def test_save_and_load(self, tmp_state):
        settings = state.load_settings()
        settings["gmail"]["email"] = "test@gmail.com"
        state.save_settings(settings)

        reloaded = state.load_settings()
        assert reloaded["gmail"]["email"] == "test@gmail.com"

    def test_corrupt_settings_returns_defaults(self, tmp_state):
        (tmp_state / "settings.json").write_text("NOT JSON")
        settings = state.load_settings()
        assert settings == state.DEFAULT_SETTINGS


# ---------------------------------------------------------------------------
# Processed emails
# ---------------------------------------------------------------------------

class TestProcessedEmails:
    def test_load_empty(self, tmp_state):
        data = state.load_processed()
        assert data["emails"] == []
        assert data["last_processed_at"] is None

    def test_record_and_check(self, tmp_state):
        email_data = {
            "email_uid": "123",
            "message_id": "<test@gmail.com>",
            "from": "alerts@hdfcbank.net",
            "subject": "Debit Alert",
        }
        state.record_processed_email(email_data, "inserted", [42])

        assert state.is_email_processed("<test@gmail.com>") is True
        assert state.is_email_processed("<other@gmail.com>") is False

    def test_record_updates_last_processed(self, tmp_state):
        email_data = {
            "email_uid": "123",
            "message_id": "<test@gmail.com>",
            "from": "test@test.com",
        }
        state.record_processed_email(email_data, "inserted")
        data = state.load_processed()
        assert data["last_processed_at"] is not None

    def test_multiple_records(self, tmp_state):
        for i in range(5):
            state.record_processed_email(
                {"email_uid": str(i), "message_id": f"<{i}@test.com>"},
                "inserted",
            )
        data = state.load_processed()
        assert len(data["emails"]) == 5


# ---------------------------------------------------------------------------
# Pending transactions
# ---------------------------------------------------------------------------

class TestPending:
    def test_load_empty(self, tmp_state):
        data = state.load_pending()
        assert data["candidates"] == []

    def test_save_and_load(self, tmp_state):
        state.save_pending({
            "version": 1,
            "generated_at": "2026-03-23",
            "candidates": [{"email_uid": "1", "amount": 500}],
            "summary": {"inserted": 1},
        })
        data = state.load_pending()
        assert len(data["candidates"]) == 1

    def test_clear(self, tmp_state):
        state.save_pending({
            "version": 1,
            "generated_at": "2026-03-23",
            "candidates": [{"test": True}],
            "summary": {},
        })
        state.clear_pending()
        data = state.load_pending()
        assert data["candidates"] == []


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

class TestPruning:
    def test_prune_old_records(self, tmp_state):
        # Add a record from 90 days ago
        processed = state.load_processed()
        old_date = (datetime.now() - timedelta(days=90)).isoformat()
        processed["emails"].append({
            "email_uid": "old",
            "message_id": "<old@test.com>",
            "processed_at": old_date,
            "result": "inserted",
        })
        # Add a recent record
        processed["emails"].append({
            "email_uid": "new",
            "message_id": "<new@test.com>",
            "processed_at": datetime.now().isoformat(),
            "result": "inserted",
        })
        state.save_processed(processed)

        pruned = state.prune_old_records(days=60)
        assert pruned == 1

        data = state.load_processed()
        assert len(data["emails"]) == 1
        assert data["emails"][0]["email_uid"] == "new"

    def test_prune_nothing_to_prune(self, tmp_state):
        state.record_processed_email(
            {"email_uid": "1", "message_id": "<1@test.com>"},
            "inserted",
        )
        pruned = state.prune_old_records(days=60)
        assert pruned == 0


# ---------------------------------------------------------------------------
# Reference ID helper
# ---------------------------------------------------------------------------

class TestRecentReferenceIDs:
    def test_get_recent_refs(self, tmp_state):
        email_data = {
            "email_uid": "1",
            "message_id": "<1@test.com>",
            "reference_ids": {"utr": "ABC123", "order_id": "SWG-456"},
        }
        state.record_processed_email(email_data, "inserted")

        refs = state.get_recent_reference_ids(days=7)
        assert "abc123" in refs
        assert "swg-456" in refs

    def test_old_refs_excluded(self, tmp_state):
        processed = state.load_processed()
        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        processed["emails"].append({
            "email_uid": "old",
            "message_id": "<old@test.com>",
            "processed_at": old_date,
            "result": "inserted",
            "reference_ids": {"utr": "OLDREF"},
        })
        state.save_processed(processed)

        refs = state.get_recent_reference_ids(days=7)
        assert "oldref" not in refs
