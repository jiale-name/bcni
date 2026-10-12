# TBL-37 Assessment Event Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the frozen TBL-37 `assessment_event` data layer and verify its exact ORM, migration, MySQL, and contract-test shape.

**Architecture:** Add `AssessmentEvent` to the existing `app.models.assessment` module and export it from `app.models`. Create one hand-written Alembic revision after `f5a6b7c8d9e0`; the migration creates only the 13 frozen columns, primary key, client-event unique constraint, and `idx_attempt_event_received`. No physical foreign keys or business/API code are added.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, MySQL dialect types, Alembic, pytest.

**Spec:** `docs/人才测评分析Agent/数据库设计文档.md`, `docs/人才测评分析Agent/数据字典.md`, `docs/人才测评分析Agent/接口设计文档.md`, `docs/人才测评分析Agent/开发实施文档.md`, `docs/人才测评分析Agent/约束性文档.md`.

## Global Constraints

- TBL-37 has exactly 13 columns and no additional public fields.
- Every TBL-37 column has no database default; `received_at`, `severity`, and `review_status` are application-assigned.
- The only unique constraint is `uk_attempt_client_event(tenant_id, attempt_id, client_event_id)`.
- The only ordinary index is `idx_attempt_event_received(attempt_id, received_at, event_type)`.
- No physical ForeignKey is created.
- Do not implement API-139, Monitoring, Worker, TBL-38, or any unrelated Alembic drift.

---

### Task 1: Add failing TBL-37 metadata contract tests

**Files:**
- Modify: `backend/tests/test_assessment_database_contract.py`

**Interfaces:**
- Consumes: `app.models.Base.metadata`.
- Produces: Tests that fail until `assessment_event` is registered with the frozen metadata contract.

- [ ] **Step 1: Add tests for registration, exact columns, types, nullability, and no defaults.**
- [ ] **Step 2: Add tests for the primary key, `uk_attempt_client_event`, `idx_attempt_event_received`, and no foreign keys.**
- [ ] **Step 3: Run the new TBL-37 test selection and confirm failure is caused by the missing model/table.**

Run: `pytest tests/test_assessment_database_contract.py -k assessment_event -v`

Expected: FAIL because `assessment_event` is not yet in `Base.metadata`.

### Task 2: Add the ORM model and registry export

**Files:**
- Modify: `backend/app/models/assessment.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Consumes: Existing `_id_column()`, `MYSQL_TABLE_KWARGS`, SQLAlchemy MySQL types, and registry import conventions.
- Produces: `AssessmentEvent` mapped to `assessment_event` with exactly 13 columns.

- [ ] **Step 1: Add `AssessmentEvent` with unsigned BIGINT identifiers, `DATETIME(fsp=3)`, JSON metadata, and the specified VARCHAR lengths.**
- [ ] **Step 2: Add `uk_attempt_client_event` and `idx_attempt_event_received` to `__table_args__`; do not add ForeignKey constraints or defaults.**
- [ ] **Step 3: Import and expose `AssessmentEvent` through `app.models`.**
- [ ] **Step 4: Run the TBL-37 metadata tests and confirm they pass.**

Run: `pytest tests/test_assessment_database_contract.py -k assessment_event -v`

Expected: ORM metadata assertions pass; database assertions remain skipped or fail until migration upgrade.

### Task 3: Create the isolated Alembic migration

**Files:**
- Create: `backend/alembic/versions/<timestamp>_assessment_event.py`

**Interfaces:**
- Consumes: Current head `f5a6b7c8d9e0`.
- Produces: One migration that creates and drops only `assessment_event`.

- [ ] **Step 1: Write the revision with `down_revision = "f5a6b7c8d9e0"`.**
- [ ] **Step 2: Define the exact 13 columns, primary key, unique constraint, and ordinary index without defaults or FKs.**
- [ ] **Step 3: Define downgrade as `op.drop_table("assessment_event")` only.**
- [ ] **Step 4: Run Alembic heads and migration compilation checks.**

Run: `python -m alembic heads` and `python -m compileall app alembic tests`

Expected: One head at the new revision and no syntax errors.

### Task 4: Upgrade and verify the database contract

**Files:**
- No additional production files.

**Interfaces:**
- Consumes: ORM metadata, migration, and existing database configured by `app.config.settings.database_url`.
- Produces: MySQL `assessment_event` matching metadata exactly.

- [ ] **Step 1: Run `python -m alembic upgrade head`.**
- [ ] **Step 2: Run `python -m alembic upgrade head` again and verify it is a no-op.**
- [ ] **Step 3: Run the database-backed TBL-37 contract tests and inspect columns, defaults, PK, constraints, indexes, and foreign keys.**
- [ ] **Step 4: Run the full assessment database contract suite, Batch 2A tests, and full pytest.**

Run: `python -m alembic upgrade head`; `pytest tests/test_assessment_database_contract.py -v`; `pytest -q`; `python -m compileall app alembic tests`

Expected: All relevant tests pass, the second upgrade makes no schema changes, and no unrelated table is altered.

### Task 5: Final scope and consistency review

- [ ] **Step 1: Confirm only TBL-37 ORM, registry, migration, and contract-test changes were made.**
- [ ] **Step 2: Confirm no API-139, service, router, worker, scoring, TBL-38, or unrelated migration changes were introduced.**
- [ ] **Step 3: Report the revision, test counts, upgrade results, and any remaining discrepancy with evidence from fresh commands.**
