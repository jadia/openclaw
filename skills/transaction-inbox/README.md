# Transaction Inbox Skill

Batch email transaction ingestion for the [finance-tracker](../finance-tracker/) skill. Fetches transaction emails from a dedicated Gmail inbox via IMAP, parses them with regex patterns for Indian banks and merchants, deduplicates, auto-inserts into the finance-tracker ledger, and sends a Telegram review summary via OpenClaw.

## Features

- **Gmail IMAP integration** — App Password authentication, no OAuth required
- **Two-tier parsing** — regex for known senders (HDFC, SBI, ICICI, Axis, Equitas, Swiggy, Zomato, etc.), LLM fallback for unknown formats
- **Smart deduplication** — hard match on reference IDs (UTR, order_id), soft match on amount + merchant + time window
- **Auto-insert** — high-confidence transactions go directly into the finance-tracker ledger
- **Telegram summaries** — nightly review via OpenClaw with numbered transactions
- **Review & correction** — reply with "delete #3", "recategorise #2 to Junk", etc.
- **Reprocessing** — re-fetch older emails by date range without duplicating entries
- **Comprehensive logging** — daily run logs with regex hit/miss stats for ongoing improvement
- **Non-destructive** — non-matching emails stay unseen, state pruning only affects local JSON, never Gmail

## Architecture

```
bin/main.py       → CLI orchestrator (--setup, --process, --reprocess)
lib/gmail_client.py → IMAP connection, email fetching, MIME parsing
lib/parser.py       → Regex pattern registry + LLM fallback
lib/dedup.py        → Hard + soft duplicate detection
lib/state.py        → State file management, checkpointing, pruning
```

## Setup

### 1. Prerequisites

- Python 3.9+
- OpenClaw with Telegram integration (for summaries)
- A dedicated Gmail account for transaction email collection
- The [finance-tracker](../finance-tracker/) skill initialised (`python3 tracker.py --init`)

### 2. Gmail Account Setup

