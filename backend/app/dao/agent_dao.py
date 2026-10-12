"""Agent DAO（TBL-25 ``agent_task`` / TBL-26 ``agent_step`` / TBL-27 ``agent_interrupt``）。

🔴 本模块的两个关键设计点
======================

① **``agent_step`` 没有任何 update / delete 函数**
-----------------------------------------------
它是**执行轨迹**（只追加），与 :mod:`app.dao.audit_dao` 同源。
「跑过没跑过、这一步产出什么」必须可追问，故结构上不给改删入口。

② **人工待办的「先到先得」靠条件 UPDATE，不靠「先查后改」**
------------------------------------------------------
API-94 的核心并发场景是「两个人同时点通过」。若写成::

    row = get_interrupt(...)          # 两人都读到 status=0
    if row.status != 0: raise 40902   # 都通过了检查
    update_interrupt(status=1)        # 都写了 —— 后写者静默覆盖先写者

则**两次请求都会成功**，其中一次的处理意见被静默丢弃，
而审计与 ``decided_by`` 只会留下一个人 —— 这是最难查的一类问题。

故 :func:`resolve_interrupt` 把状态判定放进 ``WHERE`` 里（compare-and-set）::

    UPDATE agent_interrupt SET status=?, decided_by=?, ... WHERE id=? AND status=0

**受影响行数 0 就说明「这次操作不是第一个」**，服务层据此区分 40902 / 40903。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentInterrupt, AgentStep, AgentTask

__all__ = [
    "count_interrupts",
    "count_steps",
    "count_tasks",
    "find_interrupt_by_idempotent_key",
    "find_task_by_idempotent_key",
    "get_interrupt",
    "get_task",
    "insert_interrupt",
    "insert_step",
    "insert_task",
    "list_interrupts",
    "list_steps",
    "list_tasks",
    "next_step_index",
    "resolve_interrupt",
    "update_task",
]


# ============================================================================
# TBL-25 Agent 任务
# ============================================================================
def get_task(db: Session, tenant_id: int, task_id: int) -> AgentTask | None:
    stmt = select(AgentTask).where(
        AgentTask.tenant_id == int(tenant_id),  # 🔴 租户条件恒为首位
        AgentTask.id == int(task_id),
        AgentTask.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def find_task_by_idempotent_key(
    db: Session, tenant_id: int, idempotent_key: str
) -> AgentTask | None:
    """按幂等键取**既有任务**（F-A0.6 / 接口 §1.6）。

    ⚠️ ``idempotent_key`` 为空串时直接返回 ``None``：MySQL 唯一索引对 ``NULL`` 不生效
    （多行 ``NULL`` 可共存），但空串**会**参与唯一性 —— 若允许空串入库，
    第二条无幂等键的任务就会撞 1062。故这里把空串等价于 ``NULL``（不查、不命中）。
    """
    key = (idempotent_key or "").strip()
    if not key:
        return None
    stmt = select(AgentTask).where(
        AgentTask.tenant_id == int(tenant_id),
        AgentTask.idempotent_key == key,
        AgentTask.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_tasks(
    db: Session,
    tenant_id: int,
    *,
    task_type: str | None = None,
    status: int | None = None,
    biz_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AgentTask]:
    """任务列表。排序 ``id DESC``（雪花 ID 单调递增 ⇒ 等价于「创建时间倒序」）。"""
    stmt = select(AgentTask).where(
        AgentTask.tenant_id == int(tenant_id), AgentTask.is_deleted == 0
    )
    if task_type:
        stmt = stmt.where(AgentTask.task_type == task_type)
    if status is not None:
        stmt = stmt.where(AgentTask.status == int(status))
    if biz_id is not None:
        stmt = stmt.where(AgentTask.biz_id == int(biz_id))
    stmt = stmt.order_by(AgentTask.id.desc())
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_tasks(
    db: Session,
    tenant_id: int,
    *,
    task_type: str | None = None,
    status: int | None = None,
    biz_id: int | None = None,
) -> int:
    stmt = select(func.count()).select_from(AgentTask).where(
        AgentTask.tenant_id == int(tenant_id), AgentTask.is_deleted == 0
    )
    if task_type:
        stmt = stmt.where(AgentTask.task_type == task_type)
    if status is not None:
        stmt = stmt.where(AgentTask.status == int(status))
    if biz_id is not None:
        stmt = stmt.where(AgentTask.biz_id == int(biz_id))
    return int(db.execute(stmt).scalar_one())


def insert_task(db: Session, task: AgentTask) -> AgentTask:
    db.add(task)
    db.flush()
    return task


def update_task(db: Session, tenant_id: int, task_id: int, **fields: Any) -> int:
    """更新任务（状态 / 图状态 / 当前节点 / 重试次数 / 幂等快照）。"""
    if not fields:
        return 0
    stmt = (
        AgentTask.__table__.update()
        .where(
            AgentTask.tenant_id == int(tenant_id),
            AgentTask.id == int(task_id),
            AgentTask.is_deleted == 0,
        )
        .values(**fields)
    )
    return int(db.execute(stmt).rowcount or 0)


# ============================================================================
# TBL-26 Agent 步骤（只追加）
# ============================================================================
def next_step_index(db: Session, tenant_id: int, task_id: int) -> int:
    """取下一个步骤序号（从 1 起）。

    ⚠️ 这是**读后写**，并发跑同一任务时会撞号 —— 但本项目的执行模型是
    「单任务串行推进」（同一次任务不会有两个 worker 同时跑一步），
    故不加锁。若将来引入并行分支，须改为「按 ``(task_id, node_name)`` 各自计数」
    或从锁表取号。
    """
    stmt = select(func.max(AgentStep.step_index)).where(
        AgentStep.tenant_id == int(tenant_id), AgentStep.task_id == int(task_id)
    )
    return int(db.execute(stmt).scalar_one_or_none() or 0) + 1


def insert_step(db: Session, step: AgentStep) -> AgentStep:
    db.add(step)
    db.flush()
    return step


def list_steps(
    db: Session, tenant_id: int, task_id: int, *, limit: int | None = None, offset: int = 0
) -> list[AgentStep]:
    """步骤回放（API-92）：按 ``step_index`` **升序** —— 回放要按执行顺序看。

    排序补 ``id ASC`` 构成全序（同号步骤不应出现，但一旦出现，顺序必须确定）。
    """
    stmt = (
        select(AgentStep)
        .where(AgentStep.tenant_id == int(tenant_id), AgentStep.task_id == int(task_id))
        .order_by(AgentStep.step_index.asc(), AgentStep.id.asc())
    )
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_steps(db: Session, tenant_id: int, task_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(AgentStep)
        .where(AgentStep.tenant_id == int(tenant_id), AgentStep.task_id == int(task_id))
    )
    return int(db.execute(stmt).scalar_one())


# ============================================================================
# TBL-27 人工介入待办
# ============================================================================
def get_interrupt(
    db: Session, tenant_id: int, interrupt_id: int, *, refresh: bool = False
) -> AgentInterrupt | None:
    """取一条待办（跨租户 / 已软删 → ``None``）。

    🔴 ``refresh=True`` 会**强制用库里的值覆盖 Identity Map 里的旧对象**。
    为什么需要它：本项目引擎配了 ``expire_on_commit=False``，而 API-94 的状态流转
    走的是**Core 条件更新**（不经过 ORM），因此 commit 之后那条在内存里的实体
    **仍是 ``status=0``**；此时再 ``select`` 同一个主键，SQLAlchemy 默认直接返回
    内存里的旧对象（**不重新填充字段**）—— 于是接口响应会告诉前端
    「还是 pending」，而库里其实已经是 approved。
    ❌ 这不是「缓存慢一拍」，而是**同一操作两种答案**：首次调用答 pending、
    40903 幂等回放答 approved（回放路径是新请求、新 Session，读到的是真值）。
    故凡是「更新后要原样回显」的地方都必须 ``refresh=True``。
    """
    stmt = select(AgentInterrupt).where(
        AgentInterrupt.tenant_id == int(tenant_id),
        AgentInterrupt.id == int(interrupt_id),
        AgentInterrupt.is_deleted == 0,
    )
    if refresh:
        stmt = stmt.execution_options(populate_existing=True)
    return db.execute(stmt).scalar_one_or_none()


def find_interrupt_by_idempotent_key(
    db: Session, tenant_id: int, idempotent_key: str
) -> AgentInterrupt | None:
    """按幂等键取已处理的待办（API-94 幂等回放）。空串等价 ``NULL``，见任务侧同函数说明。"""
    key = (idempotent_key or "").strip()
    if not key:
        return None
    stmt = select(AgentInterrupt).where(
        AgentInterrupt.tenant_id == int(tenant_id),
        AgentInterrupt.idempotent_key == key,
        AgentInterrupt.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def _apply_interrupt_filters(
    stmt: Select[tuple[AgentInterrupt]],
    tenant_id: int,
    *,
    status: int | None = None,
    task_id: int | None = None,
    interrupt_type: str | None = None,
    assignee_role: str | None = None,
) -> Select[tuple[AgentInterrupt]]:
    stmt = stmt.where(
        AgentInterrupt.tenant_id == int(tenant_id), AgentInterrupt.is_deleted == 0
    )
    if status is not None:
        stmt = stmt.where(AgentInterrupt.status == int(status))
    if task_id is not None:
        stmt = stmt.where(AgentInterrupt.task_id == int(task_id))
    if interrupt_type:
        stmt = stmt.where(AgentInterrupt.interrupt_type == interrupt_type)
    if assignee_role:
        stmt = stmt.where(AgentInterrupt.assignee_role == assignee_role)
    return stmt


def list_interrupts(
    db: Session,
    tenant_id: int,
    *,
    status: int | None = None,
    task_id: int | None = None,
    interrupt_type: str | None = None,
    assignee_role: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AgentInterrupt]:
    """待办列表（API-93）。默认按 ``id DESC``（最新的待办在最前）。"""
    stmt = _apply_interrupt_filters(
        select(AgentInterrupt),
        tenant_id,
        status=status,
        task_id=task_id,
        interrupt_type=interrupt_type,
        assignee_role=assignee_role,
    )
    stmt = stmt.order_by(AgentInterrupt.id.desc())
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_interrupts(
    db: Session,
    tenant_id: int,
    *,
    status: int | None = None,
    task_id: int | None = None,
    interrupt_type: str | None = None,
    assignee_role: str | None = None,
) -> int:
    stmt = _apply_interrupt_filters(
        select(func.count()).select_from(AgentInterrupt),
        tenant_id,
        status=status,
        task_id=task_id,
        interrupt_type=interrupt_type,
        assignee_role=assignee_role,
    )
    return int(db.execute(stmt).scalar_one())


def insert_interrupt(db: Session, interrupt: AgentInterrupt) -> AgentInterrupt:
    db.add(interrupt)
    db.flush()
    return interrupt


def resolve_interrupt(
    db: Session,
    tenant_id: int,
    interrupt_id: int,
    *,
    decision: int,
    decided_by: int,
    decided_at: datetime,
    comment: str | None = None,
    idempotent_key: str | None = None,
) -> int:
    """**条件更新**待办状态（compare-and-set），返回受影响行数。

    🔴 ``WHERE ... AND status = 0`` 是本函数存在的全部理由（见模块 docstring ②）：
    只有「把它从 pending 改走」的那**一个**请求会拿到 ``rowcount == 1``。
    并发的其余请求拿到 0，服务层再区分「别人处理过」（40902）
    与「同一人重复提交」（40903）。

    :param decision: ``1`` = approved / ``2`` = rejected（见 ``AgentInterruptStatus``）
    :return: 受影响行数（``1`` = 本次操作生效；``0`` = 已被处理）
    """
    tbl = AgentInterrupt.__table__
    stmt = (
        tbl.update()
        .where(
            tbl.c.tenant_id == int(tenant_id),
            tbl.c.id == int(interrupt_id),
            tbl.c.is_deleted == 0,
            tbl.c.status == 0,  # 🔴 只有 pending 可流转
        )
        .values(
            status=int(decision),
            decided_by=int(decided_by),
            decided_at=decided_at,
            comment=comment,
            idempotent_key=(idempotent_key or None),
        )
    )
    return int(db.execute(stmt).rowcount or 0)
