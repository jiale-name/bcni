"""角色与权限路由（API-18 ~ API-22，模块 C「角色与权限」）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。
任何业务判定（内置角色不可改码、绑定合法性、缓存失效）**都在 Service**（架构 §3.3）。

权限模型（接口 §4 权限矩阵）
--------------------------
本组 5 个接口在矩阵中均为 **仅 ADM（``✓``）**，其余角色一律 ``❌``。
实现方式：统一挂 :func:`app.core.security.require_perm` ——

- ``ADM`` 在 ``require_perm`` 内**直接放行**；
- 其余角色走权限点快照；快照里没有该码就 **40301（fail-closed）**。

⚠️ 因此**权限点未种子化时，非 ADM 一律 40301** —— 这正是矩阵期望的结果，
   不需要额外写「角色白名单」判定。权限码命名统一为 ``rbac:<资源>:<动作>``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok
from app.schemas.rbac import (
    BindPermissionsRequest,
    PermissionTreeOut,
    RoleCreateRequest,
    RoleListOut,
    RoleOut,
    RolePermissionsOut,
    RoleUpdateRequest,
)
from app.services.rbac_service import RbacService

router = APIRouter(tags=["C · 角色与权限 (F-1.3)"])

_ROLE_ID = Path(..., description="角色 ID（雪花 ID，字符串或数字均可）")


# ============================================================================
# API-18 角色列表
# ============================================================================
@router.get(
    "/roles",
    response_model=CommonResp[RoleListOut],
    summary="API-18 角色列表（预置 + 自定义）",
    dependencies=[Depends(require_perm("rbac:role:list"))],
    description="返回本租户全部未删除角色，**预置角色（`is_builtin=true`）在前**。",
)
def list_roles(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[RoleListOut]:
    return ok(RbacService.list_roles(db, principal))


# ============================================================================
# API-19 新建角色
# ============================================================================
@router.post(
    "/roles",
    response_model=CommonResp[RoleOut],
    summary="API-19 新建自定义角色",
    dependencies=[Depends(require_perm("rbac:role:create"))],
    description="**只能建自定义角色**（`is_builtin=false`）；角色码在租户内唯一（重复 → 40901）。",
)
def create_role(
    payload: RoleCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[RoleOut]:
    return ok(RbacService.create_role(db, principal, payload))


# ============================================================================
# API-20 修改角色
# ============================================================================
@router.put(
    "/roles/{role_id}",
    response_model=CommonResp[RoleOut],
    summary="API-20 修改角色（含 data_scope）",
    dependencies=[Depends(require_perm("rbac:role:update"))],
    description="可改角色名 / 数据范围 / 状态。**不含 role_code**（改码请新建角色）。"
    "`ADM` 的数据范围固定为「全部」，不可改（42201）。",
)
def update_role(
    payload: RoleUpdateRequest,
    role_id: int = _ROLE_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[RoleOut]:
    return ok(RbacService.update_role(db, principal, role_id, payload))


# ============================================================================
# API-21 权限点树
# ============================================================================
@router.get(
    "/permissions/tree",
    response_model=CommonResp[PermissionTreeOut],
    summary="API-21 权限点树（菜单 / 按钮 / 接口）",
    dependencies=[Depends(require_perm("rbac:perm:tree"))],
    description="父节点缺失（被停用）的权限点**提升为根**，不会让整棵子树在界面上消失。",
)
def permission_tree(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PermissionTreeOut]:
    return ok(RbacService.permission_tree(db, principal))


# ============================================================================
# API-22 绑定权限点
# ============================================================================
@router.put(
    "/roles/{role_id}/permissions",
    response_model=CommonResp[RolePermissionsOut],
    summary="API-22 绑定权限点（全量替换）",
    dependencies=[Depends(require_perm("rbac:role:bind"))],
    description="`perm_ids` 即角色最终权限集合（**未列出的会被解绑**，便于幂等重放）。"
    "提交后自动失效该角色下所有用户的权限快照并递增权限版本号。",
)
def bind_role_permissions(
    payload: BindPermissionsRequest,
    role_id: int = _ROLE_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[RolePermissionsOut]:
    return ok(RbacService.bind_role_permissions(db, principal, role_id, payload))
