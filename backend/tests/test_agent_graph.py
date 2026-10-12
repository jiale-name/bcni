"""CU-13 · Agent 图运行时 / 阈值引擎 / 注册表（L1 契约层，**无外部依赖**）。

分层（沿用 CU-07 / CU-08 已确立的口径）
--------------------------------------
- **L1（本文件）**：不依赖 MySQL / Redis，任何机器都能跑 —— 图运行时的全部分支、
  注册表、阈值引擎的确定性、占位节点的行为、``NodeError`` 不重试。
- **L2（``tests/test_agent_flow.py``）**：依赖 DS，逐条 skip 而非 fail。

🔴 为什么图运行时值得这么密的用例
--------------------------------
它是**唯一**能把一次 Agent 执行导向 5 种结局（success / waiting_human / failed /
MAX_STEPS_EXCEEDED / EDGE_INVALID）的地方。它一旦出错，现象全是「静默」的：
- 把未知后继当结束 ⇒ 任务**看起来成功了**，实际少跑了后半段；
- 不兜死循环 ⇒ 请求挂住直到超时，而日志里什么都不明显；
- 抛异常而不是返回 ``failed`` ⇒ 任务**永远停在 running**（最难排查的状态）。

因此这里对每一条出口都单独钉一个用例，并对「任何异常都不得外抛」做一次**全域**断言。

覆盖的验收编号
--------------
| 编号 | 断言 | 位置 |
| --- | --- | --- |
| **AC-38** | 条件边返回未注册节点 → 结构化失败（**不静默结束**） | ``test_router_returning_unknown_node_fails`` |
| **AC-39** | 超过 ``max_steps`` → ``MAX_STEPS_EXCEEDED`` | ``test_max_steps_guard_breaks_infinite_loop`` |
| **AC-40** | 节点抛任意异常 → ``failed`` 且**不外抛** | ``test_graph_never_raises_for_any_node_failure`` |
| **AC-41** | ``NodeError`` **不重试**（与 docstring 一致） | ``test_node_error_is_not_retried`` |
| **AC-42** | ``RetryableNodeError`` 真重试，成功后继续 | ``test_retryable_error_is_retried_then_succeeds`` |
| **F-A0.3** | ``interrupt`` 立即挂起，且**预先算好** ``next_node`` | ``test_interrupt_suspends_and_precomputes_next_node`` |
| F-A0.2 | ``routers`` 优先于 ``edges`` | ``test_router_takes_precedence_over_edge`` |
| AC-36 补 | «降级不伪造»：占位节点走人工复核而非编造数据 | ``test_parse_graph_placeholder_requests_review`` |
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph import (
    DEFAULT_MAX_STEPS,
    HUMAN_KEY,
    GraphDefinition,
    InterruptRequest,
    NodeContext,
    NodeError,
    NodeResult,
    RetryableNodeError,
    resume_state,
    run_graph,
)
from app.agents.registry import available_task_types, describe_all, get_graph, has_graph, successor_of
from app.core.threshold import (
    HIGH_IMPACT_FIELDS,
    OVERALL_CONFIDENCE_FLOOR,
    ThresholdEngine,
)
from app.exceptions import BizError, ErrorCode


def _ctx_names(graph: GraphDefinition) -> list[str]:
    """跑一遍图，返回**实际执行的节点名序列**（用于断言路径）。"""
    return [s.node_name for s in _run(graph).steps]


def _run(graph: GraphDefinition, state: dict[str, Any] | None = None):
    return run_graph(graph, dict(state or {}))


# ============================================================================
# L1 · 线性推进 / 终止
# ============================================================================
def test_linear_chain_runs_to_end() -> None:
    """``a → b → c(ends)``：正常路径应 ``success``、``next_node=None``、步骤有序。"""
    graph = GraphDefinition(
        name="t_linear",
        entry="a",
        nodes={
            "a": lambda ctx: {"x": 1},
            "b": lambda ctx: {"y": ctx.state["x"] + 1},
            "c": lambda ctx: None,
        },
        edges={"a": "b", "b": "c"},
        ends=frozenset({"c"}),
    )
    outcome = _run(graph)

    assert outcome.status == "success"
    assert outcome.next_node is None
    assert [s.node_name for s in outcome.steps] == ["a", "b", "c"]
    assert [s.step_index for s in outcome.steps] == [1, 2, 3]
    # 节点写入的状态被累积（``b`` 能读到 ``a`` 写的 ``x``）
    assert outcome.state["x"] == 1
    assert outcome.state["y"] == 2
    assert all(s.status == 1 for s in outcome.steps)


def test_start_node_not_registered_fails_without_raising() -> None:
    """起始节点名写错 → ``NODE_NOT_FOUND``（而不是 KeyError 冒到 FastAPI 变 50000）。"""
    graph = GraphDefinition(name="t_entry", entry="nope", nodes={"a": lambda ctx: None})
    outcome = _run(graph)

    assert outcome.status == "failed"
    assert outcome.error_code == "NODE_NOT_FOUND"
    assert outcome.steps == []


def test_node_without_successor_ends_graph() -> None:
    """未声明后继且非 ``ends`` ⇒ 图结束。

    ⚠️ 这里的语义是「**显式无后继**就是结束」，与「路由返回未注册节点」是**两件事**
    （后者必须失败，见下一条）。两者混淆过一次会让「配置漏了一条边」变成静默成功。
    """
    graph = GraphDefinition(name="t_noedge", entry="a", nodes={"a": lambda ctx: {"z": 1}})
    outcome = _run(graph)

    assert outcome.status == "success"
    assert outcome.next_node is None
    assert outcome.state["z"] == 1


# ============================================================================
# L1 · 条件边（F-A0.2）
# ============================================================================
def test_router_takes_precedence_over_edge() -> None:
    """同一节点同时有 ``edges`` 与 ``routers`` 时，**routers 优先**。

    ⚠️ 这条不是形式主义：``parse_graph`` 里 ``score_confidence`` 两种都声明过一版，
    静默地「走了静态边」会让抽样复核分支永远不被执行（而那正是复核漏斗的入口）。
    """
    graph = GraphDefinition(
        name="t_router",
        entry="a",
        nodes={"a": lambda ctx: {"go": "right"}, "left": lambda ctx: None, "right": lambda ctx: None},
        edges={"a": "left"},
        routers={"a": lambda state: state.get("go")},
        ends=frozenset({"left", "right"}),
    )
    assert _ctx_names(graph) == ["a", "right"]


def test_router_returning_unknown_node_fails() -> None:
    """🔴 路由返回未注册节点名 → ``EDGE_INVALID`` 结构化失败（**绝不当作图结束**）。

    为什么必须失败：静默结束会让「本该继续的任务」以 ``success`` 收尾 ——
    上游拿到「成功」却缺少后半段的全部产出，而**任何日志里都看不出异常**。
    """
    graph = GraphDefinition(
        name="t_badrouter",
        entry="a",
        nodes={"a": lambda ctx: None},
        routers={"a": lambda state: "ghost"},
    )
    outcome = _run(graph)

    assert outcome.status == "failed"
    assert outcome.error_code == "EDGE_INVALID"
    assert "ghost" in (outcome.error or "")


def test_router_returning_none_ends_graph() -> None:
    """``None`` 是**合法的「结束」**信号（与「未注册节点」区分开）。"""
    graph = GraphDefinition(
        name="t_none",
        entry="a",
        nodes={"a": lambda ctx: None},
        routers={"a": lambda state: None},
    )
    assert _run(graph).status == "success"


def test_explicit_goto_beats_router() -> None:
    """``NodeResult.goto`` 优先级**最高**（高于 routers / edges）—— 节点自己做最终裁决。"""
    graph = GraphDefinition(
        name="t_goto",
        entry="a",
        nodes={"a": lambda ctx: NodeResult(goto="c"), "b": lambda ctx: None, "c": lambda ctx: None},
        edges={"a": "b"},
        routers={"a": lambda state: "b"},
        ends=frozenset({"b", "c"}),
    )
    assert _ctx_names(graph) == ["a", "c"]


# ============================================================================
# L1 · 死循环兜底（AC-39）
# ============================================================================
def test_max_steps_guard_breaks_infinite_loop() -> None:
    """自环（``a → a``）必须在 ``max_steps`` 处**明确失败**，而不是把请求挂住。"""
    graph = GraphDefinition(
        name="t_loop",
        entry="a",
        nodes={"a": lambda ctx: {"n": ctx.state.get("n", 0) + 1}},
        edges={"a": "a"},
        max_steps=3,
    )
    outcome = _run(graph)

    assert outcome.status == "failed"
    assert outcome.error_code == "MAX_STEPS_EXCEEDED"
    assert len(outcome.steps) == 3
    # ``next_node`` 指向「卡住的那个节点」—— 排查时直接告诉你循环在哪
    assert outcome.next_node == "a"


def test_default_max_steps_matches_config_expectation() -> None:
    """默认上限存在且为正 —— 它是**唯一**防住条件边死循环的闸门。"""
    assert DEFAULT_MAX_STEPS > 0


# ============================================================================
# L1 · 失败与重试（AC-40 / AC-41 / AC-42）
# ============================================================================
def test_node_error_is_not_retried() -> None:
    """🔴 ``NodeError`` **不重试**（类 docstring 的承诺必须与实现一致）。

    这一条是**批次 7 抓出的实现/声明不符**：原实现在 ``retries_for > 0`` 时
    仍会重跑 ``NodeError``（``range(attempt_limit)`` 已定死，异常分支拦不住循环）。
    后果不是错误结果，而是**失败反馈被拖慢 retries 倍**，
    以及日志里出现「同一个确定性错误重复 N 次」的噪声。
    """
    calls: list[int] = []

    def boom(ctx: NodeContext) -> NodeResult:
        calls.append(ctx.step_index)
        raise NodeError("确定性失败", code="E_PARSE")

    graph = GraphDefinition(
        name="t_nodeerr", entry="a", nodes={"a": boom}, retries={"a": 3}
    )
    outcome = _run(graph)

    assert calls == [1], f"NodeError 被重试了 {len(calls)} 次（应为 1 次）"
    assert outcome.status == "failed"
    # 节点自定义的 code 必须透传（否则排查时只剩「NODE_FAILED」这种无信息量的码）
    assert outcome.error_code == "E_PARSE"
    # 失败那一步**仍要有记录**：没有记录就等于「任务莫名失败、回放里空无一物」
    assert len(outcome.steps) == 1
    assert outcome.steps[0].status == 0


def test_retryable_error_is_retried_then_succeeds() -> None:
    """``RetryableNodeError`` 真重试；成功后继续推进（**反向对照**，防「一律不重试」也算过）。"""
    calls: list[int] = []

    def flaky(ctx: NodeContext) -> NodeResult:
        calls.append(ctx.step_index)
        if len(calls) < 3:
            raise RetryableNodeError("下游超时")
        return {"ok": True}

    graph = GraphDefinition(
        name="t_retry", entry="a", nodes={"a": flaky}, retries={"a": 2}, ends=frozenset({"a"})
    )
    outcome = _run(graph)

    assert calls == [1, 1, 1], "应在同一节点上重试 3 次（1 + retries=2）"
    assert outcome.status == "success"
    assert outcome.state["ok"] is True


def test_arbitrary_exception_is_structured_not_raised() -> None:
    """节点抛 ``ZeroDivisionError`` 这类**未知**异常也必须被结构化（图是编排边界）。"""

    def boom(ctx: NodeContext) -> None:
        raise ZeroDivisionError("division by zero")

    graph = GraphDefinition(name="t_unknown", entry="a", nodes={"a": boom})
    outcome = _run(graph)

    assert outcome.status == "failed"
    assert outcome.error_code == "NODE_FAILED"
    assert "ZeroDivisionError" in (outcome.error or "")


@pytest.mark.parametrize(
    "exc",
    [
        NodeError("x"),
        RetryableNodeError("y"),
        ValueError("z"),
        RuntimeError("w"),
        MemoryError("m"),
        KeyError("k"),
    ],
)
def test_graph_never_raises_for_any_node_failure(exc: Exception) -> None:
    """🔴 **全域**断言：无论节点抛什么，``run_graph`` 都只返回 ``GraphOutcome``。

    用参数化而不是「写一个 except Exception 的大断言」，是为了让**新增的异常类型**
    也走同一条检查（``MemoryError`` / ``KeyError`` 这类不是 ``Exception`` 子类直觉里会漏）。
    """

    def boom(ctx: NodeContext) -> None:
        raise exc

    graph = GraphDefinition(name=f"t_any_{type(exc).__name__}", entry="a", nodes={"a": boom})
    outcome = _run(graph)  # ← 这行不许抛
    assert outcome.status == "failed"


def test_on_step_callback_receives_every_step() -> None:
    """``on_step`` 是落 ``agent_step`` / 检查点的唯一挂钩点，必须每步都被调用一次。"""
    seen: list[tuple[str, int, int]] = []

    graph = GraphDefinition(
        name="t_cb",
        entry="a",
        nodes={"a": lambda ctx: {"k": 1}, "b": lambda ctx: None},
        edges={"a": "b"},
        ends=frozenset({"b"}),
    )
    outcome = run_graph(
        graph, {}, on_step=lambda ctx, record: seen.append((ctx.node_name, record.step_index, record.status))
    )

    assert seen == [("a", 1, 1), ("b", 2, 1)]
    assert len(outcome.steps) == 2


def test_checkpoint_state_is_returned_by_reference() -> None:
    """``outcome.state`` 就是传入的那个 dict（原地更新）—— 检查点依赖这一点。

    若改成「复制一份再返回」，``AgentService._persist_outcome`` 里
    「先写库、再拿 task.graph_state」的顺序会拿到旧状态，现象是
    「检查点总是落后一步」，而单测若只断言内容相等则完全看不出来。
    """
    state: dict[str, Any] = {}
    graph = GraphDefinition(
        name="t_ref", entry="a", nodes={"a": lambda ctx: {"v": 42}}, ends=frozenset({"a"})
    )
    outcome = run_graph(graph, state)

    assert outcome.state is state
    assert state["v"] == 42


# ============================================================================
# L1 · 人工介入（F-A0.3）
# ============================================================================
def test_interrupt_suspends_and_precomputes_next_node() -> None:
    """🔴 挂起时 **``next_node`` 就已算好并随 outcome 返回**（不是恢复时才求）。

    这是「接口说下一步执行 A，就真的是 A」的落地方式：API-94 的响应字段与
    恢复时实际执行的节点取自**同一个** ``_resolve_next`` 调用结果。
    """
    graph = GraphDefinition(
        name="t_intr",
        entry="a",
        nodes={
            "a": lambda ctx: NodeResult(
                update={"draft": 1},
                interrupt=InterruptRequest(
                    interrupt_type="field_review",
                    payload={"field": "salary"},
                    assignee_role="HR",
                ),
            ),
            "b": lambda ctx: {"done": True},
        },
        edges={"a": "b"},
        ends=frozenset({"b"}),
    )
    outcome = _run(graph)

    assert outcome.status == "waiting_human"
    assert outcome.interrupt_node == "a"
    assert outcome.next_node == "b"
    assert outcome.interrupt is not None
    assert outcome.interrupt.interrupt_type == "field_review"
    assert outcome.interrupt.assignee_role == "HR"
    # 挂起前**先应用** update：否则人工在待办里看到的是一份过期状态
    assert outcome.state["draft"] == 1
    # 且**不推进后继** —— ``b`` 一次都没跑
    assert [s.node_name for s in outcome.steps] == ["a"]


def test_interrupt_with_invalid_successor_returns_edge_invalid_not_waiting() -> None:
    """挂起点的后继若无效 → 立刻 ``EDGE_INVALID``，**不能**先挂起再在恢复时炸。

    若先挂起，结果是：人处理完待办、点「通过」，任务才报错 ——
    人工白做一次，且待办记录显示「已通过」而任务却是失败的。
    """
    graph = GraphDefinition(
        name="t_intr_bad",
        entry="a",
        nodes={
            "a": lambda ctx: NodeResult(interrupt=InterruptRequest(interrupt_type="generic"))
        },
        routers={"a": lambda state: "ghost"},
    )
    outcome = _run(graph)

    assert outcome.status == "failed"
    assert outcome.error_code == "EDGE_INVALID"


def test_resume_state_records_human_decision_under_human_key() -> None:
    """``resume_state`` 把人工结论写进 ``state[HUMAN_KEY][node]``，供后继节点读取。

    ⚠️ 它返回**新 dict**（不是原地更新）—— 与 ``run_graph`` 的 ``outcome.state`` 不同。
    区别是刻意的：``run_graph`` 原地更新的是「执行中不断累积的状态」，
    而 ``resume_state`` 是「基于检查点派生出一份可继续执行的状态」，
    调用方（``AgentService._resume``）随后还会往上面补 ``__task_id__`` / ``__tenant_id__``。
    """
    state = {"draft": 1}
    resumed = resume_state(
        state, "field_review", {"decision": "approved", "payload": {"value": "7"}, "comment": "已核对"}
    )

    assert resumed is not state, "resume_state 应返回新 dict（不污染检查点原值）"
    assert HUMAN_KEY not in state, "原 state 不得被写入"
    human = resumed[HUMAN_KEY]
    assert human["field_review"]["decision"] == "approved"
    assert human["field_review"]["payload"] == {"value": "7"}
    assert human["field_review"]["comment"] == "已核对"


def test_node_context_human_input_and_last_decision() -> None:
    """节点通过 ``ctx.human_input()`` / ``ctx.last_decision`` 读取人工结论。"""
    captured: dict[str, Any] = {}

    def probe(ctx: NodeContext) -> None:
        captured["mine"] = ctx.human_input()
        captured["other"] = ctx.human_input("other_node")
        captured["last"] = ctx.last_decision

    graph = GraphDefinition(name="t_human", entry="probe", nodes={"probe": probe})
    run_graph(
        graph,
        {
            HUMAN_KEY: {
                "other_node": {"decision": "rejected", "comment": "不对"},
                "probe": {"decision": "approved"},
            }
        },
    )

    assert captured["mine"] == {"decision": "approved"}
    assert captured["other"]["decision"] == "rejected"
    assert captured["last"] == "approved"


def test_node_context_carries_task_and_tenant() -> None:
    """``__task_id__`` / ``__tenant_id__`` 从 state 传入 ``NodeContext``（节点落库要用）。"""
    captured: dict[str, Any] = {}

    def probe(ctx: NodeContext) -> None:
        captured["task_id"] = ctx.task_id
        captured["tenant_id"] = ctx.tenant_id

    run_graph(
        GraphDefinition(name="t_ids", entry="probe", nodes={"probe": probe}),
        {"__task_id__": 123, "__tenant_id__": 9},
    )

    assert captured == {"task_id": 123, "tenant_id": 9}


# ============================================================================
# L1 · 注册表
# ============================================================================
def test_registry_has_parse_graph() -> None:
    assert has_graph("parse")
    assert "parse" in available_task_types()


def test_registry_unknown_task_type_raises_40001() -> None:
    """未注册的任务类型 → **40001**（而不是 40401 / 500）。

    语义是「**请求的参数不合法**」而不是「资源不存在」：图定义是**编译进代码的**，
    传一个不存在的 ``task_type`` 属于调用方拼错，与「某个 ID 不存在」是两回事。
    """
    with pytest.raises(BizError) as exc:
        get_graph("no_such_graph")
    assert int(exc.value.code) == int(ErrorCode.PARAM_INVALID)


def test_successor_of_matches_graph_definition() -> None:
    """``successor_of`` 必须与 ``GraphDefinition.next_node`` **同一份逻辑**。

    ``field_review`` 的后继由 router 决定（读 ``HUMAN_KEY`` 里的人工结论）：
    通过 → ``persist_talent``，驳回 → ``manual_reject``。

    🔴 这也解释了为什么 ``AgentService._resume`` **必须重新求一次** ``next_node``：
    ``run_graph`` 在挂起时算出的那个 ``next_node`` 是**基于「还没有人工结论」的状态**，
    router 只能走默认分支。若拿它当恢复起点，驳回就会走成「入库」——
    而 API-94 报出的 ``next_node`` 也会与实际执行的节点不一致。
    """
    approved = resume_state({}, "field_review", {"decision": "approved"})
    rejected = resume_state({}, "field_review", {"decision": "rejected"})

    assert successor_of("parse", "field_review", approved) == "persist_talent"
    assert successor_of("parse", "field_review", rejected) == "manual_reject"


def test_successor_of_degrades_silently_unlike_get_graph() -> None:
    """🔴 ``successor_of`` 与 ``get_graph`` 对「未知类型」的行为**刻意不同**。

    - ``get_graph`` **抛 40001**：它是主流程入口，类型拼错必须立刻暴露；
    - ``successor_of`` **返回 ``None``**：它是**展示用途**（API-94 的 ``next_node`` 字段），
      拿不到就显示 ``null``。在这里抛异常会让「查一下下一步跑什么」这种只读补全
      把整个写操作带崩 —— 一个展示字段不该有这种杀伤力。

    这条断言存在的意义：**将来有人为了「统一口径」把两处改成一样时，这里会红**。
    统一之前请先想清楚哪一边该让步（答案是 ``successor_of`` 不炸主流程）。
    """
    assert successor_of("no_such_graph", "a", {}) is None
    assert successor_of("parse", "no_such_node", {}) is None
    with pytest.raises(BizError) as exc:
        get_graph("no_such_graph")
    assert int(exc.value.code) == int(ErrorCode.PARAM_INVALID)


def test_describe_all_exposes_structure_for_visualisation() -> None:
    """F-A0.7「可视化配置」的数据源：只输出**声明的结构**。"""
    described = describe_all()
    assert described and all("nodes" in item and "edges" in item for item in described)
    parse = next(item for item in described if item["name"] == "parse")
    assert parse["entry"] in [n["name"] for n in parse["nodes"]]
    kinds = {edge["kind"] for edge in parse["edges"]}
    assert "conditional" in kinds, "parse 图必须暴露条件边，否则前端画不出分支"


# ============================================================================
# L1 · parse 图：占位实现**不伪造数据**（N0 未就绪时的正确行为）
# ============================================================================
def test_parse_graph_placeholder_requests_review() -> None:
    """🔴 N0（抽取能力）未就绪时，占位节点走**人工复核**而不是编造字段值。

    这是铁律 10 的直接推论：**确定性结论必须来自确定性组件**。
    一个「先返回假数据、以后再接真模型」的占位实现，会让整条链路
    （复核漏斗 → 标签 → 入库）在演示里看起来很完整，而**没有任何地方标明它是假的**。
    """
    graph = get_graph("parse")
    outcome = run_graph(
        graph,
        {"__task_id__": 1, "__tenant_id__": 1, "file_name": "a.pdf", "text": ""},
    )
    # 未就绪 ⇒ 走到 field_review 挂起（等人工），而不是 success
    assert outcome.status == "waiting_human"
    assert outcome.interrupt is not None
    assert outcome.interrupt.interrupt_type == "field_review"


def test_parse_graph_reject_path_leads_to_manual_reject() -> None:
    """驳回分支：``field_review`` 由人工驳回 → ``manual_reject``（**不是**静默丢弃）。

    接口 §3 备注明确「``rejected`` 会把任务导向失败分支，不静默丢弃」。

    ⚠️ 本用例刻意**不**用挂起时算出的 ``outcome.next_node`` 作为恢复起点 ——
    那是「还没有人工结论」时算出来的默认分支（``persist_talent``）。
    恢复起点必须用 ``successor_of`` + **已含人工结论的 state** 重新求，见上一条用例。
    """
    graph = get_graph("parse")
    state = {"__task_id__": 1, "__tenant_id__": 1, "file_name": "a.pdf"}
    first = run_graph(graph, state)
    assert first.status == "waiting_human"

    resumed = resume_state(first.state, first.interrupt_node or "field_review", {"decision": "rejected"})
    start = successor_of("parse", first.interrupt_node or "field_review", resumed)
    assert start == "manual_reject"

    second = run_graph(graph, resumed, start_node=start)
    assert "manual_reject" in [s.node_name for s in second.steps]
    assert second.status in {"success", "failed"}


# ============================================================================
# L1 · 阈值引擎（确定性组件，铁律 10）
# ============================================================================
def test_high_impact_field_below_threshold_needs_review() -> None:
    """高影响字段**低于阈值**必须复核（规则 1）。"""
    decision = ThresholdEngine.needs_manual_review(
        {"name": 0.5, "phone": 0.99}, sample_key="seed-1", threshold=0.85, sample_rate=0.0
    )
    assert decision.needed is True
    assert decision.fields == ["name"]
    assert "high_impact" in decision.reasons


def test_high_impact_field_with_high_confidence_needs_no_review() -> None:
    """**反向对照**：高影响字段但置信度达标 ⇒ 不复核（「一律复核」不能算通过）。

    这条与上一条成对存在，否则一个「见到高影响字段就复核」的实现也能让上一条通过。
    """
    decision = ThresholdEngine.needs_manual_review(
        {"name": 0.99, "phone": 0.97}, sample_key="seed-2", threshold=0.85, sample_rate=0.0
    )
    assert decision.needed is False
    assert decision.fields == []


def test_low_impact_below_threshold_is_not_reviewed_when_sampling_off() -> None:
    """低影响字段低于阈值、且抽样率为 0 ⇒ 不复核（抽样率是唯一的控制旋钮）。

    ⚠️ 数据构造有讲究：整体均值必须 **≥** ``OVERALL_CONFIDENCE_FLOOR``，
    否则会先命中规则 3（整体过低 → 全量复核），这个用例就测不到规则 2 了。

    ⚠️ 另一处同样有讲究：**必须带一个高置信的 ``name``**。批次 21 起
    「无姓名」本身是一条**文件级**理由（规则 0，``name_missing``）——
    少了它，本用例会被规则 0 命中而测不到规则 2（这正是本批全量回归抓到的失效点）。
    """
    confidences = {"name": 0.99, "n1": 0.99, "n2": 0.99, "n3": 0.99, "nickname": 0.10}
    assert sum(confidences.values()) / len(confidences) >= OVERALL_CONFIDENCE_FLOOR

    decision = ThresholdEngine.needs_manual_review(
        confidences, sample_key="seed-3", threshold=0.85, sample_rate=0.0
    )
    assert decision.needed is False
    assert decision.fields == []


def test_low_impact_below_threshold_is_reviewed_when_sampled() -> None:
    """**反向对照**：同一条数据、抽样率 1.0 ⇒ 必进复核（证明确实是抽样在起作用）。

    （同上：带 ``name`` 以隔离规则 0 的干扰。）
    """
    confidences = {"name": 0.99, "n1": 0.99, "n2": 0.99, "n3": 0.99, "nickname": 0.10}
    decision = ThresholdEngine.needs_manual_review(
        confidences, sample_key="seed-3", threshold=0.85, sample_rate=1.0
    )
    assert decision.needed is True
    assert decision.fields == ["nickname"]
    assert "sampled" in decision.reasons


def test_low_impact_sampling_is_deterministic() -> None:
    """低影响字段的抽样必须**确定性**：同一个 ``sample_key`` 反复判定结果一致。

    若用 ``random.random()``，同一份档案每次跑都会得到不同的复核结论 ——
    使用者会认为「系统不稳定」，而真正的问题是「结论不可复现」。
    """
    results = [
        ThresholdEngine.needs_manual_review(
            {"nickname": 0.1}, sample_key="stable-key", threshold=0.85, sample_rate=0.5
        ).needed
        for _ in range(20)
    ]
    assert len(set(results)) == 1, "同一 sample_key 的抽样结论必须稳定"


def test_sampling_rate_one_reviews_everything_rate_zero_reviews_nothing() -> None:
    """``rate=1.0`` 必抽中、``rate=0.0`` 必不抽中（抽样函数的两个边界）。"""
    assert ThresholdEngine._sampled("k", "f", 1.0) is True
    assert ThresholdEngine._sampled("k", "f", 0.0) is False
    assert ThresholdEngine._sampled("k", "f", -0.5) is False


def test_overall_confidence_floor_triggers_full_review() -> None:
    """整体置信度低于下限 → 规则 3 命中，**全部字段**进入复核（不是只挑低的）。

    部分字段低分往往说明「整份抽取质量可疑」，只挑低分字段会漏掉那些
    「明明抽错了、但模型很有信心」的字段。
    """
    low = {"nickname": 0.1, "hobby": 0.2}
    assert sum(low.values()) / len(low) < OVERALL_CONFIDENCE_FLOOR

    decision = ThresholdEngine.needs_manual_review(low, sample_key="k", threshold=0.85, sample_rate=0.0)
    assert decision.needed is True
    assert decision.fields == sorted(low), "规则 3 应把**全部**字段纳入复核"
    assert "overall_low" in decision.reasons


def test_empty_confidences_requires_review() -> None:
    """一个字段都没抽出来 ⇒ **必须复核**（不是「无需复核」）。

    空结果最容易被当成「没什么要看的」，而它恰恰意味着这次抽取彻底失败。
    """
    decision = ThresholdEngine.needs_manual_review({}, sample_key="k")
    assert decision.needed is True
    assert "no_fields" in decision.reasons


def test_decision_is_explainable() -> None:
    """判定结果必须带 ``reasons`` / ``as_dict()`` —— 复核队列要能解释「为什么进来了」。"""
    decision = ThresholdEngine.needs_manual_review(
        {"name": 0.1}, sample_key="k", threshold=0.85, sample_rate=0.0
    )
    payload = decision.as_dict()
    assert set(payload) == {"needed", "fields", "reasons"}
    assert payload["reasons"], "命中了规则却不说明原因，排查时无从下手"


def test_high_impact_fields_cover_the_documented_critical_columns() -> None:
    """高影响字段集合必须覆盖需求文档 §3 的关键字段。

    ⚠️ 空集会让「高影响必复核」这条规则**永远不触发**（且上面那些用例照样通过），
    故这里对**内容**做断言，而不是只断言「非空」。
    """
    assert {"name", "phone", "email", "education_level", "work_experience"} <= HIGH_IMPACT_FIELDS


def test_confidence_of_extracts_from_extraction_payload() -> None:
    """``confidence_of`` 把 ``{字段: {value, confidence}}`` 摘成 ``{字段: 置信度}``。

    🔴 关键约定：**没有 ``confidence`` 的字段按 ``default=1.0`` 处理，而不是 0**。
    按 0 会让每一个未标注置信度的字段都触发复核，把人工队列瞬间塞满 ——
    这是「默认值选错导致功能被淹没」的典型。
    """
    extracted = {
        "name": {"value": "张三", "confidence": 0.93},
        "phone": {"value": "138****8888", "confidence": "0.77"},  # 字符串也要能转
        "education_level": {"value": "本科"},                     # 无 confidence
        "broken": {"value": "x", "confidence": "not-a-number"},   # 非法值
    }
    out = ThresholdEngine.confidence_of(extracted)

    assert out["name"] == 0.93
    assert out["phone"] == 0.77
    assert out["education_level"] == 1.0, "无 confidence 应取 default=1.0（不塞爆复核队列）"
    assert out["broken"] == 1.0, "非法值也应回落到 default，而不是抛异常"
    # 显式 default 生效
    assert ThresholdEngine.confidence_of({"a": {"value": 1}}, default=0.5)["a"] == 0.5
