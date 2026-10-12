"""个人信息权利响应契约（CU-22，API-71~80，模块 N「个人权利」，F-3.11 / SM-3）。

接口 §2.2 只给了这十个接口的一行摘要，详规里只有 **API-72 / API-77** 有示例响应 ——
**本文件即这十个接口的契约事实来源**（同 CU-05/07/08 的既有做法）。

🔴 五条口径（写在这里，避免实现与前端各理解一套）
===============================================

1. **「已通过」的含义随类型不同**（SM-3 四类请求差异表）——
   删除 = 「数据真的删干净了」（软删 + 向量已清理）；更正 = 「字段已更正 + 已通知本人」；
   导出 = 「数据包已生成，本人可下载」。**接口不把它们压成一个「成功」**：
   出参带 ``vector_cleaned`` / ``applied_value`` / ``download_url`` 各自的事实字段。
2. **本人视角只走 `user_id` 关联，不接受前端传 `talent_id`**（API-71）——
   否则「查阅本人档案」会变成「查阅任意档案」（传一个别人的 ID 就穿透）。
   API-72/75/78 虽带 ``talent_id``，但服务端**逐条校验其 ``user_id`` 等于本人**，
   不符一律 **40301**（CR-8：越权与不存在同码，不泄露「这个 ID 存在但不是你的」）。
3. **证件号永不明文下发**（`id_card_masked`）—— 《个保法》的查阅权也受「最小必要」约束；
   完整证件号的核验走线下。这是本单元**唯一一处对「全部字段」的收窄**，已登记说明。
4. **驳回必须带理由**（SM-3 红线②）—— ``approved=false`` 时 ``reason`` 必填，
   且终态是「已驳回」而**不是**回到「待处理」（否则会产生无限重提的死循环）。
5. **导出的内容放在 ``data.content`` 里，不返回文件流** —— 同 CU-08 API-29 的取舍：
   统一响应五件套（``code``/``trace_id``）比二进制流更重要（**下载行为必须留痕**，
   而文件流会让统一拦截器失效，出问题时用户只能看到一个坏掉的包）。
   代价：一期无 MinIO，PDF 生成库亦未引入 → 仅 JSON，PDF 登记为遗留。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.privacy import CORRECTABLE_FIELD_CODES, CORRECTION_EXCLUDED
from app.models.enums import PrivacyRequestStatus, PrivacyRequestType
from app.schemas.common import DateTimeStr, SnowflakeId
from app.schemas.talent_master import ProjectExpOut, WorkExpOut

#: 佐证材料上限（接口 §2.2 API-72：``attachments`` ≤5 个文件 ID）
MAX_ATTACHMENTS = 5

#: 理由长度（API-72：10~500 字符）
REASON_MIN = 10
REASON_MAX = 500

#: 支持的数据包格式。⚠️ PDF 未引入生成库（一期无 MinIO、requirements 无 PDF 依赖），
#: 故**契约上就不宣称支持** —— 比「收下 format=pdf 再返回 JSON」诚实（登记为遗留）。
EXPORT_FORMATS: tuple[str, ...] = ("json",)


def request_type_name_of(value: int | None) -> str | None:
    return PrivacyRequestType.name_of(value) if value is not None else None


def request_status_name_of(value: int | None) -> str | None:
    """对外状态名。🔴 **``3 处理中`` 映射为「待处理」**（SM-3：内部态不对外暴露）。

    理由：内部态只存在于一次 CAS 之后的**秒级窗口**。若对外下发「处理中」，
    前端会在那一瞬渲染一个随后消失的状态，用户会以为系统在反复重试。
    对用户而言「还没出结果」才是事实。
    """
    if value is None:
        return None
    if int(value) == int(PrivacyRequestStatus.PROCESSING):
        return PrivacyRequestStatus.name_of(int(PrivacyRequestStatus.PENDING))
    return PrivacyRequestStatus.name_of(value)


# ============================================================================
# API-71 查阅本人档案
# ============================================================================
class SelfFieldOut(BaseModel):
    """本人档案中的一个字段（**值 + 来源**，F-3.11 查阅权的「来源」部分）。"""

    model_config = ConfigDict(extra="forbid")

    field_code: str = Field(..., description="字段名")
    field_label: str | None = Field(None, description="字段中文名")
    value: str | None = Field(None, description="当前值（PII 在此已按最小必要处理）")
    source: int | None = Field(None, description="来源 0 AI 解析 1 人工 2 合并 3 重解析")
    source_name: str | None = Field(None, description="来源中文名")
    confidence: float | None = Field(None, description="置信度（AI 抽取才有）")
    review_status: int | None = Field(None, description="复核状态 0 待复核 1 已确认 2 已修正 3 免复核")
    review_status_name: str | None = Field(None, description="复核状态中文名")
    review_field_id: SnowflakeId | None = Field(
        None, description="对应复核队列条目 ID（提交更正申请时用于复用同一队列）"
    )
    source_file: str | None = Field(None, description="来源文件名")


class SelfTagOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_code: str
    tag_name: str | None = None
    source: int | None = Field(None, description="0 人工 1 AI")
    source_name: str | None = None


class SelfProfileOut(BaseModel):
    """API-71 出参：本人档案全字段 + 来源。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    name: str | None = None
    gender: int | None = None
    gender_name: str | None = None
    birth_date: str | None = None
    education_level: str | None = None
    school: str | None = None
    major: str | None = None
    work_years: int | None = None
    phone: str | None = Field(None, description="手机号（本人可见明文）")
    email: str | None = Field(None, description="邮箱（本人可见明文）")
    id_card_masked: str | None = Field(
        None, description="身份证**脱敏**形态（明文不通过接口下发，核验走线下，见模块 docstring 口径 ③）"
    )
    data_status: int | None = None
    data_status_name: str | None = None
    version_no: int | None = None
    fields: list[SelfFieldOut] = Field(default_factory=list, description="字段级明细（含来源与置信度）")
    tags: list[SelfTagOut] = Field(default_factory=list)
    work_exps: list[WorkExpOut] = Field(default_factory=list)
    project_exps: list[ProjectExpOut] = Field(default_factory=list)
    pending_corrections: list[SnowflakeId] = Field(
        default_factory=list, description="该档案尚在处理的更正申请 ID（避免重复提交）"
    )
    correction_field_codes: list[str] = Field(
        default_factory=list, description="可申请更正的字段白名单（前端直接渲染可选清单）"
    )
    correction_excluded_reason: dict[str, str] = Field(
        default_factory=dict, description="不开放更正的字段及理由（避免用户以为漏了）"
    )
    change_count: int = Field(0, description="该档案的变更留痕条数（F-3.10 与查阅权的衔接点）")


