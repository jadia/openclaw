---
name: finance-tracker
description: Proactive finance manager with audit logging, category budgets, and smart categorisation.
user-invocable: true
---

# Finance Tracker Instructions

You manage a personal finance system via `tracker.py`. All transactions are in Indian Rupees (₹).
All script output is JSON. Always run commands from the skill directory.

## First-Time Setup

If the database does not exist, initialise it:
```
python3 tracker.py --init
```

## Database Schema

- **expenses**: `id`, `amount`, `category`, `description`, `transaction_date` (YYYY-MM-DD), `deleted_at`, `inserted_on`, `updated_on`
- **budgets**: `id`, `month_key` (YYYY-MM or 'default'), `category` (NULL = overall), `budget_limit`, `inserted_on`, `updated_on`
- **audit_log**: `id`, `action`, `table_name`, `row_id`, `old_values`, `new_values`, `source`, `created_on`

## Operational Rules

### 1. Expense Entry

- **Single**: `python3 tracker.py --add <amount> <category> <description> [YYYY-MM-DD]`
- **Bulk** (always prefer for multiple items):
  ```
  python3 tracker.py --bulk-add '[{"amount":50,"category":"Food","description":"Tea"},...]'
  ```
- **Aggregation**: If adding multiple items that fall under the SAME category, aggregate them into a single entry with the total amount. List the individual items in the description.
  - *Example*: "Add grocery items (milk, bread, eggs) for Rs.700 and ice-cream for Rs.50." → 
    `--bulk-add '[{"amount":700,"category":"Groceries","description":"Grocery items: milk, bread, eggs"}, {"amount":50,"category":"Junk","description":"Ice-cream"}]'`
- **Order Tracking**: If an order ID or transaction ID is mentioned, ALWAYS append it to the description to help identify duplicates later (e.g., `Pizza [Order ID: 1234]`).
- **Backdating**: Convert natural language dates to YYYY-MM-DD and pass as 4th arg.
  - *Example*: "Add Rs.220 for pizza to junk on last Friday" → `--add 220 Junk Pizza 2026-02-20`

### 2. Smart Categorisation

When the user omits a category:
1. **Suggest**: `python3 tracker.py --suggest-category "Pizza"`
   - Returns `{"suggested":"Junk","confidence":5,...}`
2. **If confidence ≥ 3**: Auto-use the suggested category.
3. **If confidence < 3**: Run `python3 tracker.py --categories` to list all categories, then use your judgement or ask the user.
4. **If wrong**: `python3 tracker.py --update-category <id> <correct_category>`

### 3. Deletions

- `--remove <ID>` performs a **soft-delete** (recoverable). The entry is excluded from reports.
- Always confirm the entry with the user before removing.
- If ID is unknown, use `--query` to find it first.

### 4. Budget Management

- **Set overall budget**: `python3 tracker.py --set-budget 50000 2026-03`
- **Set category budget**: `python3 tracker.py --set-budget 3000 2026-03 Junk`
- **Set global default**: `python3 tracker.py --set-budget 50000 default`
- **Set global category default**: `python3 tracker.py --set-budget 3000 default Junk`

### 5. Budget Alerts

When the summary JSON shows `percentage >= 80`, include:
> **⚠️ Alert: You have exhausted [X]% of your monthly budget!**

When a category's `overspent` field is `true`, include:
> **⚠️ Over budget on [Category]! Spent ₹X / ₹Y limit.**

### 6. SQL Query Rules

- **`--query` (read-only)**: Run freely. SELECT only. Format output as aligned code block.
- **`--query-write` (mutations)**: You **MUST**:
  1. Show the user the exact SQL.
  2. Explain what will change.
  3. Wait for explicit "Yes" / "Proceed" before executing.

### 7. Summaries

- `python3 tracker.py --summarize daily`
- `python3 tracker.py --summarize weekly`
- `python3 tracker.py --summarize monthly`
- Past month: `python3 tracker.py --summarize monthly --month 2026-01`

Summaries include per-category breakdown with budget comparison.

### 8. Maintenance

- **Purge deleted entries**: `python3 tracker.py --purge`
- **Export to CSV**: `python3 tracker.py --export`

### 9. Error Handling

- If JSON contains `"status": "error"`, explain the error in plain English.
- If database is locked, apologise and retry once.

### 10. Output Formatting

Display results in a **code block** with aligned columns. Do not use markdown table pipes.
```
ID    Date        Category    Amount   Description
1     2026-02-28  Food        500.00   Lunch
```

## Cheat Sheet

| Action | Command |
|:---|:---|
| **Init DB** | `python3 tracker.py --init` |
| **Add one** | `python3 tracker.py --add 500 Junk Pizza` |
| **Add many** | `python3 tracker.py --bulk-add '[...]'` |
| **Remove** | `python3 tracker.py --remove 5` |
| **Set budget** | `python3 tracker.py --set-budget 50000 2026-03` |
| **Category budget** | `python3 tracker.py --set-budget 3000 default Junk` |
| **Fix category** | `python3 tracker.py --update-category 5 Food` |
| **Suggest cat** | `python3 tracker.py --suggest-category "Pizza"` |
| **List cats** | `python3 tracker.py --categories` |
| **Summary** | `python3 tracker.py --summarize monthly` |
| **Read query** | `python3 tracker.py --query "SELECT ..."` |
| **Write query** | `python3 tracker.py --query-write "UPDATE ..."` |
| **Purge** | `python3 tracker.py --purge` |
