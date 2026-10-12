"""分层复核契约（CU-16，API-44~49，模块 H「复核工作台」，F-3.3 / F-3.3.1）。

🔴 **AC-20/21 的落点**：``raw_value`` / ``parsed_value`` 在序列化层按角色脱敏 ——
MGR（及更窄角色）看到 ``138****8888``；HR/ADM 明文（业务必需）。见 ``mask_for_role``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ParseReviewStatus
from app.schemas.common import CommonResp, DateTimeStr, SnowflakeId

#: 影响面（`talent_parse_field.impact_level` 的取值域；HIGH/LOW 而非 0/1 —— 出参可读）
ImpactLevel = Literal["HIGH", "LOW", "ALL"]

#: API-46 的单批上限（接口 §4.2：>200 须显式 force）
CONFIRM_BATCH_LIMIT = 200

#: API-46 的 items 上限（请求体本身，不含 force 逃生舱）
CONFIRM_ITEMS_HARD_LIMIT = 500


def review_status_name_of(status: int | None) -> str | None:
    return ParseReviewStatus.name_of(status) if status is not None else None


def mask_for_role(field_name: str, value: str | None, *, can_see_pii: bool) -> str | None:
    """字段级脱敏（序列化层，AC-20/21）。

    - ``can_see_pii=True``（HR/ADM）→ 明文；
    - 否则 → 按 PII 字段脱敏（手机 ``138****8888`` / 邮箱 ``z***@x.com``）；
      **非 PII 字段不受角色影响**（工作年限对谁都不敏感）。
    """
    if value is None or can_see_pii:
        return value
    from app.core.crypto import mask_email, mask_phone  # 局部导入：横切层不出现在契约依赖

    if field_name == "phone":
        return mask_phone(value)
    if field_name == "email":
        return mask_email(value)
    return value


class ReviewQueueItem(BaseModel):
    """API-44 队列行（原文 ↔ 抽取值并排的最小单元）。"""

    model_config = ConfigDict(extra="forbid")

    field_id: SnowflakeId
    talent_id: SnowflakeId
    talent_name: str
    field_code: str
    field_name: str
    raw_value: str | None
    parsed_value: str | None
    confidence: float | None = None
    impact_level: str
    impact_level_name: str
    review_status: int
    review_status_name: str | None
    source_file: str | None = None
    source_locator: str | None = None


class ReviewQueueMeta(BaseModel):
    """API-44 的 ``meta``（非阻断提示挂这里，``code`` 仍为 0）。"""

    model_config = ConfigDict(extra="forbid")

    high_impact_total: int = 0
    low_impact_sampled_total: int = 0
    warning: str | None = None
    estimated_minutes: int | None = None


class ReviewQueueResp(CommonResp[list[ReviewQueueItem]]):
    """API-44 的专用响应壳：``meta`` 与 ``data``/``page`` 同级（接口 §4.2 示例）。

    ⚠️ ``data`` 是**列表**（队列行数组）；泛型参数必须是
    ``list[ReviewQueueItem]`` 而不是单个 ``ReviewQueueItem``。
    ⚠️ 不改全局 :class:`CommonResp`（加 meta 会让所有接口的响应多一个 ``null`` 键，
    破坏既有契约断言）—— 只有队列接口需要 meta，故**局部扩展**。
    """

    model_config = ConfigDict(extra="forbid")

    meta: ReviewQueueMeta | None = None


class ReviewDetailOut(ReviewQueueItem):
    """API-45 单条复核详情（当前与队列行同构，独立类型留出扩展位）。"""


class ReviewConfirmItem(BaseModel):
    """API-46 的 ``items[]`` 元素。"""

    model_config = ConfigDict(extra="forbid")

    field_id: SnowflakeId
    action: Literal["ACCEPT_AI", "ACCEPT_RAW", "EDIT"]
    value: str | None = Field(None, max_length=512)

    @model_validator(mode="after")
    def _check(self) -> "ReviewConfirmItem":
        if self.action == "EDIT" and not (self.value or "").strip():
            raise ValueError("action=EDIT 时 value 必填")
        return self


class ReviewConfirmOut(BaseModel):
    """API-46 出参（**部分成功**：并发冲突不整批回滚）。"""

    model_config = ConfigDict(extra="forbid")

    confirmed_count: int
    corrected_count: int
    skipped_count: int
    conflicted: list[dict[str, str | int]]
    change_log_ids: list[SnowflakeId]


class ReviewCorrectOut(BaseModel):
    """API-47 / API-48 出参。"""

    model_config = ConfigDict(extra="forbid")

    field_id: SnowflakeId
    talent_id: SnowflakeId
    review_status: int
    review_status_name: str | None
    change_log_id: SnowflakeId | None = None


class ReviewDashboardOut(BaseModel):
    """API-49 复核工作量看板。"""

    model_config = ConfigDict(extra="forbid")

    pending_total: int
    confirmed_total: int
    corrected_total: int
    skipped_total: int
    by_field: list[dict[str, str | int | float]]
    estimated_remaining_minutes: int


# ============================================================================
# Router 侧查询参数复核模型
# ============================================================================
class ReviewQueueFilter(BaseModel):
    """API-44 查询参数（Router 收 Query 后走 ``model_validate`` 复核）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId | None = None
    impact_level: ImpactLevel = "HIGH"
    confidence_max: float | None = Field(None, ge=0, le=1)
    field_codes: str | None = None
    sample_only: bool = False

    @field_validator("field_codes")
    @classmethod
    def _clip_codes(cls, v: str | None) -> str | None:
        if not v:
            return None
        codes = [c.strip() for c in v.split(",") if c.strip()][:10]  # 接口 §4.2：≤10
        return ",".join(codes) if codes else None

    @property
    def code_list(self) -> list[str]:
        return [c for c in (self.field_codes or "").split(",") if c]


class ReviewSkipRequest(BaseModel):
    """API-48 请求体（预留备注字段）。"""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(None, max_length=200)


def estimated_minutes(count: int, *, seconds_per_item: float) -> int:
    """待复核数 × 平均单条耗时（接口 §4.2 口径，秒 → 分钟向上取整）。"""
    if count <= 0:
        return 0
    import math

    return max(1, math.ceil(count * seconds_per_item / 60))


def reviewed_at_name(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None
