"""N0 探针实现与注册表（CU-27）。

覆盖需求 §12.1 N0 的第 ③④⑤⑥⑦⑧⑩ 项
--------------------------------------
- ③ 验证现有 MySQL / Redis / Milvus / Ollama / Dify 可用性 → ``mysql`` / ``redis`` / ``milvus`` /
  ``ollama`` / ``dify``（另加架构 DS-4 的 ``minio``，它是 F-3.1 导入链路的落盘位置）
- ④ 模型就绪验证（H-17）：bge-m3 维度与吞吐、Reranker 延迟、OCR 依赖完整 → ``embedding`` /
  ``reranker`` / ``ocr``
- ⑤ 国产化清单 → **不由脚本产出**（属甲方问答，见 ``targets.N0_DELIVERABLES``）
- ⑥ Milvus collection 与索引规划 → 脚本给可用性与版本，规划由人工定稿
- ⑦ 基线压测 → 4 项给出单点实测值（另 2 项见 ``targets`` 的 ``load`` 范围）
- ⑧ OCR 选型验证 → 脚本给「依赖完整度」清单
- ⑩ 数据出网边界 → 由 ``cloud_channel`` 探针给出「是否配置 / RTT」事实

三条实现纪律
------------
1. **每个探针独立兜底**：连不上、没装包、没配密钥，一律转成结果对象，
   由 ``result.run_spec`` 再兜一层预期外异常 —— 一条挂掉不影响整份报告。
2. **不猜端口、不猜模型名**：全部取自 ``settings``，保证「探针测的就是业务用的那一套」。
3. **测量口径写进 ``detail`` / ``metrics``**：单连接串行吞吐**不是**并发压测、
   TCP RTT **不是** HTTP 全链路 —— 这类话必须出现在报告里，否则数据会被误用。
"""

from __future__ import annotations

import importlib.util
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine

from app.config import settings
from app.dao.redis_client import get_redis
from scripts.n0.result import Probe, ProbePayload, ProbeSpec, ProbeStatus, run_spec

__all__ = [
    "GROUPS",
    "PROBE_SPECS",
    "KNOWN_KEYS",
    "specs_by_key",
    "run_all",
    "set_embedding_rounds",
    "embedding_rounds",
]

#: 分组 → 中文名（报告按组分节）
GROUPS: dict[str, str] = {
    "env": "环境依赖可用性（需求 §12.1 N0 ③）",
    "model": "模型就绪验证（需求 §12.1 N0 ④ · H-17 / D-19）",
    "ops": "选型与规划前置（需求 §12.1 N0 ⑥⑧）",
    "perf": "性能基线（需求 §6.1 基线压测先行原则）",
}

# 各类探测的独立超时（本地模型在 CPU 上很慢，不能和探活用同一个超时）
PROBE_TIMEOUT = 5.0  # 探活类（HTTP 元数据接口 / TCP）
EMBEDDING_TIMEOUT = 120.0  # CPU 推理首次加载模型可能很慢
RERANKER_TIMEOUT = 120.0
REDIS_THROUGHPUT_ROUNDS = 200
RTT_ROUNDS = 3


@contextmanager
def _direct_client(timeout: float) -> Iterator[httpx.Client]:
    """创建一个**不读代理环境变量**的 httpx 客户端（``trust_env=False``）。

    🔴 为什么必须这样（2026-09-23 实测）
    ------------------------------------
    本机与企政内网普遍设有 ``HTTP_PROXY=http://127.0.0.1:60458`` 这类变量。
    裸 ``httpx.get`` / ``httpx.post`` 会**读取**它，于是对 ``127.0.0.1:9997``
    的请求被交给**代理**去连，而不是直连本机端口。后果是探针结论被系统性污染：

    - 服务**没起**时，代理回 **502 Bad Gateway** —— 探针把「端口无监听」
      报成「服务端 502」。这会把排障方向带向「查服务端日志 / 模型加载失败」，
      而真相是**服务根本没启动**；
    - 服务**起着**时，代理可能按策略拦截（403 / 502 / 超时）。

    同一时刻实测对照::

        curl --noproxy '*' http://127.0.0.1:9997/rerank   → 连接失败（真相）
        curl               http://127.0.0.1:9997/rerank   → 502（代理伪造的）

    这与 :mod:`app.core.model_gateway` 的 ``trust_env=False`` **同源同因** ——
    业务链路早就规避了，探针这一侧此前漏了。两处必须保持一致，
    否则会出现「网关能连上 Ollama，但 N0 报告说 Ollama 不通」这种自相矛盾的结论。
    """
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        yield client

# 🔴 探针签名统一为无参（便于注册表统一调度），故「采样次数」用模块级可调项，
#    由 CLI 的 ``--embedding-rounds`` 覆盖。默认 8：既够摊平冷启动，又不至于让
#    CPU 推理把一次 N0 全量探测拖到十几分钟。
_EMBEDDING_ROUNDS = 8


def set_embedding_rounds(n: int) -> None:
    """设置 Embedding 吞吐采样次数（CLI 用）。"""
    if n < 1:
        raise ValueError("采样次数必须 ≥ 1")
    global _EMBEDDING_ROUNDS  # noqa: PLW0603 — 见上方注释：无参探针的必经妥协
    _EMBEDDING_ROUNDS = n


