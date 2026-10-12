"""查重与合并路由（CU-19，API-57~61，模块 J「查重」，F-3.5 / SQ-4 / SM-1）。

权限矩阵（接口 §4）：
| 接口 | 矩阵 | 权限码 |
| --- | --- | --- |
| API-57 候选对列表 | ADM/HR | ``dedup:list`` |
| API-58 并排对比 | ADM/HR | ``dedup:compare`` |
| API-59 一键合并 | ADM/HR | ``dedup:merge`` |
| API-60 忽略候选对 | ADM/HR | ``dedup:ignore`` |
| API-61 合并回滚 | ADM/HR | ``dedup:rollback`` |

🔴 越权口径：档案是行级数据域表 → 域外访问 **40301**（CR-8，防资源探测）。
🔴 API-59 幂等由**查重对状态机**天然保证：同一对只能合并一次，重复合并 → **40901**。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok, paginated
from app.schemas.dedup import (
    DedupMergeOut,
    DedupMergeRequest,
    DedupRollbackOut,
    DupCompareOut,
    DupIgnoreOut,
)
from app.services.dedup_service import DedupService

logger = get_logger(__name__)

router = APIRouter(tags=["J · 查重合并 (F-3.5)"])

DbDep = Annotated[Session, Depends(get_db)]


# ============================================================================
# API-57 重复候选对列表（相似度降序）
# ============================================================================
@router.get("/talent/dedup")
def list_dedup_pairs(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("dedup:list"))],
    status: Annotated[int | None, Query(ge=0, le=2)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommonResp:
    rows, total, current, size = DedupService.list_candidates(
        db, principal, status=status, page=page, per_page=per_page
    )
    return paginated(rows, total, page=current, per_page=size)


# ============================================================================
# API-58 并排对比
# ============================================================================
@router.get("/talent/dedup/{pair_id}")
def compare_dedup_pair(
    pair_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("dedup:compare"))],
) -> CommonResp[DupCompareOut]:
    return ok(DedupService.compare(db, principal, pair_id))


# ============================================================================
# API-59 一键合并（幂等：同一对重复合并 → 40901）
# ============================================================================
@router.post("/talent/dedup/{pair_id}/merge")
def merge_dedup_pair(
    pair_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("dedup:merge"))],
    payload: DedupMergeRequest,
) -> CommonResp[DedupMergeOut]:
    return ok(
        DedupService.merge(
            db,
            principal,
            pair_id,
            master_id=int(payload.master_id),
            field_strategy=payload.field_strategy,
            merge_tags=payload.merge_tags,
            merge_detail=payload.merge_detail,
        )
    )


# ============================================================================
# API-60 忽略候选对
# ============================================================================
@router.post("/talent/dedup/{pair_id}/ignore")
def ignore_dedup_pair(
    pair_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("dedup:ignore"))],
) -> CommonResp[DupIgnoreOut]:
    return ok(DedupService.ignore(db, principal, pair_id))


# ============================================================================
# API-61 合并回滚（merge_id = 查重对的 ID）
# ============================================================================
@router.post("/talent/dedup/merges/{merge_id}/rollback")
def rollback_merge(
    merge_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("dedup:rollback"))],
) -> CommonResp[DedupRollbackOut]:
    return ok(DedupService.rollback(db, principal, merge_id))
