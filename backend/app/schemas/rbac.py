"""角色与权限点的请求 / 响应契约（接口 API-18 ~ API-22，模块 C「角色与权限」）。

🔴 三条硬约束（与 ``schemas/auth.py`` 一致）
------------------------------------------
1. **所有 ID 一律 ``SnowflakeId``**（接口 §1.2）—— 响应序列化为 string，请求兼容 string/number；
2. **入参 ``extra="forbid"``**（接口 §1.1）—— 字段拼错直接 40001，不静默忽略；
3. **枚举一律 ``*_code`` + ``*_name`` 同回**（接口 §1.1）—— 前端不得自建映射表。

⚠️ 内置角色（``is_builtin=1``）的删改规则是**业务判定**，不写在本文件；
   本文件只负责「形状」，判定在 :mod:`app.services.rbac_service`。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PermType, RoleDataScope
from app.schemas.common import SnowflakeId


class _StrictModel(BaseModel):
    """入参基类：``extra="forbid"``（接口 §1.1）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


#: 角色码长度上限（对齐 ``sys_role.role_code`` VARCHAR(32)）
ROLE_CODE_MAX = 32
#: 角色名长度上限（对齐 ``sys_role.role_name`` VARCHAR(64)）
ROLE_NAME_MAX = 64


# ============================================================================
# API-18 角色列表
# ============================================================================
class RoleOut(BaseModel):
    """单个角色视图。

    ``data_scope`` 是**行级 + 字段级裁剪的依据**（接口 §1.7），
    故必须同回中文名，避免前端自行维护映射。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="角色 ID（雪花 ID 字符串）")
    role_code: str = Field(..., description="角色码，如 ADM/HR/MGR")
    role_name: str = Field(..., description="角色名")
    data_scope: int = Field(..., description="数据范围 1 全部 2 本部门 3 本人 4 仅统计值")
    data_scope_name: str | None = Field(None, description="数据范围中文名")
    is_builtin: bool = Field(False, description="是否预置角色（预置角色不可删除）")
    status: int = Field(1, description="状态 0 停用 1 启用")
    status_name: str | None = Field(None, description="状态中文名")


class RoleListOut(BaseModel):
    """``GET /roles`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="角色总数")
    items: list[RoleOut] = Field(default_factory=list, description="角色列表")


# ============================================================================
# API-19 新建角色
# ============================================================================
class RoleCreateRequest(_StrictModel):
    """``POST /roles`` 请求体。"""

    role_code: str = Field(
        ...,
        min_length=2,
        max_length=ROLE_CODE_MAX,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
        description="角色码（字母开头，仅字母/数字/下划线）",
    )
    role_name: str = Field(..., min_length=1, max_length=ROLE_NAME_MAX, description="角色名")
    data_scope: int = Field(
        int(RoleDataScope.SELF),
        ge=1,
        le=4,
        description="数据范围 1 全部 2 本部门 3 本人 4 仅统计值",
    )
    status: int = Field(1, ge=0, le=1, description="状态 0 停用 1 启用")

    @field_validator("role_code", mode="before")
    @classmethod
    def _upper_code(cls, v: object) -> object:
        """角色码统一大写 —— ``ADM`` 与 ``adm`` 必须是同一个角色，否则权限判定会漏。"""
        return v.upper() if isinstance(v, str) else v


# ============================================================================
# API-20 修改角色
# ============================================================================
class RoleUpdateRequest(_StrictModel):
    """``PUT /roles/{id}`` 请求体（**全部字段可选**，未传则不改）。

    ⚠️ **不含 ``role_code``** —— 角色码是权限判定的一部分，
    改码等于换一个角色，会与既有绑定/审计记录脱节；如需改码请新建角色。
    """

    role_name: str | None = Field(None, min_length=1, max_length=ROLE_NAME_MAX, description="角色名")
    data_scope: int | None = Field(None, ge=1, le=4, description="数据范围 1~4")
    status: int | None = Field(None, ge=0, le=1, description="状态 0 停用 1 启用")


# ============================================================================
# API-21 权限点树
# ============================================================================
class PermissionNodeOut(BaseModel):
    """权限树节点（``perm_type``：1 菜单 / 2 按钮 / 3 接口）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="权限 ID（雪花 ID 字符串）")
    perm_code: str = Field(..., description="权限码，如 talent:view")
    perm_name: str = Field(..., description="权限名")
    perm_type: int = Field(..., description="类型 1 菜单 2 按钮 3 接口")
    perm_type_name: str | None = Field(None, description="类型中文名")
    parent_id: SnowflakeId = Field(0, description="父权限 ID（0 为根）")
    sort_no: int = Field(0, description="排序")
    status: int = Field(1, description="状态 0 停用 1 启用")
    children: list["PermissionNodeOut"] = Field(default_factory=list, description="子节点")


PermissionNodeOut.model_rebuild()


class PermissionTreeOut(BaseModel):
    """``GET /permissions/tree`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="权限点总数（扁平计数）")
    items: list[PermissionNodeOut] = Field(default_factory=list, description="权限点树（根节点列表）")


# ============================================================================
# API-22 绑定权限点
# ============================================================================
class BindPermissionsRequest(_StrictModel):
    """``PUT /roles/{id}/permissions`` 请求体。

    🔴 **全量替换语义**：``perm_ids`` 即角色最终的权限集合（未列出的会被解绑）。
    采用全量而非增量，是为了让「绑定结果」可幂等重放 —— 前端不必计算差集。
    """

    perm_ids: list[SnowflakeId] = Field(
        default_factory=list, max_length=2000, description="权限 ID 列表（全量替换）"
    )


class RolePermissionsOut(BaseModel):
    """``PUT /roles/{id}/permissions`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    role_id: SnowflakeId = Field(..., description="角色 ID")
    perm_ids: list[SnowflakeId] = Field(default_factory=list, description="绑定后的权限 ID 列表")
    perm_codes: list[str] = Field(default_factory=list, description="绑定后的权限码列表")
    affected_users: int = Field(0, description="受影响的用户数（已失效其权限快照）")
    perm_version: int = Field(0, description="递增后的租户全局权限版本号")


__all__ = [
    "BindPermissionsRequest",
    "PermissionNodeOut",
    "PermissionTreeOut",
    "RoleCreateRequest",
    "RoleListOut",
    "RoleOut",
    "RolePermissionsOut",
    "RoleUpdateRequest",
    "PermType",
    "RoleDataScope",
]
