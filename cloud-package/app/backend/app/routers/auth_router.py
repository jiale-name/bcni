"""认证与会话路由（API-01 ~ API-06）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。
任何业务判定（锁定、数据域、事务）**都不得出现在这里**（架构 §3.3 分层红线）。

路径前缀 ``/api/v1``（接口 §1.1 BaseURL）由 :mod:`app.main` 统一挂载。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import get_access_claims, get_current_principal
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.auth import (
    ChangePasswordOut,
    ChangePasswordRequest,
    LoginOut,
    LoginRequest,
    LogoutOut,
    LogoutRequest,
    PermissionsOut,
    RefreshOut,
    RefreshRequest,
    UserInfoOut,
)
from app.schemas.common import CommonResp, ok
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["A · 认证与会话 (F-1.2 / F-1.3)"])


def _client_ip(request: Request) -> str | None:
    """取真实客户端 IP。

    Nginx 反代后 ``request.client.host`` 恒为网关地址，故优先读 ``X-Forwarded-For`` 首段
    （部署侧须由 Nginx 覆写该头，见 ``deploy/nginx.conf``）。
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


# ============================================================================
# API-01 登录（公开）
# ============================================================================
@router.post(
    "/login",
    response_model=CommonResp[LoginOut],
    summary="API-01 账号密码登录",
    description="公开接口。连续 5 次密码错误锁定 10 分钟（F-1.2）。",
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CommonResp[LoginOut]:
    data = AuthService.login(db, payload, client_ip=_client_ip(request))
    return ok(data)


# ============================================================================
# API-02 刷新（持 refresh_token，无需 access_token）
# ============================================================================
@router.post(
    "/refresh",
    response_model=CommonResp[RefreshOut],
    summary="API-02 刷新 access_token",
    description="用 refresh_token 换新 access_token；**刷新时不延长 refresh_token 生命周期**。",
)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> CommonResp[RefreshOut]:
    return ok(AuthService.refresh(db, payload))


# ============================================================================
# API-03 登出（需登录）
# ============================================================================
@router.post(
    "/logout",
    response_model=CommonResp[LogoutOut],
    summary="API-03 登出",
    description="吊销当前会话；可一并吊销 refresh_token 或全端注销。**幂等**：重复调用同样成功。",
)
def logout(
    payload: LogoutRequest | None = None,
    principal: CurrentPrincipal = Depends(get_current_principal),
    claims: dict[str, Any] = Depends(get_access_claims),
    db: Session = Depends(get_db),
) -> CommonResp[LogoutOut]:
    # 请求体可选：不带 body 也能登出（前端重试/降级场景不应因缺 body 而失败）
    return ok(AuthService.logout(db, principal, payload or LogoutRequest(), access_payload=claims))


# ============================================================================
# API-04 当前用户（需登录）
# ============================================================================
@router.get(
    "/me",
    response_model=CommonResp[UserInfoOut],
    summary="API-04 当前用户信息与数据域",
    description="以数据库为准返回（改角色后无需等 Token 过期）。",
)
def me(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[UserInfoOut]:
    return ok(AuthService.me(db, principal))


# ============================================================================
# API-05 权限快照（需登录）
# ============================================================================
@router.get(
    "/permissions",
    response_model=CommonResp[PermissionsOut],
    summary="API-05 按钮级权限快照",
    description="供前端 ``v-perm`` 指令使用。🔴 后端 40301 才是最终裁决，前端隐藏不是安全边界。",
)
def permissions(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PermissionsOut]:
    return ok(AuthService.permissions(db, principal))


# ============================================================================
# API-06 修改本人密码（需登录）
# ============================================================================
@router.put(
    "/password",
    response_model=CommonResp[ChangePasswordOut],
    summary="API-06 修改本人密码",
    description="成功后**旧会话全部失效**（F-1.2），前端须引导重新登录。",
)
def change_password(
    payload: ChangePasswordRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[ChangePasswordOut]:
    return ok(AuthService.change_password(db, principal, payload))
