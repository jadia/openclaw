"""
ledger.py — Data layer for the finance tracker.

Owns all database mutations (INSERT, UPDATE, DELETE). Every write is
audit-logged so changes are traceable. The reporting layer (reports.py)
handles read-only operations.
"""

import sqlite3
import json
import os
import contextlib
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_DIR = os.path.expanduser("~/data/finance-tracker")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "db_path": os.path.join(DEFAULT_CONFIG_DIR, "finance.db"),
    "currency": "₹",
    "audit": {
        "enabled": True,
        "log_select_queries": False,
    },
}


def load_config(config_path=None):
    """Load config from disk. Creates default config + directory on first run."""
    path = config_path or DEFAULT_CONFIG_PATH
    directory = os.path.dirname(path)

    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG.copy()

    with open(path, "r") as f:
        config = json.load(f)

    # Expand ~ in db_path so downstream code gets an absolute path
    if "db_path" in config:
        config["db_path"] = os.path.expanduser(config["db_path"])
    return config


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def get_db(config):
    """Context manager for a DB connection. Uses the path from config."""
    db_path = config["db_path"]
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Database not found at '{db_path}'. "
            "Run with --init to create it first."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _audit(conn, action, table_name, row_id=None,
           old_values=None, new_values=None, source="cli"):
    """Write a row to audit_log. Silently skips if auditing is off."""
    conn.execute(
        "INSERT INTO audit_log "
        "(action, table_name, row_id, old_values, new_values, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            action,
            table_name,
            row_id,
            json.dumps(old_values) if old_values else None,
            json.dumps(new_values) if new_values else None,
            source,
        ),
    )


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS expenses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    amount           REAL    NOT NULL,
    category         TEXT    DEFAULT 'Uncategorised',
    description      TEXT,
    transaction_date TEXT    NOT NULL,
    deleted_at       TEXT    DEFAULT NULL,
    inserted_on      TEXT    DEFAULT (datetime('now','localtime')),
    updated_on       TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    month_key    TEXT    NOT NULL,
    category     TEXT    DEFAULT NULL,
    budget_limit REAL    NOT NULL,
    inserted_on  TEXT    DEFAULT (datetime('now','localtime')),
    updated_on   TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE(month_key, category)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    row_id      INTEGER,
    old_values  TEXT,
    new_values  TEXT,
    source      TEXT DEFAULT 'cli',
    created_on  TEXT DEFAULT (datetime('now','localtime'))
);

-- Auto-update updated_on timestamp on row changes
CREATE TRIGGER IF NOT EXISTS update_expenses_timestamp
    AFTER UPDATE ON expenses BEGIN
    UPDATE expenses SET updated_on = datetime('now','localtime')
     WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS update_budgets_timestamp
    AFTER UPDATE ON budgets BEGIN
    UPDATE budgets SET updated_on = datetime('now','localtime')
     WHERE id = old.id;
END;
"""


def init_db(config, force=False):
    """
    Create the database file and tables.

    Refuses to overwrite an existing DB unless *force* is True.
    Returns a status dict for CLI output.
    """
    db_path = config["db_path"]
    db_dir = os.path.dirname(db_path)

    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    if os.path.exists(db_path) and not force:
        return {
            "status": "error",
            "message": (
                f"Database already exists at '{db_path}'. "
                "Use --force to re-initialise (tables use IF NOT EXISTS, "
                "so existing data is preserved)."
            ),
        }

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "message": f"Database initialised at '{db_path}'."}


# ---------------------------------------------------------------------------
# Expense operations
# ---------------------------------------------------------------------------

def _monthly_stats(conn, date_str, config):
    """
    Calculate monthly spend vs budget for the month containing *date_str*.
    Uses the budget resolution chain: month-specific → global default.
    """
    month_key = date_str[:7]  # YYYY-MM

    row = conn.execute(
        "SELECT SUM(amount) AS total FROM expenses "
        "WHERE transaction_date LIKE ? AND deleted_at IS NULL",
        (f"{month_key}%",),
    ).fetchone()
    spent = row["total"] if row and row["total"] else 0.0

    # Budget resolution: month-specific overall → global default overall
    budget_row = conn.execute(
        "SELECT budget_limit FROM budgets "
        "WHERE month_key = ? AND category IS NULL",
        (month_key,),
    ).fetchone()
    if not budget_row:
        budget_row = conn.execute(
            "SELECT budget_limit FROM budgets "
            "WHERE month_key = 'default' AND category IS NULL",
        ).fetchone()

    limit = budget_row["budget_limit"] if budget_row else 0.0
    percentage = (spent / limit) * 100 if limit > 0 else 0

    return {"spent": spent, "limit": limit, "percentage": round(percentage, 2)}


def add_expense(config, amount, category, description, date=None):
    """Insert a single expense, audit-log it, and return the row + stats."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO expenses "
                "(amount, category, description, transaction_date) "
                "VALUES (?, ?, ?, ?)",
                (amount, category or "Uncategorised", description, date),
            )
            new_id = cur.lastrowid
            row = dict(
                cur.execute(
                    "SELECT * FROM expenses WHERE id = ?", (new_id,)
                ).fetchone()
            )

            _audit(conn, "INSERT", "expenses", new_id, new_values=row)
            stats = _monthly_stats(conn, date, config)
            conn.commit()
            return {"status": "success", "data": row, "stats": stats}
        except Exception:
            conn.rollback()
            raise


