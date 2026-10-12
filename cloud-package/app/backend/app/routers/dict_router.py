"""数据字典路由（API-23 ~ API-27，模块 D「数据字典与配置」）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。
任何业务判定（能否改 ``item_code``、``parent_code`` 是否成环、停用是否幂等）
**都在 Service**（架构 §3.3）。

权限模型（接口 §4 权限矩阵）
--------------------------
| 接口 | 矩阵要求 | 本实现 | 说明 |
| --- | --- | --- | --- |
| API-23 字典类型列表 | 仅 ADM | ``dict:type:list`` | 见下 |
| API-24 字典项列表 | 全 7 角色 | **仅要求已登录**（无权限码） | 见下 |
| API-25 新增字典项 | 仅 ADM | ``dict:create`` | 该码只授 ADM |
| API-26 修改字典项 | 仅 ADM | ``dict:update`` | 该码只授 ADM |
| API-27 停用字典项 | 仅 ADM | ``dict:update`` | 见下 |

⚠️ 三处**刻意不照抄直觉写法**的取舍，都为了**精确对齐矩阵**：

1. **API-23 用新权限点 ``dict:type:list``，而不是复用 ``dict:view``。**
   ``dict:view`` 在种子中被广授给 HR / MGR / EMP / EXEC（供前端下拉与菜单权限），
   若用它把关，这 4 个角色就能列字典类型 —— 与矩阵「仅 ADM」不符。
   「管理端列类型」与「读字典项做下拉」本就是两件事，分成两个权限点更准确。

2. **API-24 用「仅要求已登录」，不挂 ``dict:view``。**
   接口 §2.2 给 API-24 标注的权限就是**「已登录」**，矩阵也是 7 个角色**全 ✓**。
   若挂 ``dict:view``，AIOPS / AUDIT 这两个角色（种子里没有该权限点）会被拒 ——
   与「全角色 ✓」冲突。这里遵循矩阵，不引入额外限制。

3. **API-27 复用 ``dict:update``。**
   接口矩阵里**没有** ``dict:disable`` 这个动作，且「停用」在语义上就是
   修改 ``status`` 字段（不是删除行，接口标题自己写着「不物理删」）。
   凭空造一个 ``dict:disable`` 权限点会造成「有权限点、矩阵里没有对应动作」的悬空项。

> 与 CU-06 的「岗位只有 view / create，故不提供改/删岗位接口」同一条纪律：
> **矩阵未定义即不实现**，不凭空造未经评审的权限点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok
from app.schemas.dict import (
    CODE_PATTERN,
    DictItemCreateRequest,
    DictItemDeleteOut,
    DictItemListOut,
    DictItemOut,
    DictItemUpdateRequest,
    DictTypeListOut,
)
from app.services.dict_service import DictService

router = APIRouter(tags=["D · 数据字典与配置 (F-1.7)"])

_TYPE_CODE = Path(
    ...,
    description="字典类型编码（小写字母/数字/下划线，如 `education_level`）",
    pattern=CODE_PATTERN,
)
_ITEM_ID = Path(..., description="字典项 ID（雪花 ID，字符串或数字均可）")


# ============================================================================
# API-23 字典类型列表
# ============================================================================
@router.get(
    "/dicts",
    response_model=CommonResp[DictTypeListOut],
    summary="API-23 字典类型列表（仅 ADM）",
    dependencies=[Depends(require_perm("dict:type:list"))],
    description="非分页列表，形状为 `data = {total, items}`（与 `/roles`、`/positions` 一致）"
    "—— 字典类型量级仅 10¹，分页没有收益。每项带 `item_count` / `enabled_item_count`，"
    "由**一条聚合查询**批量算出（不是逐类型 `COUNT(*)`）。"
    "可按 `status`（不传=含停用）与 `keyword` 过滤。",
)
def list_dict_types(
    status: int | None = Query(None, ge=0, le=1, description="状态 0 停用 1 启用；不传=全部"),
    keyword: str | None = Query(None, max_length=64, description="按类型编码/名称模糊搜索"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DictTypeListOut]:
    return ok(DictService.list_types(db, principal, status=status, keyword=keyword))


# ============================================================================
# API-24 字典项列表
# ============================================================================
@router.get(
    "/dicts/{type_code}/items",
    response_model=CommonResp[DictItemListOut],
    summary="API-24 字典项列表（已登录即可）",
    dependencies=[Depends(require_perm())],
    description="非分页列表，形状为 `data = {total, items}`。**默认返回全部项（含停用项）**："
    "下拉框场景请显式传 `status=1`。之所以默认全量 —— 若默认只给启用项，"
    "管理页就看不到停用项，也就**无法再把它启用回来**。"
    "支持 `parent_code`（多级联动）与 `keyword` 过滤；类型不存在 → 40401。",
)
def list_dict_items(
    type_code: str = _TYPE_CODE,
    status: int | None = Query(None, ge=0, le=1, description="状态 0 停用 1 启用；不传=全部"),
    parent_code: str | None = Query(None, max_length=64, description="仅取该父项下的子项"),
    keyword: str | None = Query(None, max_length=64, description="按项编码/显示名模糊搜索"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DictItemListOut]:
    return ok(
        DictService.list_items(
            db,
            principal,
            type_code,
            status=status,
            parent_code=parent_code,
            keyword=keyword,
        )
    )


# ============================================================================
# API-25 新增字典项
# ============================================================================
@router.post(
    "/dicts/{type_code}/items",
    response_model=CommonResp[DictItemOut],
    summary="API-25 新增字典项（仅 ADM）",
    dependencies=[Depends(require_perm("dict:create"))],
    description="`item_code` 只允许小写字母/数字/下划线（排序规则大小写不敏感，"
    "放行大写会变成含义模糊的 40901 唯一冲突）。同类型下编码重复 → **40901**；"
    "`parent_code` 必须已存在于**同一类型**下（否则 42201）；类型不存在 → **40401**。",
)
def create_dict_item(
    payload: DictItemCreateRequest,
    type_code: str = _TYPE_CODE,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DictItemOut]:
    return ok(DictService.create_item(db, principal, type_code, payload))


# ============================================================================
# API-26 修改字典项（改前值/改后值留痕）
# ============================================================================
@router.put(
    "/dict-items/{item_id}",
    response_model=CommonResp[DictItemOut],
    summary="API-26 修改字典项（仅 ADM，留痕）",
    dependencies=[Depends(require_perm("dict:update"))],
    description="部分更新：**未传的字段不改动**。`parent_code` 为三态 ——"
    "不传=不改、传 `null`=清空（提升为根项）、传值=挂到该父项下。"
    "🔴 **`item_code` 不可修改**：它是业务表引用的值，改了会让历史数据的枚举值"
    "静默变成孤儿；因请求体 `extra=forbid`，仍传 `item_code` 会得到 **40001**。"
    "改动前后值会 diff 后写入审计留痕（改前值/改后值/操作人）。",
)
def update_dict_item(
    payload: DictItemUpdateRequest,
    item_id: int = _ITEM_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DictItemOut]:
    return ok(DictService.update_item(db, principal, item_id, payload))


# ============================================================================
# API-27 停用字典项（不物理删）
# ============================================================================
@router.delete(
    "/dict-items/{item_id}",
    response_model=CommonResp[DictItemDeleteOut],
    summary="API-27 停用字典项（仅 ADM，不物理删）",
    dependencies=[Depends(require_perm("dict:update"))],
    description="把 `status` 置 0，**不删行、不软删** —— 业务表里存的是 `item_code`，"
    "删行会让历史数据查不到显示名。**幂等**：已停用时返回 `already_disabled=true` 而不报错。"
    "若该项下仍有**启用中的子项** → **40904**，先停子项再停父项"
    "（否则多级联动下子项永远选不到，且不报错）。",
)
def disable_dict_item(
    item_id: int = _ITEM_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DictItemDeleteOut]:
    return ok(DictService.disable_item(db, principal, item_id))