# ============================================================================
# API-72 / 74 更正申请
# ============================================================================
class CorrectionSubmitRequest(BaseModel):
    """API-72 提交更正申请。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId = Field(..., description="本人档案 ID（服务端校验归属）")
    field_code: str = Field(..., max_length=64, description="要更正的字段（白名单内）")
    current_value: str = Field(..., max_length=500, description="当前值（服务端比对，防并发覆盖）")
    expected_value: str = Field(..., max_length=500, description="申请更正后的值")
    reason: str = Field(..., min_length=REASON_MIN, max_length=REASON_MAX, description="更正理由")
    attachments: list[str] = Field(default_factory=list, description=f"佐证材料文件 ID（≤{MAX_ATTACHMENTS}）")

    @model_validator(mode="after")
    def _check(self) -> CorrectionSubmitRequest:
        if self.field_code not in CORRECTABLE_FIELD_CODES:
            excluded = CORRECTION_EXCLUDED.get(self.field_code)
            detail = f"（{excluded}）" if excluded else ""
            raise ValueError(
                f"field_code={self.field_code!r} 不在可更正字段白名单内{detail}；"
                f"合法取值：{', '.join(CORRECTABLE_FIELD_CODES)}"
            )
        if len(self.attachments) > MAX_ATTACHMENTS:
            raise ValueError(f"佐证材料最多 {MAX_ATTACHMENTS} 个")
        if (self.expected_value or "").strip() == (self.current_value or "").strip():
            raise ValueError("expected_value 与 current_value 相同，无需提交更正申请")
        # 空串与空白统一折叠（否则「当前值为空但填了空格」会被判成不一致）
        for name in ("current_value", "expected_value"):
            object.__setattr__(self, name, str(getattr(self, name)).strip())
        return self


class CorrectionSubmitOut(BaseModel):
    """API-72 出参。"""

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    request_no: str
    request_type: Literal["correction"] = "correction"
    status: int
    status_name: str | None = None
    review_queue: str = Field("talent_review", description="**复用 F-3.3 人工复核队列**，不另建审批流")
    review_field_id: SnowflakeId | None = Field(None, description="已入队的复核条目 ID")
    sla_hours: int = Field(..., description="处理时限（提交时刻快照，可配置）")
    submitted_at: DateTimeStr


class CorrectionResolveRequest(BaseModel):
    """API-74 处理更正申请。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool = Field(..., description="是否采纳")
    reason: str | None = Field(None, max_length=REASON_MAX, description="`approved=false` 时必填（须告知本人）")
    applied_value: str | None = Field(
        None,
        max_length=500,
        description="采纳时实际写入的值（缺省用申请里的 expected_value；HR 可据佐证材料修正）",
    )

    @model_validator(mode="after")
    def _check(self) -> CorrectionResolveRequest:
        if not self.approved:
            text = (self.reason or "").strip()
            if len(text) < REASON_MIN:
                raise ValueError(f"驳回必须给出理由（≥{REASON_MIN} 字符）—— 否则本人只能反复重提")
            object.__setattr__(self, "reason", text)
        return self


