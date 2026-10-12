"""组织与岗位的请求 / 响应契约（接口 API-07 ~ API-12，模块 B「组织与用户」）。

🔴 四条硬约束（与 ``schemas/auth.py`` / ``schemas/rbac.py`` 一致）
-----------------------------------------------------------------
1. **所有 ID 一律 ``SnowflakeId``**（接口 §1.2）—— 响应序列化为 string，请求兼容 string/number；
2. **入参 ``extra="forbid"``**（接口 §1.1）—— 字段拼错直接 40001，不静默忽略；
3. **枚举一律 ``*_code`` + ``*_name`` 同回**（接口 §1.1）—— 前端不得自建映射表；
4. **非分页列表统一 ``{total, items}``** —— 与 CU-05 的 ``RoleListOut`` / ``PermissionTreeOut``
   保持一致；**分页**列表（API-13）另走 ``data[] + page`` 同级结构（接口 §1.1）。

⚠️ ``parent_id=0`` 表示**根节点**，与 ``None``（未传 = 不移动）语义不同 —— 见
   :class:`OrgUpdateRequest` 的说明。这是本文件最容易搞错的一处。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import SnowflakeId

#: 组织编码长度上限（对齐 ``org_dept.dept_code`` VARCHAR(64)）
DEPT_CODE_MAX = 64
#: 组织名称长度上限（对齐 ``org_dept.dept_name`` VARCHAR(128)）
DEPT_NAME_MAX = 128
#: 岗位编码长度上限（对齐 ``position.position_code`` VARCHAR(64)）
POSITION_CODE_MAX = 64
#: 岗位名称长度上限（对齐 ``position.position_name`` VARCHAR(128)）
POSITION_NAME_MAX = 128
#: ``dept_path`` 列长上限（VARCHAR(512)）—— 决定组织最大嵌套层数
DEPT_PATH_MAX = 512
#: 层级上限：``dept_level`` 为 ``TINYINT``（有符号，上限 127），留出余量取 100
DEPT_LEVEL_MAX = 100


class _StrictModel(BaseModel):
    """入参基类：``extra="forbid"``（接口 §1.1）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ============================================================================
