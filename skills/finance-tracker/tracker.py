import sqlite3
import json
import argparse
import csv
import os
import contextlib
from datetime import datetime

DB_NAME = "finance.db"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        config = {"default_monthly_budget": 50000.0, "currency": "₹"}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
        return config
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys just in case, though not strictly used here yet
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            category TEXT DEFAULT 'Uncategorised',
            description TEXT,
            transaction_date TEXT,
            inserted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_key TEXT UNIQUE,
            budget_limit REAL,
            monthly_savings REAL DEFAULT 0,
            inserted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_type TEXT,
            period_key TEXT,
            total_amount REAL,
            category_breakdown TEXT,
            inserted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(period_type, period_key))''')
        
        for table in ['expenses', 'budgets', 'analytics']:
            c.execute(f'''CREATE TRIGGER IF NOT EXISTS update_{table}_timestamp 
                        AFTER UPDATE ON {table} BEGIN
                        UPDATE {table} SET updated_on = CURRENT_TIMESTAMP WHERE id = old.id; END;''')
        conn.commit()

def get_monthly_stats(conn, date_str):
    """
    Internal helper to calculate stats using an existing connection.
    Does NOT commit or close the connection.
    """
    month_key = date_str[:7]
    
    res = conn.execute("SELECT SUM(amount) as total FROM expenses WHERE transaction_date LIKE ?", (f"{month_key}%",)).fetchone()
    spent = res['total'] if res and res['total'] is not None else 0.0
    
    budget = conn.execute("SELECT budget_limit FROM budgets WHERE month_key = ?", (month_key,)).fetchone()
    
    limit = budget['budget_limit'] if budget else load_config()['default_monthly_budget']
    
    return {"spent": spent, "limit": limit, "percentage": (spent/limit)*100 if limit > 0 else 0}

def add_expense(amount, category, description, date=None):
    if not date: date = datetime.now().strftime('%Y-%m-%d')
    
    with get_db() as conn:
        # Use immediate transaction to prevent other writers from jumping in
        conn.execute("BEGIN IMMEDIATE") 
        try:
            cur = conn.cursor()
            cur.execute("INSERT INTO expenses (amount, category, description, transaction_date) VALUES (?, ?, ?, ?)",
                        (amount, category or "Uncategorised", description, date))
            new_id = cur.lastrowid
            
            # Fetch the row back within the same transaction
            row = dict(cur.execute("SELECT * FROM expenses WHERE id = ?", (new_id,)).fetchone())
            
            # Calculate stats within the same isolation snapshot
            stats = get_monthly_stats(conn, date)
            
            conn.commit()
            return {"status": "success", "data": row, "stats": stats}
        except Exception as e:
            conn.rollback()
            raise e

def summarize(p_type, month_num=None):
    year_month = f"{datetime.now().year}-{month_num}" if month_num else datetime.now().strftime('%Y-%m')
    
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            res = conn.execute("SELECT SUM(amount) as s FROM expenses WHERE transaction_date LIKE ?", (f"{year_month}%",)).fetchone()
            spent = res['s'] if res and res['s'] else 0
            
            config = load_config()
            budget_row = conn.execute("SELECT budget_limit FROM budgets WHERE month_key = ?", (year_month,)).fetchone()
            limit = budget_row['budget_limit'] if budget_row else config['default_monthly_budget']
            savings = limit - spent
            
            conn.execute('''INSERT INTO budgets (month_key, budget_limit, monthly_savings) VALUES (?, ?, ?)
                            ON CONFLICT(month_key) DO UPDATE SET monthly_savings=excluded.monthly_savings''', (year_month, limit, savings))
            conn.commit()
            return {"month": year_month, "spent": spent, "budget": limit, "savings": savings}
        except Exception as e:
            conn.rollback()
            raise e

if __name__ == "__main__":
    init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs='+') # amount, cat, desc, [date]
    parser.add_argument("--bulk-add", type=str) # JSON string
    parser.add_argument("--remove", type=int)
    parser.add_argument("--query", type=str)
    parser.add_argument("--summarize", choices=['daily', 'weekly', 'monthly'])
    parser.add_argument("--month", type=str)
    parser.add_argument("--set-budget", nargs=2) # amount, month_key
    parser.add_argument("--export", action="store_true")

    args = parser.parse_args()
    try:
        if args.add:
            amount = float(args.add[0])
            category = args.add[1]
            description = args.add[2]
            d = args.add[3] if len(args.add) > 3 else None
            print(json.dumps(add_expense(amount, category, description, d)))
        elif args.bulk_add:
            expenses = json.loads(args.bulk_add)
            results = []
            with get_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.cursor()
                try:
                    for exp in expenses:
                        amount = float(exp.get('amount'))
                        category = exp.get('category', 'Uncategorised')
                        description = exp.get('description', '')
                        date = exp.get('date', datetime.now().strftime('%Y-%m-%d'))
                        
                        cur.execute("INSERT INTO expenses (amount, category, description, transaction_date) VALUES (?, ?, ?, ?)",
                                    (amount, category, description, date))
                        
                        # We don't fetch every row back to keep it fast, just track success
                        results.append({"status": "queued", "description": description})
                    
                    # Calculate stats once at the end
                    stats = get_monthly_stats(conn, datetime.now().strftime('%Y-%m-%d'))
                    conn.commit()
                    print(json.dumps({"status": "success", "count": len(results), "stats": stats}))
                except Exception as e:
                    conn.rollback()
                    raise e
        elif args.remove:
            with get_db() as conn:
                conn.execute("DELETE FROM expenses WHERE id=?", (args.remove,))
                conn.commit()
            print(json.dumps({"status": "deleted", "id": args.remove}))
        elif args.query:
            with get_db() as conn:
                # Read-only query, no need for immediate transaction unless strictly required for consistency
                # Default deferred transaction is fine for SELECT
                res = [dict(r) for r in conn.execute(args.query).fetchall()]
            print(json.dumps(res))
        elif args.summarize:
            print(json.dumps(summarize(args.summarize, args.month)))
        elif args.export:
            with get_db() as conn:
                for t in ['expenses', 'budgets', 'analytics']:
                    rows = conn.execute(f"SELECT * FROM {t}").fetchall()
                    if rows:
                        with open(f"{t}.csv", 'w') as f:
                            w = csv.DictWriter(f, fieldnames=rows[0].keys())
                            w.writeheader(); w.writerows([dict(r) for r in rows])
            print(json.dumps({"status": "exported"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