def embedding_rounds() -> int:
    return _EMBEDDING_ROUNDS


# ============================================================================
# 一、环境依赖可用性
# ============================================================================
def probe_mysql() -> ProbePayload:
    """MySQL（DS-1）：连接 + 版本 + 库 + 表数 + 两类查询耗时。"""
    engine = None
    try:
        engine = create_engine(
            settings.database_url,
            connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
            poolclass=None,  # 探测用一次性引擎，不复用业务连接池
        )
        t0 = time.perf_counter()
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT VERSION(), DATABASE(), @@character_set_database, @@collation_database"
            ).first()
            connect_ms = (time.perf_counter() - t0) * 1000
            version, dbname, charset, collation = (str(v) for v in row)

            table_count = int(
                conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()"
                ).scalar_one()
            )

            # 用一个「真实存在但规模为零」的聚合查询取元数据耗时：
            # information_schema 在 MySQL 8 上是数据字典视图，能反映「复杂查询」的起步成本。
            t1 = time.perf_counter()
            conn.exec_driver_sql(
                "SELECT table_name, COUNT(*) AS cols FROM information_schema.columns "
                "WHERE table_schema = DATABASE() GROUP BY table_name"
            ).all()
            meta_query_ms = (time.perf_counter() - t1) * 1000
    except Exception as exc:  # noqa: BLE001 — 探针统一兜底
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"MySQL 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                "① 确认 MySQL 已启动且端口可达；"
                f"② 确认库 `{settings.MYSQL_DATABASE}` 已建（utf8mb4 / utf8mb4_unicode_ci）；"
                "③ 确认 `backend/.env` 的 `MYSQL_USER` / `MYSQL_PASSWORD` 已填"
                "（本机密码需人工填入，见 README §4.1）；"
                "④ 未跑迁移则先 `alembic upgrade head`。"
            ),
        )
    finally:
        if engine is not None:
            engine.dispose()

    return ProbePayload(
        status=ProbeStatus.OK,
        detail=(
            f"MySQL {version} · 库 {dbname} · {table_count} 张表 · 字符集 {charset}/{collation}"
        ),
        metrics={
            "version": version,
            "database": dbname,
            "charset": charset,
            "collation": collation,
            "table_count": table_count,
            "connect_ms": round(connect_ms, 2),
            "meta_query_ms": round(meta_query_ms, 2),
        },
    )


def probe_redis() -> ProbePayload:
    """Redis（DS-3）：PING + 版本 + SET/GET 往返吞吐。"""
    try:
        client = get_redis()
        t0 = time.perf_counter()
        client.ping()
        ping_ms = (time.perf_counter() - t0) * 1000
        info = client.info("server")
        version = str(info.get("redis_version") or "unknown")
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Redis 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                "① 确认 Redis 已启动；"
                "② 若报 `unknown command 'HELLO'`，说明服务端 < 6.0，"
                "把 `REDIS_PROTOCOL` 固定为 2（本项目默认已是 2，勿改成 3）；"
                "③ 确认 `REDIS_PASSWORD` 与 `requirepass` 一致；"
                "④ Windows 版 Redis 见 README 排障章节。"
            ),
        )

    key = f"{settings.REDIS_KEY_PREFIX}n0:probe:throughput"
    try:
        t0 = time.perf_counter()
        for i in range(REDIS_THROUGHPUT_ROUNDS):
            client.set(key, f"{i}")
            client.get(key)
        elapsed = time.perf_counter() - t0
        client.delete(key)
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Redis 连通但读写压测失败：{type(exc).__name__}: {exc}",
            remediation="检查是否触发了 maxmemory 逐出或 ACL 限制（`CONFIG GET maxmemory-policy`）。",
        )

    ops_total = REDIS_THROUGHPUT_ROUNDS * 2  # SET + GET 各算一次
    ops_per_sec = ops_total / elapsed if elapsed > 0 else 0.0
    return ProbePayload(
        status=ProbeStatus.OK,
        detail=(
            f"Redis {version} · PING {ping_ms:.1f}ms · "
            f"单连接串行 {ops_per_sec:,.0f} ops/s（{ops_total} 次 / {elapsed:.3f}s）"
        ),
        metrics={
            "version": version,
            "ping_ms": round(ping_ms, 2),
            "ops_per_sec": round(ops_per_sec, 1),
            "rounds": REDIS_THROUGHPUT_ROUNDS,
            "elapsed_s": round(elapsed, 4),
            "measurement": "single_connection_serial",
        },
    )


