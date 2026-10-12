"""图注册表（F-A0.7「可视化配置」的数据源 + 任务类型 → 图的唯一映射）。

为什么需要注册表而不是「在 service 里 if/else」
============================================
``agent_task.task_type`` 是 ``VARCHAR(32)`` 自由文本（TBL-25）。
若在 ``agent_service`` 里写 ``if task_type == "parse": ... elif ...``：

1. **API-91 的实现与 API-90 的筛选项会漂移** —— 列表能筛出的类型，
   详情页可能没有对应图（于是详情页 500）；
2. **F-A0.7 的「可视化配置」无处可取** —— 前端要画图就必须知道有哪些图、每张图长什么样，
   而这个信息只在代码的分支里；本模块把它变成可序列化的 :meth:`GraphDefinition.describe`；
3. **新增图要改 3 处**（列表校验 / 详情 / 执行），漏一处的表现是「跑不通但没有报错」。

⚠️ **本批只注册 ``parse``**：其余任务类型对应的图分别属于
CU-17（``tag_suggest``）/ CU-19（``merge``）/ CU-21（``ask``）/ F-4（``score``）。
在它们落地前，请求这些类型的任务会得到明确的 **40001**（而不是「静默无事发生」）。
"""

from __future__ import annotations

from typing import Any

from app.agents.graph import GraphDefinition
from app.agents.parse_graph import (
    KEY_EXTRACTOR,
    KEY_NORMALIZER,
    KEY_PERSIST,
    GRAPH_NAME as PARSE,
    build_parse_graph,
)
from app.exceptions import ErrorCode, BizError

#: 注入键的**公开面**（Service 层组装注入依赖时从这里取，不直接 import 图定义 ——
#: 批次 9 的 AST 守卫 ``test_service_layer_imports_only_public_runtime_symbols``
#: 只放行 registry 与运行时白名单，注入键属于「图 ↔ 服务」的契约面，经 registry 暴露）。
INJECTION_KEYS = (KEY_EXTRACTOR, KEY_NORMALIZER, KEY_PERSIST)

__all__ = [
    "GRAPH_REGISTRY",
    "INJECTION_KEYS",
    "KEY_EXTRACTOR",
    "KEY_NORMALIZER",
    "KEY_PERSIST",
    "available_task_types",
    "describe_all",
    "get_graph",
    "has_graph",
    "successor_of",
]

#: ``task_type`` → 图定义。
#: ⚠️ **值在模块导入时构建**（图定义是纯声明，不含 IO），进程内复用同一份 ——
#: 每次请求重新 build 会得到等价但不同的对象，``is`` 比较与缓存都会失效。
GRAPH_REGISTRY: dict[str, GraphDefinition] = {
    PARSE: build_parse_graph(),
}


def has_graph(task_type: str) -> bool:
    return task_type in GRAPH_REGISTRY


def available_task_types() -> list[str]:
    """已注册图的类型（供 API 的错误提示与前端下拉）。"""
    return sorted(GRAPH_REGISTRY)


def get_graph(task_type: str) -> GraphDefinition:
    """按任务类型取图。

    :raises BizError: 类型未注册 → **40001**（参数不合法），
        而不是 40401（资源不存在）—— 因为「不存在的 task_type」是**请求写错了**，
        而不是「你要找的对象不在」。口径见接口 §1.5 的错误码表。
    """
    graph = GRAPH_REGISTRY.get(task_type)
    if graph is None:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"不支持的任务类型 {task_type!r}",
            {"available": available_task_types(), "field": "task_type"},
        )
    return graph


def successor_of(task_type: str, node_name: str, state: dict[str, Any] | None = None) -> str | None:
    """求某节点的后继（供 API-94 的 ``next_node`` 复用**同一个**求值逻辑）。

    🔴 必须复用 :meth:`GraphDefinition.next_node`：若 API 层自己写一遍
    「approved 之后跑哪个节点」，两处迟早不一致，
    而现象是**前端显示会执行 A、实际执行了 B** —— 极难排查。

    ⚠️ 条件边需要 ``state`` 才能求值：不传时按「静态边优先」尽力而为，
    求不出返回 ``None``（调用方据此显示 ``null`` 而不是编一个节点名）。
    """
    graph = GRAPH_REGISTRY.get(task_type)
    if graph is None:
        return None
    if node_name not in graph.nodes:
        return None
    try:
        return graph.next_node(node_name, state or {})
    except Exception:  # noqa: BLE001 — 求值是展示用途，绝不因此让主流程失败
        return None


def describe_all() -> list[dict[str, Any]]:
    """全部图的静态结构（F-A0.7 可视化配置）。"""
    return [graph.describe() for graph in GRAPH_REGISTRY.values()]
