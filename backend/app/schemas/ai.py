"""AI 中台契约（API-81 ~ API-89，F-9.1 / F-9.2 / F-9.5）。

接口 §2.2 只给了这 9 个接口一行摘要（方法 / 路径 / 权限 / 表），
与 CU-05/07/08 的情况相同 —— **本文件即这些接口的契约事实来源**，
沿用已确立的约定：雪花 ID 一律字符串、时间 `YYYY-MM-DD HH:mm:ss`、
未填字段下发 `null`、请求体 `extra="forbid"`。

## 为什么把「场景码」也集中定义

``ai_call_log.scene`` / ``prompt_template.scene`` / ``ai_model_config.scene``
**三张表共用同一套场景码**，而它是 ``VARCHAR(32)`` 自由文本 —— 数据库管不住它。

写错一个字母的后果比「查不到」更糟：**成本看板会安静地多出一行**
（``talent_ask`` 与 ``talen_ask`` 各占一行），总额对得上但分不出组，
排查时才会发现统计口径早就裂了。故用 :class:`AiScene` 集中定义，
写入方与筛选校验共用同一份（与 :class:`app.schemas.audit.AuditAction` 同源处理）。

## 出网判定（``scene_stage``）

接口 §3 API-88 的响应里有 ``scene_stage``（``local`` / ``cloud``）。
本文件的取值是**每个场景的固有属性**：``talent_ask`` 一定是 ``cloud``
（生成走云端模型），``resume_parse`` 一定是 ``local``（抽取在本地 Ollama）。

⚠️ 它与 ``ai_model_config.deployment`` 是**两个层次**：
前者是「这个场景的默认部署形态」（用于展示与合规分组），
后者是「这一路具体模型的真实部署位置」（用于路由与判定是否必须脱敏）。
**判定合规看后者**，前者只用于界面提示 —— 两者理论上应一致，
但 A/B 切换期可能不一致（如临时把 ``talent_ask`` 指到本地模型），
此时 ``mask_applied_rate`` 的分母必须以 ``deployment`` 为准。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.schemas.common import CommonResp, DateTimeStr, SnowflakeId, build_page_info

# ============================================================================
# 常量
# ============================================================================
#: ``ai_call_log`` 查询区间上限（接口 §3 API-88：默认最近 7 天，最长 90 天）。
#: 理由：本表按月分区，跨月区间无法被分区裁剪。
AI_CALL_DEFAULT_RANGE_DAYS = 7
AI_CALL_MAX_RANGE_DAYS = 90

#: 成本看板（API-89）的默认区间：比调用日志长（看板关心趋势，不看单次）。
AI_COST_DEFAULT_RANGE_DAYS = 30

#: 单次调用日志的导出/展示上限，与审计导出同口径（超出报 40002 而非截断）。
AI_CALL_EXPORT_MAX_ROWS = 50000

#: 金额序列化：DB 是 ``DECIMAL(12,6)``，下发按**字符串 + 4 位小数**
#: （接口 §3 的示例即 ``"0.0421"``）。用字符串而非 float：
#: 金额经过 JSON float 往返会引入二进制误差，前端再累加就会出现「合计对不上」。
MoneyStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: f"{Decimal(v):.4f}", return_type=str, when_used="json"),
]

#: ``prompt_code`` 的格式：**大写字母 / 数字 / 下划线，且不以 ``_`` 开头**。
#:
#: 🔴 为什么必须显式写成正则而不是用 ``str.isalnum()``：
#: ``"简历抽取".isalnum()`` 在 Python 里是 **True**（汉字属 alphanumeric），
#: 于是「只允许字母数字下划线」这句注释实际上**没有拦住中文**——
#: 编码是给人看、给日志筛、给前端下拉用的标识符，混进中文后
#: 「按编码 grep 调用日志」这类运维动作会立刻失效。
#: 与字典（CU-07 的 ``CODE_PATTERN``）同源处理：格式写在契约层，入口就拒绝。
PROMPT_CODE_PATTERN = r"[A-Z0-9][A-Z0-9_]*"


class AiScene:
    """AI 场景码**受控词表**（三张表共用；字符串常量，不是 Enum）。

    为什么不用 ``enum.StrEnum``：``scene`` 列是 VARCHAR 自由文本，
    词表属应用层约定；用 Enum 会在「历史日志里存在已废弃场景」时让**读取侧反序列化失败**。
    日志表必须能读十年前的数据 → 常量 + 标签映射，未知值读取时降级（标签为 ``None``）。
    """

    RESUME_PARSE = "resume_parse"
    QUERY_REWRITE = "query_rewrite"
    TALENT_ASK = "talent_ask"
    TAG_SUGGEST = "tag_suggest"
    JUDGE = "judge"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    OCR = "ocr"

    #: 场景 → 中文名（API-88 的 ``scene_name``）
    LABELS: ClassVar[dict[str, str]] = {
        RESUME_PARSE: "简历字段抽取",
        QUERY_REWRITE: "检索问题改写",
        TALENT_ASK: "档案 RAG 问答",
        TAG_SUGGEST: "标签建议",
        JUDGE: "AI 判定",
        EMBEDDING: "向量化",
        RERANK: "精排",
        OCR: "OCR 识别",
    }

    #: 场景 → **固有部署形态**（API-88 的 ``scene_stage``）。见模块 docstring。
    STAGES: ClassVar[dict[str, str]] = {
        RESUME_PARSE: "local",
        QUERY_REWRITE: "local",
        TALENT_ASK: "cloud",
        TAG_SUGGEST: "local",
        JUDGE: "local",
        EMBEDDING: "local",
        RERANK: "local",
        OCR: "local",
    }

    @classmethod
    def all(cls) -> list[str]:
        return list(cls.LABELS)

    @classmethod
    def label_of(cls, scene: str | None) -> str | None:
        """未知场景返回 ``None``（**不抛异常** —— 历史数据里可能有已废弃场景）。"""
        if not scene:
            return None
        return cls.LABELS.get(scene)

    @classmethod
    def stage_of(cls, scene: str | None) -> str | None:
        if not scene:
            return None
        return cls.STAGES.get(scene)


class AiDeployment:
    """模型部署位置（``ai_model_config.deployment``）—— **出网判定的唯一依据**。"""

    LOCAL = "local"
    CLOUD = "cloud"
    LABELS: ClassVar[dict[str, str]] = {LOCAL: "本地（不出网）", CLOUD: "云端（出网）"}


class AiProvider:
    """模型提供方（``ai_model_config.provider``）。"""

    OLLAMA = "ollama"
    DIFY = "dify"
    OPENAI = "openai"
    CLOUD = "cloud"
    LABELS: ClassVar[dict[str, str]] = {
        OLLAMA: "Ollama（本地）",
        DIFY: "Dify 网关",
        OPENAI: "OpenAI 兼容",
        CLOUD: "云端通道",
    }


class PromptScene:
    """``prompt_template.scene`` 的受控词表（数据库 §4.6：``extract/rewrite/ask/judge``）。

    ⚠️ 它与 :class:`AiScene` **不是同一套取值**，这是上游文档的既有设计
    （Prompt 的 ``scene`` 更粗，一个 Prompt 可服务多个调用场景）。
    本批**不强行合并** —— 合并会让 Prompt 的 ``scene`` 语义被调用场景绑架
    （加一个新调用场景就要加一条 Prompt 场景）。保留两套，并在种子数据里
    建立对应关系（见 ``seed_dev.py`` 的 ``_seed_ai``）。
    """

    EXTRACT = "extract"
    REWRITE = "rewrite"
    ASK = "ask"
    JUDGE = "judge"
    LABELS: ClassVar[dict[str, str]] = {
        EXTRACT: "字段抽取",
        REWRITE: "问题改写",
        ASK: "档案问答",
        JUDGE: "判定打分",
    }


# ============================================================================
# API-81/82 · 模型配置
# ============================================================================
class ModelConfigOut(BaseModel):
    """单条模型配置（API-81 列表项 / API-82 修改结果）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    scene: str = Field(description="场景码")
    scene_name: str | None = Field(None, description="场景中文名")
    model_code: str = Field(description="模型配置编码")
    display_name: str
    provider: str
    provider_name: str | None = None
    deployment: str = Field(description="`local` 不出网 / `cloud` 出网")
    deployment_name: str | None = None
    base_url: str
    model_name: str
    priority: int = Field(description="主备顺序，1 = 主")
    timeout_seconds: float
    max_retries: int
    breaker_threshold: int
    breaker_cooldown_seconds: int
    #: 单价（元 / 千 token）。本地模型为 0 —— 是**默认值**，不是「忘了配」。
    price_in_per_1k: MoneyStr
    price_out_per_1k: MoneyStr
    status: int = Field(description="0 停用 1 启用")
    status_name: str | None = None
    remark: str | None = None
    created_at: DateTimeStr
    updated_at: DateTimeStr


