"""简历导入契约（CU-14，API-36~43，模块 G「导入」，F-3.1 / F-3.8）。

🔴 **CR-5**：全部 ID 用 :data:`~app.schemas.common.SnowflakeId`（响应为字符串）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ImportFileParseStatus, ImportTaskStatus
from app.schemas.common import DateTimeStr, SnowflakeId

#: 支持的简历扩展名（接口 API-36：pdf/doc/docx/jpg/png/zip）
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "doc", "docx", "jpg", "jpeg", "png", "zip"})


class ImportCreateOut(BaseModel):
    """API-36 创建导入任务的出参。"""

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    task_no: str
    status: int
    status_name: str | None
    total_count: int
    #: 单批恰好达到上限时的非阻断提示（接口 §4.1 示例）
    warn: str | None = None


class ImportTaskOut(BaseModel):
    """API-37 / API-38 任务行（列表与详情共用，详情多三个聚合字段）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    task_no: str
    status: int
    status_name: str | None
    parse_strategy: str | None = None
    total_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    dup_pair_count: int = 0  # CU-19 落地前恒为 0（契约先占位，防前端字段缺列）
    review_pending_count: int = 0
    review_high_impact_count: int = 0
    review_low_impact_sampled_count: int = 0
    field_complete_rate: float | None = None
    progress_pct: int = 0
    agent_task_id: SnowflakeId | None = None
    remark: str | None = None
    created_by: SnowflakeId | None = None
    created_by_name: str | None = None
    started_at: DateTimeStr | None = None
    finished_at: DateTimeStr | None = None
    created_at: DateTimeStr | None = None


class ImportFileOut(BaseModel):
    """API-40 文件明细行。"""

    model_config = ConfigDict(extra="forbid")

    file_id: SnowflakeId
    resume_id: int
    file_name: str
    file_ext: str | None = None
    file_size: int = 0
    file_hash: str | None = None
    parse_status: int
    parse_status_name: str | None = None
    parse_error: str | None = None
    talent_id: SnowflakeId | None = None
    agent_task_id: SnowflakeId | None = None
    ocr_used: bool = False
    created_at: DateTimeStr | None = None


class FailureReasonOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str
    reason_name: str
    count: int


class FieldCompletenessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_code: str
    field_name: str
    filled: int
    total: int
    rate: float


class ImportReportOut(BaseModel):
    """API-41 数据质量报告。

    🔴 **不含解析准确率**（接口 §4.1 备注）：准确率必须由金标准集（IDX-02）离线评估，
    接口内联准确率会让前端误以为是可实时计算的指标 —— 由测试钉死。
    """

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    task_no: str
    summary: dict[str, object]
    field_completeness: list[FieldCompletenessOut]
    failure_reasons: list[FailureReasonOut]
    accuracy_note: str
    generated_at: DateTimeStr


class ImportReportExportOut(BaseModel):
    """API-42 导出（CSV 文本走统一响应壳，与 API-29 同口径）。"""

    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str
    row_count: int
    generated_at: DateTimeStr


class ImportRetryOut(BaseModel):
    """API-43 失败重试的出参。"""

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    retry_count: int
    retryable_count: int
    message: str | None = None


# ============================================================================
# SSE 事件负载（API-39）
# ============================================================================
class ImportProgressEvent(BaseModel):
    """SSE ``event: progress`` 的 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: int
    status_name: str | None
    done: int
    total: int
    progress_pct: int


def status_name_of(status: int | None) -> str | None:
    return ImportTaskStatus.name_of(status) if status is not None else None


def parse_status_name_of(status: int | None) -> str | None:
    return ImportFileParseStatus.name_of(status) if status is not None else None


# ============================================================================
# 内部：表单解析（multipart 不能用 Pydantic 直接收，Router 收原始字段后走这里复核）
# ============================================================================
class ImportCreateForm(BaseModel):
    """API-36 表单的业务字段复核（文件数量与大小校验在 Router / Service）。"""

    model_config = ConfigDict(extra="forbid")

    auto_dedup: bool = True
    auto_tag: bool = True
    parse_strategy: Literal["auto", "ocr_force", "text_only"] = "auto"
    remark: str | None = None

    @field_validator("remark")
    @classmethod
    def _clip_remark(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v[:200] if v else None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.remark is not None and len(self.remark) == 0:
            self.remark = None
        return self