# API-07 组织架构树
# ============================================================================
class OrgNodeOut(BaseModel):
    """组织树节点（``dept_path`` 是行级权限的实现基础，故一并下发便于排查）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="组织 ID（雪花 ID 字符串）")
    parent_id: SnowflakeId = Field(0, description="父组织 ID（0 为根）")
    dept_code: str = Field(..., description="组织编码")
    dept_name: str = Field(..., description="组织名称")
    dept_level: int = Field(1, description="层级（1 起）")
    dept_path: str | None = Field(None, description="祖级路径，如 /1/12/125/（含自身，以 / 收尾）")
    leader_user_id: SnowflakeId | None = Field(None, description="负责人用户 ID")
    sort_no: int = Field(0, description="排序")
    status: int = Field(1, description="状态 0 停用 1 启用")
    status_name: str | None = Field(None, description="状态中文名")
    children: list["OrgNodeOut"] = Field(default_factory=list, description="子部门")


OrgNodeOut.model_rebuild()


class OrgTreeOut(BaseModel):
    """``GET /orgs/tree`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="部门总数（扁平计数，含全部层级）")
    items: list[OrgNodeOut] = Field(default_factory=list, description="组织树（根节点列表）")


# ============================================================================
# API-08 新建部门
# ============================================================================
class OrgCreateRequest(_StrictModel):
    """``POST /orgs`` 请求体。

    ``parent_id`` 缺省为 ``0``（建根部门）。``dept_path`` / ``dept_level``
    **不接受前端传入** —— 由服务端按父节点推导，前端传了也会被 ``extra="forbid"`` 拒绝。
    """

    parent_id: SnowflakeId = Field(0, description="父组织 ID（0 为根）")
    dept_code: str = Field(
        ...,
        min_length=1,
        max_length=DEPT_CODE_MAX,
        description="组织编码（租户内唯一，重复 → 40901）",
    )
    dept_name: str = Field(..., min_length=1, max_length=DEPT_NAME_MAX, description="组织名称")
    leader_user_id: SnowflakeId | None = Field(None, description="负责人用户 ID")
    sort_no: int = Field(0, ge=0, description="排序")
    status: int = Field(1, ge=0, le=1, description="状态 0 停用 1 启用")


# ============================================================================
# API-09 修改部门（含移动父节点）
# ============================================================================
class OrgUpdateRequest(_StrictModel):
    """``PUT /orgs/{id}`` 请求体（全部可选，未传则不改）。

    🔴 **``parent_id`` 的三种语义**（本接口最易踩的坑）：

    ================== ====================================================
    传入值             含义
    ================== ====================================================
    不传该字段           **不移动**（保持原父节点）
    ``parent_id=0``     **移动到根**
    ``parent_id=<id>``  移动到指定部门下
    ================== ====================================================

    服务端用 ``model_fields_set`` 区分「未传」与「传了 0」，
    因此**绝不能**用 ``req.parent_id is not None`` 来判断是否移动 —— 那会把
    「移到根」误判为「不移动」。
    """

    dept_code: str | None = Field(None, min_length=1, max_length=DEPT_CODE_MAX, description="组织编码")
    dept_name: str | None = Field(None, min_length=1, max_length=DEPT_NAME_MAX, description="组织名称")
    parent_id: SnowflakeId | None = Field(None, description="新父组织 ID（0 为移动到根）")
    leader_user_id: SnowflakeId | None = Field(None, description="负责人用户 ID")
    sort_no: int | None = Field(None, ge=0, description="排序")
    status: int | None = Field(None, ge=0, le=1, description="状态 0 停用 1 启用")


class OrgDeleteOut(BaseModel):
    """``DELETE /orgs/{id}`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    dept_id: SnowflakeId = Field(..., description="被删除的部门 ID")
    dept_name: str = Field(..., description="被删除的部门名称")
    deleted: bool = Field(True, description="是否已软删")
    affected_children: int = Field(0, description="连带受影响的子孙部门数（正常为 0：有子部门时拒绝）")


# ============================================================================
# API-11 岗位列表
# ============================================================================
class PositionOut(BaseModel):
    """岗位视图。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="岗位 ID（雪花 ID 字符串）")
    position_code: str = Field(..., description="岗位编码")
    position_name: str = Field(..., description="岗位名称")
    dept_id: SnowflakeId | None = Field(None, description="所属组织 ID")
    position_level: int | None = Field(None, description="岗位层级")
    status: int = Field(1, description="状态 0 停用 1 启用")
    status_name: str | None = Field(None, description="状态中文名")


class PositionListOut(BaseModel):
    """``GET /positions`` 响应 ``data``。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="岗位总数")
    items: list[PositionOut] = Field(default_factory=list, description="岗位列表")


# ============================================================================
# API-12 新建岗位
# ============================================================================
class PositionCreateRequest(_StrictModel):
    """``POST /positions`` 请求体。"""

    position_code: str = Field(
        ...,
        min_length=1,
        max_length=POSITION_CODE_MAX,
        description="岗位编码（租户内唯一，重复 → 40901）",
    )
    position_name: str = Field(..., min_length=1, max_length=POSITION_NAME_MAX, description="岗位名称")
    dept_id: SnowflakeId | None = Field(None, description="所属组织 ID（须存在，否则 40401）")
    position_level: int | None = Field(None, ge=0, le=100, description="岗位层级")
    status: int = Field(1, ge=0, le=1, description="状态 0 停用 1 启用")


__all__ = [
    "DEPT_CODE_MAX",
    "DEPT_LEVEL_MAX",
    "DEPT_NAME_MAX",
    "DEPT_PATH_MAX",
    "POSITION_CODE_MAX",
    "POSITION_NAME_MAX",
    "OrgCreateRequest",
    "OrgDeleteOut",
    "OrgNodeOut",
    "OrgTreeOut",
    "OrgUpdateRequest",
    "PositionCreateRequest",
    "PositionListOut",
    "PositionOut",
]
