"""受控标签词表路由（CU-17，API-50~56，模块 I「标签」，F-3.4 / F-3.4.1）。

权限矩阵（接口 §4）：
| 接口 | 矩阵 | 权限码 |
| --- | --- | --- |
| API-50 词表 | 已登录（全角色） | 仅登录（无权限点 —— 词表是打标的下拉数据源） |
| API-51/52 增改 | ADM/HR | ``tag:create`` / ``tag:update`` |
| API-53 建议 | 系统/HR | ``tag:suggest`` |
| API-54 审核 | ADM/HR | ``tag:audit`` |
| API-55 人才标签 | ADM/HR/MGR/EMP(本人) | ``talent:detail``（EMP 本人由服务层数据域收窄） |
| API-56 调整标签 | ADM/HR | ``tag:assign`` |

🔴 越权口径：**档案**是行级数据域表 → 域外访问 **40301**（CR-8，防资源探测）。
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
from app.schemas.tag import (
    TagCreateRequest,
    TagDictOut,
    TagReviewOut,
    TagReviewRequest,
    TagSuggestOut,
    TagSuggestRequest,
    TagTreeNode,
    TagUpdateRequest,
    TalentTagsOut,
    TalentTagsPutRequest,
)
from app.services.tag_service import TagService

logger = get_logger(__name__)

router = APIRouter(tags=["I · 受控标签 (F-3.4)"])

DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]


def _fields(exc: ValidationError) -> BizError:
    fields = validation_fields(exc.errors())
    logger.warning("标签请求参数校验失败 %s", fields)
    return BizError(
        ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields}
    )


# ============================================================================
# API-50 受控标签词表（三级树 + 平铺）
# ============================================================================
@router.get("/talent/tag-dict")
def tag_dict(
    db: DbDep,
    principal: PrincipalDep,  # 矩阵：已登录（下拉数据源，无权限点）
    view: Annotated[str, Query(pattern="^(tree|flat)$")] = "flat",
    status: Annotated[int | None, Query(ge=0, le=2)] = None,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CommonResp:
    if view == "tree":
        return ok(TagService.tree(db, principal))
    rows, total, current, size = TagService.list_flat(
        db, principal, status=status, keyword=keyword, page=page, per_page=per_page
    )
    return paginated(rows, total, page=current, per_page=size)


# ============================================================================
# API-51 新增标签（管理员直接启用）
# ============================================================================
@router.post("/talent/tag-dict")
def create_tag(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("tag:create"))],
    payload: TagCreateRequest,
) -> CommonResp[TagDictOut]:
    return ok(TagService.create_tag(db, principal, payload))


# ============================================================================
# API-52 修改标签（含启用/停用，留痕走审计）
# ============================================================================
@router.put("/talent/tag-dict/{tag_id}")
def update_tag(
    tag_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("tag:update"))],
    payload: TagUpdateRequest,
) -> CommonResp[TagDictOut]:
    return ok(TagService.update_tag(db, principal, tag_id, payload))


# ============================================================================
# API-53 提交 AI 建议标签（不得直接入库）
# ============================================================================
@router.post("/talent/tag-dict/suggestions")
def suggest_tag(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("tag:suggest"))],
    payload: TagSuggestRequest,
) -> CommonResp[TagSuggestOut]:
    return ok(TagService.submit_suggestion(db, principal, payload, suggested_by=principal.user_id))


# ============================================================================
# API-54 审核标签建议
# ============================================================================
@router.post("/talent/tag-dict/suggestions/{suggestion_id}/review")
def review_tag_suggestion(
    suggestion_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("tag:audit"))],
    payload: TagReviewRequest,
) -> CommonResp[TagReviewOut]:
    return ok(
        TagService.review_suggestion(
            db,
            principal,
            suggestion_id,
            decision=payload.decision,
            tag_code=payload.tag_code,
            reject_reason=payload.reject_reason,
        )
    )


# ============================================================================
# API-55 人才标签（含来源：人工/AI）
# ============================================================================
@router.get("/talents/{talent_id}/tags")
def talent_tags(
    talent_id: Annotated[int, Path()],
    db: DbDep,
    principal: PrincipalDep,
) -> CommonResp[TalentTagsOut]:
    return ok(TagService.talent_tags(db, principal, talent_id))


# ============================================================================
# API-56 人工调整标签（只能选启用项）
# ============================================================================
@router.put("/talents/{talent_id}/tags")
def assign_talent_tags(
    talent_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("tag:assign"))],
    payload: TalentTagsPutRequest,
) -> CommonResp[TalentTagsOut]:
    return ok(TagService.assign_talent_tags(db, principal, talent_id, payload.tag_codes))
