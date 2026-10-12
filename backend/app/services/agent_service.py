"""Agent 任务与人工介入（CU-13，API-90 ~ API-95，F-A0.1 ~ F-A0.5）。

本模块是「图运行时」与「HTTP 契约」之间的编排层
============================================
:mod:`app.agents.graph` 是纯运行时（不碰 DB），:mod:`app.dao.agent_dao` 是纯 CRUD，
本模块负责把两者接起来，并承担三件**必须在服务层做**的事：

1. **任务级状态机**（``pending → running → waiting_human → success/failed``）；
2. **幂等**（F-A0.6）：``(tenant_id, idempotent_key)`` 先查后插；
3. **合规与并发**（API-94）：条件更新区分 40902 / 40903，写审计留痕。

🔴 API-94 的三种并发结局（必须分清，混了就是错）
==========================================
| 场景 | 码 | 语义 | 前端应做什么 |
| --- | --- | --- | --- |
| 首次提交 | ``0`` | 成功 | 正常继续 |
| **同一人**用**同一幂等键**重复提交 | ``40903`` | 幂等回放 | **当作成功**，用返回的首次结果 |
| **别人**已处理过（幂等键不同） | ``40902`` | 并发冲突 | 提示「已被 张敏 处理」，刷新后重试 |

❌ 把 40902 当成 40903 会让前端在「别人已驳回」的情况下显示「你的通过已生效」；
❌ 把 40903 当成 40902 会让网络重试变成用户可见的报错。

⚠️ 判定顺序是先看**幂等键是否已存在**，再看**状态是否已流转** ——
因为同一人的重试往往连状态都已流转（正是他自己改成非 pending 的），
若先看状态就会把它误判成「别人处理过」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.graph import GraphOutcome, resume_state, run_graph
from app.agents.registry import get_graph, successor_of
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import agent_dao, user_admin_dao
from app.exceptions import BizError, ConflictError, ErrorCode, NotFoundError
from app.models.agent import AgentInterrupt, AgentTask
from app.models.enums import AgentInterruptStatus, AgentTaskStatus
from app.schemas.agent import (
    DECISION_TO_STATUS,
    AgentInterruptOut,
    AgentStepOut,
    AgentTaskDetailOut,
    AgentTaskFilterRequest,
    AgentTaskOut,
    AgentTaskRetryOut,
    AgentTaskType,
    InterruptFilterRequest,
    InterruptResolveOut,
    InterruptResolveRequest,
    InterruptType,
)
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.services.audit_service import AuditService

logger = get_logger(__name__)

#: 可从 ``failed`` 重试的状态（``success`` / ``waiting_human`` 不允许重试）
RETRYABLE_STATUSES = frozenset({int(AgentTaskStatus.FAILED), int(AgentTaskStatus.PENDING)})


class AgentService:
    """Agent 任务查询与人工待办处理（API-90 ~ API-95）。"""

    # ------------------------------------------------------------------
    # API-90 任务列表
    # ------------------------------------------------------------------
    @classmethod
    def list_tasks(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: AgentTaskFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[AgentTaskOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = agent_dao.list_tasks(
            db,
            principal.tenant_id,
            task_type=filters.task_type,
            status=filters.status,
            biz_id=filters.biz_id,
            limit=size,
            offset=(current - 1) * size,
        )
        total = agent_dao.count_tasks(
            db,
            principal.tenant_id,
            task_type=filters.task_type,
            status=filters.status,
            biz_id=filters.biz_id,
        )
        return [cls.to_task_out(row) for row in rows], total, current, size

    # ------------------------------------------------------------------
    # API-91 任务详情
    # ------------------------------------------------------------------
    @classmethod
    def get_task_detail(
        cls, db: Session, principal: CurrentPrincipal, task_id: int
    ) -> AgentTaskDetailOut:
        task = cls._get_task(db, principal, task_id)
        state = _load_state(task)
        out = cls.to_task_out(task)
        return AgentTaskDetailOut(
            **out.model_dump(),
            idempotent_key=task.idempotent_key,
            graph_state=state or None,
            step_count=agent_dao.count_steps(db, principal.tenant_id, task_id),
        )

    # ------------------------------------------------------------------
    # API-92 步骤回放
    # ------------------------------------------------------------------
    @classmethod
    def list_steps(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[AgentStepOut], int, int, int]:
        """步骤回放。**先校验任务可见性**再取步骤 ——
        否则可以用一个跨租户的 ``task_id`` 直接读步骤（步骤表虽带 ``tenant_id``，
        但「任务本身不可见 ⇒ 步骤也不可见」这条语义要显式表达）。"""
        cls._get_task(db, principal, task_id)
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = agent_dao.list_steps(
            db, principal.tenant_id, task_id, limit=size, offset=(current - 1) * size
        )
        total = agent_dao.count_steps(db, principal.tenant_id, task_id)
        return [cls.to_step_out(row) for row in rows], total, current, size

    # ------------------------------------------------------------------
    # API-93 人工待办列表
    # ------------------------------------------------------------------
    @classmethod
    def list_interrupts(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: InterruptFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[AgentInterruptOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = agent_dao.list_interrupts(
            db,
            principal.tenant_id,
            status=filters.status,
            task_id=filters.task_id,
            interrupt_type=filters.interrupt_type,
            assignee_role=filters.assignee_role,
            limit=size,
            offset=(current - 1) * size,
        )
        total = agent_dao.count_interrupts(
            db,
            principal.tenant_id,
            status=filters.status,
            task_id=filters.task_id,
            interrupt_type=filters.interrupt_type,
            assignee_role=filters.assignee_role,
        )
        names = cls._decided_by_names(db, principal.tenant_id, rows)
        return (
            [cls.to_interrupt_out(row, names=names) for row in rows],
            total,
            current,
            size,
        )

    # ------------------------------------------------------------------
    # API-94 处理待办（核心：幂等 + 并发 + 恢复执行）
    # ------------------------------------------------------------------
    @classmethod
    def resolve_interrupt(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        interrupt_id: int,
        payload: InterruptResolveRequest,
        *,
        idempotent_key: str | None = None,
    ) -> InterruptResolveOut:
        """处理人工待办并**从 ``interrupt`` 点恢复执行**（接口 §3 API-94）。

        :raises NotFoundError: 待办不存在 / 已软删 → **40401**
        :raises BizError(40903): 同一幂等键已处理过 → **返回首次结果**
        :raises ConflictError(40902): 已被他人处理 → ``data`` 带处理人姓名与时间
        """
        # ---- 1. 幂等回放（**先于状态判定**，见模块 docstring）----
        if idempotent_key:
            replay = agent_dao.find_interrupt_by_idempotent_key(
                db, principal.tenant_id, idempotent_key
            )
            if replay is not None:
                # 同一幂等键 → 返回**首次结果**，code=40903（接口 §1.6）
                raise BizError(
                    ErrorCode.IDEMPOTENT_REPLAY,
                    "该待办已处理，返回首次结果",
                    cls._replay_data(db, principal, replay, idempotent_key),
                )

        interrupt = agent_dao.get_interrupt(db, principal.tenant_id, interrupt_id)
        if interrupt is None:
            raise NotFoundError("待办不存在")

        decision_status = DECISION_TO_STATUS[payload.decision]
        now = datetime.now()

        # ---- 2. 条件更新（compare-and-set）----
        affected = agent_dao.resolve_interrupt(
            db,
            principal.tenant_id,
            interrupt_id,
            decision=decision_status,
            decided_by=principal.user_id,
            decided_at=now,
            comment=(payload.comment or "").strip() or None,
            idempotent_key=idempotent_key,
        )
        if affected == 0:
            # 状态已不是 pending → 一定是**别人**（同一人的重试已在第 1 步被拦下）
            db.rollback()
            handled = agent_dao.get_interrupt(db, principal.tenant_id, interrupt_id, refresh=True)
            raise ConflictError(
                cls._conflict_message(db, principal, handled),
                cls._conflict_data(db, principal, handled),
            )

        # ---- 3. 恢复执行（从 ``interrupt`` 点的后继开始）----
        task = agent_dao.get_task(db, principal.tenant_id, int(interrupt.task_id))
        resumed = False
        next_node: str | None = None
        if task is None:
            # 任务被软删 → 待办仍可处理（责任人留痕），但不恢复执行
            logger.warning("待办 %s 所属任务已不可见，跳过恢复执行", interrupt_id)
        else:
            resumed, next_node = cls._resume(db, principal, task, interrupt, payload, now)

        db.commit()

        # ---- 4. 审计（commit 之后，独立事务）----
        AuditService.record(
            AuditAction.AGENT_INTERRUPT_RESOLVE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.AGENT_INTERRUPT,
            target_id=interrupt_id,
            before={"status": 0, "status_name": AgentInterruptStatus.name_of(0)},
            after={
                "status": decision_status,
                "status_name": AgentInterruptStatus.name_of(decision_status),
                "decision": payload.decision,
                "comment": payload.comment,
                "task_id": str(interrupt.task_id),
                "node_name": interrupt.node_name,
                "interrupt_type": interrupt.interrupt_type,
                "resumed": resumed,
                "next_node": next_node,
                "payload_keys": sorted((payload.payload or {}).keys()),
            },
        )

        # 🔴 ``refresh=True``：状态流转走的是 Core 条件更新，内存里的实体仍是旧值 ——
        # 不强制刷新就会把「刚刚通过」回显成 pending（见 ``agent_dao.get_interrupt``）。
        refreshed = agent_dao.get_interrupt(db, principal.tenant_id, interrupt_id, refresh=True)
        names = cls._decided_by_names(
            db, principal.tenant_id, [refreshed] if refreshed else []
        )
        return cls._to_resolve_out(
            refreshed or interrupt, next_node=next_node, resumed=resumed, names=names, task=task
        )

    # ------------------------------------------------------------------
    # API-95 重试失败任务
    # ------------------------------------------------------------------
    @classmethod
    def retry_task(
        cls, db: Session, principal: CurrentPrincipal, task_id: int
    ) -> AgentTaskRetryOut:
        """重试失败任务（F-A0.4）。

        🔴 **只允许从 ``failed`` / ``pending`` 重试**。
        对 ``success`` 重试会**重复执行副作用**（重复入库、重复向量化）；
        对 ``waiting_human`` 重试会**绕过人工确认**（那是合规红线，接口 §3 明确
        「关键决策点必须走人工确认」）。两者的共同点是「功能上能跑通，
        但破坏了一条更重要的规则」—— 故必须显式拒绝（**42201**）。

        重试从**当前节点**（即失败的那一步）开始，不是从头 —— 已完成的步骤不重跑。
        """
        task = cls._get_task(db, principal, task_id)
        if int(task.status) not in RETRYABLE_STATUSES:
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                f"当前状态（{AgentTaskStatus.name_of(int(task.status))}）不允许重试",
                {
                    "status": int(task.status),
                    "status_name": AgentTaskStatus.name_of(int(task.status)),
                    "retryable": [
                        AgentTaskStatus.name_of(s) for s in sorted(RETRYABLE_STATUSES)
                    ],
                },
            )

        graph = get_graph(str(task.task_type))
        start_node = task.current_node or graph.entry
        task.retry_count = int(task.retry_count) + 1
        db.flush()

        outcome = run_graph(
            graph,
            _load_state(task),
            start_node=start_node,
            on_step=cls._step_callback(db, principal, task),
            on_checkpoint=cls._checkpoint_callback(db, task),
        )
        cls._persist_outcome(db, principal, task, outcome)
        db.commit()

        AuditService.record(
            AuditAction.AGENT_TASK_RETRY,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.AGENT_TASK,
            target_id=task_id,
            after={
                "retry_count": int(task.retry_count),
                "start_node": start_node,
                "outcome": outcome.status,
                "next_node": outcome.next_node,
                "error": outcome.error,
            },
        )

        refreshed = agent_dao.get_task(db, principal.tenant_id, task_id) or task
        return AgentTaskRetryOut(
            task_id=refreshed.id,
            status=int(refreshed.status),
            status_name=AgentTaskStatus.name_of(int(refreshed.status)),
            retry_count=int(refreshed.retry_count),
            current_node=refreshed.current_node,
            resumed=True,
            message=outcome.error if outcome.status == "failed" else None,
        )

    # ------------------------------------------------------------------
    # 图执行（供 CU-14 等后续单元复用）
    # ------------------------------------------------------------------
    @classmethod
    def create_pending_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        task_type: str,
        state: dict[str, Any] | None = None,
        biz_id: int | None = None,
        idempotent_key: str | None = None,
    ) -> tuple[AgentTask, bool]:
        """Create an idempotent Agent task without resolving or executing a graph.

        The caller owns the transaction.  This allows an Outbox consumer to
        create a pending task and publish its event in one commit.
        """
        normalized_type = (task_type or "").strip()
        if normalized_type not in AgentTaskType.LABELS:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"不支持的任务类型 {task_type!r}",
                {"available": sorted(AgentTaskType.LABELS), "field": "task_type"},
            )
        if state is not None and not isinstance(state, dict):
            raise BizError(ErrorCode.PARAM_INVALID, "任务状态必须是对象", {"field": "state"})

        if idempotent_key:
            existing = agent_dao.find_task_by_idempotent_key(
                db, principal.tenant_id, idempotent_key
            )
            if existing is not None:
                return existing, True

        task = AgentTask(
            tenant_id=principal.tenant_id,
            task_type=normalized_type,
            biz_id=int(biz_id) if biz_id is not None else None,
            status=int(AgentTaskStatus.PENDING),
            retry_count=0,
            idempotent_key=(idempotent_key or None),
            graph_state=None,
            current_node=None,
        )
        agent_dao.insert_task(db, task)

        payload = dict(state or {})
        payload["__task_id__"] = int(task.id)
        payload["__tenant_id__"] = principal.tenant_id
        task.graph_state = _dump(payload)
        db.flush()
        return task, False

    # ------------------------------------------------------------------
    # 图执行（供 CU-14 等后续单元复用）
    # ------------------------------------------------------------------
    @classmethod
    def start_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        task_type: str,
        state: dict[str, Any] | None = None,
        biz_id: int | None = None,
        idempotent_key: str | None = None,
    ) -> tuple[AgentTask, GraphOutcome, bool]:
        """创建并**立即推进**一个任务（幂等）。

        :return: ``(task, outcome, replayed)``。``replayed=True`` 表示命中了幂等键、
            本次**没有新建也没有重跑**（调用方应把 ``code`` 置为 40903）。

        🔴 与接口 §1.6 一致：重复请求返回**首次结果**。
        首次结果快照存 ``agent_task.idempotent_result``（不存 Redis —— Redis 只存可丢数据）。
        """
        graph = get_graph(task_type)  # 未注册类型 → 40001

        if idempotent_key:
            existing = agent_dao.find_task_by_idempotent_key(
                db, principal.tenant_id, idempotent_key
            )
            if existing is not None:
                outcome = GraphOutcome(
                    status=_status_to_outcome(int(existing.status)),
                    state=_load_state(existing),
                    next_node=existing.current_node,
                )
                return existing, outcome, True

        task = AgentTask(
            tenant_id=principal.tenant_id,
            task_type=task_type,
            biz_id=int(biz_id) if biz_id is not None else None,
            status=int(AgentTaskStatus.RUNNING),
            retry_count=0,
            idempotent_key=(idempotent_key or None),
            graph_state=None,
            current_node=graph.entry,
        )
        agent_dao.insert_task(db, task)

        payload = dict(state or {})
        payload["__task_id__"] = int(task.id)
        payload["__tenant_id__"] = principal.tenant_id

        outcome = run_graph(
            graph,
            payload,
            on_step=cls._step_callback(db, principal, task),
            on_checkpoint=cls._checkpoint_callback(db, task),
        )
        cls._persist_outcome(db, principal, task, outcome)
        task.idempotent_result = _dump(
            {"status": outcome.status, "next_node": outcome.next_node, "task_id": str(task.id)}
        )
        db.commit()
        return task, outcome, False

    # ------------------------------------------------------------------
    # 内部：恢复执行
    # ------------------------------------------------------------------
    @classmethod
    def _resume(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task: AgentTask,
        interrupt: AgentInterrupt,
        payload: InterruptResolveRequest,
        now: datetime,
    ) -> tuple[bool, str | None]:
        """从 ``interrupt`` 点的后继恢复执行，返回 ``(resumed, next_node)``。

        🔴 ``next_node`` 由 ``registry.successor_of`` 求值 —— **与图运行时同一份逻辑**，
        避免「API 显示要执行 A、实际执行了 B」。

        🔴 返回的是**即将执行的那个后继节点**（``successor_of`` 的结果），
        而**不是** ``outcome.next_node``（= 跑完之后的下一个节点）。
        两者在「后继是终态节点」时差别很大：``persist_talent`` / ``manual_reject``
        都没有后继，``outcome.next_node`` 恒为 ``None``，于是接口会告诉前端
        「这个中断后面没有后续节点」——而它明明刚执行完入库 / 驳回。
        更直接的证据是**自相矛盾**：40903 幂等回放走的 ``_replay_data`` 用的是
        ``successor_of(...)``（返回 ``persist_talent``），首次调用却返回 ``None``，
        同一操作两种答案（前端在「首次提交」与「网络重试」上会看到不同结果）。
        """
        graph = get_graph(str(task.task_type))
        state = resume_state(
            _load_state(task),
            interrupt.node_name,
            {
                "decision": payload.decision,
                "payload": payload.payload or {},
                "comment": payload.comment,
                "handled_by": principal.user_id,
                "handled_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "interrupt_type": interrupt.interrupt_type,
            },
        )
        state["__task_id__"] = int(task.id)
        state["__tenant_id__"] = principal.tenant_id

        next_node = successor_of(str(task.task_type), interrupt.node_name, state)
        if next_node is None:
            # 该中断节点后无后继（图定义里的终态）→ 任务按「已驳回/已确认」收尾
            task.status = (
                int(AgentTaskStatus.SUCCESS)
                if payload.decision == "approved"
                else int(AgentTaskStatus.FAILED)
            )
            task.graph_state = _dump(state)
            db.flush()
            return False, None

        outcome = run_graph(
            graph,
            state,
            start_node=next_node,
            on_step=cls._step_callback(db, principal, task),
            on_checkpoint=cls._checkpoint_callback(db, task),
        )
        cls._persist_outcome(db, principal, task, outcome)
        # 返回**恢复起点**（= 刚刚真正执行的那个节点），见 docstring 的说明
        return True, next_node

    @classmethod
    def _step_callback(
        cls, db: Session, principal: CurrentPrincipal, task: AgentTask
    ) -> Any:
        """构造 ``on_step`` 回调：把运行时的**相对**步号平移成任务内的**全局**步号。

        🔴 图运行时的 ``record.step_index`` 是「**本次 run 内**从 1 开始」的相对序号。
        而一个任务会被恢复（API-94）与重试（API-95）多次，每次都是一个**新的 run** ——
        直接落相对序号会让第二次 run 的步骤与第一次**撞号**，
        而 API-92 承诺「按 ``step_index`` 升序 = 回放的时间线顺序」，
        ``agent_step`` 模型的注释也写着「由服务层单调分配」。
        实测的错乱结果（approve 之后）：

        ``detect_format(1), persist_talent(1), ocr(2), extract_fields(2), …``

        即「入库」这一步插到了「OCR」前面 —— 回放界面显示的是一条不可能发生的时间线。
        故回调按**任务内已有步数**做一次偏移，保证 ``step_index`` 全局单调。
        """
        offset = agent_dao.count_steps(db, principal.tenant_id, int(task.id))

        def _on_step(ctx: Any, record: Any) -> None:
            cls._persist_step(db, principal, task, ctx, record, index_offset=offset)

        return _on_step

    @classmethod
    def _persist_step(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task: AgentTask,
        ctx: Any,
        record: Any,
        *,
        index_offset: int = 0,
    ) -> None:
        """步骤回调：落 ``agent_step``（**只追加**，F-A0.5）。

        :param index_offset: 本次 run 之前该任务已有的步数（见 :meth:`_step_callback`）。
        """
        from app.dao import agent_dao as _dao

        _dao.insert_step(
            db,
            __import__("app.models.agent", fromlist=["AgentStep"]).AgentStep(
                tenant_id=principal.tenant_id,
                task_id=int(task.id),
                node_name=str(record.node_name),
                step_index=int(record.step_index) + int(index_offset),
                input_json=_dump(record.input),
                output_json=_dump(record.output),
                latency_ms=record.latency_ms,
                status=int(record.status),
                error_msg=_clip(record.error_msg, 512),
            ),
        )

    @classmethod
    def _checkpoint_callback(cls, db: Session, task: AgentTask) -> Any:
        """构造 ``on_checkpoint`` 回调：**节点级检查点**（批次 10 补齐，CU-14 开工前置）。

        此前检查点是 **run 级**：``_persist_outcome`` 只在 ``run_graph`` 返回后落一次，
        一个多节点任务跑到一半进程崩溃，只能**从头重跑** ——
        而已执行过的 ``side_effects`` 节点（写库 / 调模型计费）会被执行两遍。

        现在每个节点成功后立即把「state + 恢复起点」刷进 ``agent_task``：
        - ``graph_state`` = 执行到当前进度时的完整状态；
        - ``current_node`` = **下一个**待执行节点（不是刚跑完的那个）——
          与 API-95 重试「从 ``current_node`` 续跑」的语义对接后，
          恢复自然从「没跑过的第一步」开始，副作用不会重复执行。
        - 🔴 失败步骤不触发检查点：检查点必须指向「确定成功过」的进度；
          失败终态仍由 ``_persist_outcome`` 统一落（含 ``current_node`` 回拨到失败节点）。

        ⚠️ 与 ``agent_step`` 不同（步骤是**只追加**的历史），检查点是**同一行的原地更新**
        —— 每 run 内多次 flush 只产生最后一次可见值，语义就是「进度」，不是「历史」。
        """
        def _on_checkpoint(state: Any, next_node: str | None) -> None:
            task.graph_state = _dump(state)
            if next_node is not None:
                task.current_node = next_node
            db.flush()

        return _on_checkpoint

    @classmethod
    def _persist_outcome(
        cls, db: Session, principal: CurrentPrincipal, task: AgentTask, outcome: GraphOutcome
    ) -> AgentInterrupt | None:
        """把图运行结果落成任务状态 + 检查点（+ 必要时建待办）。"""
        task.graph_state = _dump(outcome.state)
        task.current_node = outcome.next_node or outcome.interrupt_node
        task.status = {
            "success": int(AgentTaskStatus.SUCCESS),
            "waiting_human": int(AgentTaskStatus.WAITING_HUMAN),
            "failed": int(AgentTaskStatus.FAILED),
        }[outcome.status]
        db.flush()

        if outcome.status != "waiting_human" or outcome.interrupt is None:
            return None
        interrupt = AgentInterrupt(
            tenant_id=principal.tenant_id,
            task_id=int(task.id),
            node_name=outcome.interrupt_node or "unknown",
            interrupt_type=outcome.interrupt.interrupt_type,
            payload_json=_dump(outcome.interrupt.payload),
            assignee_role=outcome.interrupt.assignee_role,
            status=0,
        )
        agent_dao.insert_interrupt(db, interrupt)
        if outcome.error:
            logger.warning("任务 %s 挂起等待人工，附注：%s", task.id, outcome.error)
        return interrupt

    # ------------------------------------------------------------------
    # 内部：查询与装配
    # ------------------------------------------------------------------
    @staticmethod
    def _get_task(db: Session, principal: CurrentPrincipal, task_id: int) -> AgentTask:
        task = agent_dao.get_task(db, principal.tenant_id, task_id)
        if task is None:
            # ⚠️ 越权与不存在统一 40301 的口径在此**不适用**：Agent 任务**没有行级数据域**
            # （它是执行流水，不是人员明细），故跨租户 = 本域内不存在 = 40401。
            # 与审计日志 / 字典同源。
            raise NotFoundError("Agent 任务不存在")
        return task

    @staticmethod
    def to_task_out(task: AgentTask) -> AgentTaskOut:
        return AgentTaskOut(
            id=task.id,
            task_type=str(task.task_type),
            task_type_name=AgentTaskType.label_of(str(task.task_type)),
            biz_id=task.biz_id,
            status=int(task.status),
            status_name=AgentTaskStatus.name_of(int(task.status)),
            current_node=task.current_node,
            retry_count=int(task.retry_count),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    @staticmethod
    def to_step_out(step: Any) -> AgentStepOut:
        return AgentStepOut(
            id=step.id,
            task_id=step.task_id,
            node_name=str(step.node_name),
            step_index=int(step.step_index),
            input=_loads_or_raw(step.input_json),
            output=_loads_or_raw(step.output_json),
            latency_ms=step.latency_ms,
            status=int(step.status),
            status_name=AiCallStatusLike.name_of(int(step.status)),
            error_msg=step.error_msg,
            created_at=step.created_at,
        )

    @classmethod
    def to_interrupt_out(
        cls, row: AgentInterrupt, *, names: dict[int, str] | None = None
    ) -> AgentInterruptOut:
        return AgentInterruptOut(
            id=row.id,
            task_id=row.task_id,
            node_name=str(row.node_name),
            interrupt_type=str(row.interrupt_type),
            interrupt_type_name=InterruptType.label_of(str(row.interrupt_type)),
            payload=_loads_or_raw(row.payload_json),
            assignee_role=row.assignee_role,
            status=int(row.status),
            status_name=AgentInterruptStatus.name_of(int(row.status)),
            decided_by=row.decided_by,
            decided_by_name=(names or {}).get(int(row.decided_by)) if row.decided_by else None,
            decided_at=row.decided_at,
            comment=row.comment,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _decided_by_names(
        db: Session, tenant_id: int, rows: list[AgentInterrupt]
    ) -> dict[int, str]:
        """批量反查处理人账号（**不过滤 ``is_deleted``** —— 已删用户也要能显示，
        否则历史待办的问责链会断裂，与审计日志同源处理）。"""
        ids = {int(row.decided_by) for row in rows if row.decided_by is not None}
        if not ids:
            return {}
        # 复用 CU-06 的批量反查（含已删除用户）
        return user_admin_dao.list_usernames_by_ids(db, tenant_id, sorted(ids))

    # ------------------------------------------------------------------
    # 内部：40902 / 40903 的响应体
    # ------------------------------------------------------------------
    @classmethod
    def _conflict_data(
        cls, db: Session, principal: CurrentPrincipal, handled: AgentInterrupt | None
    ) -> dict[str, Any]:
        if handled is None:  # pragma: no cover — 刚读过，理论上不可能
            return {}
        names = cls._decided_by_names(db, principal.tenant_id, [handled])
        return {
            "handled_by_name": names.get(int(handled.decided_by)) if handled.decided_by else None,
            "handled_at": (
                handled.decided_at.strftime("%Y-%m-%d %H:%M:%S") if handled.decided_at else None
            ),
            "status": int(handled.status),
        }

    @classmethod
    def _conflict_message(
        cls, db: Session, principal: CurrentPrincipal, handled: AgentInterrupt | None
    ) -> str:
        if handled is None:  # pragma: no cover
            return "该待办已被处理"
        names = cls._decided_by_names(db, principal.tenant_id, [handled])
        who = names.get(int(handled.decided_by)) if handled.decided_by else None
        return f"该待办已被 {who} 处理" if who else "该待办已被他人处理"

    @classmethod
    def _replay_data(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        replay: AgentInterrupt,
        idempotent_key: str,
    ) -> dict[str, Any]:
        """40903 的 ``data``：带**首次处理的结果**（前端按成功继续）。"""
        names = cls._decided_by_names(db, principal.tenant_id, [replay])
        task = agent_dao.get_task(db, principal.tenant_id, int(replay.task_id))
        decision = _status_to_decision(int(replay.status))
        result = cls._to_resolve_out(
            replay,
            next_node=(
                successor_of(str(task.task_type), replay.node_name, _load_state(task))
                if task is not None
                else None
            ),
            resumed=False,
            names=names,
            task=task,
        )
        return {
            "idempotent_key": idempotent_key,
            "first_handled_at": (
                replay.decided_at.strftime("%Y-%m-%d %H:%M:%S") if replay.decided_at else None
            ),
            "result": result.model_dump(mode="json"),
            "decision": decision,
        }

    @classmethod
    def _to_resolve_out(
        cls,
        row: AgentInterrupt,
        *,
        next_node: str | None,
        resumed: bool,
        names: dict[int, str] | None = None,
        task: AgentTask | None = None,
    ) -> InterruptResolveOut:
        return InterruptResolveOut(
            interrupt_id=row.id,
            interrupt_type=str(row.interrupt_type),
            status=int(row.status),
            status_name=AgentInterruptStatus.name_of(int(row.status)),
            task_id=row.task_id,
            resumed=resumed,
            next_node=next_node,
            task_status=int(task.status) if task is not None else None,
            task_status_name=AgentTaskStatus.name_of(int(task.status)) if task is not None else None,
            handled_by=row.decided_by,
            handled_by_name=(names or {}).get(int(row.decided_by)) if row.decided_by else None,
            handled_at=row.decided_at,
        )


class AiCallStatusLike:
    """``agent_step.status`` 的 0/1 名称（与 ``AiCallStatus`` 同值域但**不复用**）。

    ⚠️ 为什么不直接用 :class:`~app.models.enums.AiCallStatus`：
    它的标签是「失败 / 成功」；``agent_step`` 的 0/1 语义是「执行失败 / 执行成功」，
    目前恰好同词。但**语义归属不同**（一个是模型调用、一个是图节点执行），
    复用一个枚举会让「以后给节点加一个 ``skipped`` 状态」时被迫改动模型调用的枚举。
    当前标签一致是巧合，不是耦合的理由。
    """

    LABELS = {0: "失败", 1: "成功"}

    @classmethod
    def name_of(cls, value: int | None) -> str | None:
        if value is None:
            return None
        return cls.LABELS.get(int(value))


# ============================================================================
# 工具
# ============================================================================
def _load_state(task: AgentTask) -> dict[str, Any]:
    """读检查点里的图状态（解析失败按空状态处理并记 ERROR，与 checkpointer 同源）。"""
    raw = task.graph_state
    if not raw:
        return {}
    from json import JSONDecodeError, loads

    try:
        data = loads(raw)
    except (JSONDecodeError, TypeError) as exc:
        logger.error("graph_state 解析失败 task_id=%s: %s", task.id, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _loads_or_raw(text: str | None) -> Any:
    """JSON 字符串 → 对象；解析失败**原样返回字符串**（读取侧永远降级不阻塞）。"""
    if text is None:
        return None
    from json import JSONDecodeError, loads

    try:
        return loads(text)
    except (JSONDecodeError, TypeError):
        return text


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    from json import dumps

    return dumps(value, ensure_ascii=False, default=str)


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit]


def _status_to_outcome(status: int) -> str:
    """``AgentTaskStatus`` → ``GraphOutcome.status``（幂等回放时用于还原结果形状）。"""
    if status == int(AgentTaskStatus.SUCCESS):
        return "success"
    if status == int(AgentTaskStatus.WAITING_HUMAN):
        return "waiting_human"
    return "failed"


def _status_to_decision(status: int) -> str:
    return "approved" if status == int(AgentInterruptStatus.APPROVED) else "rejected"
