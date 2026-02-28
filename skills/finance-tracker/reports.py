"""
reports.py — Reporting layer for the finance tracker.

All operations are read-only. Summaries and breakdowns are computed
on-the-fly from the expenses table (no stored analytics).
Soft-deleted rows (deleted_at IS NOT NULL) are excluded from all reports.
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta

from ledger import get_db, load_config


# ---------------------------------------------------------------------------
# Budget resolution
# ---------------------------------------------------------------------------

def _resolve_budget(conn, month_key, category=None):
    """
    Resolve budget limit using the fallback chain:
      1. (month_key, category)     — month-specific override
      2. ('default', category)     — global default for category
      3. None                      — no limit (unlimited)

    When category is None, resolves the *overall* monthly budget.
    """
    # Try month-specific first
    row = conn.execute(
        "SELECT budget_limit FROM budgets "
        "WHERE month_key = ? AND "
        "(category = ? OR (category IS NULL AND ? IS NULL))",
        (month_key, category, category),
    ).fetchone()
    if row:
        return row["budget_limit"]

    # Fallback to global default
    row = conn.execute(
        "SELECT budget_limit FROM budgets "
        "WHERE month_key = 'default' AND "
        "(category = ? OR (category IS NULL AND ? IS NULL))",
        (category, category),
    ).fetchone()
    if row:
        return row["budget_limit"]

    return None  # No budget set → unlimited


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def summarize(config, period, month_key=None):
    """
    Compute spend summary for *period* (daily | weekly | monthly).

    Always includes monthly context: overall budget, spent, remaining,
    and percentage used. Daily/weekly add their period-specific spend.
    """
    now = datetime.now()

    # Determine which month to report against
    if month_key and len(month_key) == 7:
        year_month = month_key
    elif month_key and len(month_key) == 10:
        year_month = month_key[:7]
    else:
        year_month = now.strftime("%Y-%m")

    with get_db(config) as conn:
        # --- Monthly context (always computed) ---
        monthly_row = conn.execute(
            "SELECT SUM(amount) AS total FROM expenses "
            "WHERE transaction_date LIKE ? AND deleted_at IS NULL",
            (f"{year_month}%",),
        ).fetchone()
        monthly_spent = monthly_row["total"] if monthly_row and monthly_row["total"] else 0.0

        budget = _resolve_budget(conn, year_month, category=None)
        savings = (budget - monthly_spent) if budget else None
        percentage = round((monthly_spent / budget) * 100, 2) if budget and budget > 0 else 0

        # --- Period-specific spend ---
        period_spent = monthly_spent  # default for 'monthly'

        if period == "daily":
            if month_key and len(month_key) == 10:
                target_date = month_key
            else:
                target_date = now.strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT SUM(amount) AS total FROM expenses "
                "WHERE transaction_date = ? AND deleted_at IS NULL",
                (target_date,),
            ).fetchone()
            period_spent = row["total"] if row and row["total"] else 0.0

        elif period == "weekly":
            start_of_week = (now.date() - timedelta(days=now.weekday()))
            end_of_week = start_of_week + timedelta(days=6)
            row = conn.execute(
                "SELECT SUM(amount) AS total FROM expenses "
                "WHERE transaction_date BETWEEN ? AND ? "
                "AND deleted_at IS NULL",
                (str(start_of_week), str(end_of_week)),
            ).fetchone()
            period_spent = row["total"] if row and row["total"] else 0.0

        # --- Category breakdown for the month ---
        cat_rows = conn.execute(
            "SELECT category, SUM(amount) AS total FROM expenses "
            "WHERE transaction_date LIKE ? AND deleted_at IS NULL "
            "GROUP BY category ORDER BY total DESC",
            (f"{year_month}%",),
        ).fetchall()

        categories = {}
        for cr in cat_rows:
            cat_budget = _resolve_budget(conn, year_month, cr["category"])
            entry = {"spent": cr["total"]}
            if cat_budget is not None:
                entry["budget"] = cat_budget
                entry["remaining"] = cat_budget - cr["total"]
                entry["overspent"] = cr["total"] > cat_budget
            categories[cr["category"]] = entry

        return {
            "period": period,
            "period_spent": period_spent,
            "month": year_month,
            "monthly_spent": monthly_spent,
            "budget": budget,
            "savings": savings,
            "percentage": percentage,
            "categories": categories,
        }


# ---------------------------------------------------------------------------
# Category helpers (smart categorisation support)
# ---------------------------------------------------------------------------

def list_categories(config):
    """Return all distinct categories from active (non-deleted) expenses."""
    with get_db(config) as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM expenses "
            "WHERE deleted_at IS NULL ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]


def suggest_category(config, description):
    """
    Suggest a category based on past entries with matching descriptions.

    Looks for expenses where the description contains the search term
    (case-insensitive), groups by category, and returns the most
    frequent match. Confidence is the match count.
    """
    with get_db(config) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM expenses "
            "WHERE LOWER(description) LIKE ? AND deleted_at IS NULL "
            "GROUP BY category ORDER BY cnt DESC",
            (f"%{description.lower()}%",),
        ).fetchall()

        if not rows:
            return {"suggested": None, "confidence": 0, "total_matches": 0}

        total = sum(r["cnt"] for r in rows)
        return {
            "suggested": rows[0]["category"],
            "confidence": rows[0]["cnt"],
            "total_matches": total,
            "all": {r["category"]: r["cnt"] for r in rows},
        }


# ---------------------------------------------------------------------------
# Read-only SQL queries
# ---------------------------------------------------------------------------

def query_read(config, sql):
    """
    Execute a SELECT-only query. Rejects any mutating SQL.

    Returns the result rows as a list of dicts.
    """
    stripped = sql.strip().upper()
    # Only allow statements that start with SELECT, WITH, or EXPLAIN
    if not (stripped.startswith("SELECT")
            or stripped.startswith("WITH")
            or stripped.startswith("EXPLAIN")):
        return {
            "status": "error",
            "message": (
                "Only SELECT / WITH / EXPLAIN queries allowed. "
                "Use --query-write for mutations."
            ),
        }

    with get_db(config) as conn:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Export (low priority)
# ---------------------------------------------------------------------------

def export_csv(config, output_dir=None):
    """Export expenses and budgets tables to CSV files."""
    import csv

    if not output_dir:
        output_dir = os.path.dirname(config["db_path"])

    with get_db(config) as conn:
        exported = []
        for table in ["expenses", "budgets"]:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                path = os.path.join(output_dir, f"{table}.csv")
                with open(path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows([dict(r) for r in rows])
                exported.append(path)

    return {"status": "exported", "files": exported}
