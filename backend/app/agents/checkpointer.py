"""Checkpointer：Agent 图状态的持久化（F-A0.1，ADR-11）。

🔴 **权威状态落 MySQL，不落 Redis**
=================================
架构 §8 的「记忆职责边界」写得非常明确：

| 存储 | 存什么 | 丢了会怎样 |
| --- | --- | --- |
| Redis ``agent_mem:<task_id>`` | **短期热记忆**（可丢） | 下一轮重新读库，功能不受影响 |
| **MySQL ``agent_task.graph_state``** | **权威状态**（不可丢） | **任务无法恢复**，只能重跑整条链 |

因此 :class:`MySqlCheckpointer` 是**唯一**的恢复依据；
Redis 侧的 ``agent_mem`` 只是加速缓存，**永远不能**作为恢复来源。
❌ 不要把 ``graph_state`` 只写 Redis 然后「定期同步到 MySQL」——
那正是 ADR-11 要避免的模式：宕机窗口内完成的任务会丢失检查点。

## 与 LangGraph 的可替换点

:class:`BaseCheckpointer` 是协议（Protocol）。将来 ``pip install langgraph`` 之后，
新增一个实现 ``langgraph.checkpoint.base.BaseCheckpointSaver`` 的适配器即可，
``agent_service`` 只需换构造参数 —— **业务节点函数一行不改**。
本模块末尾的 :class:`LangGraphCompatCheckpointer` 保留了探测入口
（langgraph 未安装时 :meth:`available` 返回 ``False``，**不抛 ImportError**）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.agents.graph import GraphOutcome, StepRecord
from app.core.logging import get_logger
from app.models.agent import AgentInterrupt, AgentStep, AgentTask
from app.models.enums import AgentTaskStatus

logger = get_logger(__name__)


@runtime_checkable
class BaseCheckpointer(Protocol):
    """检查点读写协议（可替换点）。

    ⚠️ 只定义**两个**方法：加载与保存。「删除检查点」不提供 ——
    删掉检查点等于让任务永久不可恢复，任何调用方都不该有这个机会。
    """

    def load(self, task: AgentTask) -> dict[str, Any]:
        """从检查点载入图状态（无检查点返回空 dict，**不返回 None**）。"""
        ...

    def save(self, task: AgentTask, outcome: GraphOutcome) -> None:
        """把一次运行的结果落成检查点。"""
        ...


class MySqlCheckpointer:
    """MySQL 检查点（落 ``agent_task.graph_state`` / ``current_node`` / ``status``）。

    三件事一起做、且必须**同一次 flush**：

    1. ``graph_state`` —— 恢复执行的全部依据；
    2. ``current_node`` —— 便于列表页展示进度（不必反序列化 graph_state）；
    3. ``status`` —— 任务级状态（``running`` / ``waiting_human`` / ``success`` / ``failed``）。

    ⚠️ 若只写 1 不写 2/3，列表页会显示「仍在 running」而实际已挂起等人；
    若只写 3 不写 1，恢复时会从错误的节点重跑。三者是一组。
    """

    def __init__(self, db: Session, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = int(tenant_id)

    # ------------------------------------------------------------------
    def load(self, task: AgentTask) -> dict[str, Any]:
        """载入图状态。

        ⚠️ 反序列化失败**不抛异常**，而是返回空状态并记 ERROR ——
        理由：抛异常会让整个任务的恢复路径直接崩掉，而返回空状态至少能让
        服务层选择「重跑」；同时 ERROR 日志保证这件事不会被静默。
        （写入侧保证永远写合法 JSON，故这条分支只会在人为改库后触发。）
        """
        raw = task.graph_state
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.error(
                "graph_state 反序列化失败，按空状态处理 task_id=%s: %s", task.id, exc
            )
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    def save(self, task: AgentTask, outcome: GraphOutcome) -> None:
        """落检查点（状态 + 当前节点 + 任务状态）。**由调用方 commit**。"""
        task.graph_state = json.dumps(outcome.state, ensure_ascii=False, default=str)
        task.current_node = outcome.next_node or outcome.interrupt_node
        task.status = _status_of(outcome)
        self.db.flush()

    # ------------------------------------------------------------------
    def record_step(self, task: AgentTask, record: StepRecord, *, node_name: str) -> None:
        """落一条 ``agent_step``（F-A0.5 步骤回放）。

        :param node_name: 与 ``record.node_name`` 同值，显式传入是为了防止
            「记录里的节点名」与「实际执行的节点名」在重构后漂移。
        """
        step = AgentStep(
            tenant_id=self.tenant_id,
            task_id=int(task.id),
            node_name=node_name,
            step_index=int(record.step_index),
            input_json=_dumps(record.input),
            output_json=_dumps(record.output),
            latency_ms=record.latency_ms,
            status=record.status,
            error_msg=_clip(record.error_msg, 512),
        )
        self.db.add(step)
        self.db.flush()

    # ------------------------------------------------------------------
    def create_interrupt(self, task: AgentTask, outcome: GraphOutcome) -> AgentInterrupt:
        """为挂起的图运行建一条待办（``agent_interrupt``）。"""
        request = outcome.interrupt
        if request is None:  # pragma: no cover — 调用方已保证
            raise ValueError("outcome 不含 interrupt，无法建待办")
        interrupt = AgentInterrupt(
            tenant_id=self.tenant_id,
            task_id=int(task.id),
            node_name=outcome.interrupt_node or "unknown",
            interrupt_type=request.interrupt_type,
            payload_json=_dumps(request.payload),
            assignee_role=request.assignee_role,
            status=0,  # pending
        )
        self.db.add(interrupt)
        self.db.flush()
        return interrupt


# ============================================================================
# 未来适配点（N2 决策）
# ============================================================================
class LangGraphCompatCheckpointer:
    """LangGraph 适配器**占位**（当前不生效）。

    🔴 本类存在的意义是**把可替换点显式写进代码**，而不是只写在文档里。
    在 ``requirements.txt`` 按既定策略引入 ``langgraph`` 之前，
    :meth:`available` 恒为 ``False``，:meth:`build` 会抛 :class:`RuntimeError`
    —— **明确失败**，而不是静默返回一个「看起来能跑」的空实现
    （那会让「以为已经在用 LangGraph 做持久化」的错误认知存活很久）。

    ⚠️ 遗留登记：**N2（CU-14/15）开工前**必须决策
    「切 LangGraph 官方 Checkpointer 还是保留 :class:`MySqlCheckpointer`」。
    两者的 ``graph_state`` 结构不同，**中途切换需要数据迁移**，越早决策越好。
    """

    @staticmethod
    def available() -> bool:
        """langgraph 是否已安装（**不抛异常**）。"""
        try:
            import importlib.util

            return importlib.util.find_spec("langgraph") is not None
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def build(cls, db: Session, tenant_id: int) -> BaseCheckpointer:
        raise RuntimeError(
            "LangGraph 尚未接入（N1 阶段按 requirements 的分批启用策略未安装 langgraph）。"
            "请使用 MySqlCheckpointer；若要切换，见 agents/checkpointer.py 的类说明。"
        )


# ============================================================================
# 工具
# ============================================================================
def _status_of(outcome: GraphOutcome) -> int:
    """``GraphOutcome.status`` → ``AgentTaskStatus``（三态映射，见 graph.py 的表）。"""
    if outcome.status == "success":
        return int(AgentTaskStatus.SUCCESS)
    if outcome.status == "waiting_human":
        return int(AgentTaskStatus.WAITING_HUMAN)
    return int(AgentTaskStatus.FAILED)


def _dumps(value: Any) -> str | None:
    """JSON 序列化（``None`` 原样返回，不写 "null" 字符串）。"""
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover — default=str 已兜住绝大多数
        logger.warning("步骤内容序列化失败，已降级为字符串：%s", exc)
        return json.dumps(str(value), ensure_ascii=False)


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit]
