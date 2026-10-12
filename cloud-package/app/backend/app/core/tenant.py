"""数据域：``CurrentPrincipal`` / ``DataScopeFilter`` / ``TenantQueryFilter``（类图 CL-6）。

🔴 **本模块存在的唯一理由**：接口 §1.7 与 CR-11 要求
**「列表侧 MySQL 条件」与「检索侧 Milvus 表达式」必须由同一个 principal 推导**。
把两者收在同一个类里，就不会出现「列表接口过滤了、检索接口忘了加 expr」的越权漏洞。

职责边界
--------
- **读**：一次请求的权限数据全部来自 JWT（接口 §1.3 红线：**只从 JWT 读取，绝不接受前端传参**）。
- **收窄**：前端可传 ``filters.org_ids`` 等参数，但**只能收窄不能放宽**，超出即 40301。
- **不写业务**：本模块只产出条件表达式与判定，不含任何业务规则。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_

from app.exceptions import PermissionDeniedError
from app.models.enums import RoleDataScope, label_of


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    """当前请求主体（由 JWT 解析而来）。

    字段名与 JWT claim 对应（接口 §1.3）：``sub``/``tid``/``oid``/``rls``/``dsc``。
    """

    user_id: int
    tenant_id: int
    org_id: int | None
    roles: tuple[str, ...] = ()
    data_scope: int = int(RoleDataScope.SELF)
    username: str | None = None
    real_name: str | None = None
    org_name: str | None = None
    #: 本部门子树 ID（含自身），由 Service 层按需解析后回填（惰性，避免每次都查组织树）
    dept_ids: tuple[int, ...] = field(default=())

    # ------------------------------------------------------------------
    @property
    def data_scope_name(self) -> str | None:
        return label_of(RoleDataScope, self.data_scope)

    def has_role(self, *role_codes: str) -> bool:
        wanted = {c.upper() for c in role_codes}
        return bool(wanted & {r.upper() for r in self.roles})

    @property
    def is_admin(self) -> bool:
        return self.has_role("ADM")

    @property
    def is_stats_only(self) -> bool:
        """``data_scope=4 仅统计值`` —— 明细接口一律 40301（接口 §1.7）。"""
        return int(self.data_scope) == int(RoleDataScope.STATS_ONLY)


class DataScopeFilter:
    """把 :class:`CurrentPrincipal` 翻译成**数据库条件**与**向量库表达式**。

    用法（Service 层）::

        scope = DataScopeFilter.mysql_condition(TalentBase, principal, dept_ids=dept_ids)
        stmt = select(TalentBase).where(scope)
    """

    # ------------------------------------------------------------------
    # 明细可见性
    # ------------------------------------------------------------------
    @staticmethod
    def require_detail_access(principal: CurrentPrincipal) -> None:
        """``EXEC / AIOPS / AUDIT``（仅统计值）访问明细接口 → 40301。

        ⚠️ 注意与「资源不存在」的统一口径：越权一律 40301，**不返回 40401**
        （接口 §1.7：避免通过状态码探测资源存在性）。
        """
        if principal.is_stats_only:
            raise PermissionDeniedError("当前角色仅可见统计值，不可访问明细数据")

    # ------------------------------------------------------------------
    # MySQL 侧（行级）
    # ------------------------------------------------------------------
    @staticmethod
    def mysql_condition(
        model: Any,
        principal: CurrentPrincipal,
        *,
        dept_ids: Sequence[int] | None = None,
        owner_column: str | None = None,
    ) -> ColumnElement[bool]:
        """产出行级过滤条件。

        :param model: ORM 模型类（需含 ``tenant_id`` 列）
        :param principal: 当前主体
        :param dept_ids: ``data_scope=2`` 时使用的部门子树（含自身）；缺省用 ``principal.dept_ids``
        :param owner_column: ``data_scope=3`` 时用于比对的"归属人"列名。
            档案类表传 ``"emp_user_id"``（本人）；用户表传 ``"id"``。
        :raises PermissionDeniedError: ``data_scope=4`` 访问明细
        """
        DataScopeFilter.require_detail_access(principal)

        tenant_clause = model.tenant_id == principal.tenant_id  # 🔴 租户条件永远第一位
        scope = int(principal.data_scope)

        if scope == int(RoleDataScope.ALL):
            return tenant_clause

        if scope == int(RoleDataScope.OWN_DEPT):
            ids = tuple(dept_ids if dept_ids is not None else principal.dept_ids)
            if not ids:
                # 未解析出部门子树时**不放行全量** —— 宁可查不到，也不越权
                return and_(tenant_clause, model.tenant_id == -1)
            return and_(tenant_clause, model.__table__.c["org_id"].in_(ids))

        if scope == int(RoleDataScope.SELF):
            column = owner_column or "id"
            return and_(tenant_clause, model.__table__.c[column] == principal.user_id)

        # 未知 data_scope → 拒绝（fail-closed）
        raise PermissionDeniedError(f"未知的数据范围配置：{scope}")

    # ------------------------------------------------------------------
    # Milvus 侧（检索前置过滤）
    # ------------------------------------------------------------------
    @staticmethod
    def milvus_expr(
        principal: CurrentPrincipal,
        *,
        dept_ids: Sequence[int] | None = None,
    ) -> str:
        """产出 Milvus ``search`` 的 ``expr`` 前置过滤表达式（CR-11 / DF-3 的 P7.2）。

        🔴 **权限在检索前生效**，禁止「先召回全量再应用层过滤」。
        🔴 即使降级为关键词检索，**仍然要保留该表达式**（架构 §4.4 降级要求）。

        产出形如::

            tenant_id == 1001 and org_id in [10, 11, 12]

        :raises PermissionDeniedError: ``data_scope=4`` 不允许进入检索链路
        """
        DataScopeFilter.require_detail_access(principal)

        clauses = [f"tenant_id == {int(principal.tenant_id)}"]
        scope = int(principal.data_scope)

        if scope == int(RoleDataScope.ALL):
            return clauses[0]

        if scope == int(RoleDataScope.OWN_DEPT):
            ids = tuple(dept_ids if dept_ids is not None else principal.dept_ids)
            if not ids:
                return f"{clauses[0]} and org_id == -1"
            joined = ", ".join(str(int(i)) for i in ids)
            return f"{clauses[0]} and org_id in [{joined}]"

        if scope == int(RoleDataScope.SELF):
            # 本人：退化为"本部门 + 后续按 talent_id 二次收窄"
            ids = tuple(dept_ids if dept_ids is not None else principal.dept_ids)
            if not ids:
                return f"{clauses[0]} and org_id == -1"
            joined = ", ".join(str(int(i)) for i in ids)
            return f"{clauses[0]} and org_id in [{joined}]"

        raise PermissionDeniedError(f"未知的数据范围配置：{scope}")

    # ------------------------------------------------------------------
    # 前端传参收窄校验
    # ------------------------------------------------------------------
    @staticmethod
    def assert_scope_narrowing(
        principal: CurrentPrincipal,
        requested_org_ids: Sequence[int] | None,
        *,
        dept_ids: Sequence[int] | None = None,
    ) -> tuple[int, ...] | None:
        """校验前端传来的 ``org_ids`` **只能收窄不能放宽**（接口 §1.3 红线）。

        :return: 校验通过的 org_ids（``None`` 表示未传，沿用自身范围）
        :raises PermissionDeniedError: 请求范围越出自身数据域
        """
        if not requested_org_ids:
            return None
        DataScopeFilter.require_detail_access(principal)

        scope = int(principal.data_scope)
        allowed: set[int] = set()
        if scope == int(RoleDataScope.ALL):
            # 全部范围：不限制具体部门，但仍强制租户条件（由 SQL 条件保证）
            return tuple(int(i) for i in requested_org_ids)
        if scope == int(RoleDataScope.OWN_DEPT):
            allowed = set(int(i) for i in (dept_ids if dept_ids is not None else principal.dept_ids))
        elif scope == int(RoleDataScope.SELF):
            if principal.org_id is not None:
                allowed = {int(principal.org_id)}

        out_of_range = {int(i) for i in requested_org_ids} - allowed
        if out_of_range:
            raise PermissionDeniedError("请求的数据范围超出行级权限")
        return tuple(int(i) for i in requested_org_ids)


class TenantQueryFilter:
    """租户条件的兜底注入（架构 §5 ``core/tenant.py``）。

    一期由 :meth:`DataScopeFilter.mysql_condition` 统一产出；本类保留为
    **DAO 层的最后一道防线**：即便某个 DAO 忘记带租户条件，也在此处补齐。
    """

    @staticmethod
    def for_tenant(model: Any, tenant_id: int) -> ColumnElement[bool]:
        return model.tenant_id == tenant_id
