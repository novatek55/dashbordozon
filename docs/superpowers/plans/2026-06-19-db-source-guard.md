# DB Source Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent agents and local runs from silently using stale local databases when server data is expected.

**Architecture:** Add a small pure guard module that classifies database URLs and validates them against explicit source-mode settings. Wire the guard into `src.database.DatabaseManager.initialize`, expose settings via `.env.example`, and add a diagnostic CLI for humans/agents.

**Tech Stack:** Python, pydantic-settings, SQLAlchemy async, pytest.

---

### Task 1: Guard Rules

**Files:**
- Create: `src/db_source_guard.py`
- Test: `test_db_source_guard.py`

- [ ] Write failing tests for local SQLite and localhost rejection in server mode.
- [ ] Run `pytest test_db_source_guard.py -q` and verify imports fail because `src.db_source_guard` does not exist.
- [ ] Implement `classify_database_url`, `validate_database_source`, and `DatabaseSourceError`.
- [ ] Run `pytest test_db_source_guard.py -q` and verify guard tests pass.

### Task 2: Configuration And Wiring

**Files:**
- Modify: `src/config.py`
- Modify: `src/database.py`
- Modify: `.env.example`
- Modify: `deploy/ozon-dashboard.env.example`

- [ ] Add `DB_SOURCE_MODE`, `ALLOW_LOCAL_DATABASE`, and `EXPECTED_DB_HOST` settings.
- [ ] Call `validate_database_source` before creating the SQLAlchemy engine.
- [ ] Document safe defaults in example env files.

### Task 3: Diagnostic CLI And Docs

**Files:**
- Create: `scripts/check_db_source.py`
- Modify: `README.md`
- Modify: `DATABASE_SETUP.md`

- [ ] Add a CLI that prints the configured mode, database classification, and whether it is accepted.
- [ ] Document server DB as source of truth and local DB as explicit snapshot/dev fixture only.
- [ ] Run `python scripts/check_db_source.py` in the current environment and record the result.

### Task 4: Verification

**Files:**
- Test: `test_db_source_guard.py`

- [ ] Run `pytest test_db_source_guard.py -q`.
- [ ] Run a focused import check: `python -c "from src.config import settings; print(settings.db_source_mode)"`.
- [ ] Review `git diff` to make sure no unrelated user changes were reverted.