class CorrectionResolveOut(BaseModel):
    """API-74 出参。"""

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    status: int
    status_name: str | None = None
    talent_id: SnowflakeId
    field_code: str | None = None
    applied: bool = Field(False, description="是否真的把值写进了档案（驳回时为 false）")
    applied_value: str | None = Field(None, description="生效后的值")
    change_log_id: SnowflakeId | None = Field(None, description="本次更正产生的留痕 ID（F-3.10）")
    notified: bool = Field(False, description="是否已通知本人")
    processed_at: DateTimeStr


# ============================================================================
# API-75 / 77 删除申请与审批
# ============================================================================
class DeletionSubmitRequest(BaseModel):
    """API-75 提交删除申请。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId = Field(..., description="本人档案 ID（服务端校验归属）")
    reason: str = Field(..., min_length=REASON_MIN, max_length=REASON_MAX, description="删除理由")

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class DeletionSubmitOut(BaseModel):
    """API-75 出参（**审核权归 ADM**，故这里只回报「已受理」）。"""

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    request_no: str
    request_type: Literal["deletion"] = "deletion"
    status: int
    status_name: str | None = None
    talent_id: SnowflakeId
    sla_hours: int
    approver_hint: str = Field("仅超级管理员（ADM）可审批删除", description="前端提示语")
    submitted_at: DateTimeStr


class DeletionApproveRequest(BaseModel):
    """API-77 审批删除申请。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool = Field(..., description="是否同意删除")
    reason: str | None = Field(None, max_length=REASON_MAX, description="`approved=false` 时必填")


class DeletionApproveOut(BaseModel):
    """API-77 出参（**跨库一致性的验收点**）。"""

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    status: int
    status_name: str | None = None
    talent_id: SnowflakeId
    talent_soft_deleted: bool = Field(
        False, description="档案是否已软删（`approved=false` 或向量清理失败时为 false）"
    )
    vector_cleaned: dict[str, int] = Field(
        default_factory=dict,
        description="各 collection 清理的分片数（`approved=false` 时为空；降级环境为全 0 且 `vector_degraded=true`）",
    )
    vector_degraded: bool = Field(
        False,
        description="向量库不可用（降级为关键词实现）时无法执行清理 —— 此时**不阻断**软删，但如实标记",
    )
    change_log_id: SnowflakeId | None = Field(None, description="删除动作的留痕 ID（F-3.10）")
    notified: bool = Field(False, description="是否已通知本人")
    processed_at: DateTimeStr


