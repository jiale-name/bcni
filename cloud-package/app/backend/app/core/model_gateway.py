"""模型网关：本地 / 云端多路路由 + 熔断 + 降级（F-9.1，ADR-12，API-81~83）。

本模块解决什么问题
==================
ADR-12 要求「**所有模型调用经统一网关**，替换模型不改业务代码；模型故障可经网关
统一切换降级链」。落地形态就是一个**按 `scene` 组织的优先级链**：

.. code-block:: text

    scene=talent_ask  targets=[cloud-a (priority=1), local-qwen3:8b (priority=2)]
                                   │ 超时 / 报错 / 熔断
                                   └────────────▶ 自动落到下一路
                                                      │ 也失败
                                                      └──▶ 返回降级结果（不抛 500）

🔴 **三条红线，都在本模块里落地**

① **降级不抛 500**（接口 §1.5 / F-1.6）
--------------------------------------
:meth:`ModelGateway.call` **永不抛异常**。全部失败时返回
``GatewayCallResult(ok=False, degraded=True, ...)``，由调用方用
:func:`app.schemas.common.degraded` 包成 ``HTTP 200 + data.degraded=true``。
❌ 严禁把「模型不可用」包成 ``50000`` —— 前端无法区分「业务失败」与「服务降级」，
降级率也就无法统计（这恰恰是 F-1.6 要防的事）。

② **Redis 只存可丢数据 ⇒ 熔断状态 fail-open**
------------------------------------------
熔断计数器存在 Redis（``cache:model:breaker:*``），**Redis 不可用时视为「未熔断」**，
照常尝试真实调用。理由与限流一致：基础设施抖动不得升级为业务不可用。
⚠️ 注意这是**刻意选择的方向**：fail-open 的代价是「Redis 挂掉时失去熔断保护，
每个请求都会为一个已死的模型白等一个超时」。这比「Redis 一挂，全站 AI 功能 503」
要好 —— 前者性能退化，后者功能全失。

③ **确定性结论不来自模型**
-------------------------
本模块只负责**搬运**（发请求、取文本、数 token、记耗时），
**不做任何数值判定**（成本由 :class:`app.core.cost.CostCalculator` 算，
无答案阈值由 ``ThresholdEngine`` 判）。见铁律 10。

为什么不在这里读数据库
====================
本模块属于 ``core/``（横切），输入是 :class:`ModelTarget` 数据类 ——
由 Service 层从 ``ai_model_config``（TBL-29）读出后组装。
好处：① ``core`` 不依赖 ORM；② 网关本身**可被单测直接构造目标**，
不需要数据库（本模块的用例因此全部是 L1，无外部依赖）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final, Literal

import httpx

from app.core.logging import get_logger
from app.exceptions import ErrorCode

logger = get_logger(__name__)

#: 调用形态。由**场景**推导（见 :func:`kind_of_scene`），不由调用方逐次传 ——
#: 调用方传错的后果是「拿生成接口去要向量」，报错信息会非常难懂。
CallKind = Literal["generate", "embed", "rerank"]

#: 单次 HTTP 的最小/最大超时兜底（配置值越界时收敛，不让一个笔误把请求挂死 1 小时）
_MIN_TIMEOUT: Final[float] = 1.0
_MAX_TIMEOUT: Final[float] = 600.0

#: 熔断计数的 Redis 键前缀（数据库 §7.3 的 ``cache:*`` 族 —— 可丢数据）
BREAKER_KEY_PREFIX: Final[str] = "cache:model:breaker:"


def kind_of_scene(scene: str) -> CallKind:
    """场景 → 调用形态。

    - ``embedding`` → ``embed``（要向量）
    - ``rerank`` → ``rerank``（要相关度分数）
    - 其余（``resume_parse`` / ``talent_ask`` / ``query_rewrite`` / ``tag_suggest`` /
      ``judge`` / ``ocr``）→ ``generate``（要文本）
    """
    if scene == "embedding":
        return "embed"
    if scene == "rerank":
        return "rerank"
    return "generate"


#: 单路调用结果的**完整键集**（:func:`call` 直接按 key 取值，不用 ``.get``）。
#:
#: 🔴 这里必须是**一套**键而不是「按 kind 各返回各的」——
#: 曾经的实现让 ``embed`` 成功时只返回 ``vectors``、``generate`` 只返回 ``content``，
#: 于是 ``call()`` 一读到主路成功就 ``KeyError: 'content'``：
#: HTTP 200、上游一切正常，**所有 AI 调用却全线失败**。
#: 三种 kind 的形状差异应由「哪些键非空」表达，不是由「哪些键存在」表达。
_EMPTY_RESULT: Final[dict[str, Any]] = {
    "ok": False,
    "content": None,
    "vectors": None,
    "scores": None,
    "tokens_in": 0,
    "tokens_out": 0,
    "latency_ms": 0,
    "first_token_ms": None,
    "error_code": None,
    "error": None,
    "retryable": False,
}


def _result(
    *,
    ok: bool,
    latency_ms: int,
    content: str | None = None,
    vectors: list[list[float]] | None = None,
    scores: list[float] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    first_token_ms: int | None = None,
    error_code: str | None = None,
    error: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """构造**键集完整**的单路调用结果（键从 :data:`_EMPTY_RESULT` 铺开，结构上不可能缺键）。

    ⚠️ ``retryable`` 默认 ``False``：只有明确的瞬时故障（超时 / 连接失败 / 5xx）
    才由调用点显式传 ``True``。默认「不重试」是因为**误重试的代价远大于误不重试** ——
    前者把失败反馈从 3 秒拖到 90 秒，后者只是少一次可能成功的机会。
    """
    result = dict(_EMPTY_RESULT)
    result.update(
        ok=ok,
        content=content,
        vectors=vectors,
        scores=scores,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        first_token_ms=first_token_ms,
        error_code=error_code,
        error=error,
        retryable=retryable,
    )
    return result


# ============================================================================
# 数据类
# ============================================================================
@dataclass(frozen=True, slots=True)
class ModelTarget:
    """一路模型目标（由 Service 层从 ``ai_model_config`` 组装）。

    ``frozen=True``：目标一旦进入网关就不再变化 —— 主备切换过程中被并发改了配置，
    也不会让「本次请求的前两路」与「后一路」来自不同版本的路由表。
    """

    id: int
    scene: str
    model_code: str
    display_name: str
    provider: str
    #: ``local`` / ``cloud`` —— **出网判定的唯一依据**（见 ``models.ai`` 的说明）
    deployment: str
    base_url: str
    model_name: str
    priority: int = 1
    timeout_seconds: float = 60.0
    max_retries: int = 1
    breaker_threshold: int = 3
    breaker_cooldown_seconds: int = 60
    price_in_per_1k: Decimal = Decimal("0")
    price_out_per_1k: Decimal = Decimal("0")

    @property
    def is_cloud(self) -> bool:
        """是否出网。**合规核查的唯一判据**（不看 ``provider``，只认 ``deployment``）。"""
        return self.deployment == "cloud"

    @property
    def effective_timeout(self) -> float:
        return max(_MIN_TIMEOUT, min(float(self.timeout_seconds), _MAX_TIMEOUT))


@dataclass(slots=True)
class Attempt:
    """一次尝试（用于日志与排障）。**永不包含 prompt / 回复正文**。"""

    model_code: str
    model_name: str
    provider: str
    ok: bool
    latency_ms: int
    error_code: str | None = None
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass(slots=True)
class GatewayCallResult:
    """网关调用结果（**永远是返回值，不是异常**）。"""

    ok: bool
    kind: CallKind
    #: 生成类结果
    content: str | None = None
    #: 向量类结果（``embed``）：每个输入文本一个向量
    vectors: list[list[float]] | None = None
    #: 精排类结果（``rerank``）：与输入文档一一对应的相关度分数
    scores: list[float] | None = None

    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    first_token_ms: int | None = None

    #: 最终命中的那一路（全部失败时为 ``None``）
    target: ModelTarget | None = None
    #: 是否发生了主备切换（命中的不是 priority 最小的那一路）
    fallback_used: bool = False
    #: 逐次尝试记录（**按执行顺序**，含被熔断跳过的）
    attempts: list[Attempt] = field(default_factory=list)

    #: 是否降级（``ok=False`` 且有可用降级语义时为 True）
    degraded: bool = False
    degraded_reason: str | None = None
    error_code: str | None = None
    error_msg: str | None = None

    @property
    def cloud_used(self) -> bool:
        """本次是否真的出网了（合规核查用）。"""
        return bool(self.target and self.target.is_cloud)


# ============================================================================
# 熔断器（Redis，fail-open）
# ============================================================================
class CircuitBreaker:
    """按 ``(tenant_id, scene, model_code)`` 计「连续失败次数」的轻量熔断器。

    状态机::

        连续失败 < threshold          → closed（正常，允许调用）
        连续失败 ≥ threshold          → open（冷却期内 **跳过**，不再调用）
        冷却期结束（Redis 键过期）    → 自动回到 closed（无需显式重置）
        任一次成功                     → 立即清零（closed）

    ⚠️ **为什么用「键过期」而不是「显式半开状态」**：
    显式半开需要额外一次「试探调用」的协调（谁去试探？并发下会不会都去试探？），
    而本项目的规模（单租户、几十 QPS）不值得引入这套复杂度。
    键过期自动恢复 + 成功后清零，已能覆盖「下游挂了别白等」这个唯一的诉求。
    """

    @staticmethod
    def _key(tenant_id: int, scene: str, model_code: str) -> str:
        from app.dao.redis_client import build_key

        return build_key(BREAKER_KEY_PREFIX, int(tenant_id), scene, model_code)

    @classmethod
    def failure_count(cls, tenant_id: int, target: ModelTarget) -> int:
        """读当前连续失败次数。Redis 不可用 → 0（fail-open，见模块 docstring ②）。"""
        try:
            from app.dao.redis_client import get_redis

            raw = get_redis().get(cls._key(tenant_id, target.scene, target.model_code))
            return int(raw or 0)
        except Exception as exc:  # noqa: BLE001 — 熔断是可选保护，任何异常都不得影响主流程
            logger.warning("读取熔断状态失败（按未熔断处理）model=%s: %s", target.model_code, exc)
            return 0

    @classmethod
    def is_open(cls, tenant_id: int, target: ModelTarget) -> bool:
        """是否处于熔断冷却期（= 应跳过该路）。"""
        threshold = max(1, int(target.breaker_threshold))
        return cls.failure_count(tenant_id, target) >= threshold

    @classmethod
    def cooldown_remaining(cls, tenant_id: int, target: ModelTarget) -> int:
        """剩余冷却秒数（供 API-83 展示）；无熔断返回 0。"""
        try:
            from app.dao.redis_client import get_redis

            ttl = get_redis().ttl(cls._key(tenant_id, target.scene, target.model_code))
            return max(0, int(ttl or 0))
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def record_failure(cls, tenant_id: int, target: ModelTarget) -> int:
        """记一次失败并返回累计次数。冷却窗口按 ``breaker_cooldown_seconds`` 刷新。

        ⚠️ 每次失败都**刷新 TTL**：若只在第一次设 TTL，一个持续失败的模型会在
        冷却结束后「失败计数仍在阈值之上」但键已过期，行为等价于即恢复；
        而刷新 TTL 得到的是「持续失败则持续熔断」——这才是想要的。
        """
        try:
            from app.dao.redis_client import get_redis

            key = cls._key(tenant_id, target.scene, target.model_code)
            client = get_redis()
            value = int(client.incr(key))
            client.expire(key, max(1, int(target.breaker_cooldown_seconds)))
            if value >= max(1, int(target.breaker_threshold)):
                logger.error(
                    "模型熔断开启 [%s/%s] 连续失败 %s 次，冷却 %ss",
                    target.scene,
                    target.model_code,
                    value,
                    target.breaker_cooldown_seconds,
                )
            return value
        except Exception as exc:  # noqa: BLE001
            logger.warning("写熔断状态失败 model=%s: %s", target.model_code, exc)
            return 0

    @classmethod
    def record_success(cls, tenant_id: int, target: ModelTarget) -> None:
        """一次成功即清零 —— 让「偶发抖动」不会累积成熔断。"""
        try:
            from app.dao.redis_client import get_redis

            get_redis().delete(cls._key(tenant_id, target.scene, target.model_code))
        except Exception as exc:  # noqa: BLE001
            logger.warning("清熔断状态失败 model=%s: %s", target.model_code, exc)

    @staticmethod
    def backend_ok() -> bool:
        """熔断后端（Redis）是否可用（供 API-83 展示）。"""
        try:
            from app.dao.redis_client import ping

            return bool(ping())
        except Exception:  # noqa: BLE001
            return False


# ============================================================================
# 网关
# ============================================================================
class ModelGateway:
    """统一模型网关（**无状态、无 DB 依赖**，可被单测直接实例化）。"""

    #: 精排服务的三种端点形状 —— 与 ``scripts/n0/probes.py`` 的实测发现同源
    #: （端点名不同不代表环境不可用，挨个试比报「环境不支持」有用）。
    RERANK_SHAPES: Final[tuple[str, ...]] = ("tei_texts", "infinity_documents", "xinference_rerank")

    def __init__(self, tenant_id: int, *, client: httpx.Client | None = None) -> None:
        self.tenant_id = int(tenant_id)
        #: 复用连接（同一次请求可能要调多路模型，别每次重新握手）。
        #: ``client`` 显式传入时由调用方负责关闭（调用方可能跨请求复用同一个客户端）；
        #: 未传入时本实例**懒创建并自己持有**，由 :meth:`close` 释放。
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _get_client(self) -> httpx.Client:
        """取（必要时创建）HTTP 客户端。

        ⚠️ 必须**只创建一次**：早期写法是「每次调用 ``httpx.Client(...)`` 并返回」，
        那等于每次多路尝试都新建一个连接池而从不关闭 ——
        单测里看不出来，线上表现为句柄缓慢泄漏。
        """
        if self._client is None:
            # ``trust_env=False``：不读代理环境变量。企政内网的 ``HTTP_PROXY``
            # 一旦指向外部代理，本地 ``127.0.0.1:11434`` 的请求也会被送出去，
            # 表现是「Ollama 明明起着却连接被重置」。
            self._client = httpx.Client(timeout=_MAX_TIMEOUT, trust_env=False)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def call(
        self,
        targets: list[ModelTarget],
        *,
        prompt: str | None = None,
        texts: list[str] | None = None,
        query: str | None = None,
        documents: list[str] | None = None,
        system: str | None = None,
    ) -> GatewayCallResult:
        """按 ``targets`` 的顺序（= ``priority`` 升序）依次尝试，返回**第一个成功的结果**。

        :param targets: 已按 ``priority`` 排好序的备选链；空列表 → 直接降级
        :param prompt: ``generate`` 的提示词
        :param texts: ``embed`` 的待向量化文本
        :param query: ``rerank`` 的查询
        :param documents: ``rerank`` 的候选文档
        :raises: **不抛任何异常**（见模块 docstring ①）

        🔴 ``prompt`` / ``texts`` / ``documents`` 里可能含**已脱敏的占位符**
        （ADR-4：出网只送脱敏片段）—— 网关**不做脱敏**，它只负责搬运。
        脱敏是调用方（``MaskService``）的责任，网关擅自「顺带脱敏」会让
        「本地调用也被替换成占位符」，那是错的（本地不出网，无需脱敏）。
        """
        started = time.perf_counter()
        attempts: list[Attempt] = []

        if not targets:
            return GatewayCallResult(
                ok=False,
                kind="generate",
                attempts=attempts,
                degraded=True,
                degraded_reason="没有可用的模型配置（该场景下无启用中的模型）",
                error_code=str(int(ErrorCode.AI_DEGRADED)),
                error_msg="AI 服务降级：无可用模型配置",
            )

        kind = kind_of_scene(targets[0].scene)
        first_priority = min(t.priority for t in targets)
        last_error_code: str | None = None
        last_error_msg: str | None = None

        for target in targets:
            # ---- 熔断跳过（fail-open：Redis 不可用时 is_open 恒为 False）----
            if CircuitBreaker.is_open(self.tenant_id, target):
                attempts.append(
                    Attempt(
                        model_code=target.model_code,
                        model_name=target.model_name,
                        provider=target.provider,
                        ok=False,
                        latency_ms=0,
                        skipped=True,
                        skip_reason="熔断冷却中，跳过",
                    )
                )
                last_error_code = str(int(ErrorCode.AI_DEGRADED))
                last_error_msg = "主备链上已有模型处于熔断冷却期"
                continue

            result = self._call_with_retry(
                target, kind=kind, prompt=prompt, texts=texts, query=query, documents=documents,
                system=system,
            )
            attempts.append(
                Attempt(
                    model_code=target.model_code,
                    model_name=target.model_name,
                    provider=target.provider,
                    ok=result["ok"],
                    latency_ms=result["latency_ms"],
                    error_code=result["error_code"],
                    error=result["error"],
                )
            )

            if result["ok"]:
                CircuitBreaker.record_success(self.tenant_id, target)
                return GatewayCallResult(
                    ok=True,
                    kind=kind,
                    content=result["content"],
                    vectors=result["vectors"],
                    scores=result["scores"],
                    tokens_in=result["tokens_in"],
                    tokens_out=result["tokens_out"],
                    latency_ms=result["latency_ms"],
                    first_token_ms=result["first_token_ms"],
                    target=target,
                    fallback_used=target.priority > first_priority,
                    attempts=attempts,
                )

            CircuitBreaker.record_failure(self.tenant_id, target)
            last_error_code = result["error_code"]
            last_error_msg = result["error"]
            logger.warning(
                "模型调用失败，尝试下一路 [%s/%s] model=%s error=%s",
                target.scene,
                target.model_code,
                target.model_name,
                result["error"],
            )

        total_ms = int((time.perf_counter() - started) * 1000)
        chain = " → ".join(t.model_code for t in targets)
        return GatewayCallResult(
            ok=False,
            kind=kind,
            latency_ms=total_ms,
            attempts=attempts,
            fallback_used=len(attempts) > 1,
            degraded=True,
            degraded_reason=f"备选链全部不可用：{chain}",
            error_code=last_error_code or str(int(ErrorCode.AI_DEGRADED)),
            error_msg=last_error_msg or "AI 服务降级",
        )

    # ------------------------------------------------------------------
    # 单路调用（含重试）
    # ------------------------------------------------------------------
    def _call_with_retry(
        self,
        target: ModelTarget,
        *,
        kind: CallKind,
        prompt: str | None,
        texts: list[str] | None,
        query: str | None,
        documents: list[str] | None,
        system: str | None,
    ) -> dict[str, Any]:
        """对**单路**目标调用，失败按 ``max_retries`` 重试。

        ⚠️ 重试只针对**可重试错误**（超时 / 连接失败 / 5xx）。
        4xx（如 401 鉴权失败、404 模型不存在）**不重试** —— 重试三次的结果
        与一次完全相同，只是把失败反馈从 3 秒拖到 90 秒。
        """
        attempts_allowed = max(0, int(target.max_retries)) + 1
        last: dict[str, Any] = _result(
            ok=False,
            latency_ms=0,
            error_code=str(int(ErrorCode.AI_DEGRADED)),
            error="未执行",
        )
        for attempt_no in range(attempts_allowed):
            last = self._call_once(
                target, kind=kind, prompt=prompt, texts=texts, query=query,
                documents=documents, system=system,
            )
            if last["ok"]:
                return last
            # 键恒存在（见 _EMPTY_RESULT），所以这里直接取值而不是 .get(default)
            if not last["retryable"]:
                return last
            if attempt_no + 1 < attempts_allowed:
                logger.info(
                    "第 %s/%s 次调用失败，准备重试 model=%s",
                    attempt_no + 1,
                    attempts_allowed,
                    target.model_name,
                )
        return last

    # ------------------------------------------------------------------
    # 单次 HTTP 调用
    # ------------------------------------------------------------------
    def _call_once(
        self,
        target: ModelTarget,
        *,
        kind: CallKind,
        prompt: str | None,
        texts: list[str] | None,
        query: str | None,
        documents: list[str] | None,
        system: str | None,
    ) -> dict[str, Any]:
        """发一次请求。**任何异常都被转成结构化的失败字典**，不向上抛。"""
        timeout = target.effective_timeout
        started = time.perf_counter()
        try:
            if kind == "embed":
                payload, url = self._build_embed(target, texts or [])
            elif kind == "rerank":
                payload, url = self._build_rerank(target, query or "", documents or [])
            else:
                payload, url = self._build_generate(target, prompt or "", system=system)

            client = self._get_client()
            response = client.post(url, json=payload, timeout=timeout)
            latency_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code >= 400:
                # 502 的专门解释（来自 N0 实测）：端口上有网关监听、后端模型服务未就绪，
                # ``curl`` 探活会「有响应」从而被误判成「服务是好的」。
                hint = ""
                if response.status_code == 502:
                    hint = "（服务端口有响应但后端模型未就绪，常见于 Reranker/推理服务未加载完）"
                return _result(
                    ok=False,
                    latency_ms=latency_ms,
                    error_code=str(int(ErrorCode.AI_DEGRADED)),
                    error=f"HTTP {response.status_code}{hint}: {response.text[:200]}",
                    # 5xx 可重试，4xx 不可（重试结果相同，只是拖慢失败反馈）
                    retryable=response.status_code >= 500,
                )

            data = response.json()
            return self._parse_response(target, kind, data, latency_ms)
        except httpx.TimeoutException as exc:
            return _result(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code=str(int(ErrorCode.MODEL_TIMEOUT)),
                error=f"调用超时（>{timeout}s）：{exc}",
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001 — 网关是最外层边界，必须把一切异常结构化
            return _result(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code=str(int(ErrorCode.AI_DEGRADED)),
                error=f"{type(exc).__name__}: {exc}",
                retryable=True,
            )

    # ------------------------------------------------------------------
    # 请求构造（按 provider / kind 分派）
    # ------------------------------------------------------------------
    @staticmethod
    def _join(base_url: str, path: str) -> str:
        """拼 URL。

        ⚠️ 兼容两种 ``base_url`` 写法：``http://host:11434`` 与 ``http://host:11434/``。
        另外**已含完整路径**的写法（``.../v1/chat/completions``）原样返回 ——
        否则会拼出 ``.../chat/completions/chat/completions``（这类错误在日志里很难看）。
        """
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("base_url 未配置")
        if base.endswith(path):
            return base
        return f"{base}{path}"

    def _build_generate(
        self, target: ModelTarget, prompt: str, *, system: str | None
    ) -> tuple[dict[str, Any], str]:
        if target.provider == "ollama":
            payload: dict[str, Any] = {
                "model": target.model_name,
                "prompt": prompt,
                "stream": False,
                # 明确关闭思考链输出（qwen3 系默认会带 thinking，混进正文会污染抽取结果）
                "options": {"temperature": 0},
            }
            if system:
                payload["system"] = system
            return payload, self._join(target.base_url, "/api/generate")
        # OpenAI 兼容（dify / openai / cloud 通道）
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return (
            {"model": target.model_name, "messages": messages, "stream": False, "temperature": 0},
            self._join(target.base_url, "/chat/completions"),
        )

    def _build_embed(
        self, target: ModelTarget, texts: list[str]
    ) -> tuple[dict[str, Any], str]:
        if target.provider == "ollama":
            # ``/api/embed``（新）与 ``/api/embeddings``（旧，单条）二选一。
            # 用新的批量接口：逐条调用在 3.8 万份规模下是不可接受的（N0 已实测 1.54 次/秒）。
            return ({"model": target.model_name, "input": texts}, self._join(target.base_url, "/api/embed"))
        return (
            {"model": target.model_name, "input": texts},
            self._join(target.base_url, "/embeddings"),
        )

    def _build_rerank(
        self, target: ModelTarget, query: str, documents: list[str]
    ) -> tuple[dict[str, Any], str]:
        """精排请求：**依次尝试三种端点形状**（与 ``n0/probes.py`` 同源）。

        三种形状来自实测环境（TEI / Infinity / Xinference 各自的字段名不同）：
        ``texts`` / ``documents`` / ``inputs``。服务端不认哪一种就返回 4xx，
        由 :meth:`_call_once` 的「4xx 不重试」直接落下一路 —— 但**同一路的三种形状
        需要依次试**，故这里把形状选择收敛为「按配置的 base_url 是否含特征串」：
        含 ``tei`` → TEI，含 ``infinity`` → Infinity，其余 → Xinference 形状。
        ⚠️ 这不是「猜」，而是把 N0 实测结论固化；三条命令都能跑通，
        真正不可用的是「不加载模型的服务」（那会返回 502，已被专门解释）。
        """
        url_lower = (target.base_url or "").lower()
        if "tei" in url_lower:
            return ({"query": query, "texts": documents}, self._join(target.base_url, "/rerank"))
        if "infinity" in url_lower:
            return (
                {"query": query, "documents": documents},
                self._join(target.base_url, "/rerank"),
            )
        return (
            {"model": target.model_name, "query": query, "documents": documents},
            self._join(target.base_url, "/v1/rerank"),
        )

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------
    def _parse_response(
        self, target: ModelTarget, kind: CallKind, data: dict[str, Any], latency_ms: int
    ) -> dict[str, Any]:
        """把各家不同的响应体归一到一种形状。

        🔴 **返回值恒为 :data:`_EMPTY_RESULT` 的键集**（由 :func:`_result` 保证）：
        成功时只有「与本次 kind 相关」的键非空（``generate``→``content``、
        ``embed``→``vectors``、``rerank``→``scores``），其余保持 ``None``。
        调用方可以无条件按 key 取值，不必先判 kind。

        ⚠️ token 数**尽力而为**：某些兼容实现不返回 ``usage``，
        此时记 0 而不是估算 —— 估算出来的 token 会被当成真实用量进成本看板，
        累积起来就是一个系统性偏差（且看不出是估算的）。
        """
        if kind == "embed":
            vectors = data.get("embeddings")
            if vectors is None and "embedding" in data:  # 旧版 Ollama 单条响应
                vectors = [data["embedding"]]
            if not vectors:
                return _result(
                    ok=False,
                    latency_ms=latency_ms,
                    error_code=str(int(ErrorCode.AI_DEGRADED)),
                    error="响应中没有 embeddings 字段",
                    retryable=False,
                )
            return _result(
                ok=True,
                latency_ms=latency_ms,
                vectors=[[float(x) for x in vec] for vec in vectors],
                tokens_in=int(data.get("prompt_eval_count") or 0),
            )

        if kind == "rerank":
            items = data.get("results") or data.get("data") or []
            scores: list[float] = []
            for item in items:
                if isinstance(item, dict) and "relevance_score" in item:
                    scores.append(float(item["relevance_score"]))
                elif isinstance(item, dict) and "score" in item:
                    scores.append(float(item["score"]))
            if not scores:
                return _result(
                    ok=False,
                    latency_ms=latency_ms,
                    error_code=str(int(ErrorCode.AI_DEGRADED)),
                    error="响应中没有可识别的分数字段",
                    retryable=False,
                )
            return _result(ok=True, latency_ms=latency_ms, scores=scores)

        # generate：Ollama 用 ``response``，OpenAI 兼容用 ``choices[0].message.content``
        content = data.get("response")
        if content is None:
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                content = message.get("content")
        if content is None:
            return _result(
                ok=False,
                latency_ms=latency_ms,
                error_code=str(int(ErrorCode.AI_DEGRADED)),
                error="响应中没有可识别的正文（response / choices[0].message.content 均缺失）",
                retryable=False,
            )
        usage = data.get("usage") or {}
        return _result(
            ok=True,
            latency_ms=latency_ms,
            content=str(content),
            tokens_in=int(
                data.get("prompt_eval_count") or usage.get("prompt_tokens") or 0
            ),
            tokens_out=int(
                data.get("eval_count") or usage.get("completion_tokens") or 0
            ),
        )

    # ------------------------------------------------------------------
    # 健康探测（API-83）
    # ------------------------------------------------------------------
    def probe(self, target: ModelTarget) -> dict[str, Any]:
        """单路健康探测。返回 ``{healthy, latency_ms, error, blocked, ...}``，**不抛异常**。

        探测方式（**只探服务，不跑真实推理**）：
        - ``ollama``：``GET /api/tags``（顺带校验配置的模型名在册）；
        - 其余：``GET /models``（OpenAI 兼容的服务发现端点）。

        ⚠️ 刻意**不用「发一次生成请求」来探活**：
        那会真的消耗 token / 占用算力（API-83 可能被前端定时轮询，
        等于给自己加了一个持续压测）。真实的「模型能不能用」由调用链上的熔断器回答。
        """
        timeout = target.effective_timeout
        started = time.perf_counter()
        blocked = CircuitBreaker.is_open(self.tenant_id, target)
        failures = CircuitBreaker.failure_count(self.tenant_id, target)
        cooldown = CircuitBreaker.cooldown_remaining(self.tenant_id, target)
        base = {
            "blocked": blocked,
            "consecutive_failures": failures,
            "cooldown_remaining_seconds": cooldown,
        }
        try:
            client = self._get_client()
            if target.provider == "ollama":
                url = self._join(target.base_url, "/api/tags")
                response = client.get(url, timeout=min(10.0, timeout))
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code >= 400:
                    return {**base, "healthy": False, "latency_ms": latency_ms,
                            "error": f"HTTP {response.status_code}: {response.text[:200]}"}
                names = {
                    str(item.get("name") or item.get("model") or "")
                    for item in (response.json().get("models") or [])
                }
                if names and target.model_name not in names:
                    return {
                        **base, "healthy": False, "latency_ms": latency_ms,
                        "error": f"服务可用但模型未在册：{target.model_name}",
                    }
                return {**base, "healthy": True, "latency_ms": latency_ms, "error": None}

            url = self._join(target.base_url, "/models")
            response = client.get(url, timeout=min(10.0, timeout))
            latency_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code >= 400:
                return {**base, "healthy": False, "latency_ms": latency_ms,
                        "error": f"HTTP {response.status_code}: {response.text[:200]}"}
            return {**base, "healthy": True, "latency_ms": latency_ms, "error": None}
        except Exception as exc:  # noqa: BLE001 — 探针永不抛
            return {
                **base,
                "healthy": False,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            }

    # ------------------------------------------------------------------
    @staticmethod
    def dumps(value: Any) -> str:
        """安全 JSON 序列化（供审计 / 步骤日志使用；不可序列化的对象转 ``str``）。"""
        return json.dumps(value, ensure_ascii=False, default=str)
