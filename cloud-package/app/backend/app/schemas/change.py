"""变更留痕契约（CU-22，API-67~70，模块 M「变更留痕与版本回溯」，F-3.10）。

接口 §2.2 只给了这四个接口的一行摘要（方法 / 路径 / 权限 / 表），详规里只有 API-69
有示例响应 —— 与 CU-05/07/08 的情况相同：**本文件即这四个接口的契约事实来源**，
沿用已确立的约定：雪花 ID 一律字符串、时间 ``YYYY-MM-DD HH:mm:ss``、
未填字段下发 ``null``、请求体 ``extra="forbid"``。

🔴 三条口径（写在这里而不是散在实现里）
=====================================

1. **时间窗上限 90 天**（同审计日志 §2.2 的约定）—— 留痕 10⁷ 量级，
   ``created_at`` 上只有 ``(tenant_id, talent_id, created_at)`` 复合索引；
   不设上限的区间查询退化为全租户扫描。
2. **``field_name`` 与 ``change_source`` 都做受控校验**（非法取值 → 40001 且列出合法值）——
   留痕是自由文本列，「拼错一个字母 → 查询成功返回 0 条」是本项目已登记过的
   最难发现的一类问题（同 CU-08 的 ``action`` 词表）。**筛选值**被拒绝，
   但**历史数据里已废弃的取值仍能正常读出**（``label`` 为 ``null``）。
3. **``untracked_fields`` 不是错误**（API-69）—— 它是「该字段历史不完整」的**显式声明**。
   接口原文：「若某字段无任何留痕，则取当前值并列入 ``untracked_fields[]``，
   说明该字段变更历史不完整」。❌ 不要把它当成「查询失败」在前端报错。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.privacy import (
    CORRECTABLE_FIELDS,
    MASKED_FIELD_CODES,
    TRACKABLE_FIELDS,
    field_label,
)
from app.models.enums import ChangeSource, TalentDataStatus
from app.schemas.common import DateTimeStr, SnowflakeId

#: 时间窗跨度上限（天），口径同审计日志（``schemas/audit.MAX_RANGE_DAYS``）
MAX_RANGE_DAYS = 90

#: ``field_name`` 的合法筛选值 = 可回溯字段 + 留痕里确实会出现的其它字段。
#: ⚠️ ``data_status`` 在表内（档案状态流转），``tags`` 由标签接口写入（CU-17 走审计），
#: 故这里**只含**真正会落在 ``talent_change_log`` 里的字段。
#:
#: 🔴 CU-22 补 ``phone`` / ``email``：**更正申请采纳时会给它们写留痕**
#: （见 ``PrivacyService.resolve_correction``），若词表不含这两项，
#: 那两条留痕就只能「在不过滤的列表里恰好看到」，**无法按字段筛选** ——
#: 这正是本文件开头第 2 条口径要防的那类「拼错了所以查不到」的孪生问题
#: （这次是「拼对了但词表里没有」）。
#: ⚠️ 它们**不参与快照回溯**（:data:`~app.core.privacy.TRACKABLE_FIELDS` 不含 PII）——
#: 「能被筛选」与「能被回溯」是两件事，不要因为它们出现在这里就以为
#: ``snapshot`` 会回退手机号（那是刻意的：快照出参不下发 PII）。
CHANGE_FIELD_CODES: tuple[str, ...] = (
    *TRACKABLE_FIELDS,
    *[c for c in MASKED_FIELD_CODES if c not in TRACKABLE_FIELDS],
)


def change_source_name_of(source: int | None) -> str | None:
    return ChangeSource.name_of(source) if source is not None else None


def talent_status_name_of(status: int | None) -> str | None:
    return TalentDataStatus.name_of(status) if status is not None else None


# ============================================================================
# 出参：单条留痕
# ============================================================================
class ChangeLogOut(BaseModel):
    """一条档案变更留痕（API-67/68/70 共用）。

    🔴 ``before_value`` / ``after_value`` 是**原文**（不做脱敏、不做截断展示外的处理）：
    它们要么来自解析流水线（姓名/学历/年限等非密字段），要么来自人工复核；
    PII 的**密文**（``phone_enc``）从不写入留痕（写入侧写的是脱敏/明文业务值）。
    若后续出现敏感值写入，应在**写入侧**治理，而不是在展示侧过滤 ——
    「导出时过滤」会让同一个值在两个入口显示不同，掩盖真正的问题（同 CU-08 的取舍）。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="留痕 ID")
    talent_id: SnowflakeId = Field(..., description="档案 ID")
    talent_name: str | None = Field(None, description="档案姓名（批量反查；已删档案亦有值）")
    change_source: int = Field(..., description="来源 0 AI 解析 1 人工复核 2 合并 3 重解析")
    change_source_name: str | None = Field(None, description="来源中文名")
    field_name: str | None = Field(None, description="变更字段（整体变更时为 null）")
    field_label: str | None = Field(None, description="字段中文名")
    before_value: str | None = Field(None, description="变更前值")
    after_value: str | None = Field(None, description="变更后值")
    snapshot_ref: str | None = Field(None, description="完整快照引用（合并/删除等大范围变更）")
    operator_id: SnowflakeId | None = Field(None, description="操作人（AI 触发为 null）")
    operator_name: str | None = Field(None, description="操作人账号")
    task_id: SnowflakeId | None = Field(None, description="关联 Agent / 导入任务 ID")
    created_at: DateTimeStr = Field(..., description="变更时间")


