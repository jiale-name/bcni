"""认证与会话的请求 / 响应契约（接口 API-01 ~ API-06）。

🔴 三条硬约束（逐字对齐接口文档）：

1. **所有 ID 一律 ``SnowflakeId``**（§1.2）—— 响应序列化为 string，请求兼容 string/number。
2. **入参 ``extra="forbid"``**（§1.1）—— 字段拼错直接 40001，不静默忽略。
3. **枚举一律 ``*_code`` + ``*_name`` 同回**（§1.1）—— 前端不得自建映射表。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import SnowflakeId

#: 口令长度（API-01：8~64 字符）
PASSWORD_MIN = 8
PASSWORD_MAX = 64


class _StrictModel(BaseModel):
    """入参基类：``extra="forbid"``（接口 §1.1）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ============================================================================
# API-01 登录
# ============================================================================
class LoginRequest(_StrictModel):
    """``POST /auth/login`` 请求体。"""

    username: str = Field(..., min_length=3, max_length=64, description="登录账号")
    password: str = Field(..., min_length=PASSWORD_MIN, max_length=PASSWORD_MAX, description="明文密码")
    tenant_code: str | None = Field(
        None, max_length=32, description="租户编码（多租户预留；一期留空取默认租户）"
    )


class UserInfoOut(BaseModel):
    """登录/当前用户返回的用户视图（API-01 ``data.user`` 与 API-04 ``data`` 同构）。

    🔴 ``id`` / ``tenant_id`` / ``org_id`` 均为雪花 ID 字符串（接口 §1.2）。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="用户 ID（雪花 ID，字符串）")
    username: str = Field(..., description="登录名")
    real_name: str | None = Field(None, description="姓名")
    tenant_id: SnowflakeId = Field(..., description="租户 ID（字符串）")
    org_id: SnowflakeId | None = Field(None, description="归属组织 ID（字符串）")
    org_name: str | None = Field(None, description="归属组织名称")
    roles: list[str] = Field(default_factory=list, description="角色码列表，如 ['HR']")
    data_scope: int = Field(..., description="数据范围 1 全部 2 本部门 3 本人 4 仅统计值")
    data_scope_name: str | None = Field(None, description="数据范围中文名")


class LoginOut(BaseModel):
    """``POST /auth/login`` 成功响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., description="访问令牌（Bearer）")
    refresh_token: str = Field(..., description="刷新令牌（仅可调 API-02）")
    token_type: str = Field("Bearer", description="令牌类型，固定 Bearer")
    expires_in: int = Field(..., description="access_token 剩余有效期（秒），由配置下发，前端不得硬编码")
    user: UserInfoOut


# ============================================================================
# API-02 刷新
# ============================================================================
class RefreshRequest(_StrictModel):
    """``POST /auth/refresh`` 请求体。"""

    refresh_token: str = Field(..., min_length=16, description="refresh_token")


class RefreshOut(BaseModel):
    """``POST /auth/refresh`` 成功响应 ``data``。

    🔴 **不返回新的 refresh_token** —— 刷新不延长刷新令牌生命周期（避免无限续期）。
    """

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., description="新的访问令牌")
    token_type: str = Field("Bearer", description="令牌类型，固定 Bearer")
    expires_in: int = Field(..., description="access_token 有效期（秒）")


# ============================================================================
# API-03 登出
# ============================================================================
class LogoutRequest(_StrictModel):
    """``POST /auth/logout`` 请求体（可选）。

    - 仅传 ``refresh_token`` → 同时吊销该 refresh_token；
    - ``all_devices=true`` → 该用户全部会话失效（F-1.2 强制下线）。
    """

    refresh_token: str | None = Field(None, min_length=16, description="一并吊销的刷新令牌")
    all_devices: bool = Field(False, description="是否注销该用户全部会话")


class LogoutOut(BaseModel):
    """``POST /auth/logout`` 成功响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    revoked: bool = Field(..., description="是否已撤销")
    all_devices: bool = Field(False, description="是否全端注销")


# ============================================================================
# API-05 权限快照
# ============================================================================
class PermissionsOut(BaseModel):
    """``GET /auth/permissions`` 成功响应 ``data``。

    前端 ``v-perm`` 指令读 ``perm_codes``；路由守卫读 API-04 的 ``roles``。
    🔴 **后端 40301 才是最终裁决**，前端隐藏不是安全边界（接口 §4）。
    """

    model_config = ConfigDict(extra="forbid")

    perm_codes: list[str] = Field(default_factory=list, description="按钮/接口级权限码快照")
    roles: list[str] = Field(default_factory=list, description="角色码列表")
    is_admin: bool = Field(False, description="是否超级管理员（ADM 视为拥有全部权限点）")
    perm_version: int = Field(..., description="权限快照版本号，前端据此决定是否重新拉取")


# ============================================================================
# API-06 修改本人密码
# ============================================================================
class ChangePasswordRequest(_StrictModel):
    """``PUT /auth/password`` 请求体。"""

    old_password: str = Field(..., min_length=PASSWORD_MIN, max_length=PASSWORD_MAX, description="原密码")
    new_password: str = Field(..., min_length=PASSWORD_MIN, max_length=PASSWORD_MAX, description="新密码")


class ChangePasswordOut(BaseModel):
    """``PUT /auth/password`` 成功响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    changed: bool = Field(..., description="是否已修改")
    relogin_required: bool = Field(True, description="旧会话是否已全部失效（需重新登录）")