class ModelConfigUpdateRequest(BaseModel):
    """API-82 修改模型配置与主备路由。

    全部字段可选（PATCH 语义）—— 但**至少改一项**，空请求体直接 40001：
    「什么都没改的成功响应」会让前端误以为提交生效了。

    ⚠️ ``base_url`` 与 ``model_name`` 放在一起可改，是因为换模型实例时两者
    几乎总是一起变；但**改它们等于换了一路模型**，故服务层强制写审计日志。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(None, min_length=1, max_length=128)
    provider: str | None = Field(None, max_length=32)
    deployment: str | None = Field(None, description="`local` / `cloud`")
    base_url: str | None = Field(None, min_length=1, max_length=255)
    model_name: str | None = Field(None, min_length=1, max_length=128)
    priority: int | None = Field(None, ge=1, le=99, description="主备顺序，1 = 主")
    timeout_seconds: float | None = Field(None, gt=0, le=600)
    max_retries: int | None = Field(None, ge=0, le=5)
    breaker_threshold: int | None = Field(None, ge=1, le=100)
    breaker_cooldown_seconds: int | None = Field(None, ge=1, le=3600)
    price_in_per_1k: Decimal | None = Field(None, ge=0)
    price_out_per_1k: Decimal | None = Field(None, ge=0)
    status: int | None = Field(None, ge=0, le=1)
    remark: str | None = None

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in AiProvider.LABELS:
            raise ValueError(
                f"未知的 provider={v!r}；合法取值：{', '.join(sorted(AiProvider.LABELS))}"
            )
        return v

    @field_validator("deployment")
    @classmethod
    def _check_deployment(cls, v: str | None) -> str | None:
        if v is not None and v not in AiDeployment.LABELS:
            raise ValueError(
                f"未知的 deployment={v!r}；合法取值：{', '.join(sorted(AiDeployment.LABELS))}"
            )
        return v

    @model_validator(mode="after")
    def _require_at_least_one(self) -> ModelConfigUpdateRequest:
        # ``model_fields_set`` 才能区分「没传」与「传了 None」（PATCH 语义的关键）
        if not self.model_fields_set:
            raise ValueError("请至少提供一个待修改字段")
        return self


class ModelTargetOut(BaseModel):
    """健康状态里的一路模型（API-83）。

    ``healthy`` 是**探测结果**，``blocked`` 是**熔断状态** —— 两者不同：
    - ``healthy=false`` 说明这一路当前不可用（探测失败或未探测）；
    - ``blocked=true`` 说明它已被熔断器**跳过**，在 ``cooldown_remaining_seconds`` 内
      不会有任何请求打到它（即使它其实恢复了）。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    scene: str = Field(description="场景码（按场景分组判断「主路是否可用」，故必须下发）")
    scene_name: str | None = None
    model_code: str
    display_name: str
    provider: str
    deployment: str
    model_name: str
    priority: int
    status: int
    status_name: str | None = None
    healthy: bool = Field(description="本轮探测是否成功")
    latency_ms: int | None = Field(None, description="探测耗时（失败为 null）")
    error: str | None = Field(None, description="探测失败原因（**已脱敏**）")
    blocked: bool = Field(False, description="是否处于熔断冷却期")
    consecutive_failures: int = Field(0, description="熔断器记录的连续失败次数")
    cooldown_remaining_seconds: int = Field(0, description="熔断剩余冷却秒数")