def bulk_add(config, expenses_list):
    """
    Insert multiple expenses in a single transaction.

    Each item in *expenses_list* should be a dict with keys:
    amount, category (optional), description (optional), date (optional).
    """
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.cursor()
            inserted_ids = []
            for exp in expenses_list:
                amt = float(exp["amount"])
                cat = exp.get("category", "Uncategorised")
                desc = exp.get("description", "")
                d = exp.get("date", today)

                cur.execute(
                    "INSERT INTO expenses "
                    "(amount, category, description, transaction_date) "
                    "VALUES (?, ?, ?, ?)",
                    (amt, cat, desc, d),
                )
                inserted_ids.append(cur.lastrowid)

            # Audit a single summary entry for the bulk insert
            _audit(
                conn, "BULK_INSERT", "expenses",
                new_values={"count": len(inserted_ids), "ids": inserted_ids},
            )

            stats = _monthly_stats(conn, today, config)
            conn.commit()

            return {
                "status": "success",
                "count": len(inserted_ids),
                "ids": inserted_ids,
                "stats": stats,
            }
        except Exception:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Soft-delete / purge
# ---------------------------------------------------------------------------

def soft_delete(config, expense_id):
    """Mark an expense as deleted (sets deleted_at). Does not remove the row."""
    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM expenses WHERE id = ? AND deleted_at IS NULL",
                (expense_id,),
            ).fetchone()
            if not row:
                return {
                    "status": "error",
                    "message": f"Expense {expense_id} not found or already deleted.",
                }

            old = dict(row)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE expenses SET deleted_at = ? WHERE id = ?",
                (now, expense_id),
            )
            _audit(conn, "SOFT_DELETE", "expenses", expense_id,
                   old_values=old)
            conn.commit()
            return {"status": "deleted", "id": expense_id}
        except Exception:
            conn.rollback()
            raise


def purge_deleted(config):
    """Permanently remove all soft-deleted rows. Logs the count in audit."""
    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE deleted_at IS NOT NULL"
            ).fetchall()
            count = len(rows)

            if count == 0:
                return {"status": "success", "purged": 0,
                        "message": "No deleted entries to purge."}

            ids = [r["id"] for r in rows]
            conn.execute(
                "DELETE FROM expenses WHERE deleted_at IS NOT NULL"
            )
            _audit(
                conn, "PURGE", "expenses",
                old_values={"count": count, "ids": ids},
            )
            conn.commit()
            return {"status": "success", "purged": count}
        except Exception:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Budget management
# ---------------------------------------------------------------------------

def set_budget(config, limit, month_key="default", category=None):
    """
    Set or update a budget limit.

    - month_key='default', category=None  → global overall budget
    - month_key='default', category='Junk' → global default for Junk
    - month_key='2026-03', category=None   → March overall budget
    - month_key='2026-03', category='Junk' → March Junk override
    """
    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Manual upsert: SQLite UNIQUE doesn't treat NULL=NULL as a
            # conflict, so ON CONFLICT fails for overall budgets (category=NULL).
            if category is None:
                old_row = conn.execute(
                    "SELECT * FROM budgets "
                    "WHERE month_key = ? AND category IS NULL",
                    (month_key,),
                ).fetchone()
            else:
                old_row = conn.execute(
                    "SELECT * FROM budgets "
                    "WHERE month_key = ? AND category = ?",
                    (month_key, category),
                ).fetchone()
            old = dict(old_row) if old_row else None

            if old_row:
                conn.execute(
                    "UPDATE budgets SET budget_limit = ? WHERE id = ?",
                    (float(limit), old_row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO budgets (month_key, category, budget_limit) "
                    "VALUES (?, ?, ?)",
                    (month_key, category, float(limit)),
                )

            if category is None:
                new_row = conn.execute(
                    "SELECT * FROM budgets "
                    "WHERE month_key = ? AND category IS NULL",
                    (month_key,),
                ).fetchone()
            else:
                new_row = conn.execute(
                    "SELECT * FROM budgets "
                    "WHERE month_key = ? AND category = ?",
                    (month_key, category),
                ).fetchone()

            _audit(
                conn, "UPSERT", "budgets", new_row["id"],
                old_values=old,
                new_values=dict(new_row),
            )
            conn.commit()

            return {
                "status": "success",
                "month_key": month_key,
                "category": category or "overall",
                "budget_limit": float(limit),
            }
        except Exception:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Category update
# ---------------------------------------------------------------------------

def update_category(config, expense_id, new_category):
    """Change the category of an existing expense. Audit-logged."""
    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM expenses WHERE id = ? AND deleted_at IS NULL",
                (expense_id,),
            ).fetchone()
            if not row:
                return {
                    "status": "error",
                    "message": f"Expense {expense_id} not found or deleted.",
                }

            old = dict(row)
            conn.execute(
                "UPDATE expenses SET category = ? WHERE id = ?",
                (new_category, expense_id),
            )
            updated = dict(
                conn.execute(
                    "SELECT * FROM expenses WHERE id = ?", (expense_id,)
                ).fetchone()
            )

            _audit(conn, "UPDATE", "expenses", expense_id,
                   old_values=old, new_values=updated)
            conn.commit()
            return {"status": "success", "data": updated}
        except Exception:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# Raw SQL (audited write)
# ---------------------------------------------------------------------------

def query_write(config, sql):
    """
    Execute arbitrary mutating SQL. Fully audit-logged.

    This is the escape hatch — use with caution. SKILL.md instructs
    the agent to show the SQL to the user and wait for confirmation
    before calling this.
    """
    with get_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.cursor()
            cur.execute(sql)
            affected = cur.rowcount

            _audit(
                conn, "QUERY_WRITE", "raw_sql",
                new_values={"sql": sql, "rows_affected": affected},
            )
            conn.commit()
            return {
                "status": "success",
                "rows_affected": affected,
                "sql": sql,
            }
        except Exception:
            conn.rollback()
            raise
