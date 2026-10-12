"""AI 中台路由（API-81 ~ API-89，模块 O「AI 中台」，F-9.1 / F-9.2 / F-9.5 / F-1.6）。

🔴 **Router 层只做三件事**：声明路径与权限依赖、绑定 Schema、把 Service 结果包成统一响应。

权限模型（接口 §4 权限矩阵：ADM / EXEC / AIOPS / AUDIT 七列）
==========================================================
| 接口 | 矩阵要求 | 本实现的权限码 | 说明 |
| --- | --- | --- | --- |
| API-81 模型配置列表 | AIOPS | ``ai:model:list`` | ADM 在 ``require_perm`` 内短路 |
| API-82 修改模型配置 | AIOPS | ``ai:model:update`` | |
| API-83 模型健康状态 | **ADM / AIOPS** | ``ai:model:health`` | 与 81 分开：**能看配置 ≠ 能看探测结果** |
| API-84 Prompt 列表 | AIOPS | ``ai:prompt:list`` | |
| API-85 新建 Prompt | AIOPS | ``ai:prompt:create`` | |
| API-86 修改 Prompt | AIOPS | ``ai:prompt:update`` | |
| API-87 回滚 Prompt | AIOPS | ``ai:prompt:rollback`` | 回滚是高危动作，单独授权 |
| API-88 AI 调用日志 | ADM / AIOPS / **AUDIT** | ``ai:log:view`` | AUDIT 已由种子的 ``ai:log:view`` 授予 |
| API-89 成本看板 | **ADM / EXEC / AIOPS** | ``ai:cost:view`` | EXEC 是唯一能看聚合看板的非运维角色 |

⚠️ 三个**刻意不做**的事
----------------------

1. **不套 ``DataScopeFilter``。** 模型配置 / Prompt / 调用日志都是**租户级基础设施与流水**，
   表里没有 ``org_id``。AUDIT 的 ``data_scope=4``（仅统计值）与 EXEC 的 ``data_scope=4``
   若在这一层被 ``require_detail_access`` 拦下，矩阵里明明给了 ✓ 的两个角色会一律 40301。
   租户隔离仍由 DAO 的 ``tenant_id`` 强制注入（跨租户 → 查不到 → 40401 口径）。

2. **不给 ``ai_call_log`` 任何写/删接口。** 它由 :class:`~app.services.ai_log_service.AiLogService`
   在真实调用点写入，留存靠**按月分区 + ``DROP PARTITION``**（数据库 §4.7）。
   与审计日志同一条推理：HTTP 写入口 = 「伪造/抹掉调用痕迹」的正门。

3. **``/ai/models/health`` 声明在 ``/ai/models/{model_id}`` 之前。**
   两条路径的形状相同（``/ai/models/health`` 会被 ``/ai/models/{model_id}`` 匹配到）。
   Starlette 在**方法不匹配**时会记住「部分匹配」并继续往后找**完全匹配**，
   所以顺序写反也「碰巧能用」—— 但这依赖的是框架内部行为：
   一旦将来给 ``{model_id}`` 加上 GET，``/ai/models/health`` 就会变成
   「把 ``health`` 当 ID 去查库」并稳定 40401。故按**具体路径在前**的惯例显式排好。

⚠️ 两处与接口文档的**有意偏离**（已登记进评审文档 §7.3）
------------------------------------------------------

① **时间区间参数名用 ``start_time`` / ``end_time``**，而不是 API-88 参数表里的
   ``filters.start / end``。理由：审计日志（API-28，CU-08 已过评审）用的就是
   ``start_time`` / ``end_time``，同一语义的参数在同一套 API 里应当同名 ——
   两套命名并存的结果必然是「前端按 A 写、后端按 B 收」，而**未知 query 参数会被静默忽略**
   （不是 400），现象是「筛选了时间但结果没变」，最难排查的一类问题。

② **API-82 / API-86 用 ``PUT`` 方法**（与接口 §2.2 的方法列一致），但请求体是
   **部分更新语义**（未传字段不变，全部可选、且至少传一项）。
   不改用 ``PATCH`` 是因为方法列已经写进文档与联调清单；
   语义差异在请求体契约（:class:`~app.schemas.ai.ModelConfigUpdateRequest`）里
   用 `model_fields_set` 显式表达，并在接口描述里写清。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.ai import (
    AiCallFilterRequest,
    AiCallLogListResp,
    AiScene,
    CostDashboardOut,
    ModelConfigOut,
    ModelConfigUpdateRequest,
    ModelHealthOut,
    PromptCreateRequest,
    PromptOut,
    PromptRollbackRequest,
    PromptScene,
    PromptUpdateRequest,
    call_log_page,
)
from app.schemas.common import CommonResp, ok, paginated
from app.services.ai_log_service import AiLogService
from app.services.ai_service import AiService
from app.services.prompt_service import PromptService

logger = get_logger(__name__)

router = APIRouter(tags=["O · AI 中台 (F-9)"])


# ============================================================================
# 内部：参数校验失败的统一出口
# ============================================================================
def _param_invalid(fields: list[dict[str, str]]) -> BizError:
    """把校验错误转成 **40001 + ``data.fields[]``**（接口 §1.5）。

    手工 ``model_validate`` 抛的是 ``pydantic.ValidationError``，
    **不是** FastAPI 的 ``RequestValidationError``，因此**不经过**全局异常处理器 ——
    直接冒出去会变成 50000。故与 :mod:`app.routers.audit_router` 完全同口径地显式转换，
    并复用 :func:`app.exceptions.validation_fields` 保证 ``fields[]`` 形状逐字一致。
    """
    return BizError(ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields})


def _check_scene(scene: str | None, labels: dict[str, str], *, field: str = "scene") -> str | None:
    """场景码白名单校验（不合法 → **40001**）。

    为什么必须拦：``scene`` 列是 ``VARCHAR`` 自由文本，拼错一个字母**不会报错**，
    只会查出 0 条 —— 使用者会得出「这段时间没有这类调用」的错误结论，
    而不是「我拼错了」。故把猜测变成明确提示（同 ``AuditAction`` 处理）。
    """
    if scene is not None and scene not in labels:
        raise _param_invalid(
            [
                {
                    "field": field,
                    "message": f"未知的 {field}={scene!r}；合法取值：{', '.join(sorted(labels))}",
                    "type": "value_error",
                }
            ]
        )
    return scene


# ============================================================================
# API-81 模型配置列表
# ============================================================================
@router.get(
    "/ai/models",
    response_model=CommonResp[list[ModelConfigOut]],
    summary="API-81 模型配置列表（AIOPS）",
    dependencies=[Depends(require_perm("ai:model:list"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`（接口 §1.1）。按 `scene` / `priority` 升序。\n\n"
        "- 🔴 `deployment` 是**出网判定的唯一依据**：`cloud` 表示这一路会把数据送到租户网络之外，"
        "必须在调用前完成脱敏（见 `mask_applied`）；`local` 走本机 Ollama / bge-m3，不出网；\n"
        "- `priority` 是主备顺序（`1` = 主路），网关按它依次尝试，**主路失败降级到备路**；\n"
        "- `price_in_per_1k` / `price_out_per_1k` 单位是**元 / 千 token**，本地模型为 `0`"
        "（是默认值，不是「忘了配」）；\n"
        "- 本表**没有行级数据域**：跨租户查询一律查不到（租户条件由服务端强制注入）。"
    ),
)
def list_models(
    scene: str | None = Query(None, max_length=32, description="按场景码筛选"),
    status: int | None = Query(None, ge=0, le=1, description="0 停用 1 启用"),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    # ⚠️ 故意**不写 le=100**：接口 §1.1 约定「超出按 100 处理并在 page 中回显实际值」，
    #    写 le=100 会让 `?per_page=999` 变成 422，与文档约定相反（与 /users 同处理）。
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[ModelConfigOut]]:
    _check_scene(scene, AiScene.LABELS)
    items, total, current, size = AiService.list_models(
        db, principal, scene=scene, status=status, page=page, per_page=per_page
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-83 模型健康与熔断（⚠️ 必须声明在 /ai/models/{model_id} 之前，见模块 docstring）
# ============================================================================
@router.get(
    "/ai/models/health",
    response_model=CommonResp[ModelHealthOut],
    summary="API-83 模型健康与熔断状态（ADM / AIOPS）",
    dependencies=[Depends(require_perm("ai:model:health"))],
    description=(
        "**同步探测**每条启用/停用配置的连通性，并汇总熔断状态。\n\n"
        "- 🔴 **探测不跑真实推理**：只发一次极轻量的探活请求（Ollama `GET /api/tags`、"
        "OpenAI 兼容 `GET /models`）。发真的生成请求会让「看一眼健康状态」变成一次计费调用；\n"
        "- `healthy` 与 `blocked` 是**两件事**：前者是这一路当前能不能用，"
        "后者是它已被熔断器跳过（在 `cooldown_remaining_seconds` 内不会有任何请求打到它，"
        "即使它其实已经恢复）；\n"
        "- `all_primary_healthy` 的口径是**每个场景至少有一路主模型健康**，"
        "而不是「全部模型都健康」—— 备用链路挂掉不影响任何功能，"
        "按后者判定会让健康检查长期飘红，于是被运维学会忽略（那才是真正的风险）；\n"
        "- `breaker_backend_ok=false` 表示熔断器后端（Redis）不可用，此时熔断状态**全部视为未熔断**"
        "（fail-open）—— 与限流同一条推理：基础设施抖动不得升级为业务不可用。"
    ),
)
def models_health(
    scene: str | None = Query(None, max_length=32, description="只探测某个场景（缺省＝全部）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[ModelHealthOut]:
    _check_scene(scene, AiScene.LABELS)
    return ok(AiService.health(db, principal, scene=scene))


# ============================================================================
# API-82 修改模型配置与主备路由
# ============================================================================
@router.put(
    "/ai/models/{model_id}",
    response_model=CommonResp[ModelConfigOut],
    summary="API-82 修改模型配置与主备路由（AIOPS）",
    dependencies=[Depends(require_perm("ai:model:update"))],
    description=(
        "⚠️ 方法为 `PUT`（接口 §2.2），但请求体是**部分更新语义**："
        "未传的字段保持不变；**至少传一项**，空对象直接 40001。\n\n"
        "- 🔴 **改 `base_url` / `model_name` 等于换了一路模型**，服务层会写审计日志"
        "（`ai_model_update`，含改前/改后快照）——否则「上周那批回答的模型是谁」无从回答；\n"
        "- 🔴 **不允许把某场景的最后一个启用模型停掉**（42201）：全部停用后该场景的调用会"
        "**静默降级**（网关返回 `degraded`），而界面上看不到任何异常，只有调用日志在涨；\n"
        "- 改动若与同场景下另一条配置撞 `model_code` → **40901**（服务层预检，不靠数据库 1062）；\n"
        "- 配置不存在 / 已软删 → **40401**。"
    ),
)
def update_model(
    payload: Annotated[ModelConfigUpdateRequest, Body(description="待修改字段（至少一项）")],
    model_id: int = Path(..., gt=0, description="模型配置 ID（雪花 ID，字符串或数字均可）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[ModelConfigOut]:
    return ok(AiService.update_model(db, principal, model_id, payload))


# ============================================================================
# API-84 Prompt 模板列表
# ============================================================================
@router.get(
    "/ai/prompts",
    response_model=CommonResp[list[PromptOut]],
    summary="API-84 Prompt 模板列表（含版本）（AIOPS）",
    dependencies=[Depends(require_perm("ai:prompt:list"))],
    description=(
        "`data` 为**数组**、分页信息在同级 `page`。**一个版本一条记录**（不是一个模板一条）——\n\n"
        "- `prompt_code` 是模板族编码，同一族内 `version` **单调递增**；"
        "「版本号越大越新」是全局依赖的性质（`ai_call_log.prompt_version` 的追溯、"
        "前端版本下拉、排序都靠它）；\n"
        "- `status`：`0` 草稿 / `1` 启用 / `2` 停用。**同一 `prompt_code` 下最多一个 `1`**"
        "（服务层强制），改版本时旧版本自动转 `2`；\n"
        "- 🔴 与 :class:`~app.schemas.ai.AiScene` **不是同一套取值**（更粗，一个 Prompt 可服务"
        "多个调用场景），这是上游文档的既有设计，本批不合并。\n\n"
        "筛选：`scene`（`extract`/`rewrite`/`ask`/`judge`）、`prompt_code`、`status`。"
    ),
)
def list_prompts(
    scene: str | None = Query(None, max_length=32, description="按 Prompt 场景筛选"),
    prompt_code: str | None = Query(None, max_length=64, description="按模板族编码筛选"),
    status: int | None = Query(None, ge=0, le=2, description="0 草稿 1 启用 2 停用"),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[list[PromptOut]]:
    _check_scene(scene, PromptScene.LABELS)
    items, total, current, size = PromptService.list_prompts(
        db,
        principal,
        scene=scene,
        prompt_code=prompt_code,
        status=status,
        page=page,
        per_page=per_page,
    )
    return paginated(items, total, page=current, per_page=size)


# ============================================================================
# API-85 新建 Prompt 模板
# ============================================================================
@router.post(
    "/ai/prompts",
    response_model=CommonResp[PromptOut],
    summary="API-85 新建 Prompt 模板（AIOPS）",
    dependencies=[Depends(require_perm("ai:prompt:create"))],
    description=(
        "`version` **固定从 1 开始**，不接受传入。\n\n"
        "- 🔴 若 `prompt_code` 已存在 → **40901**，`data` 带 `latest_version` 与最新版本 ID。"
        "**刻意不**「为了友好」自动变成「给已有族加一个版本」——"
        "调用方以为在建新模板、实际改了另一个模板的启用版本，"
        "属于「界面上什么异常都没有、但线上 Prompt 已经换了」的事故；\n"
        "- `status=1`（直接启用）时，服务层会把同族的其他版本转 `2`；\n"
        "- `remark` 写入审计 `after` 快照（本条记录的变更说明）。"
    ),
)
def create_prompt(
    payload: Annotated[PromptCreateRequest, Body(description="模板编码 / 场景 / 正文")],
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PromptOut]:
    return ok(PromptService.create_prompt(db, principal, payload))


# ============================================================================
# API-86 修改 Prompt（生成新版本，不改历史）
# ============================================================================
@router.put(
    "/ai/prompts/{prompt_id}",
    response_model=CommonResp[PromptOut],
    summary="API-86 修改 Prompt（生成新版本，不改历史）（AIOPS）",
    dependencies=[Depends(require_perm("ai:prompt:update"))],
    description=(
        "以 `{id}` 指向的版本为**源版本**，生成 `version = 该族最大值 + 1` 的新版本。\n\n"
        "- 🔴 **永不原地改写历史版本**：历史版本的正文一旦被改，"
        "所有引用过它的 `ai_call_log` 记录都会指向一段从未被真正执行过的 Prompt，"
        "「这条回答当时是用什么提示词生成的」这个问题就再也答不了；\n"
        "- 契约里**没有** `prompt_code` / `version` 字段且 `extra=\"forbid\"`："
        "「改编码」等于换了模板族（历史版本全部失去归属），「改版本号」等于篡改历史 ——"
        "多传即 **40001**（而不是被静默忽略）；\n"
        "- 新正文与源版本**完全一致** → **42201**（无意义操作，不产生新版本）；\n"
        "- `status` 缺省**继承源版本**。源版本不存在 / 已软删 → **40401**。"
    ),
)
def update_prompt(
    payload: Annotated[PromptUpdateRequest, Body(description="新正文 + 可选状态 / 变更说明")],
    prompt_id: int = Path(..., gt=0, description="**源版本** ID（雪花 ID，字符串或数字均可）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PromptOut]:
    return ok(PromptService.update_prompt(db, principal, prompt_id, payload))


# ============================================================================
# API-87 回滚到指定版本
# ============================================================================
@router.post(
    "/ai/prompts/{prompt_id}/rollback",
    response_model=CommonResp[PromptOut],
    summary="API-87 回滚到指定版本（AIOPS）",
    dependencies=[Depends(require_perm("ai:prompt:rollback"))],
    description=(
        "🔴 **回滚不是「把旧版本重新置为启用」，而是以旧版本正文追加一个新版本**"
        "（`version = max + 1`）并把新版本置为启用。\n\n"
        "为什么不做那件「最自然」的事：\n\n"
        "1. 版本号会**倒退**，「版本号越大越新」这条性质失效，"
        "而 `ai_call_log.prompt_version` 的追溯、前端版本下拉、排序全都依赖它；\n"
        "2. 「v3 在 9 月启用过、又在 11 月重新启用」这件事在库里**看不出来**"
        "（只有 `updated_at` 变了，而它是状态字段的更新时间，不是业务事件时间）；\n"
        "3. 后来若有人误停用 v3，无法区分「哪次停用是哪次启用之后的」。\n\n"
        "故回滚天然留下完整时间线：`v5 = v3 的正文`，审计 `before/after` 里带上「回滚自 v3」。\n\n"
        "- `{id}` 只用于定位模板族（传哪个版本都行）；\n"
        "- `target_version` 在该族中不存在 → **40401**，`data.available_versions` 列出全部可选版本；\n"
        "- 目标版本正文与当前最新版本一致 → **42201**（无意义操作）。"
    ),
)
def rollback_prompt(
    payload: Annotated[PromptRollbackRequest, Body(description="目标版本号 + 回滚原因")],
    prompt_id: int = Path(..., gt=0, description="发起回滚的版本 ID（仅用于定位模板族）"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[PromptOut]:
    return ok(PromptService.rollback_prompt(db, principal, prompt_id, payload))


# ============================================================================
# API-88 AI 调用日志（走与审计日志同一套「显式 Query + 依赖收进模型」路径）
# ============================================================================
def _call_log_filters(
    scene: str | None = Query(None, max_length=32, description="场景码，如 `resume_parse` / `talent_ask`"),
    model: str | None = Query(None, max_length=64, description="模型名（`model_name` 或 `model_code`）"),
    status: int | None = Query(None, ge=0, le=1, description="0 失败 1 成功"),
    cache_hit: bool | None = Query(None, description="是否命中缓存"),
    mask_applied: bool | None = Query(None, description="**是否经脱敏出网**（合规核查用）"),
    user_id: int | None = Query(None, description="按发起人 ID 筛选"),
    trace_id: str | None = Query(None, max_length=64, description="按 trace_id 精确匹配（跨端串联）"),
    start_time: str | None = Query(
        None, description="起始时间 `YYYY-MM-DD HH:mm:ss`（含）；缺省＝最近 7 天"
    ),
    end_time: str | None = Query(None, description="截止时间 `YYYY-MM-DD HH:mm:ss`（含）；缺省＝当前时刻"),
) -> AiCallFilterRequest:
    """把 query 参数收进 :class:`AiCallFilterRequest`，手工转 **40001**（理由见 :func:`_param_invalid`）。

    ⚠️ 时间参数声明为 ``str`` 而**不是** ``datetime``：接口 §1.1 约定的格式是
    ``YYYY-MM-DD HH:mm:ss``（空格分隔、本地时区）。交给 FastAPI 直接解析 ``datetime``
    会**同时接受** ISO-8601（``T`` 分隔）等格式，比约定的更宽 ——
    入口放行、下游按另一套口径理解，是跨端时间戳错乱的经典成因。

    🔴 ``start_time`` / ``end_time`` 的命名偏离见模块 docstring ①。
    """
    try:
        return AiCallFilterRequest(
            scene=scene,
            model=model,
            status=status,
            cache_hit=cache_hit,
            mask_applied=mask_applied,
            user_id=user_id,
            trace_id=trace_id,
            start_time=start_time,
            end_time=end_time,
        )
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("AI 调用日志筛选参数校验失败 %s", fields)
        raise _param_invalid(fields) from exc


@router.get(
    "/ai/call-logs",
    response_model=AiCallLogListResp,
    summary="API-88 AI 调用日志（ADM / AIOPS / AUDIT）",
    dependencies=[Depends(require_perm("ai:log:view"))],
    description=(
        "`data` 为**数组**、`page` 与 `summary` **同级**（接口 §3）。按调用时间**倒序**。\n\n"
        "- 🔴 **时间区间默认最近 7 天、上限 90 天**：本表按月分区，跨月区间无法被分区裁剪，"
        "放开时间窗会让单次查询退化为全分区扫描。**超限返回 40001**"
        "（`data.max_days` / `data.requested_days`），不是静默截断；\n"
        "- `summary.mask_applied_rate` 是**合规监控的唯一抓手**："
        "`scene_stage=cloud` 的记录中 `mask_applied` **必须恒为 `true`**；"
        "⚠️ 该比率的**分母只含「出网调用」**（= `deployment='cloud'` 的记录，即**本次调用"
        "实际走了出网通道**，由调用方在调用时刻落库 —— 不是按当前配置反推，"
        "否则配置变更会追溯性改写历史合规结论）。`summary.cloud_calls` 让分母显式可见；\n"
        "- `summary.cost_alert` / `summary.mask_alert` 只**标记**不告警"
        "（接口 §3 备注：「本接口只呈报不告警」），阈值取自配置；"
        "`mask_alert=true` 意味着**存在未脱敏出网的调用**，属合规事件；\n"
        "- 🔴 响应**不含任何档案内容或问题原文**（AIOPS 只能看元数据与业务流水）："
        "`AiCallLogOut` 结构上就没有这类字段，比「记得别返回」可靠；\n"
        "- `prompt_code` 由 `prompt_id` 批量反查，**历史模板已停用也能显示**"
        "（否则老日志的 Prompt 列会变成空白，追溯链断裂）；\n"
        "- 跨租户查询一律查不到（租户条件由服务端强制注入）。"
    ),
)
def list_call_logs(
    filters: AiCallFilterRequest = Depends(_call_log_filters),
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    per_page: int = Query(20, ge=1, description="每页条数；超过 100 按 100 处理"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AiCallLogListResp:
    items, total, summary, current, size = AiLogService.query(
        db, principal, filters, page=page, per_page=per_page
    )
    return call_log_page(items, total, page=current, per_page=size, summary=summary)


# ============================================================================
# API-89 Token 成本看板
# ============================================================================
@router.get(
    "/ai/cost-dashboard",
    response_model=CommonResp[CostDashboardOut],
    summary="API-89 Token 成本看板（ADM / EXEC / AIOPS）",
    dependencies=[Depends(require_perm("ai:cost:view"))],
    description=(
        "场景 / 模型 / 时间**三维度**成本聚合，默认区间**最近 30 天**（比调用日志长 —— "
        "看板关心趋势，不看单次）。\n\n"
        "- 🔴 **三个维度的 `cost_yuan` 之和都等于 `total.cost_total_yuan`**"
        "（同一区间、同一租户）：三者由同一段 SQL 骨架产出，不是三份手写查询 ——"
        "否则「场景维度 100 元、模型维度 98 元」这类差异会先被使用者发现；\n"
        "- `by_time` 按**天**聚合，`key` 是 `YYYY-MM-DD`；`by_scene` 的 `label` 会翻成中文，"
        "`by_model` / `by_time` 的 `label` 即原值；\n"
        "- `EXEC`（`data_scope=4` 仅统计值）**可访问本接口** —— 它是聚合看板不含明细，"
        "与本接口「只呈报聚合」的定位一致；`EXEC` 访问 API-88 明细日志仍会 40301；\n"
        "- 金额与时间桶里的空值归入 `(未记录)`，不静默丢弃（丢一条会让合计对不上）。\n\n"
        "⚠️ 本接口**不复用** API-88 的 `summary` 结构以外的东西：`total` 字段名与"
        "`summary` 一致（`calls` / `cost_total_yuan` / `avg_latency_ms`），便于前端同一套渲染。"
    ),
)
def cost_dashboard(
    scene: str | None = Query(None, max_length=32, description="只统计某个场景"),
    model: str | None = Query(None, max_length=64, description="只统计某个模型"),
    start_time: str | None = Query(None, description="起始时间 `YYYY-MM-DD HH:mm:ss`（含）；缺省＝最近 30 天"),
    end_time: str | None = Query(None, description="截止时间 `YYYY-MM-DD HH:mm:ss`（含）；缺省＝当前时刻"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CommonResp[CostDashboardOut]:
    """成本看板。

    ⚠️ 这里**复用** :func:`_call_log_filters` 的同一条校验路径（构造 ``AiCallFilterRequest``
    而非再写一遍），保证「日志接口拦得住、看板接口拦不住」这类不对称不会出现：
    ``scene`` 拼错在两个接口上必须**报同一种错**，否则使用者会以为是数据缺失。
    """
    try:
        filters = AiCallFilterRequest(scene=scene, model=model, start_time=start_time, end_time=end_time)
    except ValidationError as exc:
        fields = validation_fields(exc.errors())
        logger.warning("成本看板筛选参数校验失败 %s", fields)
        raise _param_invalid(fields) from exc
    return ok(AiLogService.cost_dashboard(db, principal, page_filters=filters))


__all__ = ["router"]
