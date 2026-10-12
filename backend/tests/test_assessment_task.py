"""Batch 2A API-119~124 contract and integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError, PermissionDeniedError
from app.main import create_app
from app.models.assessment import (
    AssessmentNotification,
    AssessmentPaper,
    AssessmentTask,
    AssessmentTaskCandidate,
)
from app.models.enums import RoleDataScope
from app.schemas.assessment import (
    AssessmentTaskCreateRequest,
    AssessmentTaskListQuery,
    AssessmentTaskReminderRequest,
)
from app.services.assessment_task_service import AssessmentTaskService
from tests.conftest import requires_db


TENANT_ID = 7800000000000000001
ORG_ID = 7800000000000000011
OTHER_ORG_ID = 7800000000000000012
PAPER_ID = 7800000000000000101
TASK_ID = 7800000000000000201
TASK_ID_2 = 7800000000000000202
TALENT_ID = 7800000000000000301
USER_ID = 7800000000000000401


def _principal(
    *,
    data_scope: int = int(RoleDataScope.ALL),
    org_id: int | None = ORG_ID,
    dept_ids: tuple[int, ...] = (),
    user_id: int = USER_ID,
    roles: tuple[str, ...] = ("HR",),
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=TENANT_ID,
        org_id=org_id,
        roles=roles,
        data_scope=data_scope,
        dept_ids=dept_ids,
    )


def _create_request(*, paper_id: int = PAPER_ID, key_suffix: str = "1") -> AssessmentTaskCreateRequest:
    now = datetime.now(timezone.utc)
    return AssessmentTaskCreateRequest(
        name="数据治理骨干季度测评",
        paper_id=str(paper_id),
        start_at=now + timedelta(hours=1),
        deadline_at=now + timedelta(days=1),
        duration_min=45,
        attempt_limit=1,
        target_scope={
            "mode": "explicit",
            "candidates": [
                {"talent_id": str(TALENT_ID), "user_id": str(USER_ID), "org_id": str(ORG_ID)}
            ],
        },
        notify_rules={"channels": ["in_app"], "notify_on": ["task_created", "reminder"]},
    )


def _insert_paper(db, *, paper_id: int = PAPER_ID, status: str = "published") -> None:
    db.add(
        AssessmentPaper(
            id=paper_id,
            tenant_id=TENANT_ID,
            org_id=ORG_ID,
            paper_code=f"P-{paper_id}",
            version_no=3,
            name="数据治理能力测评卷",
            paper_type="fixed",
            target_level="3",
            duration_min=60,
            total_score=100,
            rules={"version": "v1"},
            status=status,
            published_at=datetime.now() if status == "published" else None,
        )
    )
    db.flush()


def _cleanup(db) -> None:
    db.execute(delete(AssessmentNotification).where(AssessmentNotification.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentTaskCandidate).where(AssessmentTaskCandidate.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentTask).where(AssessmentTask.tenant_id == TENANT_ID))
    db.execute(delete(AssessmentPaper).where(AssessmentPaper.tenant_id == TENANT_ID))
    db.commit()


@pytest.fixture
def assessment_db(db):
    _cleanup(db)
    try:
        yield db
    finally:
        _cleanup(db)


def test_assessment_create_schema_rejects_non_explicit_target_scope() -> None:
    payload = _create_request().model_dump()
    payload["target_scope"] = {"mode": "department", "department_ids": [str(ORG_ID)]}
    with pytest.raises(ValidationError):
        AssessmentTaskCreateRequest(**payload)


def test_assessment_create_schema_rejects_duplicate_notify_rules() -> None:
    payload = _create_request().model_dump()
    payload["notify_rules"] = {"channels": ["in_app", "in_app"], "notify_on": ["reminder"]}
    with pytest.raises(ValidationError):
        AssessmentTaskCreateRequest(**payload)


def test_assessment_create_schema_rejects_duplicate_candidates_and_invalid_time() -> None:
    payload = _create_request().model_dump()
    payload["target_scope"]["candidates"].append(payload["target_scope"]["candidates"][0])
    with pytest.raises(ValidationError):
        AssessmentTaskCreateRequest(**payload)

    invalid = _create_request().model_dump()
    invalid["duration_min"] = 0
    with pytest.raises(ValidationError):
        AssessmentTaskCreateRequest(**invalid)


@requires_db
def test_api_122_creates_task_candidates_and_notifications(assessment_db) -> None:
    _insert_paper(assessment_db)
    result = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-create-1"
    )
    assert result.status == "draft"
    assert result.paper_name == "数据治理能力测评卷"
    assert result.paper_version_no == 3
    assert assessment_db.scalar(
        select(func.count()).select_from(AssessmentTaskCandidate).where(
            AssessmentTaskCandidate.task_id == int(result.id)
        )
    ) == 1
    assert assessment_db.scalar(
        select(func.count()).select_from(AssessmentNotification).where(
            AssessmentNotification.task_id == int(result.id),
            AssessmentNotification.status == "pending",
        )
    ) == 1


@requires_db
def test_api_122_replays_duplicate_idempotency_key_without_second_task(assessment_db) -> None:
    _insert_paper(assessment_db)
    service = AssessmentTaskService
    first = service.create_task(assessment_db, _principal(), _create_request(), idempotency_key="batch2a-replay")
    with pytest.raises(BizError) as excinfo:
        service.create_task(assessment_db, _principal(), _create_request(), idempotency_key="batch2a-replay")
    assert excinfo.value.code == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert str(first.id) == str(excinfo.value.data["id"])
    assert assessment_db.scalar(select(func.count()).select_from(AssessmentTask)) == 1


@requires_db
def test_api_122_rejects_missing_paper(assessment_db) -> None:
    with pytest.raises(NotFoundError):
        AssessmentTaskService.create_task(
            assessment_db, _principal(), _create_request(paper_id=PAPER_ID + 99), idempotency_key="batch2a-no-paper"
        )


@requires_db
def test_api_119_list_and_api_120_detail_return_paper_name_and_version(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-list"
    )
    items, total = AssessmentTaskService.list_tasks(
        assessment_db, _principal(), AssessmentTaskListQuery(page_no=1, page_size=20)
    )
    detail = AssessmentTaskService.get_task_detail(assessment_db, _principal(), int(created.id))
    assert total == 1
    assert items[0].paper_name == "数据治理能力测评卷"
    assert items[0].paper_version_no == 3
    assert detail.paper_id == created.paper_id
    assert detail.paper_name == "数据治理能力测评卷"
    assert detail.paper_version_no == 3


@requires_db
def test_api_121_returns_candidate_progress_from_authoritative_table(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-progress"
    )
    progress = AssessmentTaskService.get_task_progress(assessment_db, _principal(), int(created.id))
    assert progress.summary.total == 1
    assert progress.summary.assigned == 1
    assert progress.candidates[0].progress == 0


@requires_db
def test_api_123_activates_only_against_published_paper_and_draft_task(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-activate"
    )
    activated = AssessmentTaskService.activate_task(
        assessment_db,
        _principal(),
        int(created.id),
        row_version=created.row_version,
        idempotency_key="batch2a-activate-call",
    )
    assert activated.status == "scheduled"
    assert activated.candidate_count == 1


@requires_db
def test_api_123_rejects_non_draft_and_row_version_conflict(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-state"
    )
    activated = AssessmentTaskService.activate_task(
        assessment_db, _principal(), int(created.id), row_version=created.row_version, idempotency_key="state-1"
    )
    with pytest.raises(IllegalStateError) as state_error:
        AssessmentTaskService.activate_task(
            assessment_db, _principal(), int(created.id), row_version=created.row_version, idempotency_key="state-2"
        )
    assert state_error.value.code == int(ErrorCode.ILLEGAL_STATE)
    second = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-state-second"
    )
    with pytest.raises(BizError) as conflict_error:
        AssessmentTaskService.activate_task(
            assessment_db, _principal(), int(second.id), row_version=999, idempotency_key="state-3"
        )
    assert conflict_error.value.code == int(ErrorCode.CONCURRENT_CONFLICT)


@requires_db
def test_api_123_rejects_missing_or_unpublished_paper_and_missing_candidates(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-paper-status"
    )
    paper = assessment_db.get(AssessmentPaper, PAPER_ID)
    paper.status = "draft"
    paper.published_at = None
    assessment_db.commit()
    with pytest.raises(BizError) as excinfo:
        AssessmentTaskService.activate_task(
            assessment_db, _principal(), int(created.id), row_version=created.row_version, idempotency_key="paper-status"
        )
    assert excinfo.value.code == 42202


@requires_db
def test_api_123_rejects_missing_candidates(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-no-candidate"
    )
    assessment_db.execute(
        delete(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(created.id))
    )
    assessment_db.commit()
    with pytest.raises(BizError) as excinfo:
        AssessmentTaskService.activate_task(
            assessment_db,
            _principal(),
            int(created.id),
            row_version=created.row_version,
            idempotency_key="batch2a-no-candidate-activate",
        )
    assert excinfo.value.code == 42202


@requires_db
def test_api_123_rejects_candidate_outside_data_scope(assessment_db) -> None:
    _insert_paper(assessment_db)
    request = _create_request()
    request.target_scope.candidates[0].org_id = OTHER_ORG_ID
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), request, idempotency_key="batch2a-scope-create"
    )
    with pytest.raises(PermissionDeniedError):
        AssessmentTaskService.activate_task(
            assessment_db,
            _principal(data_scope=int(RoleDataScope.OWN_DEPT), dept_ids=(ORG_ID,)),
            int(created.id),
            row_version=created.row_version,
            idempotency_key="batch2a-scope-activate",
        )


@requires_db
def test_api_124_creates_pending_reminders_and_rejects_cross_task_candidate(assessment_db) -> None:
    _insert_paper(assessment_db)
    first = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-remind-1"
    )
    request = _create_request(key_suffix="2")
    second = AssessmentTaskService.create_task(
        assessment_db, _principal(), request, idempotency_key="batch2a-remind-2"
    )
    first_candidate = assessment_db.scalar(
        select(AssessmentTaskCandidate).where(AssessmentTaskCandidate.task_id == int(first.id))
    )
    with pytest.raises(NotFoundError):
        AssessmentTaskService.remind_task(
            assessment_db,
            _principal(),
            int(second.id),
            AssessmentTaskReminderRequest(candidate_ids=[int(first_candidate.id)], channels=["email"]),
            idempotency_key="batch2a-remind-cross",
        )
    out = AssessmentTaskService.remind_task(
        assessment_db,
        _principal(),
        int(first.id),
        AssessmentTaskReminderRequest(candidate_ids=[], channels=["email"]),
        idempotency_key="batch2a-remind-ok",
    )
    assert out.notification_count == 1
    assert out.status == "pending"


@requires_db
def test_api_124_rejects_candidate_outside_data_scope(assessment_db) -> None:
    _insert_paper(assessment_db)
    request = _create_request()
    request.target_scope.candidates[0].org_id = OTHER_ORG_ID
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), request, idempotency_key="batch2a-remind-scope"
    )
    with pytest.raises(PermissionDeniedError):
        AssessmentTaskService.remind_task(
            assessment_db,
            _principal(data_scope=int(RoleDataScope.OWN_DEPT), dept_ids=(ORG_ID,)),
            int(created.id),
            AssessmentTaskReminderRequest(candidate_ids=[], channels=["email"]),
            idempotency_key="batch2a-remind-scope-key",
        )


@requires_db
def test_api_124_replays_duplicate_request(assessment_db) -> None:
    _insert_paper(assessment_db)
    created = AssessmentTaskService.create_task(
        assessment_db, _principal(), _create_request(), idempotency_key="batch2a-remind-replay"
    )
    request = AssessmentTaskReminderRequest(candidate_ids=[], channels=["email"])
    first = AssessmentTaskService.remind_task(
        assessment_db, _principal(), int(created.id), request, idempotency_key="batch2a-remind-key"
    )
    with pytest.raises(BizError) as excinfo:
        AssessmentTaskService.remind_task(
            assessment_db, _principal(), int(created.id), request, idempotency_key="batch2a-remind-key"
        )
    assert excinfo.value.code == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert excinfo.value.data["notification_ids"] == [str(first.notification_ids[0])]


@requires_db
def test_assessment_router_exposes_frozen_page_names_and_iso_time(assessment_db) -> None:
    _insert_paper(assessment_db)
    app = create_app()
    principal = _principal(roles=("ADM",))

    def _db_override():
        yield assessment_db

    app.dependency_overrides[get_db] = _db_override
    from app.core.security import get_current_principal

    app.dependency_overrides[get_current_principal] = lambda: principal
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assessment/tasks",
            headers={"Idempotency-Key": "batch2a-http-create"},
            json=_create_request().model_dump(mode="json"),
        )
        assert response.status_code == 200
        assert response.json()["data"]["created_at"].endswith("+08:00")
        listed = client.get("/api/v1/assessment/tasks", params={"page_no": 1, "page_size": 20})
        assert listed.status_code == 200
        assert listed.json()["page"] == {"page_no": 1, "page_size": 20, "total": 1}
        assert listed.json()["data"][0]["paper_name"] == "数据治理能力测评卷"
