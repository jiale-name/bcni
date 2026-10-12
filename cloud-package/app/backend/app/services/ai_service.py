"""模型网关配置与会话健康（CU-10，API-81 / API-82 / API-83，F-9.1）。

本模块负责三件事，以及一件**被其它模块复用**的事
============================================

| API | 行为 |
| --- | --- |
| API-81 | 列出模型配置（按场景 + 主备顺序） |
| API-82 | 修改配置与主备路由（**配置变更必须入审计**） |
| API-83 | 模型健康与熔断状态（**探测，不跑真实推理**） |
| — | :func:`build_targets` —— 把 ``ai_model_config`` 行转成
  :class:`~app.core.model_gateway.ModelTarget`，供**所有** AI 调用方复用 |

🔴 为什么 :func:`build_targets` 放在这里（而不是各调用点自己写）
==========================================================
「从配置表取可用模型并按 priority 排序」这件事，CU-12（调用日志）、CU-14/15（抽取）、
CU-20（检索）、CU-21（问答）**全都要做**。各写一遍的后果不是代码重复，
而是**口径漂移**：某一处忘了过滤 ``status=1``（于是停用的模型还在被调用）、
或忘了 ``is_deleted=0``（于是被删的配置复活）。故只留一个入口。

⚠️ API-83 **不做真实推理**（见 ``model_gateway.probe`` 的说明）：
「模型能不能真的生成」由熔断器在实际调用链上回答，
而健康探测若真的发一次生成请求，被前端轮询时就是一个持续压测。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.model_gateway import CircuitBreaker, ModelGateway, ModelTarget
from app.core.tenant import CurrentPrincipal
from app.dao import ai_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError
from app.models.ai import AiModelConfig
from app.models.enums import EnabledStatus
from app.schemas.ai import (
    AiDeployment,
    AiProvider,
    AiScene,
    ModelConfigOut,
    ModelConfigUpdateRequest,
    ModelHealthOut,
    ModelTargetOut,
)
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class AiService:
    """模型网关配置（API-81/82）与健康状态（API-83）。"""

    # ------------------------------------------------------------------
    # API-81 列表
    # ------------------------------------------------------------------
    @classmethod
    def list_models(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        scene: str | None = None,
        status: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ModelConfigOut], int, int, int]:
        """返回 ``(items, total, page, per_page)``。

        ⚠️ **不套行级数据域过滤**：模型配置是**租户级基础设施配置**（没有 ``org_id``），
        与字典（CU-07）同理 —— 把它当作「人员明细」过滤会让 AIOPS 永远看到空列表。
        租户隔离仍由 DAO 的 ``tenant_id`` 条件保证（跨租户 → 查不到 → 40401 口径）。
        """
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = ai_dao.list_models(
            db, principal.tenant_id, scene=scene, status=status, limit=size, offset=(current - 1) * size
        )
        total = ai_dao.count_models(db, principal.tenant_id, scene=scene, status=status)
        return [cls.to_out(row) for row in rows], total, current, size

    # ------------------------------------------------------------------
    # API-82 修改
    # ------------------------------------------------------------------
    @classmethod
    def update_model(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        model_id: int,
        payload: ModelConfigUpdateRequest,
    ) -> ModelConfigOut:
        """修改模型配置与主备路由。

        :raises NotFoundError: 配置不存在 / 已软删 → **40401**
        :raises UniqueConflictError: 改后与其他配置撞唯一键 → **40901**
        :raises IllegalStateError: 改动会导致某场景「没有任何启用中的模型」→ **42201**
        """
        config = ai_dao.get_model(db, principal.tenant_id, model_id)
        if config is None:
            raise NotFoundError("模型配置不存在")

        fields = payload.model_dump(exclude_unset=True, exclude_none=True)
        if not fields:
            # schema 已挡「空对象」，这里挡「只传了 null」的情形
            raise IllegalStateError("请至少提供一个待修改字段", {"fields": sorted(payload.model_fields_set)})

        before = cls._snapshot(config)
        target_scene = str(fields.get("scene", config.scene))
        target_code = str(fields.get("model_code", config.model_code))

        # ---- 唯一键预检（不靠数据库 1062 报错 —— 那条信息对用户毫无帮助）----
        if target_scene != config.scene or target_code != config.model_code:
            existing = ai_dao.get_model_by_code(db, principal.tenant_id, target_scene, target_code)
            if existing is not None and int(existing.id) != int(config.id):
                # 与 CU-05/06/07 同口径：40901 + data 里带冲突的业务键
                raise BizError(
                    ErrorCode.UNIQUE_CONFLICT,
                    f"场景 {target_scene!r} 下已存在编码 {target_code!r} 的模型配置",
                    {"scene": target_scene, "model_code": target_code},
                )

        # ---- 停用前自检：不允许把某场景的**最后一个启用模型**停掉 ----
        # 理由与「不许停用有启用中子项的字典父项」同源（CU-07 决策 ⑥）：
        # 全部停用后该场景的调用会**静默降级**（网关返回 degraded），
        # 而运维在界面上看不到任何异常 —— 只有调用日志在涨 degraded_reason。
        new_status = fields.get("status", config.status)
        if int(config.status) == 1 and int(new_status) == 0:
            others = [
                row
                for row in ai_dao.list_enabled_models_by_scene(db, principal.tenant_id, config.scene)
                if int(row.id) != int(config.id)
            ]
            if not others:
                raise IllegalStateError(
                    f"场景 {config.scene!r} 只剩这一个启用中的模型，停用后该场景将无模型可用",
                    {"scene": config.scene},
                )

        ai_dao.update_model(db, principal.tenant_id, config.id, **fields)
        db.commit()

        refreshed = ai_dao.get_model(db, principal.tenant_id, model_id)
        if refreshed is None:  # pragma: no cover — 刚提交，理论上不可能
            raise NotFoundError("模型配置不存在")
        after = cls._snapshot(refreshed)

        # 🔴 审计在 commit **之后**（独立事务 + fail-open，见 audit_service 模块 docstring）
        AuditService.record(
            AuditAction.AI_MODEL_UPDATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.AI_MODEL,
            target_id=model_id,
            before=before,
            after=after,
        )
        return cls.to_out(refreshed)

    # ------------------------------------------------------------------
    # API-83 健康与熔断
    # ------------------------------------------------------------------
    @classmethod
    def health(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        scene: str | None = None,
    ) -> ModelHealthOut:
        """探测全部（或某场景的）模型，并汇总熔断状态。

        🔴 **``all_primary_healthy`` 的口径是「每个场景至少有一路主模型健康」**，
        而不是「所有模型都健康」。按后者判定的话，一个备用链路挂了会让整个
        平台健康检查变红 —— 而备用链路挂掉**不影响任何功能**，
        于是运维会逐渐学会忽略这个红色的健康检查（那才是真正的风险）。
        """
        rows = ai_dao.list_models(db, principal.tenant_id, scene=scene)
        gateway = ModelGateway(principal.tenant_id)
        items: list[ModelTargetOut] = []
        try:
            for row in rows:
                target = cls.to_target(row)
                probe = gateway.probe(target)
                items.append(
                    ModelTargetOut(
                        id=row.id,
                        scene=row.scene,
                        scene_name=AiScene.label_of(row.scene),
                        model_code=row.model_code,
                        display_name=row.display_name,
                        provider=row.provider,
                        deployment=row.deployment,
                        model_name=row.model_name,
                        priority=int(row.priority),
                        status=int(row.status),
                        status_name=EnabledStatus.name_of(int(row.status)),
                        healthy=bool(probe["healthy"]),
                        latency_ms=probe.get("latency_ms"),
                        error=_clip(probe.get("error"), 200),
                        blocked=bool(probe["blocked"]),
                        consecutive_failures=int(probe["consecutive_failures"]),
                        cooldown_remaining_seconds=int(probe["cooldown_remaining_seconds"]),
                    )
                )
        finally:
            gateway.close()

        # ---- 按场景分组，判断「每个场景是否至少一路主模型健康」----
        # 🔴 口径：**主路可用即场景可用**。不按「所有模型都健康」判定 ——
        # 备用链路挂掉不影响任何功能，按后者判定会让健康检查长期变红，
        # 于是运维逐渐学会忽略它（那才是真正的风险）。
        by_scene: dict[str, list[ModelTargetOut]] = {}
        for item in items:
            by_scene.setdefault(item.scene, []).append(item)
        primary_ok = {
            scene_code: bool(group and sorted(group, key=lambda x: (x.priority, int(x.id)))[0].healthy)
            for scene_code, group in by_scene.items()
        }

        healthy = sum(1 for item in items if item.healthy)
        blocked = sum(1 for item in items if item.blocked)
        return ModelHealthOut(
            checked_at=datetime.now(),
            total=len(items),
            healthy=healthy,
            unavailable=len(items) - healthy,
            blocked=blocked,
            # 一条模型配置都没有时判 False —— 「没有任何配置」显然是**不可用**，
            # 若因为它「没有反例」而返回 True，前端会显示一个绿色的空看板。
            all_primary_healthy=all(primary_ok.values()) if primary_ok else False,
            breaker_backend_ok=CircuitBreaker.backend_ok(),
            items=items,
        )

    # ------------------------------------------------------------------
    # 复用：配置行 → 网关目标
    # ------------------------------------------------------------------
    @classmethod
    def to_target(cls, row: AiModelConfig) -> ModelTarget:
        """把 ORM 行转成 :class:`~app.core.model_gateway.ModelTarget`（纯函数）。"""
        return ModelTarget(
            id=int(row.id),
            scene=str(row.scene),
            model_code=str(row.model_code),
            display_name=str(row.display_name),
            provider=str(row.provider),
            deployment=str(row.deployment),
            base_url=str(row.base_url),
            model_name=str(row.model_name),
            priority=int(row.priority),
            timeout_seconds=float(row.timeout_seconds),
            max_retries=int(row.max_retries),
            breaker_threshold=int(row.breaker_threshold),
            breaker_cooldown_seconds=int(row.breaker_cooldown_seconds),
            price_in_per_1k=_dec(row.price_in_per_1k),
            price_out_per_1k=_dec(row.price_out_per_1k),
        )

    # ------------------------------------------------------------------
    # 出参装配
    # ------------------------------------------------------------------
    @classmethod
    def to_out(cls, row: AiModelConfig) -> ModelConfigOut:
        return ModelConfigOut(
            id=row.id,
            scene=row.scene,
            scene_name=AiScene.label_of(row.scene),
            model_code=row.model_code,
            display_name=row.display_name,
            provider=row.provider,
            provider_name=AiProvider.LABELS.get(row.provider),
            deployment=row.deployment,
            deployment_name=AiDeployment.LABELS.get(row.deployment),
            base_url=row.base_url,
            model_name=row.model_name,
            priority=int(row.priority),
            timeout_seconds=float(row.timeout_seconds),
            max_retries=int(row.max_retries),
            breaker_threshold=int(row.breaker_threshold),
            breaker_cooldown_seconds=int(row.breaker_cooldown_seconds),
            price_in_per_1k=_dec(row.price_in_per_1k),
            price_out_per_1k=_dec(row.price_out_per_1k),
            status=int(row.status),
            status_name=EnabledStatus.name_of(int(row.status)),
            remark=row.remark,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _snapshot(row: AiModelConfig) -> dict[str, Any]:
        """审计快照（**不含任何密钥** —— 模型配置里目前也没有密钥字段，
        密钥一律经环境变量注入，见架构 §5「密钥经环境变量启动注入（NFR-15）」）。"""
        return {
            "scene": row.scene,
            "model_code": row.model_code,
            "display_name": row.display_name,
            "provider": row.provider,
            "deployment": row.deployment,
            "base_url": row.base_url,
            "model_name": row.model_name,
            "priority": int(row.priority),
            "timeout_seconds": float(row.timeout_seconds),
            "max_retries": int(row.max_retries),
            "breaker_threshold": int(row.breaker_threshold),
            "breaker_cooldown_seconds": int(row.breaker_cooldown_seconds),
            "price_in_per_1k": str(_dec(row.price_in_per_1k)),
            "price_out_per_1k": str(_dec(row.price_out_per_1k)),
            "status": int(row.status),
            "remark": row.remark,
        }


# ============================================================================
# 模块级复用入口（供 CU-12 / CU-14 / CU-20 / CU-21 调用）
# ============================================================================
def build_targets(db: Session, tenant_id: int, scene: str) -> list[ModelTarget]:
    """取某场景的**启用模型备选链**（按 ``priority`` 升序），供网关直接使用。

    :return: 有序的 :class:`ModelTarget` 列表；**空列表表示该场景无可用配置**
        （网关会返回降级结果，各调用点据此写 ``ai_call_log`` 并渲染降级徽标）

    🔴 过滤条件是「``status=1`` + ``is_deleted=0`` + 租户匹配」，三者缺一不可：
    - 漏 ``status``：停用的模型仍在被调用（改配置时最危险的一类失效）；
    - 漏 ``is_deleted``：被软删的配置「复活」；
    - 漏租户：跨租户调用别人的模型通道（**越权**，且账单算到别人头上）。
    """
    rows = ai_dao.list_enabled_models_by_scene(db, tenant_id, scene)
    return [AiService.to_target(row) for row in rows]


def get_pricing(
    db: Session, tenant_id: int, scene: str, model_name: str | None
) -> tuple[Decimal, Decimal]:
    """按 ``(scene, model_name)`` 取单价（元/千 token）。

    ⚠️ 取不到时返回 ``(0, 0)`` 而不是抛错：单价缺失**不应该**让一次已经成功的
    调用写不进日志（那会丢掉一条真实的调用记录）。成本记 0 是「少算」，
    而丢日志是「不可追溯」—— 后者严重得多。缺失的单价由 ``seed`` / 运维补齐。
    """
    for row in ai_dao.list_enabled_models_by_scene(db, tenant_id, scene):
        if model_name and str(row.model_name) == model_name:
            return _dec(row.price_in_per_1k), _dec(row.price_out_per_1k)
    rows = ai_dao.list_models(db, tenant_id, scene=scene)
    for row in rows:
        if model_name and str(row.model_name) == model_name:
            return _dec(row.price_in_per_1k), _dec(row.price_out_per_1k)
    return Decimal("0"), Decimal("0")


# ============================================================================
# 工具
# ============================================================================
def _dec(value: Any) -> Decimal:
    """``None`` → ``Decimal("0")``（DB 有默认值，但 Python 侧对象可能是 ``None``）。"""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _clip(text: Any, limit: int) -> str | None:
    if text is None:
        return None
    s = str(text)
    return s if len(s) <= limit else s[:limit]
