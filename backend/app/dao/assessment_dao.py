"""Pure data access for Assessment Task Batch 2A."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentEvent,
    AssessmentNotification,
    OutboxEvent,
    AssessmentPaper,
    AssessmentPaperQuestion,
    AssessmentTask,
    AssessmentTaskCandidate,
)


def get_paper(db: Session, tenant_id: int, paper_id: int) -> AssessmentPaper | None:
    stmt = select(AssessmentPaper).where(
        AssessmentPaper.tenant_id == int(tenant_id),
        AssessmentPaper.id == int(paper_id),
        AssessmentPaper.deleted_at.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_task(
    db: Session,
    tenant_id: int,
    task_id: int,
    *,
    scope_clause: Any | None = None,
    for_update: bool = False,
) -> AssessmentTask | None:
    conditions = [
        AssessmentTask.tenant_id == int(tenant_id),
        AssessmentTask.id == int(task_id),
        AssessmentTask.deleted_at.is_(None),
    ]
    if scope_clause is not None:
        conditions.append(scope_clause)
    stmt = select(AssessmentTask).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def get_task_by_code(db: Session, tenant_id: int, task_code: str) -> AssessmentTask | None:
    stmt = select(AssessmentTask).where(
        AssessmentTask.tenant_id == int(tenant_id),
        AssessmentTask.task_code == task_code,
        AssessmentTask.deleted_at.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()


def list_tasks(
    db: Session,
    tenant_id: int,
    *,
    scope_clause: Any,
    keyword: str | None = None,
    paper_id: int | None = None,
    position_level: int | None = None,
    status: str | None = None,
    limit: int,
    offset: int,
) -> list[tuple[AssessmentTask, AssessmentPaper]]:
    stmt: Select[tuple[AssessmentTask, AssessmentPaper]] = (
        select(AssessmentTask, AssessmentPaper)
        .join(
            AssessmentPaper,
            and_(
                AssessmentPaper.tenant_id == AssessmentTask.tenant_id,
                AssessmentPaper.id == AssessmentTask.paper_id,
                AssessmentPaper.deleted_at.is_(None),
            ),
        )
        .where(scope_clause, AssessmentTask.deleted_at.is_(None))
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            AssessmentTask.name.like(like)
            | AssessmentTask.task_code.like(like)
            | AssessmentPaper.paper_code.like(like)
        )
    if paper_id is not None:
        stmt = stmt.where(AssessmentTask.paper_id == int(paper_id))
    if position_level is not None:
        stmt = stmt.where(AssessmentPaper.target_level == str(int(position_level)))
    if status is not None:
        stmt = stmt.where(AssessmentTask.status == status)
    stmt = stmt.order_by(AssessmentTask.created_at.desc(), AssessmentTask.id.desc())
    stmt = stmt.limit(int(limit)).offset(int(offset))
    return list(db.execute(stmt).all())


def count_tasks(
    db: Session,
    tenant_id: int,
    *,
    scope_clause: Any,
    keyword: str | None = None,
    paper_id: int | None = None,
    position_level: int | None = None,
    status: str | None = None,
) -> int:
    stmt = (
        select(func.count(AssessmentTask.id))
        .select_from(AssessmentTask)
        .join(
            AssessmentPaper,
            and_(
                AssessmentPaper.tenant_id == AssessmentTask.tenant_id,
                AssessmentPaper.id == AssessmentTask.paper_id,
                AssessmentPaper.deleted_at.is_(None),
            ),
        )
        .where(scope_clause, AssessmentTask.deleted_at.is_(None))
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            AssessmentTask.name.like(like)
            | AssessmentTask.task_code.like(like)
            | AssessmentPaper.paper_code.like(like)
        )
    if paper_id is not None:
        stmt = stmt.where(AssessmentTask.paper_id == int(paper_id))
    if position_level is not None:
        stmt = stmt.where(AssessmentPaper.target_level == str(int(position_level)))
    if status is not None:
        stmt = stmt.where(AssessmentTask.status == status)
    return int(db.execute(stmt).scalar_one() or 0)


def insert_task(db: Session, task: AssessmentTask) -> AssessmentTask:
    db.add(task)
    db.flush()
    return task


def insert_candidates(db: Session, candidates: Sequence[AssessmentTaskCandidate]) -> None:
    if candidates:
        db.add_all(list(candidates))
        db.flush()


def list_candidates(
    db: Session,
    tenant_id: int,
    task_id: int,
    *,
    scope_clause: Any | None = None,
) -> list[AssessmentTaskCandidate]:
    conditions = [
        AssessmentTaskCandidate.tenant_id == int(tenant_id),
        AssessmentTaskCandidate.task_id == int(task_id),
    ]
    if scope_clause is not None:
        conditions.append(scope_clause)
    stmt = select(AssessmentTaskCandidate).where(*conditions).order_by(AssessmentTaskCandidate.id)
    return list(db.execute(stmt).scalars().all())


def list_candidates_by_ids(
    db: Session,
    tenant_id: int,
    task_id: int,
    candidate_ids: Sequence[int],
    *,
    scope_clause: Any | None = None,
) -> list[AssessmentTaskCandidate]:
    conditions = [
        AssessmentTaskCandidate.tenant_id == int(tenant_id),
        AssessmentTaskCandidate.task_id == int(task_id),
        AssessmentTaskCandidate.id.in_([int(value) for value in candidate_ids]),
    ]
    if scope_clause is not None:
        conditions.append(scope_clause)
    stmt = select(AssessmentTaskCandidate).where(*conditions).order_by(AssessmentTaskCandidate.id)
    return list(db.execute(stmt).scalars().all())


def insert_notifications(db: Session, notifications: Sequence[AssessmentNotification]) -> None:
    if notifications:
        db.add_all(list(notifications))
        db.flush()


def list_notifications(db: Session, tenant_id: int, task_id: int) -> list[AssessmentNotification]:
    stmt = (
        select(AssessmentNotification)
        .where(
            AssessmentNotification.tenant_id == int(tenant_id),
            AssessmentNotification.task_id == int(task_id),
        )
        .order_by(AssessmentNotification.id)
    )
    return list(db.execute(stmt).scalars().all())


def list_notifications_for_candidate(
    db: Session, tenant_id: int, task_id: int, task_candidate_id: int
) -> list[AssessmentNotification]:
    stmt = (
        select(AssessmentNotification)
        .where(
            AssessmentNotification.tenant_id == int(tenant_id),
            AssessmentNotification.task_id == int(task_id),
            AssessmentNotification.task_candidate_id == int(task_candidate_id),
        )
        .order_by(AssessmentNotification.id)
    )
    return list(db.execute(stmt).scalars().all())


def list_notifications_by_key_prefix(
    db: Session, tenant_id: int, prefix: str
) -> list[AssessmentNotification]:
    stmt = (
        select(AssessmentNotification)
        .where(
            AssessmentNotification.tenant_id == int(tenant_id),
            AssessmentNotification.idempotency_key.like(f"{prefix}%"),
        )
        .order_by(AssessmentNotification.id)
    )
    return list(db.execute(stmt).scalars().all())


def update_task_activation(
    db: Session,
    tenant_id: int,
    task_id: int,
    *,
    expected_row_version: int,
    status: str,
    updated_by: int,
) -> int:
    stmt = (
        update(AssessmentTask)
        .where(
            AssessmentTask.tenant_id == int(tenant_id),
            AssessmentTask.id == int(task_id),
            AssessmentTask.status == "draft",
            AssessmentTask.row_version == int(expected_row_version),
            AssessmentTask.deleted_at.is_(None),
        )
        .values(
            status=status,
            row_version=AssessmentTask.row_version + 1,
            updated_by=int(updated_by),
        )
    )
    return int(db.execute(stmt).rowcount or 0)


def get_candidate_for_user(
    db: Session,
    tenant_id: int,
    task_id: int,
    user_id: int,
    *,
    scope_clause: Any | None = None,
    for_update: bool = False,
) -> AssessmentTaskCandidate | None:
    conditions = [
        AssessmentTaskCandidate.tenant_id == int(tenant_id),
        AssessmentTaskCandidate.task_id == int(task_id),
        AssessmentTaskCandidate.user_id == int(user_id),
    ]
    if scope_clause is not None:
        conditions.append(scope_clause)
    stmt = select(AssessmentTaskCandidate).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def get_attempt_by_task_user_status(
    db: Session,
    tenant_id: int,
    task_id: int,
    user_id: int,
    status: str,
    *,
    for_update: bool = False,
) -> AssessmentAttempt | None:
    stmt = select(AssessmentAttempt).where(
        AssessmentAttempt.tenant_id == int(tenant_id),
        AssessmentAttempt.task_id == int(task_id),
        AssessmentAttempt.user_id == int(user_id),
        AssessmentAttempt.status == status,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def list_expired_attempt_ids(db: Session, server_now: Any) -> list[int]:
    stmt = (
        select(AssessmentAttempt.id)
        .where(
            AssessmentAttempt.status == "in_progress",
            AssessmentAttempt.deadline_at_snapshot <= server_now,
        )
        .order_by(AssessmentAttempt.id)
    )
    return [int(value) for value in db.execute(stmt).scalars().all()]


def get_attempt_for_worker(
    db: Session, attempt_id: int, *, for_update: bool = False
) -> AssessmentAttempt | None:
    stmt = select(AssessmentAttempt).where(
        AssessmentAttempt.id == int(attempt_id),
        AssessmentAttempt.status == "in_progress",
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def get_attempt_for_user(
    db: Session,
    tenant_id: int,
    attempt_id: int,
    user_id: int,
    *,
    candidate_scope: Any | None = None,
    for_update: bool = False,
) -> AssessmentAttempt | None:
    conditions = [
        AssessmentAttempt.tenant_id == int(tenant_id),
        AssessmentAttempt.id == int(attempt_id),
        AssessmentAttempt.user_id == int(user_id),
        AssessmentTaskCandidate.tenant_id == AssessmentAttempt.tenant_id,
        AssessmentTaskCandidate.task_id == AssessmentAttempt.task_id,
        AssessmentTaskCandidate.user_id == AssessmentAttempt.user_id,
    ]
    if candidate_scope is not None:
        conditions.append(candidate_scope)
    stmt = select(AssessmentAttempt).join(
        AssessmentTaskCandidate,
        and_(
            AssessmentTaskCandidate.task_id == AssessmentAttempt.task_id,
            AssessmentTaskCandidate.user_id == AssessmentAttempt.user_id,
            AssessmentTaskCandidate.tenant_id == AssessmentAttempt.tenant_id,
        ),
    ).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def list_paper_questions(db: Session, paper_id: int) -> list[AssessmentPaperQuestion]:
    stmt = select(AssessmentPaperQuestion).where(
        AssessmentPaperQuestion.paper_id == int(paper_id)
    ).order_by(AssessmentPaperQuestion.seq_no)
    return list(db.execute(stmt).scalars().all())


def insert_attempt(db: Session, attempt: AssessmentAttempt) -> AssessmentAttempt:
    db.add(attempt)
    db.flush()
    return attempt


def list_answers(db: Session, attempt_id: int) -> list[AssessmentAnswer]:
    stmt = select(AssessmentAnswer).where(
        AssessmentAnswer.attempt_id == int(attempt_id)
    ).order_by(AssessmentAnswer.paper_question_id)
    return list(db.execute(stmt).scalars().all())


def get_answer(
    db: Session, attempt_id: int, paper_question_id: int, *, for_update: bool = False
) -> AssessmentAnswer | None:
    stmt = select(AssessmentAnswer).where(
        AssessmentAnswer.attempt_id == int(attempt_id),
        AssessmentAnswer.paper_question_id == int(paper_question_id),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def insert_answer(db: Session, answer: AssessmentAnswer) -> AssessmentAnswer:
    db.add(answer)
    db.flush()
    return answer


def insert_outbox(db: Session, event: OutboxEvent) -> OutboxEvent:
    db.add(event)
    db.flush()
    return event


def get_due_outbox_event(
    db: Session,
    *,
    event_type: str,
    server_now: Any,
    for_update: bool = False,
) -> OutboxEvent | None:
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type == event_type,
            OutboxEvent.status == "pending",
            or_(
                OutboxEvent.next_attempt_at.is_(None),
                OutboxEvent.next_attempt_at <= server_now,
            ),
        )
        .order_by(OutboxEvent.id)
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def get_outbox_event(db: Session, event_id: int, *, for_update: bool = False) -> OutboxEvent | None:
    stmt = select(OutboxEvent).where(OutboxEvent.id == int(event_id))
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def get_event_by_client_id(
    db: Session, tenant_id: int, attempt_id: int, client_event_id: str
) -> AssessmentEvent | None:
    stmt = select(AssessmentEvent).where(
        AssessmentEvent.tenant_id == int(tenant_id),
        AssessmentEvent.attempt_id == int(attempt_id),
        AssessmentEvent.client_event_id == client_event_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def insert_event(db: Session, event: AssessmentEvent) -> AssessmentEvent:
    db.add(event)
    db.flush()
    return event


__all__ = [
    "count_tasks",
    "get_answer",
    "get_attempt_by_task_user_status",
    "get_attempt_for_worker",
    "get_attempt_for_user",
    "get_event_by_client_id",
    "get_candidate_for_user",
    "get_paper",
    "get_task",
    "get_task_by_code",
    "insert_candidates",
    "insert_answer",
    "insert_attempt",
    "insert_notifications",
    "insert_outbox",
    "insert_event",
    "insert_task",
    "list_candidates",
    "list_candidates_by_ids",
    "list_answers",
    "list_expired_attempt_ids",
    "list_notifications",
    "list_notifications_for_candidate",
    "list_notifications_by_key_prefix",
    "list_paper_questions",
    "list_tasks",
    "update_task_activation",
]
