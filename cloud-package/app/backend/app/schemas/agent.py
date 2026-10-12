"""Agent 与人工介入契约（API-90 ~ API-95，F-A0.1 ~ F-A0.5）。

接口 §2.2 只给了这 6 个接口一行摘要；**API-94 有详细定义**（接口 §3），
其响应体字段（``interrupt_id`` / ``interrupt_type`` / ``resumed`` / ``next_node`` /
``handled_by`` / ``handled_at``）以文档为准，本文件逐字对齐。

## 三条口径（都来自上游，不是自创）

① **API-94 强制幂等**（接口 §1.6 列的三个强制幂等接口之一：API-36 / API-79 / API-94）
   - 同一 ``interrupt_id`` **同一人**重复提交 → **40903**，返回首次结果；
   - **不同人**并发提交 → **40902**，附处理人姓名与处理时间。

② **``rejected`` 不是丢弃**
   接口 §3 备注：``rejected`` 会把任务导向失败分支（``status=4`` 或按图定义进入重试），
   **不静默丢弃**。故本契约的 ``comment`` 在 ``rejected`` 时**必填** ——
   「为什么被驳回」是这条记录唯一的业务价值，留空等于把待办变成黑洞。

③ **``next_node`` 取自图定义，不是写死的**
   响应里的 ``next_node`` 必须与 ``registry`` 里该图的真实后继节点一致，
   否则前端会显示一个实际不会执行的节点名（排查时严重误导）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import DateTimeStr, SnowflakeId


# ============================================================================
# 受控词表
# ============================================================================
class AgentTaskType:
    """``agent_task.task_type`` 受控词表（图名 = 任务类型）。"""

    PARSE = "parse"
    ASK = "ask"
    MERGE = "merge"
    TAG_SUGGEST = "tag_suggest"
    SCORE = "score"
    LABELS: ClassVar[dict[str, str]] = {
        PARSE: "档案解析",
        ASK: "档案问答",
        MERGE: "查重合并",
        TAG_SUGGEST: "标签建议",
        SCORE: "能力测评评分",
    }

    @classmethod
    def label_of(cls, value: str | None) -> str | None:
        return cls.LABELS.get(value) if value else None


class InterruptType:
    """``agent_interrupt.interrupt_type`` 受控词表。

    **决定处理界面长什么样**（``payload`` 的形状由它决定）：
    ``field_review`` 的载荷是待复核字段，``merge_confirm`` 是待合并的两个档案，
    ``cost_confirm`` 是本次查询的预估成本。
    """

    FIELD_REVIEW = "field_review"
    MERGE_CONFIRM = "merge_confirm"
    COST_CONFIRM = "cost_confirm"
    TAG_REVIEW = "tag_review"
    GENERIC = "generic"
    LABELS: ClassVar[dict[str, str]] = {
        FIELD_REVIEW: "字段复核",
        MERGE_CONFIRM: "合并确认",
        COST_CONFIRM: "高成本查询确认",
        TAG_REVIEW: "标签审核",
        GENERIC: "通用待办",
    }

    @classmethod
    def label_of(cls, value: str | None) -> str | None:
        return cls.LABELS.get(value) if value else None


#: 待办处理结论（接口 §3 API-94）
Decision = Literal["approved", "rejected"]

#: ``decision`` → ``agent_interrupt.status`` 取值（``AgentInterruptStatus``）
DECISION_TO_STATUS: dict[str, int] = {"approved": 1, "rejected": 2}


# ============================================================================
# API-90 / API-91 · 任务
# ============================================================================
class AgentTaskOut(BaseModel):
    """Agent 任务（列表项）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_type: str
    task_type_name: str | None = None
    biz_id: SnowflakeId | None = None
    status: int = Field(description="0 pending 1 running 2 waiting_human 3 success 4 failed")
    status_name: str | None = None
    current_node: str | None = None
    retry_count: int = 0
    #: 🔴 **不返回 ``graph_state``** —— 它可能有几十 KB（含中间抽取结果），
    #: 放进列表会让 20 条记录轻松超过 1MB。需要它请调 API-91（详情）。
    created_at: DateTimeStr
    updated_at: DateTimeStr


class AgentTaskDetailOut(AgentTaskOut):
    """API-91 任务详情 + 图状态。"""

    model_config = ConfigDict(extra="forbid")

    idempotent_key: str | None = None
    #: 解析后的图状态对象（``None`` 表示尚未跑过任何节点）。
    #: ⚠️ 存储侧是 ``LONGTEXT`` 字符串，此处**已解析为对象**下发：
    #: 让前端拿到一个 JSON 字符串再自己 ``JSON.parse``，等于要求每个前端
    #: 都处理一次「解析失败」分支，而它其实不可能失败（写入侧保证是合法 JSON）。
    graph_state: dict[str, Any] | None = None
    #: 步骤总数（详情页展示「共 N 步」；列表不查，避免 N+1）
    step_count: int = 0


