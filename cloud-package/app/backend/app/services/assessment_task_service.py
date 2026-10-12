"""Task, minimal candidate assignment, and minimal notification orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import assessment_dao
from app.exceptions import BizError, ConflictError, ErrorCode, IllegalStateError, NotFoundError, PermissionDeniedError
from app.models.assessment import AssessmentNotification, AssessmentTask, AssessmentTaskCandidate
from app.schemas.assessment import (
    AssessmentReminderResponse,
    AssessmentTaskActivation,
    AssessmentTaskCreateRequest,
    AssessmentTaskDetail,
    AssessmentTaskListQuery,
    AssessmentTaskProgress,
    AssessmentTaskReminderRequest,
    AssessmentTaskSummary,
)
from app.services.audit_service import AuditService
from app.services import assessment_task_support as support


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
PRECONDITION_CODE = 42202


def _db_time(value: datetime) -> datetime:
    """Store a timezone-aware API value in the existing naive DATETIME column."""
    return value.astimezone(LOCAL_TZ).replace(tzinfo=None)


def _precondition(message: str, data: Any = None) -> BizError:
    return BizError(PRECONDITION_CODE, message, data, http_status=422)


class AssessmentTaskService:
    """API-119~124 service boundary; DAO never commits transactions."""

    @classmethod
    def list_tasks(
        cls, db: Session, principal: CurrentPrincipal, query: AssessmentTaskListQuery
    ) -> tuple[list[AssessmentTaskSummary], int]:
        scope = support.task_scope(db, principal)
        rows = assessment_dao.list_tasks(
            db,
            principal.tenant_id,
            scope_clause=scope,
            keyword=query.keyword,
            paper_id=query.paper_id,
            position_level=query.position_level,
            status=query.status,
            limit=query.page_size,
            offset=(query.page_no - 1) * query.page_size,
        )
        total = assessment_dao.count_tasks(
            db,
            principal.tenant_id,
            scope_clause=scope,
            keyword=query.keyword,
            paper_id=query.paper_id,
            position_level=query.position_level,
            status=query.status,
        )
        return [support.to_summary(db, task, paper) for task, paper in rows], total

    @classmethod
    def get_task_detail(cls, db: Session, principal: CurrentPrincipal, task_id: int) -> AssessmentTaskDetail:
        task = support.visible_task(db, principal, task_id)
        paper = support.required_paper(db, principal.tenant_id, task.paper_id)
        return support.to_detail(db, task, paper)

    @classmethod
    def get_task_progress(cls, db: Session, principal: CurrentPrincipal, task_id: int) -> AssessmentTaskProgress:
        task = support.visible_task(db, principal, task_id)
        candidate_scope = support.candidate_scope(db, principal)
        candidates = assessment_dao.list_candidates(
            db, principal.tenant_id, task.id, scope_clause=candidate_scope
        )
        return support.progress_output(task, candidates)

    @classmethod
    def create_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        request: AssessmentTaskCreateRequest,
        *,
        idempotency_key: str | None,
    ) -> AssessmentTaskDetail:
        key = support.require_idempotency(idempotency_key)
        DataScopeFilter.require_detail_access(principal)
        if principal.org_id is None:
            raise _precondition("当前认证主体缺少组织范围，不能创建测评任务")

        task_code = support.task_code(principal.tenant_id, key)
        existing = assessment_dao.get_task_by_code(db, principal.tenant_id, task_code)
        if existing is not None:
            paper = support.required_paper(db, principal.tenant_id, existing.paper_id)
            detail = support.to_detail(db, existing, paper)
            raise BizError(
                ErrorCode.IDEMPOTENT_REPLAY,
                "该任务已创建，返回首次结果",
                detail.model_dump(mode="json"),
            )

        paper = assessment_dao.get_paper(db, principal.tenant_id, request.paper_id)
        if paper is None:
            raise NotFoundError("试卷不存在")
        support.assert_org_allowed(db, principal, paper.org_id)
        if paper.status != "published" or paper.published_at is None:
            raise _precondition("试卷尚未发布，不能创建测评任务")

        target_scope = request.target_scope
        for candidate in target_scope.candidates:
            support.assert_candidate_allowed(db, principal, candidate.user_id, candidate.org_id)

        task = AssessmentTask(
            tenant_id=principal.tenant_id,
            org_id=principal.org_id,
            task_code=task_code,
            name=request.name.strip(),
            paper_id=request.paper_id,
            start_at=_db_time(request.start_at),
            deadline_at=_db_time(request.deadline_at),
            duration_min=request.duration_min,
            attempt_limit=request.attempt_limit,
            target_scope=target_scope.model_dump(mode="json"),
            notify_rules=request.notify_rules.model_dump(mode="json"),
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )

        try:
            assessment_dao.insert_task(db, task)
            candidates = [
                AssessmentTaskCandidate(
                    tenant_id=principal.tenant_id,
                    org_id=candidate.org_id,
                    task_id=task.id,
                    talent_id=candidate.talent_id,
                    user_id=candidate.user_id,
                    assignment_status="assigned",
                    attempt_count=0,
                    progress=0,
                    abnormal_count=0,
                    deadline_at=task.deadline_at,
                    reminder_status="none",
                )
                for candidate in target_scope.candidates
            ]
            assessment_dao.insert_candidates(db, candidates)
            notifications = support.task_created_notifications(
                principal.tenant_id, task, candidates, request.notify_rules, key
            )
            assessment_dao.insert_notifications(db, notifications)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            replay = assessment_dao.get_task_by_code(db, principal.tenant_id, task_code)
            if replay is not None:
                paper = support.required_paper(db, principal.tenant_id, replay.paper_id)
                detail = support.to_detail(db, replay, paper)
                raise BizError(
                    ErrorCode.IDEMPOTENT_REPLAY,
                    "该任务已创建，返回首次结果",
                    detail.model_dump(mode="json"),
                ) from exc
            raise BizError(ErrorCode.UNIQUE_CONFLICT, "任务编号已存在") from exc

        AuditService.record(
            "assessment_task_create",
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type="assessment_task",
            target_id=task.id,
            after={"status": task.status, "task_code": task.task_code},
        )
        return support.to_detail(db, task, paper)

    @classmethod
    def activate_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task_id: int,
        *,
        row_version: int,
        idempotency_key: str | None,
    ) -> AssessmentTaskActivation:
        support.require_idempotency(idempotency_key)
        scope = support.task_scope(db, principal)
        task = assessment_dao.get_task(
            db, principal.tenant_id, task_id, scope_clause=scope, for_update=True
        )
        if task is None:
            raise NotFoundError("测评任务不存在")
        if task.status != "draft":
            raise IllegalStateError("只有 draft 状态的任务可以激活")
        if int(task.row_version) != int(row_version):
            raise ConflictError("任务版本已变化，请刷新后重试")

        paper = assessment_dao.get_paper(db, principal.tenant_id, task.paper_id)
        if paper is None:
            raise _precondition("关联试卷不存在")
        if paper.status != "published" or paper.published_at is None:
            raise _precondition("关联试卷尚未发布")
        target_scope = support.parse_target_scope(task.target_scope)
        support.parse_notify_rules(task.notify_rules)
        candidates = assessment_dao.list_candidates(db, principal.tenant_id, task.id)
        if not candidates:
            raise _precondition("任务至少需要一条合法候选人分配记录")
        if len({int(row.user_id) for row in candidates}) != len(candidates):
            raise _precondition("候选人分配记录存在重复 user_id")
        expected_candidates = {
            (int(candidate.talent_id), int(candidate.user_id), int(candidate.org_id))
            for candidate in target_scope.candidates
        }
        actual_candidates = {
            (int(candidate.talent_id), int(candidate.user_id), int(candidate.org_id))
            for candidate in candidates
        }
        if expected_candidates != actual_candidates:
            raise _precondition("target_scope 与候选人分配记录不一致")
        for candidate in candidates:
            support.assert_candidate_allowed(db, principal, candidate.user_id, candidate.org_id)
        if not target_scope.candidates:
            raise _precondition("target_scope.candidates 不能为空")
        if task.start_at >= task.deadline_at or task.duration_min <= 0 or task.attempt_limit < 1:
            raise _precondition("任务时间、作答时长或作答次数不合法")

        now = datetime.now()
        status = "scheduled" if now < task.start_at else "running"
        affected = assessment_dao.update_task_activation(
            db,
            principal.tenant_id,
            task.id,
            expected_row_version=row_version,
            status=status,
            updated_by=principal.user_id,
        )
        if affected != 1:
            db.rollback()
            raise ConflictError("任务版本已变化，请刷新后重试")
        db.commit()
        db.refresh(task)
        notifications = assessment_dao.list_notifications(db, principal.tenant_id, task.id)
        AuditService.record(
            "assessment_task_activate",
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type="assessment_task",
            target_id=task.id,
            after={"status": task.status, "row_version": task.row_version},
        )
        return AssessmentTaskActivation(
            id=task.id,
            task_code=task.task_code,
            status=task.status,
            start_at=support.api_time(task.start_at),
            deadline_at=support.api_time(task.deadline_at),
            candidate_count=len(candidates),
            notification_summary=support.notification_summary(notifications),
            updated_at=support.api_time(task.updated_at),
            row_version=task.row_version,
        )

    @classmethod
    def remind_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task_id: int,
        request: AssessmentTaskReminderRequest,
        *,
        idempotency_key: str | None,
    ) -> AssessmentReminderResponse:
        key = support.require_idempotency(idempotency_key)
        task = support.visible_task(db, principal, task_id)
        prefix = f"assessment-reminder:{principal.tenant_id}:{task.id}:{key}:"
        replay = assessment_dao.list_notifications_by_key_prefix(db, principal.tenant_id, prefix)
        if replay:
            payload = support.reminder_payload(replay, task.id, request.channels)
            raise BizError(ErrorCode.IDEMPOTENT_REPLAY, "该提醒已创建，返回首次结果", payload)

        candidate_scope = support.candidate_scope(db, principal)
        if request.candidate_ids:
            unscoped = assessment_dao.list_candidates_by_ids(
                db, principal.tenant_id, task.id, request.candidate_ids
            )
            if len(unscoped) != len(request.candidate_ids):
                raise NotFoundError("候选人不属于当前任务")
            candidates = assessment_dao.list_candidates_by_ids(
                db,
                principal.tenant_id,
                task.id,
                request.candidate_ids,
                scope_clause=candidate_scope,
            )
            if len(candidates) != len(unscoped):
                raise PermissionDeniedError("候选人超出当前数据范围")
        else:
            unscoped = assessment_dao.list_candidates(db, principal.tenant_id, task.id)
            candidates = assessment_dao.list_candidates(
                db, principal.tenant_id, task.id, scope_clause=candidate_scope
            )
            if unscoped and not candidates:
                raise PermissionDeniedError("候选人超出当前数据范围")
        if not candidates:
            raise NotFoundError("当前任务没有可提醒的候选人")

        now = datetime.now()
        notifications = [
            AssessmentNotification(
                tenant_id=principal.tenant_id,
                task_id=task.id,
                task_candidate_id=candidate.id,
                channel=channel,
                notification_type="reminder",
                status="pending",
                idempotency_key=f"{prefix}{candidate.id}:{channel}",
            )
            for candidate in candidates
            for channel in request.channels
        ]
        try:
            assessment_dao.insert_notifications(db, notifications)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            replay = assessment_dao.list_notifications_by_key_prefix(db, principal.tenant_id, prefix)
            if replay:
                raise BizError(
                    ErrorCode.IDEMPOTENT_REPLAY,
                    "该提醒已创建，返回首次结果",
                    support.reminder_payload(replay, task.id, request.channels),
                ) from exc
            raise BizError(ErrorCode.UNIQUE_CONFLICT, "通知记录已存在") from exc

        AuditService.record(
            "assessment_task_remind",
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type="assessment_task",
            target_id=task.id,
            after={"notification_count": len(notifications), "status": "pending"},
        )
        return support.reminder_output(notifications, task.id, request.channels, now)

__all__ = ["AssessmentTaskService"]
