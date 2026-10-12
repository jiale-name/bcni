"""Assessment Task API-119~124 router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.assessment import (
    AssessmentPage,
    AssessmentResponse,
    AssessmentAttemptDetailResponse,
    AssessmentAttemptProgressResponse,
    AssessmentAttemptRemindersResponse,
    AssessmentAttemptStartRequest,
    AssessmentAttemptStartResponse,
    AssessmentEventCreateRequest,
    AssessmentEventCreateResponse,
    AssessmentSaveAnswerRequest,
    AssessmentSaveAnswerResponse,
    AssessmentSubmitRequest,
    AssessmentSubmitResponse,
    AssessmentQuestionSnapshotItem,
    AssessmentSubmitCheckResponse,
    AssessmentTaskActivateRequest,
    AssessmentTaskActivation,
    AssessmentTaskCreateRequest,
    AssessmentTaskDetail,
    AssessmentTaskListQuery,
    AssessmentTaskProgress,
    AssessmentTaskReminderRequest,
    AssessmentReminderResponse,
    AssessmentTaskSummary,
    assessment_ok,
)
from app.services.assessment_task_service import AssessmentTaskService
from app.services.assessment_attempt_service import AssessmentAttemptService


router = APIRouter(tags=["Q · 人才测评任务 (API-119~124)"])
DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]


def _param_invalid(exc: ValidationError) -> BizError:
    return BizError(
        ErrorCode.PARAM_INVALID,
        ERROR_META[ErrorCode.PARAM_INVALID].message,
        {"fields": validation_fields(exc.errors())},
    )


def _idempotency_header(
    value: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=64,
            description="写接口幂等键；重复请求返回首次结果并使用 40903",
        ),
    ] = None,
) -> str | None:
    return value.strip() if value else None


def _list_query(
    page_no: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=200)] = None,
    paper_id: Annotated[int | None, Query(gt=0)] = None,
    position_level: Annotated[int | None, Query(ge=0)] = None,
    status: Annotated[str | None, Query()] = None,
) -> AssessmentTaskListQuery:
    try:
        return AssessmentTaskListQuery(
            page_no=page_no,
            page_size=page_size,
            keyword=keyword,
            paper_id=paper_id,
            position_level=position_level,
            status=status,
        )
    except ValidationError as exc:
        raise _param_invalid(exc) from exc


@router.get(
    "/assessment/tasks",
    response_model=AssessmentResponse[list[AssessmentTaskSummary]],
    dependencies=[Depends(require_perm("assessment:task:list"))],
    summary="API-119 测评任务列表",
)
def list_assessment_tasks(
    principal: PrincipalDep,
    db: DbDep,
    query: AssessmentTaskListQuery = Depends(_list_query),
) -> AssessmentResponse[list[AssessmentTaskSummary]]:
    items, total = AssessmentTaskService.list_tasks(db, principal, query)
    return assessment_ok(
        items,
        page=AssessmentPage(page_no=query.page_no, page_size=query.page_size, total=total),
    )


@router.get(
    "/assessment/tasks/{task_id}",
    response_model=AssessmentResponse[AssessmentTaskDetail],
    dependencies=[Depends(require_perm("assessment:task:list"))],
    summary="API-120 测评任务详情",
)
def get_assessment_task(
    task_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentTaskDetail]:
    return assessment_ok(AssessmentTaskService.get_task_detail(db, principal, task_id))


@router.get(
    "/assessment/tasks/{task_id}/progress",
    response_model=AssessmentResponse[AssessmentTaskProgress],
    dependencies=[Depends(require_perm("assessment:task:monitor"))],
    summary="API-121 测评任务候选人进度",
)
def get_assessment_task_progress(
    task_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentTaskProgress]:
    return assessment_ok(AssessmentTaskService.get_task_progress(db, principal, task_id))


@router.post(
    "/assessment/tasks",
    response_model=AssessmentResponse[AssessmentTaskDetail],
    dependencies=[Depends(require_perm("assessment:task:create"))],
    summary="API-122 创建测评任务",
)
def create_assessment_task(
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentTaskCreateRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentTaskDetail]:
    return assessment_ok(
        AssessmentTaskService.create_task(
            db, principal, request, idempotency_key=idempotency_key
        )
    )


@router.post(
    "/assessment/tasks/{task_id}/activate",
    response_model=AssessmentResponse[AssessmentTaskActivation],
    dependencies=[Depends(require_perm("assessment:task:create"))],
    summary="API-123 激活测评任务",
)
def activate_assessment_task(
    task_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentTaskActivateRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentTaskActivation]:
    return assessment_ok(
        AssessmentTaskService.activate_task(
            db,
            principal,
            task_id,
            row_version=request.row_version,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/assessment/tasks/{task_id}/remind",
    response_model=AssessmentResponse[AssessmentReminderResponse],
    dependencies=[Depends(require_perm("assessment:task:monitor"))],
    summary="API-124 提醒测评候选人",
)
def remind_assessment_task(
    task_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentTaskReminderRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentReminderResponse]:
    return assessment_ok(
        AssessmentTaskService.remind_task(
            db, principal, task_id, request, idempotency_key=idempotency_key
        )
    )


@router.post(
    "/assessment/attempts",
    response_model=AssessmentResponse[AssessmentAttemptStartResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-131 开始作答",
)
def start_assessment_attempt(
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentAttemptStartRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentAttemptStartResponse]:
    return assessment_ok(
        AssessmentAttemptService.start_attempt(
            db, principal, request.task_id, idempotency_key=idempotency_key
        )
    )


@router.get(
    "/assessment/attempts/{attempt_id}",
    response_model=AssessmentResponse[AssessmentAttemptDetailResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-132 Attempt 详情",
)
def get_assessment_attempt(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentAttemptDetailResponse]:
    return assessment_ok(AssessmentAttemptService.get_attempt_detail(db, principal, attempt_id))


@router.get(
    "/assessment/attempts/{attempt_id}/reminders",
    response_model=AssessmentResponse[AssessmentAttemptRemindersResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-135 作答提醒",
)
def list_assessment_attempt_reminders(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentAttemptRemindersResponse]:
    return assessment_ok(
        AssessmentAttemptService.list_attempt_reminders(db, principal, attempt_id)
    )


@router.get(
    "/assessment/attempts/{attempt_id}/progress",
    response_model=AssessmentResponse[AssessmentAttemptProgressResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-136 作答进度",
)
def get_assessment_attempt_progress(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentAttemptProgressResponse]:
    return assessment_ok(
        AssessmentAttemptService.get_attempt_progress(db, principal, attempt_id)
    )


@router.post(
    "/assessment/attempts/{attempt_id}/events",
    response_model=AssessmentResponse[AssessmentEventCreateResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-139 作答监控事件",
)
def create_assessment_attempt_event(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentEventCreateRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentEventCreateResponse]:
    return assessment_ok(
        AssessmentAttemptService.create_event(
            db,
            principal,
            attempt_id,
            event_type=request.event_type,
            occurred_at=request.occurred_at,
            duration_ms=request.duration_ms,
            metadata_summary=request.metadata_summary,
            client_event_id=request.client_event_id,
            idempotency_key=idempotency_key,
        )
    )


@router.get(
    "/assessment/attempts/{attempt_id}/questions",
    response_model=AssessmentResponse[list[AssessmentQuestionSnapshotItem]],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-133 题目快照",
)
def list_assessment_attempt_questions(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    page_no: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AssessmentResponse[list[AssessmentQuestionSnapshotItem]]:
    result = AssessmentAttemptService.list_attempt_questions(
        db,
        principal,
        attempt_id,
        page_no=page_no,
        page_size=page_size,
    )
    return assessment_ok(
        result.items,
        page=AssessmentPage(page_no=page_no, page_size=page_size, total=result.total),
    )


@router.post(
    "/assessment/attempts/{attempt_id}/submit/check",
    response_model=AssessmentResponse[AssessmentSubmitCheckResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-137 提交前检查",
)
def check_assessment_attempt_submission(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
) -> AssessmentResponse[AssessmentSubmitCheckResponse]:
    return assessment_ok(
        AssessmentAttemptService.check_submission(db, principal, attempt_id)
    )


@router.post(
    "/assessment/attempts/{attempt_id}/submit",
    response_model=AssessmentResponse[AssessmentSubmitResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-138 提交作答",
)
def submit_assessment_attempt(
    attempt_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentSubmitRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentSubmitResponse]:
    return assessment_ok(
        AssessmentAttemptService.submit_attempt(
            db,
            principal,
            attempt_id,
            confirm=request.confirm,
            idempotency_key=idempotency_key,
        )
    )


@router.put(
    "/assessment/attempts/{attempt_id}/answers/{paper_question_id}",
    response_model=AssessmentResponse[AssessmentSaveAnswerResponse],
    dependencies=[Depends(require_perm("assessment:attempt:take"))],
    summary="API-134 保存答案",
)
def save_assessment_answer(
    attempt_id: Annotated[int, Path(gt=0)],
    paper_question_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    db: DbDep,
    request: AssessmentSaveAnswerRequest = Body(...),
    idempotency_key: str | None = Depends(_idempotency_header),
) -> AssessmentResponse[AssessmentSaveAnswerResponse]:
    return assessment_ok(
        AssessmentAttemptService.save_answer(
            db,
            principal,
            attempt_id,
            paper_question_id,
            answer=request.answer.model_dump(mode="json"),
            answer_version=request.answer_version,
            idempotency_key=idempotency_key,
        )
    )


__all__ = ["router"]