1. **Create a dedicated Gmail account** (e.g., `mytransactions@gmail.com`)
2. **Enable 2-Factor Authentication**:
   - Go to [Google Account Security](https://myaccount.google.com/security)
   - Under "Signing in to Google", enable 2-Step Verification
3. **Generate an App Password**:
   - Go to [App Passwords](https://myaccount.google.com/apppasswords)
   - Select "Mail" and your device, click "Generate"
   - Copy the 16-character password (shown once only)
4. **Enable IMAP** (usually enabled by default):
   - Gmail → Settings → See all settings → Forwarding and POP/IMAP
   - Ensure "Enable IMAP" is selected

### 3. Email Forwarding Setup

In each of your personal email accounts, create forwarding filters:

**Gmail example:**
1. Settings → Filters and Blocked Addresses → Create a new filter
2. From: `alerts@hdfcbank.net` (or relevant sender)
3. Create filter → Forward to: `mytransactions@gmail.com`
4. Repeat for each bank, card, and merchant sender

**Example senders to forward:**

| Source | Sender |
|:---|:---|
| HDFC Bank | `alerts@hdfcbank.net` |
| SBI | `donotreply@sbi.co.in` |
| ICICI | `transaction@icicibank.com` |
| Axis Bank | `alerts@axisbank.com` |
| Equitas | `noreply@equitasbank.com` |
| Swiggy | `noreply@swiggy.in` |
| Zomato | `noreply@zomato.com` |
| Amazon | `auto-confirm@amazon.in` |
| Flipkart | `noreply@flipkart.com` |
| Uber | `noreply@uber.com` |
| Ola | `noreply@olacabs.com` |

### 4. Skill Installation

```bash
cd skills/transaction-inbox

# Create virtualenv (optional, skill uses stdlib only)
bash bin/install.sh

# Initialize state directory
python3 bin/main.py --setup
```

### 5. Configuration

Edit `state/settings.json`:

```json
{
  "gmail": {
    "host": "imap.gmail.com",
    "email": "mytransactions@gmail.com",
    "app_password": "abcd efgh ijkl mnop"
  },
  "allowed_senders": [
    "alerts@hdfcbank.net",
    "donotreply@sbi.co.in",
    "noreply@swiggy.in"
  ]
}
```

**Key settings:**

| Field | Description | Default |
|:---|:---|:---|
| `gmail.email` | Dedicated Gmail address | (required) |
| `gmail.app_password` | 16-char App Password | (required) |
| `allowed_senders` | Sender email addresses to process | (pre-filled) |
| `dedup.time_window_minutes` | Soft-match time tolerance | `30` |
| `dedup.amount_tolerance` | Amount tolerance in ₹ | `1.0` |
| `state.prune_after_days` | Days to keep processed records | `60` |
| `finance_tracker.skill_dir` | Relative path to finance-tracker | `../finance-tracker` |

### 6. First Run

```bash
# Test the connection and process any existing emails
python3 bin/main.py --process
```

### 7. Cron Setup

Add to your crontab (`crontab -e`):

```cron
# Process transaction emails at 22:00 daily
0 22 * * * cd /path/to/skills/transaction-inbox && python3 bin/main.py --process
```

Or if using the virtualenv:
```cron
0 22 * * * cd /path/to/skills/transaction-inbox && .venv/bin/python bin/main.py --process
```

## Usage

### Process New Emails (CLI)

```bash
python3 bin/main.py --process
```

### Process via OpenClaw Chat

Tell OpenClaw: "Process my transaction emails"

### Reprocess a Date Range

```bash
python3 bin/main.py --reprocess --from 2026-03-15 --to 2026-03-23
```

This re-fetches all emails in the range regardless of Seen status. Dedup against the ledger prevents double-insertion.

## How It Works

### Parsing Pipeline

1. **Sender filter** — only process emails from `allowed_senders`
2. **Tier-1 regex** — pre-built patterns for Indian banks and merchants extract amount, merchant, date, reference IDs
3. **Tier-2 LLM fallback** — if regex fails, the email body is packaged for OpenClaw to extract details
4. **Description building** — merchant name + reference IDs appended (e.g., "Swiggy [order_id: 12345]")

### Duplicate Detection

**Stage 1 — Hard match:**
Strong identifiers (UTR, order_id, ref_no, booking_id) are compared against recently processed emails. If any match → definite duplicate, skipped.

**Stage 2 — Soft match:**
Heuristic scoring on: amount (±₹1), merchant (fuzzy), direction, time (±30 min).
- Score ≥ 4 → auto-merged (higher-detail record kept)
- Score ≥ 3 → probable duplicate (flagged for review)
- Score < 3 → new transaction

### Review & Correction Flow

After processing, OpenClaw sends a Telegram summary. You can reply with:

- `delete #3` → removes the transaction
- `change #5 amount to 450` → updates the amount
- `recategorise #2 to Junk` → changes the category
- `merge #4 and #6` → keeps the richer one, removes the other
- `confirm all` → no action needed (already inserted)

## State Files

| File | Purpose | Pruning |
|:---|:---|:---|
| `state/settings.json` | Configuration | Never |
| `state/processed_emails.json` | Processed email IDs, results | After 60 days |
| `state/pending_transactions.json` | Last run's candidates + summary | Overwritten each run |
| `state/logs/run_YYYY-MM-DD.log` | Detailed run logs | Manual cleanup |

## Troubleshooting

### Gmail connection fails
- Verify `state/settings.json` has correct email and app_password
- Ensure IMAP is enabled in Gmail settings
- Check that 2FA is enabled (required for App Passwords)
- Check `state/logs/` for detailed error messages

### Regex not matching an email format
- Check `state/logs/run_YYYY-MM-DD.log` for `PARSE_MISS` entries
- The log shows which patterns were tried and why they failed
- Add custom patterns to `state/settings.json` under `parsing.custom_patterns`
- Or let the LLM fallback handle it

### Duplicate transactions inserted
- Reprocessing automatically deduplicates against the ledger
- Use `python3 ../finance-tracker/tracker.py --remove <id>` to remove extras
- Check `processed_emails.json` for the duplicate's email_uid

### Want to safely rerun
- `--process` only fetches UNSEEN emails, so re-running is safe
- `--reprocess` checks against the ledger, so re-running a date range is safe
- Worst case: remove duplicates manually via finance-tracker

### State file corrupted
```bash
# Re-initialize (preserves settings.json if it exists)
python3 bin/main.py --setup
```

## Data Safety

- **Email safety**: Non-matching emails are left UNSEEN. Processed emails are only marked as Seen, never deleted or modified.
- **State pruning**: Only removes local JSON records after 60 days. Gmail data is untouched.
- **Ledger safety**: All inserts go through finance-tracker's audit log and support soft-delete.
- **Reprocessing**: Dedup against the ledger prevents double-insertion even when re-running.
