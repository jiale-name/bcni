"""审计日志路由（API-28 / API-29，模块 E「审计日志」，F-1.5）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。

权限模型（接口 §4 权限矩阵）
--------------------------
| 接口 | 矩阵要求 | 本实现 | 说明 |
| --- | --- | --- | --- |
| API-28 审计日志查询 | ADM / AUDIT | ``audit:view`` | 种子已授给 AUDIT（``audit:`` 前缀）；ADM 走短路 |
| API-29 导出审计日志 | ADM / AUDIT | ``audit:export`` | 与查看**分开**（见下） |

🔴 查询参数用**显式 Query 参数 + 依赖收进模型**，并在校验失败时**显式转 40001**

之所以不省这一步、也不走 FastAPI 的「查询参数模型」特性（``Annotated[Model, Query()]``），
理由见 :func:`_audit_filters` 的 docstring —— 那是本单元实测踩到的坑：
框架特性在最小复现与真实应用里表现不一致，而手工 ``model_validate``
抛的 ``ValidationError`` 不经过全局异常处理器（会变成 50000）。
两个坑都已在代码里堵住，``tests/test_audit.py`` 有对应用例钉死「400 而非 500」。

⚠️ 三个**刻意不做**的事，理由都来自矩阵/文档而非直觉：

1. **不套 ``DataScopeFilter``。** AUDIT 角色的 ``data_scope=4``（仅统计值），
   若在查询前调 ``require_detail_access`` 就会把它 40301 掉 —— 与矩阵「AUDIT ✓」冲突。
   审计日志是**操作流水**而非「人员明细」，两者的可见性规则本就不同。

2. **不加写接口。** §4 明确「AUDIT 全列只读」；审计只由 Service 层在业务动作里写入，
   不经任何 HTTP 入口 —— 这从架构上杜绝了「调用 API 伪造审计记录」。

3. **不提供 ``DELETE /audit-logs``。** 留存靠**按月分区 + ``DROP PARTITION``**
   （数据库 §4.7），是运维动作而非业务接口。加一个删除接口等于给「抹掉痕迹」开了正门。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.audit import (
    AuditExportOut,
    AuditExportRequest,
    AuditFilterRequest,
    AuditLogOut,
)
from app.schemas.common import CommonResp, paginated, ok
from app.services.audit_service import AuditService

logger = get_logger(__name__)

router = APIRouter(tags=["E · 审计日志 (F-1.5)"])


# ============================================================================
# 内部：GET 的筛选参数 → AuditFilterRequest（与 API-29 的请求体共用同一套校验）
# ============================================================================
def _audit_filters(
    user_id: int | None = Query(None, description="按操作人 ID 筛选"),
    action: str | None = Query(
        None,
        max_length=64,
        description="按动作码筛选（精确匹配），如 `login`、`dict_item_update`、`audit_export`",
    ),
    target_type: str | None = Query(None, max_length=32, description="按对象类型筛选，如 `role`"),
    target_id: str | None = Query(None, max_length=64, description="按对象 ID 筛选"),
    start_time: str | None = Query(None, description="起始时间 `YYYY-MM-DD HH:mm:ss`（含）"),
    end_time: str | None = Query(None, description="截止时间 `YYYY-MM-DD HH:mm:ss`（含）"),
    keyword: str | None = Query(None, max_length=128, description="模糊搜索 `target_id` / `trace_id`"),
) -> AuditFilterRequest:
    """把 query 参数收进 :class:`AuditFilterRequest`，与 API-29 的请求体**共用同一套校验**。

    为什么绕这一步（而不是把 7 个参数直接传给 Service）：
    两处走同一套规则（空串归一、时间窗跨度、起止颠倒、词表校验），
    不会出现「列表接口拦得住、导出接口拦不住」。

    🔴 为什么在函数体里手工 ``model_validate`` 并**自己转 BizError**
    ------------------------------------------------------------
    ⚠️ 踩过的坑（已实测）：若把参数声明成 ``Annotated[AuditFilterRequest, Query()]``
    交给 FastAPI 的「查询参数模型」特性处理，本机 FastAPI 0.141.1 上
    **独立最小复现是通的，但在真实应用里退化成「一个名叫 filters 的必填标量参数」**
    （错误信息 ``query.filters Field required``）。这种「换个上下文就变行为」的
    框架特性不适合压在红线接口上。

    而手工 ``model_validate`` 抛的是 ``pydantic.ValidationError``，
    **不是** FastAPI 的 ``RequestValidationError``，因此**不经过**全局异常处理器 ——
    直接冒出去会变成 **50000**，而接口 §1.5 要求「参数错误 → 40001 + data.fields[]」。
    故此处显式转成 :class:`BizError`，并复用
    :func:`app.exceptions.validation_fields` 保证 ``fields[]`` 形状与请求体校验**逐字一致**。

    ⚠️ 时间参数在 query 里声明为 ``str`` 而不是 ``datetime``：接口 §1.1 约定的格式是
    ``YYYY-MM-DD HH:mm:ss``（空格分隔、本地时区）。交给 FastAPI 直接解析 ``datetime``
    会同时接受 ISO-8601（``T`` 分隔）等格式，**比约定的更宽** ——
    入口放行、下游按另一套口径理解，是跨端时间戳错乱的经典成因。
    """
    try:
        return AuditFilterRequest(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            start_time=start_time,
            end_time=end_time,
            keyword=keyword,
        )
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("审计筛选参数校验失败 %s", fields)
        raise BizError(
            ErrorCode.PARAM_INVALID,
            ERROR_META[ErrorCode.PARAM_INVALID].message,
            {"fields": fields},
        ) from exc


# ============================================================================
# API-28 审计日志查询
# ============================================================================
@router.get(
    "/audit-logs",
    response_model=CommonResp[list[AuditLogOut]],
    summary="API-28 审计日志查询（ADM / AUDIT）",
    dependencies=[Depends(require_perm("audit:view"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`（接口 §1.1）。按操作时间**倒序**。\n\n"
        "- 筛选维度：操作人 `user_id`、动作 `action`、对象 `target_type`/`target_id`、"
        "时间窗 `start_time`/`end_time`、模糊搜索 `keyword`（匹配 `target_id`/`trace_id`）；\n"
        "- 🔴 **时间跨度上限 90 天**：本表按月分区，跨月查询无法被分区裁剪，"
        "放开时间窗会让单次查询退化为全表扫描；\n"
        "- `before`/`after` 已**解析为对象**下发（改前/改后快照；非变更类动作的 "
        "`after` 是动作结果上下文）；\n"
        "- `username` 由 `user_id` 批量反查（**已删除用户也能显示**，历史日志不会因用户消失而空白）；\n"
        "- 跨租户查询**一律查不到**（租户条件由服务端强制注入，前端无法放宽）。"
    ),
)
def list_audit_logs(
    filters: AuditFilterRequest = Depends(_audit_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    # ⚠️ 故意**不写 le=100**：接口 §1.1 约定「超出按 100 处理并在 page 中回显实际值」，
    #    写 le=100 会让 `?per_page=999` 变成 422，与文档约定相反（与 `/users` 同处理）。
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[AuditLogOut]]:
    items, total, current, size = AuditService.query(
        db, principal, filters, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-29 导出审计日志（导出行为自身留痕）
# ============================================================================
@router.post(
    "/audit-logs/export",
    response_model=CommonResp[AuditExportOut],
    summary="API-29 导出审计日志（ADM / AUDIT，导出自身留痕）",
    dependencies=[Depends(require_perm("audit:export"))],
    description=(
        "按与 API-28 **完全相同的筛选语义**导出。\n\n"
        "- 🔴 **本身即写一条审计**（`action=audit_export`），"
        "内容含**本次实际生效的筛选条件**与条数 —— 否则「谁在什么时候导走了什么范围」无从回答；\n"
        "- 单次上限 `max_rows`（默认 10000，上限 50000）；**超限直接 40002 而不是截断**："
        "「导出一半却看不出不完整」在审计场景里是最危险的静默错误；\n"
        "- 返回 `data.content`（CSV 文本，UTF-8 **带 BOM**，Excel 双击可直接打开）"
        "而非文件流 —— 为了保住统一响应的 `code`/`trace_id`（见 `AuditExportOut` 文档）；\n"
        "- 🔴 已做 **CSV 公式注入防护**：`ua` 列内容由客户端 `User-Agent` 控制，"
        "以 `=`/`+`/`-`/`@` 开头的值会被前置单引号转义，避免用 Excel 打开时触发公式执行。"
    ),
)
def export_audit_logs(
    payload: Annotated[AuditExportRequest, Body(description="筛选条件 + 导出口径")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[AuditExportOut]:
    return ok(AuditService.export(db, principal, payload))
