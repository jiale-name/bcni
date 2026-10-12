"""Agent 能力层（架构 §4.3：``agents/`` —— LangGraph 图定义与运行时）。

本包在六层架构中的位置
====================
``app/agents/`` 与 ``app/services/`` **同层**（``tests/test_layering.py`` 的
``LAYER_ORDER`` 中值为 2）：

.. code-block:: text

    routers(3) ──▶ services(2) ──▶ agents(2) ──▶ dao(1) ──▶ models(0)
                       └──────────────┴─────────────┴───────▶ core(1)

- **允许**：``agents`` 导入 ``dao`` / ``models`` / ``schemas`` / ``core``；
- **禁止**：``agents`` 导入 ``routers``；``dao`` / ``models`` 导入 ``agents``；
- ``services`` 可以导入 ``agents``（同一层，方向是「编排 → 能力」）。

> 为什么把 ``agents`` 单独成目录而不是塞进 ``services``：架构 §4.3 明确
> ``agents/parse_graph.py`` / ``agents/ask_graph.py`` 的落位；图定义与运行时是
> **可复用的能力**（F-A0 的 7 项能力被 F-3/F-4/F-6 共用），
> 塞进 services 会让「编排逻辑」与「图定义」混在一起，
> 而后者需要能被 API-91（图可视化）单独取用。

模块清单
========
| 模块 | 职责 |
| --- | --- |
| :mod:`~app.agents.graph` | 纯运行时：StateGraph / 条件边 / interrupt / 重试 / 步骤记录 |
| :mod:`~app.agents.checkpointer` | 检查点持久化（MySQL ``agent_task.graph_state``）+ LangGraph 适配占位 |
| :mod:`~app.agents.parse_graph` | 档案解析图（F-3.1/3.2/3.3 的编排骨架） |
| :mod:`~app.agents.registry` | 图注册表（``task_type`` → 图定义，F-A0.7 数据源） |
"""

from app.agents.graph import (
    AgentState,
    GraphDefinition,
    GraphOutcome,
    InterruptRequest,
    NodeContext,
    NodeError,
    NodeResult,
    RetryableNodeError,
    StepRecord,
    resume_state,
    run_graph,
)
from app.agents.registry import GRAPH_REGISTRY, available_task_types, get_graph, has_graph

__all__ = [
    "GRAPH_REGISTRY",
    "AgentState",
    "GraphDefinition",
    "GraphOutcome",
    "InterruptRequest",
    "NodeContext",
    "NodeError",
    "NodeResult",
    "RetryableNodeError",
    "StepRecord",
    "available_task_types",
    "get_graph",
    "has_graph",
    "resume_state",
    "run_graph",
]
