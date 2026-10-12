"""查重对 DAO（TBL-17，CU-19，API-57~61）。

🔴 每个查询首位恒为 ``tenant_id``（CR-9）。``uk_tenant_pair`` 是「同一对只一行」的
库级兜底；服务层「先查后插」只用于组装 40901 的错误体。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.models.dup_pair import TalentDupPair
from app.models.enums import TalentDataStatus
from app.models.talent import TalentBase

__all__ = [
    "count_pairs",
    "get_pair",
    "get_pair_by_ids",
    "insert_pair",
    "list_dedup_candidates",
    "list_pairs",
    "update_pair_fields",
]


# ============================================================================
# TBL-17 talent_dup_pair
# ============================================================================
def insert_pair(db: Any, pair: TalentDupPair) -> TalentDupPair:
    db.add(pair)
    db.flush()
    return pair


def get_pair(db: Any, tenant_id: int, pair_id: int) -> TalentDupPair | None:
    stmt = select(TalentDupPair).where(
        TalentDupPair.tenant_id == tenant_id,
        TalentDupPair.id == pair_id,
        TalentDupPair.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_pair_by_ids(db: Any, tenant_id: int, talent_a: int, talent_b: int) -> TalentDupPair | None:
    """按 (a, b) 查对（调用方保证 ``a < b``）。"""
    a, b = sorted((int(talent_a), int(talent_b)))
    stmt = select(TalentDupPair).where(
        TalentDupPair.tenant_id == tenant_id,
        TalentDupPair.talent_id_a == a,
        TalentDupPair.talent_id_b == b,
        TalentDupPair.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def update_pair_fields(db: Any, pair: TalentDupPair, fields: dict[str, object]) -> TalentDupPair:
    for key, value in fields.items():
        setattr(pair, key, value)
    db.flush()
    return pair


def list_pairs(
    db: Any,
    tenant_id: int,
    *,
    status: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TalentDupPair]:
    conditions = [TalentDupPair.tenant_id == tenant_id, TalentDupPair.is_deleted == 0]
    if status is not None:
        conditions.append(TalentDupPair.status == int(status))
    stmt = (
        select(TalentDupPair)
        .where(*conditions)
        # 相似度降序（最可疑的排最前）；同分按 a 升序稳定
        .order_by(TalentDupPair.similarity.desc(), TalentDupPair.talent_id_a.asc())
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def count_pairs(db: Any, tenant_id: int, *, status: int | None = None) -> int:
    conditions = [TalentDupPair.tenant_id == tenant_id, TalentDupPair.is_deleted == 0]
    if status is not None:
        conditions.append(TalentDupPair.status == int(status))
    stmt = select(func.count()).select_from(TalentDupPair).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


# ============================================================================
# 精确键候选扫描（供服务层分组）
# ============================================================================
def list_dedup_candidates(db: Any, tenant_id: int) -> list[tuple[int, str | None, str | None]]:
    """返回「有效」档案的 ``(id, phone_hash, id_card_hash)``，供精确键分组。

    ⚠️ 只扫 ``data_status=1 有效``：待复核 / 已合并 / 已删除不进查重
    （否则会把「还没复核完的草稿」或「已合并走的从档」重新判成重复）。
    """
    stmt = (
        select(TalentBase.id, TalentBase.phone_hash, TalentBase.id_card_hash)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.data_status == int(TalentDataStatus.ACTIVE),
            TalentBase.is_deleted == 0,
        )
        .order_by(TalentBase.id)
    )
    return [(int(r[0]), r[1], r[2]) for r in db.execute(stmt).all()]
