"""个人权利响应路由（CU-22 后半，API-71~80，模块 N，F-3.11 / SM-3）。

权限矩阵（接口 §4）：
| 接口 | 矩阵 | 权限码 | 说明 |
| --- | --- | --- | --- |
| API-71 查阅本人档案 | EMP（本人） | ``privacy:self_view`` | 仅本人，**无 ``talent_id`` 入参** |
| API-72 提交更正申请 | EMP（本人） | ``privacy:correct`` | 服务端校验档案归属 |
| API-73 更正申请列表 | ADM/HR | ``privacy:list`` | EMP 亦可（只能看自己提交的） |
| API-74 处理更正申请 | ADM/HR | ``privacy:correct`` | ⚠️ 服务层**再补角色判定**，见下 |
| API-75 提交删除申请 | EMP（本人） | ``privacy:delete`` | 服务端校验档案归属 |
| API-76 删除申请列表 | ADM/HR | ``privacy:list`` | 同上 |
| API-77 审批删除 | **ADM** | ``privacy:approve`` | **强制幂等** |
| API-78 提交导出申请 | EMP（本人） | ``privacy:export`` | 提交即完成（口径 5） |
| API-79 下载个人数据包 | EMP（本人）/ADM | ``privacy:export`` | 下载行为写审计 |
| API-80 权利请求台账 | ADM/HR | ``privacy:list`` | 含处理时限统计 |

🔴 两处「权限点不够、必须在服务层补判定」的地方（本单元实测出来的）
================================================================

1. **API-74**：``privacy:correct`` 同时授给了 EMP（他要提交申请）与 HR（他要处理申请）——
   同码复用是上游种子配置的现状。若只靠 ``require_perm``，**任何 EMP 都能审批自己的申请**。
   故 ``PrivacyService._require_manager`` 在服务层再判 ADM/HR（fail-closed）。
2. **API-73/76/80**：EMP 也持有 ``privacy:list``（用于查看**自己**的申请进度）。
   服务层按 ``data_scope``/角色把非管理者的查询**强制收窄为本人**，
   前端传 ``applicant_user_id`` 试图查看他人申请时只会得到空列表。

⚠️ 两个**刻意不做**的事
====================
- **不提供「代本人提交」**（ADM/HR 无 API-71/72/75/78 权限）：这是 F-3.11 的本人权利入口，
  管理者的档案维护走 API-31/32（接口 §4 备注）。加一个「代提」入口等于把
  「本人意愿」变成「管理者意愿」，对权利响应而言是根本性的失真。
- **不提供申请单删除/修改**：请求单是合规台账（TBL-30，只追加语义）。
  要撤销只能在处理意见里说明，而不是抹掉记录。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.common import CommonResp, build_page_info, ok, paginated
from app.schemas.privacy import (
    CorrectionResolveOut,
    CorrectionResolveRequest,
    CorrectionSubmitOut,
    CorrectionSubmitRequest,
    DeletionApproveOut,
    DeletionApproveRequest,
    DeletionSubmitOut,
    DeletionSubmitRequest,
    ExportDownloadOut,
    ExportSubmitOut,
    ExportSubmitRequest,
    PrivacyLedgerOut,
    PrivacyRequestFilter,
    PrivacyRequestOut,
)
from app.services.privacy_service import PrivacyService

logger = get_logger(__name__)

router = APIRouter(tags=["N · 个人权利响应 (F-3.11)"])


# ============================================================================
# 内部：Idempotency-Key（与 agent_router 同源约定，接口 §1.6）
# ============================================================================
def _idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=64,
            description="幂等键（雪花 ID 或 UUID）。**重复提交返回首次结果 + `code=40903`**",
        ),
    ] = None,
) -> str | None:
    """读取 ``Idempotency-Key`` 请求头。

    - 空串 / 纯空白 → ``None``：前端「未填」时常发出空串，若当成真实键，
      所有未填的请求会**共用同一个键** → 第二个请求会拿到第一个的结果；
    - 长度由 FastAPI 在解析阶段挡住（超长 → 40001，形状仍是 ``fields[]``）。
    """
    if idempotency_key is None:
        return None
    key = idempotency_key.strip()
    return key or None


# ============================================================================
# 内部：GET 查询参数 → PrivacyRequestFilter（API-73/76/80 共用同一套校验）
# ============================================================================
def _privacy_filters(
    request_type: int | None = Query(None, ge=0, le=2, description="0 更正 1 删除 2 导出"),
    status: int | None = Query(None, ge=0, le=3, description="0 待处理 1 已通过 2 已驳回 3 处理中"),
    talent_id: str | None = Query(None, description="按档案 ID 筛选"),
    applicant_user_id: str | None = Query(None, description="按申请人 ID 筛选（非管理者会被忽略并收窄为本人）"),
    overdue_only: bool = Query(False, description="只看**当前**超时的单子（API-80 的待办视图）"),
    start_time: str | None = Query(None, description="起始时间 `YYYY-MM-DD HH:mm:ss`（含）"),
    end_time: str | None = Query(None, description="截止时间 `YYYY-MM-DD HH:mm:ss`（含）"),
) -> PrivacyRequestFilter:
    """把 query 参数收进 :class:`PrivacyRequestFilter`（三个列表接口的唯一校验点）。

    ⚠️ 时间参数与雪花 ID 参数都声明为 ``str``，理由同 ``change_router._change_filters``：
    时间只接受约定格式（``T`` 分隔会让最下游静默错乱）；雪花 ID 超 JS 安全整数范围，
    前端一律以字符串下发（接口 §1.2），Pydantic lax 模式负责转换。
    """
    try:
        return PrivacyRequestFilter(
            request_type=request_type,
            status=status,
            talent_id=talent_id,
            applicant_user_id=applicant_user_id,
            overdue_only=overdue_only,
            start_time=start_time,
            end_time=end_time,
        )
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("权利请求筛选参数校验失败 %s", fields)
        raise BizError(
            ErrorCode.PARAM_INVALID,
            ERROR_META[ErrorCode.PARAM_INVALID].message,
            {"fields": fields},
        ) from exc


# ============================================================================
# API-71 查阅本人档案
# ============================================================================
@router.get(
    "/privacy/me/profile",
    response_model=CommonResp,
    summary="API-71 查阅本人档案（EMP 本人）",
    dependencies=[Depends(require_perm("privacy:self_view"))],
    description=(
        "返回**本人**档案的全部字段 + 每个字段的来源、置信度、复核状态（F-3.11 查阅权）。\n\n"
        "- 🔴 定位只走「登录用户 → 关联档案」（`talent_base.user_id`），"
        "**接口没有 `talent_id` 入参** —— 否则本接口会退化成「传个 ID 就穿透」的任意档案读取；\n"
        "- 🔴 证件号**只下发脱敏形态**（`id_card_masked`，如 `110101********1234`）："
        "查阅权同样受「最小必要」约束，完整核验走线下；\n"
        "- 手机号 / 邮箱对**本人**下发明文（这是本人对自己数据的查阅权）；\n"
        "- `fields[]` 里的 `source` 来自变更留痕（`talent_base` 本身没有「来源」列）；\n"
        "- `pending_corrections` 给出尚在处理的更正申请 ID（前端据此禁用重复提交）；\n"
        "- `correction_field_codes` / `correction_excluded_reason` 让前端**直接渲染可选清单**，"
        "并解释「为什么证件号不能自己改」；\n"
        "- `change_count` 是该档案的留痕条数（F-3.10 与查阅权的衔接点）；\n"
        "- 查阅行为写审计（`action=privacy_self_view`）。"
    ),
)
def get_my_profile(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp:
    return ok(PrivacyService.self_profile(db, principal))


# ============================================================================
# API-72 提交更正申请
# ============================================================================
@router.post(
    "/privacy/corrections",
    response_model=CommonResp[CorrectionSubmitOut],
    summary="API-72 提交更正申请（EMP 本人）",
    dependencies=[Depends(require_perm("privacy:correct"))],
    description=(
        "提交字段更正申请，**复用 F-3.3 人工复核队列**（`review_queue=talent_review`），"
        "由 HR 在 API-74 统一处理 —— 不另建一套审批流。\n\n"
        "- 🔴 `current_value` 是**乐观锁**：与库中不一致即 **40902**"
        "（`data.latest_value` / `data.latest_updated_at`），防「本人基于旧值提交 → 覆盖 HR 刚修好的值」；\n"
        "- 字段必须在**可更正白名单**内（`name` / `gender` / `birth_date` / `education_level` / "
        "`school` / `major` / `work_years` / `phone` / `email`）；"
        "证件号等不在白名单的字段会**40001 并说明理由**（不是静默忽略）；\n"
        "- 同一字段已有未终态的申请 → **40901**（不做静默合并）；\n"
        "- 目标值在**提交时**就按字段类型预检（如 `work_years` 必须是整数）——"
        "避免 HR 批准时才发现填的是「五年」；\n"
        "- 提交即写审计；处理时限 `sla_hours` 由服务端配置并**快照**进请求单。"
    ),
)
def submit_correction(
    payload: Annotated[CorrectionSubmitRequest, Body(description="更正字段 + 当前值 + 目标值 + 理由")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[CorrectionSubmitOut]:
    return ok(PrivacyService.submit_correction(db, principal, payload))


# ============================================================================
# API-73 更正申请列表
# ============================================================================
@router.get(
    "/privacy/corrections",
    response_model=CommonResp[list[PrivacyRequestOut]],
    summary="API-73 更正申请列表（ADM / HR；EMP 仅见自己的）",
    dependencies=[Depends(require_perm("privacy:list"))],
    description=(
        "更正申请列表（`data` 为数组、分页同级 `page`）。**待处理优先**排序 ——"
        "处理人的视线应先落在「还没出结果的单子」上，否则积压的老单会被新单盖住。\n\n"
        "- 非 ADM/HR 的调用者（如 EMP）**强制只看到自己提交的申请**"
        "（传 `applicant_user_id` 查他人无效）；\n"
        "- `overdue` 是**当前**是否超时（已终态的单子恒为 `false`：行内标记的用途是"
        "「提醒现在该处理谁」，不是「记过」）；\n"
        "- `waited_hours` 对未终态算到今天、对终态算到处理时刻。"
    ),
)
def list_corrections(
    filters: PrivacyRequestFilter = Depends(_privacy_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[PrivacyRequestOut]]:
    items, total, current, size = PrivacyService.list_corrections(
        db,
        principal,
        filters,
        page=page,
        per_page=per_page,
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-74 处理更正申请
# ============================================================================
@router.post(
    "/privacy/corrections/{request_id}/resolve",
    response_model=CommonResp[CorrectionResolveOut],
    summary="API-74 处理更正申请（ADM / HR，采纳即写档案 + 留痕）",
    dependencies=[Depends(require_perm("privacy:correct"))],
    description=(
        "采纳或驳回更正申请。**采纳时把值真正写进档案并写 `talent_change_log`**"
        "（F-3.10 与 F-3.11 的衔接点），同时把关联的复核条目收口为「已修正」。\n\n"
        "- 🔴 **驳回必须给理由**（≥10 字符，SM-3 红线②）：不带理由的驳回等于无限重提的起点；\n"
        "- 采纳时 `applied_value` 可覆盖申请里的 `expected_value`（HR 可据佐证材料修正）；\n"
        "- 🔴 手机号 / 邮箱的更正会**同时重算** `*_enc` 与 `*_hash`；"
        "留痕与审计里只写**脱敏形态**（PII 明文不进留痕表）；\n"
        "- 申请已被他人处理 → **40902**（`data` 带处理人姓名与时间）；\n"
        "- ⚠️ 仅 ADM / HR 可调用：`privacy:correct` 与「提交申请」共用同一个权限码，"
        "服务层会再补一次角色判定（否则 EMP 能审批自己的申请）；\n"
        "- 处理结果会写入请求单的通知字段（`notify_message` / `notified_at`）——"
        "一期无站内信表，本人**不会收到推送**，已登记为遗留。"
    ),
)
def resolve_correction(
    request_id: Annotated[int, Path(ge=1, description="权利申请 ID")],
    payload: Annotated[CorrectionResolveRequest, Body(description="是否采纳 + 驳回理由 + 实际写入值")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[CorrectionResolveOut]:
    return ok(PrivacyService.resolve_correction(db, principal, request_id, payload))


# ============================================================================
# API-75 提交删除申请
# ============================================================================
@router.post(
    "/privacy/deletions",
    response_model=CommonResp[DeletionSubmitOut],
    summary="API-75 提交删除申请（EMP 本人）",
    dependencies=[Depends(require_perm("privacy:delete"))],
    description=(
        "提交档案删除申请（F-3.11 删除权）。\n\n"
        "- 🔴 **审核权仅归 ADM**（接口 §4 备注）：本人只能提交，不能自审；\n"
        "- 服务端校验档案归属（`talent_base.user_id` = 登录用户），不符 **40301**；\n"
        "- 同一档案已有待处理的删除申请 → **40901**；\n"
        "- 提交**不会**立即删除任何数据 —— 实际删除在 API-77 审批通过时执行"
        "（含 Milvus 向量同步清理）。"
    ),
)
def submit_deletion(
    payload: Annotated[DeletionSubmitRequest, Body(description="档案 ID + 删除理由")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DeletionSubmitOut]:
    return ok(PrivacyService.submit_deletion(db, principal, payload))


# ============================================================================
# API-76 删除申请列表
# ============================================================================
@router.get(
    "/privacy/deletions",
    response_model=CommonResp[list[PrivacyRequestOut]],
    summary="API-76 删除申请列表（ADM / HR；EMP 仅见自己的）",
    dependencies=[Depends(require_perm("privacy:list"))],
    description=(
        "删除申请列表。筛选 / 排序 / 可见范围口径与 API-73 **完全一致**"
        "（同一套服务方法，仅 `request_type` 不同）。"
    ),
)
def list_deletions(
    filters: PrivacyRequestFilter = Depends(_privacy_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[PrivacyRequestOut]]:
    items, total, current, size = PrivacyService.list_deletions(
        db,
        principal,
        filters,
        page=page,
        per_page=per_page,
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-77 审批删除（🔴 跨库一致性 + 强制幂等）
# ============================================================================
@router.post(
    "/privacy/deletions/{request_id}/approve",
    response_model=CommonResp[DeletionApproveOut],
    summary="API-77 审批删除（ADM，跨库一致性 + 强制幂等）",
    dependencies=[Depends(require_perm("privacy:approve"))],
    description=(
        "审批删除申请。**MySQL 软删 + Milvus 向量清理是两个库的写操作，无分布式事务**，"
        "约定顺序与补偿（SQ-5）：\n\n"
        "1. 先把申请标记为「处理中」（CAS，防并发双审）；\n"
        "2. **先删 Milvus 向量**（COL-01~03 三个 collection），记录每个集合删了多少；\n"
        "3. 任一分片失败 → **不执行软删**，尝试重索引补偿已删分片，"
        "返回 **50000**，申请**保持待处理**（`data.vector_cleaned` 给出完整现场）；\n"
        "4. 全部向量清理成功 → **再软删 MySQL**（`is_deleted=1` + `data_status=4 已删除`）"
        "+ 写 `talent_change_log`；\n"
        "5. 最后才把申请置为**已通过**并生成通知正文。\n\n"
        "- 🔴 **强制幂等**：同一 `Idempotency-Key` 重复提交 → **40903** 并返回首次结果；\n"
        "- `approved=false` 时 `reason` 必填，且**不动任何数据**；\n"
        "- `vector_degraded=true` 表示运行在降级环境（关键词实现），本来就没有向量库 ——"
        "此时**不阻断删除**（档案必须删得掉），但会**如实标记**「本次未清理任何向量」；\n"
        "- 申请已被他人处理 → **40902**。"
    ),
)
def approve_deletion(
    request_id: Annotated[int, Path(ge=1, description="权利申请 ID")],
    payload: Annotated[DeletionApproveRequest, Body(description="是否同意 + 驳回理由")],
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[DeletionApproveOut]:
    return ok(
        PrivacyService.approve_deletion(
            db, principal, request_id, payload, idempotent_key=idempotency_key
        )
    )


# ============================================================================
# API-78 提交导出申请
# ============================================================================
@router.post(
    "/privacy/exports",
    response_model=CommonResp[ExportSubmitOut],
    summary="API-78 提交导出申请（EMP 本人，提交即完成）",
    dependencies=[Depends(require_perm("privacy:export"))],
    description=(
        "提交个人数据包导出申请（F-3.11 数据可携带权）。\n\n"
        "- 🔴 **提交即完成**（`status=1 已通过`）：接口集里没有「审批导出」的接口，"
        "若停在待处理它将永远无法变成已通过、API-79 也就永远下载不了；\n"
        "- `download_url` 是 API-79 的相对路径（下载行为写审计 + 计数）；\n"
        "- 格式一期仅 `json`（无 MinIO、无 PDF 生成库，契约上不宣称支持）；\n"
        "- 服务端校验档案归属，不符 **40301**。"
    ),
)
def submit_export(
    payload: Annotated[ExportSubmitRequest, Body(description="档案 ID + 数据包格式")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[ExportSubmitOut]:
    return ok(PrivacyService.submit_export(db, principal, payload))


# ============================================================================
# API-79 下载个人数据包
# ============================================================================
@router.get(
    "/privacy/exports/{request_id}/download",
    response_model=CommonResp[ExportDownloadOut],
    summary="API-79 下载个人数据包（EMP 本人 / ADM，下载留痕）",
    dependencies=[Depends(require_perm("privacy:export"))],
    description=(
        "生成并下发个人数据包。\n\n"
        "- 🔴 内容放在 **`data.content`（JSON 字符串）**，不返回文件流 ——"
        "同 CU-08 API-29 的取舍：**下载行为必须留痕**，文件流会让统一响应壳失效；\n"
        "- 每次下载写审计（`action=privacy_export_download`）并**原子自增**"
        "`download_count`（「数据包被取走几次」是合规凭据）；\n"
        "- 内容含：档案字段（**含来源与置信度**）/ 标签 / 变更摘要（计数与时间范围）/ 申请元信息；\n"
        "- 🔴 数据包里**证件号仍是脱敏形态**：数据包一旦落到本人设备就脱离平台管控，"
        "完整证件号的核验走线下；\n"
        "- 变更摘要**不含前值/后值**（留痕可能含他人操作明细）——"
        "本人有权知道「被改过几次、什么时候改的」，不足以也不需要看到同事的操作明细；\n"
        "- 仅本人或 ADM 可下载，其它角色 **40301**；申请未完成 → **42201**。"
    ),
)
def download_export(
    request_id: Annotated[int, Path(ge=1, description="导出申请 ID")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[ExportDownloadOut]:
    return ok(PrivacyService.download_export(db, principal, request_id))


# ============================================================================
# API-80 权利请求统一台账（含处理时限统计）
# ============================================================================
@router.get(
    "/privacy/requests",
    response_model=CommonResp[PrivacyLedgerOut],
    summary="API-80 权利请求统一台账（ADM / HR，含处理时限统计）",
    dependencies=[Depends(require_perm("privacy:list"))],
    description=(
        "三类权利请求的统一台账 + **处理时限统计**（F-3.11「处理时限可配置并统计」）。\n\n"
        "- ⚠️ `data` 是**对象**（`items` + `summary`）、分页仍在同级 `page`："
        "为承载统计而放宽 `data` 形状（与 CU-20 的 `SearchOut(items, meta)` 同一取舍）；\n"
        "- `summary.by_type` **分类型**给出处理时长：导出的处理时长恒为 0（提交即完成），"
        "跨类型平均值会被它拉低，故合规数字应以分类型为准；\n"
        "- 统计口径（`core.privacy.summarize_requests`）：超时**只算未终态**、"
        "平均/最长处理时长**只算终态**、时限用**单子自己提交时的快照值**"
        "（改配置不会让历史单据的判定集体反转）；\n"
        "- `overdue_only=true` 只看当前超时的单子（待办视图）——"
        "该过滤在服务端内存里完成（要按每行自己的时限比较，无法下推 SQL）；\n"
        "- `summary.truncated=true` 表示匹配行数超过 5000、统计基于前 5000 行（**如实声明**）；\n"
        "- `summary.scope` 为 `tenant`（ADM/HR 全租户）或 `self`（EMP 只看自己的）。"
    ),
)
def privacy_ledger(
    filters: PrivacyRequestFilter = Depends(_privacy_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PrivacyLedgerOut]:
    ledger_out, total, current, size = PrivacyService.ledger(
        db, principal, filters, page=page, per_page=per_page
    )
    # ⚠️ 不能用 ``paginated()``：它把 ``data`` 固定成数组，而台账的 ``data`` 是对象
    #    （见 ``PrivacyLedgerOut`` 的 docstring）。分页信息仍按接口 §1.1 放在同级 ``page``。
    return ok(
        ledger_out,
        page=build_page_info(total, current, size),
        message=f"共 {total} 条匹配（统计口径见 summary）",
    )


__all__ = ["router"]
