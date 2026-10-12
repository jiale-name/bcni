"""Stage 3A score-request Outbox consumer tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from sqlalchemy import delete, select

from app.core.tenant import CurrentPrincipal
from app.dao.session import SessionLocal
from app.models.agent import AgentTask
from app.models.assessment import OutboxEvent
from app.models.enums import AgentTaskStatus, RoleDataScope
from app.schemas.agent import AgentTaskType
from app.services.agent_service import AgentService
from app.workers.assessment_score_outbox_worker import run_once
from tests.conftest import requires_db


TENANT_ID = 7920000000000000001
ATTEMPT_ID = 7920000000000000101
TASK_ID = 7920000000000000201
ANSWER_VERSION = 8


def _principal() -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=0,
        tenant_id=TENANT_ID,
        org_id=None,
        roles=("AIOPS",),
        data_scope=int(RoleDataScope.ALL),
    )


def _score_business_key(*, attempt_id: int = ATTEMPT_ID, answer_version: int = ANSWER_VERSION) -> str:
    return f"{attempt_id}:score:v{answer_version}"


def _agent_key(*, attempt_id: int = ATTEMPT_ID) -> str:
    return f"assessment_attempt:{attempt_id}"


def _score_payload(
    *,
    attempt_id: int = ATTEMPT_ID,
    task_id: int = TASK_ID,
    tenant_id: int = TENANT_ID,
    answer_version: int = ANSWER_VERSION,
) -> dict[str, str | int]:
    return {
        "attempt_id": str(attempt_id),
        "task_id": str(task_id),
        "tenant_id": str(tenant_id),
        "answer_version": answer_version,
    }


def _outbox(
    db,
    *,
    event_type: str = "assessment.score.requested",
    payload: dict | None = None,
    attempt_id: int = ATTEMPT_ID,
    answer_version: int = ANSWER_VERSION,
) -> OutboxEvent:
    event = OutboxEvent(
        tenant_id=TENANT_ID,
        aggregate_type="assessment_attempt",
        aggregate_id=attempt_id,
        event_type=event_type,
        payload=payload if payload is not None else _score_payload(
            attempt_id=attempt_id, answer_version=answer_version
        ),
        idempotency_key=_score_business_key(
            attempt_id=attempt_id, answer_version=answer_version
        ),
        status="pending",
    )
    db.add(event)
    db.commit()
    return event


@pytest.fixture
def score_outbox_db(db):
    db.execute(delete(OutboxEvent).where(OutboxEvent.tenant_id == TENANT_ID))
    db.execute(delete(AgentTask).where(AgentTask.tenant_id == TENANT_ID))
    db.commit()
    try:
        yield db
    finally:
        db.execute(delete(OutboxEvent).where(OutboxEvent.tenant_id == TENANT_ID))
        db.execute(delete(AgentTask).where(AgentTask.tenant_id == TENANT_ID))
        db.commit()


@requires_db
def test_create_pending_task_creates_score_task_without_graph_execution(score_outbox_db, monkeypatch) -> None:
    def graph_must_not_run(*_args, **_kwargs):
        raise AssertionError("pending task creation must not resolve or run a graph")

    monkeypatch.setattr("app.services.agent_service.get_graph", graph_must_not_run)
    monkeypatch.setattr("app.services.agent_service.run_graph", graph_must_not_run)

    task, replayed = AgentService.create_pending_task(
        score_outbox_db,
        _principal(),
        task_type=AgentTaskType.SCORE,
        state={"attempt_id": ATTEMPT_ID, "task_id": TASK_ID, "answer_version": ANSWER_VERSION},
        biz_id=ATTEMPT_ID,
        idempotent_key=_agent_key(),
    )

    assert replayed is False
    assert task.task_type == AgentTaskType.SCORE
    assert task.status == int(AgentTaskStatus.PENDING)
    assert task.current_node is None
    assert task.idempotent_key == _agent_key()
    assert json.loads(task.graph_state or "{}") == {
        "attempt_id": ATTEMPT_ID,
        "task_id": TASK_ID,
        "answer_version": ANSWER_VERSION,
        "__task_id__": int(task.id),
        "__tenant_id__": TENANT_ID,
    }


@requires_db
def test_consumer_creates_pending_score_task_and_publishes_outbox(score_outbox_db) -> None:
    event = _outbox(score_outbox_db)

    assert run_once(score_outbox_db, server_now=datetime.now()) == 1

    score_outbox_db.expire_all()
    stored_event = score_outbox_db.get(OutboxEvent, int(event.id))
    task = score_outbox_db.scalar(
        select(AgentTask).where(AgentTask.tenant_id == TENANT_ID, AgentTask.idempotent_key == _agent_key())
    )
    assert stored_event.status == "published"
    assert stored_event.published_at is not None
    assert stored_event.next_attempt_at is None
    assert task is not None
    assert task.task_type == AgentTaskType.SCORE
    assert task.status == int(AgentTaskStatus.PENDING)
    assert task.biz_id == ATTEMPT_ID
    state = json.loads(task.graph_state or "{}")
    assert state["tenant_id"] == TENANT_ID
    assert state["attempt_id"] == ATTEMPT_ID
    assert state["task_id"] == TASK_ID
    assert state["answer_version"] == ANSWER_VERSION
    assert state["agent_business_key"] == _agent_key()
    assert state["score_business_idempotency_key"] == _score_business_key()


@requires_db
def test_consumer_duplicate_delivery_does_not_create_second_agent_task(score_outbox_db) -> None:
    _outbox(score_outbox_db)

    assert run_once(score_outbox_db, server_now=datetime.now()) == 1
    assert run_once(score_outbox_db, server_now=datetime.now()) == 0
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 1


@requires_db
def test_consumer_concurrent_delivery_creates_one_agent_task(score_outbox_db) -> None:
    _outbox(score_outbox_db)
    barrier = Barrier(2)

    def consume() -> int:
        session = SessionLocal()
        try:
            barrier.wait()
            return run_once(session, server_now=datetime.now())
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: consume(), (1, 2)))

    assert sorted(outcomes) == [0, 1]
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 1


@requires_db
def test_consumer_creation_failure_keeps_outbox_pending_for_retry(score_outbox_db, monkeypatch) -> None:
    event = _outbox(score_outbox_db)

    def create_failure(*_args, **_kwargs):
        raise RuntimeError("agent task storage unavailable")

    monkeypatch.setattr(
        "app.workers.assessment_score_outbox_worker.AgentService.create_pending_task", create_failure
    )

    assert run_once(score_outbox_db, server_now=datetime.now()) == 0

    score_outbox_db.expire_all()
    stored_event = score_outbox_db.get(OutboxEvent, int(event.id))
    assert stored_event.status == "pending"
    assert stored_event.published_at is None
    assert stored_event.retry_count == 1
    assert stored_event.next_attempt_at is not None
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 0


@requires_db
def test_consumer_retry_can_succeed_after_transient_creation_failure(score_outbox_db, monkeypatch) -> None:
    event = _outbox(score_outbox_db)

    def create_failure(*_args, **_kwargs):
        raise RuntimeError("temporary storage failure")

    target = "app.workers.assessment_score_outbox_worker.AgentService.create_pending_task"
    with monkeypatch.context() as patch:
        patch.setattr(target, create_failure)
        assert run_once(score_outbox_db, server_now=datetime.now()) == 0

    score_outbox_db.expire_all()
    failed_event = score_outbox_db.get(OutboxEvent, int(event.id))
    assert failed_event.next_attempt_at is not None
    assert run_once(score_outbox_db, server_now=failed_event.next_attempt_at) == 1
    score_outbox_db.expire_all()
    assert score_outbox_db.get(OutboxEvent, int(event.id)).status == "published"
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 1


@requires_db
def test_consumer_ignores_non_score_outbox_events(score_outbox_db) -> None:
    event = _outbox(score_outbox_db, event_type="profile.sync.requested")

    assert run_once(score_outbox_db, server_now=datetime.now()) == 0
    score_outbox_db.expire_all()
    assert score_outbox_db.get(OutboxEvent, int(event.id)).status == "pending"
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 0


@requires_db
def test_consumer_rejects_payload_missing_required_field(score_outbox_db) -> None:
    payload = _score_payload()
    payload.pop("task_id")
    event = _outbox(score_outbox_db, payload=payload)

    assert run_once(score_outbox_db, server_now=datetime.now()) == 0
    score_outbox_db.expire_all()
    stored_event = score_outbox_db.get(OutboxEvent, int(event.id))
    assert stored_event.status == "pending"
    assert stored_event.retry_count == 1
    assert stored_event.published_at is None
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 0


@requires_db
def test_consumer_rejects_payload_with_wrong_tenant(score_outbox_db) -> None:
    event = _outbox(score_outbox_db, payload=_score_payload(tenant_id=TENANT_ID + 1))

    assert run_once(score_outbox_db, server_now=datetime.now()) == 0
    score_outbox_db.expire_all()
    assert score_outbox_db.get(OutboxEvent, int(event.id)).status == "pending"
    assert score_outbox_db.query(AgentTask).filter(AgentTask.tenant_id == TENANT_ID).count() == 0