class ModelHealthOut(BaseModel):
    """API-83 模型健康与熔断状态。"""

    model_config = ConfigDict(extra="forbid")

    checked_at: DateTimeStr
    total: int
    healthy: int
    unavailable: int
    blocked: int
    #: 整体是否可用：**至少有一路主模型可用**即为 true。
    #: ⚠️ 刻意不要求「全部健康」—— 备用链路挂了不影响服务，
    #: 若按「全部健康」判定，运维会为了一个备用模型被反复告警。
    all_primary_healthy: bool
    #: 熔断器后端（Redis）是否可用。不可用时熔断状态**全部视为未熔断**（fail-open）
    #: —— 与限流同一条推理：基础设施抖动不得升级为业务不可用。
    breaker_backend_ok: bool
    items: list[ModelTargetOut]


# ============================================================================
# API-84 ~ API-87 · Prompt 版本管理
# ============================================================================
class PromptOut(BaseModel):
    """Prompt 模板（**一个版本**一条记录）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    prompt_code: str
    scene: str
    scene_name: str | None = None
    version: int
    content: str
    status: int = Field(description="0 草稿 1 启用 2 停用")
    status_name: str | None = None
    created_by: SnowflakeId | None = None
    created_at: DateTimeStr
    updated_at: DateTimeStr


class PromptCreateRequest(BaseModel):
    """API-85 新建 Prompt 模板（``version`` 固定从 1 开始，不接受传入）。"""

    model_config = ConfigDict(extra="forbid")

    prompt_code: str = Field(min_length=2, max_length=64, description="模板编码，大写字母/数字/下划线")
    scene: str = Field(description="场景：extract/rewrite/ask/judge")
    content: str = Field(min_length=1, description="模板正文（含变量占位）")
    status: int = Field(0, ge=0, le=2, description="0 草稿 1 启用 2 停用，默认草稿")
    remark: str | None = Field(None, max_length=255, description="变更说明（写入审计 after 快照）")

    @field_validator("prompt_code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        """规范化并校验模板编码：去空白 → 转大写 → 必须匹配 :data:`PROMPT_CODE_PATTERN`。

        ⚠️ 转大写而不是「拒绝小写」：``prompt_code`` 是要**手写**在代码与配置里的标识符，
        「输入小写被拒」在实践中只会让人反复试错；字典（``item_code``）选择拒绝大写，
        是因为那边已有大量小写历史数据、且排序规则大小写不敏感（见 CU-07 的说明）。
        两处口径不同是**有意的**，各自的理由都写下来了。
        """
        import re

        code = v.strip().upper()
        if not re.fullmatch(PROMPT_CODE_PATTERN, code or ""):
            raise ValueError(
                f"prompt_code={v!r} 不合规；只允许大写字母、数字与下划线，且不以 _ 开头"
                f"（匹配 {PROMPT_CODE_PATTERN}）"
            )
        return code

    @field_validator("scene")
    @classmethod
    def _check_scene(cls, v: str) -> str:
        if v not in PromptScene.LABELS:
            raise ValueError(
                f"未知的 scene={v!r}；合法取值：{', '.join(sorted(PromptScene.LABELS))}"
            )
        return v


class PromptUpdateRequest(BaseModel):
    """API-86 修改 Prompt（**生成新版本，不改历史**）。

    🔴 这里**没有** ``prompt_code`` / ``version`` 字段，且 ``extra="forbid"``：
    「改编码」等于换了一个模板族（历史版本全部失去归属），
    「改版本号」等于篡改历史 —— 两者都不该出现在这个契约里，
    多传即 **40001**（而不是被静默忽略）。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="新的模板正文")
    status: int | None = Field(None, ge=0, le=2, description="新版本的状态；缺省继承源版本")
    remark: str | None = Field(None, max_length=255, description="变更说明")


