"""Attempt API-131~134 behavior tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.dao.session import SessionLocal
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError
from app.main import create_app
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentEvent,
    AssessmentPaper,
    AssessmentPaperQuestion,
    AssessmentTask,
    AssessmentTaskCandidate,
    AssessmentNotification,
    OutboxEvent,
)
from app.models.enums import RoleDataScope
from app.schemas.assessment import AssessmentTaskCreateRequest
from app.services.assessment_attempt_service import AssessmentAttemptService
from app.services.assessment_task_service import AssessmentTaskService
from app.workers.assessment_attempt_worker import run_once
from tests.conftest import requires_db


TENANT_ID = 7810000000000000001
ORG_ID = 7810000000000000011
PAPER_ID = 7810000000000000101
TASK_ID = 7810000000000000201
QUESTION_ROW_1 = 7810000000000000301
QUESTION_ROW_2 = 7810000000000000302
QUESTION_ID_1 = 7810000000000000401
QUESTION_ID_2 = 7810000000000000402
TALENT_ID = 7810000000000000501
USER_ID = 7810000000000000601


def _principal(
    *, user_id: int = USER_ID, org_id: int | None = ORG_ID, roles: tuple[str, ...] = ("EMP",)
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=TENANT_ID,
        org_id=org_id,
        roles=roles,
        data_scope=int(RoleDataScope.SELF),
    )


def _task_request() -> AssessmentTaskCreateRequest:
    now = datetime.now(timezone.utc)
    return AssessmentTaskCreateRequest(
        name="Attempt API 测试任务",
        paper_id=str(PAPER_ID),
        start_at=now - timedelta(minutes=5),
        deadline_at=now + timedelta(hours=2),
        duration_min=60,
        attempt_limit=2,
        target_scope={
            "mode": "explicit",
            "candidates": [
                {"talent_id": str(TALENT_ID), "user_id": str(USER_ID), "org_id": str(ORG_ID)}
            ],
        },
        notify_rules={"channels": ["in_app"], "notify_on": ["task_created"]},
    )


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _cleanup(db) -> None:
    db.execute(delete(OutboxEvent).where(OutboxEvent.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentEvent).where(AssessmentEvent.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentAnswer).where(AssessmentAnswer.attempt_id.in_(select(AssessmentAttempt.id))))
    db.execute(delete(AssessmentAttempt).where(AssessmentAttempt.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentPaperQuestion).where(AssessmentPaperQuestion.paper_id == PAPER_ID))
    db.execute(delete(AssessmentNotification).where(AssessmentNotification.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentTaskCandidate).where(AssessmentTaskCandidate.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentTask).where(AssessmentTask.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentPaper).where(AssessmentPaper.tenant_id == TENANT_ID))
    db.commit()


@pytest.fixture
def attempt_db(db):
    _cleanup(db)
    try:
        yield db
    finally:
        _cleanup(db)


def _prepare_task(db, *, question_count: int = 2) -> AssessmentTask:
    now = datetime.now(timezone.utc)
    db.add(
        AssessmentPaper(
            id=PAPER_ID,
            tenant_id=TENANT_ID,
            org_id=ORG_ID,
            paper_code="ATTEMPT-PAPER",
            version_no=1,
            name="Attempt 测试试卷",
            paper_type="fixed",
            target_level="3",
            duration_min=60,
            total_score=100,
            rules={"version": "v1"},
            status="published",
            published_at=now.replace(tzinfo=None),
        )
    )
    db.flush()
    for index in range(question_count):
        question_id = QUESTION_ID_1 if index == 0 else QUESTION_ID_2
        row_id = QUESTION_ROW_1 if index == 0 else QUESTION_ROW_2
        db.add(
            AssessmentPaperQuestion(
                id=row_id,
                paper_id=PAPER_ID,
                question_id=question_id,
                question_version=2,
                question_snapshot={
                    "question_type": "single",
                    "question_content": f"题目 {index + 1}",
                    "content": f"题目 {index + 1}",
                    "options": [{"code": "A", "text": "选项 A", "order": 1}],
                },
                seq_no=index + 1,
                score=50,
                dimension_snapshot={"dimension_code": "logic"},
                answer_rule_snapshot={"answer": "A"},
            )
        )
    db.flush()
    created = AssessmentTaskService.create_task(
        db, _principal(), _task_request(), idempotency_key="attempt-task-create"
    )
    return AssessmentTaskService.activate_task(
        db,
        _principal(),
        int(created.id),
        row_version=created.row_version,
        idempotency_key="attempt-task-activate",
    ) and db.get(AssessmentTask, int(created.id))


@requires_db
def test_api_131_creates_attempt_and_freezes_paper_snapshot(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    candidate_before = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    deadline_before = candidate_before.deadline_at

    result = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-1"
    )

    assert result.status == "in_progress"
    assert result.answer_version == 0
    assert result.attempt_no == 1
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    attempt = attempt_db.get(AssessmentAttempt, int(result.attempt_id))
    assert candidate.assignment_status == "started"
    assert candidate.attempt_count == 1
    assert candidate.progress == 0
    assert candidate.deadline_at == deadline_before
    assert attempt.paper_snapshot[0]["question_id"] == str(QUESTION_ID_1)


@requires_db
def test_api_131_reuses_in_progress_attempt_without_incrementing_count(attempt_db) -> None:
    task = _prepare_task(attempt_db)

    first = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-2"
    )
    second = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-3"
    )

    assert second.attempt_id == first.attempt_id
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    assert candidate.attempt_count == 1


@requires_db
def test_api_132_and_133_read_attempt_snapshot_with_saved_answer(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-4"
    )

    detail = AssessmentAttemptService.get_attempt_detail(
        attempt_db, _principal(), int(started.attempt_id)
    )
    questions = AssessmentAttemptService.list_attempt_questions(
        attempt_db, _principal(), int(started.attempt_id), page_no=1, page_size=10
    )

    assert detail.task_id == task.id
    assert detail.progress == 0
    assert questions.total == 2
    assert questions.items[0].content == "题目 1"
    assert questions.items[0].answer_summary.answered is False


@requires_db
def test_api_134_increments_version_and_overwrites_current_answer(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-5"
    )

    first = AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("answer-save-1"),
    )
    second = AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "B"},
        answer_version=1,
        idempotency_key=_key("answer-save-2"),
    )

    assert first.answer_version == 1
    assert second.answer_version == 2
    assert second.progress == 50
    answers = list(attempt_db.scalars(select(AssessmentAnswer)))
    assert len(answers) == 1
    assert answers[0].answer == {"value": "B"}
    assert answers[0].answer_version == 2


@requires_db
def test_api_134_rejects_stale_answer_version(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-6"
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("answer-save-3"),
    )

    with pytest.raises(BizError) as excinfo:
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            QUESTION_ROW_1,
            answer={"value": "B"},
            answer_version=0,
            idempotency_key=_key("answer-save-4"),
        )
    assert excinfo.value.code == int(ErrorCode.CONCURRENT_CONFLICT)


@requires_db
def test_api_131_enforces_attempt_limit_and_generates_next_attempt_no(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    first = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-limit-1"
    )
    attempt = attempt_db.get(AssessmentAttempt, int(first.attempt_id))
    attempt.status = "submitted"
    attempt_db.commit()
    second = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-limit-2"
    )
    assert second.attempt_no == 2

    second_attempt = attempt_db.get(AssessmentAttempt, int(second.attempt_id))
    second_attempt.status = "submitted"
    attempt_db.commit()
    with pytest.raises(IllegalStateError):
        AssessmentAttemptService.start_attempt(
            attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-limit-3"
        )


@requires_db
def test_api_133_reads_immutable_snapshot_after_paper_question_changes(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-snapshot"
    )
    question = attempt_db.get(AssessmentPaperQuestion, QUESTION_ROW_1)
    question.question_snapshot = {
        "question_type": "single",
        "question_content": "实时变更题目",
        "content": "实时变更题目",
        "options": [],
    }
    attempt_db.commit()

    result = AssessmentAttemptService.list_attempt_questions(
        attempt_db, _principal(), int(started.attempt_id), page_no=1, page_size=10
    )
    assert result.items[0].content == "题目 1"


@requires_db
def test_api_132_does_not_read_another_users_attempt(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-owner"
    )

    with pytest.raises(NotFoundError):
        AssessmentAttemptService.get_attempt_detail(
            attempt_db, _principal(user_id=USER_ID + 1), int(started.attempt_id)
        )


@requires_db
def test_api_134_replays_same_idempotency_key_without_second_version(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-replay"
    )
    first = AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=(_replay_key := _key("answer-replay")),
    )

    with pytest.raises(BizError) as excinfo:
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            QUESTION_ROW_1,
            answer={"value": "A"},
            answer_version=0,
            idempotency_key=_replay_key,
        )
    assert excinfo.value.code == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert str(first.answer_version) == str(excinfo.value.data["answer_version"])


@requires_db
def test_api_134_rejects_non_in_progress_attempt(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key="attempt-start-terminal"
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.status = "cancelled"
    attempt_db.commit()

    with pytest.raises(IllegalStateError):
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            QUESTION_ROW_1,
            answer={"value": "A"},
            answer_version=0,
            idempotency_key="answer-terminal",
        )


@requires_db
def test_attempt_http_routes_use_frozen_paths_and_get_without_idempotency_key(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    app = create_app()

    def _db_override():
        yield attempt_db

    from app.core.security import get_current_principal

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_principal] = lambda: _principal(roles=("ADM",))
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/assessment/attempts",
            headers={"Idempotency-Key": _key("http-start")},
            json={"task_id": str(task.id)},
        )
        assert started.status_code == 200
        attempt_id = started.json()["data"]["attempt_id"]

        detail = client.get(f"/api/v1/assessment/attempts/{attempt_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["progress"] == 0

        reminders = client.get(f"/api/v1/assessment/attempts/{attempt_id}/reminders")
        assert reminders.status_code == 200
        assert reminders.json()["data"]["attempt_id"] == attempt_id
        assert "created_at" not in reminders.json()["data"]["notifications"][0]

        progress = client.get(f"/api/v1/assessment/attempts/{attempt_id}/progress")
        assert progress.status_code == 200
        assert progress.json()["data"]["current_question_id"] == str(QUESTION_ID_1)

        questions = client.get(
            f"/api/v1/assessment/attempts/{attempt_id}/questions",
            params={"page_no": 1, "page_size": 1},
        )
        assert questions.status_code == 200
        assert questions.json()["page"] == {"page_no": 1, "page_size": 1, "total": 2}

        saved = client.put(
            f"/api/v1/assessment/attempts/{attempt_id}/answers/{QUESTION_ROW_1}",
            headers={"Idempotency-Key": _key("http-answer")},
            json={"answer": {"value": "A"}, "answer_version": 0},
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["answer_version"] == 1

        event = client.post(
            f"/api/v1/assessment/attempts/{attempt_id}/events",
            headers={"Idempotency-Key": _key("http-event")},
            json={
                "event_type": "refresh",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "metadata_summary": {"page": "question"},
                "client_event_id": "http-client-event-1",
            },
        )
        assert event.status_code == 200
        assert set(event.json()["data"]) == {"event_id"}


@requires_db
def test_api_131_concurrent_start_requests_share_one_attempt(attempt_db) -> None:
    task = _prepare_task(attempt_db)

    def _start(index: int) -> int:
        session = SessionLocal()
        try:
            result = AssessmentAttemptService.start_attempt(
                session,
                _principal(),
                int(task.id),
                idempotency_key=_key(f"concurrent-start-{index}"),
            )
            return int(result.attempt_id)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempt_ids = list(pool.map(_start, (1, 2)))

    assert attempt_ids[0] == attempt_ids[1]
    attempt_db.rollback()
    attempt_db.expire_all()
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    assert candidate.attempt_count == 1


@requires_db
def test_api_134_concurrent_same_version_has_one_success(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("concurrent-answer-start")
    )

    def _save(index: int) -> int:
        session = SessionLocal()
        try:
            result = AssessmentAttemptService.save_answer(
                session,
                _principal(),
                int(started.attempt_id),
                QUESTION_ROW_1,
                answer={"value": "A"},
                answer_version=0,
                idempotency_key=_key(f"concurrent-answer-{index}"),
            )
            return int(result.answer_version)
        except BizError as exc:
            return -int(exc.code)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(_save, (1, 2)))

    assert sorted(versions) == [-int(ErrorCode.CONCURRENT_CONFLICT), 1]


@requires_db
def test_api_137_reports_unanswered_questions_without_side_effects(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-check-unanswered")
    )

    result = AssessmentAttemptService.check_submission(
        attempt_db, _principal(), int(started.attempt_id)
    )

    assert result.can_submit is False
    assert result.unanswered_count == 2
    assert [(item.question_id, item.question_order) for item in result.unanswered_questions] == [
        (QUESTION_ID_1, 1),
        (QUESTION_ID_2, 2),
    ]
    assert result.timeout is False
    assert "存在未作答题目" in result.blocking_reasons
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    assert attempt.status == "in_progress"


@requires_db
def test_api_137_allows_submission_when_all_answers_are_saved(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-check-ready")
    )
    for question_id, value, version in (
        (QUESTION_ROW_1, "A", 0),
        (QUESTION_ROW_2, "B", 1),
    ):
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            question_id,
            answer={"value": value},
            answer_version=version,
            idempotency_key=_key("submit-check-answer"),
        )

    result = AssessmentAttemptService.check_submission(
        attempt_db, _principal(), int(started.attempt_id)
    )

    assert result.can_submit is True
    assert result.unanswered_count == 0
    assert result.unanswered_questions == []
    assert result.timeout is False
    assert result.blocking_reasons == []


@requires_db
def test_api_137_rejects_terminal_attempt(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-check-terminal")
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.status = "submitted"
    attempt_db.commit()

    with pytest.raises(IllegalStateError):
        AssessmentAttemptService.check_submission(
            attempt_db, _principal(), int(started.attempt_id)
        )


@requires_db
def test_api_137_reports_timeout_without_transitioning_attempt(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-check-timeout")
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.deadline_at_snapshot = datetime.now() - timedelta(minutes=1)
    attempt_db.commit()

    result = AssessmentAttemptService.check_submission(
        attempt_db, _principal(), int(started.attempt_id)
    )

    assert result.timeout is True
    assert result.can_submit is False
    assert "已超过作答截止时间" in result.blocking_reasons
    attempt_db.refresh(attempt)
    assert attempt.status == "in_progress"


@requires_db
def test_api_135_lists_attempt_notifications_without_created_at(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("reminders-list")
    )

    result = AssessmentAttemptService.list_attempt_reminders(
        attempt_db, _principal(), int(started.attempt_id)
    )

    assert result.attempt_id == started.attempt_id
    assert [item.notification_type for item in result.notifications] == ["task_created"]
    assert result.notifications[0].status == "pending"
    assert "created_at" not in result.notifications[0].model_dump()


@requires_db
def test_api_135_returns_empty_notifications_for_attempt_without_notifications(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    attempt_db.execute(
        delete(AssessmentNotification).where(AssessmentNotification.tenant_id == TENANT_ID)
    )
    attempt_db.commit()
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("reminders-empty")
    )

    result = AssessmentAttemptService.list_attempt_reminders(
        attempt_db, _principal(), int(started.attempt_id)
    )

    assert result.notifications == []


@requires_db
def test_api_135_rejects_attempt_outside_data_scope(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("reminders-scope")
    )

    with pytest.raises(NotFoundError):
        AssessmentAttemptService.list_attempt_reminders(
            attempt_db, _principal(user_id=USER_ID + 1), int(started.attempt_id)
        )


@requires_db
def test_api_136_calculates_progress_and_current_question_from_snapshot(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("progress-read")
    )

    initial = AssessmentAttemptService.get_attempt_progress(
        attempt_db, _principal(), int(started.attempt_id)
    )
    assert initial.total_questions == 2
    assert initial.answered_count == 0
    assert initial.unanswered_count == 2
    assert initial.progress == 0
    assert initial.current_question_id == QUESTION_ID_1
    assert initial.last_saved_at is None

    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("progress-save-1"),
    )
    partial = AssessmentAttemptService.get_attempt_progress(
        attempt_db, _principal(), int(started.attempt_id)
    )
    assert partial.answered_count == 1
    assert partial.unanswered_count == 1
    assert partial.progress == 50
    assert partial.current_question_id == QUESTION_ID_2
    assert partial.last_saved_at is not None

    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_2,
        answer={"value": "B"},
        answer_version=1,
        idempotency_key=_key("progress-save-2"),
    )
    complete = AssessmentAttemptService.get_attempt_progress(
        attempt_db, _principal(), int(started.attempt_id)
    )
    assert complete.answered_count == 2
    assert complete.unanswered_count == 0
    assert complete.progress == 100
    assert complete.current_question_id is None


@requires_db
def test_api_136_rejects_attempt_outside_data_scope(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("progress-scope")
    )

    with pytest.raises(NotFoundError):
        AssessmentAttemptService.get_attempt_progress(
            attempt_db, _principal(user_id=USER_ID + 1), int(started.attempt_id)
        )


@requires_db
def test_phase2_normal_attempt_lifecycle_reaches_score_outbox(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("lifecycle-start")
    )
    snapshot = AssessmentAttemptService.list_attempt_questions(
        attempt_db, _principal(), int(started.attempt_id), page_no=1, page_size=10
    )
    assert snapshot.total == 2

    for question_id, value, version in (
        (QUESTION_ROW_1, "A", 0),
        (QUESTION_ROW_2, "B", 1),
    ):
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            question_id,
            answer={"value": value},
            answer_version=version,
            idempotency_key=_key("lifecycle-answer"),
        )

    progress = AssessmentAttemptService.get_attempt_progress(
        attempt_db, _principal(), int(started.attempt_id)
    )
    check = AssessmentAttemptService.check_submission(
        attempt_db, _principal(), int(started.attempt_id)
    )
    result = AssessmentAttemptService.submit_attempt(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        confirm=True,
        idempotency_key=_key("lifecycle-submit"),
    )

    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    assert progress.progress == 100
    assert progress.current_question_id is None
    assert check.can_submit is True
    assert result.status == "submitted"
    assert candidate.assignment_status == "submitted"
    assert candidate.progress == 100
    assert attempt_db.query(OutboxEvent).filter(
        OutboxEvent.aggregate_id == int(started.attempt_id),
        OutboxEvent.event_type == "assessment.score.requested",
    ).count() == 1


@requires_db
def test_api_139_creates_event_with_server_fields(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("event-start")
    )

    result = AssessmentAttemptService.create_event(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        event_type="visibility_hidden",
        occurred_at=datetime.now(timezone.utc),
        duration_ms=1500,
        metadata_summary={"tab": "assessment"},
        client_event_id="client-event-1",
        idempotency_key=_key("event-create"),
    )

    event = attempt_db.get(AssessmentEvent, int(result.event_id))
    assert event is not None
    assert event.severity == "medium"
    assert event.review_status == "unreviewed"
    assert event.received_at is not None
    assert result.model_dump(mode="json") == {"event_id": str(event.id)}


@requires_db
def test_api_139_reuses_event_for_same_client_event_id_with_different_http_key(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("event-replay-start")
    )
    payload = {
        "event_type": "refresh",
        "occurred_at": datetime.now(timezone.utc),
        "duration_ms": None,
        "metadata_summary": {"page": "question"},
        "client_event_id": "client-event-replay",
    }
    first = AssessmentAttemptService.create_event(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        **payload,
        idempotency_key=_key("event-replay-http-1"),
    )
    second = AssessmentAttemptService.create_event(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        **payload,
        idempotency_key=_key("event-replay-http-2"),
    )

    assert second.event_id == first.event_id
    assert attempt_db.query(AssessmentEvent).count() == 1


@requires_db
def test_api_139_replays_same_idempotency_key(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("event-idem-start")
    )
    key = _key("event-idem")
    kwargs = {
        "event_type": "paste",
        "occurred_at": datetime.now(timezone.utc),
        "duration_ms": 10,
        "metadata_summary": {"source": "keyboard"},
        "client_event_id": "client-event-idem",
    }
    first = AssessmentAttemptService.create_event(
        attempt_db, _principal(), int(started.attempt_id), **kwargs, idempotency_key=key
    )

    with pytest.raises(BizError) as excinfo:
        AssessmentAttemptService.create_event(
            attempt_db, _principal(), int(started.attempt_id), **kwargs, idempotency_key=key
        )
    assert excinfo.value.code == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert excinfo.value.data == first.model_dump(mode="json")


@requires_db
def test_api_139_rejects_attempt_outside_data_scope(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("event-scope-start")
    )

    with pytest.raises(NotFoundError):
        AssessmentAttemptService.create_event(
            attempt_db,
            _principal(user_id=USER_ID + 1),
            int(started.attempt_id),
            event_type="idle",
            occurred_at=datetime.now(timezone.utc),
            duration_ms=None,
            metadata_summary={},
            client_event_id="client-event-scope",
            idempotency_key=_key("event-scope"),
        )


@requires_db
def test_auto_submit_worker_transitions_expired_attempt_and_writes_outbox(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("worker-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("worker-answer"),
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.deadline_at_snapshot = datetime.now() - timedelta(minutes=1)
    attempt_db.commit()

    processed = run_once(attempt_db, server_now=datetime.now())

    assert processed == 1
    attempt_db.refresh(attempt)
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    outbox = attempt_db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == int(started.attempt_id),
            OutboxEvent.event_type == "assessment.score.requested",
        )
    )
    assert attempt.status == "auto_submitted"
    assert attempt.submitted_at is not None
    assert candidate.assignment_status == "submitted"
    assert candidate.progress == 50
    assert outbox is not None
    assert outbox.idempotency_key == f"{started.attempt_id}:score:v1"


@requires_db
def test_auto_submit_worker_is_noop_before_deadline_and_after_completion(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("worker-noop-start")
    )
    future = datetime.now() + timedelta(minutes=5)
    first = run_once(attempt_db)
    assert first == 0

    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.deadline_at_snapshot = datetime.now() - timedelta(minutes=1)
    attempt_db.commit()
    assert run_once(attempt_db, server_now=datetime.now()) == 1
    assert run_once(attempt_db, server_now=future) == 0
    assert attempt_db.query(OutboxEvent).filter(
        OutboxEvent.aggregate_id == int(started.attempt_id),
        OutboxEvent.event_type == "assessment.score.requested",
    ).count() == 1


@requires_db
def test_auto_submit_worker_and_second_worker_only_finalize_once(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("worker-race-start")
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.deadline_at_snapshot = datetime.now() - timedelta(minutes=1)
    attempt_db.commit()

    def _run_worker() -> int:
        session = SessionLocal()
        try:
            return run_once(session, server_now=datetime.now())
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        processed = list(pool.map(lambda _: _run_worker(), (1, 2)))

    assert sorted(processed) == [0, 1]
    assert attempt_db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == int(started.attempt_id),
            OutboxEvent.event_type == "assessment.score.requested",
        )
    ) is not None
    assert attempt_db.query(OutboxEvent).filter(
        OutboxEvent.aggregate_id == int(started.attempt_id),
        OutboxEvent.event_type == "assessment.score.requested",
    ).count() == 1


@requires_db
def test_api_138_and_auto_submit_worker_race_has_one_finalization(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("api138-worker-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("api138-worker-answer"),
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.deadline_at_snapshot = datetime.now() + timedelta(hours=1)
    attempt_db.commit()
    worker_now = attempt.deadline_at_snapshot + timedelta(seconds=1)
    barrier = Barrier(2)

    def _submit() -> str:
        session = SessionLocal()
        try:
            barrier.wait()
            result = AssessmentAttemptService.submit_attempt(
                session,
                _principal(),
                int(started.attempt_id),
                confirm=True,
                idempotency_key=_key("api138-worker-submit"),
            )
            return f"api:{result.status}"
        except BizError as exc:
            return f"api-error:{exc.code}"
        finally:
            session.close()

    def _worker() -> str:
        session = SessionLocal()
        try:
            barrier.wait()
            return f"worker:{run_once(session, server_now=worker_now)}"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        api_result, worker_result = list(pool.map(lambda fn: fn(), (_submit, _worker)))

    assert api_result in {f"api:submitted", f"api-error:{int(ErrorCode.CONCURRENT_CONFLICT)}"}
    assert worker_result in {"worker:0", "worker:1"}
    assert attempt_db.query(OutboxEvent).filter(
        OutboxEvent.aggregate_id == int(started.attempt_id),
        OutboxEvent.event_type == "assessment.score.requested",
    ).count() == 1
    attempt_db.expire_all()
    final_attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    assert final_attempt.status in {"submitted", "auto_submitted"}


@requires_db
def test_api_138_submits_attempt_and_writes_score_outbox(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-start")
    )
    for question_id, value, version in (
        (QUESTION_ROW_1, "A", 0),
        (QUESTION_ROW_2, "B", 1),
    ):
        AssessmentAttemptService.save_answer(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            question_id,
            answer={"value": value},
            answer_version=version,
            idempotency_key=_key("submit-answer"),
        )

    result = AssessmentAttemptService.submit_attempt(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        confirm=True,
        idempotency_key=_key("submit-success"),
    )

    assert result.status == "submitted"
    assert str(result.attempt_id) == str(started.attempt_id)
    assert result.outbox_event_id
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    outbox = attempt_db.get(OutboxEvent, int(result.outbox_event_id))
    assert attempt.status == "submitted"
    assert attempt.submitted_at is not None
    assert candidate.assignment_status == "submitted"
    assert candidate.progress == 100
    assert outbox.event_type == "assessment.score.requested"
    assert outbox.aggregate_type == "assessment_attempt"
    assert outbox.aggregate_id == int(started.attempt_id)
    assert outbox.payload == {
        "attempt_id": str(started.attempt_id),
        "task_id": str(task.id),
        "tenant_id": str(TENANT_ID),
        "answer_version": 2,
    }
    assert outbox.idempotency_key == f"{started.attempt_id}:score:v2"


@requires_db
def test_api_138_same_idempotency_key_returns_first_result(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-replay-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("submit-replay-answer"),
    )
    key = _key("submit-replay")
    first = AssessmentAttemptService.submit_attempt(
        attempt_db, _principal(), int(started.attempt_id), confirm=True, idempotency_key=key
    )

    with pytest.raises(BizError) as exc_info:
        AssessmentAttemptService.submit_attempt(
            attempt_db, _principal(), int(started.attempt_id), confirm=True, idempotency_key=key
        )

    assert exc_info.value.code == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert exc_info.value.data == first.model_dump(mode="json")
    assert attempt_db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == int(started.attempt_id),
            OutboxEvent.event_type == "assessment.score.requested",
        )
    ) is not None


@requires_db
def test_api_138_different_key_on_submitted_attempt_returns_40902(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-conflict-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("submit-conflict-answer"),
    )
    AssessmentAttemptService.submit_attempt(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        confirm=True,
        idempotency_key=_key("submit-conflict-first"),
    )

    with pytest.raises(BizError) as exc_info:
        AssessmentAttemptService.submit_attempt(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            confirm=True,
            idempotency_key=_key("submit-conflict-second"),
        )

    assert exc_info.value.code == int(ErrorCode.CONCURRENT_CONFLICT)


@requires_db
def test_api_138_outbox_failure_rolls_back_submission(attempt_db, monkeypatch) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-rollback-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("submit-rollback-answer"),
    )
    candidate_before = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    progress_before = candidate_before.progress

    def fail_outbox(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("app.services.assessment_attempt_service.assessment_dao.insert_outbox", fail_outbox)
    with pytest.raises(RuntimeError):
        AssessmentAttemptService.submit_attempt(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            confirm=True,
            idempotency_key=_key("submit-rollback"),
        )

    attempt_db.expire_all()
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    candidate = attempt_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(task.id))
    )
    assert attempt.status == "in_progress"
    assert attempt.submitted_at is None
    assert candidate.assignment_status == "started"
    assert candidate.progress == progress_before


@requires_db
def test_api_138_rejects_confirm_false_without_side_effect(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-confirm-start")
    )

    with pytest.raises(IllegalStateError) as exc_info:
        AssessmentAttemptService.submit_attempt(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            confirm=False,
            idempotency_key=_key("submit-confirm-false"),
        )

    assert exc_info.value.code == int(ErrorCode.ILLEGAL_STATE)
    attempt_db.expire_all()
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    assert attempt.status == "in_progress"


@requires_db
def test_api_138_auto_submitted_attempt_returns_40902(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-auto-start")
    )
    attempt = attempt_db.get(AssessmentAttempt, int(started.attempt_id))
    attempt.status = "auto_submitted"
    attempt_db.commit()

    with pytest.raises(BizError) as exc_info:
        AssessmentAttemptService.submit_attempt(
            attempt_db,
            _principal(),
            int(started.attempt_id),
            confirm=True,
            idempotency_key=_key("submit-auto"),
        )

    assert exc_info.value.code == int(ErrorCode.CONCURRENT_CONFLICT)


@requires_db
def test_api_138_concurrent_different_keys_has_one_success(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-concurrent-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("submit-concurrent-answer"),
    )

    def _submit(index: int) -> int:
        session = SessionLocal()
        try:
            result = AssessmentAttemptService.submit_attempt(
                session,
                _principal(),
                int(started.attempt_id),
                confirm=True,
                idempotency_key=_key(f"submit-concurrent-{index}"),
            )
            return int(result.attempt_id)
        except BizError as exc:
            return -int(exc.code)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_submit, (1, 2)))

    assert sorted(results) == [-int(ErrorCode.CONCURRENT_CONFLICT), int(started.attempt_id)]
    assert attempt_db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == int(started.attempt_id),
            OutboxEvent.event_type == "assessment.score.requested",
        )
    ) is not None


@requires_db
def test_api_138_http_route_returns_frozen_response_and_requires_key(attempt_db) -> None:
    task = _prepare_task(attempt_db, question_count=1)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-http-start")
    )
    AssessmentAttemptService.save_answer(
        attempt_db,
        _principal(),
        int(started.attempt_id),
        QUESTION_ROW_1,
        answer={"value": "A"},
        answer_version=0,
        idempotency_key=_key("submit-http-answer"),
    )
    app = create_app()

    def _db_override():
        yield attempt_db

    from app.core.security import get_current_principal

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_principal] = lambda: _principal(roles=("ADM",))
    with TestClient(app) as client:
        missing_key = client.post(
            f"/api/v1/assessment/attempts/{started.attempt_id}/submit",
            json={"confirm": True},
        )
        response = client.post(
            f"/api/v1/assessment/attempts/{started.attempt_id}/submit",
            json={"confirm": True},
            headers={"Idempotency-Key": _key("submit-http-success")},
        )

    assert missing_key.status_code == 400
    assert missing_key.json()["code"] == int(ErrorCode.PARAM_INVALID)
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "submitted"
    assert set(response.json()["data"]) == {
        "attempt_id",
        "status",
        "submitted_at",
        "outbox_event_id",
    }


@requires_db
def test_api_137_http_route_returns_frozen_response(attempt_db) -> None:
    task = _prepare_task(attempt_db)
    started = AssessmentAttemptService.start_attempt(
        attempt_db, _principal(), int(task.id), idempotency_key=_key("submit-check-http")
    )
    app = create_app()

    def _db_override():
        yield attempt_db

    from app.core.security import get_current_principal

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_principal] = lambda: _principal(roles=("ADM",))
    with TestClient(app) as client:
        response = client.post(f"/api/v1/assessment/attempts/{started.attempt_id}/submit/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["can_submit"] is False
    assert payload["data"]["unanswered_count"] == 2
    assert payload["data"]["unanswered_questions"] == [
        {"question_id": str(QUESTION_ID_1), "question_order": 1},
        {"question_id": str(QUESTION_ID_2), "question_order": 2},
    ]
    assert payload["data"]["timeout"] is False