def probe_milvus() -> ProbePayload:
    """Milvus（DS-2）：健康 + 版本 + **配置的库是否存在** + 该库内 collection 列表。

    ``pymilvus`` 是 **N4 的正式依赖**，N1 阶段不在 requirements 里。因此「未装」
    是**预期内**状态 → ``blocked`` 而非 ``failed``；但 N0 的实测任务要求必须测，
    所以它的 ``remediation`` 直接给出安装命令。

    🔴 **本探针必须与生产同源**
    --------------------------
    生产侧（``app/retrieval/milvus_provider.MilvusProvider._connect``）用的是
    **``MilvusClient``** —— ``pymilvus`` 3.x 的推荐 API。本探针原先用 ORM 式的
    ``connections.connect`` + ``utility.*``，装 ``pymilvus 3.0.2`` 后报
    ``ConnectionNotExistException: should create connection first``（**而服务其实是好的**：
    19530 在监听、版本 2.6.1）。⇒ **探针与生产不同源，就会报出生产根本不会遇到的错误**；
    这类「假失败」比不测更坏 —— 它把返工引到错误的地方。

    🔴 **为什么必须单独检查「库是否存在」**
    ------------------------------------
    生产 ``_connect`` 传了 ``db_name=settings.MILVUS_DATABASE``。**库不存在时**，
    ``list_collections()`` 抛 ``code=800 database not found``，而 ``_connect`` 会
    **捕获它并返回 None ⇒ 向量检索静默降级为关键词检索**（HTTP 200 + ``code=50301``）。
    ⇒ 「服务好、库没建」这种**配置级不一致**在运行期**表现为「降级」而不是「报错」**，
    极易被当成「Milvus 没装 / 不可用」而长期误判。故 N0 必须把它测出来并**指名**。
    """
    if importlib.util.find_spec("pymilvus") is None:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未安装 pymilvus 客户端，Milvus 可用性**尚未验证**（N0 必做项）",
            remediation=(
                "`.venv/Scripts/python.exe -m pip install pymilvus`（N4 正式依赖），"
                "装完重跑本探针；随后按需求 §12.1⑥ 规划 collection 与索引"
                "（度量方式建议 COSINE，维度须等于 `EMBEDDING_DIM`）。"
            ),
        )

    try:
        from pymilvus import MilvusClient  # 与生产同源（见 docstring）

        # ⚠️ 这里**故意不传 db_name**：先以「服务器视角」枚举数据库，
        #    才能判断「配置的库是否存在」。传了 db_name 的话，库不存在会直接抛异常，
        #    连「服务到底是好是坏」都答不上来。
        client = MilvusClient(
            uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
            timeout=settings.MILVUS_TIMEOUT,
        )
        version = str(client.get_server_version())
        databases = [str(name) for name in client.list_databases()]
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Milvus 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                f"① 确认 Milvus 容器已启动、{settings.MILVUS_HOST}:{settings.MILVUS_PORT} 可达"
                "（先 `netstat -ano | findstr 19530` 看端口，**不要用走代理的 curl 判断**）；"
                "② 确认客户端与服务端主版本兼容（本机实测 pymilvus 3.x 对 Milvus 2.6.x 可用）；"
                "③ 国产 OS/CPU 环境须按 R-16 另做实机验证或书面豁免。"
            ),
        )

    # ---- 库维度：配置的库是否真的存在（见 docstring 的说明）----
    if settings.MILVUS_DATABASE not in databases:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail=(
                f"Milvus {version} **连通**，但**配置的库 `{settings.MILVUS_DATABASE}` 不存在** —— "
                f"服务端现有库：{databases}。⚠️ 生产 `MilvusProvider._connect()` 会把"
                "「库不存在」当成连接失败并**静默降级为关键词检索**（HTTP 200 + `code=50301`），"
                "故这一项必须在 N0 闭环，否则 `pymilvus` 装了也等于没装。"
            ),
            metrics={
                "version": version,
                "databases": databases,
                "configured_database": settings.MILVUS_DATABASE,
                "configured_database_exists": False,
                "host": settings.MILVUS_HOST,
                "port": settings.MILVUS_PORT,
            },
            remediation=(
                f"① **建库**（推荐，符合「每项目一个库」的隔离意图）：\n"
                f"   `MilvusClient(uri='http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}')"
                f".create_database('{settings.MILVUS_DATABASE}')`\n"
                f"② 或**改配置**：把 `MILVUS_DATABASE` 指向已存在的库（本机为 `default`）—— "
                "⚠️ 本机 `default` 库内有**其他项目**的 collection（如 `json_documents_milvus` / "
                "`vanna_train`），多项目共用同一 Milvus 时**不建议**混库；\n"
                "③ 建库后重跑本探针 → 应转为 🟡 降级（连通 + 待灌数）。⚠️ 勿用本项结果回答 IDX-04"
                "（规模性能需 N4 灌入 100 万向量后按 P95 ≤ 500ms / 200 并发补测）。"
            ),
        )

    # 库存在 → 再看库内 collection（与生产 `_connect` 的入参完全一致）
    try:
        db_client = MilvusClient(
            uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
            db_name=settings.MILVUS_DATABASE,
            timeout=settings.MILVUS_TIMEOUT,
        )
        collections = [str(name) for name in db_client.list_collections()]
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Milvus 库 `{settings.MILVUS_DATABASE}` 存在但列举 collection 失败：{type(exc).__name__}: {exc}",
            remediation="检查 Milvus 服务日志与鉴权配置（本机默认无鉴权）。",
        )

    return ProbePayload(
        # ⚠️ 即使连通也**不判 ok 就是 ok**：N0 的真实交付是「100 万向量规模下的延迟与 QPS」，
        #    空集合连得上只能证明「服务能用」，故如实标 degraded 并在 detail 里说清差距。
        status=ProbeStatus.DEGRADED,
        detail=(
            f"Milvus {version} 连通 · 库 `{settings.MILVUS_DATABASE}` 存在 · "
            f"现有 collection {len(collections)} 个 —— "
            "**规模性能未验证**（§6.1① 要求 100 万向量下的延迟与 QPS，需灌数后复测）"
        ),
        metrics={
            "version": version,
            "databases": databases,
            "configured_database": settings.MILVUS_DATABASE,
            "configured_database_exists": True,
            "collection_count": len(collections),
            "collections": collections,
            "million_scale_measured": False,
            "host": settings.MILVUS_HOST,
            "port": settings.MILVUS_PORT,
        },
        remediation=(
            "N0 仅能验证可用性。规模结论请在 N4 灌入 100 万向量后按 IDX-04 口径"
            "（P95 ≤ 500ms @ 100 万向量 / 200 并发）补测，勿用本项结果回答 IDX-04。"
        ),
    )