class PromptRollbackRequest(BaseModel):
    """API-87 回滚到指定版本。

    🔴 回滚**不是**「把旧版本重新置为启用」，而是**以旧版本正文追加一个新版本**
    （``version = max + 1``）。理由见 ``services/prompt_service`` 的模块 docstring：
    原地复活旧版本会破坏「版本号单调递增 ⇒ 版本号即时间线」这条性质，
    且会让「第 3 版在启用」出现两次而无法区分是哪一次启用。
    """

    model_config = ConfigDict(extra="forbid")

    target_version: int = Field(ge=1, description="要回滚到的版本号（必须已存在）")
    remark: str | None = Field(None, max_length=255, description="回滚原因")


# ============================================================================
# API-88 · AI 调用日志
# ============================================================================
class AiCallLogOut(BaseModel):
    """单条调用日志（**不含任何档案内容或问题原文** —— 见下方说明）。

    🔴 接口 §3 备注：AIOPS **只能看元数据与日志，不含业务数据明文**。
    本契约刻意**只有元数据字段**（模型 / token / 耗时 / 成本 / 脱敏标记）：
    结构上不存在的字段就永远不会被误返回，这比「记得别返回」可靠。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    trace_id: str | None = None
    scene: str | None = None
    scene_name: str | None = None
    scene_stage: str | None = Field(
        None,
        description=(
            "`local` / `cloud`。**本次调用实际**的部署位置（`ai_call_log.deployment`），"
            "仅在未记录时才退回场景固有形态 —— 混合主备链下降级到本地的那次调用"
            "不该被打上「云端」徽标"
        ),
    )
    model: str | None = None
    provider: str | None = None
    prompt_id: SnowflakeId | None = None
    prompt_code: str | None = Field(None, description="由 `prompt_id` 反查（历史模板已停用也能显示）")
    prompt_version: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_yuan: MoneyStr
    latency_ms: int | None = None
    first_token_ms: int | None = None
    cache_hit: bool = False
    fallback_used: bool = False
    #: 🔴 **合规核查的唯一抓手**：``scene_stage=cloud`` 的记录中该字段必须恒为 ``true``。
    mask_applied: bool = False
    status: int = Field(description="0 失败 1 成功")
    status_name: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    created_at: DateTimeStr


class AiCallSummary(BaseModel):
    """API-88 的 ``summary``（与 ``page`` 同级下发）。"""

    model_config = ConfigDict(extra="forbid")

    calls: int = 0
    cost_total_yuan: MoneyStr = Decimal("0")
    avg_latency_ms: int = 0
    failed: int = 0
    #: 脱敏率。🔴 **分母只含「出网调用」**（``ai_call_log.deployment='cloud'``
    #: —— 由调用方在**调用时刻**落库的出网事实，见 ``AiLogService.summary``）。
    mask_applied_rate: float = 0.0
    cache_hit_rate: float = 0.0
    #: ``cloud`` 调用的总数 —— 让「脱敏率」的分母显式可见，
    #: 否则前端看到 ``mask_applied_rate=1.0`` 却不知道它是在几个调用上算出来的。
    cloud_calls: int = 0
    #: ``cost_total_yuan`` 是否超过 ``settings.AI_COST_ALERT_YUAN``。
    #: ⚠️ 接口 §3 备注：「本接口只呈报不告警」—— 这里只**标记**，不发通知。
    cost_alert: bool = False
    #: ``mask_applied_rate`` 是否低于 ``settings.AI_MASK_APPLIED_RATE_FLOOR``。
    #: 🔴 为真意味着**存在未脱敏出网的调用**，属合规事件，必须被看到。
    mask_alert: bool = False


class AiCallFilterRequest(BaseModel):
    """API-88 查询参数（走与审计日志相同的「显式 Query + 依赖收进模型」路径）。

    时间区间：**默认最近 7 天，上限 90 天**（接口 §3 API-88）。
    ⚠️ 默认值在此处**不填**（留 ``None``），由服务层用「请求时刻」计算 ——
    若在这里写 ``default_factory``，模型一旦被缓存或跨日复用，
    区间就会固定在「导入时刻的 7 天前」，这是很隐蔽的一类错误。
    """

    model_config = ConfigDict(extra="forbid")

    scene: str | None = Field(None, max_length=32)
    model: str | None = Field(None, max_length=64)
    status: int | None = Field(None, ge=0, le=1, description="0 失败 1 成功")
    cache_hit: bool | None = None
    mask_applied: bool | None = Field(None, description="是否经脱敏出网（合规核查用）")
    user_id: SnowflakeId | None = None
    trace_id: str | None = Field(None, max_length=64)
    start_time: datetime | None = Field(None, description="起始时间（含），`YYYY-MM-DD HH:mm:ss`")
    end_time: datetime | None = Field(None, description="截止时间（含）")

    @field_validator("scene", "model", "trace_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """空串视为未传（前端清空筛选后常发出 ``?scene=``）。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("scene")
    @classmethod
    def _check_scene(cls, v: str | None) -> str | None:
        """场景码必须在词表内 —— 拼错会「查得到 0 条」，让人误以为「这段没调用」。
        报 40001 并列出合法取值，把猜测变成明确提示（同 ``AuditAction`` 处理）。"""
        if v is not None and v not in AiScene.LABELS:
            raise ValueError(f"未知的 scene={v!r}；合法取值：{', '.join(AiScene.all())}")
        return v

    @model_validator(mode="after")
    def _check_order(self) -> AiCallFilterRequest:
        """只校验「起止颠倒」。**跨度上限（90 天）在服务层校验** ——
        因为接口 §3 要求超限时返回 ``data.max_days`` / ``data.requested_days``，
        而 Pydantic 校验失败会走全局 40001 处理器（``data`` 里只有 ``fields[]``），
        形状对不上。故此处不管跨度，由服务层抛带 ``data`` 的 BizError。"""
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time 不能晚于 end_time")
        return self


