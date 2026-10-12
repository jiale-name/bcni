"""Agent 数据模型（TBL-25 ``agent_task`` / TBL-26 ``agent_step`` / TBL-27 ``agent_interrupt``）。

三张表对应 F-A0 的三件事：**任务**（权威状态）、**步骤**（可观测性）、**待办**（人工介入）。

🔴 Checkpointer 的落点就是 ``agent_task.graph_state``
==================================================
ADR-11 要求「Agent 权威状态落 MySQL，Checkpointer 从最后成功节点恢复」。
本实现把**图状态**存进 ``agent_task.graph_state``（LONGTEXT JSON），
而 ``agent_step`` 是**追加型的执行轨迹**（人看的），两者**不是一回事**：

| | ``agent_task.graph_state`` | ``agent_step`` |
| --- | --- | --- |
| 语义 | **机器用**：恢复执行所需的全部状态 | **人用**：每一步的输入/输出/耗时 |
| 写入方式 | 每步 **覆盖**（原地更新） | 每步 **追加**（只增不改） |
| 少了会怎样 | 任务无法恢复（要重跑整条链） | 只是看不到回放，功能不受影响 |

❌ 因此**不能**用「重放 ``agent_step``」来实现恢复 —— 步骤日志可能被清理
（数据库 §9：``agent_step`` 只保留 6 个月），而任务恢复必须随时可用。

🔴 ``agent_task.status``（任务级）与 ``agent_interrupt.status``（待办级）是两个字段
=============================================================================
一个任务可产生**多个**待办（如「字段复核」+「合并确认」两处 interrupt），
若共用一个字段，处理完第一个待办就会把任务错误地标记为终态。**类图 CL-5 已把它列为红线。**

🔴 ``agent_task`` 的软删 vs ``agent_step`` 的只追加
================================================
- ``agent_task`` / ``agent_interrupt`` 是**业务态**（可软删）；
- ``agent_step`` 是**执行轨迹**（只追加，无 ``is_deleted`` / ``updated_at``），
  与 TBL-24 / TBL-28 同源。轨迹可被软删的话，「跑过没跑过」就无法追问了。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMTEXT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.snowflake import next_id
from app.models.base import MYSQL_TABLE_KWARGS, Base, IdTenantCommonMixin, SnowflakeIdMixin, TenantColumnMixin


# ============================================================================
# TBL-25 Agent 任务（权威状态）
# ============================================================================
class AgentTask(IdTenantCommonMixin, Base):
    """TBL-25 Agent 任务（**权威状态**，F-A0.1 / F-A0.6）。

    ``uk_tenant_idem`` 是 F-A0.6「幂等」的落点：``(tenant_id, idempotent_key)`` 唯一。
    调用方（导入任务、检索请求等）带同一个 ``idempotent_key`` 重复提交时，
    服务层先查后插，命中即**返回既有任务**（不新建、不重跑），
    并以 ``code=40903``（幂等回放）告知前端「按首次结果继续」。

    ⚠️ ``idempotent_key`` 允许为 ``NULL``（非幂等场景，如人工在界面上点「重试」）。
    MySQL 的唯一索引对 ``NULL`` 不生效，故多行 ``NULL`` 可共存 —— 这正是要的语义。
    ❌ 不要为了「统一」把默认值写成空字符串：那样第二条无幂等键的任务就会撞 1062。
    """

    __tablename__ = "agent_task"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotent_key", name="uk_tenant_idem"),
        Index("idx_tenant_status", "tenant_id", "status"),
        Index("idx_biz", "biz_id"),
        MYSQL_TABLE_KWARGS,
    )

    task_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="任务类型：parse/ask/merge/..."
    )
    biz_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="业务 ID（如 talent_id / import_task_id）"
    )
    #: 任务级状态。取值见 :class:`~app.models.enums.AgentTaskStatus`
    #: （0 pending / 1 running / 2 waiting_human / 3 success / 4 failed）。
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"),
        comment="状态 0 pending 1 running 2 waiting_human 3 success 4 failed",
    )
    #: 🔴 **LangGraph 图状态（JSON）** —— Checkpointer 的落点，恢复执行的全部依据。
    graph_state: Mapped[str | None] = mapped_column(
        LONGTEXT, nullable=True, comment="图状态（JSON，Checkpointer 落点）"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="重试次数"
    )
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当前节点")
    idempotent_key: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="幂等键")
    #: 幂等回放时**首次**的响应快照（供 API 层原样返回 `code=40903` 的结果）
    idempotent_result: Mapped[str | None] = mapped_column(
        MEDIUMTEXT, nullable=True, comment="幂等回放响应快照（JSON）"
    )

    def __repr__(self) -> str:
        return (
            f"<AgentTask id={self.id} type={self.task_type!r} status={self.status} "
            f"node={self.current_node!r}>"
        )


# ============================================================================
# TBL-26 Agent 步骤（只追加）
# ============================================================================
class AgentStep(SnowflakeIdMixin, TenantColumnMixin, Base):
    """TBL-26 Agent 步骤（**只追加**，F-A0.5 步骤回放）。

    刻意**不继承** :class:`~app.models.base.IdTenantCommonMixin` ——
    它含 ``is_deleted`` / ``updated_at``，而执行轨迹一旦可改可删，
    「这一步到底跑没跑、跑出什么」就无从追问（与 TBL-24/28 同一条推理）。

    ``step_index`` 与 ``node_name`` 一起构成回放顺序；同一节点被循环执行多次时
    ``node_name`` 会重复，故**排序必须用 ``step_index``**（由服务层单调分配）。
    """

    __tablename__ = "agent_step"
    __table_args__ = (
        Index("idx_task_step", "task_id", "step_index"),
        MYSQL_TABLE_KWARGS,
    )

    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="Agent 任务 ID")
    node_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="节点名")
    step_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="步骤序号"
    )
    input_json: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="输入（JSON）")
    output_json: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="输出（JSON）")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="耗时 ms")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"), comment="状态 0 失败 1 成功"
    )
    error_msg: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="错误信息")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )

    def __repr__(self) -> str:
        return f"<AgentStep task={self.task_id} #{self.step_index} node={self.node_name!r}>"


# ============================================================================
# TBL-27 人工介入待办
# ============================================================================
class AgentInterrupt(IdTenantCommonMixin, Base):
    """TBL-27 人工介入待办（F-A0.3，API-93/94）。

    状态机（SM 补充）::

        pending(0) ──approved──▶ approved(1)   ──▶ 任务从 interrupt 点恢复继续
                   └─rejected──▶ rejected(2)   ──▶ 任务走失败分支（status=4 或按图定义重试）

    🔴 **状态流转只允许 pending → approved / rejected**（单向）。
    已处理的待办被二次提交必须报 **40902**（并发冲突，含处理人姓名）
    或 **40903**（同一人幂等重放，返回首次结果）——
    两者不可混：前者要人「刷新后重试」，后者要前端「当作成功继续」。
    """

    __tablename__ = "agent_interrupt"
    __table_args__ = (
        Index("idx_tenant_status", "tenant_id", "status"),
        Index("idx_task", "task_id"),
        MYSQL_TABLE_KWARGS,
    )

    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="Agent 任务 ID")
    node_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="中断节点名")
    #: ``interrupt_type`` —— 待办类型（``field_review`` / ``merge_confirm`` / ``cost_confirm``…）。
    #: ⚠️ 上游 DDL（数据库 §4.6）**没有这一列**，而接口 §3 API-94 的响应体里有
    #: ``interrupt_type``。缺列则无法生成该字段，也无法按类型分派处理界面。
    #: 故本批补上该列（属**结构补充**，已登记进评审文档 §7.3 批次 7）。
    interrupt_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="generic", server_default=text("'generic'"),
        comment="待办类型：field_review/merge_confirm/cost_confirm/generic",
    )
    payload_json: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="待办载荷（JSON）")
    assignee_role: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="指派角色")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"),
        comment="状态 0 pending 1 approved 2 rejected",
    )
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="处理人")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="处理时间")
    comment: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="处理意见")
    #: 幂等键（API-94 是接口 §1.6 的三个**强制幂等**接口之一）
    idempotent_key: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="幂等键")

    def __repr__(self) -> str:
        return (
            f"<AgentInterrupt id={self.id} task={self.task_id} node={self.node_name!r} "
            f"type={self.interrupt_type!r} status={self.status}>"
        )
