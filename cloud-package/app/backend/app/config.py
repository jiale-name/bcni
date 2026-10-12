"""全局配置。

架构 §5 红线：**全部配置经 settings 读取，禁止硬编码**。
本文件是唯一的配置入口 —— 业务代码不得直接读 ``os.environ``。

配置文件加载顺序（后者被前者覆盖）：进程环境变量 > ``backend/.env`` > 仓库根 ``.env``。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: 开发环境 JWT 占位密钥（**仅**用于本地跑通链路；生产必须替换）。
#: 长度 75 字节 —— 满足 RFC 7518 §3.2 对 HS256 密钥 ≥ 32 字节的建议。
DEV_JWT_SECRET_PLACEHOLDER = (
    "CHANGE_ME_IN_ENV_FILE__dev_only_placeholder_secret__do_not_use_in_production"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ==================================================================
    # 应用
    # ==================================================================
    APP_NAME: str = "AI 数字化人才平台"
    APP_VERSION: str = "0.1.0"
    APP_ENV: Literal["dev", "test", "prod"] = "dev"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8002  # 架构 §7.3：后端固定 8002
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # ==================================================================
    # DS-1 MySQL（一期唯一关系库，28 表；ORM + Alembic 管理）
    # ==================================================================
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    #: 🔴 库名**偏离设计文档 DS-1**（DS-1 写 `talent_platform`），原因是本机
    #: ``talent_platform`` 已被 2026-08-30 的旧 schema（11 表、AUTO_INCREMENT 主键、
    #: 无 ``tenant_id``）连同真实数据占用，且其 ``sys_user`` 与本项目迁移的 ``sys_user``
    #: 同名不同构 —— 直接建表必冲突。故改用 ``talent_platform_v2``，旧库原样保留。
    #: 这里改默认值（而非只改 .env）是为了让「漏配 .env」的新机器也**默认指向安全的那一个**，
    #: 避免有人 clone 后顺手 migrate 到旧库上炸掉。
    MYSQL_DATABASE: str = "talent_platform_v2"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600  # 秒，避免 MySQL 8h 空闲断连
    DB_CONNECT_TIMEOUT: int = 5  # 秒；连接失败要快速暴露（/health 探针依赖它）

    #: 🔴 **审计专用库账号**（CU-24，「只追加」的**第三层**保证）。
    #:
    #: 前两层是「模型没有 ``is_deleted``/``updated_at`` 列」+「DAO 只导出 insert/list/count」
    #: —— 它们挡的是**应用代码**，挡不住「有人直接连库写 SQL」。
    #: 这一层从**数据库权限**上把 ``UPDATE``/``DELETE`` 收走：
    #: 审计写入走一个只有 ``SELECT`` + ``INSERT`` 的账号，于是「改审计」在库级就不可能。
    #:
    #: ⚠️ 留空 = **退回主账号**（功能不受影响，只是第三层保证不生效）。
    #: 这样设计是为了不让「本地开发忘了配」直接导致审计写不进去 ——
    #: 一个 fail-open 的降级，比一个「审计全丢」的硬失败安全。
    #: 建号 SQL 见 ``deploy/mysql-init/01-audit-account.sql``。
    AUDIT_DB_USER: str = ""
    AUDIT_DB_PASSWORD: str = ""

    # ==================================================================
    # DS-2 Milvus（仅存脱敏检索单元；N4/N5 启用）
    # ==================================================================
    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: int = 19530
    MILVUS_DATABASE: str = "talent_vec"
    MILVUS_TIMEOUT: int = 10

    # ==================================================================
    # DS-3 Redis（sess / cache / rate / agent_mem / queue）
    # ==================================================================
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_KEY_PREFIX: str = ""
    # 🔴 RESP 协议版本。**默认 2，不要轻易改成 3**：
    #   redis-py 8.x 的默认值是 3，连接时会先发 ``HELLO 3`` 协商，而 `HELLO` 是 Redis 6.0
    #   才引入的命令 —— 本机/国产化环境中的旧版或 Windows 版 Redis 会直接报
    #   ``unknown command `HELLO```，表现为「Redis 明明起着却连不上」。
    #   本项目不需要 RESP3 的推送特性，故固定 RESP2 以获得最大兼容性。
    REDIS_PROTOCOL: int = 2

    # ==================================================================
    # DS-4 MinIO（原始简历文件，ADR-10）
    # ==================================================================
    MINIO_ENDPOINT: str = "127.0.0.1:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "talent-files"
    MINIO_SECURE: bool = False

    # ==================================================================
    # AI 算力（D-4 混合算力路由 / ADR-6 / ADR-7）
    # ==================================================================
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL_PARSE: str = "qwen2.5:3b"  # 字段抽取（本地，不出网）
    OLLAMA_MODEL_REASON: str = "qwen3:8b"  # 推理增强（本地）
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIM: int = 1024  # 架构 §4.3 / 数据库 §7
    RERANKER_MODEL: str = "bge-reranker-v2-m3"
    RERANKER_BASE_URL: str = "http://127.0.0.1:9997"
    DIFY_BASE_URL: str = "http://127.0.0.1"
    DIFY_API_KEY: str = ""

    # 🔴 出网通道（ADR-4 / D-15）：仅接收「脱敏占位符片段 + Prompt」
    CLOUD_LLM_BASE_URL: str = ""
    CLOUD_LLM_API_KEY: str = ""
    CLOUD_LLM_MODEL: str = ""

    # ==================================================================
    # RAG 参数基线（需求 §10.2 / D-14 / B-8）—— 全部可配，禁止散落魔法数
    # ==================================================================
    RAG_RECALL_TOP_K: int = 20  # 召回 Top-20
    RAG_RRF_K: int = 60  # RRF 融合 k=60
    RAG_RERANK_TOP_N: int = 5  # 精排 Top-5
    RAG_NO_ANSWER_THRESHOLD: float = 0.35  # 无答案阈值 < 0.35 直接兜底
    RAG_RETRIEVAL_MODE: Literal["vector", "keyword", "hybrid"] = "hybrid"

    # ==================================================================
    # 确定性阈值（需求 §10.1 约束³：置信度阈值判定）
    # ==================================================================
    PARSE_CONFIDENCE_THRESHOLD: float = 0.85
    PARSE_LOW_IMPACT_SAMPLE_RATE: float = 0.10  # 低影响字段 10% 抽样复核
    DEDUP_SIMILARITY_THRESHOLD: float = 0.90

    # ==================================================================
    # AI 网关 / 成本 / Agent（F-9.1 / F-9.5 / F-A0，CU-10 ~ CU-13）
    # ==================================================================
    #: 模型配置里未指定超时时的兜底值（秒）。单个模型可在 ``ai_model_config`` 里覆盖。
    AI_GATEWAY_DEFAULT_TIMEOUT: float = 60.0
    #: 新区间内成本超过该值（元）时，API-88 的 ``summary`` 带 ``cost_alert=true``。
    #: ⚠️ 接口 §3 备注明确「**本接口只呈报不告警**」—— 故这里只用于**标记**，
    #: 真正的告警动作（发通知 / 写告警表）属运维侧，不在本接口内做。
    AI_COST_ALERT_YUAN: float = 100.0
    #: ``scene_stage=cloud`` 调用的脱敏率（``mask_applied_rate``）下限。
    #: **低于 1.0 即意味着存在未脱敏出网的调用**（接口 §3 的合规红线），
    #: 故默认 1.0，且 API-88 会在低于该值时置 ``mask_alert=true``。
    AI_MASK_APPLIED_RATE_FLOOR: float = 1.0
    #: Agent 图单次运行的最大步数（死循环兜底）。
    #: 与 ``agents/graph.py`` 的 ``DEFAULT_MAX_STEPS`` 同义，此处为**可配置覆盖**：
    #: 图定义里显式给了 ``max_steps`` 时以图为准。
    AGENT_MAX_STEPS: int = 50
    #: ``agent_step`` 的保留月数（数据库 §9：保留 6 个月，超期清理）。
    #: 仅被 ``scripts/audit_partition.py`` 的清理提示使用 —— 实际清理是运维动作，
    #: **不由应用自动执行**（自动删执行轨迹是不可逆的，必须有人按下按钮）。
    AGENT_STEP_RETENTION_MONTHS: int = 6

    # ==================================================================
    # 认证与安全（F-1.2）
    # ==================================================================
    #: 🔴 开发环境占位密钥。长度 ≥ 32 字节以满足 RFC 7518 对 HS256 的建议
    #: （过短的密钥会让 PyJWT 抛 InsecureKeyLengthWarning，也真的不安全）。
    #: **生产环境必须替换** —— 见 .env.example / README §4.1。
    JWT_SECRET_KEY: str = DEV_JWT_SECRET_PLACEHOLDER
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_SECONDS: int = 7200  # 2h，前端不得硬编码
    REFRESH_TOKEN_EXPIRE_SECONDS: int = 604800  # 7d
    BCRYPT_ROUNDS: int = 12
    LOGIN_MAX_FAIL_COUNT: int = 5  # 连续 5 次失败
    LOGIN_LOCK_SECONDS: int = 600  # 锁定 10 分钟
    AES_KEY: str = ""  # 敏感字段（身份证/手机/邮箱）加密密钥，64 位 hex
    #: 联系方式**比对哈希**（TBL-11 的 `phone_hash` / `id_card_hash`）的盐。
    #: 手机号仅 10^11 个可能值，裸 SHA256 在 GPU 上秒级可穷举 —— 必须加盐，
    #: 否则「哈希存储」等于变相明文（详见 `core/crypto.hash_contact` 的 docstring）。
    #: 🔴 与 `AES_KEY` **必须是不同的值**：一个用于不可逆比对、一个用于可逆解密。
    #: 🔴 一经使用不可更换 —— 换盐会让已入库的全部哈希失效（需配套数据重刷方案）。
    #: 未配置时 `hash_contact` 直接抛错，❌ 不静默退化成不加盐的弱哈希。
    CONTACT_HASH_SALT: str = ""

    # ==================================================================
    # 雪花 ID（数据库 §0.1：应用生成，有符号 BIGINT）
    # ==================================================================
    SNOWFLAKE_WORKER_ID: int = 1  # 0~31
    SNOWFLAKE_DATACENTER_ID: int = 1  # 0~31
    SNOWFLAKE_EPOCH_MS: int = 1704038400000  # 2024-01-01T00:00:00Z

    # ==================================================================
    # 限流（F-1.6）+ CORS
    # ==================================================================
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 300  # 按 uid + 接口维度（接口 §1.4 第 1 层）
    # 🔴 ``NoDecode`` 必不可少：pydantic-settings 对 ``list`` 类型字段**默认按 JSON 解析**
    #    （在字段校验器之前执行 ``json.loads``）。若 .env 写 ``CORS_ORIGINS=http://a,http://b``
    #    就会抛 ``SettingsError: error parsing value for field "CORS_ORIGINS"`` ——
    #    而且是在**导入 settings 时**炸掉，表现为「服务完全起不来」。
    #    加 ``NoDecode`` 后原始字符串直通校验器，由下面 ``_split_cors_origins`` 负责切分
    #    （同时兼容 JSON 数组写法，避免使用者两种写法二选一踩坑）。
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # ==================================================================
    # 上传上限（接口 §1.5 → 41301；Q-25：上限值配置化）
    # ==================================================================
    UPLOAD_MAX_FILES_PER_BATCH: int = 100
    UPLOAD_MAX_FILE_SIZE_MB: int = 50
    #: 原始简历文件的本地留存目录（CU-14）。MinIO（DS-4）就绪前本地落盘，路径由本项唯一控制；
    #: MinIO 接入后本项仅作降级兜底（登记于评审文档批次 10 遗留）。
    UPLOAD_STORAGE_DIR: str = "data/uploads"
    #: 复核工作量估算的平均单条耗时（秒，接口 §4.2 API-44/49 口径，默认 20s）
    REVIEW_AVG_SECONDS_PER_ITEM: float = 20.0
    # ==================================================================
    # 个人权利响应（CU-22，F-3.11 / SM-3）
    # ==================================================================
    #: 权利请求的处理时限（小时，接口 API-72 备注「处理时限 ``sla_hours`` 可配置」）。
    #: 默认 72h（3 个工作日量级，《个保法》惯例的响应窗口）。
    #:
    #: 🔴 该值在**提交时刻快照**进 ``privacy_request.sla_hours``（不由请求行实时读取配置）——
    #: 否则改一次配置会让**历史单据**的「是否超时」集体反转，
    #: 一份已经出过的合规报告会变得无法复核。
    PRIVACY_SLA_HOURS: int = 72

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, v: object) -> object:
        """兼容三种写法（配合字段上的 ``NoDecode``）。

        .. code-block:: ini

            CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173   # 逗号分隔（推荐）
            CORS_ORIGINS=["http://localhost:5173"]                     # JSON 数组
            # 或留空 → 使用默认值
        """
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    return []  # 非法 JSON：交由后续校验报错，不静默用默认值
                return parsed if isinstance(parsed, list) else []
            return [item.strip() for item in text.split(",") if item.strip()]
        return v

    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串（PyMySQL 驱动）。"""
        pwd = quote_plus(self.MYSQL_PASSWORD)
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{pwd}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )

    @property
    def audit_database_url(self) -> str:
        """审计写入专用连接串；未配置 ``AUDIT_DB_USER`` 时**退回主连接串**。

        ⚠️ 两个连接串**只有账号不同**（host/port/db 必须一致）：
        审计表与业务表在同一个库里，「拆账号」拆的是**权限**而不是数据位置。
        把 host/db 也做成可配只会多出一条「审计写到了另一个库」的错配路径。
        """
        if not self.AUDIT_DB_USER:
            return self.database_url
        pwd = quote_plus(self.AUDIT_DB_PASSWORD)
        return (
            f"mysql+pymysql://{self.AUDIT_DB_USER}:{pwd}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{quote_plus(self.REDIS_PASSWORD)}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"

    @property
    def upload_max_file_size_bytes(self) -> int:
        return self.UPLOAD_MAX_FILE_SIZE_MB * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例（lru_cache）。测试可用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()


settings = get_settings()
