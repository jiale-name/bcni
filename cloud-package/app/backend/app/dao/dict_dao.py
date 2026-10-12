"""数据字典 DAO（TBL-09 / TBL-10，接口 API-23 ~ API-27）。

分层约定（架构 §3.3）：**DAO 只做纯数据访问** —— 不含业务判定
（能否改 ``item_code``、``parent_code`` 是否成环、停用是否幂等），
这些全在 :mod:`app.services.dict_service`。

🔴 三条不可省略的约束（与 :mod:`app.dao.org_dao` 同源）
------------------------------------------------------
1. **每个查询都带 ``tenant_id``**，且放在条件的**第一个**位置，
   以命中「``tenant_id`` 为最左列」的联合索引（数据库 §0 原则 5）；
2. **软删过滤默认打开** —— 字典**从不软删**（只停用，见 :mod:`app.models.dict`），
   但本层仍显式写 ``is_deleted == 0``，以便将来加真删除时不至于静默漏过滤；
3. **排序必须确定** —— 类型按 ``type_code``，项按 ``(sort_no, item_code)``；
   排序不确定会让同一份字典两次请求顺序不同，前端下拉框的选项会「跳位」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.dict import DictItem, DictType


# ============================================================================
# TBL-09 字典类型
# ============================================================================
def list_types(
    db: Session,
    tenant_id: int,
    *,
    status: int | None = None,
    keyword: str | None = None,
) -> list[DictType]:
    """取本租户字典类型，按 ``type_code`` 升序。

    :param status: ``None`` 取全部（**默认**）；``1`` 仅启用。默认全量而不是仅启用，
        因为管理页要能看到停用类型，否则停用后它就「消失」且无法恢复。
    :param keyword: 按 ``type_code`` / ``type_name`` 模糊匹配。
    """
    stmt: Select[tuple[DictType]] = select(DictType).where(
        DictType.tenant_id == tenant_id,
        DictType.is_deleted == 0,
    )
    if status is not None:
        stmt = stmt.where(DictType.status == int(status))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(DictType.type_code.like(pattern), DictType.type_name.like(pattern)))
    stmt = stmt.order_by(DictType.type_code.asc())
    return list(db.execute(stmt).scalars().all())


def get_type_by_code(db: Session, tenant_id: int, type_code: str) -> DictType | None:
    """按类型编码取**未删除**类型（40401 判定与创建字典项的前置校验）。"""
    stmt = select(DictType).where(
        DictType.tenant_id == tenant_id,
        DictType.type_code == type_code,
        DictType.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


class ItemCounts(NamedTuple):
    """某个字典类型的项计数（``total`` 含停用项，``enabled`` 仅 ``status=1``）。"""

    total: int
    enabled: int


def count_items_by_types(
    db: Session, tenant_id: int, type_codes: Sequence[str]
) -> dict[str, ItemCounts]:
    """**一次聚合**取多个类型的字典项计数，返回 ``{type_code: ItemCounts}``。

    为什么单独写一个聚合函数：API-23 的类型列表要带 ``item_count``。
    若在循环里逐类型 ``SELECT COUNT(*)``，10 个类型就是 10（甚至 20）次查询（典型 N+1）。
    这里用一条 ``GROUP BY type_code`` + 条件求和，**查询次数与类型数无关**。
    """
    codes = [c for c in type_codes if c]
    if not codes:
        return {}
    enabled_flag = case((DictItem.status == 1, 1), else_=0)
    stmt = (
        select(
            DictItem.type_code,
            func.count(DictItem.id),
            func.coalesce(func.sum(enabled_flag), 0),
        )
        .where(
            DictItem.tenant_id == tenant_id,
            DictItem.type_code.in_(codes),
            DictItem.is_deleted == 0,
        )
        .group_by(DictItem.type_code)
    )
    return {row[0]: ItemCounts(total=int(row[1]), enabled=int(row[2])) for row in db.execute(stmt).all()}


# ============================================================================
# TBL-10 字典项
# ============================================================================
def list_items(
    db: Session,
    tenant_id: int,
    type_code: str,
    *,
    status: int | None = None,
    parent_code: str | None = None,
    keyword: str | None = None,
) -> list[DictItem]:
    """取某类型的字典项，按 ``(sort_no, item_code)`` 升序。

    :param status: ``None`` 取全部（**默认**，含停用项）；``1`` 仅启用 —— 下拉框场景
        由调用方显式传 ``status=1``。默认全量的理由同 :func:`list_types`。
    :param parent_code: 仅取该父项下的子项（多级联动用）。
    """
    stmt: Select[tuple[DictItem]] = select(DictItem).where(
        DictItem.tenant_id == tenant_id,
        DictItem.type_code == type_code,
        DictItem.is_deleted == 0,
    )
    if status is not None:
        stmt = stmt.where(DictItem.status == int(status))
    if parent_code is not None:
        stmt = stmt.where(DictItem.parent_code == parent_code)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(DictItem.item_code.like(pattern), DictItem.item_name.like(pattern)))
    stmt = stmt.order_by(DictItem.sort_no.asc(), DictItem.item_code.asc())
    return list(db.execute(stmt).scalars().all())


def get_item_by_id(db: Session, tenant_id: int, item_id: int) -> DictItem | None:
    """按 ID 取**未删除**字典项（40401 判定的唯一入口）。"""
    stmt = select(DictItem).where(
        DictItem.tenant_id == tenant_id,
        DictItem.id == int(item_id),
        DictItem.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_item_by_code(
    db: Session, tenant_id: int, type_code: str, item_code: str
) -> DictItem | None:
    """按 ``(类型编码, 项编码)`` 取**未删除**字典项（唯一性校验 / 父项存在性校验）。"""
    stmt = select(DictItem).where(
        DictItem.tenant_id == tenant_id,
        DictItem.type_code == type_code,
        DictItem.item_code == item_code,
        DictItem.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_parent_child_pairs(
    db: Session, tenant_id: int, type_code: str
) -> list[tuple[str, str | None]]:
    """取该类型下全部 ``(item_code, parent_code)`` —— 供服务层做**环路检测**。

    只取两列（不拉实体），因为环路检测只需要父子关系，
    且该函数在每次写操作前都会被调用，避免把宽表拉进内存。
    """
    stmt = select(DictItem.item_code, DictItem.parent_code).where(
        DictItem.tenant_id == tenant_id,
        DictItem.type_code == type_code,
        DictItem.is_deleted == 0,
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def insert_item(db: Session, item: DictItem) -> DictItem:
    """插入字典项并 flush（拿到雪花 ID 与时间戳，**由调用方负责 commit**）。"""
    db.add(item)
    db.flush()
    return item


def update_item_fields(db: Session, item: DictItem, fields: Mapping[str, object]) -> DictItem:
    """按字段字典更新字典项并 flush（只更新传入的键，未传的字段保持原值）。"""
    for key, value in fields.items():
        setattr(item, key, value)
    db.flush()
    return item


def count_items_with_parent(db: Session, tenant_id: int, type_code: str, parent_code: str) -> int:
    """统计挂在某父项下的字典项数量（停用父项时的引用提示用）。"""
    stmt = select(func.count(DictItem.id)).where(
        DictItem.tenant_id == tenant_id,
        DictItem.type_code == type_code,
        DictItem.parent_code == parent_code,
        DictItem.is_deleted == 0,
    )
    return int(db.execute(stmt).scalar_one())


def any_item_in_type(db: Session, tenant_id: int, type_code: str) -> bool:
    """该类型下是否已存在字典项（供调用方判断，避免拉全量）。"""
    stmt = (
        select(DictItem.id)
        .where(
            DictItem.tenant_id == tenant_id,
            DictItem.type_code == type_code,
            DictItem.is_deleted == 0,
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def insert_items(db: Session, items: Iterable[DictItem]) -> int:
    """批量插入字典项（**仅供种子脚本使用**），返回插入条数。"""
    rows = list(items)
    if not rows:
        return 0
    db.add_all(rows)
    db.flush()
    return len(rows)