class AgentStepOut(BaseModel):
    """API-92 步骤回放的一条。

    ``input`` / ``output`` 是**解析后的对象**（同 ``graph_state`` 的理由）。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_id: SnowflakeId
    node_name: str
    step_index: int
    input: Any = None
    output: Any = None
    latency_ms: int | None = None
    status: int = Field(description="0 失败 1 成功")
    status_name: str | None = None
    error_msg: str | None = None
    created_at: DateTimeStr


class AgentTaskFilterRequest(BaseModel):
    """API-90 查询参数。"""

    model_config = ConfigDict(extra="forbid")

    task_type: str | None = Field(None, max_length=32)
    status: int | None = Field(None, ge=0, le=4)
    biz_id: SnowflakeId | None = None

    @model_validator(mode="after")
    def _check_type(self) -> AgentTaskFilterRequest:
        if self.task_type and self.task_type not in AgentTaskType.LABELS:
            raise ValueError(
                f"未知的 task_type={self.task_type!r}；"
                f"合法取值：{', '.join(sorted(AgentTaskType.LABELS))}"
            )
        return self


class AgentTaskRetryOut(BaseModel):
    """API-95 重试失败任务的结果。"""

    model_config = ConfigDict(extra="forbid")

    task_id: SnowflakeId
    status: int
    status_name: str | None = None
    retry_count: int
    current_node: str | None = None
    #: 本次重试是否真的推进了图（``False`` 表示任务已在终态，未执行重试）
    resumed: bool = False
    message: str | None = None


# ============================================================================
# API-93 / API-94 · 人工待办
# ============================================================================
class AgentInterruptOut(BaseModel):
    """人工介入待办。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    task_id: SnowflakeId
    node_name: str
    interrupt_type: str
    interrupt_type_name: str | None = None
    #: 待办载荷（解析后对象）。**形状由 ``interrupt_type`` 决定**。
    payload: dict[str, Any] | None = None
    assignee_role: str | None = None
    status: int = Field(description="0 pending 1 approved 2 rejected")
    status_name: str | None = None
    decided_by: SnowflakeId | None = None
    #: 处理人姓名（由 ``decided_by`` 批量反查；**已删除用户也能显示**，
    #: 否则历史待办的处理人会变成空白，问责链断裂）
    decided_by_name: str | None = None
    decided_at: DateTimeStr | None = None
    comment: str | None = None
    created_at: DateTimeStr
    updated_at: DateTimeStr


class InterruptFilterRequest(BaseModel):
    """API-93 查询参数。"""

    model_config = ConfigDict(extra="forbid")

    status: int | None = Field(None, ge=0, le=2)
    task_id: SnowflakeId | None = None
    interrupt_type: str | None = Field(None, max_length=32)
    assignee_role: str | None = Field(None, max_length=32)

    @model_validator(mode="after")
    def _check_type(self) -> InterruptFilterRequest:
        if self.interrupt_type and self.interrupt_type not in InterruptType.LABELS:
            raise ValueError(
                f"未知的 interrupt_type={self.interrupt_type!r}；"
                f"合法取值：{', '.join(sorted(InterruptType.LABELS))}"
            )
        return self


class InterruptResolveRequest(BaseModel):
    """API-94 处理待办（**强制幂等**）。

    .. code-block:: json

        { "decision": "approved", "comment": "字段值已核对", "payload": {"value": "7"} }

    ``rejected`` 时 ``comment`` **必填**（见模块 docstring ②）。
    """

    model_config = ConfigDict(extra="forbid")

    decision: Decision = Field(description="`approved` / `rejected`")
    comment: str | None = Field(None, max_length=500, description="处理意见（`rejected` 时必填）")
    payload: dict[str, Any] | None = Field(
        None, description="与 `interrupt_type` 相关的补充数据（如复核修正值）"
    )

    @model_validator(mode="after")
    def _reject_requires_comment(self) -> InterruptResolveRequest:
        if self.decision == "rejected" and not (self.comment or "").strip():
            raise ValueError("驳回（rejected）必须填写处理意见 comment")
        return self


class InterruptResolveOut(BaseModel):
    """API-94 响应（字段名与接口 §3 逐字对齐）。"""

    model_config = ConfigDict(extra="forbid")

    interrupt_id: SnowflakeId
    interrupt_type: str
    status: int = Field(description="1 approved / 2 rejected")
    status_name: str | None = None
    task_id: SnowflakeId
    #: 图是否已从 ``interrupt`` 点恢复推进。
    #: ``False`` 有三种情况：① 任务已被软删；② 图定义里该节点后无后继（终态）；
    #: ③ 执行中抛错（此时 ``task.status`` 会是 ``failed``）。**不静默返回 true**。
    resumed: bool
    #: 恢复后将要执行的节点名（取自图定义；``None`` = 该 interrupt 后无后继）
    next_node: str | None = None
    #: 恢复后的任务状态（``AgentTaskStatus`` 取值）
    task_status: int | None = None
    task_status_name: str | None = None
    handled_by: SnowflakeId | None = None
    handled_by_name: str | None = None
    handled_at: DateTimeStr | None = None


class InterruptHandleConflictData(BaseModel):
    """40902 / 40903 的 ``data`` 形状（接口 §3 的失败响应示例）。"""

    model_config = ConfigDict(extra="forbid")

    handled_by_name: str | None = None
    handled_at: DateTimeStr | None = None
    status: int | None = None
    #: 仅 40903（幂等回放）携带：首次处理的结果，前端按成功继续
    result: InterruptResolveOut | None = None
