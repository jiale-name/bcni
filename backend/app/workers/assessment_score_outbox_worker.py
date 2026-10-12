"""Single-round consumer for assessment score-request Outbox events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.tenant import CurrentPrincipal
from app.dao import assessment_dao
from app.models.enums import RoleDataScope
from app.schemas.agent import AgentTaskType
from app.services.agent_service import AgentService
from app.services.assessment_attempt_service import _server_now


SCORE_REQUESTED = "assessment.score.requested"


def _database_time(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _required_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if value is None or isinstance(value, bool):
        raise ValueError(f"评分事件缺少 {field}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"评分事件 {field} 非法") from exc
    if number < 0:
        raise ValueError(f"评分事件 {field} 非法")
    return number


def _score_input(event) -> tuple[int, int, int, int, str]:
    payload = event.payload
    if not isinstance(payload, dict):
        raise ValueError("评分事件 payload 必须是对象")

    attempt_id = _required_int(payload, "attempt_id")
    task_id = _required_int(payload, "task_id")
    tenant_id = _required_int(payload, "tenant_id")
    answer_version = _required_int(payload, "answer_version")
    if tenant_id != int(event.tenant_id):
        raise ValueError("评分事件 tenant_id 与 Outbox 租户不一致")

    score_business_key = f"{attempt_id}:score:v{answer_version}"
    if event.idempotency_key != score_business_key:
        raise ValueError("评分事件幂等键与 payload 不一致")
    return tenant_id, attempt_id, task_id, answer_version, score_business_key


def _mark_retry(db: Session, event_id: int, server_now: datetime) -> None:
    event = assessment_dao.get_outbox_event(db, event_id, for_update=True)
    if event is None or event.status != "pending":
        db.rollback()
        return
    event.retry_count = int(event.retry_count) + 1
    # The scheduler controls retry cadence; no stage-3A retry limit is introduced here.
    event.next_attempt_at = server_now
    db.commit()


def run_once(db: Session, *, server_now: datetime | None = None) -> int:
    """Consume one due ``assessment.score.requested`` event.

    Task creation and event publication share one transaction.  Failures leave
    the event pending, increment its existing retry counter, and make it due
    for a later worker invocation.
    """
    now = _database_time(server_now or _server_now())
    event = assessment_dao.get_due_outbox_event(
        db,
        event_type=SCORE_REQUESTED,
        server_now=now,
        for_update=True,
    )
    if event is None:
        return 0

    event_id = int(event.id)
    try:
        tenant_id, attempt_id, task_id, answer_version, score_business_key = _score_input(event)
        agent_business_key = f"assessment_attempt:{attempt_id}"
        AgentService.create_pending_task(
            db,
            CurrentPrincipal(
                user_id=0,
                tenant_id=tenant_id,
                org_id=None,
                roles=("AIOPS",),
                data_scope=int(RoleDataScope.ALL),
            ),
            task_type=AgentTaskType.SCORE,
            biz_id=attempt_id,
            idempotent_key=agent_business_key,
            state={
                "tenant_id": tenant_id,
                "attempt_id": attempt_id,
                "task_id": task_id,
                "answer_version": answer_version,
                "agent_business_key": agent_business_key,
                "score_business_idempotency_key": score_business_key,
            },
        )
        event.status = "published"
        event.published_at = now
        event.next_attempt_at = None
        db.commit()
        return 1
    except Exception:
        db.rollback()
        _mark_retry(db, event_id, now)
        return 0
