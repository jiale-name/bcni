"""Shared validation, scope, and response projection for Assessment Task APIs."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import assessment_dao
from app.exceptions import BizError, ErrorCode, NotFoundError, PermissionDeniedError
from app.models.assessment import AssessmentNotification, AssessmentPaper, AssessmentTask, AssessmentTaskCandidate
from app.models.enums import RoleDataScope
from app.schemas.assessment import (
    AssessmentCandidateProgress,
    AssessmentCandidateSummary,
    AssessmentNotifyRules,
    AssessmentNotificationSummary,
    AssessmentReminderResponse,
    AssessmentTargetScope,
    AssessmentTaskDetail,
    AssessmentTaskProgress,
    AssessmentTaskSummary,
)
from app.services.org_scope_service import OrgScopeService


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def api_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=LOCAL_TZ) if value.tzinfo is None else value


def require_idempotency(key: str | None) -> str:
    normalized = (key or "").strip()
    if not normalized:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "写接口必须携带 Idempotency-Key",
            {"fields": [{"field": "Idempotency-Key", "message": "不能为空"}]},
        )
    return normalized


def task_code(tenant_id: int, key: str) -> str:
    digest = sha256(f"{tenant_id}:{key}".encode("utf-8")).hexdigest()[:24]
    return f"AT-{digest}"


def required_paper(db: Session, tenant_id: int, paper_id: int) -> AssessmentPaper:
    paper = assessment_dao.get_paper(db, tenant_id, paper_id)
    if paper is None:
        raise NotFoundError("关联试卷不存在")
    return paper


def task_scope(db: Session, principal: CurrentPrincipal) -> Any:
    DataScopeFilter.require_detail_access(principal)
    if int(principal.data_scope) == int(RoleDataScope.SELF):
        if principal.org_id is None:
            return and_(AssessmentTask.tenant_id == principal.tenant_id, AssessmentTask.org_id == -1)
        return and_(AssessmentTask.tenant_id == principal.tenant_id, AssessmentTask.org_id == principal.org_id)
    return DataScopeFilter.mysql_condition(
        AssessmentTask, principal, dept_ids=dept_ids(db, principal)
    )


def candidate_scope(db: Session, principal: CurrentPrincipal) -> Any:
    return DataScopeFilter.mysql_condition(
        AssessmentTaskCandidate,
        principal,
        dept_ids=dept_ids(db, principal),
        owner_column="user_id",
    )


def dept_ids(db: Session, principal: CurrentPrincipal) -> tuple[int, ...]:
    if int(principal.data_scope) != int(RoleDataScope.OWN_DEPT):
        return principal.dept_ids
    return principal.dept_ids or OrgScopeService.resolve_dept_ids(
        db, principal.tenant_id, principal.org_id
    )


def assert_org_allowed(db: Session, principal: CurrentPrincipal, org_id: int) -> None:
    DataScopeFilter.require_detail_access(principal)
    DataScopeFilter.assert_scope_narrowing(
        principal, [int(org_id)], dept_ids=dept_ids(db, principal)
    )


def assert_candidate_allowed(
    db: Session, principal: CurrentPrincipal, user_id: int, org_id: int
) -> None:
    assert_org_allowed(db, principal, org_id)
    if int(principal.data_scope) == int(RoleDataScope.SELF) and int(user_id) != int(principal.user_id):
        raise PermissionDeniedError("候选人超出当前数据范围")


def parse_target_scope(value: Any) -> AssessmentTargetScope:
    try:
        return AssessmentTargetScope.model_validate(value)
    except ValidationError as exc:
        raise BizError(42202, "target_scope 不符合 Batch 2A explicit 契约", http_status=422) from exc


def parse_notify_rules(value: Any) -> AssessmentNotifyRules:
    try:
        return AssessmentNotifyRules.model_validate(value)
    except ValidationError as exc:
        raise BizError(42202, "notify_rules 不符合 Batch 2A 契约", http_status=422) from exc


def visible_task(db: Session, principal: CurrentPrincipal, task_id: int) -> AssessmentTask:
    task = assessment_dao.get_task(
        db, principal.tenant_id, task_id, scope_clause=task_scope(db, principal)
    )
    if task is None:
        raise NotFoundError("测评任务不存在")
    return task


def task_created_notifications(
    tenant_id: int,
    task: AssessmentTask,
    candidates: list[AssessmentTaskCandidate],
    rules: AssessmentNotifyRules,
    key: str,
) -> list[AssessmentNotification]:
    if "task_created" not in rules.notify_on:
        return []
    return [
        AssessmentNotification(
            tenant_id=tenant_id,
            task_id=task.id,
            task_candidate_id=candidate.id,
            channel=channel,
            notification_type="task_created",
            status="pending",
            idempotency_key=f"assessment-task:{key}:{candidate.id}:{channel}",
        )
        for candidate in candidates
        for channel in rules.channels
    ]


def candidate_summary(candidates: list[AssessmentTaskCandidate]) -> AssessmentCandidateSummary:
    total = len(candidates)
    progress = sum(float(row.progress) for row in candidates) / total if total else 0
    return AssessmentCandidateSummary(
        total=total,
        assigned=sum(row.assignment_status == "assigned" for row in candidates),
        started=sum(row.assignment_status == "started" for row in candidates),
        submitted=sum(row.assignment_status == "submitted" for row in candidates),
        expired=sum(row.assignment_status == "expired" for row in candidates),
        withdrawn=sum(row.assignment_status == "withdrawn" for row in candidates),
        progress=round(progress, 2),
        abnormal_count=sum(int(row.abnormal_count) for row in candidates),
    )


def notification_summary(
    notifications: list[AssessmentNotification],
) -> AssessmentNotificationSummary:
    return AssessmentNotificationSummary(
        total=len(notifications),
        pending=sum(row.status == "pending" for row in notifications),
        sent=sum(row.status == "sent" for row in notifications),
        failed=sum(row.status == "failed" for row in notifications),
    )


def to_summary(db: Session, task: AssessmentTask, paper: AssessmentPaper) -> AssessmentTaskSummary:
    candidates = assessment_dao.list_candidates(db, task.tenant_id, task.id)
    summary = candidate_summary(candidates)
    return AssessmentTaskSummary(
        id=task.id,
        task_code=task.task_code,
        name=task.name,
        paper_id=paper.id,
        paper_name=paper.name,
        paper_version_no=paper.version_no,
        start_at=api_time(task.start_at),
        deadline_at=api_time(task.deadline_at),
        duration_min=task.duration_min,
        attempt_limit=task.attempt_limit,
        status=task.status,
        candidate_count=summary.total,
        submitted_count=summary.submitted,
        progress=summary.progress,
        abnormal_count=summary.abnormal_count,
        created_at=api_time(task.created_at),
        updated_at=api_time(task.updated_at),
        row_version=task.row_version,
    )


def to_detail(
    db: Session, task: AssessmentTask, paper: AssessmentPaper
) -> AssessmentTaskDetail:
    candidates = assessment_dao.list_candidates(db, task.tenant_id, task.id)
    notifications = assessment_dao.list_notifications(db, task.tenant_id, task.id)
    target_scope_value = parse_target_scope(task.target_scope)
    notify_rules_value = parse_notify_rules(task.notify_rules)
    return AssessmentTaskDetail(
        id=task.id,
        task_code=task.task_code,
        name=task.name,
        org_id=task.org_id,
        paper_id=paper.id,
        paper_name=paper.name,
        paper_version_no=paper.version_no,
        start_at=api_time(task.start_at),
        deadline_at=api_time(task.deadline_at),
        duration_min=task.duration_min,
        attempt_limit=task.attempt_limit,
        target_scope=target_scope_value.model_dump(mode="json"),
        notify_rules=notify_rules_value.model_dump(mode="json"),
        status=task.status,
        withdrawn_at=api_time(task.withdrawn_at),
        created_at=api_time(task.created_at),
        updated_at=api_time(task.updated_at),
        row_version=task.row_version,
        candidate_summary=candidate_summary(candidates),
        notification_summary=notification_summary(notifications),
    )


def progress_output(
    task: AssessmentTask, candidates: list[AssessmentTaskCandidate]
) -> AssessmentTaskProgress:
    return AssessmentTaskProgress(
        task_id=task.id,
        deadline_at=api_time(task.deadline_at),
        summary=candidate_summary(candidates),
        candidates=[
            AssessmentCandidateProgress(
                id=row.id,
                talent_id=row.talent_id,
                user_id=row.user_id,
                org_id=row.org_id,
                assignment_status=row.assignment_status,
                attempt_count=row.attempt_count,
                progress=float(row.progress),
                abnormal_count=row.abnormal_count,
                deadline_at=api_time(row.deadline_at),
                reminder_status=row.reminder_status,
                last_reminded_at=api_time(row.last_reminded_at),
            )
            for row in candidates
        ],
    )


def reminder_payload(
    notifications: list[AssessmentNotification], task_id: int, channels: list[str]
) -> dict[str, Any]:
    replay_channels = list(dict.fromkeys(str(row.channel) for row in notifications))
    return {
        "task_id": str(task_id),
        "notification_ids": [str(row.id) for row in notifications],
        "notification_count": len(notifications),
        "status": "pending",
        "channels": replay_channels or list(channels),
        "notify_on": ["reminder"],
        "created_at": api_time(datetime.now()),
    }


def reminder_output(
    notifications: list[AssessmentNotification],
    task_id: int,
    channels: list[str],
    created_at: datetime,
) -> AssessmentReminderResponse:
    return AssessmentReminderResponse(
        task_id=task_id,
        notification_ids=[row.id for row in notifications],
        notification_count=len(notifications),
        status="pending",
        channels=channels,
        notify_on=["reminder"],
        created_at=api_time(created_at),
    )


__all__ = [
    "api_time",
    "assert_candidate_allowed",
    "assert_org_allowed",
    "candidate_scope",
    "candidate_summary",
    "notification_summary",
    "parse_notify_rules",
    "parse_target_scope",
    "progress_output",
    "reminder_output",
    "reminder_payload",
    "required_paper",
    "require_idempotency",
    "task_code",
    "task_created_notifications",
    "task_scope",
    "to_detail",
    "to_summary",
    "visible_task",
]
