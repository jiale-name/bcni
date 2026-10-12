"""受控标签词表 / 人才-标签关系 DAO（TBL-18/19，CU-17，API-50~56）。

🔴 每个查询首位恒为 ``tenant_id``（CR-9）。唯一性由 ``uk_tenant_tag_code`` /
``uk_tenant_talent_tag`` 数据库兜底，服务层先查后插仅用于组装 40901 的错误体。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.models.enums import TagDictStatus, TagSource
from app.models.talent import TalentTagDict, TalentTagRel

__all__ = [
    "count_enabled_tags",
    "count_tags",
    "find_tag_suggestion",
    "get_tag",
    "get_tag_by_code",
    "get_tag_by_name",
    "insert_tag",
    "insert_tag_rel",
    "list_enabled_tag_entries",
    "list_rels_by_talent_ids",
    "list_tags",
    "list_talent_tags",
    "names_by_codes",
    "replace_talent_tags",
    "update_tag_fields",
]


# ============================================================================
# TBL-18 talent_tag_dict
# ============================================================================
def insert_tag(db: Any, tag: TalentTagDict) -> TalentTagDict:
    db.add(tag)
    db.flush()
    return tag


def get_tag(db: Any, tenant_id: int, tag_id: int) -> TalentTagDict | None:
    stmt = select(TalentTagDict).where(
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.id == tag_id,
        TalentTagDict.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_tag_by_code(db: Any, tenant_id: int, tag_code: str) -> TalentTagDict | None:
    stmt = select(TalentTagDict).where(
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.tag_code == tag_code,
        TalentTagDict.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_tag_by_name(db: Any, tenant_id: int, tag_name: str) -> TalentTagDict | None:
    stmt = select(TalentTagDict).where(
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.tag_name == tag_name,
        TalentTagDict.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def find_tag_suggestion(db: Any, tenant_id: int, tag_name: str, category_l3: str | None) -> TalentTagDict | None:
    """AI 重复建议的判定：同名 + 同三级分类 + 仍在待审核 → 返回已有行（防刷屏）。"""
    conditions = [
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.tag_name == tag_name,
        TalentTagDict.status == int(TagDictStatus.PENDING_AUDIT),
        TalentTagDict.is_deleted == 0,
    ]
    if category_l3:
        conditions.append(TalentTagDict.category_l3 == category_l3)
    stmt = select(TalentTagDict).where(*conditions)
    return db.execute(stmt).scalar_one_or_none()


def update_tag_fields(db: Any, tag: TalentTagDict, fields: dict[str, object]) -> TalentTagDict:
    for key, value in fields.items():
        setattr(tag, key, value)
    db.flush()
    return tag


def list_tags(
    db: Any,
    tenant_id: int,
    *,
    status: int | None = None,
    category_l1: str | None = None,
    category_l2: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TalentTagDict]:
    conditions = [TalentTagDict.tenant_id == tenant_id, TalentTagDict.is_deleted == 0]
    if status is not None:
        conditions.append(TalentTagDict.status == int(status))
    if category_l1:
        conditions.append(TalentTagDict.category_l1 == category_l1)
    if category_l2:
        conditions.append(TalentTagDict.category_l2 == category_l2)
    if keyword:
        conditions.append(TalentTagDict.tag_name.like(f"%{keyword}%"))
    stmt = (
        select(TalentTagDict)
        .where(*conditions)
        .order_by(TalentTagDict.category_l1, TalentTagDict.category_l2, TalentTagDict.category_l3, TalentTagDict.id)
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def count_tags(
    db: Any,
    tenant_id: int,
    *,
    status: int | None = None,
    keyword: str | None = None,
) -> int:
    conditions = [TalentTagDict.tenant_id == tenant_id, TalentTagDict.is_deleted == 0]
    if status is not None:
        conditions.append(TalentTagDict.status == int(status))
    if keyword:
        conditions.append(TalentTagDict.tag_name.like(f"%{keyword}%"))
    stmt = select(func.count()).select_from(TalentTagDict).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


def list_enabled_tag_entries(db: Any, tenant_id: int) -> dict[str, dict[str, object]]:
    """全部**启用**项 → ``{tag_code: {"name", "synonyms", "category_l1"~"l3"}}``。

    供 :class:`app.core.tagdict.TagDictValidator.normalize_to_dict` 使用
    （量级 10²，一次拉全无压力；AI 选词必须查这份快照，而不是信 AI 原文）。
    """
    stmt = select(TalentTagDict).where(
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.status == int(TagDictStatus.ENABLED),
        TalentTagDict.is_deleted == 0,
    )
    out: dict[str, dict[str, object]] = {}
    for tag in db.execute(stmt).scalars().all():
        out[str(tag.tag_code)] = {
            "name": tag.tag_name,
            "synonyms": list(tag.synonyms or []),
            "category_l1": tag.category_l1,
            "category_l2": tag.category_l2,
            "category_l3": tag.category_l3,
        }
    return out


def count_enabled_tags(db: Any, tenant_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(TalentTagDict)
        .where(
            TalentTagDict.tenant_id == tenant_id,
            TalentTagDict.status == int(TagDictStatus.ENABLED),
            TalentTagDict.is_deleted == 0,
        )
    )
    return int(db.execute(stmt).scalar_one() or 0)


# ============================================================================
# TBL-19 talent_tag_rel
# ============================================================================
def list_talent_tags(db: Any, tenant_id: int, talent_id: int) -> list[TalentTagRel]:
    stmt = (
        select(TalentTagRel)
        .where(
            TalentTagRel.tenant_id == tenant_id,
            TalentTagRel.talent_id == talent_id,
            TalentTagRel.is_deleted == 0,
        )
        .order_by(TalentTagRel.id)
    )
    return list(db.execute(stmt).scalars().all())


def list_rels_by_talent_ids(
    db: Any, tenant_id: int, talent_ids: Sequence[int]
) -> dict[int, list[TalentTagRel]]:
    """批量取「档案 ID → 标签关系」映射（CU-29 · API-30 的 ``top_tags`` 用）。

    🔴 为什么必须批量：列表页每页 20~100 行，逐行调 :func:`list_talent_tags`
    就是 20~100 次数据库往返（N+1）。档案列表是**最高频入口**，
    N+1 足以把它拖成慢接口（一次 20 行 → 21 条 SQL）。
    """
    ids = [int(i) for i in talent_ids]
    if not ids:
        return {}
    stmt = (
        select(TalentTagRel)
        .where(
            TalentTagRel.tenant_id == tenant_id,
            TalentTagRel.is_deleted == 0,
            TalentTagRel.talent_id.in_(ids),
        )
        .order_by(TalentTagRel.talent_id, TalentTagRel.id)
    )
    out: dict[int, list[TalentTagRel]] = {}
    for rel in db.execute(stmt).scalars().all():
        out.setdefault(int(rel.talent_id), []).append(rel)
    return out


def names_by_codes(db: Any, tenant_id: int, codes: Sequence[str]) -> dict[str, str]:
    """按标签编码批量取名称（**不限启用状态**，CU-29 · API-30）。

    为什么不复用 :func:`list_enabled_tag_entries`：那份只含 ``status=1`` 的启用项
    （它是给 AI 选词用的「可选集合」，必须只含可选项）。而档案上挂的标签可能是
    **后来被停用**的 —— 列表页若不显示它的名字，用户会看到一个空白标签，
    看起来像数据坏了。停用只表示「不能再被新打标」，不表示「历史标错了」。
    """
    wanted = [str(c) for c in codes]
    if not wanted:
        return {}
    stmt = select(TalentTagDict.tag_code, TalentTagDict.tag_name).where(
        TalentTagDict.tenant_id == tenant_id,
        TalentTagDict.tag_code.in_(wanted),
        TalentTagDict.is_deleted == 0,
    )
    return {str(r[0]): str(r[1]) for r in db.execute(stmt).all()}


def insert_tag_rel(db: Any, rel: TalentTagRel) -> TalentTagRel:
    db.add(rel)
    db.flush()
    return rel


def replace_talent_tags(
    db: Any, tenant_id: int, talent_id: int, entries: list[dict[str, object]]
) -> tuple[list[str], list[str]]:
    """**全量替换**语义（同角色-权限绑定）：软删旧行 + 插入新行。

    :return: ``(added_codes, removed_codes)`` —— 供变更留痕 before/after 快照
    """
    existing = list_talent_tags(db, tenant_id, talent_id)
    before = [str(r.tag_code) for r in existing]
    for row in existing:
        row.is_deleted = 1
        row.deleted_at = func.now()
    for entry in entries:
        db.add(
            TalentTagRel(
                tenant_id=tenant_id,
                talent_id=talent_id,
                tag_code=str(entry["tag_code"]),
                confidence=entry.get("confidence"),
                source=int(entry.get("source", TagSource.MANUAL)),
            )
        )
    db.flush()
    after = [str(e["tag_code"]) for e in entries]
    added = [c for c in after if c not in before]
    removed = [c for c in before if c not in after]
    return added, removed
