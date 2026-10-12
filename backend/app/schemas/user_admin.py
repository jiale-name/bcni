"""用户管理后台的请求 / 响应契约（接口 API-13 ~ API-17，模块 B「组织与用户」）。

🔴 本文件承载的三条业务红线（都在 §1.7 有出处）
-----------------------------------------------
1. **手机号 / 邮箱一律脱敏下发** —— ``AdminUserOut.phone`` / ``email`` 是 ``138****8888``
   这类掩码，**任何角色**都拿不到明文。用户管理是「配置」场景，明文 PII 不参与管理决策；
   明文身份字段按 §1.7 由**档案模块（N2 / CU-14/15）**在字段级裁剪下提供。
   这样做的好处：即便将来有人把 ``/users`` 的权限放开给 HR，也不会凭空多一个明文泄露口。
2. **``password_hash`` 永不出现在任何响应模型里** —— 不是靠「记得别塞」，而是
   本文件**没有**这个字段，多塞一个键就会被 ``extra="forbid"`` 顶回去。
3. **分页响应用 ``data[] + page`` 同级结构**（接口 §1.1），❌ 不是 ``data.items[]``。
   因此本文件**没有** ``UserListOut`` —— 列表由 ``CommonResp[list[AdminUserOut]]`` 承载。

⚠️ 时间字段统一用 :data:`app.schemas.common.DateTimeStr`（``YYYY-MM-DD HH:mm:ss``），
   不用 Pydantic 默认的 ISO-8601（带 ``T``）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import PASSWORD_MIN_LENGTH
from app.schemas.common import DateTimeStr, SnowflakeId

#: 登录名长度上限（对齐 ``sys_user.username`` VARCHAR(64)）
USERNAME_MAX = 64
#: 姓名长度上限（对齐 ``sys_user.real_name`` VARCHAR(64)）
REAL_NAME_MAX = 64
#: 手机号长度上限（手机号本身不超 20 位，留余量给国际号）
PHONE_MAX = 32
#: 邮箱长度上限
EMAIL_MAX = 128
PASSWORD_MAX = 64
#: 单次分配的角儿数量上限（防「一次绑 2000 个角色」这类畸形请求）
MAX_ROLES_PER_USER = 100


class _StrictModel(BaseModel):
    """入参基类：``extra="forbid"``（接口 §1.1）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ============================================================================
