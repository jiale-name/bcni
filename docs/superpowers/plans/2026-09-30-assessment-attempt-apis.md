# Assessment Attempt APIs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement only API-131 through API-134 on top of the accepted assessment data layer.

**Architecture:** Extend the existing assessment schema, DAO, service, and router modules. Start and save-answer operations use one database transaction; API-131 locks the task-candidate row, and API-134 locks the attempt row before checking `answer_version`. API-133 reads only the immutable `paper_snapshot` stored on the attempt.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, MySQL, pytest.

**Spec:** Current user-approved Attempt API-131~134 implementation request.

## Global Constraints

- Implement only API-131, API-132, API-133, and API-134.
- Do not add or alter database fields, tables, foreign keys, indexes, migrations, or frozen documents.
- Reuse the existing authentication, permission, DataScope, session, transaction, error, and Assessment response conventions.
- Do not implement API-135~139, Worker, auto-submit, scoring, monitoring, reporting, or a Candidate module.

---

### Task 1: Add failing API contract tests

**Files:**
- Create: `backend/tests/test_assessment_attempt_api.py`
- Test existing: `backend/tests/test_assessment_task.py`

**Interfaces:**
- Consumes the existing `AssessmentPaper`, `AssessmentPaperQuestion`, `AssessmentTask`, and `AssessmentTaskCandidate` fixtures and app dependency overrides.
- Produces executable coverage for API-131~134, including idempotency, access control, snapshot reads, version conflicts, and candidate progress.

- [ ] Write focused tests for first start, in-progress replay, paper snapshot creation, detail access, snapshot immutability, first/overwrite answer saves, and `40902` version conflicts.
- [ ] Run the new test module and verify failures are caused by missing Attempt API behavior.

### Task 2: Add Attempt and Answer schemas

**Files:**
- Modify: `backend/app/schemas/assessment.py`

**Interfaces:**
- Produces request and response models consumed by the Attempt service and router.
- Uses the existing Assessment ISO-8601 serializer, Snowflake ID serializer, and response envelope.

- [ ] Add request models for API-131 and API-134 with strict fields and JSON answer validation.
- [ ] Add response models for API-131, API-132, API-133, and API-134 without exposing unfrozen fields.
- [ ] Run schema-focused tests.

### Task 3: Add Attempt DAO operations

**Files:**
- Modify: `backend/app/dao/assessment_dao.py`

**Interfaces:**
- Produces scoped reads, `with_for_update()` candidate/attempt reads, paper-question snapshot reads, and current-answer insert/update operations.
- DAO methods flush but do not commit.

- [ ] Add DAO methods with tenant/task/user/candidate scope conditions.
- [ ] Ensure lock-bearing methods use row locks and answer lookup uses the frozen unique key.
- [ ] Run service tests that exercise these methods.

### Task 4: Implement Attempt service flows

**Files:**
- Create: `backend/app/services/assessment_attempt_service.py`

**Interfaces:**
- `start_attempt(db, principal, task_id, idempotency_key)` returns API-131 data.
- `get_attempt_detail(db, principal, attempt_id)` returns API-132 data.
- `list_attempt_questions(db, principal, attempt_id, page_no, page_size)` returns API-133 data.
- `save_answer(db, principal, attempt_id, paper_question_id, request, idempotency_key)` returns API-134 data.

- [ ] Implement scoped task/candidate lookup and paper publication/start-time checks required by the frozen start contract.
- [ ] Implement Candidate row locking, in-progress replay, attempt-limit validation, snapshot creation, and one-transaction updates.
- [ ] Implement immutable snapshot pagination and answer summary reads.
- [ ] Implement Attempt row locking, version validation, current-answer upsert, server timestamps, and progress update in one transaction.
- [ ] Run focused tests and fix production code, not tests, until green.

### Task 5: Register Attempt routes

**Files:**
- Modify: `backend/app/routers/assessment_router.py`

**Interfaces:**
- Adds only the frozen paths for API-131~134 under the existing `/api/v1` prefix.
- Applies the existing permission dependency and Idempotency-Key dependency only to write endpoints.

- [ ] Add the four routes and response models.
- [ ] Verify OpenAPI paths, permissions, and GET behavior without Idempotency-Key.

### Task 6: Verify scope and regression safety

**Files:**
- No production schema or migration files.

- [ ] Run Attempt-specific tests.
- [ ] Run related assessment tests and the full pytest suite.
- [ ] Run `compileall`.
- [ ] Verify Alembic head remains `a6b7c8d9e0f1`, no migration changed, and ORM/DB contract checks remain clean.
- [ ] Confirm frozen documents and accepted tables were not modified.
