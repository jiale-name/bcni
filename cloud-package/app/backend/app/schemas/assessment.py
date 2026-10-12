"""Assessment API-119~124 schemas.

The assessment API intentionally has its own response envelope and pagination
types.  The legacy common schemas expose ``page/per_page`` and local datetime
strings, while the frozen assessment contract requires ``page_no/page_size``
and timezone-aware ISO 8601 values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.core.context import get_trace_id
from app.schemas.common import SnowflakeId

AssessmentDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda value: (
            value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if value.tzinfo is None
            else value
        ).isoformat(),
        return_type=str,
        when_used="json",
    ),
]

AssessmentTaskStatus = Literal["draft", "scheduled", "running", "completed", "expired", "archived"]
AssignmentStatus = Literal["assigned", "started", "submitted", "expired", "withdrawn"]
AssessmentAttemptStatus = Literal[
    "not_started", "in_progress", "submitted", "auto_submitted", "cancelled"
]
NotificationStatus = Literal["pending", "sent", "failed"]
NotificationChannel = Literal["in_app", "email"]
NotificationEvent = Literal["task_created", "reminder"]


def _positive_ids(values: list[int], field_name: str) -> list[int]:
    if any(int(value) <= 0 for value in values):
        raise ValueError(f"{field_name} 必须为正的 BIGINT UNSIGNED")
    return values


class AssessmentPage(BaseModel):
    """Assessment-only pagination object exposed at the response top level."""

    model_config = ConfigDict(extra="forbid")

    page_no: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    total: int = Field(0, ge=0)


T = TypeVar("T")


class AssessmentResponse(BaseModel, Generic[T]):
    """Frozen common response envelope with assessment pagination semantics."""

    model_config = ConfigDict(extra="forbid")

    code: int = 0
    message: str = "ok"
    data: T | None = None
    page: AssessmentPage | None = None
    trace_id: str | None = None


def assessment_ok(
    data: Any = None,
    *,
    page: AssessmentPage | None = None,
    message: str = "ok",
    code: int = 0,
) -> AssessmentResponse[Any]:
    return AssessmentResponse(
        code=code,
        message=message,
        data=data,
        page=page,
        trace_id=get_trace_id(),
    )


class AssessmentCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    user_id: SnowflakeId
    org_id: SnowflakeId

    @field_validator("talent_id", "user_id", "org_id")
    @classmethod
    def _positive_id(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("候选人 ID 必须为正的 BIGINT UNSIGNED")
        return int(value)


class AssessmentTargetScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["explicit"]
    candidates: list[AssessmentCandidateInput] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _reject_duplicate_users(self) -> AssessmentTargetScope:
        user_ids = [int(candidate.user_id) for candidate in self.candidates]
        if len(user_ids) != len(set(user_ids)):
            raise ValueError("同一任务内 user_id 不得重复分配")
        return self


class AssessmentNotifyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[NotificationChannel] = Field(..., min_length=1)
    notify_on: list[NotificationEvent] = Field(..., min_length=1)

    @field_validator("channels", "notify_on")
    @classmethod
    def _deduplicate_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("通知配置数组不得重复")
        return values


class AssessmentTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    paper_id: SnowflakeId
    start_at: datetime
    deadline_at: datetime
    duration_min: int = Field(..., gt=0)
    attempt_limit: int = Field(1, ge=1, le=255)
    target_scope: AssessmentTargetScope
    notify_rules: AssessmentNotifyRules

    @field_validator("paper_id")
    @classmethod
    def _paper_id_positive(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("paper_id 必须为正的 BIGINT UNSIGNED")
        return int(value)

    @field_validator("start_at", "deadline_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须为带时区的 ISO 8601 格式")
        return value

    @model_validator(mode="after")
    def _check_time_range(self) -> AssessmentTaskCreateRequest:
        if self.start_at >= self.deadline_at:
            raise ValueError("start_at 必须早于 deadline_at")
        return self


class AssessmentTaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_no: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    keyword: str | None = Field(None, max_length=200)
    paper_id: SnowflakeId | None = None
    position_level: int | None = Field(None, ge=0)
    status: AssessmentTaskStatus | None = None

    @field_validator("paper_id")
    @classmethod
    def _filter_paper_id_positive(cls, value: int | None) -> int | None:
        if value is not None and int(value) <= 0:
            raise ValueError("paper_id 必须为正的 BIGINT UNSIGNED")
        return value


class AssessmentTaskActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(..., gt=0)


class AssessmentTaskReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[SnowflakeId] = Field(default_factory=list)
    channels: list[NotificationChannel] = Field(..., min_length=1)

    @field_validator("candidate_ids")
    @classmethod
    def _candidate_ids_valid(cls, values: list[int]) -> list[int]:
        _positive_ids(values, "candidate_ids")
        if len(values) != len(set(values)):
            raise ValueError("candidate_ids 不得重复")
        return values

    @field_validator("channels")
    @classmethod
    def _channels_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("channels 不得重复")
        return values


class AssessmentCandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    assigned: int = 0
    started: int = 0
    submitted: int = 0
    expired: int = 0
    withdrawn: int = 0
    progress: float = 0
    abnormal_count: int = 0


class AssessmentNotificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    pending: int = 0
    sent: int = 0
    failed: int = 0


class AssessmentTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_code: str
    name: str
    paper_id: SnowflakeId
    paper_name: str
    paper_version_no: int
    start_at: AssessmentDateTime
    deadline_at: AssessmentDateTime
    duration_min: int
    attempt_limit: int
    status: AssessmentTaskStatus
    candidate_count: int = 0
    submitted_count: int = 0
    progress: float = 0
    abnormal_count: int = 0
    created_at: AssessmentDateTime
    updated_at: AssessmentDateTime
    row_version: int


class AssessmentTaskDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_code: str
    name: str
    org_id: SnowflakeId
    paper_id: SnowflakeId
    paper_name: str
    paper_version_no: int
    start_at: AssessmentDateTime
    deadline_at: AssessmentDateTime
    duration_min: int
    attempt_limit: int
    target_scope: dict[str, Any]
    notify_rules: dict[str, Any]
    status: AssessmentTaskStatus
    withdrawn_at: AssessmentDateTime | None = None
    created_at: AssessmentDateTime
    updated_at: AssessmentDateTime
    row_version: int
    candidate_summary: AssessmentCandidateSummary
    notification_summary: AssessmentNotificationSummary


class AssessmentCandidateProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    talent_id: SnowflakeId
    user_id: SnowflakeId
    org_id: SnowflakeId
    assignment_status: AssignmentStatus
    attempt_count: int
    progress: float
    abnormal_count: int
    deadline_at: AssessmentDateTime
    reminder_status: str
    last_reminded_at: AssessmentDateTime | None = None


class AssessmentTaskProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    deadline_at: AssessmentDateTime
    summary: AssessmentCandidateSummary
    candidates: list[AssessmentCandidateProgress]


class AssessmentTaskActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_code: str
    status: Literal["scheduled", "running"]
    start_at: AssessmentDateTime
    deadline_at: AssessmentDateTime
    candidate_count: int
    notification_summary: AssessmentNotificationSummary
    updated_at: AssessmentDateTime
    row_version: int


class AssessmentReminderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    notification_ids: list[SnowflakeId]
    notification_count: int
    status: Literal["pending"]
    channels: list[NotificationChannel]
    notify_on: list[Literal["reminder"]]
    created_at: AssessmentDateTime


class AssessmentAttemptReminderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_id: SnowflakeId
    notification_type: str
    status: Literal["pending", "sent", "failed"]


class AssessmentAttemptRemindersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: SnowflakeId
    notifications: list[AssessmentAttemptReminderItem]


class AssessmentAttemptProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_questions: int = Field(..., ge=0)
    answered_count: int = Field(..., ge=0)
    unanswered_count: int = Field(..., ge=0)
    progress: int = Field(..., ge=0, le=100)
    current_question_id: SnowflakeId | None = None
    last_saved_at: AssessmentDateTime | None
    deadline_at: AssessmentDateTime
    server_now: AssessmentDateTime


class AssessmentEventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "visibility_hidden",
        "paste",
        "idle",
        "refresh",
        "submit_after_deadline",
        "device_change",
    ]
    occurred_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    metadata_summary: dict[str, Any]
    client_event_id: str = Field(..., min_length=1, max_length=64)


class AssessmentEventCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: SnowflakeId


class AssessmentAttemptStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId

    @field_validator("task_id")
    @classmethod
    def _task_id_positive(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("task_id 必须为正的 BIGINT UNSIGNED")
        return int(value)


class AssessmentQuestionNavigationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: SnowflakeId
    question_order: int = Field(..., ge=1)
    answered: bool


class AssessmentSavedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: SnowflakeId
    answer: dict[str, Any]
    answer_version: int = Field(..., ge=1)


class AssessmentQuestionNavigation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_question_id: SnowflakeId
    total_questions: int = Field(..., ge=0)
    answered_count: int = Field(..., ge=0)
    questions: list[AssessmentQuestionNavigationItem]


class AssessmentAttemptStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: SnowflakeId
    task_id: SnowflakeId
    status: Literal["in_progress"]
    attempt_no: int = Field(..., ge=1)
    answer_version: int = Field(..., ge=0)
    server_now: AssessmentDateTime
    duration_min_snapshot: int = Field(..., ge=1)
    deadline_at: AssessmentDateTime
    question_navigation: AssessmentQuestionNavigation
    saved_answers: list[AssessmentSavedAnswer]


class AssessmentAttemptDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: SnowflakeId
    task_id: SnowflakeId
    attempt_no: int = Field(..., ge=1)
    status: AssessmentAttemptStatus
    answer_version: int = Field(..., ge=0)
    started_at: AssessmentDateTime | None
    duration_min_snapshot: int = Field(..., ge=1)
    deadline_at_snapshot: AssessmentDateTime
    submitted_at: AssessmentDateTime | None
    server_now: AssessmentDateTime
    progress: int = Field(..., ge=0, le=100)


class AssessmentQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    text: str
    order: int = Field(..., ge=1)


class AssessmentAnswerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answered: bool
    answer: dict[str, Any]


class AssessmentQuestionSnapshotItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: SnowflakeId
    question_version: int = Field(..., ge=1)
    question_type: str
    content: str
    options: list[AssessmentQuestionOption]
    score: float
    question_order: int = Field(..., ge=1)
    answer_summary: AssessmentAnswerSummary


class AssessmentQuestionSnapshotPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AssessmentQuestionSnapshotItem]
    total: int = Field(..., ge=0)


class AssessmentUnansweredQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: SnowflakeId
    question_order: int = Field(..., ge=1)


class AssessmentSubmitCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_submit: bool
    unanswered_count: int = Field(..., ge=0)
    unanswered_questions: list[AssessmentUnansweredQuestion]
    server_now: AssessmentDateTime
    deadline_at: AssessmentDateTime
    timeout: bool
    blocking_reasons: list[str]


class AssessmentSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool


class AssessmentSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: SnowflakeId
    status: Literal["submitted"]
    submitted_at: AssessmentDateTime
    outbox_event_id: SnowflakeId


class AssessmentAnswerValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any

    @field_validator("value")
    @classmethod
    def _allowed_value(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (str, int, float, bool, list)):
            raise ValueError("answer.value 只允许 string、number、boolean、array 或 null")
        return value


class AssessmentSaveAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: AssessmentAnswerValue
    answer_version: int = Field(..., ge=0)
    client_saved_at: datetime | None = None

    @field_validator("client_saved_at")
    @classmethod
    def _client_time_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("client_saved_at 必须为带时区的 ISO 8601 时间")
        return value


class AssessmentSaveAnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: SnowflakeId
    paper_question_id: SnowflakeId
    answer_version: int = Field(..., ge=1)
    saved_at: AssessmentDateTime
    progress: int = Field(..., ge=0, le=100)


__all__ = [
    "AssessmentCandidateInput",
    "AssessmentCandidateProgress",
    "AssessmentCandidateSummary",
    "AssessmentDateTime",
    "AssessmentAnswerSummary",
    "AssessmentAnswerValue",
    "AssessmentAttemptDetailResponse",
    "AssessmentEventCreateRequest",
    "AssessmentEventCreateResponse",
    "AssessmentAttemptProgressResponse",
    "AssessmentAttemptReminderItem",
    "AssessmentAttemptRemindersResponse",
    "AssessmentAttemptStartRequest",
    "AssessmentAttemptStartResponse",
    "AssessmentAttemptStatus",
    "AssessmentNotifyRules",
    "AssessmentNotificationSummary",
    "AssessmentPage",
    "AssessmentQuestionNavigation",
    "AssessmentQuestionNavigationItem",
    "AssessmentQuestionOption",
    "AssessmentQuestionSnapshotItem",
    "AssessmentQuestionSnapshotPage",
    "AssessmentSubmitCheckResponse",
    "AssessmentSubmitRequest",
    "AssessmentSubmitResponse",
    "AssessmentReminderResponse",
    "AssessmentResponse",
    "AssessmentSaveAnswerRequest",
    "AssessmentSaveAnswerResponse",
    "AssessmentSavedAnswer",
    "AssessmentUnansweredQuestion",
    "AssessmentTargetScope",
    "AssessmentTaskActivateRequest",
    "AssessmentTaskActivation",
    "AssessmentTaskCreateRequest",
    "AssessmentTaskDetail",
    "AssessmentTaskListQuery",
    "AssessmentTaskProgress",
    "AssessmentTaskReminderRequest",
    "AssessmentTaskStatus",
    "AssessmentTaskSummary",
    "assessment_ok",
]
