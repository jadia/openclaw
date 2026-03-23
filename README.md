# OpenClaw Skills Repository

Repository for OpenClaw Skills, configuration, and documentation.

## Available Skills
- **ai_brief**: Daily or weekly AI briefing focused on frontier models, developer tools, open-source repos, and practical research updates. Now powered by a robust Python orchestrator.
- **finance-tracker**: Proactive finance manager with audit logging, category budgets, and smart categorisation.
- **transaction-inbox**: Batch email transaction ingestion — fetches from Gmail, parses, deduplicates, and inserts into finance-tracker with Telegram review summaries.

## How to Add a New Skill

1. **Create Skill Directory**:
   - `skills/<your-skill-name>/`
   - Must contain `manifest.yaml`, `SKILL.md`, and your entry script (e.g., `main.py`).

2. **Add Tests**:
   - Create `tests/<your-skill-name>/`
   - Add `requirements.txt` (if needed) and test files (e.g., `test_main.py`).

3. **CI/CD**:
   - The `.github/workflows/ci-python.yaml` workflow automatically detects folders in `tests/` and runs them.
   - It sets `PYTHONPATH` to `skills/<your-skill-name>/` automatically.

## Testing Structure

```
tests/
├── finance-tracker/       # Matches skills/finance-tracker/
│   ├── requirements.txt   # Test-specific dependencies
│   ├── conftest.py        
│   └── test_tracker.py    # Unit tests
```
