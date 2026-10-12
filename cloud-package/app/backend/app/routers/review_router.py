"""分层复核工作台路由（CU-16，API-44~49，模块 H「复核」，F-3.3 / F-3.3.1）。

权限矩阵（接口 §4）：API-44~49 全部 **ADM / HR**（``review:*`` 权限点）。

🔴 越权口径：解析字段 / 复核条目挂在档案上（档案有 ``org_id`` 行级域），
但字段行的跨租户读取由 DAO 强制 ``tenant_id`` 收敛 → **40401**（同 Agent/审计口径）；
域内的行级收窄由列表侧承担（本批登记简化，单条子树校验随 CU-22 统一补）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.common import CommonResp, ok, paginated
from app.schemas.review import (
    ReviewConfirmItem,
    ReviewConfirmOut,
    ReviewCorrectOut,
    ReviewDashboardOut,
    ReviewDetailOut,
    ReviewQueueFilter,
    ReviewQueueItem,
    ReviewQueueMeta,
    ReviewQueueResp,
)
from app.services.review_service import ReviewService

logger = get_logger(__name__)

router = APIRouter(tags=["H · 分层复核 (F-3.3)"])

DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]


def _queue_filter(
    task_id: Annotated[int | None, Query()] = None,
    impact_level: Annotated[str, Query(pattern="^(HIGH|LOW|ALL)$")] = "HIGH",
    confidence_max: Annotated[float | None, Query(ge=0, le=1)] = None,
    field_codes: Annotated[str | None, Query(max_length=200)] = None,
    sample_only: Annotated[bool, Query()] = False,
) -> ReviewQueueFilter:
    try:
        return ReviewQueueFilter(
            task_id=task_id,
            impact_level=impact_level,  # type: ignore[arg-type]
            confidence_max=confidence_max,
            field_codes=field_codes,
            sample_only=sample_only,
        )
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("ReviewQueueFilter 参数校验失败 %s", fields)
        raise BizError(
            ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields}
        ) from exc


# ============================================================================
# API-44 复核队列（F-3.3 漏斗的落地点）
# ============================================================================
@router.get("/talent/review", response_model=ReviewQueueResp)
def review_queue(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:queue"))],
    filters: Annotated[ReviewQueueFilter, Depends(_queue_filter)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReviewQueueResp:
    """复核队列：默认只返回**高影响**字段中置信度低于阈值的条目。

    ``meta.warning`` 是非阻断提示（``code=0``）；真正阻断在 API-46。
    """
    items, meta, total, current, size = ReviewService.queue(
        db, principal, filters, page=page, per_page=per_page
    )
    from app.schemas.common import build_page_info, ok

    base = ok(items, page=build_page_info(total, current, size))
    return ReviewQueueResp(
        code=base.code,
        message=base.message,
        data=base.data,
        page=base.page,
        trace_id=base.trace_id,
        meta=meta,
    )


# ============================================================================
# API-49 复核工作量看板
# ============================================================================
# ⚠️ 必须注册在 ``/talent/review/{field_id}`` **之前**：
# FastAPI 按注册顺序匹配，静态段 ``dashboard`` 若排在路径参数路由后面，
# 会被 ``{field_id}`` 捕获（``"dashboard"`` 转 int 失败 → 422）。
@router.get("/talent/review/dashboard")
def review_dashboard(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:dashboard"))],
) -> CommonResp[ReviewDashboardOut]:
    return ok(ReviewService.dashboard(db, principal))


# ============================================================================
# API-45 单条复核详情
# ============================================================================
@router.get("/talent/review/{field_id}")
def review_detail(
    field_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:detail"))],
) -> CommonResp[ReviewDetailOut]:
    return ok(ReviewService.get_detail(db, principal, field_id))


# ============================================================================
# API-46 批量确认
# ============================================================================
@router.post("/talent/review/confirm")
def review_confirm(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:confirm"))],
    payload: Annotated[
        dict, Body(description='{"items": [...], "force": false}')
    ],
) -> CommonResp[ReviewConfirmOut]:
    """批量确认复核（**部分成功**：并发冲突进 ``conflicted[]``，不整批回滚）。"""
    try:
        items = [
            ReviewConfirmItem.model_validate(raw)
            for raw in (payload.get("items") or [])
        ]
    except ValidationError as exc:
        raise _fields(exc) from exc
    if not items:
        raise BizError(
            ErrorCode.PARAM_INVALID, "items 不能为空", {"field": "items"}
        )
    return ok(ReviewService.confirm(db, principal, items, force=bool(payload.get("force"))))


# ============================================================================
# API-47 修正单字段
# ============================================================================
@router.post("/talent/review/{field_id}/correct")
def review_correct(
    field_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:correct"))],
    payload: Annotated[dict, Body(description='{"value": "7"}')],
) -> CommonResp[ReviewCorrectOut]:
    value = str(payload.get("value") or "").strip()
    if not value:
        raise BizError(ErrorCode.PARAM_INVALID, "value 必填", {"field": "value"})
    return ok(ReviewService.correct_field(db, principal, field_id, value))


# ============================================================================
# API-48 标记免复核
# ============================================================================
@router.post("/talent/review/{field_id}/skip")
def review_skip(
    field_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("review:skip"))],
) -> CommonResp[ReviewCorrectOut]:
    return ok(ReviewService.skip_field(db, principal, field_id))


def _fields(exc: ValidationError) -> BizError:
    fields = validation_fields(exc.errors())
    logger.warning("复核请求参数校验失败 %s", fields)
    return BizError(
        ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields}
    )
