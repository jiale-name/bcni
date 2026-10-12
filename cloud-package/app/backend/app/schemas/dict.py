"""数据字典契约（接口 API-23 ~ API-27，F-1.7）。

接口设计文档 §2.2 只给了这 5 个接口的**一行摘要**（方法 / 路径 / 权限 / 表），
没有逐字段的请求响应定义 —— 与 CU-05 的 API-18~22 情况相同。
因此本文件即这几个接口的**契约事实来源**，规则如下（沿用 CU-05 已确立的约定）：

| 约定 | 出处 |
| --- | --- |
| 雪花 ID 一律字符串下发 | 接口 §1.2（:data:`~app.schemas.common.SnowflakeId`） |
| 时间一律 ``YYYY-MM-DD HH:mm:ss`` | 接口 §1.1（:data:`~app.schemas.common.DateTimeStr`） |
| 未填字段下发 ``null``，不发空字符串 | 接口 §1.1 |
| 请求体 ``extra="forbid"``（多传字段 → 40001） | 接口 §1.5 参数校验失败 |

⚠️ 本文件里两处**刻意的「缺字段」**，不是遗漏：
1. :class:`DictItemUpdateRequest` **没有 ``item_code``** —— 编码是业务数据引用的值，
   不允许改（改了等于静默篡改历史语义）。因为 ``extra="forbid"``，
   客户端若仍传 ``item_code`` 会得到 **40001**，而不是被静默忽略。
2. :class:`DictTypeOut` 没有「创建/修改」请求体 —— 接口矩阵中**类型只有 GET**
   （API-23），类型由种子脚本初始化。不凭空造接口（与 CU-06 不给岗位加改/删接口同源）。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import DateTimeStr, SnowflakeId

# ============================================================================
# 编码规则（类型编码 / 项编码共用）
# ============================================================================
#: 🔴 只允许「小写字母或数字开头 + 小写字母/数字/下划线」。
#:
#: 为什么**必须**限制小写，而不是仅仅当作风格约定：
#: 字典两表的排序规则是 ``utf8mb4_unicode_ci``（**大小写不敏感**），
#: 于是 ``Master`` 与 ``master`` 在唯一键 ``uk_tenant_type_item`` 上是**同一个值**。
#: 若放行大写，调用方拿到的是含义模糊的 **40901 唯一冲突**；
#: 在入口就限制小写，则拿到的是明确的 **40001 参数格式错误** —— 同一件事，
#: 排障成本差一个数量级。
#:
#: 允许数字开头是为了兼容「可排序的枚举码」（如 ``01``/``02``）；
#: 不允许连字符与空格，因为这类编码会出现在 URL 路径与查询串里。
CODE_PATTERN = r"^[a-z0-9][a-z0-9_]{0,63}$"

Code = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=CODE_PATTERN),
]

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]

#: 排序号上限 —— 字典是「人工维护的少量枚举」，四位数足够；
#: 设上限可避免把 ``sort_no`` 写成时间戳这类滥用（读出来是乱序，且无法理解）。
SORT_NO_MAX = 9999


# ============================================================================
# API-23 字典类型列表（仅 ADM）
# ============================================================================
class DictTypeOut(BaseModel):
    """字典类型（含项计数，供管理页显示「3 启用 / 5 总」）。

    ``item_count`` / ``enabled_item_count`` 由**一条 ``GROUP BY`` 聚合**得出，
    不是逐类型 ``COUNT(*)``（见 :func:`app.dao.dict_dao.count_items_by_types`）。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="类型 ID")
    type_code: str = Field(..., description="类型编码，如 education_level")
    type_name: str = Field(..., description="类型名")
    remark: str | None = Field(None, description="备注")
    status: int = Field(..., description="状态 0 停用 1 启用")
    status_name: str | None = Field(None, description="状态名（中文，供直接展示）")
    item_count: int = Field(0, description="字典项总数（含停用项）")
    enabled_item_count: int = Field(0, description="启用中的字典项数")


class DictTypeListOut(BaseModel):
    """非分页列表（形状 ``data = {total, items}``，与 ``/roles`` ``/positions`` 一致）。

    为什么不分页：字典类型量级为 **10¹**（数据库 §3.1 表规模预估），
    一次性返回比让前端处理分页更省事，也避免了「下拉框要不要翻页」这种无意义的取舍。
    """

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="类型总数")
    items: list[DictTypeOut] = Field(default_factory=list)