def probe_minio() -> ProbePayload:
    """MinIO（DS-4）：原始简历文件落盘位置（ADR-10）。"""
    if importlib.util.find_spec("minio") is None:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未安装 minio 客户端，MinIO 可用性未验证",
            remediation="`.venv/Scripts/python.exe -m pip install minio`（已在 requirements.txt 内）。",
        )
    if not settings.MINIO_ACCESS_KEY or not settings.MINIO_SECRET_KEY:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未配置 MINIO_ACCESS_KEY / MINIO_SECRET_KEY，跳过 MinIO 探测",
            remediation="在 `backend/.env` 填入 MinIO 访问凭据（默认端口 9000）。",
        )

    from minio import Minio

    try:
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        exists = client.bucket_exists(settings.MINIO_BUCKET)
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"MinIO 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                f"① 确认 MinIO 已启动、{settings.MINIO_ENDPOINT} 可达；"
                "② 确认凭据为「访问密钥」而非控制台账号密码；"
                "③ 若本机**本就没部署** MinIO，属预期状态（本项不阻塞 N1 编码）—— "
                "它只在 N2 导入链路（F-3.1 落原始文件）启用前必须就绪；"
                "④ 「连接被拒绝」与「服务挂了」在协议层无法区分，故此处统一报异常，请按 ① 排查。"
            ),
        )

    if not exists:
        return ProbePayload(
            status=ProbeStatus.DEGRADED,
            detail=f"MinIO 连通但桶 `{settings.MINIO_BUCKET}` 不存在",
            metrics={"bucket": settings.MINIO_BUCKET, "bucket_exists": False},
            remediation=(
                f"建桶：`mc mb local/{settings.MINIO_BUCKET}`，"
                "或由 N2 的导入链路在首次写入时自动建桶（需确认实现）。"
            ),
        )

    return ProbePayload(
        status=ProbeStatus.OK,
        detail=f"MinIO 连通 · 桶 `{settings.MINIO_BUCKET}` 已存在",
        metrics={
            "endpoint": settings.MINIO_ENDPOINT,
            "bucket": settings.MINIO_BUCKET,
            "bucket_exists": True,
            "secure": settings.MINIO_SECURE,
        },
    )


def probe_dify() -> ProbePayload:
    """Dify（DS-5 集成层）：需 ``DIFY_API_KEY`` 才能验证。"""
    if not settings.DIFY_API_KEY:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未配置 DIFY_API_KEY，Dify 可用性未验证",
            remediation=(
                "在 Dify 控制台创建应用并复制 API Key 写入 `backend/.env` 的 `DIFY_API_KEY`；"
                f"同时确认 `DIFY_BASE_URL`（当前 `{settings.DIFY_BASE_URL}`）指向本机 Dify 服务。"
            ),
        )

    url = f"{settings.DIFY_BASE_URL.rstrip('/')}/v1/parameters"
    try:
        t0 = time.perf_counter()
        with _direct_client(PROBE_TIMEOUT) as client:
            resp = client.get(
                url,
                params={"user": "n0-probe"},
                headers={"Authorization": f"Bearer {settings.DIFY_API_KEY}"},
            )
            resp.raise_for_status()
        rtt_ms = (time.perf_counter() - t0) * 1000
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Dify 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                f"① 确认 Dify 已启动且 `{settings.DIFY_BASE_URL}` 可达（自托管常见端口 80 / 3000）；"
                "② 确认 API Key 属于**已发布**的应用（草稿态取不到 parameters）；"
                "③ 确认 URL 以 `/v1` 结尾而非 `/v1/`。"
            ),
        )

    return ProbePayload(
        status=ProbeStatus.OK,
        detail=f"Dify 连通 · /v1/parameters {rtt_ms:.0f}ms",
        metrics={"base_url": settings.DIFY_BASE_URL, "rtt_ms": round(rtt_ms, 2)},
    )


# ============================================================================
# 二、模型就绪验证
# ============================================================================
def _model_present(name: str, models: list[str]) -> str | None:
    """在 ``ollama list`` 的结果里找 ``name``。

    先精确匹配，再退回**同族匹配**（``qwen2.5:3b`` → 任意 ``qwen2.5:*``）。
    同族命中**不算就绪**，但会在 ``detail`` 里点名 —— 避免「拉了个近似 tag 就以为就绪」。
    """
    for model in models:
        if model == name:
            return model
    base = name.split(":", 1)[0]
    for model in models:
        if model.split(":", 1)[0] == base:
            return model
    return None


