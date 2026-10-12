"""审计日志 DAO（TBL-28，接口 API-28 / API-29）。

🔴 本模块存在的核心意义：**没有 update、没有 delete**
====================================================
「只追加」在模型层靠「没有 ``is_deleted`` / ``updated_at`` 两列」体现，
但真正拦得住人的是**本层** —— 「没有函数可以调」远强于「有函数但请别调」。
因此：

- 本模块**只导出** :func:`insert_log` / :func:`list_logs` / :func:`count_logs` 三个函数；
- ``__all__`` 显式声明，且 ``tests/test_audit.py`` 有一条断言钉死
  「本模块不存在名字里含 update/delete/remove/soft 的可调用对象」，
  防止将来有人「顺手」加一个 ``update_log``。

⚠️ 若确实需要改审计内容（几乎不可能，正确做法是**再追加一条纠正记录**），
必须先改模型、再改迁移、并更新该测试 —— 让这件事「必须被评审」，而不是随手可做。

与其它 DAO 的两处**刻意的不同**
==============================
1. **没有 ``is_deleted`` 过滤** —— 表里根本没有这一列（只追加表不存在「删除态」）。
   这不是漏写，若照抄 :mod:`app.dao.dict_dao` 的模板反而会引用不存在的列而报错。
2. **没有 ``update_*`` 函数** —— 见上。

保留的共性：**每个查询都带 ``tenant_id`` 且放条件首位**（数据库 §0 原则 5），
排序**确定**（``created_at DESC, id DESC``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog

__all__ = ["count_logs", "insert_log", "list_logs"]


# ============================================================================
# 写入（只增）
# ============================================================================
def insert_log(db: Session, log: AuditLog) -> AuditLog:
    """插入一条审计日志并 flush（**由调用方负责 commit**）。

    ⚠️ 一般不要直接调本函数 —— 走 :func:`app.services.audit_service.AuditService.record`，
    那里统一处理「JSON 序列化 + 敏感字段脱敏 + 超长截断 + 独立事务 + fail-open」。
    直接调本函数意味着这些保护全部缺失。
    """
    db.add(log)
    db.flush()
    return log


# ============================================================================
# 读取（只查）
# ============================================================================
def _apply_filters(
    stmt: Select[tuple[AuditLog]],
    tenant_id: int,
    *,
    user_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    keyword: str | None = None,
) -> Select[tuple[AuditLog]]:
    """把查询条件挂到语句上（list 与 count **共用**，避免两处条件漂移）。

    🔴 条件顺序固定为「先 ``tenant_id``」：本表的联合索引是
    ``(tenant_id, created_at)`` / ``(tenant_id, user_id)``，
    ``tenant_id`` 不在最左就会退化成全表（且分区表上等于放弃裁剪）。
    """
    stmt = stmt.where(AuditLog.tenant_id == int(tenant_id))

    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == int(user_id))
    if action:
        # 精确匹配：``action`` 是**代码里写死的枚举式取值**（login / dict_item_update …），
        # 不是用户输入的自由文本。用 LIKE 会让「login」顺带匹配「login_fail」，
        # 排查时看到一堆不相关记录。
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == str(target_id))
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at <= end)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        # 模糊搜索只开在「不会误伤索引」的列上：target_id / trace_id 是本表仅有的
        # 面向「追查某次具体操作」的字段（action 已有精确筛选，不重复参与模糊）。
        stmt = stmt.where(or_(AuditLog.target_id.like(pattern), AuditLog.trace_id.like(pattern)))
    return stmt


def list_logs(
    db: Session,
    tenant_id: int,
    *,
    user_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    keyword: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AuditLog]:
    """按条件取审计日志，**时间倒序**（最新的操作在最前）。

    :param limit: ``None`` 表示不限制（供导出使用，调用方须自己保证已设上限）。

    排序为什么是 ``created_at DESC, id DESC`` 而不是只按 ``created_at``：
    同一秒内的多条记录（批量操作很常见）若只按时间排，MySQL 返回顺序**不确定**，
    翻页时会出现「第 2 页重复出现第 1 页的记录」。补一个 ``id`` 才构成全序。
    """
    stmt = select(AuditLog)
    stmt = _apply_filters(
        stmt,
        tenant_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        start=start,
        end=end,
        keyword=keyword,
    )
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_logs(
    db: Session,
    tenant_id: int,
    *,
    user_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    keyword: str | None = None,
) -> int:
    """取符合条件的总条数（分页用）。

    ⚠️ 条件与 :func:`list_logs` **必须逐字一致**（故共用 :func:`_apply_filters`）：
    两者不一致的典型症状是「total 说有 500 条，翻到第 3 页就空了」。
    """
    stmt = select(func.count()).select_from(AuditLog)
    stmt = _apply_filters(
        stmt,
        tenant_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        start=start,
        end=end,
        keyword=keyword,
    )
    return int(db.execute(stmt).scalar_one())
