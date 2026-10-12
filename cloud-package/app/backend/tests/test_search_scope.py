"""CU-26 · **越权检索专项**（F-3.6 · 需求 §12.3「向量检索权限前置专项」，关闭 **B-8**）。

为什么单独一个文件
==================
评审文档 §6.2.1 的 **B-8** 指出：接口 §2.2 定义的 **API-30 ~ API-35 无人认领**，
而 AC-12 / AC-13 的**端点级** 40301 断言原定「并入 CU-14」，**该并入从未发生**。
需求 §12.3 对 F-3.6 另有独立要求：

> **越权检索测试用例集（≥20 条：跨部门 / 跨人 / 跨租户）** ∣
> **拦截率 100%；Milvus ``search`` 均携带 ``tenant_id + org_id`` 元数据过滤，无「先召回后过滤」**

故本文件承担两件事：
1. **≥20 条越权检索用例**（用例号 **T-01 ~ T-20**，本文件即需求所指的「用例集」）；
2. 🔴 **把「无『先召回后过滤』」变成结构断言** —— 这句要求**光靠行为用例证不出来**：
   一个「先召回全量再在应用层过滤」的实现，在**结果上看起来完全正确**
   （不该看到的确实没返回），但**全量数据已经越过了权限边界进入进程内存**，
   而且一旦过滤逻辑写漏一处就直接泄漏。故 T-20 用 **AST 扫描**断言
   「权限表达式必须在**调用 provider 之前**就拼进检索参数」。

分层
----
**全部 L1**（不依赖 MySQL / Redis / Milvus）—— 权限推导与表达式构造都是**纯函数**
（铁律 10：确定性结论来自确定性组件），可以在任何机器上跑。
这也正是 CR-11 把「列表侧 MySQL 条件」与「检索侧 Milvus 表达式」收进
同一个 ``DataScopeFilter`` 的收益：**它可被脱离外部服务验证**。

🔴 每条「拒绝」用例都配**反向对照**（同依赖在应放行时确实放行）——
   否则一个「永远抛 40301 / 永远返回 ``org_id == -1``」的实现也能让全部用例通过。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.exceptions import ErrorCode, PermissionDeniedError
from app.models.enums import RoleDataScope
from app.models.talent import TalentBase

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SEARCH_SERVICE = BACKEND_ROOT / "app" / "services" / "search_service.py"

#: 固定测试标识（雪花量级，避免与真实数据撞号；与 ``test_access_control`` 同源策略）
TENANT_A = 7000000000000000001
TENANT_B = 7000000000000000002
USER_ID = 7000000000000000101
OWN_ORG = 7000000000000000201
SIBLING_ORG = 7000000000000000202
FOREIGN_ORG = 7000000000000000301
DEPT_SUBTREE = (OWN_ORG, SIBLING_ORG)

#: 角色 → 数据域（与 ``seed_dev.BUILTIN_ROLES`` / 接口 §1.7 一致）
_SCOPE_BY_ROLE: dict[str, int] = {
    "ADM": int(RoleDataScope.ALL),
    "HR": int(RoleDataScope.ALL),
    "MGR": int(RoleDataScope.OWN_DEPT),
    "EMP": int(RoleDataScope.SELF),
    "EXEC": int(RoleDataScope.STATS_ONLY),
    "AIOPS": int(RoleDataScope.STATS_ONLY),
    "AUDIT": int(RoleDataScope.STATS_ONLY),
}


def _principal(
    role_code: str,
    *,
    data_scope: int | None = None,
    tenant_id: int = TENANT_A,
    org_id: int | None = OWN_ORG,
    dept_ids: tuple[int, ...] = (),
    user_id: int = USER_ID,
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        roles=(role_code,),
        data_scope=int(data_scope if data_scope is not None else _SCOPE_BY_ROLE[role_code]),
        dept_ids=tuple(dept_ids),
    )


def _assert_denied(callable_) -> PermissionDeniedError:  # type: ignore[no-untyped-def]
    """断言抛 40301（**不断言 HTTP 404**：防探测靠业务码统一，CR-8）。"""
    with pytest.raises(PermissionDeniedError) as excinfo:
        callable_()
    assert excinfo.value.code == int(ErrorCode.NO_PERMISSION) == 40301
    return excinfo.value


# ============================================================================
# T-01 ~ T-04 · 跨租户
# ============================================================================
def test_t01_cross_tenant_first_clause_is_own_tenant() -> None:
    """🔴 跨租户 · **expr 的首条子句恒为本人租户**，与 data_scope 无关。

    这是「跨租户检索」在结构上不可能的**唯一**依据：租户条件不是「某一档数据域
    才加」，而是**每一档都加**（含 ``ALL``）。
    """
    for role in ("HR", "MGR", "EMP"):
        expr = DataScopeFilter.milvus_expr(_principal(role, dept_ids=DEPT_SUBTREE))
        assert expr.startswith(f"tenant_id == {TENANT_A}"), f"{role} 的 expr 未以本人租户开头：{expr}"


def test_t02_cross_tenant_expr_signature_cannot_be_overridden() -> None:
    """🔴 跨租户 · ``milvus_expr`` **签名里没有 tenant 入参** ⇒ 外部无法覆盖租户。

    反面写法（形如 ``milvus_expr(principal, tenant_id=...)``）会让调用方
    「顺手」把租户换成请求里传来的值 —— 那就把 CR-9 交给了调用者的自觉。
    """
    params = set(inspect.signature(DataScopeFilter.milvus_expr).parameters)
    assert "principal" in params
    for forbidden in ("tenant_id", "tid", "org_ids", "scope"):
        assert forbidden not in params, f"milvus_expr 不应接受 {forbidden} 入参（应只从 principal 推导）"


def test_t03_cross_tenant_all_scope_still_carries_tenant_clause() -> None:
    """🔴 跨租户 · ``data_scope=1 全部`` **仍然带租户条件**（只是不加 org 条件）。

    「全部」= 租户内全部，**不是**平台全部。这两者在代码里只差一个子句，
    故必须有用例把它钉住。
    """
    expr = DataScopeFilter.milvus_expr(_principal("HR", data_scope=int(RoleDataScope.ALL)))
    assert expr == f"tenant_id == {TENANT_A}"
    assert "org_id" not in expr  # 反向对照：ALL **不**加 org 条件（避免误加导致查不到）


def test_t04_cross_tenant_two_tenants_expressions_never_intersect() -> None:
    """🔴 跨租户 · 两个租户的同档主体产出**互不可达**的 expr（不含对方的 tenant_id）。"""
    a = DataScopeFilter.milvus_expr(
        _principal("HR", tenant_id=TENANT_A, data_scope=int(RoleDataScope.ALL))
    )
    b = DataScopeFilter.milvus_expr(
        _principal("HR", tenant_id=TENANT_B, data_scope=int(RoleDataScope.ALL))
    )
    assert a != b
    assert str(TENANT_B) not in a and str(TENANT_A) not in b


# ============================================================================
# T-05 ~ T-08 · 跨部门
# ============================================================================
def test_t05_cross_dept_own_dept_expr_is_exactly_the_subtree() -> None:
    """🔴 跨部门 · ``OWN_DEPT`` 的 org 集合**恰好等于**传入子树（不多不少）。"""
    expr = DataScopeFilter.milvus_expr(_principal("MGR"), dept_ids=DEPT_SUBTREE)
    assert expr == f"tenant_id == {TENANT_A} and org_id in [{OWN_ORG}, {SIBLING_ORG}]"


def test_t06_cross_dept_empty_subtree_is_fail_closed() -> None:
    """🔴 跨部门 · **子树未解析出来时不能放行全量** —— 退化为 ``org_id == -1``（查不到）。

    「宁可查不到，也不越权」。若这里写成「不带 org 条件」，那一次组织树查询失败
    就会静默升级为**全租户可见** —— 这正是最危险的一类降级。
    """
    expr = DataScopeFilter.milvus_expr(_principal("MGR"), dept_ids=())
    assert expr == f"tenant_id == {TENANT_A} and org_id == -1"
    # 反向对照：同主体传入子树时**确实**加 org 条件（证明上一条不是「永远不会加」）
    assert "org_id in" in DataScopeFilter.milvus_expr(_principal("MGR"), dept_ids=DEPT_SUBTREE)


def test_t07_cross_dept_principal_without_dept_ids_is_fail_closed() -> None:
    """🔴 跨部门 · 缺省 ``dept_ids`` 且 principal 自身也没有子树 → 同样 fail-closed。"""
    expr = DataScopeFilter.milvus_expr(_principal("MGR", dept_ids=()))
    assert expr.endswith("org_id == -1")
    # 反向对照：principal 自带子树时正常展开
    expr2 = DataScopeFilter.milvus_expr(_principal("MGR", dept_ids=DEPT_SUBTREE))
    assert str(SIBLING_ORG) in expr2


def test_t08_cross_dept_expr_excludes_foreign_org() -> None:
    """🔴 跨部门 · 子树外的部门 id **绝不出现在** expr 中。"""
    expr = DataScopeFilter.milvus_expr(_principal("MGR"), dept_ids=DEPT_SUBTREE)
    assert str(FOREIGN_ORG) not in expr


# ============================================================================
# T-09 ~ T-10 · 跨人（本人）
# ============================================================================
def test_t09_cross_person_self_scope_is_not_bare_tenant_clause() -> None:
    """🔴 跨人 · ``SELF`` 的 expr **不得退化为裸租户条件**。

    ``data_scope=3 本人`` 的最终收敛要由 ``talent_id`` 二次收窄完成，
    但 expr 这一层**仍必须带 org 约束** —— 否则向量召回阶段就已经把全租户的
    画像取回进程内存，「先召回后过滤」在 SELF 档最容易发生。
    """
    expr = DataScopeFilter.milvus_expr(_principal("EMP"), dept_ids=DEPT_SUBTREE)
    assert expr != f"tenant_id == {TENANT_A}"
    assert "org_id in" in expr


def test_t10_cross_person_self_scope_without_subtree_is_fail_closed() -> None:
    """🔴 跨人 · ``SELF`` 且子树为空 → ``org_id == -1``（不越权）。"""
    expr = DataScopeFilter.milvus_expr(_principal("EMP"), dept_ids=())
    assert expr == f"tenant_id == {TENANT_A} and org_id == -1"


# ============================================================================
# T-11 ~ T-17 · 前端传参只能收窄（接口 §1.3 红线）
# ============================================================================
def test_t11_narrowing_out_of_scope_denied_40301() -> None:
    """🔴 收窄 · MGR 请求子树外的部门 → **40301**（不放行、不静默裁剪）。"""
    _assert_denied(
        lambda: DataScopeFilter.assert_scope_narrowing(
            _principal("MGR"), [FOREIGN_ORG], dept_ids=DEPT_SUBTREE
        )
    )


def test_t12_narrowing_subset_allowed() -> None:
    """收窄 · 请求子树**子集** → 放行，且返回的就是请求值（不是自己的全量）。"""
    out = DataScopeFilter.assert_scope_narrowing(
        _principal("MGR"), [SIBLING_ORG], dept_ids=DEPT_SUBTREE
    )
    assert out == (SIBLING_ORG,)
    # 反向对照：越出即拒（证明上一条不是「永不拒绝」）
    _assert_denied(
        lambda: DataScopeFilter.assert_scope_narrowing(
            _principal("MGR"), [FOREIGN_ORG], dept_ids=DEPT_SUBTREE
        )
    )


def test_t13_narrowing_exact_subtree_allowed() -> None:
    """收窄 · 请求**恰好等于**自身范围 → 放行。"""
    out = DataScopeFilter.assert_scope_narrowing(
        _principal("MGR"), list(DEPT_SUBTREE), dept_ids=DEPT_SUBTREE
    )
    assert set(out or ()) == set(DEPT_SUBTREE)


def test_t14_narrowing_self_scope_foreign_org_denied() -> None:
    """🔴 收窄 · ``SELF`` 请求他人部门 → 40301（只能收窄到自己的 org）。"""
    _assert_denied(
        lambda: DataScopeFilter.assert_scope_narrowing(_principal("EMP"), [SIBLING_ORG])
    )


def test_t15_narrowing_self_scope_own_org_allowed() -> None:
    """收窄 · ``SELF`` 请求自己的 org → 放行。"""
    out = DataScopeFilter.assert_scope_narrowing(_principal("EMP"), [OWN_ORG])
    assert out == (OWN_ORG,)


def test_t16_narrowing_all_scope_unrestricted_within_tenant() -> None:
    """收窄 · ``ALL`` 档不受部门集合限制（租户条件由 expr / SQL 承担）。"""
    out = DataScopeFilter.assert_scope_narrowing(_principal("HR"), [FOREIGN_ORG])
    assert out == (FOREIGN_ORG,)


def test_t17_narrowing_absent_means_inherit_own_scope() -> None:
    """收窄 · **未传** ``org_ids`` → ``None``（沿用自身范围，不是「无限制」）。"""
    assert DataScopeFilter.assert_scope_narrowing(_principal("MGR"), None) is None
    assert DataScopeFilter.assert_scope_narrowing(_principal("MGR"), []) is None


# ============================================================================
# T-18 ~ T-19 · 仅统计值与未知档位
# ============================================================================
@pytest.mark.parametrize("role", ["EXEC", "AIOPS", "AUDIT"])
def test_t18_stats_only_roles_denied_on_search(role: str) -> None:
    """🔴 收窄（关闭 F-3.6 越权检索）· ``仅统计值`` 三角色**不得进入检索链路**。

    ``EXEC / AIOPS / AUDIT`` 能看到统计口径，但**不能**看到任何一条档案明细
    —— 语义检索的返回天然是明细，故在**表达式构造这一步**就要拒绝，
    而不是「召回了再滤掉」。
    """
    _assert_denied(lambda: DataScopeFilter.milvus_expr(_principal(role)))
    _assert_denied(
        lambda: DataScopeFilter.assert_scope_narrowing(_principal(role), [OWN_ORG])
    )
    # 反向对照：同角色在**明细接口**的准入也是拒绝，但 MGR 在检索上是放行的
    assert DataScopeFilter.milvus_expr(_principal("MGR"), dept_ids=DEPT_SUBTREE)


def test_t19_unknown_data_scope_fails_closed() -> None:
    """🔴 未知 ``data_scope`` → **fail-closed 40301**（不得默认放行）。

    这是一个「将来新增档位时忘了加分支」的兜底：默认分支必须是**拒绝**，
    而不是「当作 ALL 处理」。两条产出路径（Milvus ``expr`` 与 MySQL 条件）
    都要各测一次 —— 它们当初就是为了「不会一处加了、一处忘了」才收进同一个类。
    """
    weird = _principal("HR", data_scope=99)
    _assert_denied(lambda: DataScopeFilter.milvus_expr(weird))
    _assert_denied(lambda: DataScopeFilter.mysql_condition(TalentBase, weird))


# ============================================================================
# T-20 · 结构断言：「无『先召回后过滤』」（**本文件最重要的一条**）
# ============================================================================
def test_t20_permission_filter_is_pushed_down_before_provider_call() -> None:
    """🔴 **权限表达式必须在调用 provider 之前拼进检索参数**（需求 §12.3 的原话）。

    为什么必须是**结构**断言而不是行为断言
    --------------------------------------
    「先召回全量、再在应用层滤掉不该看的」这种实现，**行为用例测不出来** ——
    返回结果与正确实现**完全一致**（不该看到的确实没返回）。但它的代价是：
    ① 全量画像已经越过权限边界进入进程内存；② 过滤逻辑一旦漏改一处就直接泄漏。

    断言三件事（AST 扫描真实源码，不看文档、不看注释）：
    1. ``_vector_recall`` 的**签名**必须接收权限参数（``allowed_org_ids``）；
    2. 该方法内对 ``provider.search(...)`` 的调用**必须显式传** ``tenant_id=`` 与 ``org_ids=``；
    3. ``_keyword_recall``（降级路径）**同样**如此 —— 架构 §4.4 要求
       「即使降级为关键词检索，仍要保留权限表达式」。
    """
    tree = ast.parse(SEARCH_SERVICE.read_text(encoding="utf-8"))

    def _find_method(name: str) -> ast.FunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"search_service.py 中找不到方法 {name}（被改名或删除了？）")

    # --- 断言 1：签名必须带权限参数（防「把权限从签名里摘掉」）---
    for method_name in ("_vector_recall", "_keyword_recall"):
        fn = _find_method(method_name)
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        assert "allowed_org_ids" in params, (
            f"{method_name} 的签名缺少 allowed_org_ids —— 权限参数被摘掉了，"
            "检索将退化为「先召回后过滤」"
        )

    # --- 断言 2 / 3：provider.search 的调用必须显式带上租户与 org ---
    for method_name in ("_vector_recall", "_keyword_recall"):
        fn = _find_method(method_name)
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "search"
        ]
        assert calls, f"{method_name} 中未找到 provider.search 调用（结构已变，请更新本守卫）"
        for call in calls:
            kw = {k.arg for k in call.keywords if k.arg}
            assert "tenant_id" in kw, f"{method_name} 的 provider.search 未显式传 tenant_id"
            assert "org_ids" in kw, f"{method_name} 的 provider.search 未显式传 org_ids"


def test_t20b_retrieval_provider_search_declares_permission_parameters() -> None:
    """🔴 协议层同一条约束：``RetrievalProvider.search`` 必须**声明**权限参数。

    T-20 查的是「调用方有没有传」，本条查的是「协议有没有这个位置」——
    两者缺一，权限前置就会被绕开（例如新增一个不接收 ``org_ids`` 的 provider 实现）。
    """
    from app.retrieval.provider import RetrievalProvider

    sig = inspect.signature(RetrievalProvider.search)
    assert "tenant_id" in sig.parameters, "协议未声明 tenant_id —— 实现方无从前置过滤"
    assert "org_ids" in sig.parameters, "协议未声明 org_ids —— 实现方无从前置过滤"
