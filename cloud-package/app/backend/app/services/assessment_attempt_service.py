"""Attempt API-131~134 orchestration."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import assessment_dao
from app.dao.redis_client import KEY_IDEMPOTENT, build_key, get_redis
from app.exceptions import BizError, ConflictError, ErrorCode, IllegalStateError, NotFoundError
from app.models.assessment import AssessmentAnswer, AssessmentAttempt, AssessmentEvent, OutboxEvent
from app.schemas.assessment import (
    AssessmentAnswerSummary,
    AssessmentAttemptDetailResponse,
    AssessmentAttemptProgressResponse,
    AssessmentAttemptReminderItem,
    AssessmentAttemptRemindersResponse,
    AssessmentAttemptStartResponse,
    AssessmentEventCreateResponse,
    AssessmentQuestionNavigation,
    AssessmentQuestionNavigationItem,
    AssessmentQuestionOption,
    AssessmentQuestionSnapshotItem,
    AssessmentQuestionSnapshotPage,
    AssessmentSaveAnswerResponse,
    AssessmentSubmitResponse,
    AssessmentSavedAnswer,
    AssessmentSubmitCheckResponse,
    AssessmentUnansweredQuestion,
)
from app.services import assessment_task_support as support


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_LOCAL_IDEMPOTENCY: dict[str, dict[str, Any]] = {}
_LOCAL_IDEMPOTENCY_LOCK = RLock()
_EVENT_SEVERITY = {
    "refresh": "low",
    "idle": "low",
    "visibility_hidden": "medium",
    "paste": "medium",
    "device_change": "high",
    "submit_after_deadline": "critical",
}


def _server_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def _idempotency_key(principal: CurrentPrincipal, operation: str, key: str) -> str:
    return build_key(KEY_IDEMPOTENT, "assessment", operation, principal.tenant_id, principal.user_id, key)


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _read_idempotent(
    principal: CurrentPrincipal,
    operation: str,
    key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    redis_key = _idempotency_key(principal, operation, key)
    raw: str | None = None
    try:
        raw = get_redis().get(redis_key)
    except RedisError:
        with _LOCAL_IDEMPOTENCY_LOCK:
            cached = _LOCAL_IDEMPOTENCY.get(redis_key)
        if cached is not None:
            if cached["fingerprint"] != fingerprint:
                raise ConflictError("同一 Idempotency-Key 不得提交不同请求")
            raise BizError(
                ErrorCode.IDEMPOTENT_REPLAY,
                "该请求已处理，返回首次结果",
                cached["result"],
            )
        return None
    if raw is None:
        return None
    cached = json.loads(raw)
    if cached["fingerprint"] != fingerprint:
        raise ConflictError("同一 Idempotency-Key 不得提交不同请求")
    raise BizError(
        ErrorCode.IDEMPOTENT_REPLAY,
        "该请求已处理，返回首次结果",
        cached["result"],
    )


def _write_idempotent(
    principal: CurrentPrincipal,
    operation: str,
    key: str,
    fingerprint: str,
    result: dict[str, Any],
) -> None:
    redis_key = _idempotency_key(principal, operation, key)
    payload = json.dumps({"fingerprint": fingerprint, "result": result}, ensure_ascii=False)
    try:
        get_redis().set(redis_key, payload, ex=24 * 60 * 60)
    except RedisError:
        with _LOCAL_IDEMPOTENCY_LOCK:
            _LOCAL_IDEMPOTENCY[redis_key] = {"fingerprint": fingerprint, "result": result}


def _snapshot_item(row: Any) -> dict[str, Any]:
    source = dict(row.question_snapshot or {})
    raw_options = source.get("options") or []
    options: list[dict[str, Any]] = []
    for index, option in enumerate(raw_options, start=1):
        if isinstance(option, dict):
            options.append(
                {
                    "code": str(option.get("code", "")),
                    "text": str(option.get("text", "")),
                    "order": int(option.get("order", index)),
                }
            )
    return {
        "paper_question_id": str(row.id),
        "question_id": str(row.question_id),
        "question_version": int(row.question_version),
        "question_type": str(source.get("question_type", "")),
        "question_content": str(source.get("question_content", source.get("content", ""))),
        "content": str(source.get("content", source.get("question_content", ""))),
        "options": options,
        "option_order": [option["order"] for option in options],
        "score": float(row.score),
        "question_order": int(row.seq_no),
        "dimension_snapshot": row.dimension_snapshot,
        "answer_rule_snapshot": row.answer_rule_snapshot,
    }


def _is_answered(answer: dict[str, Any] | None) -> bool:
    return isinstance(answer, dict) and answer.get("value") is not None


def _progress(snapshot: list[dict[str, Any]], answers: list[AssessmentAnswer]) -> int:
    valid_ids = {str(item["paper_question_id"]) for item in snapshot}
    answered = sum(
        1
        for answer in answers
        if str(answer.paper_question_id) in valid_ids and _is_answered(answer.answer)
    )
    return round(answered * 100 / len(snapshot)) if snapshot else 0


def _answer_map(answers: list[AssessmentAnswer]) -> dict[str, AssessmentAnswer]:
    return {str(answer.paper_question_id): answer for answer in answers}


class AssessmentAttemptService:
    """Service boundary; methods own their transaction and DAO never commits."""

    @classmethod
    def _visible_task_candidate(
        cls, db: Session, principal: CurrentPrincipal, task_id: int, *, for_update: bool
    ) -> tuple[Any, Any]:
        task = support.visible_task(db, principal, task_id)
        candidate = assessment_dao.get_candidate_for_user(
            db,
            principal.tenant_id,
            task.id,
            principal.user_id,
            scope_clause=support.candidate_scope(db, principal),
            for_update=for_update,
        )
        if candidate is None:
            raise NotFoundError("当前用户未分配到该测评任务")
        return task, candidate

    @classmethod
    def _start_response(
        cls, db: Session, attempt: AssessmentAttempt, *, server_now: datetime | None = None
    ) -> AssessmentAttemptStartResponse:
        snapshot = list(attempt.paper_snapshot or [])
        answers = assessment_dao.list_answers(db, attempt.id)
        answer_by_question = _answer_map(answers)
        navigation = [
            AssessmentQuestionNavigationItem(
                question_id=int(item["question_id"]),
                question_order=int(item["question_order"]),
                answered=_is_answered(
                    answer_by_question.get(str(item["paper_question_id"])).answer
                    if answer_by_question.get(str(item["paper_question_id"]))
                    else None
                ),
            )
            for item in snapshot
        ]
        current = next((item for item in navigation if not item.answered), navigation[0])
        saved_answers = [
            AssessmentSavedAnswer(
                question_id=int(item["question_id"]),
                answer=answer.answer,
                answer_version=int(answer.answer_version),
            )
            for item in snapshot
            for answer in [answer_by_question.get(str(item["paper_question_id"]))]
            if answer is not None
        ]
        return AssessmentAttemptStartResponse(
            attempt_id=attempt.id,
            task_id=attempt.task_id,
            status="in_progress",
            attempt_no=attempt.attempt_no,
            answer_version=attempt.answer_version,
            server_now=support.api_time(server_now or _server_now()),
            duration_min_snapshot=attempt.duration_min_snapshot,
            deadline_at=support.api_time(attempt.deadline_at_snapshot),
            question_navigation=AssessmentQuestionNavigation(
                current_question_id=current.question_id,
                total_questions=len(navigation),
                answered_count=sum(item.answered for item in navigation),
                questions=navigation,
            ),
            saved_answers=saved_answers,
        )

    @classmethod
    def start_attempt(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task_id: int,
        *,
        idempotency_key: str | None,
    ) -> AssessmentAttemptStartResponse:
        key = support.require_idempotency(idempotency_key)
        DataScopeFilter.require_detail_access(principal)
        try:
            task, candidate = cls._visible_task_candidate(db, principal, task_id, for_update=True)
            existing = assessment_dao.get_attempt_by_task_user_status(
                db,
                principal.tenant_id,
                task.id,
                principal.user_id,
                "in_progress",
                for_update=True,
            )
            if existing is not None:
                return cls._start_response(db, existing)

            now = _server_now()
            if task.status not in ("scheduled", "running"):
                raise IllegalStateError("当前任务状态不允许开始作答")
            if now < task.start_at or now >= task.deadline_at:
                raise IllegalStateError("当前不在任务作答时间窗口内")
            paper = support.required_paper(db, principal.tenant_id, task.paper_id)
            if paper.status != "published" or paper.published_at is None:
                raise IllegalStateError("关联试卷尚未发布")
            questions = assessment_dao.list_paper_questions(db, paper.id)
            if not questions:
                raise IllegalStateError("试卷没有可作答题目")
            if int(candidate.attempt_count) >= int(task.attempt_limit):
                raise IllegalStateError("已达到最大作答次数")

            snapshot = [_snapshot_item(row) for row in questions]
            attempt = AssessmentAttempt(
                tenant_id=principal.tenant_id,
                task_id=task.id,
                talent_id=candidate.talent_id,
                user_id=principal.user_id,
                paper_snapshot=snapshot,
                attempt_no=int(candidate.attempt_count) + 1,
                answer_version=0,
                started_at=now,
                duration_min_snapshot=task.duration_min,
                deadline_at_snapshot=task.deadline_at,
                status="in_progress",
            )
            candidate.assignment_status = "started"
            candidate.progress = 0
            candidate.attempt_count = int(candidate.attempt_count) + 1
            assessment_dao.insert_attempt(db, attempt)
            db.commit()
            return cls._start_response(db, attempt, server_now=now)
        except (BizError, IntegrityError):
            db.rollback()
            raise

    @classmethod
    def _get_visible_attempt(
        cls, db: Session, principal: CurrentPrincipal, attempt_id: int, *, for_update: bool = False
    ) -> AssessmentAttempt:
        DataScopeFilter.require_detail_access(principal)
        attempt = assessment_dao.get_attempt_for_user(
            db,
            principal.tenant_id,
            attempt_id,
            principal.user_id,
            candidate_scope=support.candidate_scope(db, principal),
            for_update=for_update,
        )
        if attempt is None:
            raise NotFoundError("Attempt 不存在")
        return attempt

    @classmethod
    def get_attempt_detail(
        cls, db: Session, principal: CurrentPrincipal, attempt_id: int
    ) -> AssessmentAttemptDetailResponse:
        attempt = cls._get_visible_attempt(db, principal, attempt_id)
        progress = _progress(list(attempt.paper_snapshot or []), assessment_dao.list_answers(db, attempt.id))
        return AssessmentAttemptDetailResponse(
            attempt_id=attempt.id,
            task_id=attempt.task_id,
            attempt_no=attempt.attempt_no,
            status=attempt.status,
            answer_version=attempt.answer_version,
            started_at=support.api_time(attempt.started_at),
            duration_min_snapshot=attempt.duration_min_snapshot,
            deadline_at_snapshot=support.api_time(attempt.deadline_at_snapshot),
            submitted_at=support.api_time(attempt.submitted_at),
            server_now=support.api_time(_server_now()),
            progress=progress,
        )

    @classmethod
    def list_attempt_reminders(
        cls, db: Session, principal: CurrentPrincipal, attempt_id: int
    ) -> AssessmentAttemptRemindersResponse:
        attempt = cls._get_visible_attempt(db, principal, attempt_id)
        candidate = assessment_dao.get_candidate_for_user(
            db,
            principal.tenant_id,
            attempt.task_id,
            principal.user_id,
            scope_clause=support.candidate_scope(db, principal),
        )
        if candidate is None:
            raise NotFoundError("当前用户未分配到该测评任务")
        notifications = assessment_dao.list_notifications_for_candidate(
            db, principal.tenant_id, attempt.task_id, candidate.id
        )
        return AssessmentAttemptRemindersResponse(
            attempt_id=attempt.id,
            notifications=[
                AssessmentAttemptReminderItem(
                    notification_id=notification.id,
                    notification_type=notification.notification_type,
                    status=notification.status,
                )
                for notification in notifications
            ],
        )

    @classmethod
    def get_attempt_progress(
        cls, db: Session, principal: CurrentPrincipal, attempt_id: int
    ) -> AssessmentAttemptProgressResponse:
        attempt = cls._get_visible_attempt(db, principal, attempt_id)
        snapshot = list(attempt.paper_snapshot or [])
        answers = _answer_map(assessment_dao.list_answers(db, attempt.id))
        answered_count = sum(
            1
            for item in snapshot
            if _is_answered(
                answers.get(str(item["paper_question_id"])).answer
                if answers.get(str(item["paper_question_id"])) is not None
                else None
            )
        )
        current_item = next(
            (
                item
                for item in snapshot
                if not _is_answered(
                    answers.get(str(item["paper_question_id"])).answer
                    if answers.get(str(item["paper_question_id"])) is not None
                    else None
                )
            ),
            None,
        )
        now = _server_now()
        return AssessmentAttemptProgressResponse(
            total_questions=len(snapshot),
            answered_count=answered_count,
            unanswered_count=len(snapshot) - answered_count,
            progress=_progress(snapshot, list(answers.values())),
            current_question_id=(
                int(current_item["question_id"]) if current_item is not None else None
            ),
            last_saved_at=support.api_time(attempt.last_saved_at),
            deadline_at=support.api_time(attempt.deadline_at_snapshot),
            server_now=support.api_time(now),
        )

    @classmethod
    def create_event(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        attempt_id: int,
        *,
        event_type: str,
        occurred_at: datetime,
        duration_ms: int | None,
        metadata_summary: dict[str, Any],
        client_event_id: str,
        idempotency_key: str | None,
    ) -> AssessmentEventCreateResponse:
        key = support.require_idempotency(idempotency_key)
        fingerprint = _fingerprint(
            {
                "attempt_id": int(attempt_id),
                "event_type": event_type,
                "occurred_at": occurred_at.isoformat(),
                "duration_ms": duration_ms,
                "metadata_summary": metadata_summary,
                "client_event_id": client_event_id,
            }
        )
        _read_idempotent(principal, "event", key, fingerprint)

        try:
            attempt = cls._get_visible_attempt(db, principal, attempt_id)
            existing = assessment_dao.get_event_by_client_id(
                db, principal.tenant_id, attempt.id, client_event_id
            )
            if existing is not None:
                result = AssessmentEventCreateResponse(event_id=existing.id)
                _write_idempotent(
                    principal, "event", key, fingerprint, result.model_dump(mode="json")
                )
                return result

            event = AssessmentEvent(
                tenant_id=principal.tenant_id,
                attempt_id=attempt.id,
                client_event_id=client_event_id,
                event_type=event_type,
                occurred_at=occurred_at.replace(tzinfo=None),
                received_at=_server_now(),
                duration_ms=duration_ms,
                metadata_summary=metadata_summary,
                severity=_EVENT_SEVERITY[event_type],
                review_status="unreviewed",
            )
            assessment_dao.insert_event(db, event)
            db.commit()
            result = AssessmentEventCreateResponse(event_id=event.id)
            _write_idempotent(
                principal, "event", key, fingerprint, result.model_dump(mode="json")
            )
            return result
        except IntegrityError:
            db.rollback()
            existing = assessment_dao.get_event_by_client_id(
                db, principal.tenant_id, int(attempt_id), client_event_id
            )
            if existing is None:
                raise
            result = AssessmentEventCreateResponse(event_id=existing.id)
            _write_idempotent(
                principal, "event", key, fingerprint, result.model_dump(mode="json")
            )
            return result
        except Exception:
            db.rollback()
            raise

    @classmethod
    def list_attempt_questions(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        attempt_id: int,
        *,
        page_no: int,
        page_size: int,
    ) -> AssessmentQuestionSnapshotPage:
        attempt = cls._get_visible_attempt(db, principal, attempt_id)
        snapshot = list(attempt.paper_snapshot or [])
        answers = _answer_map(assessment_dao.list_answers(db, attempt.id))
        total = len(snapshot)
        start = (page_no - 1) * page_size
        page_items: list[AssessmentQuestionSnapshotItem] = []
        for item in snapshot[start : start + page_size]:
            saved = answers.get(str(item["paper_question_id"]))
            answer = saved.answer if saved is not None and saved.answer is not None else {"value": None}
            page_items.append(
                AssessmentQuestionSnapshotItem(
                    question_id=int(item["question_id"]),
                    question_version=int(item["question_version"]),
                    question_type=str(item["question_type"]),
                    content=str(item["content"]),
                    options=[AssessmentQuestionOption.model_validate(option) for option in item["options"]],
                    score=float(item["score"]),
                    question_order=int(item["question_order"]),
                    answer_summary=AssessmentAnswerSummary(
                        answered=_is_answered(saved.answer if saved is not None else None),
                        answer=answer,
                    ),
                )
            )
        return AssessmentQuestionSnapshotPage(items=page_items, total=total)

    @classmethod
    def check_submission(
        cls, db: Session, principal: CurrentPrincipal, attempt_id: int
    ) -> AssessmentSubmitCheckResponse:
        attempt = cls._get_visible_attempt(db, principal, attempt_id)
        if attempt.status != "in_progress":
            raise IllegalStateError("只有 in_progress Attempt 可以进行提交前检查")

        snapshot = list(attempt.paper_snapshot or [])
        answers = _answer_map(assessment_dao.list_answers(db, attempt.id))
        unanswered_questions = [
            AssessmentUnansweredQuestion(
                question_id=int(item["question_id"]),
                question_order=int(item["question_order"]),
            )
            for item in snapshot
            if not _is_answered(
                answers.get(str(item["paper_question_id"])).answer
                if answers.get(str(item["paper_question_id"])) is not None
                else None
            )
        ]
        now = _server_now()
        timeout = now >= attempt.deadline_at_snapshot
        blocking_reasons: list[str] = []
        if unanswered_questions:
            blocking_reasons.append("存在未作答题目")
        if timeout:
            blocking_reasons.append("已超过作答截止时间")

        return AssessmentSubmitCheckResponse(
            can_submit=not unanswered_questions and not timeout,
            unanswered_count=len(unanswered_questions),
            unanswered_questions=unanswered_questions,
            server_now=support.api_time(now),
            deadline_at=support.api_time(attempt.deadline_at_snapshot),
            timeout=timeout,
            blocking_reasons=blocking_reasons,
        )

    @classmethod
    def submit_attempt(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        attempt_id: int,
        *,
        confirm: bool,
        idempotency_key: str | None,
    ) -> AssessmentSubmitResponse:
        key = support.require_idempotency(idempotency_key)
        fingerprint = _fingerprint(
            {"attempt_id": int(attempt_id), "confirm": bool(confirm)}
        )
        _read_idempotent(principal, "submit", key, fingerprint)

        try:
            if not confirm:
                raise IllegalStateError("confirm 必须为 true")

            attempt = cls._get_visible_attempt(db, principal, attempt_id, for_update=True)
            if attempt.status != "in_progress":
                raise ConflictError("当前 Attempt 已完成或不允许再次提交")

            snapshot = list(attempt.paper_snapshot or [])
            answers = _answer_map(assessment_dao.list_answers(db, attempt.id))
            unanswered_questions = [
                item
                for item in snapshot
                if not _is_answered(
                    answers.get(str(item["paper_question_id"])).answer
                    if answers.get(str(item["paper_question_id"])) is not None
                    else None
                )
            ]
            now = _server_now()
            if unanswered_questions or now >= attempt.deadline_at_snapshot:
                raise IllegalStateError("提交前检查不通过")

            candidate = assessment_dao.get_candidate_for_user(
                db,
                principal.tenant_id,
                attempt.task_id,
                principal.user_id,
                scope_clause=support.candidate_scope(db, principal),
                for_update=True,
            )
            if candidate is None:
                raise NotFoundError("当前用户未分配到该测评任务")

            attempt.status = "submitted"
            attempt.submitted_at = now
            candidate.assignment_status = "submitted"
            candidate.progress = 100

            answer_version = int(attempt.answer_version)
            outbox = OutboxEvent(
                tenant_id=principal.tenant_id,
                aggregate_type="assessment_attempt",
                aggregate_id=attempt.id,
                event_type="assessment.score.requested",
                payload={
                    "attempt_id": str(attempt.id),
                    "task_id": str(attempt.task_id),
                    "tenant_id": str(principal.tenant_id),
                    "answer_version": answer_version,
                },
                idempotency_key=f"{attempt.id}:score:v{answer_version}",
                status="pending",
            )
            assessment_dao.insert_outbox(db, outbox)
            db.commit()

            result = AssessmentSubmitResponse(
                attempt_id=attempt.id,
                status="submitted",
                submitted_at=support.api_time(now),
                outbox_event_id=outbox.id,
            )
            _write_idempotent(
                principal, "submit", key, fingerprint, result.model_dump(mode="json")
            )
            return result
        except Exception:
            db.rollback()
            raise

    @classmethod
    def save_answer(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        attempt_id: int,
        paper_question_id: int,
        *,
        answer: dict[str, Any],
        answer_version: int,
        idempotency_key: str | None,
    ) -> AssessmentSaveAnswerResponse:
        key = support.require_idempotency(idempotency_key)
        fingerprint = _fingerprint(
            {
                "attempt_id": int(attempt_id),
                "paper_question_id": int(paper_question_id),
                "answer": answer,
                "answer_version": int(answer_version),
            }
        )
        _read_idempotent(principal, "answer", key, fingerprint)
        try:
            attempt = cls._get_visible_attempt(db, principal, attempt_id, for_update=True)
            if attempt.status != "in_progress":
                raise IllegalStateError("只有 in_progress Attempt 可以保存答案")
            snapshot = list(attempt.paper_snapshot or [])
            if not any(str(item["paper_question_id"]) == str(paper_question_id) for item in snapshot):
                raise NotFoundError("题目不属于当前 Attempt")
            if int(attempt.answer_version) != int(answer_version):
                raise ConflictError("答案版本已变化，请刷新后重试")

            now = _server_now()
            new_version = int(attempt.answer_version) + 1
            current = assessment_dao.get_answer(
                db, attempt.id, paper_question_id, for_update=True
            )
            if current is None:
                current = AssessmentAnswer(
                    attempt_id=attempt.id,
                    paper_question_id=int(paper_question_id),
                    answer_version=new_version,
                    answer=answer,
                    answered_at=now,
                    review_status="unreviewed",
                )
                assessment_dao.insert_answer(db, current)
            else:
                current.answer_version = new_version
                current.answer = answer
                current.answered_at = now
            attempt.answer_version = new_version
            attempt.last_saved_at = now
            candidate = assessment_dao.get_candidate_for_user(
                db,
                principal.tenant_id,
                attempt.task_id,
                principal.user_id,
                scope_clause=support.candidate_scope(db, principal),
                for_update=True,
            )
            if candidate is None:
                raise NotFoundError("当前用户未分配到该测评任务")
            db.flush()
            progress = _progress(snapshot, assessment_dao.list_answers(db, attempt.id))
            candidate.progress = progress
            db.commit()
            result = AssessmentSaveAnswerResponse(
                attempt_id=attempt.id,
                paper_question_id=paper_question_id,
                answer_version=new_version,
                saved_at=support.api_time(now),
                progress=progress,
            )
            _write_idempotent(
                principal, "answer", key, fingerprint, result.model_dump(mode="json")
            )
            return result
        except (BizError, IntegrityError):
            db.rollback()
            raise


__all__ = ["AssessmentAttemptService"]
