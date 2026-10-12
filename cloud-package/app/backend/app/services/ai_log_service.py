"""AI 调用日志与成本看板（CU-12，API-88 / API-89，F-9.5，CL-7）。

本模块的两件事
============
1. **写**：:meth:`AiLogService.record` —— 每次 AI 调用后落一条 ``ai_call_log``；
2. **读**：:meth:`AiLogService.query` / :meth:`AiLogService.cost_dashboard` —— 日志列表与三维度成本看板。

🔴 写入：**失败绝不能影响业务**（fail-open，与审计日志同源）
=====================================================
``record`` **永不抛异常**，任何失败只记 ``ERROR`` 日志。理由：
一次 AI 调用已经成功（模型回了内容、业务已经用上了），
此时因为「日志表写不进去」而把整个请求变成 500，是纯粹的伤害 ——
用户丢了一次成功的结果，运维丢了一条审计线索。

⚠️ 与审计日志的一处**有意不同**：审计走**独立 Session**（避免业务回滚带走审计），
而调用日志**复用业务 Session**（同一事务）。原因：

- 审计要记「失败的事件」（登录失败、越权被拒）—— 那些场景业务必然会回滚，
  共用 Session 会把审计一起回滚掉，所以必须独立；
- 调用日志记的是「已经发生的外部调用」—— 调用本身**不可回滚**
  （token 已经烧掉了）。若业务随后回滚而日志也一起没了，
  下一次还会再烧一次 token，且成本看板永远看不到这笔钱。
  **「不可逆的外部副作用必须留下痕迹」**，故它跟着业务事务走反而更安全。

🔴 读取：``mask_applied_rate`` 的分母是「出网调用」，不是「全部调用」
=============================================================
接口 §3 明确「``scene_stage=cloud`` 的记录中 ``mask_applied`` **必须恒为 true**，
``mask_applied_rate`` 低于 1.0 即告警」。
若把本地调用（``resume_parse`` 等，本就不需要脱敏）算进分母，
该指标会长期显示 0.6~0.8，告警阈值形同虚设（狼来了）。

**分母 = ``ai_call_log.deployment = 'cloud'`` 的记录数**，
即「本次调用**实际**走了出网通道」——它是**调用时刻的事实**，
由网关调用方落库（见 ``record`` 的 ``deployment`` 参数与
:func:`app.dao.ai_dao.aggregate_outbound_mask` 里被推翻的第一版实现）。
📌 一句话记住：**合规分母绝不能从「当前配置」反推** ——
配置会变，而历史合规结论一旦能随配置漂移，「当时确实都脱敏了」这句话就不再有证据力。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cost import CostCalculator
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import ai_dao
from app.exceptions import BizError, ErrorCode
from app.models.ai import AiCallLog, PromptTemplate
from app.models.enums import AiCallStatus
from app.schemas.ai import (
    AI_CALL_DEFAULT_RANGE_DAYS,
    AI_CALL_MAX_RANGE_DAYS,
    AI_COST_DEFAULT_RANGE_DAYS,
    AiCallFilterRequest,
    AiCallLogOut,
    AiCallSummary,
    AiDeployment,
    AiScene,
    CostBucketOut,
    CostDashboardOut,
)
from app.schemas.common import normalize_per_page

logger = get_logger(__name__)


class AiLogService:
    """AI 调用日志（API-88）与成本看板（API-89）。"""

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    @staticmethod
    def record(
        *,
        tenant_id: int,
        scene: str,
        provider: str | None = None,
        model: str | None = None,
        model_version: str | None = None,
        deployment: str | None = None,
        prompt_id: int | None = None,
        prompt_version: int | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        price_in_per_1k: Decimal | float | int | None = None,
        price_out_per_1k: Decimal | float | int | None = None,
        latency_ms: int | None = None,
        first_token_ms: int | None = None,
        cache_hit: bool = False,
        fallback_used: bool = False,
        mask_applied: bool = False,
        status: int = int(AiCallStatus.SUCCESS),
        error_code: str | None = None,
        error_msg: str | None = None,
        trace_id: str | None = None,
        user_id: int | None = None,
        db: Session | None = None,
    ) -> AiCallLog | None:
        """写一条 AI 调用日志。

        :param mask_applied: 🔴 **是否经脱敏出网**。默认 ``False``，由调用链在
            **确认已脱敏**后显式置 ``True`` —— 默认 ``True`` 会让这个合规字段失去意义。
        :param deployment: 🔴 **本次调用实际走的那一路模型的部署位置**
            （``local`` / ``cloud``）。网关调用方应当直接传
            ``AiDeployment.CLOUD if result.cloud_used else AiDeployment.LOCAL`` ——
            它比谁都清楚这一路到底出没出网。
            缺省时按 **场景固有形态**（``AiScene.STAGES``）兜底；场景码不在词表内才落 ``NULL``。
            ⚠️ 这个值的意义见 :func:`app.dao.ai_dao.aggregate_outbound_mask`：
            它是脱敏率**分母的唯一依据**，且必须是调用时刻的事实
            （事后从配置反推会被配置变更追溯性改写）。
        :param db: 传入则**复用业务会话**（同一事务，见模块 docstring）；
            不传时退化为**独立会话 + 立即提交**（供无会话的后台调用方使用）。
        :return: 写入的实体；失败返回 ``None``（**不抛异常**）
        """
        try:
            cost = CostCalculator.compute(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                price_in_per_1k=price_in_per_1k,
                price_out_per_1k=price_out_per_1k,
            )
        except Exception as exc:  # noqa: BLE001 — 成本算不出来不能阻止记录调用事实
            logger.error("成本计算失败，按 0 记录 scene=%s err=%s", scene, exc)
            cost = Decimal("0")

        log = AiCallLog(
            tenant_id=int(tenant_id),
            trace_id=clip(trace_id, 64),
            user_id=int(user_id) if user_id is not None else _user_id_from_context(),
            scene=clip(scene, 32),
            # 出网事实：调用方声明优先，缺省按场景固有形态兜底（见 :param deployment:）
            deployment=_resolve_deployment(scene, deployment),
            provider=clip(provider, 32),
            model=clip(model, 64),
            model_version=clip(model_version, 64),
            prompt_id=int(prompt_id) if prompt_id is not None else None,
            prompt_version=int(prompt_version) if prompt_version is not None else None,
            prompt_tokens=max(0, int(tokens_in)),
            completion_tokens=max(0, int(tokens_out)),
            cost=cost,
            latency_ms=int(latency_ms) if latency_ms is not None else None,
            first_token_ms=int(first_token_ms) if first_token_ms is not None else None,
            cache_hit=1 if cache_hit else 0,
            fallback_used=1 if fallback_used else 0,
            mask_applied=1 if mask_applied else 0,
            status=int(status),
            error_code=clip(error_code, 64),
            # 🔴 错误信息截断到 512（列宽）：**不写入 prompt / 回复正文** ——
            # 那可能含未脱敏的个人信息，而本表是「元数据表」（接口 §3 备注：
            # AIOPS 只能看元数据与日志，不含业务数据明文）。
            error_msg=clip(error_msg, 512),
        )

        try:
            if db is not None:
                ai_dao.insert_call_log(db, log)
                return log
            # 无会话：自建独立事务（不抛异常，失败只记日志）
            from app.dao.session import SessionLocal

            own = SessionLocal()
            try:
                ai_dao.insert_call_log(own, log)
                own.commit()
                return log
            finally:
                own.close()
        except Exception as exc:  # noqa: BLE001 — fail-open：调用已发生，日志写不进不该升级为故障
            logger.error(
                "AI 调用日志写入失败（已忽略）scene=%s model=%s trace=%s err=%s",
                scene,
                model,
                trace_id,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # API-88 查询
    # ------------------------------------------------------------------
    @classmethod
    def query(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: AiCallFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[AiCallLogOut], int, AiCallSummary, int, int]:
        """日志列表 + 汇总。

        :raises BizError: 区间跨度超 90 天 → **40001**，
            ``data`` 带 ``max_days`` / ``requested_days``（接口 §3 的失败响应示例）
        """
        start, end = cls.resolve_range(filters, default_days=AI_CALL_DEFAULT_RANGE_DAYS)
        size = normalize_per_page(per_page)
        current = max(1, int(page))

        rows = ai_dao.list_call_logs(
            db,
            principal.tenant_id,
            start=start,
            end=end,
            scene=filters.scene,
            model=filters.model,
            status=filters.status,
            cache_hit=filters.cache_hit,
            mask_applied=filters.mask_applied,
            user_id=filters.user_id,
            trace_id=filters.trace_id,
            limit=size,
            offset=(current - 1) * size,
        )
        total = ai_dao.count_call_logs(
            db,
            principal.tenant_id,
            start=start,
            end=end,
            scene=filters.scene,
            model=filters.model,
            status=filters.status,
            cache_hit=filters.cache_hit,
            mask_applied=filters.mask_applied,
            user_id=filters.user_id,
            trace_id=filters.trace_id,
        )
        summary = cls.summary(db, principal.tenant_id, start=start, end=end)
        prompt_codes = cls._prompt_codes(db, principal.tenant_id, rows)
        return (
            [cls.to_out(row, prompt_codes=prompt_codes) for row in rows],
            total,
            summary,
            current,
            size,
        )

    # ------------------------------------------------------------------
    # API-89 成本看板
    # ------------------------------------------------------------------
    @classmethod
    def cost_dashboard(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        page_filters: AiCallFilterRequest | None = None,
    ) -> CostDashboardOut:
        """三维度成本看板（场景 / 模型 / 日期）。

        ⚠️ 三个维度由**同一段 SQL 骨架**产出（``ai_dao._aggregate``），
        故三者的 ``cost_yuan`` 之和必然等于 ``total.cost_total_yuan``。
        若哪天有人给某个维度单独写了一句查询，这条保证就断了 ——
        而现象是「场景维度 100 元、模型维度 98 元」，用户先发现。
        """
        filters = page_filters or AiCallFilterRequest()
        resolved_start, resolved_end = cls.resolve_range(
            filters, default_days=AI_COST_DEFAULT_RANGE_DAYS, start=start, end=end
        )
        total = cls.summary(db, principal.tenant_id, start=resolved_start, end=resolved_end)

        def _buckets(rows: list[dict[str, Any]], *, translate: bool) -> list[CostBucketOut]:
            out: list[CostBucketOut] = []
            for row in rows:
                key = str(row["key"]) if row["key"] is not None else "(未记录)"
                out.append(
                    CostBucketOut(
                        key=key,
                        label=AiScene.label_of(key) if translate else key,
                        calls=int(row["calls"]),
                        tokens_in=int(row["tokens_in"]),
                        tokens_out=int(row["tokens_out"]),
                        cost_yuan=row["cost_yuan"],
                        avg_latency_ms=int(row["avg_latency_ms"]),
                    )
                )
            return out

        return CostDashboardOut(
            start_time=resolved_start,
            end_time=resolved_end,
            total=total,
            by_scene=_buckets(
                ai_dao.aggregate_cost_by_scene(
                    db, principal.tenant_id, start=resolved_start, end=resolved_end
                ),
                translate=True,
            ),
            by_model=_buckets(
                ai_dao.aggregate_cost_by_model(
                    db, principal.tenant_id, start=resolved_start, end=resolved_end
                ),
                translate=False,
            ),
            by_time=_buckets(
                ai_dao.aggregate_cost_by_time(
                    db, principal.tenant_id, start=resolved_start, end=resolved_end
                ),
                translate=False,
            ),
        )

    # ------------------------------------------------------------------
    # 汇总（API-88 的 summary / API-89 的 total 共用）
    # ------------------------------------------------------------------
    @classmethod
    def summary(
        cls, db: Session, tenant_id: int, *, start: datetime, end: datetime
    ) -> AiCallSummary:
        """汇总指标 + 两条合规标记（成本 / 脱敏）。"""
        raw = ai_dao.aggregate_call_summary(db, tenant_id, start=start, end=end)
        cloud_calls, masked_calls = ai_dao.aggregate_outbound_mask(
            db, tenant_id, start=start, end=end, deployment=AiDeployment.CLOUD
        )
        # 没有出网调用 → 脱敏率记 1.0（**没有出网 = 没有违规**，而不是「无法判定」）
        mask_rate = 1.0 if cloud_calls == 0 else round(masked_calls / cloud_calls, 4)
        cost_total: Decimal = raw["cost_total_yuan"]
        return AiCallSummary(
            calls=int(raw["calls"]),
            cost_total_yuan=cost_total,
            avg_latency_ms=int(raw["avg_latency_ms"]),
            failed=int(raw["failed"]),
            mask_applied_rate=mask_rate,
            cache_hit_rate=float(raw["cache_hit_rate"]),
            cloud_calls=cloud_calls,
            cost_alert=float(cost_total) > float(settings.AI_COST_ALERT_YUAN),
            mask_alert=cloud_calls > 0
            and mask_rate < float(settings.AI_MASK_APPLIED_RATE_FLOOR),
        )

    # ------------------------------------------------------------------
    # 区间解析
    # ------------------------------------------------------------------
    @staticmethod
    def resolve_range(
        filters: AiCallFilterRequest,
        *,
        default_days: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """解析查询区间：缺省「最近 N 天」，上限 90 天。

        🔴 跨度校验放在**服务层**（而不是 Pydantic 校验器）：
        接口 §3 要求超限时返回 ``data = {max_days, requested_days}``，
        而 Pydantic 校验失败走全局 ``RequestValidationError`` 处理器，
        ``data`` 里只有 ``fields[]`` —— 形状对不上文档。
        这是「框架的默认行为与文档契约不一致」时，选**文档**的一例。

        ⚠️ 默认区间用**请求时刻**计算（而不是在 Schema 里写 ``default_factory``）：
        模型若被缓存或跨日复用，默认区间会固定在「导入时刻的 N 天前」，
        表现为「今天的调用查不到」——非常隐蔽。

        ⚠️ 另有一条**实测得到**的 1 秒级可见性延迟（不是丢数据，别去查库）：
        ``created_at`` 是 ``DATETIME``（**无小数秒**），MySQL 对亚秒值是**四舍五入**的
        （实测 ``…10:00:00.5`` → ``10:00:01``）。于是「刚发生 0.6 秒的那次调用」
        落库后可能比 ``now()`` 大 1 秒，而默认 ``end`` 取的正是 ``now()`` ——
        它在这一瞬间查不到，下一秒就有了。监控页只需知道「不是丢数据」即可；
        ❌ 不要为此把默认 ``end`` 加 1 秒：那会让「恰好 90 天」的边界校验
        变成「90 天 + 1 秒」，把一个精确口径改成模糊口径。
        """
        resolved_start = start or filters.start_time
        resolved_end = end or filters.end_time
        now = datetime.now()
        if resolved_end is None:
            resolved_end = now
        if resolved_start is None:
            resolved_start = resolved_end - timedelta(days=default_days)

        span = (resolved_end - resolved_start).days
        if span > AI_CALL_MAX_RANGE_DAYS:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"查询区间不得超过 {AI_CALL_MAX_RANGE_DAYS} 天",
                {
                    "max_days": AI_CALL_MAX_RANGE_DAYS,
                    "requested_days": span,
                    "hint": "本表按月分区，跨月查询无法被分区裁剪，请收窄区间后重试",
                },
            )
        if resolved_start > resolved_end:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "start_time 不能晚于 end_time",
                {"fields": [{"field": "start_time", "message": "不能晚于 end_time"}]},
            )
        return resolved_start, resolved_end

    # ------------------------------------------------------------------
    # 出参装配
    # ------------------------------------------------------------------
    @staticmethod
    def _prompt_codes(db: Session, tenant_id: int, rows: list[AiCallLog]) -> dict[int, str]:
        """批量反查 ``prompt_id → prompt_code``（避免 N+1）。

        ⚠️ 用**一次** ``select ... in_()`` 而不是逐行查：日志列表默认 20 条、
        上限 100 条，逐行查就是 100 次往返（这是「日志页越用越慢」的典型成因）。

        ⚠️ 反查时**不过滤** ``is_deleted`` / ``status``：历史调用日志指向的
        Prompt 可能早已停用，但仍然必须能显示出它当时的编码 ——
        否则「这条日志用的是哪个 Prompt」在 Prompt 停用后就永久丢失了。
        """
        ids = {int(row.prompt_id) for row in rows if row.prompt_id is not None}
        if not ids:
            return {}
        table = PromptTemplate.__table__
        stmt = select(table.c.id, table.c.prompt_code).where(
            table.c.tenant_id == int(tenant_id), table.c.id.in_(sorted(ids))
        )
        return {int(pid): str(code) for pid, code in db.execute(stmt).all()}

    @classmethod
    def to_out(
        cls, row: AiCallLog, *, prompt_codes: dict[int, str] | None = None
    ) -> AiCallLogOut:
        prompt_code: str | None = None
        if row.prompt_id is not None and prompt_codes:
            prompt_code = prompt_codes.get(int(row.prompt_id))
        return AiCallLogOut(
            id=row.id,
            trace_id=row.trace_id,
            scene=row.scene,
            scene_name=AiScene.label_of(row.scene),
            # 🔴 ``scene_stage`` 取**本次调用的实际部署位置**（``deployment`` 列），
            #    仅在该列为 NULL 时才退回场景固有形态。
            #    与「按场景猜」的区别在混合链路上很实在：``talent_ask`` 的云端主路挂掉、
            #    落到本地备用时，这次调用**没有出网**，前端就不该给它打「云端」徽标 ——
            #    否则合规核查者会看到一个「云端但未脱敏」的假阳性。
            scene_stage=row.deployment or AiScene.stage_of(row.scene),
            model=row.model,
            provider=row.provider,
            prompt_id=row.prompt_id,
            prompt_code=prompt_code,
            prompt_version=row.prompt_version,
            tokens_in=int(row.prompt_tokens),
            tokens_out=int(row.completion_tokens),
            cost_yuan=_dec(row.cost),
            latency_ms=row.latency_ms,
            first_token_ms=row.first_token_ms,
            cache_hit=bool(row.cache_hit),
            fallback_used=bool(row.fallback_used),
            mask_applied=bool(row.mask_applied),
            status=int(row.status),
            status_name=AiCallStatus.name_of(int(row.status)),
            error_code=row.error_code,
            error_msg=row.error_msg,
            created_at=row.created_at,
        )


# ============================================================================
# 工具
# ============================================================================
def clip(text: Any, limit: int) -> str | None:
    """截断到列宽（``None`` 原样返回）。"""
    if text is None:
        return None
    s = str(text)
    return s if len(s) <= limit else s[:limit]


def _resolve_deployment(scene: str, declared: str | None) -> str | None:
    """确定一次调用的部署位置：**调用方声明 > 场景固有形态 > ``None``**。

    - ``declared`` 来自网关调用方（它知道这一路到底是云端还是本地），**永远优先**；
    - 缺省时按 :attr:`app.schemas.ai.AiScene.STAGES`（场景的固有部署形态）兜底 ——
      这只是一个**合理默认**，对混合主备链上的偶发降级并不准确，
      故网关调用方应当显式传参；
    - 场景码连词表都不认识时返回 ``None``（不猜），该记录**不进脱敏率分母**。
    """
    if declared:
        return clip(declared, 16)
    # ``stage_of`` 对 ``None`` / 未知场景都返回 ``None``（不抛异常，见 AiScene.label_of 的说明）
    return AiScene.stage_of(scene)


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _user_id_from_context() -> int | None:
    """从请求上下文取当前用户（无请求上下文时返回 ``None``）。

    ⚠️ 上下文里存的是**字符串**（``context.get_user_id`` 的返回值是 ``str``），
    这里显式转 ``int`` —— 否则会往 ``BIGINT`` 列里写字符串，
    MySQL 能隐式转换（不报错），但一旦出现非数字值就会静默变成 0。
    """
    try:
        from app.core.context import get_user_id

        raw = get_user_id()
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001 — 上下文缺失不应影响日志写入
        return None
