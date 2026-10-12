"""导入任务 / 导入文件 DAO（TBL-20/21，CU-14，API-36~43）。

🔴 每个查询首位恒为 ``tenant_id``（CR-9）。任务计数（success/fail）由**逐文件状态聚合**，
不维护独立计数列 —— 聚合是唯一事实，写两份数据迟早对不上。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.models.enums import ImportFileParseStatus, ImportTaskStatus
from app.models.talent import ImportFile, ImportTask

__all__ = [
    "count_by_status",
    "count_files",
    "find_task_by_idempotent_key",
    "get_file",
    "get_task",
    "get_task_by_no",
    "insert_file",
    "insert_task",
    "list_failed_files",
    "list_files",
    "list_failure_reasons",
    "list_tasks",
    "mark_file",
    "set_task_status",
]


# ============================================================================
# TBL-20 import_task
# ============================================================================
def insert_task(db: Any, task: ImportTask) -> ImportTask:
    db.add(task)
    db.flush()
    return task


def get_task(db: Any, tenant_id: int, task_id: int) -> ImportTask | None:
    stmt = select(ImportTask).where(
        ImportTask.tenant_id == tenant_id,
        ImportTask.id == task_id,
        ImportTask.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_task_by_no(db: Any, tenant_id: int, task_no: str) -> ImportTask | None:
    stmt = select(ImportTask).where(
        ImportTask.tenant_id == tenant_id, ImportTask.task_no == task_no, ImportTask.is_deleted == 0
    )
    return db.execute(stmt).scalar_one_or_none()


def find_task_by_idempotent_key(db: Any, tenant_id: int, key: str) -> ImportTask | None:
    stmt = select(ImportTask).where(
        ImportTask.tenant_id == tenant_id,
        ImportTask.idempotent_key == key,
        ImportTask.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_tasks(
    db: Any,
    tenant_id: int,
    *,
    status: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ImportTask]:
    conditions = [ImportTask.tenant_id == tenant_id, ImportTask.is_deleted == 0]
    if status is not None:
        conditions.append(ImportTask.status == int(status))
    stmt = (
        select(ImportTask)
        .where(*conditions)
        .order_by(ImportTask.created_at.desc(), ImportTask.id.desc())
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def count_tasks(db: Any, tenant_id: int, *, status: int | None = None) -> int:
    conditions = [ImportTask.tenant_id == tenant_id, ImportTask.is_deleted == 0]
    if status is not None:
        conditions.append(ImportTask.status == int(status))
    stmt = select(func.count()).select_from(ImportTask).where(*conditions)
    return int(db.execute(stmt).scalar_one() or 0)


def list_stale_parsing_tasks(db: Any, *, limit: int = 100) -> list[ImportTask]:
    """跨租户列出「解析中(1)」任务 —— **仅供启动自愈**（:meth:`ImportService.resume_interrupted`）。

    🔴 这是 CR-9「每查询必带 ``tenant_id``」的**唯一显式例外**，理由要写透：
    进程死亡（kill / 崩溃 / 断电）会把任务永远留在「解析中」，而自愈发生在**启动期**，
    此时没有请求作用域、没有租户上下文 —— 它不做任何面向用户的数据返回，
    只取 ``(task_id, tenant_id, created_by)`` 三元组去重拉线程，不存在越权泄漏面。
    用户请求路径的查询**一律不得**使用本函数。
    """
    stmt = (
        select(ImportTask)
        .where(ImportTask.status == 1, ImportTask.is_deleted == 0)
        .order_by(ImportTask.id.asc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


def set_task_status(db: Any, task: ImportTask, new_status: int) -> None:
    task.status = int(new_status)
    db.flush()


def count_by_status(db: Any, tenant_id: int, task_id: int) -> dict[int, int]:
    """逐文件解析状态聚合（``成功数/失败数/进度`` 的唯一事实来源）。"""
    stmt = (
        select(ImportFile.parse_status, func.count())
        .where(
            ImportFile.tenant_id == tenant_id,
            ImportFile.task_id == task_id,
            ImportFile.is_deleted == 0,
        )
        .group_by(ImportFile.parse_status)
    )
    return {int(k): int(v) for k, v in db.execute(stmt).all()}


# ============================================================================
# TBL-21 import_file
# ============================================================================
def insert_file(db: Any, file: ImportFile) -> ImportFile:
    db.add(file)
    db.flush()
    return file


def get_file(db: Any, tenant_id: int, file_id: int) -> ImportFile | None:
    stmt = select(ImportFile).where(
        ImportFile.tenant_id == tenant_id, ImportFile.id == file_id, ImportFile.is_deleted == 0
    )
    return db.execute(stmt).scalar_one_or_none()


def count_files(db: Any, tenant_id: int, task_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(ImportFile)
        .where(ImportFile.tenant_id == tenant_id, ImportFile.task_id == task_id, ImportFile.is_deleted == 0)
    )
    return int(db.execute(stmt).scalar_one() or 0)


def list_files(
    db: Any,
    tenant_id: int,
    task_id: int,
    *,
    parse_status: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ImportFile]:
    conditions = [
        ImportFile.tenant_id == tenant_id,
        ImportFile.task_id == task_id,
        ImportFile.is_deleted == 0,
    ]
    if parse_status is not None:
        conditions.append(ImportFile.parse_status == int(parse_status))
    stmt = (
        select(ImportFile)
        .where(*conditions)
        .order_by(ImportFile.resume_id)
        .limit(int(limit))
        .offset(int(offset))
    )
    return list(db.execute(stmt).scalars().all())


def list_failed_files(db: Any, tenant_id: int, task_id: int) -> list[ImportFile]:
    stmt = (
        select(ImportFile)
        .where(
            ImportFile.tenant_id == tenant_id,
            ImportFile.task_id == task_id,
            ImportFile.parse_status == int(ImportFileParseStatus.FAILED),
            ImportFile.is_deleted == 0,
        )
        .order_by(ImportFile.resume_id)
    )
    return list(db.execute(stmt).scalars().all())


def list_failure_reasons(db: Any, tenant_id: int, task_id: int) -> list[tuple[str, int]]:
    """失败原因分组计数（API-41 ``failure_reasons``）。"""
    stmt = (
        select(ImportFile.parse_error, func.count())
        .where(
            ImportFile.tenant_id == tenant_id,
            ImportFile.task_id == task_id,
            ImportFile.parse_status == int(ImportFileParseStatus.FAILED),
            ImportFile.is_deleted == 0,
        )
        .group_by(ImportFile.parse_error)
        .order_by(func.count().desc())
    )
    return [(str(k) if k is not None else "UNKNOWN", int(v)) for k, v in db.execute(stmt).all()]


def mark_file(
    db: Any,
    file: ImportFile,
    *,
    parse_status: int,
    parse_error: str | None = None,
    talent_id: int | None = None,
    agent_task_id: int | None = None,
    ocr_used: bool | None = None,
) -> None:
    """落单文件解析结果（逐文件提交，单文件失败不阻塞整批）。"""
    file.parse_status = int(parse_status)
    file.parse_error = parse_error
    if talent_id is not None:
        file.talent_id = int(talent_id)
    if agent_task_id is not None:
        file.agent_task_id = int(agent_task_id)
    if ocr_used is not None:
        file.ocr_used = 1 if ocr_used else 0
    db.flush()


def next_task_no(db: Any, tenant_id: int, *, day_prefix: str) -> str:
    """``IMPyyyymmddNNN``：按当天已有任务数 +1 生成（租户内连续）。

    ⚠️ 任务号**不是**唯一性防线（``uk_tenant_task_no`` 兜底并发），
    并发撞号时由唯一键触发 40901 后重试一次（调用方处理）。
    """
    stmt = (
        select(func.count())
        .select_from(ImportTask)
        .where(
            ImportTask.tenant_id == tenant_id,
            ImportTask.task_no.like(f"IMP{day_prefix}%"),
            ImportTask.is_deleted == 0,
        )
    )
    seq = int(db.execute(stmt).scalar_one() or 0) + 1
    return f"IMP{day_prefix}{seq:03d}"