# API-13 / API-14 / API-15 用户视图
# ============================================================================
class AdminUserOut(BaseModel):
    """用户管理视图（**手机/邮箱已脱敏**，接口 §1.7）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="用户 ID（雪花 ID 字符串）")
    username: str = Field(..., description="登录名（创建后不可改）")
    real_name: str | None = Field(None, description="姓名")
    org_id: SnowflakeId | None = Field(None, description="归属组织 ID")
    org_name: str | None = Field(None, description="归属组织名称")
    position_id: SnowflakeId | None = Field(None, description="岗位 ID")
    position_name: str | None = Field(None, description="岗位名称")
    phone: str | None = Field(None, description="手机号（**已脱敏**，如 138****8888）")
    email: str | None = Field(None, description="邮箱（**已脱敏**，如 z***@example.com）")
    status: int = Field(1, description="状态 0 禁用 1 启用")
    status_name: str | None = Field(None, description="状态中文名")
    role_ids: list[SnowflakeId] = Field(default_factory=list, description="角色 ID 列表")
    roles: list[str] = Field(default_factory=list, description="角色码列表，如 ['HR']")
    last_login_at: DateTimeStr | None = Field(None, description="最近登录时间")
    created_at: DateTimeStr | None = Field(None, description="创建时间")


class UserCreateRequest(_StrictModel):
    """``POST /users`` 请求体。

    ``password`` 可留空 —— 留空时服务端生成随机初始密码并**仅在响应中回显一次**
    （``data.initial_password``）。这是管理后台的常规做法：管理员不必自己想密码，
    也不会有「所有人初始密码都是 123456」的批量风险。
    """

    username: str = Field(
        ...,
        min_length=3,
        max_length=USERNAME_MAX,
        description="登录名（租户内唯一，重复 → 40901）",
    )
    password: str | None = Field(
        None,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX,
        description=f"初始密码（≥{PASSWORD_MIN_LENGTH} 位）；留空则服务端随机生成并回显一次",
    )
    real_name: str | None = Field(None, max_length=REAL_NAME_MAX, description="姓名")
    org_id: SnowflakeId | None = Field(None, description="归属组织 ID（须存在，否则 40401）")
    position_id: SnowflakeId | None = Field(None, description="岗位 ID（须存在，否则 40401）")
    phone: str | None = Field(None, max_length=PHONE_MAX, description="手机号（AES 加密存储）")
    email: str | None = Field(None, max_length=EMAIL_MAX, description="邮箱（AES 加密存储）")
    role_ids: list[SnowflakeId] = Field(
        default_factory=list,
        max_length=MAX_ROLES_PER_USER,
        description="初始角色 ID 列表（须存在且属本租户，否则 40001）",
    )
    status: int = Field(1, ge=0, le=1, description="状态 0 禁用 1 启用")

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        """只做最宽松的形状校验（含 ``@`` 且 ``@`` 两侧非空）。

        ⚠️ 不做 RFC 5322 全量正则：那类正则又长又难维护，且会误杀合法地址
        （国际化域名、``+`` 别名等）。真正的验证手段是发信，这里只挡明显手误。
        """
        if v is None:
            return None
        text = v.strip()
        if not text:
            return None
        local, sep, domain = text.partition("@")
        if not sep or not local or not domain or "." not in domain:
            raise ValueError("邮箱格式不正确")
        return text


class UserCreateOut(BaseModel):
    """``POST /users`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    user: AdminUserOut = Field(..., description="新建的用户")
    initial_password: str | None = Field(
        None,
        description="🔴 **仅本次返回**的初始密码（管理员自填时不返回）。丢失只能重置，不可再查。",
    )


class UserUpdateRequest(_StrictModel):
    """``PUT /users/{id}`` 请求体（全部可选，未传则不改）。

    🔴 **``phone`` / ``email`` 的两种语义**（用 ``model_fields_set`` 区分，勿用 ``is not None``）：

    ================== ==========================================
    传入值             含义
    ================== ==========================================
    不传该字段           **不改动**（保留原值）
    显式传 ``null``      **清空**（把密文列置 NULL）
    ================== ==========================================

    ⚠️ **不含 ``username``** —— 登录名是审计与历史操作的关联键，
    改名等于换一个账号，会让既有日志对不上人；如需改名请新建账号。
    """

    real_name: str | None = Field(None, max_length=REAL_NAME_MAX, description="姓名")
    org_id: SnowflakeId | None = Field(None, description="归属组织 ID（影响行级权限）")
    position_id: SnowflakeId | None = Field(None, description="岗位 ID")
    phone: str | None = Field(None, max_length=PHONE_MAX, description="手机号；显式传 null 表示清空")
    email: str | None = Field(None, max_length=EMAIL_MAX, description="邮箱；显式传 null 表示清空")
    status: int | None = Field(None, ge=0, le=1, description="状态 0 禁用 1 启用")

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        """同 :class:`UserCreateRequest` 的宽松形状校验。"""
        if v is None:
            return None
        text = v.strip()
        if not text:
            return None
        local, sep, domain = text.partition("@")
        if not sep or not local or not domain or "." not in domain:
            raise ValueError("邮箱格式不正确")
        return text


# ============================================================================
# API-16 分配角色
# ============================================================================
class AssignRolesRequest(_StrictModel):
    """``PUT /users/{id}/roles`` 请求体。

    🔴 **全量替换语义**：``role_ids`` 即该用户最终的角色集合（未列出的会被解绑）。
    与 API-22 的角色-权限绑定同构 —— 让「分配结果」可幂等重放，前端不必算差集。
    """

    role_ids: list[SnowflakeId] = Field(
        default_factory=list,
        max_length=MAX_ROLES_PER_USER,
        description="角色 ID 列表（全量替换，允许空数组表示收回全部角色）",
    )


class UserRolesOut(BaseModel):
    """``PUT /users/{id}/roles`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    user_id: SnowflakeId = Field(..., description="用户 ID")
    role_ids: list[SnowflakeId] = Field(default_factory=list, description="分配后的角色 ID 列表")
    role_codes: list[str] = Field(default_factory=list, description="分配后的角色码列表")
    perm_version: int = Field(0, description="递增后的**用户级**权限版本号（前端据此重拉 API-05）")


# ============================================================================
# API-17 批量导入用户
# ============================================================================
class UserImportError(BaseModel):
    """单行导入失败的原因（**逐行独立**，不因一行失败放弃整批）。"""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(..., description="Excel 行号（含表头，从 1 起，便于管理员定位）")
    username: str | None = Field(None, description="该行的登录名（能解析出来才有）")
    reason: str = Field(..., description="失败原因")


class UserImportOut(BaseModel):
    """``POST /users/import`` 响应 ``data``。

    ``success + failed == total``；``failed > 0`` 时**不返回错误码**，
    仍为 ``code=0`` —— 与 F-3.1「单条失败不阻塞整批」同一取舍。
    """

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="模板中的数据行数（不含表头与空行）")
    success: int = Field(0, description="成功创建的用户数")
    failed: int = Field(0, description="失败行数")
    errors: list[UserImportError] = Field(default_factory=list, description="失败明细")


__all__ = [
    "EMAIL_MAX",
    "MAX_ROLES_PER_USER",
    "PHONE_MAX",
    "REAL_NAME_MAX",
    "USERNAME_MAX",
    "AdminUserOut",
    "AssignRolesRequest",
    "UserCreateOut",
    "UserCreateRequest",
    "UserImportError",
    "UserImportOut",
    "UserRolesOut",
    "UserUpdateRequest",
]