# ============================================================================
# API-78 / 79 导出
# ============================================================================
class ExportSubmitRequest(BaseModel):
    """API-78 提交导出申请。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId = Field(..., description="本人档案 ID（服务端校验归属）")
    format: Literal["json"] = Field("json", description="数据包格式（一期仅 json，PDF 见模块 docstring 口径 ⑤）")


class ExportSubmitOut(BaseModel):
    """API-78 出参。"""

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    request_no: str
    request_type: Literal["export"] = "export"
    status: int
    status_name: str | None = None
    format: str = "json"
    download_url: str = Field(..., description="下载路径（相对 BaseURL，**下载本身会留痕**）")
    sla_hours: int
    submitted_at: DateTimeStr


class ExportDownloadOut(BaseModel):
    """API-79 出参。

    🔴 与 CU-08 API-29 同一取舍：内容放在 ``data.content``，不返回文件流
    （下载行为必须能被统一响应壳留痕，见模块 docstring 口径 ⑤）。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: SnowflakeId
    talent_id: SnowflakeId
    filename: str = Field(..., description="建议文件名（含请求号，前端直接落盘）")
    format: Literal["json"] = "json"
    content: str = Field(..., description="数据包正文（JSON 字符串，含档案 / 字段来源 / 标签 / 变更摘要）")
    download_count: int = Field(0, description="累计下载次数（本次之后）")
    generated_at: DateTimeStr


# ============================================================================
# API-73 / 76 / 80 列表与台账
# ============================================================================
class PrivacyRequestOut(BaseModel):
    """权利请求单（API-73 / API-76 / API-80 共用的行形状）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    request_no: str
    request_type: int
    request_type_name: str | None = None
    talent_id: SnowflakeId
    talent_name: str | None = None
    applicant_user_id: SnowflakeId
    applicant_name: str | None = None
    field_code: str | None = Field(None, description="更正申请专有")
    field_label: str | None = None
    current_value: str | None = None
    expected_value: str | None = None
    review_field_id: SnowflakeId | None = None
    reason: str | None = None
    status: int
    status_name: str | None = None
    sla_hours: int
    overdue: bool = Field(False, description="是否**当前**超时（已终态恒为 false，见 core.privacy.is_overdue）")
    waited_hours: float | None = Field(None, description="已等待时长（小时）")
    processed_by: SnowflakeId | None = None
    processed_by_name: str | None = None
    processed_at: DateTimeStr | None = None
    reject_reason: str | None = None
    notified_at: DateTimeStr | None = None
    download_count: int = Field(0, description="导出申请专有（下载留痕）")
    created_at: DateTimeStr


class PrivacyRequestFilter(BaseModel):
    """API-73 / 76 / 80 共用的筛选条件。"""

    model_config = ConfigDict(extra="forbid")

    request_type: int | None = Field(None, ge=0, le=2, description="0 更正 1 删除 2 导出")
    status: int | None = Field(None, ge=0, le=3, description="0 待处理 1 已通过 2 已驳回 3 处理中")
    talent_id: SnowflakeId | None = None
    applicant_user_id: SnowflakeId | None = None
    overdue_only: bool = Field(False, description="只看**当前**超时的单子（API-80 的待办视图）")
    start_time: datetime | None = None
    end_time: datetime | None = None

    @model_validator(mode="after")
    def _check_range(self) -> PrivacyRequestFilter:
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time 不能晚于 end_time")
        return self


class PrivacyLedgerOut(BaseModel):
    """API-80 出参：`data` 为对象（`items` + `summary`），分页仍走同级 `page`。

    ⚠️ 接口 §1.1 约定「`data` 是数组、`page` 同级」。此处为承载**处理时限统计**
    而把 `data` 放宽为对象 —— 与 CU-20 的 ``SearchOut(items, meta)`` 是同一取舍
    （把统计放进 `meta`/`data` 内，而不是新增顶层字段撞 `CommonResp.extra="forbid"`）。
    已登记为**口径说明**（评审文档批次 13）。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[PrivacyRequestOut] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict, description="处理时限统计（core.privacy.SlaSummary）")


__all__ = [
    "EXPORT_FORMATS",
    "MAX_ATTACHMENTS",
    "REASON_MAX",
    "REASON_MIN",
    "CorrectionResolveOut",
    "CorrectionResolveRequest",
    "CorrectionSubmitOut",
    "CorrectionSubmitRequest",
    "DeletionApproveOut",
    "DeletionApproveRequest",
    "DeletionSubmitOut",
    "DeletionSubmitRequest",
    "ExportDownloadOut",
    "ExportSubmitOut",
    "ExportSubmitRequest",
    "PrivacyLedgerOut",
    "PrivacyRequestFilter",
    "PrivacyRequestOut",
    "SelfFieldOut",
    "SelfProfileOut",
    "SelfTagOut",
    "request_status_name_of",
    "request_type_name_of",
]