def probe_ollama() -> ProbePayload:
    """Ollama 服务 + 配置的两个模型是否在册。"""
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags"
    try:
        t0 = time.perf_counter()
        with _direct_client(PROBE_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
        rtt_ms = (time.perf_counter() - t0) * 1000
        payload: dict[str, Any] = resp.json()
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"Ollama 不可用：{type(exc).__name__}: {exc}",
            remediation=(
                f"① 确认 Ollama 已启动（`ollama list`）；② 确认 {settings.OLLAMA_BASE_URL} 可达；"
                f"③ 拉取模型：`ollama pull {settings.OLLAMA_MODEL_PARSE}` · "
                f"`ollama pull {settings.OLLAMA_MODEL_REASON}`。"
            ),
        )

    models = [
        str(item.get("name") or item.get("model") or "")
        for item in payload.get("models", [])
        if isinstance(item, dict)
    ]

    expected = {
        "parse": settings.OLLAMA_MODEL_PARSE,
        "reason": settings.OLLAMA_MODEL_REASON,
    }
    missing: list[str] = []
    approx: list[str] = []
    resolved: dict[str, str] = {}
    for role, name in expected.items():
        hit = _model_present(name, models)
        if hit is None:
            missing.append(name)
        elif hit != name:
            approx.append(f"{name} → 实际在册 {hit}")
            resolved[role] = hit
        else:
            resolved[role] = hit

    if missing:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,  # 服务在、模型未拉 → 前置未就绪，不是行为异常
            detail=f"Ollama 连通（{rtt_ms:.0f}ms）但在册模型缺少：{', '.join(missing)}",
            metrics={"models": models, "missing": missing, "rtt_ms": round(rtt_ms, 2)},
            remediation=(
                "逐个拉取：" + "；".join(f"`ollama pull {name}`" for name in missing)
                + "。注意 Ollama 的 tag 必须与 `OLLAMA_MODEL_PARSE` / `OLLAMA_MODEL_REASON` 完全一致。"
            ),
        )

    detail = f"Ollama 连通（{rtt_ms:.0f}ms）· 在册 {len(models)} 个模型 · 配置模型齐备"
    if approx:
        detail += "；⚠️ 同族近似命中：" + "；".join(approx) + "（**非精确 tag**，请确认是否可用）"

    return ProbePayload(
        status=ProbeStatus.DEGRADED if approx else ProbeStatus.OK,
        detail=detail,
        metrics={
            "version_rtt_ms": round(rtt_ms, 2),
            "models": models,
            "resolved": resolved,
            "approx_hits": approx,
        },
        remediation=(
            "同族近似命中的 tag 与配置不一致：要么把配置改成实际 tag，要么拉取配置指定的 tag。"
            "两者不一致时，N2 的抽取链路会因找不到模型而直接失败。"
            if approx
            else ""
        ),
    )


def _ollama_embed(text: str, model: str) -> tuple[list[float], str]:
    """调 Ollama 取向量，兼容新旧两种端点。

    - 新版：``POST /api/embed`` → ``{"embeddings": [[...]]}``
    - 旧版：``POST /api/embeddings`` → ``{"embedding": [...]}``

    两种都试，任一成功即返回 —— 因为「端点名不同」不代表环境不可用，
    而 N0 要的是**维度与吞吐事实**。
    """
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    attempts: tuple[tuple[str, dict[str, Any], str], ...] = (
        (f"{base}/api/embed", {"model": model, "input": text}, "embeddings"),
        (f"{base}/api/embeddings", {"model": model, "prompt": text}, "embedding"),
    )
    last_error = ""
    for url, body, field in attempts:
        try:
            with _direct_client(EMBEDDING_TIMEOUT) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
            data = resp.json()
            raw: Any = data.get(field)
            if field == "embeddings" and isinstance(raw, list) and raw:
                raw = raw[0]
            if isinstance(raw, list) and raw:
                return [float(x) for x in raw], url
            last_error = f"{url} 返回体缺少 {field}"
        except Exception as exc:  # noqa: BLE001 — 换下一个端点继续试
            last_error = f"{url}: {type(exc).__name__}: {exc}"
    raise RuntimeError(f"两种 Embedding 端点均不可用（最后错误：{last_error}）")


