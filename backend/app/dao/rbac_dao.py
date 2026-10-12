"""RBAC 数据访问（TBL-03 / TBL-04 / TBL-06 / TBL-07 子树）＋ 权限快照版本号（Redis）。

分层位置：``routers → services → **dao**``。本模块**只做纯 CRUD**，
不含任何业务判定（越权、内置角色不可删、权限点合法性、快照失效编排等一律在 Service）。

🔴 DAO 三条红线（架构 §3.3）
---------------------------
1. **强制租户条件**：每个查询/更新都带 ``tenant_id``，且过滤 ``is_deleted``；
2. **不拼接 SQL**：全走 ORM（除 ``dept_path`` 前缀匹配用 ``LIKE``，由 SQLAlchemy 参数化）；
3. **不含业务分支**：不判断角色类型、不决定谁能删谁不能删。

关于 ``dept_path`` 约定（子树查询的前提，CU-05 起生效）
------------------------------------------------------
``org_dept.dept_path`` 定义为「**从根到自身、含自身、以 ``/`` 收尾**」，
如根 ``/1/``、其子 ``/1/12/``、再下一级 ``/1/12/125/``。

因此「某部门的子树」= ``id = 自身`` ∪ ``dept_path LIKE '<自身路径>%'``。
DAO 只负责给出 ``LIKE`` 的那一半；**自身的并入与失败兜底在 Service**
（:meth:`app.services.rbac_service.OrgScopeService.resolve_dept_ids`）。

⚠️ 若 ``dept_path`` 未维护（NULL/空），DAO 返回空列表，Service 退化为「只含自身」——
**安全优先，绝不放大范围**（与 ``core/tenant.py`` 未解析子树时锁死不可见一致）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.dao.redis_client import KEY_SESSION, build_key, get_redis
from app.models.tenant import OrgDept
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUserRole

logger = get_logger(__name__)

#: 视为「Redis 不可用」的异常集合（与 token_dao 保持一致）
_REDIS_ERRORS = (RedisError, OSError)

_KEY_PERM_VER = "perm_ver"  # sess:perm_ver:<tid>[:<uid>]

#: 权限版本号有效期。权限变更比 Token 失效「活得更久」，
#: 故用 90 天而非 ``REFRESH_TOKEN_EXPIRE_SECONDS``：
#: 计数器一旦过期归零，前端会看到版本号**回退**，从而漏掉一次「需重新拉取权限」的提示。
_PERM_VER_TTL_SECONDS = 90 * 24 * 3600

#: 版本号合成基数：``全局版本 × BASE + 用户级版本``（见 :func:`get_perm_version`）
_PERM_VER_BASE = 10**9


# ============================================================================
# 角色（TBL-03）
# ============================================================================
def list_roles(db: Session, tenant_id: int) -> list[SysRole]:
    """取租户下全部未软删角色（预置角色在前，其余按 id 稳定序）。"""
    stmt: Select[tuple[SysRole]] = (
        select(SysRole)
        .where(SysRole.tenant_id == tenant_id, SysRole.is_deleted == 0)
        .order_by(SysRole.is_builtin.desc(), SysRole.id)
    )
    return list(db.execute(stmt).scalars().all())


def get_role_by_id(db: Session, tenant_id: int, role_id: int) -> SysRole | None:
    stmt = select(SysRole).where(
        SysRole.tenant_id == tenant_id,
        SysRole.id == role_id,
        SysRole.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_role_by_code(db: Session, tenant_id: int, role_code: str) -> SysRole | None:
    stmt = select(SysRole).where(
        SysRole.tenant_id == tenant_id,
        SysRole.role_code == role_code,
        SysRole.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def insert_role(db: Session, role: SysRole) -> SysRole:
    """插入角色并 ``flush`` 取回雪花 ID（事务边界仍由 Service 掌控，**不 commit**）。"""
    db.add(role)
    db.flush()
    return role


def update_role_fields(db: Session, tenant_id: int, role_id: int, **values: Any) -> int:
    """按白名单列更新角色。返回受影响行数（**不含判定**）。"""
    allowed = {"role_name", "data_scope", "status", "is_deleted", "deleted_at"}
    payload = {k: v for k, v in values.items() if k in allowed}
    if not payload:
        return 0
    stmt = (
        update(SysRole)
        .where(SysRole.id == role_id, SysRole.tenant_id == tenant_id)  # 🔴 强制租户条件
        .values(**payload)
    )
    return int(db.execute(stmt).rowcount or 0)


# ============================================================================
# 权限点（TBL-04）
# ============================================================================
def list_permissions(db: Session, tenant_id: int, *, enabled_only: bool = True) -> list[SysPermission]:
    """取权限点（默认仅启用项）。排序：父 → 子、``sort_no`` → ``id``（供构树用）。"""
    stmt = select(SysPermission).where(
        SysPermission.tenant_id == tenant_id,
        SysPermission.is_deleted == 0,
    )
    if enabled_only:
        stmt = stmt.where(SysPermission.status == 1)
    stmt = stmt.order_by(SysPermission.parent_id, SysPermission.sort_no, SysPermission.id)
    return list(db.execute(stmt).scalars().all())


def get_permission_by_id(db: Session, tenant_id: int, perm_id: int) -> SysPermission | None:
    stmt = select(SysPermission).where(
        SysPermission.tenant_id == tenant_id,
        SysPermission.id == perm_id,
        SysPermission.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_permissions_by_ids(db: Session, tenant_id: int, perm_ids: Iterable[int]) -> list[SysPermission]:
    """按 ID 集合取权限点（供绑定前校验「ID 是否都属于本租户」）。"""
    ids = [int(p) for p in perm_ids]
    if not ids:
        return []
    stmt = select(SysPermission).where(
        SysPermission.tenant_id == tenant_id,
        SysPermission.id.in_(ids),
        SysPermission.is_deleted == 0,
    )
    return list(db.execute(stmt).scalars().all())


def list_permission_codes_by_ids(db: Session, tenant_id: int, perm_ids: Iterable[int]) -> set[str]:
    """按 ID 集合取权限码（供角色权限回显）。"""
    return {p.perm_code for p in list_permissions_by_ids(db, tenant_id, perm_ids) if p.perm_code}


def get_permission_by_code(db: Session, tenant_id: int, perm_code: str) -> SysPermission | None:
    stmt = select(SysPermission).where(
        SysPermission.tenant_id == tenant_id,
        SysPermission.perm_code == perm_code,
        SysPermission.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def insert_permission(db: Session, perm: SysPermission) -> SysPermission:
    db.add(perm)
    db.flush()
    return perm


def update_permission_fields(db: Session, tenant_id: int, perm_id: int, **values: Any) -> int:
    allowed = {"perm_name", "perm_type", "parent_id", "sort_no", "status", "is_deleted", "deleted_at"}
    payload = {k: v for k, v in values.items() if k in allowed}
    if not payload:
        return 0
    stmt = (
        update(SysPermission)
        .where(SysPermission.id == perm_id, SysPermission.tenant_id == tenant_id)
        .values(**payload)
    )
    return int(db.execute(stmt).rowcount or 0)


# ============================================================================
# 角色-权限（TBL-06）
# ============================================================================
def list_perm_ids_by_role(db: Session, tenant_id: int, role_id: int) -> list[int]:
    stmt = select(SysRolePermission.perm_id).where(
        SysRolePermission.tenant_id == tenant_id,
        SysRolePermission.role_id == role_id,
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


def delete_role_permissions(db: Session, tenant_id: int, role_id: int) -> int:
    """清空该角色的权限绑定（**全量替换**的前半段）。返回删除行数。"""
    stmt = delete(SysRolePermission).where(
        SysRolePermission.tenant_id == tenant_id,
        SysRolePermission.role_id == role_id,
    )
    return int(db.execute(stmt).rowcount or 0)


def list_role_permission_ids(db: Session, tenant_id: int, role_id: int) -> list[int]:
    """取该角色当前绑定的权限点 ID（升序）。

    用途：**权限变更的审计留痕**需要「改前集合」才能回答
    「这次到底回收了哪几个权限」。而
    :func:`~app.services.rbac_service.RbacService.bind_role_permissions`
    是**全量替换**语义（先 DELETE 再 INSERT），若不在此前读一次，
    改前集合就**永久丢失**了。

    ⚠️ 必须在 ``delete_role_permissions`` **之前**调用。
    """
    stmt = (
        select(SysRolePermission.perm_id)
        .where(
            SysRolePermission.tenant_id == tenant_id,
            SysRolePermission.role_id == role_id,
        )
        .order_by(SysRolePermission.perm_id.asc())
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


def insert_role_permissions(db: Session, rows: Sequence[SysRolePermission]) -> int:
    """批量插入角色-权限绑定（全量替换的后半段）。"""
    if not rows:
        return 0
    db.add_all(list(rows))
    db.flush()
    return len(rows)


# ============================================================================
# 用户-角色（TBL-05）—— 仅用于「角色变更后需失效哪些人的权限快照」
# ============================================================================
def list_user_ids_by_role(db: Session, tenant_id: int, role_id: int) -> list[int]:
    """取拥有该角色的用户 ID（供权限变更后按用户失效快照）。"""
    stmt = select(SysUserRole.user_id).where(
        SysUserRole.tenant_id == tenant_id,
        SysUserRole.role_id == role_id,
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


def list_role_ids_by_user(db: Session, tenant_id: int, user_id: int) -> list[int]:
    stmt = select(SysUserRole.role_id).where(
        SysUserRole.tenant_id == tenant_id,
        SysUserRole.user_id == user_id,
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


# ============================================================================
# 组织子树（TBL-07）—— 行级权限 ``data_scope=2`` 的实现基础
# ============================================================================
def list_dept_subtree_ids(db: Session, tenant_id: int, path_prefix: str) -> list[int]:
    """按 ``dept_path`` 前缀取子树部门 ID（**不含自身**，自身由 Service 并入）。

    :param path_prefix: 形如 ``/1/12/``（含自身、以 ``/`` 收尾）；空串直接返回 ``[]``。

    ⚠️ 传空/None 时返回空 —— 由 Service 退化为「只含自身」，属**安全兜底**而非错误。
    """
    if not path_prefix:
        return []
    stmt = select(OrgDept.id).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.is_deleted == 0,
        OrgDept.dept_path.like(f"{path_prefix}%"),
    )
    return [int(x) for x in db.execute(stmt).scalars().all()]


# ⚠️ 「统计部门下的用户数」已移至 :func:`app.dao.org_dao.count_users_in_depts`
#    （CU-06 落地 API-10 时统一到 org_dao，并新增 ``enabled_only`` 开关）。
#    此处原有的同名函数已删除 —— 同一查询两处实现必然漂移，只保留一个事实来源。


def soft_delete_role(db: Session, tenant_id: int, role_id: int, deleted_at: datetime) -> int:
    """软删角色（同时置 ``status=0``，避免残留启用态被后续查询命中）。"""
    return update_role_fields(
        db, tenant_id, role_id, is_deleted=1, deleted_at=deleted_at, status=0
    )


# ============================================================================
# 权限快照版本号（Redis ``sess:perm_ver:*``）
# ============================================================================
# 为什么需要「精确版本号」而不是「角色数×1000+权限点数」：
#   后者在两个变更**恰好互相抵消**时会得到同一个值（如「删 1 个角色、加 1 个权限点」），
#   前端据此认为权限没变、不去重新拉取 —— 这是一次真实的越权窗口。
#   版本号只增不减，任何一次变更都必然改变它。
#
# 为什么要分「全局」与「用户级」两级：
#   ① 改「角色↔权限」绑定 → 影响该角色下的**所有用户** ⇒ 递增**全局**版本；
#   ② 改「用户↔角色」关系 → 只影响**单个用户** ⇒ 递增**用户级**版本。
#   若一律递增全局版本，一次用户角色调整会让全租户前端都重新拉权限（无谓抖动）。
def _perm_ver_key(tenant_id: int | str, user_id: int | str | None = None) -> str:
    if user_id is None:
        return build_key(KEY_SESSION, _KEY_PERM_VER, tenant_id)
    return build_key(KEY_SESSION, _KEY_PERM_VER, tenant_id, user_id)


def _incr_perm_ver(key: str) -> int:
    try:
        client = get_redis()
        with client.pipeline(transaction=True) as pipe:
            pipe.incr(key, 1)
            pipe.expire(key, _PERM_VER_TTL_SECONDS)
            version, _ = pipe.execute()
        return int(version)
    except _REDIS_ERRORS as exc:
        logger.warning("权限版本递增失败（Redis 不可用）key=%s err=%s", key, exc)
        return -1


def bump_global_perm_version(tenant_id: int | str) -> int:
    """递增**租户全局**权限版本（角色↔权限绑定变更后调用）。失败返回 ``-1``。"""
    return _incr_perm_ver(_perm_ver_key(tenant_id))


def bump_user_perm_version(tenant_id: int | str, user_id: int | str) -> int:
    """递增**用户级**权限版本（用户↔角色变更后调用）。失败返回 ``-1``。"""
    return _incr_perm_ver(_perm_ver_key(tenant_id, user_id))


def _read_perm_ver(key: str) -> int:
    try:
        value = get_redis().get(key)
        return int(value) if value else 0
    except (RedisError, OSError, ValueError) as exc:
        logger.warning("权限版本读取失败（按 0 处理）key=%s err=%s", key, exc)
        return 0


def get_perm_version(tenant_id: int | str, user_id: int | str) -> int:
    """合成权限版本号 = ``全局版本 × 1e9 + 用户级版本``（**只增不减**）。

    Redis 不可用时返回 ``0`` —— 属可丢数据（DS-3），不得因缓存抖动使接口失败。
    """
    global_ver = _read_perm_ver(_perm_ver_key(tenant_id))
    user_ver = _read_perm_ver(_perm_ver_key(tenant_id, user_id))
    return global_ver * _PERM_VER_BASE + user_ver
