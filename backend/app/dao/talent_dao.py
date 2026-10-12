"""人才档案 / 字段解析 / 变更留痕 DAO（TBL-11/16/22，CU-14~17）。

🔴 每个查询首位恒为 ``tenant_id``（CR-9 / 铁律 1）；``talent_change_log`` 为**只追加**表，
DAO 只导出 insert/list/count（无 update/delete 可调 —— 与审计日志同一套三层结构保证）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, update

from app.models.enums import TalentDataStatus
from app.models.talent import (
    TalentBase,
    TalentChangeLog,
    TalentParseField,
    TalentProjectExp,
    TalentTagRel,
    TalentWorkExp,
)

__all__ = [
    "count_change_logs",
    "count_fields_by_status",
    "count_talents",
    "count_talents_by_task",
    "get_talent",
    "get_talent_any_state",
    "get_talent_by_user",
    "insert_change_log",
    "insert_parse_field",
    "insert_parse_fields",
    "insert_talent",
    "list_all_change_logs_asc",
    "list_change_logs",
    "list_field_completeness",
    "list_fields_by_talent",
    "list_pending_fields",
    "list_talent_names_by_ids",
    "list_talents",
    "list_talents_by_task",
    "list_project_exps",
    "list_project_exps_any_state",
    "list_work_exps",
    "list_work_exps_any_state",
    "insert_project_exp",
    "insert_project_exps",
    "insert_work_exp",
    "insert_work_exps",
    "replace_project_exps",
    "replace_work_exps",
    "set_talent_data_status",
    "soft_delete_project_exps",
    "soft_delete_work_exps",
    "update_parse_field_review",
    "update_talent_fields",
]


# ============================================================================
# TBL-11 talent_base
# ============================================================================
def insert_talent(db: Any, talent: TalentBase) -> TalentBase:
    db.add(talent)
    db.flush()
    return talent


def get_talent(db: Any, tenant_id: int, talent_id: int) -> TalentBase | None:
    stmt = (
        select(TalentBase)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.id == talent_id,
            TalentBase.is_deleted == 0,
        )
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_talent_any_state(db: Any, tenant_id: int, talent_id: int) -> TalentBase | None:
    """取档案**含软删**（不滤 ``is_deleted``），仅限「合并回滚」这类需要还原被软删从档的场景。

    🔴 用途单一：API-61 回滚要把 ``data_status=2`` 的从档还原为有效，
    常规 ``get_talent`` 因 ``is_deleted=1`` 会返回 None。仍严格带 ``tenant_id``（铁律 2），
    且**不**暴露给普通查询路径（避免别处「忘记软删过滤」）。
    """
    stmt = (
        select(TalentBase)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.id == talent_id,
        )
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_talent_by_user(db: Any, tenant_id: int, user_id: int) -> TalentBase | None:
    """按关联用户取**本人档案**（CU-22 API-71~79 的入口定位）。

    🔴 为什么本人视角必须走 ``user_id`` 关联、而不是接受前端传 ``talent_id``：
    API-71「查阅本人档案」若以入参 ``talent_id`` 定位，传一个别人的 ID 就能穿透 ——
    查阅权会直接退化成任意档案读取。故入口是「当前登录用户 → 他的档案」，
    调用方（``PrivacyService``）不提供任何按 ID 查阅本人的旁路。

    ⚠️ 一个用户可能关联多份档案（重复导入 / 合并前的历史行），取值优先级：
    **有效（``data_status=1``）优先 → 再按 ``id`` 倒序取最新**。
    ❌ 不报「找到多份」的错：那会把「数据没整理干净」变成「本人查不了自己的档案」，
    对一个权利入口而言是不可接受的可用性损失（存疑档案由 HR 在档案侧合并，
    不在这里拦）。已软删 / 已合并的档案不参与定位。
    """
    stmt = (
        select(TalentBase)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.user_id == int(user_id),
            TalentBase.is_deleted == 0,
        )
        # ``(data_status == 1)`` 为真排在前面（MySQL 布尔表达式降序 = 真在前）
        .order_by((TalentBase.data_status == int(TalentDataStatus.ACTIVE)).desc(), TalentBase.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def list_talents_by_task(db: Any, tenant_id: int, parse_task_id: int) -> list[TalentBase]:
    stmt = (
        select(TalentBase)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.parse_task_id == parse_task_id,
            TalentBase.is_deleted == 0,
        )
        .order_by(TalentBase.id)
    )
    return list(db.execute(stmt).scalars().all())


def count_talents_by_task(db: Any, tenant_id: int, parse_task_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(TalentBase)
        .where(
            TalentBase.tenant_id == tenant_id,
            TalentBase.parse_task_id == parse_task_id,
            TalentBase.is_deleted == 0,
        )
    )
    return int(db.execute(stmt).scalar_one() or 0)


# ============================================================================
# TBL-12/TBL-13 履历明细
# ============================================================================
def insert_work_exp(db: Any, exp: TalentWorkExp) -> TalentWorkExp:
    db.add(exp)
    db.flush()
    return exp


def insert_work_exps(db: Any, exps: Iterable[TalentWorkExp]) -> int:
    items = list(exps)
    if not items:
        return 0
    db.add_all(items)
    db.flush()
    return len(items)


def insert_project_exp(db: Any, exp: TalentProjectExp) -> TalentProjectExp:
    db.add(exp)
    db.flush()
    return exp


def insert_project_exps(db: Any, exps: Iterable[TalentProjectExp]) -> int:
    items = list(exps)
    if not items:
        return 0
    db.add_all(items)
    db.flush()
    return len(items)


def list_work_exps(db: Any, tenant_id: int, talent_id: int) -> list[TalentWorkExp]:
    """List visible work rows in their deterministic sequence order."""
    stmt = (
        select(TalentWorkExp)
        .where(
            TalentWorkExp.tenant_id == int(tenant_id),
            TalentWorkExp.talent_id == int(talent_id),
            TalentWorkExp.is_deleted == 0,
        )
        .order_by(TalentWorkExp.seq_no.asc(), TalentWorkExp.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_work_exps_any_state(db: Any, tenant_id: int, talent_id: int) -> list[TalentWorkExp]:
    """List work rows including soft-deleted history for merge rollback only."""
    stmt = (
        select(TalentWorkExp)
        .where(
            TalentWorkExp.tenant_id == int(tenant_id),
            TalentWorkExp.talent_id == int(talent_id),
        )
        .order_by(TalentWorkExp.seq_no.asc(), TalentWorkExp.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_project_exps(db: Any, tenant_id: int, talent_id: int) -> list[TalentProjectExp]:
    """List visible project rows in their deterministic sequence order."""
    stmt = (
        select(TalentProjectExp)
        .where(
            TalentProjectExp.tenant_id == int(tenant_id),
            TalentProjectExp.talent_id == int(talent_id),
            TalentProjectExp.is_deleted == 0,
        )
        .order_by(TalentProjectExp.seq_no.asc(), TalentProjectExp.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_project_exps_any_state(db: Any, tenant_id: int, talent_id: int) -> list[TalentProjectExp]:
    """List project rows including soft-deleted history for merge rollback only."""
    stmt = (
        select(TalentProjectExp)
        .where(
            TalentProjectExp.tenant_id == int(tenant_id),
            TalentProjectExp.talent_id == int(talent_id),
        )
        .order_by(TalentProjectExp.seq_no.asc(), TalentProjectExp.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def soft_delete_work_exps(
    db: Any, tenant_id: int, talent_id: int, *, deleted_at: datetime | None = None
) -> int:
    """Soft-delete all visible work rows for one tenant-owned talent."""
    stmt = (
        update(TalentWorkExp)
        .where(
            TalentWorkExp.tenant_id == int(tenant_id),
            TalentWorkExp.talent_id == int(talent_id),
            TalentWorkExp.is_deleted == 0,
        )
        .values(is_deleted=1, deleted_at=deleted_at or datetime.now())
    )
    return int(db.execute(stmt).rowcount or 0)


def soft_delete_project_exps(
    db: Any, tenant_id: int, talent_id: int, *, deleted_at: datetime | None = None
) -> int:
    """Soft-delete all visible project rows for one tenant-owned talent."""
    stmt = (
        update(TalentProjectExp)
        .where(
            TalentProjectExp.tenant_id == int(tenant_id),
            TalentProjectExp.talent_id == int(talent_id),
            TalentProjectExp.is_deleted == 0,
        )
        .values(is_deleted=1, deleted_at=deleted_at or datetime.now())
    )
    return int(db.execute(stmt).rowcount or 0)


def replace_work_exps(
    db: Any, tenant_id: int, talent_id: int, exps: Iterable[TalentWorkExp]
) -> int:
    """Replace current work rows without committing the caller's transaction."""
    soft_delete_work_exps(db, tenant_id, talent_id)
    items = list(exps)
    for exp in items:
        exp.tenant_id = int(tenant_id)
        exp.talent_id = int(talent_id)
        exp.is_deleted = 0
        exp.deleted_at = None
    return insert_work_exps(db, items)


