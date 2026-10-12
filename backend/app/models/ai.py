"""AI 中台数据模型（TBL-23 ``prompt_template`` / TBL-24 ``ai_call_log`` / TBL-29 ``ai_model_config``）。

三张表的分工（与 TBL-28 审计日志、TBL-22 变更留痕**不可互换**）
=============================================================

| 表 | 记什么 | 谁在写 | 可否改 |
| --- | --- | --- | --- |
| **TBL-23 ``prompt_template``（本文件）** | **Prompt 模板正文 + 版本** | API-85~87（人工） | ✅ 可改状态；正文**只追加新版本** |
| **TBL-24 ``ai_call_log``（本文件）** | **每次 AI 调用的元数据**（模型 / token / 成本 / 耗时 / 脱敏标记） | ``ai_log_service`` | ❌ **只追加**（无软删列，DAO 无改删函数） |
| **TBL-29 ``ai_model_config``（本文件）** | **模型网关配置**（场景、主备顺序、熔断与单价） | API-82（人工） | ✅ 可改 |
| TBL-28 ``audit_log`` | **操作行为**（谁改了配置、谁导出了日志） | ``audit_service`` | ❌ 只追加 |
| TBL-22 ``talent_change_log`` | **人才档案**字段级变更 | CU-22 | ❌ 只追加 |

🔴 **TBL-29 是本批新增表（基线偏离）**
====================================
接口 §2.2 把 API-81/82（模型配置列表 / 修改）的「涉及表」写作 **TBL-23** ——
但 ``prompt_template`` 的 ``prompt_code`` / ``content`` 均为 NOT NULL，
语义是「Prompt 模板」，**装不下模型接入配置**（提供方、base_url、模型名、主备顺序、
超时、熔断阈值、单价）。硬塞的结果会是「一张表两种语义 + 一半列永远为空」。

处置（与 CU-07 处理 API-26 留痕表指错的方式一致）：
**按语义需要新建 TBL-29 ``ai_model_config``**，并把该偏离登记进评审文档 §7.3。
编号顺延自 TBL-28（审计日志），不占用 TBL-01~28 的既有语义。

🔴 **AI 配置与 Prompt 的启用状态用 :class:`~app.models.enums.PromptStatus`**
========================================================================
``prompt_template.status`` 是**三态**（0 草稿 / 1 启用 / 2 停用），
不能套 :class:`~app.models.enums.EnabledStatus`（二态）—— 草稿是一个**真实的业务状态**
（新写的 Prompt 先存草稿、试跑通过再启用）。

而 ``ai_model_config.status`` 是**二态**（0 停用 / 1 启用），用
:class:`~app.models.enums.EnabledStatus`：「停用」= 该模型不参与路由，与「禁用账号」不同。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.snowflake import next_id
from app.models.base import MYSQL_TABLE_KWARGS, Base, IdTenantCommonMixin

# ============================================================================
# TBL-23 Prompt 模板（版本化）
# ============================================================================
class PromptTemplate(IdTenantCommonMixin, Base):
    """TBL-23 Prompt 模板（**正文只追加新版本，永不改写历史**）。

    「版本化」这件事的落地方式
    ------------------------
    ``(tenant_id, prompt_code)`` 是一个**模板族**，``version`` 在族内单调递增。
    API-86「修改 Prompt」**不是 UPDATE**，而是插入 ``version = max + 1`` 的新行 ——
    于是「谁在什么时候改了什么」永远可回答，且历史调用日志里的
    ``prompt_version`` 仍能指回**当时真正生效的正文**。

    🔴 若这里改成原地更新 ``content``，历史 ``ai_call_log.prompt_version``
    就会指向一份**已经变了的**正文 —— 出问题时无法复现，等于白记版本号。

    ⚠️ 唯一键 ``(tenant_id, prompt_code, version, is_deleted)`` 沿用数据库 §4.6 的写法。
    本表 ``is_deleted`` **恒为 0**（Prompt 只停用/归档不删除，同字典项的处理），
    故该唯一键等价于「族内版本号唯一」——恰是需要的语义。
    """

    __tablename__ = "prompt_template"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "prompt_code", "version", "is_deleted", name="uk_tenant_code_ver"
        ),
        # 按场景列启用中的模板（API-84 的筛选主路径；也供调用方取「该场景当前启用版本」）
        Index("idx_tenant_scene", "tenant_id", "scene", "status"),
        MYSQL_TABLE_KWARGS,
    )

    prompt_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="模板编码（同一编码下多版本）"
    )
    scene: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="场景：extract/rewrite/ask/judge"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="版本号（只追加）"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="模板正文（含变量占位）")
    status: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="状态 0 草稿 1 启用 2 停用",
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="维护人")

    def __repr__(self) -> str:
        return (
            f"<PromptTemplate {self.prompt_code} v{self.version} "
            f"scene={self.scene!r} status={self.status}>"
        )


# ============================================================================
# TBL-24 AI 调用日志（只追加，按月分区）
# ============================================================================
class AiCallLog(Base):
    """TBL-24 AI 调用日志（**只追加 + 按月 RANGE 分区**，F-9.5）。

    ① 为什么是**复合主键** ``(id, created_at)``
    -----------------------------------------
    与 TBL-28 ``audit_log`` 完全同因：MySQL 要求**分区列必须出现在每个唯一键中**。
    按 ``TO_DAYS(created_at)`` 分区，``created_at`` 就必须进主键。
    副作用 ``created_at`` 不可 UPDATE —— 在日志表上是**优点**。

    ② 为什么不继承 :class:`~app.models.base.AppendOnlyMixin`
    ----------------------------------------------------
    该 mixin 提供的是**单列** ``id`` 主键。其余「只追加」语义在本类里显式体现：
    **本表根本没有 ``is_deleted`` / ``updated_at`` 两列** ——
    让「误更新 / 误软删」在 SQL 层就无从下手（三层保证的第 1 层）。

    ③ ``mask_applied`` 是本表的合规价值所在
    ------------------------------------
    ``scene`` 属 ``cloud``（模型在外部）的记录，``mask_applied`` **必须恒为 true**；
    API-88 的 ``summary.mask_applied_rate`` 持续监控，**低于 1.0 即告警**
    （意味着存在未脱敏出网的调用）。该字段是审计时**唯一的抓手**，
    因此不允许默认 ``true`` —— 默认 ``0``，由调用链在真的脱敏后显式置位。
    """

    __tablename__ = "ai_call_log"
    __table_args__ = (
        # 租户 + 时间：列表/看板的主路径（分区裁剪 + 时间倒序分页）
        Index("idx_tenant_time", "tenant_id", "created_at"),
        # 按链路 ID 追查（与业务日志、审计日志同一个 trace_id）
        Index("idx_trace", "trace_id"),
        # 按场景聚合（API-89 成本看板按场景维度）
        Index("idx_tenant_scene", "tenant_id", "scene"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        default=next_id,
        comment="主键（雪花 ID，应用生成）",
    )
    #: 分区列，同时是复合主键的第二列。Python 侧也给默认值 —— 见 audit.py 同类说明：
    #: 只靠 ``server_default`` 时插入后 ORM 对象上该字段仍是 ``None``，而它是主键的一部分。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        primary_key=True,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="调用时间（分区键）",
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="租户 ID"
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="链路 ID")
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="调用用户")
    scene: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="场景：resume_parse/talent_ask/query_rewrite/..."
    )
    #: 🔴 **本次调用实际走的那一路模型的部署位置**（``local`` 不出网 / ``cloud`` 出网）。
    #:
    #: 为什么必须在**调用时**落库，而不是事后从 ``ai_model_config`` 反推：
    #: 配置是**会变的**。某场景今天指向云端、下周 A/B 切回本地之后，
    #: 若按「当前配置」重新判定历史区间的出网调用，合规结论会**随配置漂移** ——
    #: 已正确脱敏的历史记录会被判成「不出网」而退出分母（自证清白），
    #: 而切到云端之后，历史本地调用又会被判成「未脱敏出网」（凭空产生违规告警）。
    #: 合规证据必须**不可追溯地改写**，故它是调用事实的一部分，与 ``cost`` 同级。
    #:
    #: ⚠️ 允许 ``NULL``：调用方（``AiLogService.record``）未显式给出时按
    #: ``AiScene.STAGES`` 的场景固有形态兜底；场景码也不认识时才为 ``NULL``。
    #: ``NULL`` **不计入脱敏率分母**（无法判定出网与否 → 不冤枉也不放过），
    #: 而词表内 8 个场景都有固有形态，故实际数据里它应为空。
    deployment: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="本次调用实际部署位置 local/cloud（出网判定）"
    )
    provider: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="提供方：ollama/dify/openai/cloud"
    )
    model: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="模型名")
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="模型版本")
    prompt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="Prompt ID")
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="Prompt 版本")
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="输入 token"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="输出 token"
    )
    #: 成本核算结果（元）。⚠️ 由 ``CostCalculator`` 按确定性公式算出，**不由 LLM 决定**。
    cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
        comment="成本核算（元）",
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="总耗时 ms")
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="首 token 耗时 ms")
    cache_hit: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, server_default=text("0"), comment="是否命中缓存"
    )
    fallback_used: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, server_default=text("0"), comment="是否走降级"
    )
    #: 🔴 是否经脱敏出网（F-3.9 审计点）。**默认 0**，由调用链在确认已脱敏后显式置 1。
    mask_applied: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, server_default=text("0"), comment="是否经脱敏出网"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 失败 1 成功"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="错误码")
    error_msg: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="错误信息")

    def __repr__(self) -> str:
        return (
            f"<AiCallLog id={self.id} scene={self.scene!r} model={self.model!r} "
            f"status={self.status} mask={self.mask_applied}>"
        )


# ============================================================================
# TBL-29 模型网关配置（本批新增，见模块 docstring 的基线偏离说明）
# ============================================================================
class AiModelConfig(IdTenantCommonMixin, Base):
    """TBL-29 模型网关配置（F-9.1，API-81/82）。

    一条记录 = **一个场景下的一路模型**。同一 ``scene`` 的多条记录按 ``priority``
    升序构成**主备链**（``priority=1`` 是主，``2`` 是备，依次类推），
    由 :mod:`app.core.model_gateway` 依次尝试 —— 主路熔断或超时时自动落到下一路。

    🔴 **``deployment`` 才是出网开关**
    --------------------------------
    ``deployment=local``（Ollama / bge-m3）**不出网**，``cloud`` 才需要 ``mask_applied=true``。
    把「出网与否」做成独立列而不是从 ``provider`` 猜，是因为合规核查要按它分组统计
    （API-88 的 ``scene_stage`` 即由此字段推导），猜错了就是合规事故。

    🔴 **单价放在本表**
    ----------------
    单价是**模型的属性**（不同模型、不同 provider 价不同），放这里可以让
    ``ai_log_service`` 直接算出 ``cost``，而不必再维护一张「场景 × 模型」的价目表。
    ``price_in_per_1k`` / ``price_out_per_1k`` 单位是**元 / 千 token**。
    ⚠️ 本地模型单价为 0（只耗算力不花钱）—— 这是**默认值**，不是「忘了配」。
    """

    __tablename__ = "ai_model_config"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "scene", "model_code", "is_deleted", name="uk_tenant_scene_model"
        ),
        # 路由主路径：按场景取「启用中」的备选链，再按 priority 排序
        Index("idx_tenant_scene_status", "tenant_id", "scene", "status"),
        MYSQL_TABLE_KWARGS,
    )

    #: 场景码。与 ``ai_call_log.scene``、``prompt_template.scene`` **共用同一套取值**，
    #: 由 :class:`app.schemas.ai.AiScene` 集中定义（写错场景码会让成本看板分不出组，
    #: 且失败是「静默少一行统计」而不是报错）。
    scene: Mapped[str] = mapped_column(String(32), nullable=False, comment="场景码")
    model_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="模型配置编码（场景内唯一）"
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名")
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="提供方：ollama / dify / openai / cloud"
    )
    #: ``local``（不出网）/ ``cloud``（出网，须脱敏）。**不是**从 provider 推导，见类 docstring。
    deployment: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default=text("'local'"),
        comment="部署位置 local 本地不出网 / cloud 云端出网",
    )
    base_url: Mapped[str] = mapped_column(String(255), nullable=False, comment="服务地址")
    model_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="实际模型名（如 qwen2.5:3b）"
    )
    #: 主备顺序：1 = 主。⚠️ **不设唯一键** —— 允许两路同优先级（此时按 id 兜底排序），
    #: 硬加唯一键会让「临时把备用提到主」的调整因为「优先级冲突」而失败。
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="主备顺序（1=主）"
    )
    timeout_seconds: Mapped[float] = mapped_column(
        nullable=False, default=60.0, server_default=text("60"), comment="单次调用超时（秒）"
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="失败重试次数"
    )
    #: 熔断：连续失败达到该值即在 ``breaker_cooldown_seconds`` 内**跳过该路**
    #: （不再尝试、直接落下一路），冷却结束后重新放行 —— 避免每笔请求都为
    #: 已死的模型白等一个超时。
    breaker_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3"), comment="熔断阈值（连续失败次数）"
    )
    breaker_cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=text("60"), comment="熔断冷却（秒）"
    )
    price_in_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"), server_default=text("0"),
        comment="输入单价（元/千 token）",
    )
    price_out_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"), server_default=text("0"),
        comment="输出单价（元/千 token）",
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )
    remark: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="备注")

    def __repr__(self) -> str:
        return (
            f"<AiModelConfig {self.scene}/{self.model_code} provider={self.provider!r} "
            f"deployment={self.deployment!r} priority={self.priority} status={self.status}>"
        )
