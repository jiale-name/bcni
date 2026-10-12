"""节点级检查点（批次 10 · CU-14 开工前置项）。

🔴 背景：自研运行时唯一实质缺口 —— 检查点是 **run 级**（``_persist_outcome`` 只在
``run_graph`` 返回后落一次）。多节点任务跑到一半崩溃，只能从头重跑，
已执行过的 ``side_effects`` 节点（写库 / 调模型计费）会被执行两遍。

本文件钉死三层语义：
1. **运行时**（L1，无依赖）：``on_checkpoint`` 在「每个节点成功后」以
   ``(state, next_node)`` 触发 —— 终态 ``next_node=None``、中断时 = 恢复起点、
   失败步骤**不**触发；
2. **服务层**（L1，替身）：``AgentService._checkpoint_callback`` 把 state 与
   恢复起点刷进 ``agent_task``，且**不覆盖**终态回拨；
3. **接线**：``agent_service`` 的三处 ``run_graph`` 调用点必须真的传了
   ``on_checkpoint``（防「模块写好、没接线」的假完成 —— 与限流中间件同一条教训）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.agents import graph as graph_mod
from app.agents.graph import (
    GraphDefinition,
    NodeError,
    NodeResult,
    InterruptRequest,
    run_graph,
)

# ============================================================================
# ① 运行时语义（L1）
# ============================================================================
def _linear_graph() -> GraphDefinition:
    """a → b → c(ends)。b 会把文本写进 state（供断言检查点携带最新状态）。"""
    return GraphDefinition(
        name="linear",
        entry="a",
        nodes={
            "a": lambda ctx: NodeResult(update={"seen": ["a"]}),
            "b": lambda ctx: NodeResult(update={"seen": list(ctx.state["seen"]) + ["b"]}),
            "c": lambda ctx: None,
        },
        edges={"a": "b", "b": "c"},
        ends=frozenset({"c"}),
    )


def test_checkpoint_fires_after_each_node_with_resolved_successor() -> None:
    calls: list[tuple[list[str], str | None]] = []
    outcome = run_graph(_linear_graph(), on_checkpoint=lambda s, n: calls.append((list(s["seen"]), n)))
    assert outcome.status == "success"
    # a 成功后 → 恢复起点 b；b 成功后 → 恢复起点 c；c 是终态 → None
    assert calls == [(["a"], "b"), (["a", "b"], "c"), (["a", "b"], None)]


def test_checkpoint_state_is_the_latest_mutation() -> None:
    seen: list[dict[str, Any]] = []
    run_graph(_linear_graph(), on_checkpoint=lambda s, _n: seen.append(dict(s)))
    assert seen[-1]["seen"] == ["a", "b"]


def test_interrupt_checkpoint_carries_resume_node() -> None:
    """中断节点成功执行（它本身没失败）→ 检查点 = 恢复起点（人工结论的后继）。"""
    graph = GraphDefinition(
        name="hitl",
        entry="ask",
        nodes={
            "ask": lambda ctx: NodeResult(
                update={"asked": True},
                interrupt=InterruptRequest(interrupt_type="confirm"),
            ),
            "done": lambda ctx: None,
        },
        edges={"ask": "done"},
        ends=frozenset({"done"}),
        interrupts=frozenset({"ask"}),
    )
    calls: list[str | None] = []
    outcome = run_graph(graph, on_checkpoint=lambda _s, n: calls.append(n))
    assert outcome.status == "waiting_human"
    assert calls == ["done"]


def test_failed_step_does_not_advance_checkpoint() -> None:
    """失败步骤不触发检查点 —— 检查点必须指向「确定成功过」的进度。"""
    graph = GraphDefinition(
        name="boom",
        entry="a",
        nodes={
            "a": lambda ctx: NodeResult(update={"ok": True}),
            "b": lambda ctx: (_ for _ in ()).throw(NodeError("确定性失败")),
            "c": lambda ctx: None,
        },
        edges={"a": "b", "b": "c"},
        ends=frozenset({"c"}),
    )
    calls: list[str | None] = []
    outcome = run_graph(graph, on_checkpoint=lambda _s, n: calls.append(n))
    assert outcome.status == "failed"
    # 只有一次检查点：a 成功 → b；b 失败没有检查点
    assert calls == ["b"]


def test_checkpoint_is_optional_and_signature_compatible() -> None:
    """不传 ``on_checkpoint`` 时行为与批次 9 完全一致（向后兼容）。"""
    outcome = run_graph(_linear_graph())
    assert outcome.status == "success"
    assert outcome.next_node is None


# ============================================================================
# ② 服务层回调（L1，替身 DB / 替身任务）
# ============================================================================
class _FakeTask:
    def __init__(self) -> None:
        self.graph_state: str | None = None
        self.current_node: str | None = "a"


class _FakeDB:
    def __init__(self) -> None:
        self.flushed = 0

    def flush(self) -> None:
        self.flushed += 1


def test_service_checkpoint_callback_persists_progress_and_resume_node() -> None:
    from app.services.agent_service import AgentService

    db, task = _FakeDB(), _FakeTask()
    cb = AgentService._checkpoint_callback(db, task)  # type: ignore[arg-type]
    cb({"seen": ["a"]}, "b")
    assert task.current_node == "b"
    assert task.graph_state is not None and '"seen"' in task.graph_state
    assert db.flushed == 1


def test_service_checkpoint_callback_keeps_node_on_terminal() -> None:
    """终态（next=None）不回拨 ``current_node`` —— 终态由 ``_persist_outcome`` 统一收口。"""
    from app.services.agent_service import AgentService

    db, task = _FakeDB(), _FakeTask()
    cb = AgentService._checkpoint_callback(db, task)  # type: ignore[arg-type]
    cb({"seen": ["a"]}, "b")  # 先推进到 b
    cb({"seen": ["a", "b"]}, None)  # 终态：state 更新，但 current_node 不被 None 回拨
    assert task.current_node == "b"  # 保持最后一次进度，不被 None 覆盖
    assert db.flushed == 2


# ============================================================================
# ③ 接线守卫（防「写了模块、没接线」）
# ============================================================================
_SERVICE_SRC = (Path(__file__).resolve().parents[1] / "app" / "services" / "agent_service.py").read_text(encoding="utf-8")


def test_all_run_graph_call_sites_pass_on_checkpoint() -> None:
    """``agent_service`` 的每一处 ``run_graph(...)`` 调用都必须传 ``on_checkpoint=``。

    🔴 AST 扫描真实源码，而不是数调用次数 —— 新增调用点漏接线时该用例立即失败。
    """
    tree = ast.parse(_SERVICE_SRC)
    call_sites = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "run_graph":
            call_sites += 1
            keywords = {kw.arg for kw in node.keywords}
            assert "on_step" in keywords, f"第 {node.lineno} 行的 run_graph 缺 on_step"
            assert "on_checkpoint" in keywords, (
                f"第 {node.lineno} 行的 run_graph 缺 on_checkpoint —— "
                "节点级检查点未接线，中途崩溃将退化为「只能重跑」"
            )
    assert call_sites == 3, f"期望 3 处 run_graph 调用（retry/启动/恢复），实际 {call_sites}"


def test_runtime_module_has_no_io_in_checkpoint_path() -> None:
    """运行时模块不得在检查点路径引入 IO（graph.py 保持纯运行时，不 import dao/session）。"""
    src = Path(graph_mod.__file__).read_text(encoding="utf-8")
    assert "app.dao" not in src
    assert "sqlalchemy" not in src