# ============================================================================
# API-24 字典项列表（已登录）
# ============================================================================
class DictItemOut(BaseModel):
    """字典项。

    ⚠️ ``item_code`` 是**业务表引用它**的值（如 ``talent_base.education_level``），
    因此它一旦创建就不再变化。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="字典项 ID")
    type_code: str = Field(..., description="所属字典类型编码")
    item_code: str = Field(..., description="枚举值")
    item_name: str = Field(..., description="显示名")
    sort_no: int = Field(0, description="排序")
    parent_code: str | None = Field(None, description="父项编码（多级）")
    status: int = Field(..., description="状态 0 停用 1 启用")
    status_name: str | None = Field(None, description="状态名（中文）")
    created_at: DateTimeStr | None = Field(None, description="创建时间")
    updated_at: DateTimeStr | None = Field(None, description="更新时间")


class DictItemListOut(BaseModel):
    """非分页列表（形状 ``data = {total, items}``）。

    字典项量级为 **10²**（数据库 §3.1），且**每次下拉都可能全量拉取**。
    强行分页会让「多级联动」这类需要整型数据的场景变复杂，
    收益却不成立 —— 故不分页。
    """

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="字典项总数")
    items: list[DictItemOut] = Field(default_factory=list)


# ============================================================================
# API-25 新增字典项（仅 ADM）
# ============================================================================
class DictItemCreateRequest(BaseModel):
    """新增字典项。

    ``type_code`` 取自**路径**，不放进请求体 —— 避免「路径说 A、正文说 B」
    这种自相矛盾的请求（服务层无从判断以谁为准）。
    """

    model_config = ConfigDict(extra="forbid")

    item_code: Code = Field(..., description="枚举值（小写字母/数字/下划线，创建后不可修改）")
    item_name: DisplayName = Field(..., description="显示名")
    sort_no: int = Field(0, ge=0, le=SORT_NO_MAX, description="排序，越小越靠前")
    parent_code: Code | None = Field(None, description="父项编码（多级；必须已存在于同一类型下）")
    status: int = Field(1, ge=0, le=1, description="状态 0 停用 1 启用")


# ============================================================================
# API-26 修改字典项（仅 ADM，改前值/改后值留痕）
# ============================================================================
class DictItemUpdateRequest(BaseModel):
    """修改字典项（**部分更新**：未传的字段不改动）。

    🔴 ``parent_code`` 是**三态**语义，靠 ``model_fields_set`` 区分：
    未传 = 不改动 ｜ 显式传 ``null`` = 清空父项（提升为根项）｜ 传具体编码 = 挂到该父项下。
    少这一层区分，前端「把子项提升为根项」就会变成静默的「什么都没发生」。

    ⚠️ 本模型**故意没有 ``item_code``**：编码不可修改（见模块 docstring）。
    因为 ``extra="forbid"``，多传 ``item_code`` 会直接 **40001**。
    """

    model_config = ConfigDict(extra="forbid")

    item_name: DisplayName | None = Field(None, description="显示名（不传=不改）")
    sort_no: int | None = Field(None, ge=0, le=SORT_NO_MAX, description="排序（不传=不改）")
    parent_code: Code | None = Field(
        None, description="父项编码：不传=不改，传 null=清空（提升为根项），传值=挂到该父项下"
    )
    status: int | None = Field(None, ge=0, le=1, description="状态（不传=不改）")

    @property
    def parent_code_provided(self) -> bool:
        """``parent_code`` 是否在请求中出现过（区分「不改」与「清空」）。"""
        return "parent_code" in self.model_fields_set

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> DictItemUpdateRequest:
        """至少要改一个字段 —— 空请求体（``{}``）应当报 40001，而不是静默返回原值。

        静默成功会让调用方以为「改过了」，实际什么都没发生；
        这类「假成功」是最难排查的一类线上问题。
        """
        if not (self.model_fields_set & {"item_name", "sort_no", "parent_code", "status"}):
            raise ValueError("至少需要修改一个字段（item_name / sort_no / parent_code / status）")
        return self


# ============================================================================
# API-27 停用字典项（仅 ADM，不物理删）
# ============================================================================
class DictItemDeleteOut(BaseModel):
    """停用结果。

    ``already_disabled`` 让**幂等**变得可观测：重复停用返回 ``already_disabled=true``
    而不是报错 —— 前端刷新页面重放请求不该看到红色报错。
    （与「停用态会被拒绝」的设计正相反，理由见 :meth:`app.services.dict_service.DictService.disable_item`。）
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="字典项 ID")
    type_code: str = Field(..., description="所属字典类型编码")
    item_code: str = Field(..., description="枚举值")
    item_name: str = Field(..., description="显示名")
    status: int = Field(..., description="停用后的状态，恒为 0")
    status_name: str | None = Field(None, description="状态名（中文）")
    already_disabled: bool = Field(False, description="本次请求前就已是停用态（幂等重放）")
