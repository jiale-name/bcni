"""人才档案主线路由（CU-29，API-30~35 / API-64~66）。

🔴 **Router 只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果
包成统一响应。任何业务判定（谁能看哪个部门、字段怎么裁、归档要不要清向量）
**都在 Service**（架构 §3.3）。

权限模型（接口 §4 权限矩阵）
--------------------------
| 接口 | 矩阵 | 本实现 | 说明 |
| --- | --- | --- | --- |
| API-30 档案列表 | ADM/HR/MGR | ``talent:list`` | EMP 无该点 → 自动 40301（与接口备注「EMP 直接 40301」一致）|
| API-31 档案详情 | ADM/HR/MGR/EMP(本人) | **仅登录 + 数据域** | 见下方说明 ① |
| API-32 编辑档案 | ADM/HR | ``talent:edit`` | MGR/EMP 无该点 |
| API-33 归档档案 | ADM/HR | ``talent:archive`` | 不可逆动作 → Service 侧幂等 |
| API-34 数字画像 | ADM/HR/MGR/EMP(本人) | **仅登录 + 数据域** | 同 ① |
| API-35 导出档案 | ADM/HR | ``talent:export`` | 数据出库，写审计 |
| API-64 会话回看 | ADM/HR/MGR | ``search:ask`` | 见下方说明 ② |
| API-65 清空会话 | 同上 | ``search:ask`` | 见下方说明 ② |
| API-66 脱敏自检 | ADM/AIOPS | ``ai:mask:check`` | 该权限点已在种子词表中 |

说明 ①：为什么 API-31 / API-34 **不声明权限点**
----------------------------------------------
矩阵给这两条的是「EMP（**仅本人**）」，而种子里 EMP 角色**没有** ``talent:detail``
（EMP 的入口是 ``privacy:*``，见 ``seed_dev.Role``）。若强行声明 ``talent:detail``，
EMP 会一律 40301 —— 契约里「EMP 看本人档案」这条直接落空。

改用「仅登录 + 数据域」后，四类主体**逐格对齐矩阵**：

- ADM/HR（``ALL``）→ 本租户全部；
- MGR（``OWN_DEPT``）→ 本部门子树；
- EMP（``SELF``）→ ``DataScopeFilter`` 产出 ``user_id == 自己`` → **仅本人**；
- EXEC/AIOPS/AUDIT（``STATS_ONLY=4``）→ ``require_detail_access`` → **40301**。

即：**权限点做「能否进这个功能」，数据域做「能看谁」**。这两件事分开之后，
「EMP 仅本人」不再需要靠角色名硬编码，而是由与检索侧同源的
:class:`~app.core.tenant.DataScopeFilter` 统一推导（CR-11）。

说明 ②：API-64 / API-65 的口径与一处**已登记缺口**
--------------------------------------------------
权限点用 ``search:ask``，与 API-63（档案 RAG 问答）**同源**，符合矩阵
「API-64 权限同上」。同时矩阵给这两条标了 EMP ``△（仅本人）``，但本期
**刻意不对 EMP 开放**，原因：

> Redis 里的会话**不记录 owner_user_id**（value 仅 ``[{q,a}, ...]``）。
> 若对 EMP 开放「仅本人」的回看，实际效果是「同租户内拿到 session_id
> 就能读到该会话」—— 承诺的「仅本人」无法落地，等于把一条**未生效的限制**
> 写成契约。故取安全侧：先只对 ``search:ask`` 的持有者开放
> （ADM/HR/MGR），待会话绑定 owner 后再按契约放开。

⚠️ 另一处**上游缺陷**（本批登记，不在本批修）：``search_router`` 声明的是
``require_perm("talent:ask")``，而权限词表里**只有** ``search:ask``、
没有 ``talent:ask``。除 ADM（``*`` 通配）外，**HR/MGR 调 API-63 会一律 40301**，
与矩阵不符。修法是把它改成 ``search:ask``（一行），但属 CU-21 的返工，
应有独立批次 + 回归，故此处只登记。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, CommonResp, ok, paginated
from app.schemas.talent_master import (
    DEFAULT_SORT,
    MAX_FILTER_ORG_IDS,
    MAX_FILTER_TAG_CODES,
    AskSessionClearOut,
    AskSessionOut,
    MaskVerifyOut,
    MaskVerifyRequest,
    TalentArchiveOut,
    TalentDetailOut,
    TalentExportOut,
    TalentExportRequest,
    TalentListItemOut,
    TalentListQuery,
    TalentListFilters,
    TalentProfileOut,
    TalentUpdateRequest,
    education_int_to_code,
    split_csv,
)
from app.services.ask_service import AskService
from app.services.mask_verify_service import MaskVerifyService
from app.services.talent_service import TalentService

router = APIRouter(tags=["F · 人才档案与数字画像 (F-3.2 / F-3.4 / F-3.7 / F-3.9)"])

_TALENT_ID = Path(..., description="档案 ID（雪花 ID，字符串或数字均可）")
_SESSION_ID = Path(..., min_length=1, max_length=64, description="会话 ID（API-63 返回的 session_id）")


# ============================================================================
# API-30 档案列表
# ============================================================================
@router.get(
    "/talents",
    response_model=CommonResp[list[TalentListItemOut]],
    summary="API-30 档案列表（分页 + 多条件筛选）",
    dependencies=[Depends(require_perm("talent:list"))],
    description=(
        "行级可见性由 `core/tenant.py` 按当前主体的数据域自动注入，**不接受也不信任**"
        "前端传参放宽（`filters.org_ids` 超出自身范围 → 40301）。\n\n"
        "- `data_status` 默认口径（批次 23）：**ADM/HR = `0 待复核 + 1 有效`**，其余角色仅 `1 有效`；\n"
        "- 显式指定任一状态可精确筛选；查 `2 已合并` / `4 已删除` **仅 ADM/HR** 可用；\n"
        "- `top_tags` 最多 5 个；`keyword` 只做姓名模糊匹配（语义检索走 API-62）；\n"
        "- 🔴 所有 `id` 均为**字符串**（前端不得 `parseInt`）。"
    ),
)
def list_talents(
    keyword: str | None = Query(None, max_length=64, description="姓名模糊匹配（不做全文检索）"),
    org_ids: str | None = Query(
        None, alias="filters.org_ids", description=f"部门筛选，逗号分隔 ≤{MAX_FILTER_ORG_IDS} 个；必须在本部门子树内"
    ),
    education_level: int | None = Query(
        None, alias="filters.education_level", ge=0, le=99, description="学历（两位数字码，0=不限）"
    ),
    work_years_min: int | None = Query(None, alias="filters.work_years_min", ge=0, le=60),
    work_years_max: int | None = Query(None, alias="filters.work_years_max", ge=0, le=60),
    tag_codes: str | None = Query(
        None, alias="filters.tag_codes", description=f"标签筛选，逗号分隔 ≤{MAX_FILTER_TAG_CODES} 个"
    ),
    data_status: int | None = Query(
        None, alias="filters.data_status", ge=0, le=4,
        description="档案状态；默认 ADM/HR=待复核+有效，其余角色仅 1 有效",
    ),
    sort: str = Query(DEFAULT_SORT, description="白名单：updated_at / created_at / work_years；`-` 前缀为倒序"),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    principal: CurrentPrincipal = Depends(require_perm("talent:list")),
    db: Session = Depends(get_db),
) -> CommonResp[list[TalentListItemOut]]:
    query = TalentListQuery(
        keyword=keyword,
        filters=TalentListFilters(
            org_ids=split_csv(org_ids, limit=MAX_FILTER_ORG_IDS),  # type: ignore[arg-type]
            education_code=education_int_to_code(education_level),
            work_years_min=work_years_min,
            work_years_max=work_years_max,
            tag_codes=split_csv(tag_codes, limit=MAX_FILTER_TAG_CODES),
            data_status=data_status,
        ),
        sort=sort,
        page=page,
        per_page=per_page,
    )
    items, total = TalentService.list_talents(db, principal, query)
    return paginated(items, total, page=page, per_page=per_page)


# ============================================================================
# API-35 导出（静态路径**先于** `/{talent_id}` 注册，避免被路径参数吞掉）
# ============================================================================
@router.post(
    "/talents/export",
    response_model=CommonResp[TalentExportOut],
    summary="API-35 导出档案列表（数据出库，留痕）",
    dependencies=[Depends(require_perm("talent:export"))],
    description=(
        "返回 CSV **文本**（走统一响应壳，与 API-29 / API-42 同口径），"
        "内容带 UTF-8 BOM 以便 Excel 正确识别中文。\n\n"
        "- 本期仅 `format=csv`；`xlsx` 返回 40001（**不静默降级**成 CSV）；\n"
        "- 🔴 导出内容**不含联系方式等 PII** —— 数据出库须走单独的合规审批；\n"
        "- 导出行为**自身写审计**（谁、何时、导了多少行、什么筛选条件）。"
    ),
)
def export_talents(
    payload: TalentExportRequest = Body(...),
    principal: CurrentPrincipal = Depends(require_perm("talent:export")),
    db: Session = Depends(get_db),
) -> CommonResp[TalentExportOut]:
    return ok(TalentService.export(db, principal, payload))


# ============================================================================
# API-31 档案详情
# ============================================================================
@router.get(
    "/talents/{talent_id}",
    response_model=CommonResp[TalentDetailOut],
    summary="API-31 档案详情（字段级裁剪）",
    description=(
        "🔴 **越权与不存在统一 40301**（不区分状态码，防资源探测）。\n"
        "- 可见范围由数据域决定：ADM/HR 全部、MGR 本部门、EMP **仅本人**、"
        "EXEC/AIOPS/AUDIT 一律 40301；\n"
        "- `id_card` / `phone` / `email` **恒为脱敏形态**；`sensitive_visible` "
        "只表示「当前主体本来有权看明文」；\n"
        "- `with_detail=false` 只省掉履历明细，主档字段照常返回。"
    ),
)
def get_talent(
    talent_id: int = _TALENT_ID,
    with_detail: bool = Query(True, description="是否返回履历明细（列表抽屉可传 false 减小响应体）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[TalentDetailOut]:
    return ok(TalentService.detail(db, principal, talent_id, with_detail=with_detail))


# ============================================================================
# API-32 编辑档案
# ============================================================================
@router.put(
    "/talents/{talent_id}",
    response_model=CommonResp[TalentDetailOut],
    summary="API-32 编辑档案（写变更留痕）",
    dependencies=[Depends(require_perm("talent:edit"))],
    description=(
        "**只提交发生变化的字段**；每处变化写一条 `talent_change_log`（只追加），"
        "并推进 `version_no`（F-3.10）。\n\n"
        "- 加密字段（`phone` / `email`）的留痕值是**脱敏形态**（留痕证明「变了」，不暴露「变成什么」）；\n"
        "- 手机号变更**同时**更新密文与比对哈希（只改其一会造成去重口径漂移）；\n"
        "- 已合并（`data_status=2`）的档案不可编辑 → 42201；\n"
        "- 请求体出现白名单外的字段 → 40001（**不静默忽略**）。"
    ),
)
def update_talent(
    payload: TalentUpdateRequest,
    talent_id: int = _TALENT_ID,
    principal: CurrentPrincipal = Depends(require_perm("talent:edit")),
    db: Session = Depends(get_db),
) -> CommonResp[TalentDetailOut]:
    return ok(TalentService.update(db, principal, talent_id, payload))


# ============================================================================
# API-33 归档档案
# ============================================================================
@router.delete(
    "/talents/{talent_id}",
    response_model=CommonResp[TalentArchiveOut],
    summary="API-33 归档档案（软删 + 向量同步清理）",
    dependencies=[Depends(require_perm("talent:archive"))],
    description=(
        "归档 = 软删（`is_deleted=1`）+ `data_status=3` + 清掉向量派生索引。\n\n"
        "- 🔴 向量清理**失败不回滚归档**（MySQL 是权威，向量可随时重建）—— "
        "`vector_cleaned=false` 时按 `vector_note` 由运维补跑；\n"
        "- **幂等**：对已归档档案重复调用返回成功（`archived=true`），不报错 —— "
        "不可逆动作的重试必须得到「已经做好了」而不是一个失败。"
    ),
)
def archive_talent(
    talent_id: int = _TALENT_ID,
    principal: CurrentPrincipal = Depends(require_perm("talent:archive")),
    db: Session = Depends(get_db),
) -> CommonResp[TalentArchiveOut]:
    return ok(TalentService.archive(db, principal, talent_id))


# ============================================================================
# API-34 数字画像
# ============================================================================
@router.get(
    "/talents/{talent_id}/profile",
    response_model=CommonResp[TalentProfileOut],
    summary="API-34 数字画像（标签 + 维度得分 + 向量摘要）",
    description=(
        "`dimensions` 由档案字段**确定性折算**（经验厚度 / 学历水平 / 标签覆盖），"
        "不是 LLM 生成的文字。\n\n"
        "- 技能多样性维度**不出现**（TBL-14 未建模）—— ❌ 不用 0 分占位，"
        "0 分会被读成「这个人没有技能」；\n"
        "- `vector_summary` 未做真实向量化时为 `null` + `vector_note` 说明原因。"
    ),
)
def get_talent_profile(
    talent_id: int = _TALENT_ID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[TalentProfileOut]:
    return ok(TalentService.profile(db, principal, talent_id))


# ============================================================================
# API-64 会话历史回看
# ============================================================================
@router.get(
    "/talent/ask/sessions/{session_id}",
    response_model=CommonResp[AskSessionOut],
    summary="API-64 问答会话历史回看（≥10 轮）",
    dependencies=[Depends(require_perm("search:ask"))],
    description=(
        "返回该会话保留的全部轮次（存储上限 12 轮，满足接口「≥10 轮」）。\n\n"
        "- 🔴 会话存 Redis（**可丢数据**），`ttl_seconds` 告知还能活多久；\n"
        "- 🔴 `question` 是问题原文（业务数据），故**回看行为写审计**；\n"
        "- 租户隔离由 key 构造天然保证（跨租户取不到）。"
    ),
)
def get_ask_session(
    session_id: str = _SESSION_ID,
    principal: CurrentPrincipal = Depends(require_perm("search:ask")),
) -> CommonResp[AskSessionOut]:
    return ok(AskService.session_history(principal, session_id))


# ============================================================================
# API-65 清空会话
# ============================================================================
@router.delete(
    "/talent/ask/sessions/{session_id}",
    response_model=CommonResp[AskSessionClearOut],
    summary="API-65 清空问答会话",
    dependencies=[Depends(require_perm("search:ask"))],
    description=(
        "删除该会话在 Redis 中的全部轮次（**破坏性**动作 → 写审计）。\n\n"
        "⚠️ Redis 不可用时不返 500，而是 `cleared=false` + 说明："
        "明确告知「未执行清空」比一个 500 更可行动（会话本就可能已自行过期）。"
    ),
)
def clear_ask_session(
    session_id: str = _SESSION_ID,
    principal: CurrentPrincipal = Depends(require_perm("search:ask")),
) -> CommonResp[AskSessionClearOut]:
    return ok(AskService.clear_session(principal, session_id))


# ============================================================================
# API-66 脱敏自检
# ============================================================================
@router.post(
    "/talent/mask/verify",
    response_model=CommonResp[MaskVerifyOut],
    summary="API-66 脱敏自检（提交样例，返回漏检报告）",
    dependencies=[Depends(require_perm("ai:mask:check"))],
    description=(
        "用**生产同一套规则**（`core/masking.verify_outbound`）扫描提交的样例，"
        "返回漏检报告。\n\n"
        "- 🔴 **不回显原文** —— 只给序号 + 命中类型 + **脱敏后**预览"
        "（本接口的输入就是待检明文，回显等于把 PII 写进响应）；\n"
        "- 🔴 审计**只记条数与漏检数**，不记样例内容；\n"
        "- 样例少于 20 条时 `meets_minimum=false`：结论**不构成验收证据**"
        "（需求 §12.3 的下限是 20 条）。"
    ),
)
def verify_mask(
    payload: MaskVerifyRequest = Body(...),
    principal: CurrentPrincipal = Depends(require_perm("ai:mask:check")),
) -> CommonResp[MaskVerifyOut]:
    return ok(
        MaskVerifyService.verify(
            payload, tenant_id=int(principal.tenant_id), user_id=int(principal.user_id)
        )
    )