# ============================================================================
# 入参：筛选（API-67/68/70 共用）
# ============================================================================
class ChangeFilterRequest(BaseModel):
    """变更留痕筛选条件。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId | None = Field(None, description="按档案筛选")
    field_name: str | None = Field(None, max_length=64, description="按字段筛选（受控词表）")
    change_source: int | None = Field(None, ge=0, le=3, description="按来源筛选 0~3")
    operator_id: SnowflakeId | None = Field(None, description="按操作人筛选")
    task_id: SnowflakeId | None = Field(None, description="按关联任务筛选")
    start_time: datetime | None = Field(None, description="起始时间（含）")
    end_time: datetime | None = Field(None, description="截止时间（含）")

    @field_validator("field_name", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_vocabulary(self) -> ChangeFilterRequest:
        if self.field_name and self.field_name not in CHANGE_FIELD_CODES:
            raise ValueError(
                f"未知的 field_name={self.field_name!r}；"
                f"合法取值：{', '.join(CHANGE_FIELD_CODES)}"
            )
        return self

    @model_validator(mode="after")
    def _check_range(self) -> ChangeFilterRequest:
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time 不能晚于 end_time")
        if self.start_time and self.end_time:
            span = (self.end_time - self.start_time).days
            if span > MAX_RANGE_DAYS:
                raise ValueError(
                    f"时间跨度不得超过 {MAX_RANGE_DAYS} 天（当前 {span} 天），请收窄后重试"
                )
        return self


# ============================================================================
# API-69 时间点快照回溯
# ============================================================================
class SnapshotTalentOut(BaseModel):
    """快照中的档案字段（**只含可回溯字段**，不含 PII 密文列）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="档案 ID")
    name: str | None = Field(None, description="姓名")
    gender: int | None = Field(None, description="性别 0 未知 1 男 2 女")
    birth_date: str | None = Field(None, description="出生年月（YYYY-MM-DD）")
    education_level: str | None = Field(None, description="学历（字典码）")
    school: str | None = Field(None, description="院校")
    major: str | None = Field(None, description="专业")
    work_years: int | None = Field(None, description="工作年限")
    data_status: int | None = Field(None, description="快照时刻的档案状态")
    data_status_name: str | None = Field(None, description="状态中文名")
    version_no: int | None = Field(None, description="**当前**版本号（快照不回退版本号，见实现说明）")