def probe_embedding() -> ProbePayload:
    """bge-m3：维度是否等于 ``EMBEDDING_DIM`` + 单条吞吐。"""
    model = settings.EMBEDDING_MODEL
    sample = "N0 基线实测：用于验证向量维度与推理吞吐的样本句子。"

    try:
        vector, endpoint = _ollama_embed(sample, model)
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail=f"bge-m3 不可用：{exc}",
            remediation=(
                f"① 确认已拉取模型：`ollama pull {model}`；"
                "② 确认 Ollama 版本支持 `/api/embed`（旧版走 `/api/embeddings`，脚本两者都试）；"
                f"③ 确认 `EMBEDDING_MODEL` 配置与在册 tag 一致（当前 `{model}`）。"
            ),
        )

    dim = len(vector)
    expected_dim = settings.EMBEDDING_DIM

    # 吞吐：逐轮换文本 —— Ollama 对**完全相同的 prompt** 会命中 KV cache，
    # 用同一句反复压测会得到虚高吞吐（第二次起几乎零计算）。拼入序号保证真的前向计算。
    rounds = _EMBEDDING_ROUNDS
    try:
        t0 = time.perf_counter()
        for i in range(rounds):
            _ollama_embed(f"{sample}（第 {i + 1} 次采样）", model)
        elapsed = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        return ProbePayload(
            status=ProbeStatus.DEGRADED,
            detail=f"bge-m3 维度 {dim} 可用，但吞吐采样失败：{type(exc).__name__}: {exc}",
            metrics={"dim": dim, "expected_dim": expected_dim, "endpoint": endpoint},
            remediation="降低 `--embedding-rounds` 后重试；若持续失败检查 Ollama 内存占用。",
        )

    per_call_ms = (elapsed / rounds) * 1000
    calls_per_sec = rounds / elapsed if elapsed > 0 else 0.0
    dim_ok = dim == expected_dim

    detail = (
        f"bge-m3 维度 {dim}"
        + ("（与 EMBEDDING_DIM 一致）" if dim_ok else f" ⚠️ 与配置 {expected_dim} **不一致**")
        + f" · 单条 {per_call_ms:.0f}ms / {calls_per_sec:.2f} 次每秒（{rounds} 次采样）"
    )

    return ProbePayload(
        status=ProbeStatus.OK if dim_ok else ProbeStatus.DEGRADED,
        detail=detail,
        metrics={
            "model": model,
            "endpoint": endpoint,
            "dim": dim,
            "expected_dim": expected_dim,
            "dim_match": dim_ok,
            "per_call_ms": round(per_call_ms, 2),
            "calls_per_sec": round(calls_per_sec, 3),
            "rounds": rounds,
            "measurement": "unique_text_per_round",
        },
        remediation=(
            ""
            if dim_ok
            else (
                f"维度不一致会导致 N4 建 collection 时写入报错。**必须先二选一**："
                f"改 `EMBEDDING_DIM` 为 {dim}，或换用真正的 {expected_dim} 维模型；"
                "在此之前不要开始 N4。"
            )
        ),
    )


def probe_reranker() -> ProbePayload:
    """bge-reranker-v2-m3：单次精排延迟（需求 §6.1⑤）。

    依次尝试三种主流部署的端点形状（TEI / Infinity / Xinference），
    与 ``_ollama_embed`` 同理：端点形状不同不代表环境不可用。
    """
    base = settings.RERANKER_BASE_URL.rstrip("/")
    query = "3 年以上 AI 项目经验的数字化转型骨干"
    docs = [
        "负责企业数字化转型项目，主导 AI 中台建设，5 年相关经验。",
        "从事财务核算与报表编制，熟悉用友 U8。",
        "参与机器学习平台研发，3 年深度学习项目经验。",
    ]
    attempts: tuple[tuple[str, dict[str, Any], str], ...] = (
        (f"{base}/rerank", {"query": query, "texts": docs}, "TEI /rerank(texts)"),
        (f"{base}/rerank", {"query": query, "documents": docs}, "Infinity /rerank(documents)"),
        (f"{base}/v1/rerank", {"query": query, "documents": docs}, "Xinference /v1/rerank"),
    )

    errors: list[str] = []
    #: 三种形状**全部**是「连接不上」时，真相是「服务没起」而非「端点形状不对」。
    #: 分开记账是为了让最终结论说人话（见下方 refused_count 分支）。
    refused_count = 0
    for url, body, shape in attempts:
        try:
            t0 = time.perf_counter()
            with _direct_client(RERANKER_TIMEOUT) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
            latency_ms = (time.perf_counter() - t0) * 1000
            results = resp.json()
        except httpx.ConnectError as exc:
            # 🔴 连接层失败 = 端口无监听 / 服务未启动，属「前置未就绪」（blocked）；
            #    与 HTTP 5xx（服务起着但内部出错，属 failed）的处置动作毫无交集，
            #    必须分开记账，否则排障方向会被带偏。
            refused_count += 1
            errors.append(f"{shape} → 服务未监听（{type(exc).__name__}）")
            continue
        except Exception as exc:  # noqa: BLE001 — 换下一种端点形状
            errors.append(f"{shape} → {type(exc).__name__}: {exc}")
            continue

        count = len(results) if isinstance(results, list) else len(results.get("results", []))
        return ProbePayload(
            status=ProbeStatus.OK,
            detail=(
                f"Reranker 连通（{shape}）· 单次精排 {latency_ms:.0f}ms · "
                f"{len(docs)} 条候选返回 {count} 条打分"
            ),
            metrics={
                "model": settings.RERANKER_MODEL,
                "base_url": settings.RERANKER_BASE_URL,
                "endpoint_shape": shape,
                "latency_ms": round(latency_ms, 2),
                "candidates": len(docs),
                "scored": count,
            },
        )

    if refused_count == len(attempts):
        # 全部连接不上 → **服务未部署 / 未启动**，属 blocked（列入待办，由人工启动），
        # 而**不是** failed（failed 的语义是「服务在、但行为异常」）。
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail=(
                f"Reranker 服务未监听：{settings.RERANKER_BASE_URL} 无进程在听"
                f"（{len(attempts)} 种端点形状均连接失败）"
            ),
            metrics={
                "model": settings.RERANKER_MODEL,
                "base_url": settings.RERANKER_BASE_URL,
                "attempts": len(attempts),
                "refused": refused_count,
            },
            remediation=(
                f"① 启动 Reranker 服务并确认端口在听：`netstat -ano | findstr :9997` 应见 LISTENING；"
                f"② 装载 `{settings.RERANKER_MODEL}`"
                "（TEI 常见 9997；Infinity 9997；Xinference 9997 + `/v1`）；"
                "③ 服务起来后重跑本探针即可转为 ✅ —— 本项只取决于「有没有起服务」，无需改代码。"
                "⚠️ 本机若设有 HTTP_PROXY，`curl http://127.0.0.1:9997/...` 会得到**代理伪造的 502**，"
                "排障请加 `--noproxy '*'` 才看到真相（探针已用 `trust_env=False` 规避）。"
            ),
        )

    return ProbePayload(
        status=ProbeStatus.FAILED,
        detail="Reranker 服务可连但行为异常（三种端点形状均未成功）：" + "；".join(errors),
        remediation=(
            f"① 确认 Reranker 服务已启动、`{settings.RERANKER_BASE_URL}` 可达"
            "（TEI / Infinity 常见端口 9997，Xinference 常见 9997 + `/v1`）；"
            f"② 确认已装载 `{settings.RERANKER_MODEL}`；"
            "③ **若报 502 Bad Gateway**：先排除**本机 HTTP 代理** —— 若 "
            "`curl http://127.0.0.1:9997/rerank` 得 502 而 "
            "`curl --noproxy '*' http://127.0.0.1:9997/rerank` 得「连接失败」，"
            "则该 502 由代理伪造，真相是**服务未启动**；排除代理后仍 502，"
            "才说明端口上有网关/反向代理在监听而后端模型未就绪"
            "（常见于服务启动中、模型加载失败、或内存/显存不足被杀）；"
            "④ 若报远程主机强迫关闭连接：多为客户端与服务端协议不匹配"
            "（如往 HTTP 端口打了 HTTPS），或服务只监听 IPv6；"
            "⑤ 若部署为其它端点形状，请在 `probes.probe_reranker` 的 attempts 中补一种并回归测试。"
        ),
    )


