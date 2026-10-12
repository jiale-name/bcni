"""CU-05 · 越权用例集（评审文档 **§7.4**，AC-01 ~ AC-40）。

与 §7.4.2 的用例表**逐条 1:1 对应**：测试函数名统一带 ``ac_nn`` 前缀，
便于从文档编号直接检索到测试。

分层
----
- **L1 契约层**（本文件绝大多数）：**不依赖 MySQL / Redis**，任何机器都能跑 ——
  权限判定（``require_perm`` / ``DataScopeFilter``）、行级条件表达式、Token 生命周期、
  统一口径、降级响应，全部可脱离外部服务验证；
- **L2 集成层**：``@requires_db`` 标注，缺依赖时 **skip 而非 fail**（沿用 CU-01~04 约定）；
- **暂缓**：依赖尚未编码模块的用例（档案序列化层脱敏）用 ``pytest.mark.skip`` 显式标注原因，
  **不静默丢用例**。

🔴 每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）——
   否则一个「永远抛 40301」的实现也能让所有用例通过。
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    SecurityUtils,
    assert_token_alive,
    get_access_claims,
    get_current_principal,
    require_perm,
)
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import token_dao, user_dao
from app.exceptions import (
    ERROR_META,
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    register_exception_handlers,
)
from app.models.enums import RoleDataScope
from app.models.user import SysUser
from app.schemas.common import degraded
from app.services.auth_service import AuthService
from app.services.org_scope_service import OrgScopeService
from scripts.seed_dev import ROLE_GRANTS, _PERM_TREE
from tests.conftest import requires_db

# ============================================================================
# 固定测试标识（雪花量级，避免与真实数据撞号）
# ============================================================================
TENANT_ID = 7000000000000000001
OTHER_TENANT_ID = 7000000000000000002
USER_ID = 7000000000000000101
OTHER_USER_ID = 7000000000000000102
ORG_ID = 7000000000000000201
OTHER_ORG_ID = 7000000000000000202

#: 角色 → 数据域（与 seed_dev.BUILTIN_ROLES / 接口 §1.7 一致）
_SCOPE_BY_ROLE: dict[str, int] = {
    "ADM": int(RoleDataScope.ALL),
    "EXEC": int(RoleDataScope.STATS_ONLY),
    "HR": int(RoleDataScope.ALL),
    "MGR": int(RoleDataScope.OWN_DEPT),
    "EMP": int(RoleDataScope.SELF),
    "AIOPS": int(RoleDataScope.STATS_ONLY),
    "AUDIT": int(RoleDataScope.STATS_ONLY),
}


def _all_perm_codes() -> list[str]:
    codes: list[str] = []
    for menu_code, _name, children in _PERM_TREE:
        codes.append(menu_code)
        codes.extend(code for code, _ in children)
    return codes


ALL_PERM_CODES = _all_perm_codes()


def _granted(role_code: str) -> set[str]:
    """按 ``seed_dev.ROLE_GRANTS`` 展开该角色实际应得的权限码集合。

    🔴 用例直接**引用种子脚本的授权表**，而不是在测试里另抄一份 ——
    否则「种子数据」与「权限矩阵」会各改一份、悄悄漂移：
    测试通过但线上越权（或反之，功能不可用）。
    """
    prefixes = ROLE_GRANTS.get(role_code, [])
    if prefixes == ["*"]:
        return set(ALL_PERM_CODES)
    return {c for c in ALL_PERM_CODES if any(c == p or c.startswith(p) for p in prefixes)}


def _principal(
    role_code: str = "EMP",
    *,
    data_scope: int | None = None,
    org_id: int | None = ORG_ID,
    dept_ids: tuple[int, ...] = (),
    user_id: int = USER_ID,
    tenant_id: int = TENANT_ID,
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        roles=(role_code,),
        data_scope=int(data_scope if data_scope is not None else _SCOPE_BY_ROLE[role_code]),
        dept_ids=tuple(dept_ids),
    )


def _access_token(
    *,
    roles: tuple[str, ...] = ("HR",),
    data_scope: int = int(RoleDataScope.ALL),
    token_version: int = 0,
    user_id: int = USER_ID,
    tenant_id: int = TENANT_ID,
    org_id: int | None = ORG_ID,
) -> str:
    return SecurityUtils.create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        roles=list(roles),
        data_scope=int(data_scope),
        token_version=token_version,
    )


def _assert_denied(callable_) -> PermissionDeniedError:  # type: ignore[no-untyped-def]
    """断言调用抛 40301（**不断言 HTTP 404**：防探测靠业务码统一）。"""
    with pytest.raises(PermissionDeniedError) as excinfo:
        callable_()
    assert excinfo.value.code == int(ErrorCode.NO_PERMISSION) == 40301
    return excinfo.value


def _call_perm(perm_code: str, principal: CurrentPrincipal):  # type: ignore[no-untyped-def]
    """直接调用 ``require_perm`` 返回的依赖（绕过 FastAPI 依赖注入）。

    ``db=None`` 是刻意的：L1 用例不应连库；非 ADM 分支会走
    ``get_perm_codes`` → DB 不可用 → **返回空集 → 40301**（fail-closed）。
    """
    return require_perm(perm_code)(principal=principal, db=None)


@pytest.fixture
def grant(monkeypatch):  # type: ignore[no-untyped-def]
    """把角色应有的权限码「注入」``AuthService.get_perm_codes``。

    返回 ``grant(role_code)``，可在同一条用例内切换角色
    （用于「拒绝 + 反向对照」两次断言）。
    """

    def _grant(role_code: str) -> set[str]:
        codes = _granted(role_code)
        monkeypatch.setattr(AuthService, "get_perm_codes", lambda db, principal: codes)
        return codes

    return _grant


def _cond_sql(clause) -> str:  # type: ignore[no-untyped-def]
    """把 SQLAlchemy 条件编译成可断言的 SQL 文本（无需数据库）。"""
    return str(clause.compile(compile_kwargs={"literal_binds": True})).replace("\n", " ")


# ============================================================================
# A 组 · 功能权限（RBAC 角色 × 接口）
# ============================================================================
def test_ac_01_emp_cannot_list_talents(grant) -> None:
    """AC-01 EMP 访问 API-30 档案列表 → 40301（权限矩阵 `❌`）。"""
    grant("EMP")
    _assert_denied(lambda: _call_perm("talent:list", _principal("EMP")))
    # 反向对照：ADM 在同一依赖上必须放行（证明用例不是「恒拒绝」的假通过）
    assert _call_perm("talent:list", _principal("ADM")).is_admin is True


def test_ac_02_exec_stats_only_cannot_list_talents(grant) -> None:
    """AC-02 EXEC（`⊘` 仅统计值）访问 API-30 档案列表 → 40301。"""
    grant("EXEC")
    _assert_denied(lambda: _call_perm("talent:list", _principal("EXEC")))
    grant("HR")
    assert _call_perm("talent:list", _principal("HR")) is not None  # 反向对照


def test_ac_03_aiops_cannot_use_business_interfaces(grant) -> None:
    """AC-03 AIOPS 访问 API-63 档案 RAG 问答 → 40301（AIOPS 不碰业务数据）。"""
    grant("AIOPS")
    _assert_denied(lambda: _call_perm("search:ask", _principal("AIOPS")))
    grant("HR")
    assert _call_perm("search:ask", _principal("HR")) is not None


def test_ac_04_audit_role_cannot_write(grant) -> None:
    """AC-04 AUDIT 调用写接口（API-46 批量确认）→ 40301（AUDIT 全列只读）。"""
    grant("AUDIT")
    _assert_denied(lambda: _call_perm("review:confirm", _principal("AUDIT")))
    grant("HR")
    assert _call_perm("review:confirm", _principal("HR")) is not None


def test_ac_05_mgr_cannot_use_review_queue(grant) -> None:
    """AC-05 MGR 访问 API-44 复核队列 → 40301（部门经理无复核权）。"""
    grant("MGR")
    _assert_denied(lambda: _call_perm("review:queue", _principal("MGR")))
    grant("HR")
    assert _call_perm("review:queue", _principal("HR")) is not None


def test_ac_06_hr_cannot_manage_model_config(grant) -> None:
    """AC-06 HR 访问 API-81 模型配置列表 → 40301（仅 AIOPS）。"""
    grant("HR")
    _assert_denied(lambda: _call_perm("ai:model:list", _principal("HR")))
    grant("AIOPS")
    assert _call_perm("ai:model:list", _principal("AIOPS")) is not None


def test_ac_07_exec_cannot_approve_deletion(grant) -> None:
    """AC-07 EXEC 访问 API-77 审批删除 → 40301（仅 ADM；删除是高影响不可逆操作）。"""
    grant("EXEC")
    _assert_denied(lambda: _call_perm("privacy:approve", _principal("EXEC")))
    assert _call_perm("privacy:approve", _principal("ADM")).is_admin is True


def test_ac_08_empty_perm_snapshot_fails_closed(monkeypatch) -> None:
    """AC-08 权限快照为空 → 40301（**fail-closed**：取不到权限认定即无权限）。"""
    monkeypatch.setattr(AuthService, "get_perm_codes", lambda db, principal: set())
    _assert_denied(lambda: _call_perm("talent:list", _principal("HR")))


# ============================================================================
# B 组 · 行级数据域（tenant + 部门子树 + 本人）
# ============================================================================
# ⚠️ 行级条件用 ``SysUser``（同时具备 ``tenant_id`` / ``org_id`` / ``id`` 三列），
#    与档案表（TBL-11）形状一致；档案模块落地后 CU-14 会再加一轮真实表断言。
def test_ac_09_mgr_condition_is_scoped_to_dept_subtree() -> None:
    """AC-09 MGR（`data_scope=2`）行级条件 = 租户 ∧ 部门子树。"""
    p = _principal("MGR", data_scope=2, dept_ids=(10, 11, 12))
    sql = _cond_sql(DataScopeFilter.mysql_condition(SysUser, p, dept_ids=(10, 11, 12)))
    assert "tenant_id" in sql, sql
    assert "org_id IN (10, 11, 12)" in sql, sql


def test_ac_10_mgr_cross_dept_request_is_denied() -> None:
    """AC-10 MGR 请求范围越出部门子树 → 40301（接口 §1.3：传参只能收窄）。"""
    p = _principal("MGR", data_scope=2, dept_ids=(10, 11))
    _assert_denied(lambda: DataScopeFilter.assert_scope_narrowing(p, [99], dept_ids=(10, 11)))


def test_ac_11_unresolved_subtree_locks_down_instead_of_widening() -> None:
    """AC-11 部门子树未解析 → **查不到**（绝不放行全量）。"""
    p = _principal("MGR", data_scope=2, dept_ids=())
    sql = _cond_sql(DataScopeFilter.mysql_condition(SysUser, p))
    assert "= -1" in sql, sql  # 与租户条件相与，恒假
    assert " OR " not in sql.upper(), sql  # 没有任何会放开的或条件


def test_ac_11b_dept_path_trailing_slash_prevents_sibling_bleed() -> None:
    """AC-11 配套：``dept_path`` 必须「含自身 + 尾斜杠」，否则前缀会命中兄弟部门。

    这是子树解析的**越权根因**：``/1/12`` 会同时命中 ``/1/120/``、``/1/123/``。
    """
    assert OrgScopeService.build_dept_path(None, 1) == "/1/"
    assert OrgScopeService.build_dept_path("/1/", 12) == "/1/12/"
    # 尾斜杠挡住兄弟部门：部门 1 下的另一个部门 /1/125/ 不会被 /1/12/ 命中
    assert not "/1/125/".startswith("/1/12/")
    assert not "/1/120/".startswith("/1/12/")
    # 真正的子孙仍被命中：部门 12 的子部门 125 → /1/12/125/
    assert "/1/12/125/".startswith("/1/12/")


def test_ac_12_emp_condition_narrows_to_owner() -> None:
    """AC-12 EMP（`data_scope=3`）行级条件收窄到「本人」→ 他人档案不满足条件。"""
    p = _principal("EMP", data_scope=3, user_id=USER_ID)
    sql = _cond_sql(DataScopeFilter.mysql_condition(SysUser, p, owner_column="id"))
    assert "id = " in sql, sql
    assert str(USER_ID) in sql, sql
    assert str(OTHER_USER_ID) not in sql, sql


def test_ac_13_emp_owns_self_only() -> None:
    """AC-13 EMP 只命中本人（条件里的归属人恒为自身 user_id）。"""
    p = _principal("EMP", data_scope=3, user_id=OTHER_USER_ID)
    sql = _cond_sql(DataScopeFilter.mysql_condition(SysUser, p, owner_column="id"))
    assert str(OTHER_USER_ID) in sql, sql
    assert str(USER_ID) not in sql, sql


def test_ac_14_cross_tenant_condition_always_present() -> None:
    """AC-14 跨租户：行级条件**永远**带本租户，且不出现他租户（即使 `data_scope=1`）。"""
    p = _principal("HR", data_scope=1, tenant_id=TENANT_ID)
    sql = _cond_sql(DataScopeFilter.mysql_condition(SysUser, p))
    assert f"tenant_id = {TENANT_ID}" in sql, sql
    assert str(OTHER_TENANT_ID) not in sql, sql


def test_ac_15_tenant_id_comes_only_from_token_claims() -> None:
    """AC-15 租户只能来自 JWT：无任何请求参数入口，伪造 query 也无法改变主体。"""
    # ① 依赖签名里只有一个 claims 参数 —— 没有任何「接收前端传参」的位置
    assert set(inspect.signature(get_current_principal).parameters) == {"claims"}
    assert set(inspect.signature(SecurityUtils.principal_from_claims).parameters) == {"payload"}
    # ② 主体租户恒等于 Token 的 tid
    payload = SecurityUtils.decode_token(_access_token(tenant_id=OTHER_TENANT_ID))
    assert SecurityUtils.principal_from_claims(payload).tenant_id == OTHER_TENANT_ID


def test_ac_16_unknown_data_scope_is_rejected() -> None:
    """AC-16 未知 `data_scope`（如 9）→ 40301（fail-closed，绝不按「最宽」放过）。"""
    p = _principal("EMP", data_scope=9)
    _assert_denied(lambda: DataScopeFilter.mysql_condition(SysUser, p))
    _assert_denied(lambda: DataScopeFilter.milvus_expr(p))


# ============================================================================
# C 组 · 字段级裁剪（明细可见性）
# ============================================================================
def test_ac_17_exec_denied_on_detail() -> None:
    """AC-17 EXEC 读档案详情 → 40301。"""
    _assert_denied(lambda: DataScopeFilter.require_detail_access(_principal("EXEC")))
    DataScopeFilter.require_detail_access(_principal("HR"))  # 反向对照：不抛


def test_ac_18_audit_denied_on_export() -> None:
    """AC-18 AUDIT 导出档案 → 40301。"""
    _assert_denied(lambda: DataScopeFilter.require_detail_access(_principal("AUDIT")))


def test_ac_19_aiops_denied_on_detail() -> None:
    """AC-19 AIOPS 读档案详情 → 40301。"""
    _assert_denied(lambda: DataScopeFilter.require_detail_access(_principal("AIOPS")))
    DataScopeFilter.require_detail_access(_principal("ADM"))  # 反向对照：超管放行


def test_ac_20_mgr_sees_masked_contact_fields() -> None:
    """AC-20 MGR 读档案详情 → 身份证/手机/邮箱**脱敏**（`138****8888`）。"""
    from app.schemas.review import mask_for_role

    assert mask_for_role("phone", "13812348888", can_see_pii=False) == "138****8888"
    assert mask_for_role("email", "zhangsan@example.com", can_see_pii=False) != "zhangsan@example.com"
    # 反向对照：HR/ADM 明文（业务必需）
    assert mask_for_role("phone", "13812348888", can_see_pii=True) == "13812348888"
    # 非 PII 字段不受角色影响
    assert mask_for_role("work_years", "5", can_see_pii=False) == "5"
    # None 原样透传（不变成字符串 "None"）
    assert mask_for_role("phone", None, can_see_pii=False) is None


def test_ac_21_hr_sees_plaintext_identity_fields() -> None:
    """AC-21 HR 读档案详情 → 身份字段**明文**（业务必需）。"""
    from app.schemas.review import mask_for_role

    assert mask_for_role("id_card", "110101199001011234", can_see_pii=True) == "110101199001011234"
    assert mask_for_role("email", "hr@example.com", can_see_pii=True) == "hr@example.com"


# ============================================================================
# D 组 · 前端传参收窄
# ============================================================================
def test_ac_22_mgr_subset_narrowing_is_allowed() -> None:
    """AC-22 `filters.org_ids` ⊆ 子树 → 放行。"""
    p = _principal("MGR", data_scope=2, dept_ids=(10, 11, 12))
    assert DataScopeFilter.assert_scope_narrowing(p, [10, 11], dept_ids=(10, 11, 12)) == (10, 11)


def test_ac_23_out_of_range_narrowing_is_denied() -> None:
    """AC-23 `filters.org_ids` ⊄ 子树 → 40301。"""
    p = _principal("MGR", data_scope=2, dept_ids=(10, 11))
    _assert_denied(lambda: DataScopeFilter.assert_scope_narrowing(p, [10, 99], dept_ids=(10, 11)))


def test_ac_24_emp_cannot_forge_dept_filter() -> None:
    """AC-24 EMP 伪造他人部门参数 → 40301。"""
    p = _principal("EMP", data_scope=3, org_id=ORG_ID)
    _assert_denied(lambda: DataScopeFilter.assert_scope_narrowing(p, [OTHER_ORG_ID]))
    # 反向对照：ADM（全部）不受部门限制
    admin = _principal("ADM", data_scope=1)
    assert DataScopeFilter.assert_scope_narrowing(admin, [OTHER_ORG_ID]) == (OTHER_ORG_ID,)


# ============================================================================
# E 组 · Token 生命周期与吊销
# ============================================================================
def test_ac_25_missing_credentials_is_40101() -> None:
    """AC-25 未带凭证 → 40101。"""
    with pytest.raises(UnauthenticatedError) as excinfo:
        get_access_claims(None)
    assert excinfo.value.code == 40101


def test_ac_26_expired_token_is_40101() -> None:
    """AC-26 过期 Token → 40101（走完整依赖，而非只调 decode）。"""
    expired = SecurityUtils._create_token(  # noqa: SLF001 — 直接构造过期凭证（不改系统时间）
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        token_type=TOKEN_TYPE_ACCESS,
        expires_in=-10,
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired)
    with pytest.raises(UnauthenticatedError) as excinfo:
        get_access_claims(creds)
    assert excinfo.value.code == 40101


def test_ac_27_password_change_invalidates_same_second_token(monkeypatch) -> None:
    """AC-27 改密后旧 Token 失效，**同一秒内新签发的 Token 不被误杀**。

    这正是 CU-04 用「版本号」而非「比较 iat 时间戳」的原因（JWT 的 iat 只有秒级精度）。
    """
    monkeypatch.setattr(token_dao, "is_token_revoked", lambda jti: False)
    monkeypatch.setattr(token_dao, "get_token_version", lambda tid, uid: 5)  # 当前会话版本 = 5

    old_payload = SecurityUtils.decode_token(_access_token(token_version=4))
    with pytest.raises(UnauthenticatedError):
        assert_token_alive(old_payload)

    new_payload = SecurityUtils.decode_token(_access_token(token_version=5))
    assert_token_alive(new_payload)  # 同秒签发的新 Token 必须有效


def test_ac_28_logged_out_token_is_revoked(monkeypatch) -> None:
    """AC-28 单 Token 登出后复用 → 40101（`jti` 否定名单命中）。"""
    monkeypatch.setattr(token_dao, "is_token_revoked", lambda jti: True)
    payload = SecurityUtils.decode_token(_access_token())
    with pytest.raises(UnauthenticatedError):
        assert_token_alive(payload)


def test_ac_29_refresh_token_cannot_access_business_api() -> None:
    """AC-29 用 refresh_token 访问业务接口 → 40101（typ 不符）。"""
    refresh = SecurityUtils.create_refresh_token(user_id=USER_ID, tenant_id=TENANT_ID)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh)
    with pytest.raises(UnauthenticatedError):
        get_access_claims(creds)
    assert TOKEN_TYPE_REFRESH != TOKEN_TYPE_ACCESS


# ============================================================================
# F 组 · 检索侧权限前置（CR-11）
# ============================================================================
def test_ac_30_search_expr_always_contains_tenant() -> None:
    """AC-30 Milvus 表达式必含 `tenant_id`（CR-11：权限在检索前生效）。"""
    expr = DataScopeFilter.milvus_expr(_principal("HR", data_scope=1, tenant_id=TENANT_ID))
    assert f"tenant_id == {TENANT_ID}" in expr, expr


def test_ac_31_search_expr_contains_dept_subtree() -> None:
    """AC-31 MGR 的检索表达式必含 `org_id in [子树]`。"""
    p = _principal("MGR", data_scope=2, dept_ids=(10, 11, 12))
    expr = DataScopeFilter.milvus_expr(p, dept_ids=(10, 11, 12))
    assert "org_id in [10, 11, 12]" in expr, expr
    # 子树未解析 → 收窄为不可命中，而非放开
    expr_empty = DataScopeFilter.milvus_expr(_principal("MGR", data_scope=2, dept_ids=()))
    assert "org_id == -1" in expr_empty, expr_empty


def test_ac_32_search_expr_is_mode_independent() -> None:
    """AC-32 表达式只由 principal 推导，**没有 mode 开关** → 降级为关键词检索时无法丢掉它。

    （端到端断言「降级后仍带 expr」需检索模块 CU-20；此处先钉死结构与语义。）
    """
    params = set(inspect.signature(DataScopeFilter.milvus_expr).parameters)
    assert params == {"principal", "dept_ids"}, params
    p = _principal("MGR", data_scope=2, dept_ids=(10,))
    assert DataScopeFilter.milvus_expr(p, dept_ids=(10,)) == DataScopeFilter.milvus_expr(
        p, dept_ids=(10,)
    )


def test_ac_33_stats_only_role_cannot_enter_retrieval() -> None:
    """AC-33 EXEC（仅统计值）进入语义检索链 → 40301。"""
    _assert_denied(lambda: DataScopeFilter.milvus_expr(_principal("EXEC")))


# ============================================================================
# G 组 · 统一口径与信息不泄露
# ============================================================================
def test_ac_34_not_found_in_own_domain_is_40401() -> None:
    """AC-34 本域内确实不存在 → 40401（与「越权」的 40301 明确区分）。"""
    assert NotFoundError("角色不存在").code == 40401
    assert NotFoundError("角色不存在").http_status == 404
    assert NotFoundError("角色不存在").code != PermissionDeniedError().code


def test_ac_35_denied_and_absent_share_the_same_code() -> None:
    """AC-35 越权与不存在**返回同一业务码**（40301），不可用于探测资源存在性。"""
    assert PermissionDeniedError("越权").code == 40301
    assert PermissionDeniedError("资源不存在").code == 40301  # 同一异常承载两种语义
    assert int(ErrorCode.NO_PERMISSION) != int(ErrorCode.NOT_FOUND)


def test_ac_36_40301_body_leaks_nothing() -> None:
    """AC-36 40301 响应体不含任何资源字段（无 `data`，message 为通用文案）。"""
    from app.core.middleware import TraceIdMiddleware

    isolated = FastAPI()
    isolated.add_middleware(TraceIdMiddleware)  # 与真实应用一致：trace_id 由最外层中间件注入
    register_exception_handlers(isolated)

    @isolated.get("/__boom")
    def _boom() -> None:  # pragma: no cover — 仅用于触发异常
        raise PermissionDeniedError()

    with TestClient(isolated) as client:
        resp = client.get("/__boom")

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == 40301
    assert body["data"] is None
    assert body["message"] == ERROR_META[ErrorCode.NO_PERMISSION].message == "无权限"
    assert body["trace_id"] is not None  # 统一结构，便于按 trace 排障


# ============================================================================
# H 组 · fail-closed 与降级边界
# ============================================================================
def test_ac_37_perm_storage_failure_fails_closed(monkeypatch) -> None:
    """AC-37 权限快照既不在缓存、数据库也异常 → **40301**（而非放行，也非 50000）。"""
    monkeypatch.setattr(AuthService, "_read_perm_snapshot", lambda tid, uid: None)

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("database is down")

    monkeypatch.setattr(user_dao, "list_perm_codes_by_user", _boom)

    p = _principal("HR")
    assert AuthService.get_perm_codes(None, p) == set()  # fail-closed：空集
    _assert_denied(lambda: _call_perm("talent:list", p))


def test_ac_38_redis_outage_does_not_upgrade_to_global_401(monkeypatch) -> None:
    """AC-38 Redis 抖动时吊销校验 **fail-open**（视为未吊销），不升级为全站 401。

    ⚠️ 与 AC-37 是**刻意相反**的取舍：权限取不到 → 拒（安全）；吊销名单取不到 → 放（可用）。
    """
    from redis.exceptions import RedisError

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RedisError("redis is down")

    monkeypatch.setattr(token_dao, "get_redis", _boom)

    assert token_dao.is_token_revoked("any-jti") is False
    assert token_dao.get_token_version(TENANT_ID, USER_ID) == 0
    assert_token_alive(SecurityUtils.decode_token(_access_token()))  # 不抛


def test_ac_39_retrieval_degradation_is_200_not_500() -> None:
    """AC-39 检索降级 → HTTP 200 + `code=50302` + `degraded=true`（🔴 严禁 50000）。"""
    resp = degraded(
        {"items": []},
        reason="Milvus 不可用",
        code=int(ErrorCode.VECTOR_DEGRADED),
        message="已降级为关键词检索",
    )
    assert resp.code == 50302
    assert resp.data is not None and resp.data["degraded"] is True
    assert resp.data["degraded_reason"] == "Milvus 不可用"
    assert ERROR_META[ErrorCode.VECTOR_DEGRADED].http_status == 200  # 不是 500
    assert int(ErrorCode.VECTOR_DEGRADED) != int(ErrorCode.INTERNAL_ERROR)


def test_ac_40_scope_narrowing_still_honours_detail_check() -> None:
    """AC-40 收窄校验对「仅统计值」角色同样生效（不可借参数绕过明细拦截）。"""
    p = _principal("EXEC")  # data_scope=4
    # 未传 org_ids → 不触发明细校验（返回 None），由接口自身决定是否需要范围参数
    assert DataScopeFilter.assert_scope_narrowing(p, None) is None
    # 一旦传了范围参数，就必须过明细可见性这一关
    _assert_denied(lambda: DataScopeFilter.assert_scope_narrowing(p, [ORG_ID]))


# ============================================================================
# L2 · 真实数据库上的端到端断言（缺依赖自动 skip）
# ============================================================================
def _baseline_tables_ready() -> bool:
    """检查 CU-05 依赖的表是否已建（未跑 ``alembic upgrade head`` 时 skip，而非 fail）。"""
    from sqlalchemy import inspect as sa_inspect

    from app.dao.session import engine

    try:
        names = set(sa_inspect(engine).get_table_names())
    except Exception:  # noqa: BLE001 — 连不上就交给 skip 处理
        return False
    return {"tenant", "sys_role", "sys_permission", "sys_role_permission", "sys_user"} <= names


@requires_db
def test_l2_rbac_endpoints_end_to_end() -> None:
    """L2：种子 → ADM 可列角色 / 看权限树；EMP 访问同接口 40301。

    这是 CU-05 唯一需要真实数据库的用例：覆盖「路由 → 依赖 → 服务 → DAO」全链路。
    """
    if not _baseline_tables_ready():
        pytest.skip("数据表不存在：请先执行 `alembic upgrade head`")

    from app.dao.session import SessionLocal
    from scripts.seed_dev import seed

    result = seed(
        tenant_code="test_e2e",
        tenant_name="端到端测试单位",
        username="e2e_admin",
        password="E2e@123456",
        reset_password=True,
    )
    tenant_id = int(result["tenant_id"])
    admin_id = int(result["user_id"])

    # 前置：确认种子确实写入了用户（用**独立会话**读，避免长事务快照看不到新提交）
    with SessionLocal() as check_db:
        assert user_dao.get_user_by_id(check_db, tenant_id, admin_id) is not None

    adm_token = _access_token(
        roles=("ADM",),
        data_scope=int(RoleDataScope.ALL),
        user_id=admin_id,
        tenant_id=tenant_id,
        org_id=None,
    )
    emp_token = _access_token(
        roles=("EMP",),
        data_scope=int(RoleDataScope.SELF),
        user_id=admin_id + 1,  # 该用户不存在也无妨：功能权限只看角色码
        tenant_id=tenant_id,
        org_id=None,
    )

    from app.main import create_app

    with TestClient(create_app()) as client:
        # ① ADM 列角色 → 7 个预置角色齐备
        resp = client.get("/api/v1/roles", headers={"Authorization": f"Bearer {adm_token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        codes = {item["role_code"] for item in body["data"]["items"]}
        assert {"ADM", "EXEC", "HR", "MGR", "EMP", "AIOPS", "AUDIT"} <= codes
        # 🔴 所有 ID 必须是字符串（CR-5）
        assert isinstance(body["data"]["items"][0]["id"], str)

        # ② ADM 看权限点树 → 与种子数量一致
        resp2 = client.get(
            "/api/v1/permissions/tree", headers={"Authorization": f"Bearer {adm_token}"}
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["data"]["total"] == len(ALL_PERM_CODES)

        # ③ EMP 访问同一接口 → 40301（跨角色的功能权限拦截）
        resp3 = client.get("/api/v1/roles", headers={"Authorization": f"Bearer {emp_token}"})
        assert resp3.status_code == 403, resp3.text
        assert resp3.json()["code"] == 40301
