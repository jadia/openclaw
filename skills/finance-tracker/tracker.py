#!/usr/bin/env python3
"""
tracker.py — CLI entry point for the finance tracker skill.

This is a thin dispatcher that parses arguments and delegates to:
  - ledger.py  for all data mutations (writes, deletes, budget changes)
  - reports.py for all read-only operations (summaries, queries, exports)

All output is JSON for easy parsing by agents like OpenClaw.

Usage:
  python3 tracker.py --init               # First-time DB setup
  python3 tracker.py --add 500 Junk Pizza  # Add expense
  python3 tracker.py --summarize monthly   # Monthly report
  python3 tracker.py --help                # Full help
"""

import argparse
import json
import sys
import os

# Ensure the skill directory is on the import path so ledger/reports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger
import reports


def _output(data):
    """Print JSON to stdout. All CLI output goes through here."""
    print(json.dumps(data, default=str))


def main():
    parser = argparse.ArgumentParser(
        description="Personal finance tracker with audit logging."
    )

    # --- Ledger (write) commands ---
    parser.add_argument(
        "--init", action="store_true",
        help="Initialise the database (first-time setup).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-initialisation if DB exists (use with --init).",
    )
    parser.add_argument(
        "--add", nargs="+", metavar=("AMT", "ARGS"),
        help="Add expense: --add <amount> <category> <description> [YYYY-MM-DD]",
    )
    parser.add_argument(
        "--bulk-add", type=str, metavar="JSON",
        help='Batch add: --bulk-add \'[{"amount":50,"category":"Food","description":"Tea"}]\'',
    )
    parser.add_argument(
        "--remove", type=int, metavar="ID",
        help="Soft-delete an expense by ID.",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Permanently remove all soft-deleted expenses.",
    )
    parser.add_argument(
        "--set-budget", nargs="+", metavar=("LIMIT", "ARGS"),
        help="Set budget: --set-budget <limit> [YYYY-MM|default] [category]",
    )
    parser.add_argument(
        "--update-category", nargs=2, metavar=("ID", "CATEGORY"),
        help="Change category of an expense: --update-category <id> <category>",
    )
    parser.add_argument(
        "--query-write", type=str, metavar="SQL",
        help="Execute mutating SQL (audited). Agent must confirm with user first.",
    )

    # --- Reports (read) commands ---
    parser.add_argument(
        "--summarize", choices=["daily", "weekly", "monthly"],
        help="Spend summary for a period.",
    )
    parser.add_argument(
        "--month", type=str, metavar="YYYY-MM",
        help="Target month for --summarize or --set-budget.",
    )
    parser.add_argument(
        "--query", type=str, metavar="SQL",
        help="Run a read-only SQL query (SELECT only).",
    )
    parser.add_argument(
        "--categories", action="store_true",
        help="List all distinct expense categories.",
    )
    parser.add_argument(
        "--suggest-category", type=str, metavar="DESC",
        help="Suggest a category based on past entries matching description.",
    )
    parser.add_argument(
        "--export", action="store_true",
        help="Export expenses and budgets to CSV.",
    )

    args = parser.parse_args()
    config = ledger.load_config()

    try:
        # --init doesn't require an existing DB
        if args.init:
            _output(ledger.init_db(config, force=args.force))
            return

        # Everything else needs the DB to exist
        if not os.path.exists(config["db_path"]):
            _output({
                "status": "error",
                "message": (
                    f"Database not found at '{config['db_path']}'. "
                    "Run: python3 tracker.py --init"
                ),
            })
            sys.exit(1)

        # --- Dispatch: ledger (writes) ---
        if args.add:
            amount = float(args.add[0])
            category = args.add[1] if len(args.add) > 1 else "Uncategorised"
            description = args.add[2] if len(args.add) > 2 else ""
            date = args.add[3] if len(args.add) > 3 else None
            _output(ledger.add_expense(config, amount, category, description, date))

        elif args.bulk_add:
            expenses_list = json.loads(args.bulk_add)
            _output(ledger.bulk_add(config, expenses_list))

        elif args.remove is not None:
            _output(ledger.soft_delete(config, args.remove))

        elif args.purge:
            _output(ledger.purge_deleted(config))

        elif args.set_budget:
            limit = float(args.set_budget[0])
            month_key = args.set_budget[1] if len(args.set_budget) > 1 else "default"
            category = args.set_budget[2] if len(args.set_budget) > 2 else None
            _output(ledger.set_budget(config, limit, month_key, category))

        elif args.update_category:
            _output(ledger.update_category(
                config, int(args.update_category[0]), args.update_category[1]
            ))

        elif args.query_write:
            _output(ledger.query_write(config, args.query_write))

        # --- Dispatch: reports (reads) ---
        elif args.summarize:
            _output(reports.summarize(config, args.summarize, args.month))

        elif args.query:
            _output(reports.query_read(config, args.query))

        elif args.categories:
            _output(reports.list_categories(config))

        elif args.suggest_category:
            _output(reports.suggest_category(config, args.suggest_category))

        elif args.export:
            _output(reports.export_csv(config))

        else:
            parser.print_help()

    except Exception as e:
        _output({"status": "error", "message": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