# ============================================================================
# 三、选型与规划前置
# ============================================================================
#: OCR 后端（图片 / 扫描件简历必需 —— 需求 F-3.2 明确「图片/扫描件单列口径」）
OCR_BACKENDS: dict[str, str] = {
    "cnocr": "CnOCR（中文场景轻量，依赖 onnxruntime）",
    "rapidocr_onnxruntime": "RapidOCR（ONNX Runtime，部署轻、无 torch 依赖）",
    "paddleocr": "PaddleOCR（中文识别效果好，体积大；国产化适配须实机验证）",
    "pytesseract": "Tesseract（需另装系统二进制 + chi_sim 语言包）",
    "easyocr": "EasyOCR（依赖 torch，体积大）",
}

#: 文档解析后端（文本层可提取的 PDF / Word / Excel，无需 OCR）
DOC_BACKENDS: dict[str, str] = {
    "fitz": "PyMuPDF（PDF 文本层，速度快）",
    "pdfplumber": "pdfplumber（PDF 文本与表格，纯 Python）",
    "docx": "python-docx（Word .docx）",
    "openpyxl": "openpyxl（Excel，已装）",
    "PIL": "Pillow（图片读取，OCR 前置）",
}


def _available(modules: dict[str, str]) -> list[str]:
    return [mod for mod in modules if importlib.util.find_spec(mod) is not None]


def probe_ocr() -> ProbePayload:
    """OCR / 文档解析依赖完整度（需求 §12.1 N0 ④⑧）。

    N0 要验的是「依赖是否齐全」，不是「识别率多高」—— 后者需要样本与选型结论，
    属人工项（见 ``targets.N0_DELIVERABLES`` 的「OCR 选型验证报告」）。
    """
    ocr_have = _available(OCR_BACKENDS)
    doc_have = _available(DOC_BACKENDS)
    metrics: dict[str, Any] = {
        "ocr_available": ocr_have,
        "doc_available": doc_have,
        "ocr_candidates": list(OCR_BACKENDS),
        "doc_candidates": list(DOC_BACKENDS),
    }

    if not doc_have and not ocr_have:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未检测到任何文档解析或 OCR 后端，导入链路（F-3.1）无解析能力",
            metrics=metrics,
            remediation=(
                "至少装一组：文本件 `pip install pymupdf python-docx`；"
                "图片件 `pip install rapidocr-onnxruntime`（轻量，推荐先试）或 `paddleocr`。"
                "⚠️ 信创环境须按 R-16 实机验证后再定稿选型。"
            ),
        )

    if not ocr_have:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail=(
                f"仅有文档解析后端（{', '.join(doc_have)}），**无 OCR 后端** —— "
                "图片 / 扫描件简历无法解析（F-3.2 要求该口径单列统计，故必须有 OCR）"
            ),
            metrics=metrics,
            remediation=(
                "装一个 OCR 后端：`pip install rapidocr-onnxruntime`（轻量推荐）或 "
                "`pip install paddleocr`（中文效果更好但更重）；"
                "注意 `pytesseract` / `easyocr` 还需额外的系统级依赖（Tesseract 二进制 / torch）。"
            ),
        )

    return ProbePayload(
        status=ProbeStatus.OK,
        detail=(
            f"OCR 后端 {len(ocr_have)} 个（{', '.join(ocr_have)}）· "
            f"文档解析后端 {len(doc_have)} 个（{', '.join(doc_have)}）"
        ),
        metrics=metrics,
        remediation=(
            ""
            if "openpyxl" in doc_have
            else "注意：openpyxl 未装，而它是 API-17 用户导入的既有依赖，环境可能不完整。"
        ),
    )


