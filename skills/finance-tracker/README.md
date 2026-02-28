# Finance Tracker Skill

A personal finance tracker designed for AI agent interaction (OpenClaw). All transactions are in Indian Rupees (₹).

## Features

- **Expense tracking** — single and bulk entry with backdating support
- **Smart categorisation** — auto-suggests categories from past entries
- **Category budgets** — per-category and overall monthly budget limits with overspend alerts
- **Soft-delete** — recoverable deletions with audit trail
- **Full audit logging** — every mutation is logged with before/after snapshots
- **SQL access** — read-only queries freely, write queries with explicit auditing
- **On-the-fly summaries** — daily, weekly, monthly with category breakdowns

## Architecture

```
tracker.py   → Thin CLI entry point (argument parsing, JSON output)
ledger.py    → Data layer (all writes: add, delete, budget, audit log)
reports.py   → Reporting layer (all reads: summaries, queries, category helpers)
```

## Setup

**1. Initialise the database** (first-time only):
```bash
python3 tracker.py --init
```

This creates `~/data/finance-tracker/finance.db` and `~/data/finance-tracker/config.json` with defaults.

**2. Set your monthly budget**:
```bash
python3 tracker.py --set-budget 50000 default
```

**3. (Optional) Set category budgets**:
```bash
python3 tracker.py --set-budget 3000 default Junk
python3 tracker.py --set-budget 10000 default Food
```

## Quick Reference

| Action | Command |
|:---|:---|
| Add expense | `python3 tracker.py --add 500 Junk Pizza` |
| Add with date | `python3 tracker.py --add 500 Junk Pizza 2026-02-20` |
| Bulk add | `python3 tracker.py --bulk-add '[{"amount":50,"category":"Food","description":"Tea"}]'` |
| Remove (soft) | `python3 tracker.py --remove 5` |
| Set budget | `python3 tracker.py --set-budget 50000 2026-03` |
| Category budget | `python3 tracker.py --set-budget 3000 default Junk` |
| Fix category | `python3 tracker.py --update-category 5 Food` |
| Suggest category | `python3 tracker.py --suggest-category "Pizza"` |
| List categories | `python3 tracker.py --categories` |
| Daily summary | `python3 tracker.py --summarize daily` |
| Weekly summary | `python3 tracker.py --summarize weekly` |
| Monthly summary | `python3 tracker.py --summarize monthly` |
| Past month | `python3 tracker.py --summarize monthly --month 2026-01` |
| Read query | `python3 tracker.py --query "SELECT * FROM expenses"` |
| Write query | `python3 tracker.py --query-write "UPDATE ..."` |
| Purge deleted | `python3 tracker.py --purge` |
| Export CSV | `python3 tracker.py --export` |

## Configuration

Config file: `~/data/finance-tracker/config.json`

```json
{
    "db_path": "~/data/finance-tracker/finance.db",
    "currency": "₹",
    "audit": {
        "enabled": true,
        "log_select_queries": false
    }
}
```

Budget values are managed via the `--set-budget` command, not in config.

## Data Safety

- **No implicit DB creation** — `--init` must be run explicitly.
- **Soft-delete** — `--remove` marks entries as deleted, does not erase them.
- **Audit log** — every INSERT, UPDATE, DELETE, and raw SQL write is recorded.
- **Query guard** — `--query` only allows SELECT statements. Mutations require `--query-write`.
