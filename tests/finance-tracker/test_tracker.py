"""
test_tracker.py — Comprehensive test suite for the finance tracker skill.

Tests cover all three modules (ledger, reports, tracker CLI) to prevent
regressions from future changes. Each test gets a fresh temp DB and config.

Run with:  pytest tests/finance-tracker/ -v
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta

import pytest

import ledger
import reports


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_env(tmp_path):
    """
    Create an isolated config + DB in a temp directory.
    Returns a config dict pointing to the temp DB.
    """
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "finance.db")

    config = {
        "db_path": db_path,
        "currency": "₹",
        "audit": {"enabled": True, "log_select_queries": False},
    }
    with open(config_path, "w") as f:
        json.dump(config, f)

    # Initialise the DB
    result = ledger.init_db(config)
    assert result["status"] == "success"

    return config


@pytest.fixture
def seeded_env(tmp_env):
    """
    tmp_env with some pre-loaded data:
    - Global budget: 50000
    - Category budget for Junk: 3000
    - 3 expenses (Food 300, Junk 500, Transport 100) — all today
    """
    config = tmp_env
    ledger.set_budget(config, 50000, "default", None)
    ledger.set_budget(config, 3000, "default", "Junk")
    ledger.add_expense(config, 300, "Food", "Lunch")
    ledger.add_expense(config, 500, "Junk", "Pizza")
    ledger.add_expense(config, 100, "Transport", "Bus")
    return config


# ===========================================================================
# LEDGER TESTS
# ===========================================================================

class TestInitDB:
    """Database initialisation and safety guards."""

    def test_init_creates_db(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        config = {"db_path": db_path, "currency": "₹",
                  "audit": {"enabled": True, "log_select_queries": False}}
        result = ledger.init_db(config)
        assert result["status"] == "success"
        assert os.path.exists(db_path)

    def test_init_creates_all_tables(self, tmp_env):
        """Verify expenses, budgets, and audit_log tables exist."""
        conn = sqlite3.connect(tmp_env["db_path"])
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "expenses" in tables
        assert "budgets" in tables
        assert "audit_log" in tables

    def test_init_refuses_reinit_without_force(self, tmp_env):
        """Re-running --init without --force should error."""
        result = ledger.init_db(tmp_env)
        assert result["status"] == "error"
        assert "already exists" in result["message"]

    def test_init_with_force_preserves_data(self, seeded_env):
        """--init --force re-runs schema (IF NOT EXISTS) without wiping data."""
        result = ledger.init_db(seeded_env, force=True)
        assert result["status"] == "success"

        # Data should still be there
        with ledger.get_db(seeded_env) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses"
            ).fetchone()[0]
        assert count == 3


class TestAddExpense:
    """Single expense addition."""

    def test_add_returns_success(self, tmp_env):
        result = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        assert result["status"] == "success"
        assert result["data"]["amount"] == 100.0
        assert result["data"]["category"] == "Food"
        assert result["data"]["description"] == "Lunch"

    def test_add_default_date_is_today(self, tmp_env):
        result = ledger.add_expense(tmp_env, 50, "Snacks", "Chips")
        assert result["data"]["transaction_date"] == datetime.now().strftime("%Y-%m-%d")

    def test_add_custom_date(self, tmp_env):
        result = ledger.add_expense(tmp_env, 50, "Food", "Old bill", "2025-06-15")
        assert result["data"]["transaction_date"] == "2025-06-15"

    def test_add_null_category_defaults_uncategorised(self, tmp_env):
        result = ledger.add_expense(tmp_env, 50, None, "Mystery")
        assert result["data"]["category"] == "Uncategorised"

    def test_add_returns_monthly_stats(self, tmp_env):
        """Stats object should reflect the running total."""
        ledger.set_budget(tmp_env, 10000, "default", None)
        ledger.add_expense(tmp_env, 100, "Food", "A")
        result = ledger.add_expense(tmp_env, 200, "Food", "B")
        assert result["stats"]["spent"] == 300.0
        assert result["stats"]["limit"] == 10000.0
        assert result["stats"]["percentage"] == 3.0

    def test_add_is_audit_logged(self, tmp_env):
        ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'INSERT'"
            ).fetchone()
        assert row is not None
        assert row["table_name"] == "expenses"


class TestBulkAdd:
    """Batch expense insertion."""

    def test_bulk_add_inserts_all(self, tmp_env):
        items = [
            {"amount": 50, "category": "Food", "description": "Tea"},
            {"amount": 100, "category": "Transport", "description": "Bus"},
            {"amount": 200, "category": "Junk", "description": "Burger"},
        ]
        result = ledger.bulk_add(tmp_env, items)
        assert result["status"] == "success"
        assert result["count"] == 3
        assert len(result["ids"]) == 3

    def test_bulk_add_atomic_on_failure(self, tmp_env):
        """If one item fails, nothing should be inserted (rollback)."""
        items = [
            {"amount": 50, "category": "Food", "description": "Tea"},
            {"amount": "not_a_number"},  # Will fail on float()
        ]
        with pytest.raises(Exception):
            ledger.bulk_add(tmp_env, items)

        # No rows should have been inserted
        with ledger.get_db(tmp_env) as conn:
            count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        assert count == 0

    def test_bulk_add_defaults_category(self, tmp_env):
        """Items without a category should default to 'Uncategorised'."""
        items = [{"amount": 50, "description": "Mystery item"}]
        result = ledger.bulk_add(tmp_env, items)
        assert result["status"] == "success"

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute("SELECT category FROM expenses").fetchone()
        assert row["category"] == "Uncategorised"

    def test_bulk_add_is_audit_logged(self, tmp_env):
        items = [{"amount": 10, "category": "Food", "description": "A"}]
        ledger.bulk_add(tmp_env, items)
        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'BULK_INSERT'"
            ).fetchone()
        assert row is not None


class TestSoftDelete:
    """Soft-delete marks entries instead of erasing them."""

    def test_soft_delete_sets_deleted_at(self, tmp_env):
        result = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        eid = result["data"]["id"]

        del_result = ledger.soft_delete(tmp_env, eid)
        assert del_result["status"] == "deleted"

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT deleted_at FROM expenses WHERE id = ?", (eid,)
            ).fetchone()
        assert row["deleted_at"] is not None

    def test_soft_delete_row_still_in_db(self, tmp_env):
        """Soft-deleted rows remain in the database."""
        result = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        eid = result["data"]["id"]
        ledger.soft_delete(tmp_env, eid)

        with ledger.get_db(tmp_env) as conn:
            count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        assert count == 1  # Still there

    def test_soft_delete_nonexistent_id(self, tmp_env):
        result = ledger.soft_delete(tmp_env, 9999)
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_soft_delete_already_deleted(self, tmp_env):
        result = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        eid = result["data"]["id"]
        ledger.soft_delete(tmp_env, eid)

        # Deleting again should fail
        result2 = ledger.soft_delete(tmp_env, eid)
        assert result2["status"] == "error"

    def test_soft_delete_is_audit_logged(self, tmp_env):
        result = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        eid = result["data"]["id"]
        ledger.soft_delete(tmp_env, eid)

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'SOFT_DELETE'"
            ).fetchone()
        assert row is not None
        assert row["row_id"] == eid
        # old_values should contain the original row snapshot
        old = json.loads(row["old_values"])
        assert old["amount"] == 100.0


class TestPurge:
    """Permanent removal of soft-deleted entries."""

    def test_purge_removes_deleted_rows(self, tmp_env):
        result = ledger.add_expense(tmp_env, 100, "Food", "A")
        ledger.soft_delete(tmp_env, result["data"]["id"])

        purge = ledger.purge_deleted(tmp_env)
        assert purge["status"] == "success"
        assert purge["purged"] == 1

        with ledger.get_db(tmp_env) as conn:
            count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        assert count == 0

    def test_purge_preserves_active_rows(self, tmp_env):
        r1 = ledger.add_expense(tmp_env, 100, "Food", "Keep")
        r2 = ledger.add_expense(tmp_env, 200, "Food", "Delete")
        ledger.soft_delete(tmp_env, r2["data"]["id"])

        ledger.purge_deleted(tmp_env)

        with ledger.get_db(tmp_env) as conn:
            rows = conn.execute("SELECT * FROM expenses").fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "Keep"

    def test_purge_nothing_to_purge(self, tmp_env):
        result = ledger.purge_deleted(tmp_env)
        assert result["purged"] == 0

    def test_purge_is_audit_logged(self, tmp_env):
        r = ledger.add_expense(tmp_env, 100, "Food", "A")
        ledger.soft_delete(tmp_env, r["data"]["id"])
        ledger.purge_deleted(tmp_env)

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'PURGE'"
            ).fetchone()
        assert row is not None


class TestBudget:
    """Budget setting with the unified budgets table."""

    def test_set_global_overall_budget(self, tmp_env):
        result = ledger.set_budget(tmp_env, 50000, "default", None)
        assert result["status"] == "success"
        assert result["category"] == "overall"
        assert result["budget_limit"] == 50000.0

    def test_set_global_category_budget(self, tmp_env):
        result = ledger.set_budget(tmp_env, 3000, "default", "Junk")
        assert result["status"] == "success"
        assert result["category"] == "Junk"

    def test_set_month_specific_budget(self, tmp_env):
        result = ledger.set_budget(tmp_env, 60000, "2026-03", None)
        assert result["month_key"] == "2026-03"
        assert result["budget_limit"] == 60000.0

    def test_set_month_category_override(self, tmp_env):
        ledger.set_budget(tmp_env, 3000, "default", "Junk")
        ledger.set_budget(tmp_env, 5000, "2026-03", "Junk")

        with ledger.get_db(tmp_env) as conn:
            rows = conn.execute(
                "SELECT * FROM budgets WHERE category = 'Junk' ORDER BY month_key"
            ).fetchall()
        assert len(rows) == 2

    def test_budget_upsert_updates_existing(self, tmp_env):
        """Setting budget twice should update, not duplicate."""
        ledger.set_budget(tmp_env, 50000, "default", None)
        ledger.set_budget(tmp_env, 60000, "default", None)

        with ledger.get_db(tmp_env) as conn:
            rows = conn.execute(
                "SELECT * FROM budgets WHERE month_key = 'default' AND category IS NULL"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["budget_limit"] == 60000.0

    def test_budget_is_audit_logged(self, tmp_env):
        ledger.set_budget(tmp_env, 50000, "default", None)
        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'UPSERT'"
            ).fetchone()
        assert row is not None


class TestUpdateCategory:
    """Category correction on existing expenses."""

    def test_update_category_success(self, tmp_env):
        r = ledger.add_expense(tmp_env, 100, "Food", "Pizza")
        eid = r["data"]["id"]
        result = ledger.update_category(tmp_env, eid, "Junk")
        assert result["status"] == "success"
        assert result["data"]["category"] == "Junk"

    def test_update_category_nonexistent_id(self, tmp_env):
        result = ledger.update_category(tmp_env, 9999, "Junk")
        assert result["status"] == "error"

    def test_update_category_on_deleted_fails(self, tmp_env):
        r = ledger.add_expense(tmp_env, 100, "Food", "Pizza")
        eid = r["data"]["id"]
        ledger.soft_delete(tmp_env, eid)
        result = ledger.update_category(tmp_env, eid, "Junk")
        assert result["status"] == "error"

    def test_update_category_is_audit_logged(self, tmp_env):
        r = ledger.add_expense(tmp_env, 100, "Food", "Pizza")
        eid = r["data"]["id"]
        ledger.update_category(tmp_env, eid, "Junk")

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'UPDATE' "
                "AND table_name = 'expenses'"
            ).fetchone()
        assert row is not None
        old = json.loads(row["old_values"])
        new = json.loads(row["new_values"])
        assert old["category"] == "Food"
        assert new["category"] == "Junk"


class TestQueryWrite:
    """Audited raw SQL writes."""

    def test_query_write_executes_sql(self, seeded_env):
        result = ledger.query_write(
            seeded_env,
            "UPDATE expenses SET description = 'Updated' WHERE category = 'Junk'"
        )
        assert result["status"] == "success"
        assert result["rows_affected"] >= 1

    def test_query_write_is_audit_logged(self, seeded_env):
        ledger.query_write(seeded_env, "UPDATE expenses SET description = 'X' WHERE id = 1")
        with ledger.get_db(seeded_env) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action = 'QUERY_WRITE'"
            ).fetchone()
        assert row is not None
        vals = json.loads(row["new_values"])
        assert "sql" in vals


class TestConcurrency:
    """Thread safety for concurrent writes."""

    def test_concurrent_adds(self, tmp_env):
        """Multiple threads adding expenses should not corrupt the DB."""
        errors = []

        def add_entry():
            try:
                ledger.add_expense(tmp_env, 10.0, "Concurrent", "Test")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=add_entry) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent errors: {errors}"

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT SUM(amount) AS s, COUNT(*) AS c FROM expenses"
            ).fetchone()
        assert row["c"] == 10
        assert row["s"] == 100.0


# ===========================================================================
# REPORTS TESTS
# ===========================================================================

class TestSummarize:
    """Period summaries computed on-the-fly."""

    def test_monthly_empty_db(self, tmp_env):
        result = reports.summarize(tmp_env, "monthly")
        assert result["monthly_spent"] == 0
        assert result["period_spent"] == 0

    def test_monthly_with_budget(self, seeded_env):
        result = reports.summarize(seeded_env, "monthly")
        assert result["monthly_spent"] == 900.0  # 300 + 500 + 100
        assert result["budget"] == 50000.0
        assert result["savings"] == 49100.0

    def test_monthly_category_breakdown(self, seeded_env):
        result = reports.summarize(seeded_env, "monthly")
        cats = result["categories"]
        assert "Food" in cats
        assert "Junk" in cats
        assert "Transport" in cats
        assert cats["Food"]["spent"] == 300.0
        assert cats["Junk"]["spent"] == 500.0
        assert cats["Junk"]["budget"] == 3000.0
        assert cats["Junk"]["overspent"] is False

    def test_monthly_overspend_detection(self, tmp_env):
        ledger.set_budget(tmp_env, 100, "default", "Junk")
        ledger.add_expense(tmp_env, 150, "Junk", "Pizza")
        result = reports.summarize(tmp_env, "monthly")
        assert result["categories"]["Junk"]["overspent"] is True
        assert result["categories"]["Junk"]["remaining"] == -50.0

    def test_daily_only_today(self, seeded_env):
        # Add an expense for yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        ledger.add_expense(seeded_env, 200, "Food", "Yesterday", yesterday)

        result = reports.summarize(seeded_env, "daily")
        # Daily should only include today's expenses (900)
        assert result["period_spent"] == 900.0
        # Monthly includes everything
        assert result["monthly_spent"] == 1100.0

    def test_weekly_summary(self, tmp_env):
        # Add expense for today (always within current week)
        ledger.add_expense(tmp_env, 100, "Food", "Today")
        result = reports.summarize(tmp_env, "weekly")
        assert result["period_spent"] == 100.0

    def test_past_month_summary(self, tmp_env):
        ledger.add_expense(tmp_env, 1000, "Food", "Old", "2025-01-15")
        result = reports.summarize(tmp_env, "monthly", "2025-01")
        assert result["month"] == "2025-01"
        assert result["monthly_spent"] == 1000.0

    def test_soft_deleted_excluded_from_summary(self, seeded_env):
        """Soft-deleted entries must not appear in summaries."""
        # Delete the Junk entry (500)
        with ledger.get_db(seeded_env) as conn:
            junk_id = conn.execute(
                "SELECT id FROM expenses WHERE category = 'Junk'"
            ).fetchone()["id"]
        ledger.soft_delete(seeded_env, junk_id)

        result = reports.summarize(seeded_env, "monthly")
        assert result["monthly_spent"] == 400.0  # 300 + 100 (Pizza excluded)
        assert "Junk" not in result["categories"]


class TestBudgetResolution:
    """Budget fallback chain: month-specific → global default → None."""

    def test_no_budget_returns_none(self, tmp_env):
        result = reports.summarize(tmp_env, "monthly")
        assert result["budget"] is None

    def test_global_default_used_as_fallback(self, tmp_env):
        ledger.set_budget(tmp_env, 50000, "default", None)
        result = reports.summarize(tmp_env, "monthly")
        assert result["budget"] == 50000.0

    def test_month_specific_overrides_default(self, tmp_env):
        month = datetime.now().strftime("%Y-%m")
        ledger.set_budget(tmp_env, 50000, "default", None)
        ledger.set_budget(tmp_env, 70000, month, None)
        result = reports.summarize(tmp_env, "monthly")
        assert result["budget"] == 70000.0

    def test_category_budget_fallback(self, tmp_env):
        ledger.set_budget(tmp_env, 3000, "default", "Junk")
        ledger.add_expense(tmp_env, 100, "Junk", "Pizza")
        result = reports.summarize(tmp_env, "monthly")
        assert result["categories"]["Junk"]["budget"] == 3000.0

    def test_category_month_override(self, tmp_env):
        month = datetime.now().strftime("%Y-%m")
        ledger.set_budget(tmp_env, 3000, "default", "Junk")
        ledger.set_budget(tmp_env, 5000, month, "Junk")
        ledger.add_expense(tmp_env, 100, "Junk", "Pizza")
        result = reports.summarize(tmp_env, "monthly")
        assert result["categories"]["Junk"]["budget"] == 5000.0

    def test_no_category_budget_means_unlimited(self, tmp_env):
        """Categories without a budget row should have no budget/remaining keys."""
        ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        result = reports.summarize(tmp_env, "monthly")
        # Food has no budget set, so no 'budget' key in the dict
        assert "budget" not in result["categories"]["Food"]
        assert "overspent" not in result["categories"]["Food"]


class TestCategoryHelpers:
    """Smart categorisation support."""

    def test_list_categories(self, seeded_env):
        cats = reports.list_categories(seeded_env)
        assert sorted(cats) == ["Food", "Junk", "Transport"]

    def test_list_categories_excludes_deleted(self, seeded_env):
        with ledger.get_db(seeded_env) as conn:
            food_id = conn.execute(
                "SELECT id FROM expenses WHERE category = 'Food'"
            ).fetchone()["id"]
        ledger.soft_delete(seeded_env, food_id)

        cats = reports.list_categories(seeded_env)
        assert "Food" not in cats

    def test_suggest_category_found(self, seeded_env):
        result = reports.suggest_category(seeded_env, "Pizza")
        assert result["suggested"] == "Junk"
        assert result["confidence"] >= 1

    def test_suggest_category_not_found(self, seeded_env):
        result = reports.suggest_category(seeded_env, "Spaceship")
        assert result["suggested"] is None
        assert result["confidence"] == 0

    def test_suggest_category_case_insensitive(self, seeded_env):
        result = reports.suggest_category(seeded_env, "pizza")
        assert result["suggested"] == "Junk"

    def test_suggest_most_frequent(self, tmp_env):
        """When a description matches multiple categories, return the most common."""
        for _ in range(3):
            ledger.add_expense(tmp_env, 50, "Junk", "Burger")
        ledger.add_expense(tmp_env, 50, "Food", "Burger")

        result = reports.suggest_category(tmp_env, "Burger")
        assert result["suggested"] == "Junk"
        assert result["confidence"] == 3


class TestQueryRead:
    """SELECT-only query guard."""

    def test_select_allowed(self, seeded_env):
        result = reports.query_read(seeded_env, "SELECT COUNT(*) AS c FROM expenses")
        assert isinstance(result, list)
        assert result[0]["c"] == 3

    def test_drop_rejected(self, seeded_env):
        result = reports.query_read(seeded_env, "DROP TABLE expenses")
        assert result["status"] == "error"
        assert "SELECT" in result["message"]

    def test_delete_rejected(self, seeded_env):
        result = reports.query_read(seeded_env, "DELETE FROM expenses")
        assert result["status"] == "error"

    def test_insert_rejected(self, seeded_env):
        result = reports.query_read(
            seeded_env, "INSERT INTO expenses (amount) VALUES (999)"
        )
        assert result["status"] == "error"

    def test_update_rejected(self, seeded_env):
        result = reports.query_read(
            seeded_env, "UPDATE expenses SET amount = 0"
        )
        assert result["status"] == "error"

    def test_with_clause_allowed(self, seeded_env):
        sql = "WITH t AS (SELECT * FROM expenses) SELECT COUNT(*) AS c FROM t"
        result = reports.query_read(seeded_env, sql)
        assert isinstance(result, list)

    def test_explain_allowed(self, seeded_env):
        result = reports.query_read(
            seeded_env, "EXPLAIN QUERY PLAN SELECT * FROM expenses"
        )
        assert isinstance(result, list)


# ===========================================================================
# AUDIT LOG TESTS
# ===========================================================================

class TestAuditLog:
    """Verify audit trail integrity across operations."""

    def test_all_operations_logged(self, tmp_env):
        """Run through a full lifecycle and verify every step is audit-logged."""
        # 1. Set budget
        ledger.set_budget(tmp_env, 50000, "default", None)
        # 2. Add expense
        r = ledger.add_expense(tmp_env, 100, "Food", "Lunch")
        eid = r["data"]["id"]
        # 3. Update category
        ledger.update_category(tmp_env, eid, "Snacks")
        # 4. Soft delete
        ledger.soft_delete(tmp_env, eid)
        # 5. Purge
        ledger.purge_deleted(tmp_env)

        with ledger.get_db(tmp_env) as conn:
            actions = [
                row["action"] for row in
                conn.execute(
                    "SELECT action FROM audit_log ORDER BY id"
                ).fetchall()
            ]

        assert "UPSERT" in actions        # set_budget
        assert "INSERT" in actions         # add_expense
        assert "UPDATE" in actions         # update_category
        assert "SOFT_DELETE" in actions    # soft_delete
        assert "PURGE" in actions          # purge_deleted

    def test_audit_old_values_snapshot(self, tmp_env):
        """SOFT_DELETE should capture the full row as old_values."""
        r = ledger.add_expense(tmp_env, 250, "Junk", "Burger")
        eid = r["data"]["id"]
        ledger.soft_delete(tmp_env, eid)

        with ledger.get_db(tmp_env) as conn:
            row = conn.execute(
                "SELECT old_values FROM audit_log WHERE action = 'SOFT_DELETE'"
            ).fetchone()
        old = json.loads(row["old_values"])
        assert old["amount"] == 250.0
        assert old["category"] == "Junk"
        assert old["description"] == "Burger"


# ===========================================================================
# CLI INTEGRATION TESTS (subprocess)
# ===========================================================================

class TestCLI:
    """End-to-end tests calling tracker.py as a subprocess."""

    TRACKER_DIR = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../skills/finance-tracker")
    )

    def _run(self, args, config_path=None, env_config=None):
        """Run tracker.py as subprocess and return parsed JSON."""
        env = os.environ.copy()
        if env_config:
            # Override config by setting it before run
            pass
        result = subprocess.run(
            [sys.executable, "tracker.py"] + args,
            capture_output=True, text=True, cwd=self.TRACKER_DIR,
        )
        try:
            return json.loads(result.stdout.strip().split("\n")[-1])
        except (json.JSONDecodeError, IndexError):
            return {"raw_stdout": result.stdout, "raw_stderr": result.stderr}

    def test_cli_init(self, tmp_path, monkeypatch):
        """CLI --init should create the database."""
        config_path = str(tmp_path / "config.json")
        db_path = str(tmp_path / "finance.db")
        config = {"db_path": db_path, "currency": "₹",
                  "audit": {"enabled": True, "log_select_queries": False}}
        with open(config_path, "w") as f:
            json.dump(config, f)

        # Monkeypatch the config path
        monkeypatch.setattr(ledger, "DEFAULT_CONFIG_PATH", config_path)

        result = ledger.init_db(config)
        assert result["status"] == "success"

    def test_cli_help_doesnt_crash(self):
        """--help should exit cleanly."""
        result = subprocess.run(
            [sys.executable, "tracker.py", "--help"],
            capture_output=True, text=True, cwd=self.TRACKER_DIR,
        )
        assert result.returncode == 0


# ===========================================================================
# CONFIG TESTS
# ===========================================================================

class TestConfig:
    """Config loading and defaults."""

    def test_load_creates_default_config(self, tmp_path):
        config_path = str(tmp_path / "subdir" / "config.json")
        config = ledger.load_config(config_path)
        assert os.path.exists(config_path)
        assert "db_path" in config
        assert config["currency"] == "₹"

    def test_load_existing_config(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        custom = {"db_path": "/custom/path.db", "currency": "$",
                  "audit": {"enabled": False}}
        with open(config_path, "w") as f:
            json.dump(custom, f)
        loaded = ledger.load_config(config_path)
        assert loaded["currency"] == "$"
        assert loaded["db_path"] == "/custom/path.db"

    def test_db_path_tilde_expansion(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        custom = {"db_path": "~/data/test.db", "currency": "₹", "audit": {}}
        with open(config_path, "w") as f:
            json.dump(custom, f)
        loaded = ledger.load_config(config_path)
        assert "~" not in loaded["db_path"]
        assert loaded["db_path"].startswith("/")
