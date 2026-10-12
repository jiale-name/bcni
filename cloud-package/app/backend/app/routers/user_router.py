"""用户管理路由（API-13 ~ API-17，模块 B「组织与用户」）。

权限模型（接口 §4 权限矩阵）：本组 5 个接口在矩阵中**均为仅 ADM**，其余角色一律 ``❌``。

| 接口 | 权限码 |
| --- | --- |
| API-13 用户列表 | ``user:view`` |
| API-14 新建用户 | ``user:create`` |
| API-15 修改用户 | ``user:update`` |
| API-16 分配角色 | ``user:role`` |
| API-17 批量导入 | ``user:import`` |

🔴 **分页形状**（接口 §1.1，本组唯一的分页接口 API-13）：
``data`` **直接放数组**，分页信息放**同级** ``page``：``{total, page, per_page, pages}``。
❌ 不是 ``data.items[]`` —— 用 :func:`app.schemas.common.paginated` 组装，
   ``response_model`` 相应声明为 ``CommonResp[list[AdminUserOut]]``。

⚠️ 路由注册顺序：``POST /users/import`` 与 ``PUT /users/{user_id}`` 方法不同，不会误匹配；
   但静态段优于动态段是通用原则，新增 ``/users/xxx`` 字面路径时**放前面**更稳。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok, paginated
from app.schemas.user_admin import (
    AdminUserOut,
    AssignRolesRequest,
    UserCreateOut,
    UserCreateRequest,
    UserImportOut,
    UserRolesOut,
    UserUpdateRequest,
)
from app.services.user_admin_service import UserAdminService

router = APIRouter(tags=["B · 组织与用户 (F-1.1)"])

_USER_ID = Path(..., description="用户 ID（雪花 ID，字符串或数字均可）")


# ============================================================================
# API-17 批量导入用户
# ============================================================================
# ⚠️ 故意放在 `/users/{user_id}` 之前：虽然方法不同（POST vs PUT）不会撞车，
#    但把**静态路径**排在**动态路径**前是通用纪律，省得将来新增同方法路由时踩坑。
@router.post(
    "/users/import",
    response_model=CommonResp[UserImportOut],
    summary="API-17 批量导入用户（Excel / CSV 模板）",
    dependencies=[Depends(require_perm("user:import"))],
    description="支持 `.xlsx` / `.xlsm` / `.csv`。**逐行独立**处理 —— 单行失败只记入 "
    "`data.errors[]`，不影响其他行（与 F-3.1「单条失败不阻塞整批」同一取舍）。"
    "列：登录名* / 姓名 / 手机号 / 邮箱 / 组织编码 / 岗位编码 / 角色码（逗号分隔）。",
)
def import_users(
    file: UploadFile = File(..., description="模板文件（.xlsx / .xlsm / .csv）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[UserImportOut]:
    content = file.file.read()
    return ok(
        UserAdminService.import_users(
            db, principal, filename=file.filename or "", content=content
        )
    )


# ============================================================================
# API-13 用户列表（分页）
# ============================================================================
@router.get(
    "/users",
    response_model=CommonResp[list[AdminUserOut]],
    summary="API-13 用户列表（分页）",
    dependencies=[Depends(require_perm("user:view"))],
    description="`data` 为**数组**、分页信息在同级 `page`（接口 §1.1）。"
    "手机号/邮箱**一律脱敏**下发，任何角色都拿不到明文。"
    "`org_id` 传参**只能收窄**自身数据域，超出返回 40301。",
)
def list_users(
    keyword: str | None = Query(None, max_length=64, description="按登录名/姓名模糊搜索"),
    org_id: int | None = Query(None, description="按组织过滤（**含其子树**）"),
    position_id: int | None = Query(None, description="按岗位过滤"),
    status: int | None = Query(None, ge=0, le=1, description="状态 0 禁用 1 启用"),
    role_id: int | None = Query(None, description="按角色过滤"),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    # ⚠️ 故意**不写 le=100**：接口 §1.1 约定「超出按 100 处理并在 page 中回显实际值」，
    #    而不是报 40002/422。因此这里放开上限，由 `normalize_per_page` 收敛。
    #    写了 le=100 会让 `?per_page=999` 变成 422 —— 与文档约定相反。
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[AdminUserOut]]:
    items, total, current, size = UserAdminService.list_users(
        db,
        principal,
        keyword=keyword,
        org_id=org_id,
        position_id=position_id,
        status=status,
        role_id=role_id,
        page=page,
        per_page=per_page,
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-14 新建用户
# ============================================================================
@router.post(
    "/users",
    response_model=CommonResp[UserCreateOut],
    summary="API-14 新建用户",
    dependencies=[Depends(require_perm("user:create"))],
    description="登录名租户内唯一（重复 → 40901）。`password` 留空时服务端生成随机初始密码，"
    "**仅在 `data.initial_password` 回显这一次**，请立即记录。"
    "手机号/邮箱以 AES 加密（存 `VARBINARY`）。",
)
def create_user(
    payload: UserCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[UserCreateOut]:
    return ok(UserAdminService.create_user(db, principal, payload))


# ============================================================================
# API-15 修改用户
# ============================================================================
@router.put(
    "/users/{user_id}",
    response_model=CommonResp[AdminUserOut],
    summary="API-15 修改用户（含启用/禁用）",
    dependencies=[Depends(require_perm("user:update"))],
    description="**不含 `username`**（登录名是审计关联键，不可改）。"
    "`phone`/`email` 不传=不改动、显式传 `null`=清空。"
    "不能禁用自己，也不能禁用最后一名可用超管（42201）；启用账号会顺带**清零失败计数并解锁**。",
)
def update_user(
    payload: UserUpdateRequest,
    user_id: int = _USER_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[AdminUserOut]:
    return ok(UserAdminService.update_user(db, principal, user_id, payload))


# ============================================================================
# API-16 分配角色（全量替换）
# ============================================================================
@router.put(
    "/users/{user_id}/roles",
    response_model=CommonResp[UserRolesOut],
    summary="API-16 分配角色（全量替换）",
    dependencies=[Depends(require_perm("user:role"))],
    description="`role_ids` 即该用户最终的角色集合（**未列出的会被解绑**，便于幂等重放）。"
    "不能修改自己的角色；也不能摘掉最后一名可用超管的 `ADM`（42201）。"
    "提交后失效该用户权限快照并递增**用户级**权限版本号。",
)
def assign_user_roles(
    payload: AssignRolesRequest,
    user_id: int = _USER_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[UserRolesOut]:
    return ok(UserAdminService.assign_roles(db, principal, user_id, payload))
