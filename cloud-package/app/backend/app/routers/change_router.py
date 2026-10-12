"""变更留痕与版本回溯路由（CU-22 前半，API-67~70，模块 M，F-3.10）。

权限矩阵（接口 §4）：
| 接口 | 矩阵 | 权限码 |
| --- | --- | --- |
| API-67 变更留痕列表 | ADM/HR/AUDIT | ``change:list`` |
| API-68 单档案变更链 | ADM/HR/AUDIT | ``change:chain`` |
| API-69 时间点快照回溯 | ADM/HR/AUDIT | ``change:snapshot`` |
| API-70 单字段变更链 | ADM/HR/AUDIT | ``change:field`` |

🔴 查询参数用**显式 Query 参数 + 依赖收进模型**，校验失败**显式转 40001** ——
理由与 ``audit_router._audit_filters`` 完全相同（框架的「查询参数模型」特性
在本机实测行为不稳定；且手工 ``model_validate`` 抛的 ``ValidationError``
不经过全局异常处理器，会变成 50000）。两个坑都已在本单元同样堵住。

⚠️ 三个**刻意不做**的事
====================
1. **不套 ``DataScopeFilter`` 收窄列表**（API-67）：留痕是「操作流水」而非「人员明细」，
   矩阵给了 AUDIT（``data_scope=4``），收窄会与矩阵直接冲突 ——
   与 ``AuditService.query`` 的规则三同一取舍。行级收窄做在**单档案**接口上
   （API-68/69/70 的 ``talent_id`` 逐条校验可见性）。
2. **不提供任何写接口**：留痕由业务动作（解析 / 复核 / 合并 / 重解析 / 删除审批）
   在 Service 层写入。加一个 HTTP 写入口等于给「伪造留痕」开了正门 ——
   而留痕是 F-3.10 的合规证据。
3. **不提供留痕删除/归档**：TBL-22 是**只追加**表（无 ``is_deleted`` / ``updated_at`` 列），
   保留策略属运维动作（库级权限），不是业务接口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.change import (
    ChangeFilterRequest,
    ChangeLogOut,
    FieldHistoryOut,
    SnapshotOut,
)
from app.schemas.common import DATETIME_FORMAT, CommonResp, paginated, ok
from app.services.change_service import ChangeService

logger = get_logger(__name__)

router = APIRouter(tags=["M · 变更留痕与版本回溯 (F-3.10)"])

#: API-69 的 ``field_codes`` 上限（接口 §2.2：「逗号分隔 ≤20」）
MAX_FIELD_CODES = 20
#: API-70 的 ``field_code`` 长度上限（与 ``field_name`` 列一致）
FIELD_CODE_MAX_LEN = 64


# ============================================================================
# 内部：GET 查询参数 → ChangeFilterRequest（API-67/68/70 共用同一套校验）
# ============================================================================
def _change_filters(
    # ⚠️ 形参名刻意**不叫** ``talent_id``（用 alias 对外仍是 ``?talent_id=``）：
    #    API-68/69 的路径里也有 ``talent_id``，而 FastAPI 的依赖解析是**按参数名**
    #    把路径参数并进依赖的 —— 同名会让本依赖把该参数当成 Path 处理，
    #    直接抛 ``Cannot use Query for path param 'talent_id'``（实测启动即报错）。
    filter_talent_id: str | None = Query(None, alias="talent_id", description="按档案 ID 筛选"),
    field_name: str | None = Query(None, max_length=64, description="按字段筛选（受控词表）"),
    change_source: int | None = Query(None, ge=0, le=3, description="按来源 0 AI 解析 1 人工 2 合并 3 重解析"),
    operator_id: str | None = Query(None, description="按操作人 ID 筛选"),
    task_id: str | None = Query(None, description="按关联任务 ID 筛选"),
    start_time: str | None = Query(None, description="起始时间 `YYYY-MM-DD HH:mm:ss`（含）"),
    end_time: str | None = Query(None, description="截止时间 `YYYY-MM-DD HH:mm:ss`（含）"),
) -> ChangeFilterRequest:
    """把 query 参数收进 :class:`ChangeFilterRequest`（时间窗跨度、词表、起止颠倒的**唯一**校验点）。

    ⚠️ 时间参数声明为 ``str`` 而不是 ``datetime``：接口 §1.1 约定的格式是
    ``YYYY-MM-DD HH:mm:ss``（空格分隔、本地时区）。交给 FastAPI 直接解析 ``datetime``
    会同时接受 ISO-8601（``T`` 分隔）等格式，**比约定更宽** ——
    入口放行、下游按另一套口径理解，是跨端时间戳错乱的经典成因。

    ⚠️ 雪花 ID 类参数（``talent_id`` / ``operator_id`` / ``task_id``）声明为 ``str``：
    它们会超过 JS ``Number.MAX_SAFE_INTEGER``（9.007e15），前端一律以**字符串**下发
    （接口 §1.2）；Pydantic 的 lax 模式会把 ``"123"`` 转成 ``int``，
    故这里收字符串再交给模型转换，避免 FastAPI 的 ``int`` 声明在超长数字上直接 422。
    """
    try:
        return ChangeFilterRequest(
            talent_id=filter_talent_id,
            field_name=field_name,
            change_source=change_source,
            operator_id=operator_id,
            task_id=task_id,
            start_time=start_time,
            end_time=end_time,
        )
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("变更留痕筛选参数校验失败 %s", fields)
        raise BizError(
            ErrorCode.PARAM_INVALID,
            ERROR_META[ErrorCode.PARAM_INVALID].message,
            {"fields": fields},
        ) from exc


def _parse_at(raw: str) -> datetime:
    """解析 API-69 的 ``at``（必填，``YYYY-MM-DD HH:mm:ss``）。

    ⚠️ 与筛选参数同源：**只接受约定的那一种格式**（见 :func:`_change_filters`）。
    多接受一种格式不是「更宽容」，而是让「前端某一版发错了格式」永远暴露不出来。
    """
    text = (raw or "").strip()
    if not text:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "at 不能为空",
            {"fields": [{"field": "at", "message": f"格式应为 {DATETIME_FORMAT}"}]},
        )
    for fmt in (DATETIME_FORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise BizError(
        ErrorCode.PARAM_INVALID,
        f"at 格式应为 {DATETIME_FORMAT}",
        {"fields": [{"field": "at", "message": f"格式应为 {DATETIME_FORMAT}，收到 {text!r}"}]},
    )


def _parse_field_codes(raw: str | None) -> list[str] | None:
    """解析 API-69 的 ``field_codes``（逗号分隔 ≤20）→ 去重保序的列表。"""
    text = (raw or "").strip()
    if not text:
        return None
    codes = [c.strip() for c in text.split(",") if c.strip()]
    deduped = list(dict.fromkeys(codes))
    if len(deduped) > MAX_FIELD_CODES:
        raise BizError(
            ErrorCode.PARAM_OVER_LIMIT,
            f"field_codes 最多 {MAX_FIELD_CODES} 个（收到 {len(deduped)} 个）",
            {"received": len(deduped), "limit": MAX_FIELD_CODES},
        )
    return deduped


# ============================================================================
# API-67 变更留痕列表（跨档案）
# ============================================================================
@router.get(
    "/talent/changes",
    response_model=CommonResp[list[ChangeLogOut]],
    summary="API-67 变更留痕列表（ADM / HR / AUDIT）",
    dependencies=[Depends(require_perm("change:list"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`（接口 §1.1）。按变更时间**倒序**"
        "（`created_at DESC, id DESC` 构成全序，同秒写入的多行也不会跨页重复）。\n\n"
        "- 筛选维度：档案 `talent_id`、字段 `field_name`、来源 `change_source`、"
        "操作人 `operator_id`、任务 `task_id`、时间窗 `start_time`/`end_time`；\n"
        "- 🔴 **时间跨度上限 90 天**：留痕 10⁷ 量级，不设上限的区间查询退化为全租户扫描；\n"
        "- 🔴 `field_name` 是**受控词表**：拼错一个字母会「查询成功返回 0 条」，"
        "故非法取值直接 **40001 并列出合法值**，而不是静默返回空列表；\n"
        "- `talent_name` / `operator_name` 批量反查（**已删档案、已删用户也能显示**）——"
        "「谁被删了」恰恰是留痕最需要说清的事；\n"
        "- 传了 `talent_id` 时服务端**逐条校验该档案可见性**，域外 **40301**"
        "（否则本接口会成为绕过 API-68 的越权入口）。"
    ),
)
def list_change_logs(
    filters: ChangeFilterRequest = Depends(_change_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[ChangeLogOut]]:
    items, total, current, size = ChangeService.list_logs(
        db, principal, filters, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-68 单档案变更链
# ============================================================================
@router.get(
    "/talents/{talent_id}/changes",
    response_model=CommonResp[list[ChangeLogOut]],
    summary="API-68 单档案变更链（ADM / HR / AUDIT）",
    dependencies=[Depends(require_perm("change:chain"))],
    description=(
        "某份档案的变更链（时间倒序，分页口径同 API-67）。\n\n"
        "- 🔴 **路径里的 `talent_id` 优先**：查询串里再传一个 `talent_id` 会被忽略，"
        "不会出现「返回 200 但查的是另一份档案」；\n"
        "- 域外访问 **40301**、档案不存在 **40401**（档案是行级数据域表，CR-8）；\n"
        "- 合并 / 删除这类**整体变更**的留痕行 `field_name` 为 `null`，"
        "`snapshot_ref` 指向快照（合并时可据此回滚，F-3.5）。"
    ),
)
def list_talent_changes(
    talent_id: Annotated[int, Path(ge=1, description="档案 ID")],
    filters: ChangeFilterRequest = Depends(_change_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[ChangeLogOut]]:
    items, total, current, size, _ = ChangeService.talent_chain(
        db, principal, talent_id, filters, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-69 时间点快照回溯（⭐ 合规证据）
# ============================================================================
@router.get(
    "/talents/{talent_id}/snapshot",
    response_model=CommonResp[SnapshotOut],
    summary="API-69 时间点快照回溯（ADM / HR / AUDIT，纯读）",
    dependencies=[Depends(require_perm("change:snapshot"))],
    description=(
        "重建档案在 `at` 时刻的字段值（**反向叠加** `talent_change_log` 的字段级前后值）。\n\n"
        "- 🔴 本接口**不修改任何数据**（纯读）；建议前端显式标注「历史快照 · 只读」；\n"
        "- 🔴 `at` 早于档案创建时间 → **40001** + `data.created_at`；\n"
        "- `untracked_fields` 是**该字段历史不完整的显式声明**（无留痕 → 取当前值），"
        "**不是错误**，前端不要当成查询失败；\n"
        "- `restored_fields` 给出「相对当前值被回退过的字段」，便于界面标注差异；\n"
        "- `history_truncated=true` 表示留痕过多、只取了前 5000 条参与重建 ——"
        "**此时结果不可作为合规证据**（如实声明不完整，而不是安静地给一个近似值）；\n"
        "- ⚠️ `tags` 是**当前值**：标签变更不进 TBL-22，无法回溯到 `at` 时刻；\n"
        "- 查阅行为本身写审计（`action=change_snapshot_view`）：回溯是合规证据，"
        "「谁在什么时候看了某人的历史」同样需要留痕。"
    ),
)
def talent_snapshot(
    talent_id: Annotated[int, Path(ge=1, description="档案 ID")],
    at: str = Query(..., description="回溯时间点 `YYYY-MM-DD HH:mm:ss`"),
    field_codes: str | None = Query(
        None, description=f"只回溯指定字段（逗号分隔，≤{MAX_FIELD_CODES} 个）"
    ),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[SnapshotOut]:
    return ok(
        ChangeService.snapshot(
            db,
            principal,
            talent_id,
            at=_parse_at(at),
            field_codes=_parse_field_codes(field_codes),
        )
    )


# ============================================================================
# API-70 单字段完整变更链
# ============================================================================
@router.get(
    "/talents/{talent_id}/fields/{field_code}/history",
    response_model=CommonResp[FieldHistoryOut],
    summary="API-70 单字段完整变更链（ADM / HR / AUDIT）",
    dependencies=[Depends(require_perm("change:field"))],
    description=(
        "某档案某字段的**完整**变更链（时间倒序）+ 该字段当前值。\n\n"
        "- `current_value` 与链上的值**同为字符串形态**，便于直接逐行对照"
        "（类型差异会让「值没变」看起来像「变过」）；\n"
        "- `field_code` 同样按**受控词表**校验（非法 → 40001 + 合法取值列表）；\n"
        "- 返回上限 1000 条（`total` 为真实总次数）：留痕是单调追加的，"
        "一个被反复重解析的档案可能积累上万行，不设上限会让前端渲染卡死；\n"
        "- 域外访问 **40301**、档案不存在 **40401**。"
    ),
)
def field_history(
    talent_id: Annotated[int, Path(ge=1, description="档案 ID")],
    field_code: Annotated[str, Path(max_length=FIELD_CODE_MAX_LEN, description="字段名（受控词表）")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[FieldHistoryOut]:
    return ok(ChangeService.field_history(db, principal, talent_id, field_code))
