"""组织与岗位路由（API-07 ~ API-12，模块 B「组织与用户」）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。
任何业务判定（``dept_path`` 怎么算、能否移动、能否删除）**都在 Service**（架构 §3.3）。

权限模型（接口 §4 权限矩阵）
--------------------------
| 接口 | 矩阵要求 | 本实现 |
| --- | --- | --- |
| API-07 组织树 | ADM/HR | ``org:view`` |
| API-08 新建部门 | ADM | ``org:create`` |
| API-09 修改部门 | ADM/HR | ``org:update`` |
| API-10 删除部门 | ADM | ``org:delete`` |
| API-11 岗位列表 | ADM/HR | ``position:view`` |
| API-12 新建岗位 | ADM | ``position:create`` |

⚠️ 岗位在权限矩阵中**只有 view / create 两个动作**（种子词表里也没有
``position:update`` / ``position:delete``）—— 因此本文件**不提供**改/删岗位的接口。
不是遗漏，是矩阵未定义即不实现（避免凭空造出未经评审的权限点）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok
from app.schemas.org import (
    OrgCreateRequest,
    OrgDeleteOut,
    OrgNodeOut,
    OrgTreeOut,
    OrgUpdateRequest,
    PositionCreateRequest,
    PositionListOut,
    PositionOut,
)
from app.services.org_service import OrgService

router = APIRouter(tags=["B · 组织与用户 (F-1.1)"])

_ORG_ID = Path(..., description="部门 ID（雪花 ID，字符串或数字均可）")


# ============================================================================
# API-07 组织架构树
# ============================================================================
@router.get(
    "/orgs/tree",
    response_model=CommonResp[OrgTreeOut],
    summary="API-07 组织架构树（≥5 级）",
    dependencies=[Depends(require_perm("org:view"))],
    description="返回本租户全部未删除部门构成的树，父节点已被软删的子部门**提升为根**"
    "（不让子树在界面上凭空消失）。`dept_path` 一并下发，便于排查行级权限问题。",
)
def org_tree(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[OrgTreeOut]:
    return ok(OrgService.tree(db, principal))


# ============================================================================
# API-08 新建部门
# ============================================================================
@router.post(
    "/orgs",
    response_model=CommonResp[OrgNodeOut],
    summary="API-08 新建部门",
    dependencies=[Depends(require_perm("org:create"))],
    description="`dept_path` / `dept_level` 由服务端按父节点推导，**不接受前端传入**。"
    "组织编码在租户内唯一（重复 → 40901）。",
)
def create_org(
    payload: OrgCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[OrgNodeOut]:
    return ok(OrgService.create_dept(db, principal, payload))


# ============================================================================
# API-09 修改部门（含移动父节点）
# ============================================================================
@router.put(
    "/orgs/{org_id}",
    response_model=CommonResp[OrgNodeOut],
    summary="API-09 修改部门（含移动父节点）",
    dependencies=[Depends(require_perm("org:update"))],
    description="`parent_id` **不传=不移动**、传 `0`=移到根、传具体 ID=移到该部门下。"
    "移动时会连带重算**整个子树**的 `dept_path` 与 `dept_level`；"
    "移到自己或自己的子孙下会形成环，返回 42201。",
)
def update_org(
    payload: OrgUpdateRequest,
    org_id: int = _ORG_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[OrgNodeOut]:
    return ok(OrgService.update_dept(db, principal, org_id, payload))


# ============================================================================
# API-10 删除部门
# ============================================================================
@router.delete(
    "/orgs/{org_id}",
    response_model=CommonResp[OrgDeleteOut],
    summary="API-10 删除部门（有子部门或在职账号时拒绝）",
    dependencies=[Depends(require_perm("org:delete"))],
    description="软删。有子部门、或有账号（含已禁用）挂在该部门及其子树下时返回 **40904**，"
    "提示先处理引用 —— 因为部门软删会让其下账号的行级可见性静默失效。",
)
def delete_org(
    org_id: int = _ORG_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[OrgDeleteOut]:
    return ok(OrgService.delete_dept(db, principal, org_id))


# ============================================================================
# API-11 岗位列表
# ============================================================================
@router.get(
    "/positions",
    response_model=CommonResp[PositionListOut],
    summary="API-11 岗位列表",
    dependencies=[Depends(require_perm("position:view"))],
    description="非分页列表，形状为 `data = {total, items}`（与 `/roles` 一致）。"
    "可按 `dept_id` / `status` / `keyword` 过滤。",
)
def list_positions(
    dept_id: int | None = Query(None, description="按所属组织过滤"),
    status: int | None = Query(None, ge=0, le=1, description="状态 0 停用 1 启用"),
    keyword: str | None = Query(None, max_length=64, description="按编码/名称模糊搜索"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PositionListOut]:
    return ok(
        OrgService.list_positions(
            db, principal, dept_id=dept_id, status=status, keyword=keyword
        )
    )


# ============================================================================
# API-12 新建岗位
# ============================================================================
@router.post(
    "/positions",
    response_model=CommonResp[PositionOut],
    summary="API-12 新建岗位",
    dependencies=[Depends(require_perm("position:create"))],
    description="岗位编码在租户内唯一（重复 → 40901）；`dept_id` 若传则必须存在（否则 40401）。",
)
def create_position(
    payload: PositionCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PositionOut]:
    return ok(OrgService.create_position(db, principal, payload))