# ============================================================================
# API-89 · 成本看板
# ============================================================================
class CostBucketOut(BaseModel):
    """一个维度的成本桶（场景 / 模型 / 日期）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="维度取值（场景码 / 模型名 / `YYYY-MM-DD`）")
    label: str | None = Field(None, description="展示名（场景码会翻成中文；模型 / 日期即原值）")
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_yuan: MoneyStr
    avg_latency_ms: int = 0


class CostDashboardOut(BaseModel):
    """API-89 Token 成本看板（场景 / 模型 / 时间三维度）。

    🔴 **三个维度必须能对上总数**：``by_scene`` / ``by_model`` / ``by_time``
    各自的 ``cost_yuan`` 之和都应等于 ``total.cost_yuan``（同一区间、同一租户）。
    三者由同一段 SQL 骨架产出（``ai_dao._aggregate``），不是三份手写查询 ——
    否则「场景维度 100 元、模型维度 98 元」这类差异会先被用户发现。
    """

    model_config = ConfigDict(extra="forbid")

    start_time: DateTimeStr
    end_time: DateTimeStr
    total: AiCallSummary
    by_scene: list[CostBucketOut]
    by_model: list[CostBucketOut]
    by_time: list[CostBucketOut]


# ============================================================================
# API-88 响应体：``summary`` 与 ``page`` 同级
# ============================================================================
class AiCallLogListResp(CommonResp[list[AiCallLogOut]]):
    """AI 调用日志列表响应（接口 §3 API-88 的响应示例）。

    🔴 **``summary`` 必须与 ``page`` 同级、而不是塞进 ``data``**
    --------------------------------------------------------
    接口 §3 的响应示例里 ``summary`` 就是顶层的第三个键（与 ``page`` 并列）。
    别为了「少一个字段」把它塞进 data —— ``data`` 已约定**只放数组**（§1.1：
    「不得把 items 整包塞进 data」），改结构会让前端的分页/汇总读取路径两套并存。

    ⚠️ 为什么不直接加进 :class:`~app.schemas.common.CommonResp`：
    全平台 95 个接口里只有 API-88 带顶层 ``summary``，
    为它放宽公共契约会让另外 94 个接口的响应形状与文档不符（多出一个恒为
    ``null`` 的键），而 ``extra="forbid"`` 的契约一旦放宽就再也收不回来。
    """

    model_config = ConfigDict(extra="forbid")

    summary: AiCallSummary | None = Field(
        None, description="本次筛选条件下的汇总指标（与 `page` 同级，接口 §3）"
    )


def call_log_page(
    items: list[AiCallLogOut],
    total: int,
    *,
    page: int,
    per_page: int,
    summary: AiCallSummary,
) -> AiCallLogListResp:
    """构造 API-88 的响应（``data`` 放数组 + 同级 ``page`` + 同级 ``summary``）。

    与 :func:`app.schemas.common.paginated` 同源处理 —— ``total`` 取的是
    **筛选条件下的总数**（不是本页条数），``summary`` 同样按筛选条件聚合，
    两者口径一致；否则会出现「条数 20 / 汇总 1743」这类读不懂的组合。
    """
    # 局部导入避免 schemas → core 的模块级依赖（与 ``ok()`` 同一手法）
    from app.core.context import get_trace_id

    return AiCallLogListResp(
        code=0,
        message="ok",
        data=items,
        page=build_page_info(total, page, per_page),
        summary=summary,
        trace_id=get_trace_id(),
    )


__all__ = [
    "AI_CALL_DEFAULT_RANGE_DAYS",
    "AI_CALL_EXPORT_MAX_ROWS",
    "AI_CALL_MAX_RANGE_DAYS",
    "AI_COST_DEFAULT_RANGE_DAYS",
    "AiCallFilterRequest",
    "AiCallLogListResp",
    "AiCallLogOut",
    "AiCallSummary",
    "AiDeployment",
    "AiProvider",
    "AiScene",
    "CostBucketOut",
    "CostDashboardOut",
    "ModelConfigOut",
    "ModelConfigUpdateRequest",
    "ModelHealthOut",
    "ModelTargetOut",
    "MoneyStr",
    "PROMPT_CODE_PATTERN",
    "PromptCreateRequest",
    "PromptOut",
    "PromptRollbackRequest",
    "PromptScene",
    "PromptUpdateRequest",
    "call_log_page",
]

