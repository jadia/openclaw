---
name: transaction-inbox
description: Batch email transaction ingestion — fetches from Gmail, parses, deduplicates, and inserts into finance-tracker with Telegram review summaries.
user-invocable: true
---

# Transaction Inbox Instructions

You manage an email-based transaction ingestion pipeline that feeds into the **finance-tracker** skill.
This skill fetches transaction emails from a dedicated Gmail inbox, parses them, deduplicates, auto-inserts into the ledger, and sends you a Telegram summary.

## Execution / Orchestration

**Assumption:** You must run all commands from within the `skills/transaction-inbox/` directory so that the relative path to `.venv` resolves correctly. If you are not in this directory, `cd` into it first.

The Python orchestrator lives at `bin/main.py`. Dependencies are stdlib-only (no install needed beyond Python 3.9+).

**CLI routes:**
- Setup: `.venv/bin/python bin/main.py --setup`
- Process new emails: `.venv/bin/python bin/main.py --process`
- Reprocess date range: `.venv/bin/python bin/main.py --reprocess --from 2026-03-20 --to 2026-03-24`

## When the User Says "Process My Transaction Emails"

Run the processing pipeline:
```
.venv/bin/python bin/main.py --process
```
This will:
1. Fetch unseen emails from the configured Gmail inbox
2. Filter by allowed senders (non-matching emails stay unseen for other skills)
3. Parse transaction details (regex for known senders, LLM fallback for others)
4. Deduplicate against recent ledger and within-batch
5. Auto-insert new transactions into finance-tracker
6. Send you a Telegram summary

## When the User Says "Reprocess Emails from Last Week"

Parse the date range and run:
```
.venv/bin/python bin/main.py --reprocess --from 2026-03-16 --to 2026-03-23
```

## State

All state files live under `state/`:
- `settings.json` — Gmail credentials, allowed senders, dedup config
- `processed_emails.json` — checkpoint of processed email IDs
- `pending_transactions.json` — last run's candidates and summary
- `logs/` — daily log files for debugging

## Nightly Summary Review

After processing, a summary is sent via Telegram with numbered transactions:
```
#1 ₹450.00 — Swiggy (2026-03-23) [new]
#2 ₹1200.00 — HDFC UPI (2026-03-23) [new]
#3 ₹450.00 — HDFC Debit (2026-03-23) [auto_merged]
#4 ₹899.00 — Amazon (2026-03-23) [probable_duplicate]
```

## Review & Correction Commands

The user may reply with corrections. Map each to the appropriate finance-tracker command:

| User Says | Action |
|:---|:---|
| "delete #3" | `python3 tracker.py --remove <ledger_id>` |
| "change #5 amount to 450" | `python3 tracker.py --query-write "UPDATE expenses SET amount = 450 WHERE id = <ledger_id>"` |
| "recategorise #2 to Junk" | `python3 tracker.py --update-category <ledger_id> Junk` |
| "merge #4 and #6" | Keep the one with more detail, `--remove` the other |
| "confirm all" | No action needed — transactions are already inserted |
| "mark #4 as not a transaction" | `python3 tracker.py --remove <ledger_id>` |

**Important:** When running finance-tracker commands, use the **ledger IDs** from the summary, not the `#N` numbers. Check `pending_transactions.json` to find the mapping between summary numbers and ledger IDs.

Run finance-tracker commands from the finance-tracker skill directory:
```
cd ../finance-tracker && .venv/bin/python tracker.py --remove 42
```

## LLM Parsing Fallback

If any emails are marked `[LLM PARSING NEEDED]` in the summary:
1. Read `state/pending_transactions.json`
2. Find the candidate with `parse_method: "llm_needed"`
3. Read its `body_for_llm` field
4. Extract: amount, merchant, date, description
5. Use finance-tracker `--add` to insert:
   ```
   cd ../finance-tracker && .venv/bin/python tracker.py --add <amount> <category> "<description>" <YYYY-MM-DD>
   ```
6. Use `--suggest-category` if category is unclear

## Logs

Daily log files are at `state/logs/run_YYYY-MM-DD.log`.
These contain detailed regex hit/miss data, dedup decisions, and error traces.
If the user reports parsing issues, read the latest log file to diagnose.

## Error Handling

- If `pending_transactions.json` contains `"status": "error"`, explain the error.
- If Gmail connection fails, check `state/settings.json` for correct credentials.
- If finance-tracker insertion fails, verify the DB exists (`cd ../finance-tracker && .venv/bin/python tracker.py --init`).
- Common fix: re-run `.venv/bin/python bin/main.py --setup` to reset state files.
