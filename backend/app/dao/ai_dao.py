"""AI 中台 DAO（TBL-23 ``prompt_template`` / TBL-24 ``ai_call_log`` / TBL-29 ``ai_model_config``）。

本模块覆盖 CU-10（模型配置）/ CU-11（Prompt）/ CU-12（调用日志）三个单元的纯 CRUD。

🔴 **``ai_call_log`` 相关函数里没有 ``is_deleted`` 条件**，这不是漏写
==============================================================
该表是**只追加**的日志表，**结构上不存在** ``is_deleted`` 列
（:class:`~app.models.ai.AiCallLog` 刻意没继承软删 mixin）。
照抄 :mod:`app.dao.dict_dao` 的模板写 ``is_deleted == 0`` 会**引用不存在的列而报错**。
同理，本模块的 ``insert_call_log`` 之外**不提供任何 update / delete**。

⚠️ ``ai_call_log`` 是分区表：**查询必须带时间范围**（``start``/``end``），
否则无法分区裁剪，退化为全表扫描（数据库 §9 预测 3 年 547 万行）。
故 :func:`list_call_logs` / :func:`count_call_logs` 的 ``start`` 为**必填**位置参数，
由服务层保证「默认最近 7 天」——把「必须给时间」做成签名约束，
比写在注释里等调用方自觉可靠。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models.ai import AiCallLog, AiModelConfig, PromptTemplate

__all__ = [
    "aggregate_cost_by_model",
    "aggregate_cost_by_scene",
    "aggregate_cost_by_time",
    "aggregate_call_summary",
    "aggregate_outbound_mask",
    "count_call_logs",
    "count_prompts",
    "get_model",
    "get_prompt",
    "get_prompt_max_version",
    "insert_call_log",
    "insert_model",
    "insert_prompt",
    "list_call_logs",
    "list_enabled_models_by_scene",
    "list_models",
    "list_prompt_versions",
    "list_prompts",
    "set_model_status",
    "set_prompt_status",
    "update_model",
    "update_prompt_content",
]


# ============================================================================
# TBL-23 Prompt 模板
# ============================================================================
def get_prompt(db: Session, tenant_id: int, prompt_id: int) -> PromptTemplate | None:
    """按 ID 取某**个版本**（软删视为不存在）。"""
    stmt = select(PromptTemplate).where(
        PromptTemplate.tenant_id == int(tenant_id),  # 🔴 租户条件恒为首位
        PromptTemplate.id == int(prompt_id),
        PromptTemplate.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_prompt_max_version(db: Session, tenant_id: int, prompt_code: str) -> int:
    """取某模板族（``prompt_code``）的**最大版本号**，无记录返回 0。

    ⚠️ 必须用 ``MAX()`` 而不是 ``COUNT()``：版本号可能因回滚/停用出现跳号
    （停用不删，故一般连续，但**不要依赖连续**——一旦有人手工插过版本，
    用 COUNT 算下一个版本号就会撞唯一键 ``uk_tenant_code_ver``）。
    """
    stmt = select(func.max(PromptTemplate.version)).where(
        PromptTemplate.tenant_id == int(tenant_id),
        PromptTemplate.prompt_code == str(prompt_code),
        PromptTemplate.is_deleted == 0,
    )
    return int(db.execute(stmt).scalar_one_or_none() or 0)


def _apply_prompt_filters(
    stmt: Select[tuple[PromptTemplate]],
    tenant_id: int,
    *,
    scene: str | None = None,
    prompt_code: str | None = None,
    status: int | None = None,
) -> Select[tuple[PromptTemplate]]:
    """Prompt 筛选条件（list 与 count **共用**，避免两处条件漂移）。"""
    stmt = stmt.where(
        PromptTemplate.tenant_id == int(tenant_id), PromptTemplate.is_deleted == 0
    )
    if scene:
        stmt = stmt.where(PromptTemplate.scene == scene)
    if prompt_code:
        stmt = stmt.where(PromptTemplate.prompt_code == str(prompt_code))
    if status is not None:
        stmt = stmt.where(PromptTemplate.status == int(status))
    return stmt


def list_prompts(
    db: Session,
    tenant_id: int,
    *,
    scene: str | None = None,
    prompt_code: str | None = None,
    status: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[PromptTemplate]:
    """列表。

    排序 ``prompt_code ASC, version DESC`` —— 列表按「模板族」聚集，
    族内**最新版本在最前**（这是运维真正关心的那一条）。
    ⚠️ 补 ``id DESC`` 构成全序，否则同族同秒插入的多行顺序不确定，翻页会重复。
    """
    stmt = _apply_prompt_filters(
        select(PromptTemplate), tenant_id, scene=scene, prompt_code=prompt_code, status=status
    )
    stmt = stmt.order_by(
        PromptTemplate.prompt_code.asc(), PromptTemplate.version.desc(), PromptTemplate.id.desc()
    )
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_prompts(
    db: Session,
    tenant_id: int,
    *,
    scene: str | None = None,
    prompt_code: str | None = None,
    status: int | None = None,
) -> int:
    """计数（条件与 :func:`list_prompts` 共用同一构造函数）。"""
    stmt = _apply_prompt_filters(
        select(func.count()).select_from(PromptTemplate),
        tenant_id,
        scene=scene,
        prompt_code=prompt_code,
        status=status,
    )
    return int(db.execute(stmt).scalar_one())


def list_prompt_versions(
    db: Session, tenant_id: int, prompt_code: str
) -> list[PromptTemplate]:
    """取某模板族的**全部版本**（升序），供 API-87 回滚目标校验与前端版本下拉。"""
    stmt = (
        select(PromptTemplate)
        .where(
            PromptTemplate.tenant_id == int(tenant_id),
            PromptTemplate.prompt_code == str(prompt_code),
            PromptTemplate.is_deleted == 0,
        )
        .order_by(PromptTemplate.version.asc())
    )
    return list(db.execute(stmt).scalars().all())


def insert_prompt(db: Session, prompt: PromptTemplate) -> PromptTemplate:
    """插入一个新版本（**只增不改**，由调用方 commit）。"""
    db.add(prompt)
    db.flush()
    return prompt


def update_prompt_content(
    db: Session, tenant_id: int, prompt_id: int, **fields: Any
) -> int:
    """**仅允许改状态类字段**（``status``）；返回受影响行数。

    🔴 正文与版本号**不可改** —— 改正文必须走「插入新版本」。
    把这条限制做在 DAO 签名上（显式白名单），比在 Service 里靠注释提醒可靠：
    调用方传 ``content=...`` 会被下面这行直接拦掉。
    """
    allowed = {"status"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(
            f"Prompt 只允许原地修改 {sorted(allowed)}；"
            f"正文/版本变更必须插入新版本（收到：{sorted(unknown)}）"
        )
    stmt = (
        PromptTemplate.__table__.update()
        .where(
            PromptTemplate.tenant_id == int(tenant_id),
            PromptTemplate.id == int(prompt_id),
            PromptTemplate.is_deleted == 0,
        )
        .values(**fields)
    )
    return int(db.execute(stmt).rowcount or 0)


def set_prompt_status(
    db: Session,
    tenant_id: int,
    prompt_code: str,
    status: int,
    *,
    except_id: int | None = None,
) -> int:
    """把某模板族下**除 ``except_id`` 外**的版本统一置为指定状态，返回受影响行数。

    用途有两个，都是「同一族内最多一个启用版本」这条不变式的实现手段：

    1. **启用新版本时**把旧启用版本置 ``DISABLED(2)``（``except_id`` = 新版本）；
    2. **停用整个族**时全部置 ``DISABLED(2)``（``except_id`` = ``None``）。

    ⚠️ 用批量 UPDATE 而非「先查再逐个改」：后者在并发下会漏改
    （两次请求各改了「自己查到的那几行」）。
    """
    tbl = PromptTemplate.__table__
    stmt = tbl.update().where(
        tbl.c.tenant_id == int(tenant_id),
        tbl.c.prompt_code == str(prompt_code),
        tbl.c.is_deleted == 0,
    )
    if except_id is not None:
        stmt = stmt.where(tbl.c.id != int(except_id))
    stmt = stmt.values(status=int(status))
    return int(db.execute(stmt).rowcount or 0)


# ============================================================================
# TBL-29 模型网关配置
# ============================================================================
def get_model(db: Session, tenant_id: int, model_id: int) -> AiModelConfig | None:
    stmt = select(AiModelConfig).where(
        AiModelConfig.tenant_id == int(tenant_id),
        AiModelConfig.id == int(model_id),
        AiModelConfig.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_model_by_code(
    db: Session, tenant_id: int, scene: str, model_code: str
) -> AiModelConfig | None:
    """按 ``(scene, model_code)`` 取配置（唯一键对应的自然查询，供幂等种子使用）。"""
    stmt = select(AiModelConfig).where(
        AiModelConfig.tenant_id == int(tenant_id),
        AiModelConfig.scene == str(scene),
        AiModelConfig.model_code == str(model_code),
        AiModelConfig.is_deleted == 0,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_models(
    db: Session,
    tenant_id: int,
    *,
    scene: str | None = None,
    status: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AiModelConfig]:
    """模型配置列表。

    排序 ``scene ASC, priority ASC, id ASC`` —— 与「主备链」的语义一致：
    同一场景内 ``priority`` 小的在前（= 优先尝试）。API-81 的展示顺序即路由顺序，
    两者必须一致，否则界面上看到的顺序与真实路由顺序相反，排障时会严重误导。
    """
    stmt = select(AiModelConfig).where(
        AiModelConfig.tenant_id == int(tenant_id), AiModelConfig.is_deleted == 0
    )
    if scene:
        stmt = stmt.where(AiModelConfig.scene == scene)
    if status is not None:
        stmt = stmt.where(AiModelConfig.status == int(status))
    stmt = stmt.order_by(
        AiModelConfig.scene.asc(), AiModelConfig.priority.asc(), AiModelConfig.id.asc()
    )
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_models(
    db: Session, tenant_id: int, *, scene: str | None = None, status: int | None = None
) -> int:
    stmt = select(func.count()).select_from(AiModelConfig).where(
        AiModelConfig.tenant_id == int(tenant_id), AiModelConfig.is_deleted == 0
    )
    if scene:
        stmt = stmt.where(AiModelConfig.scene == scene)
    if status is not None:
        stmt = stmt.where(AiModelConfig.status == int(status))
    return int(db.execute(stmt).scalar_one())


def list_enabled_models_by_scene(
    db: Session, tenant_id: int, scene: str
) -> list[AiModelConfig]:
    """取某场景**启用中**的备选链（按 ``priority`` 升序）—— 路由入口。"""
    stmt = (
        select(AiModelConfig)
        .where(
            AiModelConfig.tenant_id == int(tenant_id),
            AiModelConfig.scene == str(scene),
            AiModelConfig.status == 1,  # 1 = 启用（EnabledStatus.ENABLED）
            AiModelConfig.is_deleted == 0,
        )
        .order_by(AiModelConfig.priority.asc(), AiModelConfig.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def insert_model(db: Session, model: AiModelConfig) -> AiModelConfig:
    db.add(model)
    db.flush()
    return model


def update_model(db: Session, tenant_id: int, model_id: int, **fields: Any) -> int:
    """更新模型配置（API-82）。返回受影响行数。

    ``scene`` / ``model_code`` 是唯一键的一部分 —— 允许改（「把某路挪到别的场景」
    是真实需求），但**服务层必须先校验改后不与其他配置冲突**，
    否则会得到数据库层的 1062（唯一键冲突），报错信息对用户毫无帮助。
    """
    if not fields:
        return 0
    stmt = (
        AiModelConfig.__table__.update()
        .where(
            AiModelConfig.tenant_id == int(tenant_id),
            AiModelConfig.id == int(model_id),
            AiModelConfig.is_deleted == 0,
        )
        .values(**fields)
    )
    return int(db.execute(stmt).rowcount or 0)


def set_model_status(db: Session, tenant_id: int, model_id: int, status: int) -> int:
    """停用 / 启用（停用 = 不参与路由，**不删行**）。"""
    return update_model(db, tenant_id, model_id, status=int(status))


# ============================================================================
# TBL-24 AI 调用日志（只追加；**无 is_deleted 条件**）
# ============================================================================
def insert_call_log(db: Session, log: AiCallLog) -> AiCallLog:
    """插入一条调用日志并 flush（**由调用方 commit**）。

    ⚠️ 一般不要直接调 —— 走 :meth:`app.services.ai_log_service.AiLogService.record`，
    那里统一处理「错误信息截断 + 成本计算 + fail-open」。
    """
    db.add(log)
    db.flush()
    return log


def _apply_call_log_filters(
    stmt: Select[tuple[AiCallLog]],
    tenant_id: int,
    *,
    start: datetime,
    end: datetime,
    scene: str | None = None,
    model: str | None = None,
    status: int | None = None,
    cache_hit: bool | None = None,
    mask_applied: bool | None = None,
    user_id: int | None = None,
    trace_id: str | None = None,
) -> Select[tuple[AiCallLog]]:
    """调用日志筛选条件（list / count **共用**）。

    🔴 ``start`` / ``end`` 为**必填关键字参数**（不是可选默认）——
    本表按月分区，缺时间范围 = 放弃分区裁剪 = 全表扫描。
    把它做成必填，是让「忘带时间」在**调用时就失败**，而不是在生产上慢慢拖垮库。
    """
    stmt = stmt.where(
        AiCallLog.tenant_id == int(tenant_id),  # 租户条件恒为首位
        AiCallLog.created_at >= start,
        AiCallLog.created_at <= end,
    )
    if scene:
        stmt = stmt.where(AiCallLog.scene == scene)
    if model:
        stmt = stmt.where(AiCallLog.model == model)
    if status is not None:
        stmt = stmt.where(AiCallLog.status == int(status))
    if cache_hit is not None:
        stmt = stmt.where(AiCallLog.cache_hit == (1 if cache_hit else 0))
    if mask_applied is not None:
        stmt = stmt.where(AiCallLog.mask_applied == (1 if mask_applied else 0))
    if user_id is not None:
        stmt = stmt.where(AiCallLog.user_id == int(user_id))
    if trace_id:
        stmt = stmt.where(AiCallLog.trace_id == str(trace_id))
    return stmt


def list_call_logs(
    db: Session,
    tenant_id: int,
    *,
    start: datetime,
    end: datetime,
    scene: str | None = None,
    model: str | None = None,
    status: int | None = None,
    cache_hit: bool | None = None,
    mask_applied: bool | None = None,
    user_id: int | None = None,
    trace_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AiCallLog]:
    """按条件取调用日志，**时间倒序**。排序口径同审计日志（``created_at DESC, id DESC``）。"""
    stmt = _apply_call_log_filters(
        select(AiCallLog),
        tenant_id,
        start=start,
        end=end,
        scene=scene,
        model=model,
        status=status,
        cache_hit=cache_hit,
        mask_applied=mask_applied,
        user_id=user_id,
        trace_id=trace_id,
    )
    stmt = stmt.order_by(AiCallLog.created_at.desc(), AiCallLog.id.desc())
    if offset:
        stmt = stmt.offset(max(0, int(offset)))
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    return list(db.execute(stmt).scalars().all())


def count_call_logs(
    db: Session,
    tenant_id: int,
    *,
    start: datetime,
    end: datetime,
    scene: str | None = None,
    model: str | None = None,
    status: int | None = None,
    cache_hit: bool | None = None,
    mask_applied: bool | None = None,
    user_id: int | None = None,
    trace_id: str | None = None,
) -> int:
    stmt = _apply_call_log_filters(
        select(func.count()).select_from(AiCallLog),
        tenant_id,
        start=start,
        end=end,
        scene=scene,
        model=model,
        status=status,
        cache_hit=cache_hit,
        mask_applied=mask_applied,
        user_id=user_id,
        trace_id=trace_id,
    )
    return int(db.execute(stmt).scalar_one())


def aggregate_call_summary(
    db: Session, tenant_id: int, *, start: datetime, end: datetime
) -> dict[str, Any]:
    """API-88 的 ``summary``：调用数 / 总成本 / 平均耗时 / 缓存命中率 / 失败数。

    🔴 一次 ``SELECT`` 出全部指标（而不是 5 次查询）：这些指标必须来自**同一个快照**，
    否则并发写入时会得到「调用数 1743 / 平均耗时算在 1730 次上」这类自相矛盾的看板。

    ⚠️ **脱敏率不在这里算** —— 它的分母是「出网调用」而不是「全部调用」
    （本地调用不需要脱敏，算进分母会让 ``mask_applied_rate`` 永远显著小于 1.0，
    从而让「低于 1.0 即告警」这条合规监控彻底失效 —— 狼来了）。
    出网调用数由 :func:`aggregate_outbound_mask` 单独取，两者由服务层合成。
    """
    stmt = (
        select(
            func.count().label("calls"),
            func.coalesce(func.sum(AiCallLog.cost), 0).label("cost_total"),
            func.coalesce(func.avg(AiCallLog.latency_ms), 0).label("avg_latency"),
            func.coalesce(func.avg(AiCallLog.cache_hit), 0).label("cache_rate"),
            func.coalesce(func.sum(case((AiCallLog.status == 0, 1), else_=0)), 0).label("failed"),
        )
        .select_from(AiCallLog)
        .where(
            AiCallLog.tenant_id == int(tenant_id),
            AiCallLog.created_at >= start,
            AiCallLog.created_at <= end,
        )
    )
    row = db.execute(stmt).one()
    return {
        "calls": int(row.calls or 0),
        "cost_total_yuan": Decimal(str(row.cost_total or 0)),
        "avg_latency_ms": int(round(float(row.avg_latency or 0))),
        "cache_hit_rate": round(float(row.cache_rate or 0), 4),
        "failed": int(row.failed or 0),
    }


def aggregate_outbound_mask(
    db: Session, tenant_id: int, *, start: datetime, end: datetime, deployment: str
) -> tuple[int, int]:
    """出网调用的脱敏统计，返回 ``(cloud_calls, mask_applied_calls)``。

    :param start: 区间起点（含）
    :param end: 区间终点（含）
    :param deployment: 「出网」的取值 —— 由服务层传 :attr:`app.schemas.ai.AiDeployment.CLOUD`。
        刻意做成**必传参数**而不是在本函数里写死 ``"cloud"``：
        DAO 不持有业务词表（本层原先也没有任何一处 import ``app.schemas``），
        写死一个字面量会变成「第二处定义」；将来若部署位置增加第三态
        （如私有云专线），漏改这里不会有任何测试报错，只会让合规统计安静地算错。

    🔴 分母口径 = ``deployment = 'cloud'`` 的调用 —— 即**本次调用实际走了出网通道**。

    为什么**不是**「按 ``ai_model_config`` 里配了 cloud 模型的场景集合来筛」
    ------------------------------------------------------------------
    那是本函数的第一版实现，被推翻了，两个反例都是真实会发生的情形：

    1. **云端主路失败 → 落到本地备用**。这次调用根本没出网、也**不需要**脱敏，
       但按场景集合筛会把它算成「出网但未脱敏」→ ``mask_applied_rate`` 掉到 1.0 以下
       → 触发 ``mask_alert``。**凭空产生合规告警**，而告警一旦变廉价就没人看了。
    2. **配置变更会追溯性改写历史结论**。某场景今天指向云端、下周 A/B 切回本地之后，
       按「当前配置」重算历史区间：原来正确地脱敏出网的记录会退出分母（**自证清白**），
       反过来若从本地切到云端，历史上根本没出网的本地调用会突然变成「未脱敏违规」。

    合规证据必须**不可被追溯性改写**，所以出网与否是**调用时刻的事实**，
    落在 ``ai_call_log.deployment`` 上（见该列注释），而不是事后从配置反推。
    """
    stmt = (
        select(
            func.count().label("cloud_calls"),
            func.coalesce(func.sum(AiCallLog.mask_applied), 0).label("masked"),
        )
        .select_from(AiCallLog)
        .where(
            AiCallLog.tenant_id == int(tenant_id),
            AiCallLog.created_at >= start,
            AiCallLog.created_at <= end,
            # ``NULL``（调用方未声明部署位置）**不进分母**：
            # 无法判定出网与否时，既不冤枉成「未脱敏」，也不放过成「已合规」。
            AiCallLog.deployment == str(deployment),
        )
    )
    row = db.execute(stmt).one()
    return int(row.cloud_calls or 0), int(row.masked or 0)


def _aggregate(
    db: Session, tenant_id: int, *, start: datetime, end: datetime, group_col: Any
) -> list[dict[str, Any]]:
    """按某列分组的成本聚合（三个维度共用；口径必须完全一致，否则三个维度对不上总数）。"""
    stmt = (
        select(
            group_col.label("grp"),
            func.count().label("calls"),
            func.coalesce(func.sum(AiCallLog.prompt_tokens), 0).label("tokens_in"),
            func.coalesce(func.sum(AiCallLog.completion_tokens), 0).label("tokens_out"),
            func.coalesce(func.sum(AiCallLog.cost), 0).label("cost"),
            func.coalesce(func.avg(AiCallLog.latency_ms), 0).label("avg_latency"),
        )
        .select_from(AiCallLog)
        .where(
            AiCallLog.tenant_id == int(tenant_id),
            AiCallLog.created_at >= start,
            AiCallLog.created_at <= end,
        )
        .group_by(group_col)
        .order_by(func.sum(AiCallLog.cost).desc(), func.count().desc())
    )
    return [
        {
            "key": row.grp,
            "calls": int(row.calls or 0),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost_yuan": Decimal(str(row.cost or 0)),
            "avg_latency_ms": int(round(float(row.avg_latency or 0))),
        }
        for row in db.execute(stmt).all()
    ]


def aggregate_cost_by_scene(
    db: Session, tenant_id: int, *, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """按**场景**维度聚合（API-89 ``by_scene``）。"""
    return _aggregate(db, tenant_id, start=start, end=end, group_col=AiCallLog.scene)


def aggregate_cost_by_model(
    db: Session, tenant_id: int, *, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """按**模型**维度聚合（API-89 ``by_model``）。"""
    return _aggregate(db, tenant_id, start=start, end=end, group_col=AiCallLog.model)


def aggregate_cost_by_time(
    db: Session, tenant_id: int, *, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """按**天**维度聚合（API-89 ``by_time``）。

    ⚠️ 用 MySQL 的 ``DATE(created_at)`` 而非 Python 侧分组：
    Python 分组需要把区间内**所有明细行**拉回进程（百万行级），
    而 SQL 聚合只回传「天数」行。分区的裁剪也一样只在 SQL 侧生效。
    """
    day_col = func.date(AiCallLog.created_at)
    rows = _aggregate(db, tenant_id, start=start, end=end, group_col=day_col)
    # 日期统一转字符串，避免 Pydantic 对 date/datetime 的序列化差异
    for item in rows:
        key = item["key"]
        item["key"] = key.strftime("%Y-%m-%d") if hasattr(key, "strftime") else str(key)
    return sorted(rows, key=lambda r: str(r["key"]))