def replace_project_exps(
    db: Any, tenant_id: int, talent_id: int, exps: Iterable[TalentProjectExp]
) -> int:
    """Replace current project rows without committing the caller's transaction."""
    soft_delete_project_exps(db, tenant_id, talent_id)
    items = list(exps)
    for exp in items:
        exp.tenant_id = int(tenant_id)
        exp.talent_id = int(talent_id)
        exp.is_deleted = 0
        exp.deleted_at = None
    return insert_project_exps(db, items)


def set_talent_data_status(db: Any, talent: TalentBase, new_status: int) -> None:
    """流转档案状态并自增版本号（F-3.10：任何状态变更都是一次版本推进）。"""
    talent.data_status = int(new_status)
    talent.version_no = int(talent.version_no) + 1
    db.flush()


# ============================================================================
# TBL-16 talent_parse_field
# ============================================================================
def insert_parse_fields(db: Any, fields: Iterable[TalentParseField]) -> int:
    items = list(fields)
    db.add_all(items)
    db.flush()
    return len(items)


def insert_parse_field(db: Any, field: TalentParseField) -> TalentParseField:
    db.add(field)
    db.flush()
    return field


def get_field(db: Any, tenant_id: int, field_id: int) -> TalentParseField | None:
    stmt = select(TalentParseField).where(
        TalentParseField.tenant_id == tenant_id,
        TalentParseField.id == field_id,
        TalentParseField.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_fields_by_talent(db: Any, tenant_id: int, talent_id: int) -> list[TalentParseField]:
    stmt = (
        select(TalentParseField)
        .where(
            TalentParseField.tenant_id == tenant_id,
            TalentParseField.talent_id == talent_id,
            TalentParseField.is_deleted == 0,
        )
        .order_by(TalentParseField.id)
    )
    return list(db.execute(stmt).scalars().all())


def list_pending_fields(
    db: Any,
    tenant_id: int,
    *,
    task_id: int | None = None,
    impact_levels: Sequence[str] | None = None,
    field_codes: Sequence[str] | None = None,
    confidence_max: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[tuple[TalentParseField, TalentBase]]:
    """复核队列（API-44）：待复核字段联表档案（取姓名）。按置信度升序 = 最可疑优先。"""
    conditions = [
        TalentParseField.tenant_id == tenant_id,
        TalentParseField.review_status == 0,
        TalentParseField.is_deleted == 0,
    ]
    if task_id is not None:
        conditions.append(TalentBase.parse_task_id == task_id)
    if impact_levels:
        conditions.append(TalentParseField.impact_level.in_([s.upper() for s in impact_levels]))
    if field_codes:
        conditions.append(TalentParseField.field_name.in_(list(field_codes)))
    if confidence_max is not None:
        conditions.append(TalentParseField.confidence <= float(confidence_max))
    stmt = (
        select(TalentParseField, TalentBase)
        .join(TalentBase, TalentParseField.talent_id == TalentBase.id)
        .where(*conditions)
        # MySQL 不支持 NULLS FIRST/LAST 语法；ASC 时 NULL 天然排最前，直接按 confidence 升序
        .order_by(TalentParseField.confidence.asc(), TalentParseField.id)
        .limit(int(limit))
        .offset(int(offset))
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def count_pending_fields(
    db: Any,
    tenant_id: int,
    *,
    task_id: int | None = None,
    impact_levels: Sequence[str] | None = None,
    field_codes: Sequence[str] | None = None,
    confidence_max: float | None = None,
) -> int:
    conditions = [
        TalentParseField.tenant_id == tenant_id,
        TalentParseField.review_status == 0,
        TalentParseField.is_deleted == 0,
    ]
    if task_id is not None:
        conditions.append(TalentBase.parse_task_id == task_id)
    if impact_levels:
        conditions.append(TalentParseField.impact_level.in_([s.upper() for s in impact_levels]))
    if field_codes:
        conditions.append(TalentParseField.field_name.in_(list(field_codes)))
    if confidence_max is not None:
        conditions.append(TalentParseField.confidence <= float(confidence_max))
    stmt = (
        select(func.count())
        .select_from(TalentParseField)
        .join(TalentBase, TalentParseField.talent_id == TalentBase.id)
        .where(*conditions)
    )
    return int(db.execute(stmt).scalar_one() or 0)


def count_fields_by_status(
    db: Any, tenant_id: int, *, task_id: int | None = None
) -> dict[int, int]:
    """按复核状态分组计数（API-38 / API-49 共用）。"""
    conditions = [TalentParseField.tenant_id == tenant_id, TalentParseField.is_deleted == 0]
    if task_id is not None:
        conditions.append(TalentBase.parse_task_id == task_id)
    stmt = (
        select(TalentParseField.review_status, func.count())
        .join(TalentBase, TalentParseField.talent_id == TalentBase.id)
        .where(*conditions)
        .group_by(TalentParseField.review_status)
    )
    return {int(k): int(v) for k, v in db.execute(stmt).all()}


def list_field_completeness(
    db: Any, tenant_id: int, *, task_id: int | None = None
) -> list[tuple[str, int, int]]:
    """字段完整率（API-41）：``(field_name, 有值份数, 总份数)``。

    ⚠️ 只统计**解析成功**的档案字段行（字段行只来自成功入库的档案，
    失败文件没有档案，天然不进分母 —— 完整率的分母是「成功份数」，不是「上传份数」）。
    """
    conditions = [TalentParseField.tenant_id == tenant_id, TalentParseField.is_deleted == 0]
    if task_id is not None:
        conditions.append(TalentBase.parse_task_id == task_id)
    filled = func.sum(case((TalentParseField.parsed_value.isnot(None), 1), else_=0))
    stmt = (
        select(TalentParseField.field_name, filled, func.count())
        .select_from(TalentParseField)
        .join(TalentBase, TalentParseField.talent_id == TalentBase.id)
        .where(*conditions)
        .group_by(TalentParseField.field_name)
        .order_by(TalentParseField.field_name)
    )
    return [(str(r[0]), int(r[1]), int(r[2])) for r in db.execute(stmt).all()]


def update_parse_field_review(
    db: Any,
    tenant_id: int,
    field_id: int,
    *,
    new_status: int,
    parsed_value: str | None = None,
    confidence: float | None = None,
    reviewed_by: int | None,
    reviewed_at: datetime,
) -> int:
    """条件更新复核状态（compare-and-set：仅当当前仍为 ``0 待复核`` 时生效）。

    :param confidence: 人工修正（EDIT）时传 1.0 —— 与解析流水线的人工修正口径一致
    :return: 影响行数 —— ``0`` = 已被他人处理（并发冲突，API-46 的 ``conflicted[]``）
    """
    values: dict[str, object] = {
        "review_status": int(new_status),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }
    if parsed_value is not None:
        values["parsed_value"] = parsed_value
    if confidence is not None:
        values["confidence"] = round(float(confidence), 3)
    stmt = (
        update(TalentParseField)
        .where(
            TalentParseField.tenant_id == tenant_id,
            TalentParseField.id == field_id,
            TalentParseField.review_status == 0,
            TalentParseField.is_deleted == 0,
        )
        .values(**values)
    )
    result = db.execute(stmt)
    return int(result.rowcount or 0)


# ============================================================================
# TBL-22 talent_change_log（只追加）
# ============================================================================
def insert_change_log(db: Any, log: TalentChangeLog) -> TalentChangeLog:
    db.add(log)
    db.flush()
    return log


def list_change_logs(
    db: Any,
    tenant_id: int,
    *,
    talent_id: int | None = None,
    field_name: str | None = None,
    change_source: int | None = None,
    operator_id: int | None = None,
    task_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TalentChangeLog]:
    """变更留痕列表（CU-16 的留痕回读 + CU-22 的 API-67/68/70 共用）。

    排序 ``(created_at DESC, id DESC)`` 构成**全序**：
    留痕表按同一次业务动作可能写入多行（同秒），只按 ``created_at`` 排序时
    分页会出现「同一条记录在第 1 页和第 2 页各出现一次」（CU-08 审计日志同源问题）。
    """
    conditions = _change_log_conditions(
        tenant_id,
        talent_id=talent_id,
        field_name=field_name,
        change_source=change_source,
        operator_id=operator_id,
        task_id=task_id,
        start_time=start_time,
        end_time=end_time,
    )
    stmt = (
        select(TalentChangeLog)
        .where(*conditions)
        .order_by(TalentChangeLog.created_at.desc(), TalentChangeLog.id.desc())
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def _change_log_conditions(
    tenant_id: int,
    *,
    talent_id: int | None = None,
    field_name: str | None = None,
    change_source: int | None = None,
    operator_id: int | None = None,
    task_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[Any]:
    """list / count 共用的条件（同源才能保证「翻页翻到越界」不会发生）。"""
    conditions: list[Any] = [TalentChangeLog.tenant_id == tenant_id]  # 🔴 恒为第一条件
    if talent_id is not None:
        conditions.append(TalentChangeLog.talent_id == int(talent_id))
    if field_name is not None:
        conditions.append(TalentChangeLog.field_name == field_name)
    if change_source is not None:
        conditions.append(TalentChangeLog.change_source == int(change_source))
    if operator_id is not None:
        conditions.append(TalentChangeLog.operator_id == int(operator_id))
    if task_id is not None:
        conditions.append(TalentChangeLog.task_id == int(task_id))
    if start_time is not None:
        conditions.append(TalentChangeLog.created_at >= start_time)
    if end_time is not None:
        conditions.append(TalentChangeLog.created_at <= end_time)
    return conditions


def count_change_logs(
    db: Any,
    tenant_id: int,
    *,
    talent_id: int | None = None,
    field_name: str | None = None,
    change_source: int | None = None,
    operator_id: int | None = None,
    task_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> int:
    conditions = _change_log_conditions(
        tenant_id,
        talent_id=talent_id,
        field_name=field_name,
        change_source=change_source,
        operator_id=operator_id,
        task_id=task_id,
        start_time=start_time,
        end_time=end_time,
    )
    stmt = select(func.count()).select_from(TalentChangeLog).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


def list_all_change_logs_asc(
    db: Any, tenant_id: int, talent_id: int, *, limit: int = 5000
) -> list[TalentChangeLog]:
    """取某档案的**全部**留痕并**升序**返回（API-69 时间点回溯的输入）。

    🔴 为什么必须有上限：回溯要把「T 之后的所有变更」倒着叠加，
    若某档案的留痕异常膨胀（例如重解析循环写了几十万行），
    无上限查询会直接把接口拖垮。超限时**宁可少算也不要 500** ——
    调用方据 ``len(rows) == limit`` 在出参里给出 ``history_truncated=true``。
    """
    stmt = (
        select(TalentChangeLog)
        .where(
            TalentChangeLog.tenant_id == tenant_id,
            TalentChangeLog.talent_id == int(talent_id),
        )
        .order_by(TalentChangeLog.created_at.asc(), TalentChangeLog.id.asc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


def list_talent_names_by_ids(db: Any, tenant_id: int, talent_ids: Sequence[int]) -> dict[int, str]:
    """批量取「档案 ID → 姓名」（**不过滤软删**，同审计日志的 ``list_usernames_by_ids`` 口径）。

    🔴 为什么不过滤 ``is_deleted``：变更链里必然会引用**已删除 / 已合并**的档案
    （删除本身就是一次变更）。若过滤，那些行的姓名会显示成空，
    而「谁被删了」恰恰是留痕最需要说清的事。
    """
    ids = [int(i) for i in talent_ids]
    if not ids:
        return {}
    stmt = select(TalentBase.id, TalentBase.name).where(
        TalentBase.tenant_id == tenant_id,
        TalentBase.id.in_(ids),
    )
    return {int(r[0]): str(r[1]) for r in db.execute(stmt).all()}


# ============================================================================
# TBL-11 档案列表与更新（CU-29 · API-30 / API-32 / API-35）
# ============================================================================
def _talent_conditions(
    tenant_id: int,
    *,
    scope_clause: Any,
    keyword: str | None = None,
    org_ids: Sequence[int] | None = None,
    education_code: str | None = None,
    work_years_min: int | None = None,
    work_years_max: int | None = None,
    tag_codes: Sequence[str] | None = None,
    data_status: int | Sequence[int] | None = None,
) -> list[Any]:
    """``list`` / ``count`` **共用**的条件构造。

    同源是刻意的：两处各写一遍条件，早晚会分叉 → 分页 ``total`` 与实际行数对不上，
    表现为「翻到最后一页是空的」或「有记录永远翻不到」（CU-08 审计日志已踩过）。

    🔴 前三个条件恒存在，且**都不接受前端传参**：

    1. ``tenant_id``（铁律 1）；
    2. ``is_deleted = 0``；
    3. ``scope_clause`` —— 由 :meth:`~app.core.tenant.DataScopeFilter.mysql_condition`
       产出的**行级数据域**条件。

    :param scope_clause: 行级条件。❌ 本函数**不自行拼装**权限条件 ——
        权限必须只有一个来源，否则「列表过滤了、导出忘了加」这类越权
        会在第二个调用点冒出来（CR-11 的立意）。
    """
    conditions: list[Any] = [
        TalentBase.tenant_id == int(tenant_id),  # 🔴 恒为第一条件
        TalentBase.is_deleted == 0,
        scope_clause,
    ]
    if keyword:
        # 接口原文：「姓名模糊匹配（**不做全文检索**；语义检索走 API-62）」
        conditions.append(TalentBase.name.like(f"%{keyword}%"))
    if org_ids:
        conditions.append(TalentBase.org_id.in_([int(i) for i in org_ids]))
    if education_code:
        conditions.append(TalentBase.education_level == str(education_code))
    if work_years_min is not None:
        conditions.append(TalentBase.work_years >= int(work_years_min))
    if work_years_max is not None:
        conditions.append(TalentBase.work_years <= int(work_years_max))
    if data_status is not None:
        # 🔴 批次 23：默认口径从「单值」扩为「集合」（ADM/HR 未传 = (0 待复核, 1 有效)），
        #    故此处同时接受单值与序列 —— 序列走 ``IN``，单值保持 ``==``（旧行为不变）。
        if isinstance(data_status, (list, tuple)):
            conditions.append(
                TalentBase.data_status.in_([int(v) for v in data_status])
            )
        else:
            conditions.append(TalentBase.data_status == int(data_status))
    if tag_codes:
        # 🔴 用**子查询**而不是 JOIN：JOIN 会因「一个档案命中多个标签」产生重复行，
        #    让分页 total 虚高（用户在第 2 页看到同一批人）。子查询天然去重。
        conditions.append(
            TalentBase.id.in_(
                select(TalentTagRel.talent_id).where(
                    TalentTagRel.tenant_id == int(tenant_id),
                    TalentTagRel.is_deleted == 0,
                    TalentTagRel.tag_code.in_([str(c) for c in tag_codes]),
                )
            )
        )
    return conditions


def list_talents(
    db: Any,
    tenant_id: int,
    *,
    scope_clause: Any,
    sort_field: str = "updated_at",
    sort_desc: bool = True,
    limit: int = 20,
    offset: int = 0,
    **filters: Any,
) -> list[TalentBase]:
    """API-30 档案列表（分页）。

    🔴 排序**必须补 ``id`` 作次级键**：``updated_at`` 在批量导入时会大量相同
    （同一秒入库），只按它排序时 MySQL 的行序不确定 → 同一条档案可能在第 1 页
    与第 2 页各出现一次。补 ``id`` 后构成全序，分页才稳定。

    :param sort_field: 已由 ``schemas.talent_master.parse_sort`` 过白名单；
        此处仍用 ``getattr`` 兜底，避免调用方绕过白名单造成 500。
    """
    column = getattr(TalentBase, sort_field, TalentBase.updated_at)
    order = column.desc() if sort_desc else column.asc()
    conditions = _talent_conditions(tenant_id, scope_clause=scope_clause, **filters)
    stmt = (
        select(TalentBase)
        .where(*conditions)
        .order_by(order, TalentBase.id.desc())
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def count_talents(db: Any, tenant_id: int, *, scope_clause: Any, **filters: Any) -> int:
    """与 :func:`list_talents` **同条件**的计数（条件构造函数共用）。"""
    conditions = _talent_conditions(tenant_id, scope_clause=scope_clause, **filters)
    stmt = select(func.count()).select_from(TalentBase).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


def update_talent_fields(db: Any, talent: TalentBase, values: dict[str, Any]) -> TalentBase:
    """更新档案字段并**推进版本号**（F-3.10：任何变更都是一次版本推进）。

    ⚠️ 只 ``flush`` 不 ``commit`` —— 事务边界由 Service 决定（与
    :func:`set_talent_data_status` 同口径），否则「MySQL 已提交、留痕还没写」
    会在中途失败时留下无法解释的历史。
    """
    for key, value in values.items():
        setattr(talent, key, value)
    talent.version_no = int(talent.version_no) + 1
    db.flush()
    return talent