# ============================================================================
# 四、性能基线（云通道）
# ============================================================================
def probe_cloud_channel() -> ProbePayload:
    """云 GPU / 云 LLM 通道 RTT（需求 §6.1⑥ · D-4）。

    ⚠️ 测的是 **TCP 建连 RTT**，不是 HTTP 全链路耗时 —— 前者反映「网络距离」，
    后者还要叠加 TLS 与排队。报告里会写明这个区别，避免被当成接口延迟。
    """
    target = (settings.CLOUD_LLM_BASE_URL or "").strip()
    if not target:
        return ProbePayload(
            status=ProbeStatus.BLOCKED,
            detail="未配置 CLOUD_LLM_BASE_URL，云通道未启用（D-19 一期默认全本地推理）",
            remediation=(
                "本项**不影响一期开工**。仅当 N0 压测显示本地 bge-m3 / Reranker 吞吐不足、"
                "需要按 D-4 评估 Embedding 上云时，才配置 `CLOUD_LLM_BASE_URL` 并重跑本探针"
                "（须同时遵守 D-15 脱敏边界）。"
            ),
        )

    parts = urlsplit(target if "://" in target else f"https://{target}")
    host = parts.hostname
    if not host:
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"CLOUD_LLM_BASE_URL 无法解析出主机名：{target!r}",
            remediation="配置应形如 `https://api.example.com/v1`。",
        )
    port = parts.port or (443 if parts.scheme == "https" else 80)

    samples: list[float] = []
    errors: list[str] = []
    for _ in range(RTT_ROUNDS):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
                samples.append((time.perf_counter() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    if not samples:
        return ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"云通道不可达 {host}:{port}（{errors[-1] if errors else '未知错误'}）",
            remediation=(
                "① 确认出网策略 / 代理允许访问该主机；② 确认地址与端口正确；"
                "③ 若甲方不允许出网，本项应改为「不适用」并在数据出网边界协议里写明（ADR-4）。"
            ),
        )

    avg = sum(samples) / len(samples)
    return ProbePayload(
        status=ProbeStatus.OK,
        detail=(
            f"云通道 {host}:{port} · TCP 建连 RTT 均值 {avg:.1f}ms"
            f"（{'/'.join(f'{s:.1f}' for s in samples)} ms，{RTT_ROUNDS} 次采样）"
        ),
        metrics={
            "host": host,
            "port": port,
            "rtt_ms_avg": round(avg, 2),
            "rtt_ms_samples": [round(s, 2) for s in samples],
            "measurement": "tcp_connect_only",
        },
    )


# ============================================================================
# 注册表
# ============================================================================
PROBE_SPECS: tuple[ProbeSpec, ...] = (
    ProbeSpec("mysql", "MySQL（DS-1）", "env", "需求 §12.1 N0 ③ · §6.1③", True, probe_mysql),
    ProbeSpec("redis", "Redis（DS-3）", "env", "需求 §12.1 N0 ③ · §6.1②", True, probe_redis),
    ProbeSpec("milvus", "Milvus（DS-2）", "env", "需求 §12.1 N0 ③⑥ · §6.1①", True, probe_milvus),
    ProbeSpec("minio", "MinIO（DS-4）", "env", "架构 §7 部署拓扑 · ADR-10", False, probe_minio),
    ProbeSpec("dify", "Dify（集成层）", "env", "需求 §12.1 N0 ③", False, probe_dify),
    ProbeSpec("ollama", "Ollama 服务与模型在册", "model", "需求 §12.1 N0 ④", True, probe_ollama),
    ProbeSpec(
        "embedding",
        "bge-m3 维度与吞吐（H-17 / H-19）",
        "model",
        "需求 §12.1 N0 ④ · §6.1④",
        True,
        probe_embedding,
    ),
    ProbeSpec(
        "reranker",
        "bge-reranker-v2-m3 精排延迟",
        "model",
        "需求 §12.1 N0 ④ · §6.1⑤",
        True,
        probe_reranker,
    ),
    ProbeSpec("ocr", "OCR / 文档解析依赖完整度", "ops", "需求 §12.1 N0 ④⑧", True, probe_ocr),
    ProbeSpec(
        "cloud_channel",
        "云 GPU 通道 RTT",
        "perf",
        "需求 §6.1⑥ · D-4 / D-19",
        False,
        probe_cloud_channel,
    ),
)

KNOWN_KEYS: tuple[str, ...] = tuple(spec.key for spec in PROBE_SPECS)


def specs_by_key() -> dict[str, ProbeSpec]:
    """key → 声明（CLI 的 ``--only`` 校验用）。"""
    return {spec.key: spec for spec in PROBE_SPECS}


def run_all(only: list[str] | tuple[str, ...] | None = None, on_done: Any = None) -> list[Probe]:
    """按注册表顺序执行探针。

    ``on_done`` 回调（可选）用于**流式打印**：Embedding 在 CPU 上要跑好几轮，
    全哑等一两分钟会让人以为脚本挂了。
    """
    wanted = set(only) if only else None
    results: list[Probe] = []
    for spec in PROBE_SPECS:
        if wanted is not None and spec.key not in wanted:
            continue
        probe = run_spec(spec)
        results.append(probe)
        if on_done is not None:
            on_done(probe)
    return results
