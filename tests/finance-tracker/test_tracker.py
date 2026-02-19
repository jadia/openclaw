import unittest
import sqlite3
import json
import os
import sys
import threading
import time
import datetime

# Add the skills directory to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../skills/finance-tracker')))

from tracker import add_expense, summarize, init_db, DB_NAME, CONFIG_FILE

class TestFinanceTracker(unittest.TestCase):
    def setUp(self):
        """Setup a fresh database and config before each test."""
        # Clean up any existing files first
        if os.path.exists(DB_NAME):
            os.remove(DB_NAME)
        
        # Backup existing config check
        if os.path.exists(CONFIG_FILE):
            os.rename(CONFIG_FILE, CONFIG_FILE + ".bak")
             
        # Create fresh config
        init_db()

    def tearDown(self):
        """Clean up database and config after each test."""
        if os.path.exists(DB_NAME):
            try:
                os.remove(DB_NAME)
            except OSError:
                pass # Already deleted or locked
        
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
            
        # Restore original config
        if os.path.exists(CONFIG_FILE + ".bak"):
            os.rename(CONFIG_FILE + ".bak", CONFIG_FILE)

    def test_add_expense(self):
        """Verify that an expense can be added and is returned correctly."""
        result = add_expense(100.0, "Food", "Lunch")
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['data']['amount'], 100.0)
        self.assertEqual(result['data']['category'], 'Food')
        self.assertEqual(result['data']['description'], 'Lunch')
        
    def test_add_expense_custom_date(self):
        """Verify adding an expense with a custom historical date."""
        result = add_expense(50.0, "Snacks", "Chips", date="2025-01-01")
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['data']['transaction_date'], "2025-01-01")

    def test_summarize_empty(self):
        """Verify summary of an empty database returns default budget and 0 spent."""
        result = summarize('monthly')
        self.assertEqual(result['monthly_spent'], 0)
        self.assertEqual(result['budget'], 50000.0)
        self.assertEqual(result['savings'], 50000.0)

    def test_summarize_with_data(self):
        """Verify summary calculations with multiple transactions."""
        add_expense(100.0, "Food", "Lunch")
        add_expense(200.0, "Transport", "Bus")
        
        result = summarize('monthly')
        self.assertEqual(result['monthly_spent'], 300.0)
        self.assertEqual(result['budget'], 50000.0)
        self.assertEqual(result['savings'], 49700.0)

    def test_custom_budget(self):
        """Verify that manually setting a budget references that specific month's limit."""
        from datetime import datetime
        month_key = datetime.now().strftime('%Y-%m')
        
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO budgets (month_key, budget_limit) VALUES (?, ?)", (month_key, 60000.0))
        conn.commit()
        conn.close()
        
        result = summarize('monthly')
        self.assertEqual(result['budget'], 60000.0)

    def test_concurrency_add_expense(self):
        """
        Verify that multiple threads adding expenses simultaneously 
        do not corrupt the database.
        """
        def add_entry():
            add_expense(10.0, "Concurrent", "Test")

        threads = []
        for _ in range(10):
            t = threading.Thread(target=add_entry)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

        # Check total
        conn = sqlite3.connect(DB_NAME)
        row = conn.execute("SELECT SUM(amount) as s, COUNT(*) as c FROM expenses").fetchone()
        conn.close()
        
        self.assertEqual(row[1], 10, "Should have 10 entries")
        self.assertEqual(row[0], 100.0, "Total amount should be 100.0")

    def test_backdated_summary(self):
        """
        Verify that adding an expense for a past month and summarizing it 
        correctly updates that month's budget and savings.
        """
        # Add expense for Jan 2025
        past_date = "2025-01-15"
        add_expense(1000.0, "History", "Ancient Debt", date=past_date)
        
        # Summarize Jan 2025
        # Note: summarize argument for month can now be YYYY-MM
        result = summarize('monthly', month_num="2025-01")
        
        self.assertEqual(result['month'], "2025-01")
        self.assertEqual(result['monthly_spent'], 1000.0)
        
        # Check if it persisted in budgets table
        conn = sqlite3.connect(DB_NAME)
        row = conn.execute("SELECT * FROM budgets WHERE month_key = ?", ("2025-01",)).fetchone()
        conn.close()
        
        self.assertEqual(row[3], 50000.0 - 1000.0) # default limit - spent = savings

    def test_daily_summary(self):
        """Verify daily summary returns correct day's total and monthly context."""
        # Add entry for today
        add_expense(150.0, "Test", "Daily 1")
        # Add entry for yesterday (should not be in daily total but in monthly)
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        add_expense(200.0, "Test", "Yesterday", date=yesterday)
        
        result = summarize('daily')
        
        self.assertEqual(result['period'], 'daily')
        self.assertEqual(result['period_spent'], 150.0) # Only today's
        self.assertEqual(result['monthly_spent'], 350.0) # Both today + yesterday (same month)

if __name__ == '__main__':
    unittest.main()
