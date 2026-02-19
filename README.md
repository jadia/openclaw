# OpenClaw Skills Repository

Repository for OpenClaw Skills, configuration, and documentation.

## Available Skills
- **finance-tracker**: A personal finance manager with budget alerts.

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
