"""Auto-submit worker for expired assessment attempts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.dao import assessment_dao
from app.models.assessment import AssessmentAttempt, OutboxEvent
from app.services.assessment_attempt_service import _progress, _server_now


def _database_time(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _auto_submit_one(db: Session, attempt_id: int, server_now: datetime) -> bool:
    attempt = assessment_dao.get_attempt_for_worker(db, attempt_id, for_update=True)
    if attempt is None or attempt.deadline_at_snapshot > server_now:
        return False

    candidate = assessment_dao.get_candidate_for_user(
        db,
        attempt.tenant_id,
        attempt.task_id,
        attempt.user_id,
        for_update=True,
    )
    if candidate is None:
        raise RuntimeError("到期 Attempt 缺少对应 Candidate")

    snapshot = list(attempt.paper_snapshot or [])
    answers = assessment_dao.list_answers(db, attempt.id)
    candidate_progress = _progress(snapshot, answers)
    attempt.status = "auto_submitted"
    attempt.submitted_at = server_now
    candidate.assignment_status = "submitted"
    candidate.progress = candidate_progress

    answer_version = int(attempt.answer_version)
    outbox = OutboxEvent(
        tenant_id=attempt.tenant_id,
        aggregate_type="assessment_attempt",
        aggregate_id=attempt.id,
        event_type="assessment.score.requested",
        payload={
            "attempt_id": str(attempt.id),
            "task_id": str(attempt.task_id),
            "tenant_id": str(attempt.tenant_id),
            "answer_version": answer_version,
        },
        idempotency_key=f"{attempt.id}:score:v{answer_version}",
        status="pending",
    )
    assessment_dao.insert_outbox(db, outbox)
    db.commit()
    return True


def run_once(db: Session, *, server_now: datetime | None = None) -> int:
    """Auto-submit all attempts expired at the supplied server time.

    Scheduling is intentionally external.  Each attempt has its own transaction
    so one failed record cannot commit a partial state transition for itself.
    """

    now = _database_time(server_now or _server_now())
    attempt_ids = assessment_dao.list_expired_attempt_ids(db, now)
    processed = 0
    for attempt_id in attempt_ids:
        try:
            if _auto_submit_one(db, attempt_id, now):
                processed += 1
        except Exception:
            db.rollback()
            raise
    return processed


__all__ = ["run_once"]
