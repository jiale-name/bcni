"""用户管理 DAO（TBL-02 / TBL-05，接口 API-13 ~ API-17）。

与 :mod:`app.dao.user_dao` 的分工
--------------------------------
``user_dao`` 服务于**认证链路**（登录、取角色、取权限码、解析组织），是**被认证流程依赖**的热路径；
本模块服务于**用户管理后台**（分页检索、建号、改号、分配角色），查询更重、过滤条件更多。

分成两个文件而不是塞进一个：``user_dao`` 的每次改动都要重新验证登录链路，
把管理侧的复杂 JOIN 混进去会让回归面积无谓地变大。

🔴 「最后一名管理员」守卫的数据来源
--------------------------------
:func:`count_enabled_admins` 是本模块**唯一**带业务语义的查询（判定「系统里还剩几个能用的超管」），
放在 DAO 是因为它就是一个 ``COUNT``；**要不要因此拒绝操作**的判定在 Service。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.user import SysRole, SysUser, SysUserRole


# ============================================================================
# 用户查询（API-13）
# ============================================================================
def _apply_filters(
    stmt: Select,
    tenant_id: int,
    *,
    keyword: str | None = None,
    org_ids: Sequence[int] | None = None,
    position_id: int | None = None,
    status: int | None = None,
    role_id: int | None = None,
) -> Select:
    """施加公共过滤条件（列表与计数**必须共用**，否则 total 与实际条数对不上）。"""
    stmt = stmt.where(SysUser.tenant_id == tenant_id, SysUser.is_deleted == 0)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(SysUser.username.like(like), SysUser.real_name.like(like)))
    if org_ids is not None:
        ids = [int(i) for i in org_ids]
        # 空列表 = 「已收窄到空集」⇒ 必须返回空，❌ 不能退化成「不过滤」
        stmt = stmt.where(SysUser.org_id.in_(ids)) if ids else stmt.where(SysUser.id == -1)
    if position_id is not None:
        stmt = stmt.where(SysUser.position_id == int(position_id))
    if status is not None:
        stmt = stmt.where(SysUser.status == int(status))
    if role_id is not None:
        stmt = stmt.where(
            SysUser.id.in_(
                select(SysUserRole.user_id).where(
                    SysUserRole.tenant_id == tenant_id,
                    SysUserRole.role_id == int(role_id),
                )
            )
        )
    return stmt


def list_users(
    db: Session,
    tenant_id: int,
    *,
    keyword: str | None = None,
    org_ids: Sequence[int] | None = None,
    position_id: int | None = None,
    status: int | None = None,
    role_id: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[SysUser]:
    """分页取用户（按 ID 倒序 —— 新账号在前，管理员找刚建的号最顺手）。"""
    stmt = _apply_filters(
        select(SysUser),
        tenant_id,
        keyword=keyword,
        org_ids=org_ids,
        position_id=position_id,
        status=status,
        role_id=role_id,
    )
    stmt = stmt.order_by(SysUser.id.desc()).offset(max(0, int(offset))).limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_users(
    db: Session,
    tenant_id: int,
    *,
    keyword: str | None = None,
    org_ids: Sequence[int] | None = None,
    position_id: int | None = None,
    status: int | None = None,
    role_id: int | None = None,
) -> int:
    """与 :func:`list_users` **同一套过滤条件**的总数。"""
    stmt = _apply_filters(
        select(func.count(SysUser.id)),
        tenant_id,
        keyword=keyword,
        org_ids=org_ids,
        position_id=position_id,
        status=status,
        role_id=role_id,
    )
    return int(db.execute(stmt).scalar() or 0)


def get_user_by_id(db: Session, tenant_id: int, user_id: int) -> SysUser | None:
    """按 ID 取**未软删**用户。"""
    stmt = select(SysUser).where(
        SysUser.tenant_id == tenant_id,
        SysUser.id == int(user_id),
        SysUser.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def username_exists(db: Session, tenant_id: int, username: str) -> bool:
    """登录名是否已被占用（**只看未软删** —— 与唯一索引 ``uk_tenant_username`` 口径一致）。"""
    stmt = select(SysUser.id).where(
        SysUser.tenant_id == tenant_id,
        SysUser.username == username,
        SysUser.is_deleted == 0,
    ).limit(1)
    return db.execute(stmt).scalar_one_or_none() is not None


def list_usernames_by_names(
    db: Session, tenant_id: int, usernames: Sequence[str]
) -> set[str]:
    """一批登录名中**已存在**的那些（批量导入的重复检测，避免逐行查库）。"""
    wanted = [u for u in dict.fromkeys(usernames) if u]
    if not wanted:
        return set()
    stmt = select(SysUser.username).where(
        SysUser.tenant_id == tenant_id,
        SysUser.username.in_(wanted),
        SysUser.is_deleted == 0,
    )
    return {str(x) for x in db.execute(stmt).scalars().all()}


def list_usernames_by_ids(db: Session, tenant_id: int, user_ids: Iterable[int]) -> dict[int, str]:
    """一批用户 ID → ``username`` 映射（**一次 IN 查询**，供审计日志反查操作人）。

    为什么单独写一个（而不是在 Service 里逐个 ``get_user_by_id``）：
    审计列表一页 20 条就有 20 个不同的操作人，逐条查就是典型 N+1
    （20 次往返）。这里一次查完，**查询次数与页大小无关**。

    ⚠️ 刻意**不过滤** ``is_deleted``：审计日志必须能显示「已删除用户做过什么」——
    若查不到软删用户，历史日志的操作人会集体变成空白，
    而这恰恰是审计最需要看的部分。同理，查不到就**不返回该键**，由调用方置 ``None``。
    """
    ids = {int(i) for i in user_ids if i is not None}
    if not ids:
        return {}
    stmt = select(SysUser.id, SysUser.username).where(
        SysUser.tenant_id == tenant_id,
        SysUser.id.in_(ids),
    )
    return {int(row[0]): str(row[1]) for row in db.execute(stmt).all()}


# ============================================================================
# 用户写入（API-14 / API-15）
# ============================================================================
def insert_user(db: Session, user: SysUser) -> SysUser:
    """新增用户。"""
    db.add(user)
    db.flush()
    return user


def update_user_fields(db: Session, tenant_id: int, user_id: int, **values: object) -> int:
    """按字段更新用户（**强制租户条件**）。返回受影响行数。"""
    if not values:
        return 0
    stmt = (
        update(SysUser)
        .where(SysUser.tenant_id == tenant_id, SysUser.id == int(user_id))
        .values(**values)
    )
    return int(db.execute(stmt).rowcount or 0)


def soft_delete_user(db: Session, tenant_id: int, user_id: int, deleted_at: datetime) -> int:
    """软删用户（同时置 ``status=0``，避免残留启用态被后续查询命中）。"""
    return update_user_fields(
        db, tenant_id, user_id, is_deleted=1, deleted_at=deleted_at, status=0
    )


# ============================================================================
# 用户 ↔ 角色（API-16）
# ============================================================================
def list_roles_by_ids(db: Session, tenant_id: int, role_ids: Iterable[int]) -> list[SysRole]:
    """按 ID 取未删除角色（批量校验用）。"""
    ids = [int(r) for r in role_ids]
    if not ids:
        return []
    stmt = select(SysRole).where(
        SysRole.tenant_id == tenant_id,
        SysRole.id.in_(ids),
        SysRole.is_deleted == 0,
    )
    return list(db.execute(stmt).scalars().all())


def list_role_map_by_users(
    db: Session, tenant_id: int, user_ids: Sequence[int]
) -> dict[int, list[SysRole]]:
    """批量取「用户 → 角色列表」（**一次查询**，避免列表接口 N+1）。"""
    ids = [int(u) for u in user_ids]
    if not ids:
        return {}
    stmt = (
        select(SysUserRole.user_id, SysRole)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysUserRole.tenant_id == tenant_id,
            SysUserRole.user_id.in_(ids),
            SysRole.tenant_id == tenant_id,
            SysRole.is_deleted == 0,
        )
        .order_by(SysUserRole.user_id.asc(), SysRole.role_code.asc())
    )
    out: dict[int, list[SysRole]] = {}
    for user_id, role in db.execute(stmt).all():
        out.setdefault(int(user_id), []).append(role)
    return out


def list_role_ids_by_user(db: Session, tenant_id: int, user_id: int) -> list[int]:
    """取某用户当前的角色 ID 列表。"""
    stmt = select(SysUserRole.role_id).where(
        SysUserRole.tenant_id == tenant_id,
        SysUserRole.user_id == int(user_id),
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


def delete_user_roles(db: Session, tenant_id: int, user_id: int) -> int:
    """清空某用户的角色绑定。"""
    stmt = delete(SysUserRole).where(
        SysUserRole.tenant_id == tenant_id,
        SysUserRole.user_id == int(user_id),
    )
    return int(db.execute(stmt).rowcount or 0)


def insert_user_roles(db: Session, rows: Sequence[SysUserRole]) -> int:
    """批量写入用户-角色绑定。"""
    if not rows:
        return 0
    db.add_all(list(rows))
    db.flush()
    return len(rows)


# ============================================================================
# 管理员守卫（API-15 / API-16 的「自锁」防护）
# ============================================================================
def count_enabled_admins(db: Session, tenant_id: int, *, exclude_user_id: int | None = None) -> int:
    """统计仍可用的超管数量：``status=1`` 的用户 × 有效 ``ADM`` 角色。

    :param exclude_user_id: 排除某用户（用于「**假如**把他禁用/去掉 ADM 后还剩几个」的预演）

    🔴 为什么要这个查询：禁用最后一个超管、或把自己从 ``ADM`` 里摘出去，
    会让系统**再没有任何人能进管理后台** —— 没有界面能修复，只能改库。
    这类「把自己锁在门外」的操作必须被前置拦住（42201），而不是事后救火。
    """
    stmt = (
        select(func.count(SysUserRole.user_id.distinct()))
        .join(SysUser, SysUser.id == SysUserRole.user_id)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysUserRole.tenant_id == tenant_id,
            SysRole.tenant_id == tenant_id,
            SysRole.role_code == "ADM",
            SysRole.is_deleted == 0,
            SysRole.status == 1,
            SysUser.tenant_id == tenant_id,
            SysUser.is_deleted == 0,
            SysUser.status == 1,
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(SysUser.id != int(exclude_user_id))
    return int(db.execute(stmt).scalar() or 0)


def user_has_role_code(db: Session, tenant_id: int, user_id: int, role_code: str) -> bool:
    """某用户是否持有某角色码（用于「改的是不是自己」之外的语义判定）。"""
    stmt = (
        select(SysUserRole.user_id)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysUserRole.tenant_id == tenant_id,
            SysUserRole.user_id == int(user_id),
            SysRole.tenant_id == tenant_id,
            SysRole.role_code == role_code,
            SysRole.is_deleted == 0,
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


__all__ = [
    "count_enabled_admins",
    "count_users",
    "delete_user_roles",
    "get_user_by_id",
    "insert_user",
    "insert_user_roles",
    "list_role_ids_by_user",
    "list_role_map_by_users",
    "list_roles_by_ids",
    "list_users",
    "list_usernames_by_ids",
    "list_usernames_by_names",
    "soft_delete_user",
    "update_user_fields",
    "user_has_role_code",
    "username_exists",
]
