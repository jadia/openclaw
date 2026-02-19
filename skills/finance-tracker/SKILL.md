---
name: finance-tracker
description: Proactive finance manager with SQL access and 80% budget alerts.
user-invocable: true
---

# Finance Tracker Instructions

You manage a personal finance system via `tracker.py`. All transactions are in Indian Rupees (₹).

## Database Schema Reference
- **expenses**: `id`, `amount`, `category`, `description`, `transaction_date` (YYYY-MM-DD), `inserted_on`, `updated_on`
- **budgets**: `id`, `month_key` (YYYY-MM), `budget_limit`, `monthly_savings`, `updated_on`

## Operational Rules

### 1. Expense Entry & Categorization
- **Single Entry Pattern**: The user often uses the format "Add [Amount] for [Description] in [Category]".
  - Example: "Add 500 for McDonalds in Junk" -> `tracker.py --add 500 "Junk" "McDonalds"`
- **New Categories**: If a user provides a new category, ask: "The category '[Name]' is new. Should I create it or log this as 'Uncategorised'?"
- **No Category**: If user skip category, try to identify the category from the description. If not found, use "Uncategorised".
- **Multiple Entries (Bulk Add)**:
  - **Goal**: Minimize API calls. If the user provides multiple expenses in one prompt, you MUST use `tracker.py --bulk-add`.
  - **Construct JSON**: Create a JSON list of objects: `[{"amount": 50, "category": "Food", "description": "Tea"}, ...]`.
  - **Avoid** calling `--add` multiple times.
- **Output**: Display the result in a **Code Block** (not Markdown Table) with aligned columns. Do not use pipes `|` or dashes `-`.
  - Example:
    ```
    ID    Date        Category    Amount   Description
    1     2026-02-19  Food        500.00   Lunch
    ```

### 2. Budget Alerts
- **80% Threshold**: If the script's `stats` object shows `percentage >= 80`, you MUST include a bold warning: "**Alert: You have exhausted [X]% of your monthly budget!**"

### 3. Deletions & Modifications
- Always try to identify the **ID** first. If known, use `python3 tracker.py --remove <ID>`.
- If the ID is unknown, use `--query` to `SELECT` and find it (output as table), then ask the user to confirm the ID before removing.

### 4. SQL Query Consent
- **SELECT/READ**: Run freely to answer questions. Format the output as a **Code Block** (aligned text).
- **WRITE/DELETE**: You MUST:
    1. Show the user the exact SQL string.
    2. Explain what will change.
    3. Wait for "Yes" or "Proceed" before executing.

### 6. Date Handling
- **Backdating**: The date parameter is optional. If the user specifies a past date, convert natural language (e.g., "last Friday", "yesterday", "20th Jan") to `YYYY-MM-DD` and pass it to `--add`.
- **Command Structure**: `tracker.py --add <Amount> <Category> <Description> <YYYY-MM-DD>`
- **Example**: "Add 500 Icecream in Junk on last Friday" -> `tracker.py --add 500 Junk Icecream 2026-02-13`

### 7. Summaries & Budgets
- **Reports**: Use `python3 tracker.py --summarize [daily|weekly|monthly]`.
- **Daily/Weekly**: These commands return specific period spend AND the monthly context (budget used %).
    - "How much did I spend today?" -> `tracker.py --summarize daily`
    - "How is my week going?" -> `tracker.py --summarize weekly`
- **Past Reports**: If asking about a past month, use `YYYY-MM`. e.g. "Summary for Jan 2025" -> `tracker.py --summarize monthly --month 2025-01`.
- **Custom Budget**: "Set budget to 60000 for March" -> `python3 tracker.py --set-budget 60000 2026-03`.

### 7. Error Handling
- If the script returns an error JSON, explain the error to the user in plain English.
- If the database is locked, apologize and retry once.

## Cheat Sheet

| Feature | Command Pattern |
| :--- | :--- |
| **Log One** | `python3 tracker.py --add <amt> <cat> <desc>` |
| **Log Many** | `python3 tracker.py --bulk-add '[{"amount":...}, ...]'` |
| **Check Spend** | `python3 tracker.py --summarize monthly` |
| **Set Budget** | `python3 tracker.py --set-budget <limit> <YYYY-MM>` |
| **Custom Query** | `python3 tracker.py --query "<SQL>"` |
