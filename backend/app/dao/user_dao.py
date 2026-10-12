"""用户 / 角色 / 权限 / 租户 的数据访问（架构 §4.1「基座·权限」→ ``dao/user_dao.py``）。

🔴 DAO 红线（架构 §3.3）：
- **只做纯 CRUD**，不含业务规则（业务判定在 Service）；
- **强制租户条件**（每个查询都带 ``tenant_id``，且过滤 ``is_deleted``）；
- 不拼接 SQL，全走 ORM。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from app.models.tenant import OrgDept, Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole


# ============================================================================
# 租户（TBL-01）
# ============================================================================
def get_tenant_by_code(db: Session, tenant_code: str) -> Tenant | None:
    """按租户编码取租户（登录时可选传 ``tenant_code``；一期留空即取默认租户）。"""
    stmt: Select[tuple[Tenant]] = select(Tenant).where(
        Tenant.tenant_code == tenant_code, Tenant.status == 1
    )
    return db.execute(stmt).scalar_one_or_none()


def get_default_tenant(db: Session) -> Tenant | None:
    """取默认租户（一期单行）。"""
    stmt: Select[tuple[Tenant]] = select(Tenant).where(Tenant.status == 1).order_by(Tenant.id).limit(1)
    return db.execute(stmt).scalar_one_or_none()


# ============================================================================
# 用户（TBL-02）
# ============================================================================
def get_user_by_username(db: Session, tenant_id: int, username: str) -> SysUser | None:
    """按登录名取用户（**不过滤锁定状态** —— 锁定判定属业务，交 Service）。"""
    stmt = select(SysUser).where(
        SysUser.tenant_id == tenant_id,
        SysUser.username == username,
        SysUser.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_user_by_id(db: Session, tenant_id: int, user_id: int) -> SysUser | None:
    stmt = select(SysUser).where(
        SysUser.tenant_id == tenant_id,
        SysUser.id == user_id,
        SysUser.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def update_user_fields(db: Session, user_id: int, tenant_id: int, **values: Any) -> int:
    """按列更新用户（**纯持久化，不含判定**）。返回受影响行数。

    只允许白名单列，避免上层误传 ``id`` / ``tenant_id`` 造成越权改写。
    """
    allowed = {
        "password_hash",
        "real_name",
        "org_id",
        "position_id",
        "phone_enc",
        "email_enc",
        "status",
        "login_fail_count",
        "locked_until",
        "last_login_at",
        "is_deleted",
        "deleted_at",
    }
    payload = {k: v for k, v in values.items() if k in allowed}
    if not payload:
        return 0
    stmt = (
        update(SysUser)
        .where(SysUser.id == user_id, SysUser.tenant_id == tenant_id)  # 🔴 强制租户条件
        .values(**payload)
    )
    result = db.execute(stmt)
    return int(result.rowcount or 0)


# ============================================================================
# 角色（TBL-03 / TBL-05）
# ============================================================================
def list_roles_by_user(db: Session, tenant_id: int, user_id: int) -> list[SysRole]:
    """取用户的有效角色（``status=1``，未软删，按 ``sort`` 无关的稳定序：``id``）。"""
    stmt = (
        select(SysRole)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(
            SysUserRole.tenant_id == tenant_id,
            SysUserRole.user_id == user_id,
            SysRole.tenant_id == tenant_id,
            SysRole.status == 1,
            SysRole.is_deleted == 0,
        )
        .order_by(SysRole.id)
    )
    return list(db.execute(stmt).scalars().all())


# ============================================================================
# 权限点（TBL-04 / TBL-06）
# ============================================================================
def list_perm_codes_by_user(db: Session, tenant_id: int, user_id: int) -> set[str]:
    """取用户经由角色获得的有效权限码集合（API-05 权限快照的数据来源）。

    三表联结：``sys_user_role`` → ``sys_role_permission`` → ``sys_permission``，
    全程带 ``tenant_id``，并过滤停用/软删的权限点与停用的角色。
    """
    stmt = (
        select(SysPermission.perm_code)
        .join(SysRolePermission, SysRolePermission.perm_id == SysPermission.id)
        .join(SysRole, SysRole.id == SysRolePermission.role_id)
        .join(SysUserRole, SysUserRole.role_id == SysRole.id)
        .where(
            SysUserRole.tenant_id == tenant_id,
            SysUserRole.user_id == user_id,
            SysRole.tenant_id == tenant_id,
            SysRole.status == 1,
            SysRole.is_deleted == 0,
            SysRolePermission.tenant_id == tenant_id,
            SysPermission.tenant_id == tenant_id,
            SysPermission.status == 1,
            SysPermission.is_deleted == 0,
        )
    )
    return {code for code in db.execute(stmt).scalars().all() if code}


def list_perm_codes_by_roles(db: Session, tenant_id: int, role_ids: Iterable[int]) -> set[str]:
    """按角色集合取权限码（供 CU-05 角色权限编辑后回显）。"""
    ids = [int(r) for r in role_ids]
    if not ids:
        return set()
    stmt = (
        select(SysPermission.perm_code)
        .join(SysRolePermission, SysRolePermission.perm_id == SysPermission.id)
        .where(
            SysRolePermission.tenant_id == tenant_id,
            SysRolePermission.role_id.in_(ids),
            SysPermission.tenant_id == tenant_id,
            SysPermission.status == 1,
            SysPermission.is_deleted == 0,
        )
    )
    return {code for code in db.execute(stmt).scalars().all() if code}


def list_all_perm_codes(db: Session, tenant_id: int) -> set[str]:
    """取租户下全部启用权限码（供 ``ADM`` 的 API-05 快照 —— 超管默认拥有全部权限点）。"""
    stmt = select(SysPermission.perm_code).where(
        SysPermission.tenant_id == tenant_id,
        SysPermission.status == 1,
        SysPermission.is_deleted == 0,
    )
    return {code for code in db.execute(stmt).scalars().all() if code}


# ============================================================================
# 组织（TBL-07）
# ============================================================================
def get_org_by_id(db: Session, tenant_id: int, org_id: int) -> OrgDept | None:
    stmt = select(OrgDept).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.id == org_id,
        OrgDept.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()