class SnapshotTagOut(BaseModel):
    """快照中的标签（⚠️ 标签变更不进 TBL-22，故为**当前值**）。"""

    model_config = ConfigDict(extra="forbid")

    tag_code: str
    tag_name: str | None = None
    source: int | None = None
    source_name: str | None = None


class SnapshotOut(BaseModel):
    """API-69 出参。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    at: DateTimeStr = Field(..., description="回溯时间点（回显请求值，便于前端对齐）")
    snapshot_from_change_log_id: SnowflakeId | None = Field(
        None, description="快照重建所依据的最后一条留痕（时间点之前）；无留痕时为 null"
    )
    talent: SnapshotTalentOut
    tags: list[SnapshotTagOut] = Field(default_factory=list)
    effective_from: DateTimeStr = Field(..., description="该状态的生效起点（时间点之前最后一次变更 / 建档案时间）")
    effective_to: DateTimeStr | None = Field(None, description="该状态的失效点（时间点之后第一次变更）；仍有效为 null")
    changes_after: int = Field(0, description="时间点之后还有多少次变更")
    untracked_fields: list[str] = Field(
        default_factory=list,
        description="无留痕、只能取当前值的字段（**不是错误**，说明该字段变更历史不完整）",
    )
    restored_fields: list[str] = Field(
        default_factory=list, description="相对当前值被回退过的字段（附加说明，便于界面标注差异）"
    )
    history_truncated: bool = Field(
        False, description="留痕过多导致只取前 N 条参与重建（此时结果为近似值，**不可作为合规证据**）"
    )


# ============================================================================
# API-70 单字段变更链
# ============================================================================
class FieldHistoryOut(BaseModel):
    """API-70 出参：某档案某字段的**完整**变更链。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    talent_name: str | None = None
    field_code: str = Field(..., description="字段名")
    field_label: str | None = Field(None, description="字段中文名")
    current_value: str | None = Field(None, description="字段当前值（渲染为字符串，便于与链上值对照）")
    total: int = Field(0, description="变更次数")
    items: list[ChangeLogOut] = Field(default_factory=list)


# ============================================================================
# 工具
# ============================================================================
def snapshot_talent_dict(values: dict[str, Any], *, talent_id: int, version_no: int | None) -> dict[str, Any]:
    """把 :func:`~app.core.privacy.build_snapshot` 的 ``values`` 整理成出参形状。

    为什么要有这层适配（而不是直接 ``SnapshotTalentOut(**values)``）：
    ``values`` 里可能出现 ``id`` 之外的意外键（历史数据引入了新字段），
    直接展开会因 ``extra="forbid"`` 抛 ValidationError → 回溯接口 500。
    回溯是合规证据，**不能因为多了一个字段就查不出来**，故显式挑键。
    """
    return {
        "id": talent_id,
        "name": values.get("name"),
        "gender": values.get("gender"),
        "birth_date": values.get("birth_date").isoformat() if values.get("birth_date") else None,
        "education_level": values.get("education_level"),
        "school": values.get("school"),
        "major": values.get("major"),
        "work_years": values.get("work_years"),
        "data_status": values.get("data_status"),
        "data_status_name": talent_status_name_of(values.get("data_status")),
        "version_no": version_no,
    }


def allowed_field_codes_hint() -> str:
    return ", ".join(f"{c}（{field_label(c)}）" for c in CORRECTABLE_FIELDS)


__all__ = [
    "CHANGE_FIELD_CODES",
    "MAX_RANGE_DAYS",
    "ChangeFilterRequest",
    "ChangeLogOut",
    "FieldHistoryOut",
    "SnapshotOut",
    "SnapshotTagOut",
    "SnapshotTalentOut",
    "allowed_field_codes_hint",
    "change_source_name_of",
    "snapshot_talent_dict",
    "talent_status_name_of",
]
