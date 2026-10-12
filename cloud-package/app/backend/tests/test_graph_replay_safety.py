"""CU-13b · 图运行时「迁移控制项」（replay-safety contract）—— L1 契约层，**无外部依赖**。

为什么单独有这个文件
====================
一期已拍板**沿用自研运行时**（评审文档 §7.3 批次 8 ·
``docs/图运行时选型评估-自研运行时与LangGraph.md`` v1.1）。但「不切」有一个代价要自己扛：

> **将来真切 LangGraph 时，中断节点会被重放** —— ``interrupt()`` 之前的代码会执行**两次**。

在自研运行时下这件事**不会发生**，所以它**不会在今天的任何测试里暴露**。
一旦有人（很可能是未来的自己，或 CU-16 接手复核工作台的人）在 ``field_review`` 里
顺手写一句「落一条复核待办」，**今天的 654 个用例全会绿**，
而切换那天变成「复核待办写两条 / 模型调用计费两次 / 变更留痕重复」。

⇒ 所以这条红线**不能靠约定**，必须有机械守卫。本文件是三道：

| # | 守卫 | 拦什么 |
| :-: | --- | --- |
| 1 | **装配期互斥校验**（``GraphDefinition.validate``） | 「中断节点被声明为有副作用」—— 声明层面的自相矛盾 |
| 2 | **声明完整性**（AST 扫真实源码） | 新增中断节点**却忘了声明** ⇒ 声明会慢慢变成一纸空文 |
| 3 | **红线本体**（AST 扫真实源码） | 中断节点里、``InterruptRequest`` **之前**出现写操作 |

🔴 关于第 3 条：扫描器「什么都没扫到」与「代码很干净」在输出上**一模一样**。
因此本文件**必须**包含「扫描器能抓到故意违规」的**正反对照**用例
（``test_scanner_catches_deliberate_violations``）——
否则这道守卫会在某次重构后静默失效，而**没有任何人会发现**。
「永远返回空的检查」比没有检查更危险，因为它让人以为已经检查过了。

覆盖的契约编号见 ``app/agents/graph.py`` 模块 docstring「迁移控制项」章节。
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Final

import pytest

from app.agents.graph import GraphDefinition, NodeError
from app.agents.registry import available_task_types, get_graph

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# ============================================================================
# 扫描器（纯函数，可被合成源码单独测试 —— 这正是它可被验证的前提）
# ============================================================================
#: 方法名命中即视为**写操作**（副作用）。宁可多报（用 ``# replay-safe:`` 豁免），
#: 也不要漏报 —— 漏报的代价是切换运行时后的重复副作用，且极难定位。
SIDE_EFFECT_ATTRS: Final[frozenset[str]] = frozenset(
    {
        # 写库（DAO / SQLAlchemy / cursor）
        "insert",
        "insert_many",
        "insert_one",
        "bulk_insert",
        "add",
        "add_all",
        "update",
        "update_one",
        "bulk_update",
        "delete",
        "remove",
        "soft_delete",
        "hard_delete",
        "save",
        "save_all",
        "commit",
        "flush",
        "execute",
        "executemany",
        "bulk_save_objects",
        # 外部调用 / 通知 / 投递
        "post",
        "put",
        "patch",
        "publish",
        "enqueue",
        "delay",
        "apply_async",
        # 审计
        "log_audit",
        "audit_log",
        "record_change",
    }
)

#: 函数名（含 ``Attribute`` 的属性名）命中前缀即视为写操作。
SIDE_EFFECT_NAME_PREFIXES: Final[tuple[str, ...]] = (
    "write_",
    "save_",
    "insert_",
    "record_",
    "log_",
    "send_",
    "publish_",
    "enqueue_",
    "notify_",
    "push_",
    "charge_",
    "persist_",
)

#: **内存** ``dict`` 操作的接收者白名单：``state.update(...)`` 不是副作用。
#: （不加这一条，最普通的状态更新写法会被误报，红线就会被随手放宽 ——
#:  而「随手放宽」正是红线失效最常见的方式。）
MEMORY_RECEIVERS: Final[frozenset[str]] = frozenset(
    {"state", "merged", "human", "payload", "update", "ctx", "result"}
)

#: 逃生舱：确有幂等保证的调用，在**同一行**写明理由即可豁免。
REPLAY_SAFE_ESCAPE: Final[str] = "# replay-safe:"


def _receiver_name(func: ast.expr) -> str | None:
    """取方法调用的接收者名字：``state.update`` → ``state``；``ctx.state.update`` → ``state``。"""
    if not isinstance(func, ast.Attribute):
        return None
    base = func.value
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _side_effect_call_name(call: ast.Call) -> str | None:
    """命中写操作名单则返回可读名字（``.insert()`` / ``write_audit()``），否则 ``None``。"""
    func = call.func
    if isinstance(func, ast.Attribute):
        attr = func.attr
        # ``update`` 是唯一需要区分「内存 vs 数据库」的名字：
        # ``state.update`` 是状态合并，``talent_dao.update`` 是写库。
        if attr == "update" and _receiver_name(func) in MEMORY_RECEIVERS:
            return None
        if attr in SIDE_EFFECT_ATTRS or attr.startswith(SIDE_EFFECT_NAME_PREFIXES):
            return f".{attr}()"
        return None
    if isinstance(func, ast.Name):
        if func.id.startswith(SIDE_EFFECT_NAME_PREFIXES):
            return f"{func.id}()"
        return None
    return None


def _requests_interrupt(node: ast.AST) -> bool:
    """该 AST 节点是否在**请求人工介入**（构造 ``InterruptRequest``）。"""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "InterruptRequest":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "InterruptRequest":
            return True
    if isinstance(node, ast.keyword) and node.arg == "interrupt":
        # ``interrupt=None`` 不算请求
        return not (isinstance(node.value, ast.Constant) and node.value.value is None)
    return False


def scan_replay_unsafe_calls(source: str) -> list[tuple[int, str]]:
    """扫描**一段函数源码**：``InterruptRequest`` 之前出现的副作用调用。

    :return: ``[(相对行号, 调用名)]``；空列表 = 干净（**或**该函数不是中断节点）

    ⚠️ 只检查**中断节点** —— 其余节点的重放风险由 ``side_effects`` 声明
    ＋「启用时间旅行时的幂等审计」覆盖，不属本断言范围。
    """
    dedented = textwrap.dedent(source)
    tree = ast.parse(dedented)
    if not tree.body:
        return []
    fn = tree.body[0]
    interrupt_lines = [node.lineno for node in ast.walk(fn) if _requests_interrupt(node)]
    if not interrupt_lines:
        return []
    cut = min(interrupt_lines)
    lines = dedented.splitlines()
    violations: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or node.lineno >= cut:
            continue
        name = _side_effect_call_name(node)
        if name is None:
            continue
        raw = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
        if REPLAY_SAFE_ESCAPE in raw:
            continue
        violations.append((node.lineno, name))
    return sorted(violations)


def _interrupt_nodes_by_code(graph: GraphDefinition) -> set[str]:
    """**从真实源码**探测哪些节点会请求中断（与 ``graph.interrupts`` 声明比对用）。"""
    found: set[str] = set()
    for node_name, fn in graph.nodes.items():
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):  # pragma: no cover — lambda / 动态函数
            continue
        tree = ast.parse(textwrap.dedent(source))
        if any(_requests_interrupt(node) for node in ast.walk(tree)):
            found.add(node_name)
    return found


# ============================================================================
# 守卫 1 · 装配期：声明必须自洽
# ============================================================================
def test_declarations_must_reference_registered_nodes() -> None:
    """声明指向不存在的节点 → 构造即失败（不是等运行到那一步才发现）。"""
    with pytest.raises(NodeError) as exc:
        GraphDefinition(
            name="t_unknown_declared",
            entry="a",
            nodes={"a": lambda ctx: None},
            interrupts=frozenset({"nope"}),
        )
    assert "未注册节点" in str(exc.value)


def test_interrupt_node_must_not_be_declared_with_side_effect() -> None:
    """🔴 **契约 1 的装配期守卫**：中断节点 + 副作用 = 直接拒绝构造。

    这是本文件最重要的断言 —— 它把「不要这么写」从**注释**变成了**不可构造的对象**。
    """
    with pytest.raises(NodeError) as exc:
        GraphDefinition(
            name="t_clash",
            entry="a",
            nodes={"a": lambda ctx: None},
            interrupts=frozenset({"a"}),
            side_effects=frozenset({"a"}),
        )
    message = str(exc.value)
    assert "重放" in message, "错误信息必须点明根因（会重放），否则接手的人只会把声明删掉"
    assert "副作用" in message
    assert "后继节点" in message, "必须给出可执行的处置办法，否则等于只报错不解决"


def test_disjoint_declarations_are_accepted() -> None:
    """正例：中断点与副作用节点是**不同节点**时，声明合法。"""
    graph = GraphDefinition(
        name="t_ok",
        entry="a",
        nodes={"a": lambda ctx: None, "b": lambda ctx: None},
        interrupts=frozenset({"a"}),
        side_effects=frozenset({"b"}),
    )
    assert graph.interrupts == {"a"}
    assert graph.side_effects == {"b"}


# ============================================================================
# 守卫 2 · 声明完整性：声明 == 代码（防止声明退化成注释）
# ============================================================================
def test_parse_graph_declares_field_review_as_its_only_interrupt() -> None:
    """``parse`` 图的中断点就是 ``field_review``（唯一）。"""
    assert get_graph("parse").interrupts == {"field_review"}


def test_parse_graph_declares_its_side_effect_nodes() -> None:
    """``parse`` 图的副作用节点清单 —— 同时也是**将来启用重放时的幂等审计清单**。"""
    graph = get_graph("parse")
    assert graph.side_effects == {
        "ocr",  # 调 OCR 后端
        "extract_fields",  # 走模型网关（计费 + ai_call_log）
        "persist_talent",  # 写库
        "manual_reject",  # 写库
    }
    # 🔴 红线：中断节点**必须不在**副作用清单里
    assert "field_review" not in graph.side_effects


@pytest.mark.parametrize("task_type", sorted(available_task_types()))
def test_declared_interrupts_match_code(task_type: str) -> None:
    """🔴 **声明必须与代码一致** —— 新增中断节点却忘了声明，这里挂。

    ⚠️ 这一条防的是「守卫 1 被架空」：装配期校验再严，只要声明是空的就永远不会触发。
    所以「声明 == 代码探测结果」必须由用例钉死，而不是靠人记得改两处。
    """
    graph = get_graph(task_type)
    detected = _interrupt_nodes_by_code(graph)
    assert detected == set(graph.interrupts), (
        f"图 {task_type!r} 的中断节点声明与代码不一致：\n"
        f"  代码里真实请求中断的节点：{sorted(detected)}\n"
        f"  interrupts 声明为：{sorted(graph.interrupts)}\n"
        "→ 请在 build_*_graph() 里同步 interrupts（新图的模板见 parse_graph）。"
    )


# ============================================================================
# 守卫 3 · 红线本体：中断节点的 interrupt 之前不得有副作用
# ============================================================================
@pytest.mark.parametrize("task_type", sorted(available_task_types()))
def test_no_side_effect_before_interrupt(task_type: str) -> None:
    """🔴 **契约 1 的红线**：中断节点里、``InterruptRequest`` 之前不得出现写操作。

    自研运行时下这段代码只跑一次；LangGraph 下会跑**两次**。
    本用例是「双运行时语义」之间唯一的一道静态防线。
    """
    graph = get_graph(task_type)
    offenders: dict[str, list[tuple[int, str]]] = {}
    for node_name in sorted(graph.interrupts):
        source = inspect.getsource(graph.nodes[node_name])
        violations = scan_replay_unsafe_calls(source)
        if violations:
            offenders[node_name] = violations
    assert not offenders, (
        f"图 {task_type!r} 的中断节点在 interrupt 之前产生了副作用：{offenders}\n"
        "→ 自研运行时下只执行一次，**切 LangGraph 后会执行两次**。\n"
        "→ 处置：① 把副作用移到中断之后的**后继节点**（首选）；\n"
        "     ② 或让它幂等，并在该行注明 `# replay-safe: <理由>`。"
    )


def test_field_review_body_stays_read_only_before_interrupt() -> None:
    """把 ``parse`` 图的红线单独钉一条：失败信息直接点名节点，不必再去翻参数表。"""
    fn = get_graph("parse").nodes["field_review"]
    assert scan_replay_unsafe_calls(inspect.getsource(fn)) == [], (
        "field_review 是中断节点，其函数体必须保持「只读 + 返回」。\n"
        "CU-16 实现复核工作台时若需要落复核待办 / 发通知，请写在其后继节点里。"
    )


# ============================================================================
# 扫描器的**正反对照** —— 没有这组用例，上面三条可能是「永远绿」的假守卫
# ============================================================================
def test_scanner_catches_deliberate_violations() -> None:
    """🔴 **正向对照**：故意写违规代码，扫描器必须抓到。

    这条用例的意义不在被测代码，而在**证明扫描器本身有效** ——
    一个永远返回空列表的扫描器会让上面所有断言「通过」，那比没有守卫更糟。
    """
    dirty = '''
def bad_node(ctx):
    dao.insert({"a": 1})
    session.commit()
    client.post("/notify", json={})
    record_change({"b": 2})
    logger.info("看起来无害的一行")
    return NodeResult(interrupt=InterruptRequest(interrupt_type="t"))
'''
    found = {name for _, name in scan_replay_unsafe_calls(dirty)}
    assert ".insert()" in found
    assert ".commit()" in found
    assert ".post()" in found
    assert "record_change()" in found
    # ``logger.info`` 不在名单里 —— 日志是允许的（且必须允许，否则守卫会被拖到没人用）
    assert ".info()" not in found


def test_scanner_ignores_code_after_interrupt_and_memory_updates() -> None:
    """**反向对照**：``interrupt`` **之后**的写操作、以及**内存** ``update`` 都不算违规。

    这一条是在防「守卫误报」 —— 误报会让开发者顺手删掉守卫。
    """
    clean = '''
def ok_node(ctx):
    state.update({"a": 1})
    payload.update({"b": 2})
    result = NodeResult(
        update={"c": 3},
        interrupt=InterruptRequest(interrupt_type="t"),
    )
    dao.insert({"d": 4})
    return result
'''
    assert scan_replay_unsafe_calls(clean) == []


def test_scanner_has_escape_hatch_for_documented_idempotent_calls() -> None:
    """逃生舱：确有幂等保证的调用，写明理由即豁免（**必须写理由**，行内注释）。"""
    src = '''
def ok_node(ctx):
    ledger.record(key="k")  # replay-safe: 唯一键约束保证重复执行不产生第二条
    return NodeResult(interrupt=InterruptRequest(interrupt_type="t"))
'''
    assert scan_replay_unsafe_calls(src) == []


def test_scanner_does_not_flag_non_interrupt_nodes() -> None:
    """非中断节点**不在本红线范围内** —— 它们的写操作是正常的（如 persist_talent）。"""
    src = '''
def plain_node(ctx):
    dao.insert({"a": 1})
    return NodeResult(update={})
'''
    assert scan_replay_unsafe_calls(src) == []


# ============================================================================
# 契约 4/5/6 · 声明式、接口面、Service 层依赖面
# ============================================================================
@pytest.mark.parametrize("task_type", sorted(available_task_types()))
def test_describe_exposes_migration_flags(task_type: str) -> None:
    """契约 4：迁移控制项要能**被前端看见**，而不是藏在节点函数体里。"""
    graph = get_graph(task_type)
    described = graph.describe()
    assert described["interrupts"] == sorted(graph.interrupts)
    assert described["side_effects"] == sorted(graph.side_effects)
    by_name = {node["name"]: node for node in described["nodes"]}
    for node_name in graph.interrupts:
        assert by_name[node_name]["interrupt"] is True
    for node_name in graph.side_effects:
        assert by_name[node_name]["side_effect"] is True
    # 互斥在可视化层同样成立（前端画图不该出现「既是中断点又有副作用」的节点）
    assert not set(described["interrupts"]) & set(described["side_effects"])


#: Service 层**允许** import 的运行时公开符号（契约 6）。
#: 这是一个**白名单**而不是黑名单：新增任何符号都必须先想清楚
#: 「它是给编排层用的，还是给业务层用的」。
RUNTIME_PUBLIC_API: Final[frozenset[str]] = frozenset(
    {
        "AgentState",
        "GraphDefinition",
        "GraphOutcome",
        "HUMAN_KEY",
        "InterruptRequest",
        "NodeContext",
        "NodeError",
        "NodeResult",
        "RetryableNodeError",
        "StepRecord",
        "resume_state",
        "run_graph",
    }
)


def test_service_layer_imports_only_public_runtime_symbols() -> None:
    """契约 6：Service 层只用运行时**公开面**，且**不得直接绑定某一张图**。

    为什么这条重要：切换运行时时要改的面，恰好就是「Service 直接依赖的那些符号」。
    把这份依赖面锁在 12 个公开符号内，切换时改的就只是适配器；
    一旦某个 service 开始 `from app.agents.graph import _resolve_next`，
    切换成本就从「换装配层」变成「读懂并重写业务代码」。
    """
    services_dir = BACKEND_ROOT / "app" / "services"
    service_files = sorted(services_dir.glob("*.py"))
    assert service_files, "没扫到 service 文件说明路径判断错了，不能算通过"

    offenders: list[str] = []
    for path in service_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if not module.startswith("app.agents"):
                continue
            names = [alias.name for alias in node.names]
            # ① 不得直接 import 图定义模块（「哪张图」必须经 registry 查）
            if module.endswith("_graph") or module.endswith(".parse_graph"):
                offenders.append(f"{path.name}: 直接 import 图定义 {module}")
            # ② 不得 import 下划线前缀的内部符号
            for name in names:
                if name.startswith("_"):
                    offenders.append(f"{path.name}: 依赖运行时内部符号 {module}.{name}")
                elif module == "app.agents.graph" and name not in RUNTIME_PUBLIC_API:
                    offenders.append(
                        f"{path.name}: from app.agents.graph import {name}"
                        "（不在公开面白名单内；若确需暴露，请连同理由一起加进 RUNTIME_PUBLIC_API）"
                    )
    assert not offenders, "Service 层越界依赖运行时：\n  " + "\n  ".join(offenders)
