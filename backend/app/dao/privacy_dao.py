"""个人权利请求 DAO（**TBL-30** `privacy_request`，CU-22，F-3.11 / SM-3 / API-72~80）。

🔴 每个查询首位恒为 ``tenant_id``（CR-9 / 铁律 1）。
🔴 **状态流转一律走条件更新（compare-and-set）**，不提供「无条件 set status」的函数 ——
「审批删除」是跨库（MySQL + Milvus）的不可逆写操作，两个 ADM 同时点同意时必须
**有一个人的影响行数为 0**，否则会出现「同一份档案被删两次、留两条删除留痕」。
把 CAS 做进 DAO（而不是在 Service 里先查后改）是为了让「防并发双审」不依赖调用方的自觉。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from app.models.privacy import PrivacyRequest

__all__ = [
    "cas_resolve",
    "cas_start_processing",
    "count_requests",
    "count_requests_today",
    "get_request",
    "get_request_by_idem",
    "insert_request",
    "list_requests",
    "mark_downloaded",
    "update_request_fields",
]


# ============================================================================
# 写入
# ============================================================================
def insert_request(db: Any, request: PrivacyRequest) -> PrivacyRequest:
    db.add(request)
    db.flush()
    return request


def update_request_fields(db: Any, request: PrivacyRequest, fields: dict[str, Any]) -> PrivacyRequest:
    """按字段更新（用于通知留痕 / 下载计数等**非状态**字段）。"""
    for key, value in fields.items():
        setattr(request, key, value)
    db.flush()
    return request


def cas_start_processing(
    db: Any,
    tenant_id: int,
    request_id: int,
    *,
    processed_by: int,
    idempotent_key: str | None = None,
) -> int:
    """CAS：``0 待处理`` → ``3 处理中``（SM-3 的内部临时态）。

    :return: 影响行数 —— ``0`` = 已被他人抢先（并发双审），调用方据此报 40902
    """
    values: dict[str, Any] = {"status": 3, "processed_by": processed_by}
    if idempotent_key:
        values["idempotent_key"] = idempotent_key
    stmt = (
        update(PrivacyRequest)
        .where(
            PrivacyRequest.tenant_id == tenant_id,
            PrivacyRequest.id == request_id,
            PrivacyRequest.status == 0,
            PrivacyRequest.is_deleted == 0,
        )
        .values(**values)
    )
    return int(db.execute(stmt).rowcount or 0)


def cas_resolve(
    db: Any,
    tenant_id: int,
    request_id: int,
    *,
    from_status: int,
    to_status: int,
    processed_by: int,
    processed_at: datetime,
    reject_reason: str | None = None,
    notify_message: str | None = None,
    notified_at: datetime | None = None,
    idempotent_key: str | None = None,
) -> int:
    """CAS：``from_status`` → 终态（``1 已通过`` / ``2 已驳回``）。

    调用方传 ``from_status=0``（更正 / 导出，无跨库步骤）或 ``3``（删除审批，已置处理中）。

    :return: 影响行数 —— ``0`` = 状态已被改变，调用方**必须先回查当前状态**再决定报错码
    """
    values: dict[str, Any] = {
        "status": int(to_status),
        "processed_by": processed_by,
        "processed_at": processed_at,
        "reject_reason": reject_reason,
    }
    if notify_message is not None:
        values["notify_message"] = notify_message
        values["notified_at"] = notified_at or processed_at
    if idempotent_key:
        values["idempotent_key"] = idempotent_key
    stmt = (
        update(PrivacyRequest)
        .where(
            PrivacyRequest.tenant_id == tenant_id,
            PrivacyRequest.id == request_id,
            PrivacyRequest.status == int(from_status),
            PrivacyRequest.is_deleted == 0,
        )
        .values(**values)
    )
    return int(db.execute(stmt).rowcount or 0)


def mark_downloaded(
    db: Any, tenant_id: int, request_id: int, *, now: datetime, export_format: str = "json"
) -> int:
    """下载留痕（API-79）：计数 +1、记录最近下载时间。

    ⚠️ 用 ``download_count = download_count + 1`` 的**原子自增**而不是
    「读出来 +1 再写回」：后者在两次下载并发时会把计数丢掉一次，
    而下载次数正是「数据包被取走几次」的合规凭据。
    """
    stmt = (
        update(PrivacyRequest)
        .where(
            PrivacyRequest.tenant_id == tenant_id,
            PrivacyRequest.id == request_id,
            PrivacyRequest.is_deleted == 0,
        )
        .values(
            download_count=PrivacyRequest.download_count + 1,
            last_download_at=now,
            export_format=export_format,
        )
    )
    return int(db.execute(stmt).rowcount or 0)


# ============================================================================
# 读取
# ============================================================================
def get_request(db: Any, tenant_id: int, request_id: int) -> PrivacyRequest | None:
    stmt = select(PrivacyRequest).where(
        PrivacyRequest.tenant_id == tenant_id,
        PrivacyRequest.id == request_id,
        PrivacyRequest.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_request_by_idem(db: Any, tenant_id: int, idempotent_key: str) -> PrivacyRequest | None:
    """按幂等键回查（API-77 强制幂等：「先查键、再看状态」，见 agent_service 同源约定）。

    ⚠️ 空串直接返回 ``None``：前端「未填」时常发空串，若当成真实键，
    所有未填的请求会共用同一个键 —— 第二个请求会拿到第一个的结果。
    """
    key = (idempotent_key or "").strip()
    if not key:
        return None
    stmt = select(PrivacyRequest).where(
        PrivacyRequest.tenant_id == tenant_id,
        PrivacyRequest.idempotent_key == key,
        PrivacyRequest.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def _apply_filters(
    conditions: list[Any],
    tenant_id: int,
    *,
    request_type: int | None = None,
    status: int | None = None,
    talent_id: int | None = None,
    applicant_user_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[Any]:
    """list / count 共用的筛选条件（**分页与总数必须同源**，否则会出现「翻到第 3 页越界」）。"""
    conditions.append(PrivacyRequest.tenant_id == tenant_id)
    conditions.append(PrivacyRequest.is_deleted == 0)
    if request_type is not None:
        conditions.append(PrivacyRequest.request_type == int(request_type))
    if status is not None:
        conditions.append(PrivacyRequest.status == int(status))
    if talent_id is not None:
        conditions.append(PrivacyRequest.talent_id == int(talent_id))
    if applicant_user_id is not None:
        conditions.append(PrivacyRequest.applicant_user_id == int(applicant_user_id))
    if start_time is not None:
        conditions.append(PrivacyRequest.created_at >= start_time)
    if end_time is not None:
        conditions.append(PrivacyRequest.created_at <= end_time)
    return conditions


def list_requests(
    db: Any,
    tenant_id: int,
    *,
    request_type: int | None = None,
    status: int | None = None,
    talent_id: int | None = None,
    applicant_user_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    pending_first: bool = True,
    limit: int = 20,
    offset: int = 0,
) -> list[PrivacyRequest]:
    """列表。默认 **待处理优先**（``created_at`` 升序内层）——
    处理人的视线应该先落在「还没出结果的单子」上；台账排序若纯按时间倒序，
    积压的老单会被新单盖住，这正是「超时统计很难看」的成因。
    """
    conditions = _apply_filters(
        [],
        tenant_id,
        request_type=request_type,
        status=status,
        talent_id=talent_id,
        applicant_user_id=applicant_user_id,
        start_time=start_time,
        end_time=end_time,
    )
    stmt = select(PrivacyRequest).where(*conditions)
    if pending_first:
        stmt = stmt.order_by(
            (PrivacyRequest.status == 0).desc(),
            PrivacyRequest.created_at.desc(),
            PrivacyRequest.id.desc(),
        )
    else:
        stmt = stmt.order_by(PrivacyRequest.created_at.desc(), PrivacyRequest.id.desc())
    stmt = stmt.limit(int(limit)).offset(int(offset))
    return list(db.execute(stmt).scalars().all())


def count_requests(
    db: Any,
    tenant_id: int,
    *,
    request_type: int | None = None,
    status: int | None = None,
    talent_id: int | None = None,
    applicant_user_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> int:
    conditions = _apply_filters(
        [],
        tenant_id,
        request_type=request_type,
        status=status,
        talent_id=talent_id,
        applicant_user_id=applicant_user_id,
        start_time=start_time,
        end_time=end_time,
    )
    stmt = select(func.count()).select_from(PrivacyRequest).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


def count_requests_today(db: Any, tenant_id: int, *, request_no_prefix: str) -> int:
    """当日已提交的请求数（请求号序号 = 该值 + 1）。"""
    stmt = (
        select(func.count())
        .select_from(PrivacyRequest)
        .where(
            PrivacyRequest.tenant_id == tenant_id,
            PrivacyRequest.request_no.startswith(request_no_prefix),
        )
    )
    return int(db.execute(stmt).scalar_one() or 0)
