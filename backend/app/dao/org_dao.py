"""组织与岗位 DAO（TBL-07 / TBL-08，接口 API-07 ~ API-12）。

分层约定（架构 §3.3）：**DAO 只做纯数据访问** —— 不含业务判定（能否移动、能否删除、
``dept_path`` 怎么算），这些全在 :mod:`app.services.org_service`。

🔴 三条不可省略的约束
--------------------
1. **每个查询都带 ``tenant_id``**（数据库 §0 原则 5），且放在条件的**第一个**位置，
   以便命中「``tenant_id`` 为最左列」的联合索引；
2. **软删过滤默认打开** —— 所有读方法一律 ``is_deleted == 0``，
   唯一的例外是 :func:`get_dept_by_id`（由调用方决定是否校验软删）；
3. **排序必须确定** —— 组织树与列表都按 ``(sort_no, id)`` 排序，
   否则同一份数据两次请求可能顺序不同，前端树控件会出现「节点乱跳」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.tenant import OrgDept, Position
from app.models.user import SysUser, SysUserRole


# ============================================================================
# TBL-07 组织架构
# ============================================================================
def list_depts(db: Session, tenant_id: int, *, enabled_only: bool = False) -> list[OrgDept]:
    """取本租户全部未删除部门（扁平），按层级 → 排序号 → ID。

    :param enabled_only: 仅取 ``status=1``（停用部门仍会被组织树返回，
        否则其子树会连同父节点一起从界面上消失 —— 与权限树对「父节点缺失」的处理同源）
    """
    stmt: Select[tuple[OrgDept]] = select(OrgDept).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.is_deleted == 0,
    )
    if enabled_only:
        stmt = stmt.where(OrgDept.status == 1)
    stmt = stmt.order_by(OrgDept.dept_level.asc(), OrgDept.sort_no.asc(), OrgDept.id.asc())
    return list(db.execute(stmt).scalars().all())


def get_dept_by_id(db: Session, tenant_id: int, dept_id: int) -> OrgDept | None:
    """按 ID 取部门（**含已软删**，由调用方决定是否拒绝）。

    ⚠️ 故意不过滤 ``is_deleted``：删除部门前需要先拿到已软删记录来判断 404 还是幂等，
    过滤掉会让「重复删除」从幂等变成 404。
    """
    stmt = select(OrgDept).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.id == int(dept_id),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_dept_by_code(db: Session, tenant_id: int, dept_code: str) -> OrgDept | None:
    """按组织编码取**未删除**部门（编码唯一性校验用）。"""
    stmt = select(OrgDept).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.dept_code == dept_code,
        OrgDept.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_subtree_depts(db: Session, tenant_id: int, path_prefix: str) -> list[OrgDept]:
    """按 ``dept_path`` 前缀取子树部门（**含自身**），按层级升序。

    与 :func:`app.dao.rbac_dao.list_dept_subtree_ids` 的区别：那个只返回 ID（供行级过滤），
    本函数返回**完整实体**，供 **API-09 移动部门时级联重算 ``dept_path``/``dept_level``**。

    :param path_prefix: 形如 ``/1/12/``（含自身、以 ``/`` 收尾）。空串返回 ``[]``。

    ⚠️ 升序很重要：调用方按此顺序「先改父、后改子」的写法虽然对本算法无影响
    （因为新的路径是**一次性算好**的），但升序能让日志可读、并便于将来改为增量更新。
    """
    if not path_prefix:
        return []
    stmt = (
        select(OrgDept)
        .where(
            OrgDept.tenant_id == tenant_id,
            OrgDept.is_deleted == 0,
            OrgDept.dept_path.like(f"{path_prefix}%"),
        )
        .order_by(OrgDept.dept_level.asc(), OrgDept.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def count_child_depts(db: Session, tenant_id: int, parent_id: int) -> int:
    """统计直接子部门数（API-10 删除前置校验）。"""
    stmt = select(func.count(OrgDept.id)).where(
        OrgDept.tenant_id == tenant_id,
        OrgDept.parent_id == int(parent_id),
        OrgDept.is_deleted == 0,
    )
    return int(db.execute(stmt).scalar() or 0)


def insert_dept(db: Session, dept: OrgDept) -> OrgDept:
    """新增部门（``id`` 由调用方用雪花 ID 生成，因为 ``dept_path`` 依赖它）。"""
    db.add(dept)
    db.flush()
    return dept


def update_dept_fields(db: Session, tenant_id: int, dept_id: int, **values: object) -> int:
    """按字段更新部门（**强制租户条件**）。返回受影响行数。"""
    if not values:
        return 0
    stmt = (
        update(OrgDept)
        .where(OrgDept.tenant_id == tenant_id, OrgDept.id == int(dept_id))
        .values(**values)
    )
    return int(db.execute(stmt).rowcount or 0)


def count_users_in_depts(
    db: Session, tenant_id: int, dept_ids: Iterable[int], *, enabled_only: bool = True
) -> int:
    """统计这些部门下的用户数（API-10 删除部门的引用校验）。

    :param enabled_only: ``True`` 只数**在职**（``status=1``）—— 与接口文档「有在职人员拒绝」一致；
        ``False`` 数**全部未软删账号**（含已禁用）。

    🔴 为什么删除时要连「已禁用但未删除」的账号一起拦：
    禁用账号同样持有 ``org_id``，部门一旦软删，该账号的 ``dept_path`` 解析会退化为空
    （``OrgScopeService`` 的 fail-closed 兜底），行级权限静默失效。宁可让管理员
    先把人挪走，也不要留下指向已删部门的悬挂 ``org_id``。
    """
    ids = [int(i) for i in dept_ids]
    if not ids:
        return 0
    stmt = select(func.count(SysUser.id)).where(
        SysUser.tenant_id == tenant_id,
        SysUser.org_id.in_(ids),
        SysUser.is_deleted == 0,
    )
    if enabled_only:
        stmt = stmt.where(SysUser.status == 1)
    return int(db.execute(stmt).scalar() or 0)


# ============================================================================
# TBL-08 岗位
# ============================================================================
def list_positions(
    db: Session,
    tenant_id: int,
    *,
    dept_id: int | None = None,
    status: int | None = None,
    keyword: str | None = None,
) -> list[Position]:
    """岗位列表（可选按部门 / 状态 / 关键字过滤）。"""
    stmt: Select[tuple[Position]] = select(Position).where(
        Position.tenant_id == tenant_id,
        Position.is_deleted == 0,
    )
    if dept_id is not None:
        stmt = stmt.where(Position.dept_id == int(dept_id))
    if status is not None:
        stmt = stmt.where(Position.status == int(status))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(Position.position_code.like(like), Position.position_name.like(like))
        )
    stmt = stmt.order_by(Position.position_code.asc(), Position.id.asc())
    return list(db.execute(stmt).scalars().all())


def get_position_by_id(db: Session, tenant_id: int, position_id: int) -> Position | None:
    """按 ID 取未删除岗位。"""
    stmt = select(Position).where(
        Position.tenant_id == tenant_id,
        Position.id == int(position_id),
        Position.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_position_by_code(db: Session, tenant_id: int, position_code: str) -> Position | None:
    """按岗位编码取未删除岗位（唯一性校验用）。"""
    stmt = select(Position).where(
        Position.tenant_id == tenant_id,
        Position.position_code == position_code,
        Position.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_position_ids_by_codes(
    db: Session, tenant_id: int, codes: Sequence[str]
) -> dict[str, Position]:
    """按一批岗位编码取岗位（批量导入用，避免逐行查库）。返回 ``code → Position``。"""
    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted:
        return {}
    stmt = select(Position).where(
        Position.tenant_id == tenant_id,
        Position.position_code.in_(wanted),
        Position.is_deleted == 0,
    )
    return {p.position_code: p for p in db.execute(stmt).scalars().all()}


def insert_position(db: Session, position: Position) -> Position:
    """新增岗位。"""
    db.add(position)
    db.flush()
    return position


__all__ = [
    "count_child_depts",
    "count_users_in_depts",
    "get_dept_by_code",
    "get_dept_by_id",
    "get_position_by_code",
    "get_position_by_id",
    "insert_dept",
    "insert_position",
    "list_depts",
    "list_position_ids_by_codes",
    "list_positions",
    "list_subtree_depts",
    "update_dept_fields",
]
