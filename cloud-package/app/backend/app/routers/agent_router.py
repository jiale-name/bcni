"""Agent 与人工介入路由（API-90 ~ API-95，模块 P「Agent 与人工介入」，F-A0.1 ~ F-A0.5）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。

权限模型（接口 §4 权限矩阵）
==========================
| 接口 | 矩阵要求 | 本实现的权限码 |
| --- | --- | --- |
| API-90 任务列表 | ADM / HR / AIOPS | ``agent:task:list`` |
| API-91 任务详情 | ADM / HR / AIOPS | ``agent:task:detail`` |
| API-92 步骤回放 | ADM / HR / AIOPS | ``agent:task:replay`` |
| API-93 人工待办列表 | ADM / HR / AIOPS | ``agent:interrupt:list`` |
| ⭐API-94 处理待办 | ADM / HR / AIOPS | ``agent:interrupt:resolve`` |
| API-95 重试失败任务 | **ADM / AIOPS**（无 HR） | ``agent:task:retry`` |

⚠️ 矩阵里 API-90~94 有 HR，但种子的 ``ROLE_GRANTS["HR"]`` 原本**没有任何 ``agent:`` 前缀** ——
   即 HR 会被 ``require_perm`` 全部挡成 40301，与矩阵直接冲突。
   已在 ``scripts/seed_dev.py`` 为 HR 补 ``agent:task:list`` / ``agent:task:detail`` /
   ``agent:task:replay`` / ``agent:interrupt:list`` / ``agent:interrupt:resolve``
   （**故意不补 ``agent:task:retry``** —— 矩阵里 API-95 没给 HR）。

🔴 为什么这里**不**把越权做成 40301
--------------------------------
Agent 任务 / 步骤 / 待办三张表**都没有行级数据域**（表里没有 ``org_id``），
所以「跨租户访问」在服务层表现为**查不到**（DAO 强制注入 ``tenant_id``）→ **40401**。
这与字典（CU-07）、审计日志（CU-08）同一条口径：**「本租户内不存在」就是 40401**。
📌 注意与「有行级数据域的表」区分：那里越权与不存在**统一 40301**（防资源探测）；
   这里根本没有「越权看别人部门的数据」这种情形，不存在可被探测的信息差。

🔴 幂等（接口 §1.6）
------------------
- **API-94 是三个强制幂等接口之一**（API-36 / API-79 / API-94）→ 读 ``Idempotency-Key`` 请求头，
  重复提交返回**首次结果 + ``code=40903``**，前端按成功继续。
- **API-95 不在强制幂等清单内**，故**不接受** ``Idempotency-Key``：
  它的并发安全由**状态机**保证（只有 ``pending`` / ``failed`` 可重试，
  首次重试后状态离开终态，第二次必然 **42201**）—— 这比幂等键更可靠，
  因为幂等键有 24 小时有效期，而「已成功的任务不许再跑」是**永久**约束。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.agent import (
    AgentInterruptOut,
    AgentStepOut,
    AgentTaskDetailOut,
    AgentTaskFilterRequest,
    AgentTaskOut,
    AgentTaskRetryOut,
    InterruptFilterRequest,
    InterruptResolveOut,
    InterruptResolveRequest,
)
from app.schemas.common import CommonResp, ok, paginated
from app.services.agent_service import AgentService

logger = get_logger(__name__)

router = APIRouter(tags=["P · Agent 与人工介入 (F-A0)"])


# ============================================================================
# 内部：参数校验失败的统一出口（与 ai_router / audit_router 同口径）
# ============================================================================
def _param_invalid(fields: list[dict[str, str]]) -> BizError:
    """把校验错误转成 **40001 + ``data.fields[]``**（接口 §1.5）。

    手工 ``model_validate`` 抛的 ``ValidationError`` **不经过**全局异常处理器
    （它不是 FastAPI 的 ``RequestValidationError``），直接冒出去会变成 50000。
    """
    return BizError(ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields})


def _validate(model: type, /, **kwargs: object) -> object:
    """构造筛选模型；失败 → 40001（供各 Query 依赖复用，避免三处各写一遍 try/except）。"""
    try:
        return model(**kwargs)
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("%s 参数校验失败 %s", model.__name__, fields)
        raise _param_invalid(fields) from exc


def _idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=64,
            description="幂等键（雪花 ID 或 UUID）。**重复提交返回首次结果 + `code=40903`**",
        ),
    ] = None,
) -> str | None:
    """读取 ``Idempotency-Key`` 请求头（接口 §1.6）。

    - 空串 / 纯空白 → ``None``（前端「未填」时常发出空串，不能当成一个真实键，
      否则所有未填的请求会**共用同一个幂等键**，第二个请求就会拿到第一个的结果）；
    - ``max_length=64`` 由 FastAPI 在解析阶段挡住（超长 → 40001，形状仍是 ``fields[]``）。
    """
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    return key or None


# ============================================================================
# API-90 Agent 任务列表
# ============================================================================
def _task_filters(
    task_type: str | None = Query(None, max_length=32, description="任务类型（图名），如 `parse`"),
    status: int | None = Query(
        None, ge=0, le=4, description="0 pending 1 running 2 waiting_human 3 success 4 failed"
    ),
    biz_id: int | None = Query(None, description="关联业务对象 ID（如人才档案 ID）"),
) -> AgentTaskFilterRequest:
    return _validate(  # type: ignore[return-value]
        AgentTaskFilterRequest, task_type=task_type, status=status, biz_id=biz_id
    )


@router.get(
    "/agent/tasks",
    response_model=CommonResp[list[AgentTaskOut]],
    summary="API-90 Agent 任务列表（ADM / HR / AIOPS）",
    dependencies=[Depends(require_perm("agent:task:list"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`。按创建时间**倒序**。\n\n"
        "- `status`：`0` pending / `1` running / `2` **waiting_human**（等人工确认，"
        "对应 API-93 待办）/ `3` success / `4` failed；\n"
        "- 🔴 **本接口不返回 `graph_state`**（需要它请调 API-91）：它可能几十 KB"
        "（含中间抽取结果），塞进列表会让 20 条记录轻松超过 1MB；\n"
        "- `current_node` 是「当前/下一步要执行的节点名」，可用于「卡在哪一步」的粗筛；\n"
        "- 跨租户查询一律查不到（租户条件由服务端强制注入 → 40401 口径，见模块 docstring）。"
    ),
)
def list_tasks(
    filters: AgentTaskFilterRequest = Depends(_task_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[AgentTaskOut]]:
    items, total, current, size = AgentService.list_tasks(
        db, principal, filters, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-91 任务详情 + 图状态
# ============================================================================
@router.get(
    "/agent/tasks/{task_id}",
    response_model=CommonResp[AgentTaskDetailOut],
    summary="API-91 任务详情 + 图状态（ADM / HR / AIOPS）",
    dependencies=[Depends(require_perm("agent:task:detail"))],
    description=(
        "在 API-90 的字段基础上增加：\n\n"
        "- `graph_state`：**已解析为对象**下发（存储侧是 `LONGTEXT`）——"
        "让前端拿到 JSON 字符串再自己 `JSON.parse` 等于要求每个前端都处理一次"
        "「解析失败」分支，而写入侧保证它不可能失败。`null` 表示尚未跑过任何节点；\n"
        "- `step_count`：步骤总数（详情页展示「共 N 步」；列表接口不查，避免 N+1）；\n"
        "- `idempotent_key`：本次任务绑定的幂等键（排查「为什么两次请求只跑了一次」用）；\n"
        "- 任务不存在 / 已软删 / 跨租户 → **40401**。"
    ),
)
def get_task_detail(
    task_id: int = Path(..., gt=0, description="任务 ID（雪花 ID，字符串或数字均可）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[AgentTaskDetailOut]:
    return ok(AgentService.get_task_detail(db, principal, task_id))


# ============================================================================
# API-92 步骤回放
# ============================================================================
@router.get(
    "/agent/tasks/{task_id}/steps",
    response_model=CommonResp[list[AgentStepOut]],
    summary="API-92 步骤回放（输入 / 输出 / 耗时）（ADM / HR / AIOPS）",
    dependencies=[Depends(require_perm("agent:task:replay"))],
    description=(
        "按 `step_index` **升序**返回（回放的时间线顺序，不是倒序）。\n\n"
        "- `input` / `output` 均为**解析后的对象**（同 `graph_state` 的理由）；\n"
        "- `latency_ms` 是**单步耗时**，与 `ai_call_log.latency_ms`（单次模型调用）不同层："
        "一个节点可能包含多次模型调用，也可能一次都没有（纯规则节点）；\n"
        "- 🔴 步骤表是**只追加**的（无软删、无更新）：回放价值就在于「当时到底跑了什么」，"
        "允许改写等于让回放失去证据意义；\n"
        "- 先校验**任务可见性**再取步骤：跨租户的 `task_id` 会在这里就 40401，"
        "不会泄露「步骤存在但任务不可见」这种信息差。"
    ),
)
def list_steps(
    task_id: int = Path(..., gt=0, description="任务 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(50, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[AgentStepOut]]:
    items, total, current, size = AgentService.list_steps(
        db, principal, task_id, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-93 人工介入待办列表
# ============================================================================
def _interrupt_filters(
    status: int | None = Query(None, ge=0, le=2, description="0 待处理 1 已通过 2 已驳回"),
    task_id: int | None = Query(None, description="按所属任务筛选"),
    interrupt_type: str | None = Query(None, max_length=32, description="待办类型，如 `field_review`"),
    assignee_role: str | None = Query(None, max_length=32, description="按指派角色筛选，如 `HR`"),
) -> InterruptFilterRequest:
    return _validate(  # type: ignore[return-value]
        InterruptFilterRequest,
        status=status,
        task_id=task_id,
        interrupt_type=interrupt_type,
        assignee_role=assignee_role,
    )


@router.get(
    "/agent/interrupts",
    response_model=CommonResp[list[AgentInterruptOut]],
    summary="API-93 人工介入待办列表（ADM / HR / AIOPS）",
    dependencies=[Depends(require_perm("agent:interrupt:list"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`。按创建时间**倒序**（新待办在前）。\n\n"
        "- `interrupt_type` **决定 `payload` 的形状**：`field_review` 是待复核字段、"
        "`merge_confirm` 是待合并的两个档案、`cost_confirm` 是本次查询的预估成本 ——"
        "前端应按类型分别渲染，不要套用同一个表单；\n"
        "- `payload` **已解析为对象**下发；\n"
        "- `decided_by_name` 由 `decided_by` 批量反查，**已删除用户也能显示**"
        "（否则历史待办的处理人会变成空白，问责链断裂）；\n"
        "- 待办列表是**人工介入漏斗的入口**（F-A0.3）：`status=0` 即可得到「当前待我处理的队列」。"
    ),
)
def list_interrupts(
    filters: InterruptFilterRequest = Depends(_interrupt_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[AgentInterruptOut]]:
    items, total, current, size = AgentService.list_interrupts(
        db, principal, filters, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# ⭐API-94 处理人工待办（强制幂等）
# ============================================================================
@router.post(
    "/agent/interrupts/{interrupt_id}/resolve",
    response_model=CommonResp[InterruptResolveOut],
    summary="⭐API-94 处理人工待办（approved / rejected + 恢复执行）（ADM / HR / AIOPS）",
    dependencies=[Depends(require_perm("agent:interrupt:resolve"))],
    description=(
        "🔴 **强制幂等接口**（接口 §1.6）：请带 `Idempotency-Key` 请求头。\n\n"
        "三种并发/重试情形的区别（`data` 形状不同，前端必须分开处理）：\n\n"
        "| 情形 | `code` | HTTP | 说明 |\n"
        "| --- | --- | --- | --- |\n"
        "| 正常处理 | `0` | 200 | 待办状态已变更，并**从 `interrupt` 点恢复推进图** |\n"
        "| **同一人重复提交**（同幂等键） | `40903` | 409 | `data.result` 是**首次处理的结果**，"
        "`data.status` 是首次结论 —— **前端按成功继续**，不要弹错 |\n"
        "| **他人已处理** | `40902` | 409 | `data.handled_by_name` / `data.handled_at` 指出处理人，"
        "前端应刷新列表而不是重试 |\n\n"
        "🔴 **幂等判定先于状态判定**：先查幂等键（`interrupt.idempotent_key`），"
        "再走条件更新（`WHERE ... AND status = 0`，按影响行数判断是否真的改到了）。"
        "顺序反了会把「同一人的网络重试」误判成 `40902`（他人已处理），"
        "使用者看到「已被 张三 处理」而自己就是张三 —— 是最令人困惑的一类报错。\n\n"
        "- `rejected` 时 **`comment` 必填**（否则 40001）：驳回会把任务导向失败分支"
        "（`status=4` 或按图定义进入重试），**不静默丢弃**——"
        "「为什么被驳回」是这条记录唯一的业务价值；\n"
        "- `payload` 可携带复核修正值等补充数据（形状由 `interrupt_type` 决定）；\n"
        "- `next_node` **取自图定义**（与图运行时同一份逻辑），保证「接口说下一步执行 A」"
        "就是真的 A；`resumed=false` 有三种情况：① 任务已被软删；② 该节点在图定义里无后继；"
        "③ 恢复执行抛错（此时 `task_status=4`）。**不静默返回 `true`**；\n"
        "- 待办不存在 / 已软删 / 跨租户 → **40401**。"
    ),
)
def resolve_interrupt(
    payload: Annotated[InterruptResolveRequest, Body(description="处理结论 + 意见 + 补充数据")],
    interrupt_id: int = Path(..., gt=0, description="待办 ID（雪花 ID，字符串或数字均可）"),
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[InterruptResolveOut]:
    return ok(
        AgentService.resolve_interrupt(
            db, principal, interrupt_id, payload, idempotent_key=idempotency_key
        )
    )


# ============================================================================
# API-95 重试失败任务
# ============================================================================
@router.post(
    "/agent/tasks/{task_id}/retry",
    response_model=CommonResp[AgentTaskRetryOut],
    summary="API-95 重试失败任务（ADM / AIOPS）",
    dependencies=[Depends(require_perm("agent:task:retry"))],
    description=(
        "🔴 **只允许从 `pending` / `failed` 重试**（其余状态 → **42201**，`data.retryable` 列出可重试状态）：\n\n"
        "- 对 `success` 重试会**重复执行副作用**（重复入库、重复向量化）；\n"
        "- 对 `waiting_human` 重试会**绕过人工确认** —— 那是合规红线"
        "（接口 §3：「关键决策点必须走人工确认」）。\n\n"
        "两者的共同点是「功能上跑得通、但破坏了一条更重要的规则」，故必须显式拒绝。\n\n"
        "- 重试从**当前节点**（即失败的那一步）开始，**不是从头** —— 已完成的步骤不重跑；\n"
        "- `retry_count` 每次重试 +1，可在响应里直接看到；\n"
        "- `message` 仅在**本次重试再次失败**时给出失败原因（成功为 `null`）；\n"
        "- 本接口**不接受** `Idempotency-Key`：并发安全由状态机保证"
        "（重试后状态离开可重试集合，第二次必然 42201），见模块 docstring。"
    ),
)
def retry_task(
    task_id: int = Path(..., gt=0, description="任务 ID（雪花 ID，字符串或数字均可）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[AgentTaskRetryOut]:
    return ok(AgentService.retry_task(db, principal, task_id))


__all__ = ["router"]
