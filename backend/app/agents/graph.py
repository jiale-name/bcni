"""Agent 图执行骨架（F-A0.1 ~ F-A0.7，ADR-11）。

本模块是**纯运行时**：不碰数据库、不碰 HTTP —— 因此它的用例全部是 L1
（无外部依赖即可跑），这恰好是「Agent 骨架」这种易碎逻辑最需要的测试形态。

F-A0 七项能力落在哪里
====================

| 能力 | 落地方式 |
| --- | --- |
| **F-A0.1 状态持久化**（Checkpointer） | :class:`GraphOutcome` 携带完整 ``state``；由 :mod:`app.agents.checkpointer` 落 ``agent_task.graph_state`` |
| **F-A0.2 循环与分支** | :class:`GraphDefinition` 的 ``edges``（静态边）+ ``routers``（条件边）；``max_steps`` 兜住死循环 |
| **F-A0.3 人工介入** | 节点返回 :class:`InterruptRequest` → 运行时**立即挂起**（``waiting_human``），不推进到后继节点 |
| **F-A0.4 失败重试** | 节点抛 ``RetryableNodeError`` 时按 ``graph.retries`` 重试；耗尽后任务置 ``failed`` |
| **F-A0.5 步骤可观测** | 每个节点执行产出一条 :class:`StepRecord`（输入 / 输出 / 耗时 / 错误） |
| **F-A0.6 幂等** | **不在本模块** —— 幂等键在 ``agent_task``（TBL-25）上，由 ``agent_service`` 先查后插实现 |
| **F-A0.7 可视化配置** | :meth:`GraphDefinition.describe` 输出节点/边/中断点清单，供前端画图 |

## 与 LangGraph 的关系（**2026-09-22 已拍板：一期沿用自研**）

架构 §4.3 与 ADR-11 指定 LangGraph 作为图引擎。``requirements.txt`` 的既定策略是
**分批启用**（「N5 追加 langchain 系」），langgraph 当前**未安装**。

**决策**（评审文档 §7.3 批次 8 · ``docs/图运行时选型评估-自研运行时与LangGraph.md`` v1.1）：
一期**沿用自研运行时**。理由**不是**「LangGraph 不好」，而是它相对自研多出来的能力
（super-step 检查点、状态历史 / 时间旅行、``Send`` 并行分支、子图、``stream``）
在 **CU-14 ~ CU-17 的逐单元核查中全部「无实际需求」**（评估 §9），
而自研在**租户隔离、单事务原子、与十条铁律的契合度**上明显更强。
切换的决策权保留在**三个触发条件**上（评估 §7），**监控点 = CU-20 / CU-21 开工前**。

故落地形态是：

1. **本模块** = 自研最小运行时，**语义与 LangGraph 对齐**（StateGraph + 条件边 +
   interrupt + 从检查点恢复）；
2. **:mod:`app.agents.checkpointer`** 提供 ``BaseCheckpointer`` 协议 ——
   这是**可替换点**：将来新增 ``LangGraphCheckpointer`` 适配器即可，节点函数不用改。
   ⚠️ 但**不要**用社区包 ``langgraph-checkpoint-mysql``：它 ``setup()`` 自建表**绕过 Alembic**，
   且检查点表**无 ``tenant_id``** —— 两条都是本项目的红线。

⚠️ 与 LangGraph 的**有意不同**（将来切过去时最容易被当成 bug 的地方）
====================================================================
LangGraph 里 ``interrupt()`` 的返回值在**节点内**拿到，代价是**节点函数会被重放**
（``interrupt()`` 之前的代码会**执行两次**）。本运行时则把人工结论放在
``state["__human__"][节点名]``，由**后继节点**读取：「挂起即落检查点，恢复时只跑后继节点」，
副作用只发生一次。理由：节点重放要求节点函数是**幂等纯函数**，
而本项目的解析节点会写库、调模型 —— 强行要求纯函数不现实。

## 迁移控制项（replay-safety contract）

> 目的：让「今天写得更顺手」不会变成「将来切得更贵」。
> 这组契约**不增加开发成本**（本来就是这个项目该守的纪律），
> 但把未来的切换面从「重写运行时 + 重写图」压到「只换装配层 + 自写一个 saver」。

| # | 契约 | 落地方式 |
| :-: | --- | --- |
| **1** | 🔴 **副作用不得放在 ``interrupt`` 之前** | ① ``interrupts`` / ``side_effects`` 声明 + :meth:`GraphDefinition.validate` **装配期互斥校验**；② ``tests/test_graph_replay_safety.py`` 的 **AST 用例**（扫真实节点函数源码） |
| **2** | 中断点必须**显式声明** | ``interrupts`` 必须与「代码里真实构造 ``InterruptRequest`` 的节点集合」**一致** —— 由用例比对，**漏声明即失败** |
| **3** | 有副作用的节点必须**显式声明** | ``side_effects`` 是「将来启用时间旅行 / 任意点重放时的幂等审计清单」 |
| 4 | 图定义保持**声明式** | :class:`GraphDefinition` 只描述节点 / 边 / 中断点，**不持有**连接、Session 或任何 IO 对象 |
| 5 | 节点函数只用 :class:`NodeContext` 的接口 | 只用 ``state`` / ``human_input()`` / ``last_decision``；**不得** import 本模块的下划线前缀内部符号 |
| 6 | Service 层只 import 运行时的**公开四件套** | 只允许 ``GraphOutcome`` / ``run_graph`` / ``resume_state``（+ ``registry.get_graph`` / ``successor_of``）；**不得** import ``*_graph`` 定义模块（由 ``test_layering.py`` 与本文件对应用例守） |

⚠️ **为何契约 1 要用「装配期抛错」这种激烈手段**：``registry`` 在**模块导入时**构图，
所以坏声明会让**进程起不来**。这是**故意的** ——
比起「能跑起来、但在人工复核恢复时把库写两遍 / 把模型调用计费两次」，
启动失败是**便宜得多**的失败方式。
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 共享状态（LangGraph 的 ``State`` 对应物）。**约定是可读写的普通 dict** ——
#: 让它可序列化（JSON）是硬要求，因为要落 ``agent_task.graph_state``。
AgentState = dict[str, Any]

#: 人工结论在 state 里的存放键（``{节点名: {decision, payload, comment, ...}}``）
HUMAN_KEY: Final[str] = "__human__"

#: 死循环保护：单次运行最多执行多少个节点。
#: ⚠️ 必须有 —— 「循环」是 F-A0.2 明确要支持的能力，
#: 而条件边写错方向时图会安静地转圈，没有上限就是一个把 CPU 跑满的 bug。
DEFAULT_MAX_STEPS: Final[int] = 50

GraphStatus = Literal["success", "waiting_human", "failed"]


# ============================================================================
# 异常
# ============================================================================
class NodeError(Exception):
    """节点业务失败（**不重试** —— 重试结果相同，只是拖慢失败反馈）。"""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class RetryableNodeError(Exception):
    """节点**可重试**失败（下游超时 / 连接失败 / 5xx）。"""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ============================================================================
# 数据结构
# ============================================================================
@dataclass(slots=True)
class InterruptRequest:
    """节点请求人工介入（F-A0.3）。

    ``interrupt_type`` 决定处理界面形态（见 ``schemas.agent.InterruptType``），
    ``payload`` 是待人工判断的内容（待复核字段 / 待确认成本 / 待合并的两份档案）。
    """

    interrupt_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    assignee_role: str | None = None
    #: 给人工看的说明（会随待办下发，**不得含未脱敏 PII** —— 由节点自行保证）
    note: str | None = None


@dataclass(slots=True)
class NodeResult:
    """节点执行结果。

    节点函数也可以**直接返回 dict**（等价于 ``NodeResult(update=dict)``）——
    那是绝大多数节点的写法；只有需要「跳转 / 中断」时才用本类。
    """

    #: 对共享状态的增量更新（浅合并）
    update: dict[str, Any] = field(default_factory=dict)
    #: 显式跳转（优先级高于 ``edges`` / ``routers``）
    goto: str | None = None
    #: 请求人工介入（**在挂起前先应用 ``update``**，让人工看到最新状态）
    interrupt: InterruptRequest | None = None


@dataclass(slots=True)
class NodeContext:
    """节点执行上下文（节点函数的唯一入参）。"""

    task_id: int
    tenant_id: int
    node_name: str
    state: AgentState
    #: 本节点的执行序号（从 1 起）
    step_index: int

    def human_input(self, node_name: str | None = None) -> dict[str, Any] | None:
        """取人工结论（默认取**本节点**的；恢复执行时由后继节点调用）。"""
        human = self.state.get(HUMAN_KEY) or {}
        return human.get(node_name or self.node_name)

    @property
    def last_decision(self) -> str | None:
        """最近一次人工结论（``approved`` / ``rejected``），便于链式判断。"""
        human = self.state.get(HUMAN_KEY) or {}
        for node_name in reversed(list(human)):
            entry = human.get(node_name) or {}
            if entry.get("decision"):
                return str(entry["decision"])
        return None


#: 节点函数签名
NodeFn = Callable[[NodeContext], "NodeResult | dict[str, Any] | None"]
#: 条件边函数签名（返回下一个节点名）
RouterFn = Callable[[AgentState], str | None]


@dataclass(slots=True)
class StepRecord:
    """一步的执行记录（→ ``agent_step`` TBL-26）。"""

    node_name: str
    step_index: int
    input: Any
    output: Any
    latency_ms: int
    status: int  # 1 成功 / 0 失败
    error_msg: str | None = None


@dataclass(slots=True)
class GraphOutcome:
    """一次图运行的结果。

    ``status`` 三态与 ``agent_task.status`` 的映射：

    | outcome | ``AgentTaskStatus`` |
    | --- | --- |
    | ``success`` | ``SUCCESS(3)`` |
    | ``waiting_human`` | ``WAITING_HUMAN(2)`` |
    | ``failed`` | ``FAILED(4)`` |
    """

    status: GraphStatus
    state: AgentState
    #: **下一个待执行**的节点（``waiting_human`` 时 = 恢复后要跑的节点；终态为 ``None``）
    next_node: str | None
    steps: list[StepRecord] = field(default_factory=list)
    #: 挂起时的人工请求（``status == "waiting_human"`` 才有值）
    interrupt: InterruptRequest | None = None
    #: 挂起的节点名（供 API-94 记录与恢复定位）
    interrupt_node: str | None = None
    error: str | None = None
    error_code: str | None = None


# ============================================================================
# 图定义
# ============================================================================
@dataclass(slots=True)
class GraphDefinition:
    """一张图（F-A0.7 的「可视化配置」即由本对象序列化而来）。

    ```text
    entry ──▶ node_a ──(edge)──▶ node_b ──(router)──▶ {node_c | node_d}
                   │
                   └─(interrupt)─▶ ⏸ waiting_human ──approved──▶ node_b
                                                   └─rejected──▶ reject_node
    ```

    :param retries: 「节点名 → 额外重试次数」；未列出的节点取 ``default_retries``
    :param ends: 终止节点集合（到达即 ``success``）
    :param interrupts: 🔴 **迁移控制项**：会请求人工介入的节点集合（模块 docstring 契约 2）
    :param side_effects: 🔴 **迁移控制项**：会产生外部副作用的节点集合（模块 docstring 契约 3）
    """

    name: str
    entry: str
    nodes: dict[str, NodeFn]
    edges: dict[str, str] = field(default_factory=dict)
    routers: dict[str, RouterFn] = field(default_factory=dict)
    ends: frozenset[str] = frozenset()
    retries: dict[str, int] = field(default_factory=dict)
    default_retries: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    #: 展示名（前端画图用）
    title: str | None = None
    #: 🔴 **中断节点**（返回值里带 ``InterruptRequest`` 的节点）。
    #: 与 ``side_effects`` **必须互斥** —— 中断节点在 LangGraph 下会被**重放**。
    interrupts: frozenset[str] = frozenset()
    #: 🔴 **有副作用的节点**（写库 / 调模型 / 发通知 / 投递队列）。
    #: 用途三处：① 与 ``interrupts`` 的互斥校验；② 可视化配置里标出「哪一步会产生外部影响」；
    #: ③ 将来启用「从任意检查点重放 / 时间旅行」时的**幂等审计清单** ——
    #: 一旦能从任意点重放，**所有**声明了副作用的节点都必须幂等。
    side_effects: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        # ⚠️ 只校验「声明自洽」，**不校验 ``entry`` 是否已注册** ——
        #    「起始节点未注册」在 run_graph 里有自己的失败路径（返回 failed 而不是抛），
        #    那是**运行期**语义；与「声明写错了」是两件事，混在一起会让
        #    运行期用例无法构造那种图。
        self.validate()

    def validate(self) -> None:
        """声明自洽性校验（**装配期 fail fast**）。

        :raises NodeError: 声明引用了未注册节点，或**中断节点被声明为有副作用**
        """
        declared = set(self.interrupts) | set(self.side_effects)
        unknown = sorted(declared - set(self.nodes))
        if unknown:
            raise NodeError(
                f"图 {self.name!r} 的声明引用了未注册节点 {unknown}；"
                f"已注册：{sorted(self.nodes)}"
            )
        clash = sorted(set(self.interrupts) & set(self.side_effects))
        if clash:
            raise NodeError(
                f"图 {self.name!r} 的中断节点 {clash} 同时被声明为「有副作用」。"
                "中断节点在 LangGraph 下会被**重放**（interrupt 之前的代码执行两次），"
                "因此中断节点内不得产生副作用。处置二选一："
                "① 把副作用移到中断之后的**后继节点**；"
                "② 让副作用幂等，并在测试里钉死它幂等。"
                "（见本模块 docstring「迁移控制项」契约 1）"
            )

    # ------------------------------------------------------------------
    def next_node(self, node_name: str, state: AgentState) -> str | None:
        """求 ``node_name`` 的后继节点（``routers`` 优先于 ``edges``）。

        :return: 后继节点名；``None`` = 图结束
        :raises NodeError: 路由函数返回了未注册的节点名
            （**必须报错而不是当结束** —— 静默结束会让「本该继续的任务」看起来成功了）
        """
        if node_name in self.routers:
            target = self.routers[node_name](state)
            if target is not None and target not in self.nodes:
                raise NodeError(
                    f"条件边 {node_name!r} 返回了未注册的节点 {target!r}；"
                    f"已注册：{sorted(self.nodes)}"
                )
            return target
        return self.edges.get(node_name)

    def retries_for(self, node_name: str) -> int:
        return max(0, int(self.retries.get(node_name, self.default_retries)))

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """序列化图结构（F-A0.7「可视化配置」的数据源）。

        ⚠️ 只输出**声明的结构**，不尝试推断运行时行为（如「实际会走哪条分支」）——
        那需要真实跑一遍，而这是一个只读的配置视图，前端用它画静态图。

        含**迁移控制项**的标记（``interrupt`` / ``side_effect``）：
        让「哪一步会挂起等人」、「哪一步会产生外部影响」在前端图上直接可见，
        而不是藏在节点函数体里。
        """
        return {
            "name": self.name,
            "title": self.title or self.name,
            "entry": self.entry,
            "max_steps": self.max_steps,
            # 🔴 每个节点带上「迁移控制项」的两个标记：前端据此把**中断点**画成菱形、
            #    把**有副作用的节点**加一个角标 —— 这两类节点是恢复/重放时最需要被看见的。
            "nodes": [
                {
                    "name": n,
                    "type": "node",
                    "interrupt": n in self.interrupts,
                    "side_effect": n in self.side_effects,
                }
                for n in self.nodes
            ],
            "edges": [
                {"from": src, "to": dst, "kind": "static"}
                for src, dst in sorted(self.edges.items())
            ]
            + [
                {"from": src, "to": None, "kind": "conditional"}
                for src in sorted(self.routers)
            ],
            "ends": sorted(self.ends),
            "interrupts": sorted(self.interrupts),
            "side_effects": sorted(self.side_effects),
        }


# ============================================================================
# 运行时
# ============================================================================
def run_graph(
    graph: GraphDefinition,
    state: AgentState | None = None,
    *,
    start_node: str | None = None,
    on_step: Callable[[NodeContext, StepRecord], None] | None = None,
    on_checkpoint: Callable[[AgentState, str | None], None] | None = None,
) -> GraphOutcome:
    """从 ``start_node``（缺省 = ``graph.entry``）开始执行，直到结束 / 中断 / 失败。

    🔴 **本函数不抛业务异常** —— 所有失败都变成 ``GraphOutcome(status="failed")``。
    理由：图执行发生在一次 HTTP 请求内（或后台任务内），
    把异常抛出去会让调用方需要 try/except 才能写出 ``agent_step`` 与
    ``agent_task`` 的终态 —— 「忘了 try」的后果是任务永远停在 ``running``，
    那是最难排查的状态（看不出是卡住了还是跑完了）。

    :param state: 已有状态（**恢复执行时传入检查点里的 state**）；
        会**原地修改**并作为 ``outcome.state`` 返回。
    :param on_step: 每步回调（由 ``checkpointer`` 用来落 ``agent_step`` 与检查点）
    :param on_checkpoint: **节点级检查点回调**（批次 10 补齐自研运行时唯一实质缺口）。
        每个节点**成功执行后**调用一次 ``on_checkpoint(state, next_node)``：
        ``next_node`` 是已解析出的后继（终态 / 结束时为 ``None``）。
        调用方据此把「state + 恢复起点」持久化 —— 中途崩溃后可从**下一个**节点续跑，
        而不是从头重放（重放会二次执行 ``side_effects`` 声明的副作用节点）。
        ⚠️ 本回调**只承载通知**，运行时不做任何 IO；失败步骤**不**触发检查点
        （检查点必须指向「确定成功过」的进度）。
    """
    state = state if state is not None else {}
    steps: list[StepRecord] = []
    current = start_node or graph.entry
    step_index = 0

    logger.info("图开始执行 [%s] 起点=%s steps上限=%s", graph.name, current, graph.max_steps)

    if current not in graph.nodes:
        logger.error(
            "图起始节点未注册 [%s] 起点=%s 已注册=%s",
            graph.name,
            current,
            sorted(graph.nodes),
        )
        return GraphOutcome(
            status="failed",
            state=state,
            next_node=None,
            steps=steps,
            error=f"起始节点未注册：{current!r}；已注册：{sorted(graph.nodes)}",
            error_code="NODE_NOT_FOUND",
        )

    while current is not None:
        if step_index >= graph.max_steps:
            # 死循环兜底：**明确失败**而不是静默成功
            logger.error(
                "图超过最大步数 [%s] 卡在节点=%s 已走=%s", graph.name, current, step_index
            )
            return GraphOutcome(
                status="failed",
                state=state,
                next_node=current,
                steps=steps,
                error=(
                    f"超过最大步数 {graph.max_steps}（当前节点 {current!r}）——"
                    "条件边可能形成死循环，请检查 routers"
                ),
                error_code="MAX_STEPS_EXCEEDED",
            )

        step_index += 1
        ctx = NodeContext(
            task_id=int(state.get("__task_id__") or 0),
            tenant_id=int(state.get("__tenant_id__") or 0),
            node_name=current,
            state=state,
            step_index=step_index,
        )
        attempt_limit = graph.retries_for(current) + 1
        record: StepRecord | None = None
        node_result: NodeResult | None = None
        failure: Exception | None = None

        for attempt in range(attempt_limit):
            started = time.perf_counter()
            #: 🔴 ``NodeError`` 声明「不重试」，但 ``range(attempt_limit)`` 已经定死，
            #: 单靠异常分支无法提前退出循环 —— 必须用一个显式标志在**记录完步骤后** break。
            non_retryable = False
            try:
                raw = graph.nodes[current](ctx)
                node_result = _as_node_result(raw)
                failure = None
            except RetryableNodeError as exc:
                failure = exc
                logger.warning(
                    "节点可重试失败 [%s.%s] 第 %s/%s 次：%s",
                    graph.name,
                    current,
                    attempt + 1,
                    attempt_limit,
                    exc,
                )
                if attempt + 1 < attempt_limit:
                    continue
                node_result = None
            except NodeError as exc:
                failure = exc
                node_result = None
                # ⚠️ 与网关「4xx 不重试」同一条推理：确定性失败再跑一遍结果相同，
                #    只是把失败反馈拖慢（而任务会一直停在 running，最难排查）。
                non_retryable = True
            except Exception as exc:  # noqa: BLE001 — 图是编排边界，未知异常一律结构化
                failure = exc
                logger.error(
                    "节点异常 [%s.%s]：%s\n%s",
                    graph.name,
                    current,
                    exc,
                    traceback.format_exc(limit=5),
                )
                if attempt + 1 < attempt_limit:
                    continue
                node_result = None

            latency_ms = int((time.perf_counter() - started) * 1000)
            record = StepRecord(
                node_name=current,
                step_index=step_index,
                input={"state_keys": sorted(state)},
                output=(
                    {"update_keys": sorted(node_result.update)}
                    if node_result is not None
                    else None
                ),
                latency_ms=latency_ms,
                status=1 if failure is None else 0,
                error_msg=None if failure is None else f"{type(failure).__name__}: {failure}",
            )
            if failure is None:
                break
            if non_retryable:
                break

        # 重试耗尽仍未成功 → 任务失败（**不静默跳过**，F-A0.4）
        if record is None or node_result is None:
            if record is not None:
                steps.append(record)
                if on_step is not None:
                    on_step(ctx, record)
            error_text = f"{type(failure).__name__}: {failure}" if failure else "节点失败"
            logger.error(
                "图节点失败 [%s.%s] step=%s latency=%sms error=%s",
                graph.name,
                current,
                step_index,
                record.latency_ms if record else None,
                error_text,
            )
            return GraphOutcome(
                status="failed",
                state=state,
                next_node=current,
                steps=steps,
                error=error_text,
                error_code=getattr(failure, "code", None) or "NODE_FAILED",
            )

        # ---- 应用状态更新（挂起前先落地，让待办里看到最新状态）----
        if node_result.update:
            state.update(node_result.update)

        steps.append(record)
        if on_step is not None:
            on_step(ctx, record)

        logger.info(
            "图节点完成 [%s.%s] step=%s latency=%sms update=%s",
            graph.name,
            current,
            step_index,
            record.latency_ms,
            sorted(node_result.update) or None,
        )

        # ---- 人工介入：**立即挂起**，不推进后继 ----
        if node_result.interrupt is not None:
            try:
                # 恢复后要跑的节点 —— 现在就算出来并落检查点，
                # 这样 API-94 的 ``next_node`` 与恢复时实际执行的节点**必然是同一个**
                pending = _resolve_next(graph, current, node_result, state)
            except NodeError as exc:
                logger.error("图边解析失败 [%s.%s] %s", graph.name, current, exc)
                return GraphOutcome(
                    status="failed",
                    state=state,
                    next_node=current,
                    steps=steps,
                    error=f"{type(exc).__name__}: {exc}",
                    error_code="EDGE_INVALID",
                )
            # 节点级检查点：挂起前把「state + 恢复起点」交给调用方持久化
            if on_checkpoint is not None:
                on_checkpoint(state, pending)
            logger.info(
                "图挂起等待人工 [%s.%s] 中断类型=%s 恢复点=%s",
                graph.name,
                current,
                node_result.interrupt.interrupt_type,
                pending,
            )
            return GraphOutcome(
                status="waiting_human",
                state=state,
                next_node=pending,
                steps=steps,
                interrupt=node_result.interrupt,
                interrupt_node=current,
            )

        # ---- 到达终止节点 → 成功 ----
        if current in graph.ends:
            if on_checkpoint is not None:
                on_checkpoint(state, None)
            logger.info("图执行成功 [%s] step=%s 终止节点=%s", graph.name, step_index, current)
            return GraphOutcome(
                status="success", state=state, next_node=None, steps=steps
            )

        # 求后继：路由函数返回未注册节点名必须**失败**而不是静默结束
        try:
            current = _resolve_next(graph, current, node_result, state)
        except NodeError as exc:
            logger.error("图边解析失败 [%s] 当前=%s %s", graph.name, current, exc)
            return GraphOutcome(
                status="failed",
                state=state,
                next_node=None,
                steps=steps,
                error=f"{type(exc).__name__}: {exc}",
                error_code="EDGE_INVALID",
            )
        # 节点级检查点：本节点已成功 + 后继已解析 → 调用方可安全持久化「进度 + 恢复起点」
        if on_checkpoint is not None:
            on_checkpoint(state, current)

    # 🔴 ``while`` 自然退出（``current is None`` = 最后一个节点没有后继）⇒ 图**正常走完**。
    #
    # ⚠️ 这一行是**批次 7 抓出的缺陷**：原实现只在「到达 ``ends``」时 ``return``，
    #    而「最后一个节点没有声明后继、也不在 ``ends`` 里」是最常见的收尾形态 ——
    #    此时函数**直接掉出循环、返回 ``None``**。后果不是报错而是**下游 AttributeError**：
    #    ``AgentService._persist_outcome`` 会取 ``outcome.status`` → 50000，
    #    而 ``agent_task`` 永远停在 ``running``（最难排查的一种状态）。
    #    ``tests/test_agent_graph.py::test_node_without_successor_ends_graph`` 钉死此行为。
    logger.info("图执行成功 [%s] step=%s 无后继节点，自然结束", graph.name, step_index)
    return GraphOutcome(status="success", state=state, next_node=None, steps=steps)


def _resolve_next(
    graph: GraphDefinition, current: str, node_result: NodeResult, state: AgentState
) -> str | None:
    """求后继节点：``goto`` > 条件边/静态边。带未注册校验。"""
    if node_result.goto is not None:
        if node_result.goto not in graph.nodes:
            raise NodeError(
                f"节点 {current!r} 的 goto 指向未注册节点 {node_result.goto!r}；"
                f"已注册：{sorted(graph.nodes)}"
            )
        return node_result.goto
    return graph.next_node(current, state)


def _as_node_result(raw: NodeResult | dict[str, Any] | None) -> NodeResult:
    """把节点返回值归一成 :class:`NodeResult`。"""
    if raw is None:
        return NodeResult()
    if isinstance(raw, NodeResult):
        return raw
    if isinstance(raw, dict):
        return NodeResult(update=dict(raw))
    raise NodeError(
        f"节点返回值类型不支持：{type(raw).__name__}（只接受 NodeResult / dict / None）"
    )


def resume_state(state: AgentState | None, node_name: str, decision: dict[str, Any]) -> AgentState:
    """把人工结论写进状态，返回**可继续执行**的 state（供 API-94 恢复用）。

    ```python
    state = resume_state(task_state, "field_review", {"decision": "approved", "payload": {...}})
    outcome = run_graph(graph, state, start_node="persist_talent")
    ```
    """
    merged: AgentState = dict(state or {})
    human = dict(merged.get(HUMAN_KEY) or {})
    human[node_name] = dict(decision)
    merged[HUMAN_KEY] = human
    return merged
