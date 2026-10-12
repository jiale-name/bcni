"""CU-10 · 模型网关 / 成本计算 / 熔断（L1 契约层，**无外部依赖**）。

分层（沿用 CU-07 / CU-08 的口径）
--------------------------------
- **L1（本文件）**：``CostCalculator`` / ``ModelGateway`` / ``CircuitBreaker`` 全是
  **纯逻辑 + 注入式 HTTP 客户端**，不需要 MySQL / Redis。故用假客户端（``_FakeClient``）
  把「正常返回 / 超时 / 4xx / 5xx / 空正文」这几种响应形状全部走一遍 ——
  它们正是线上最常见的失败形态，而**没有一条需要真的连上 Ollama**。
- **L2**：``tests/test_agent_flow.py``（逐条 skip 而非 fail）。

🔴 这一层为什么必须存在
--------------------
网关是「**永不抛异常**」的那个组件（红线：降级不抛 500）。它的失败路径比成功路径多得多，
且每一条失败路径的**错误码 / 是否可重试 / 是否 degraded** 都有契约含义：

| 响应形状 | 期望 | 为什么 |
| --- | --- | --- |
| 主路超时、备路正常 | ``ok=True`` + ``fallback_used=True`` | 主备链的意义就在这里 |
| 全部失败 | ``ok=False`` + ``degraded=True`` + **不抛** | 调用方据此返回 200 + 50301 |
| 4xx | **不重试** | 重试只是拖慢失败反馈（与图运行时的 ``NodeError`` 同一条推理）|
| 5xx | 重试 | 服务端瞬时故障，重试有效 |
| 空正文 / 无 embeddings | ``ok=False`` | 「HTTP 通了」不等于「拿到了结果」 |

覆盖的验收编号
--------------
| 编号 | 断言 | 位置 |
| --- | --- | --- |
| **AC-33** | 主备链切换：主路失败自动落备路且标记 ``fallback_used`` | ``test_primary_failure_falls_back_to_backup`` |
| **AC-34** | 全路失败 → ``degraded=True`` 且**不抛异常** | ``test_all_paths_failed_returns_degraded`` |
| **AC-35** | 无可用模型 → 直接降级（不发任何请求） | ``test_no_targets_degrades_without_request`` |
| **CR-7** | 降级走 ``AI_DEGRADED``/``MODEL_TIMEOUT`` 而非 50000 | ``test_degraded_error_codes_are_third_party_codes`` |
| F-9.1 | 4xx 不重试、5xx 重试 | ``test_4xx_is_not_retried`` / ``test_5xx_is_retried`` |
| 铁律 10 | 成本用 ``Decimal`` 定点计算、6 位量化、负数报错 | ``test_cost_*`` |
| 合规 | ``is_cloud`` **只认** ``deployment``，不看 ``provider`` | ``test_is_cloud_depends_only_on_deployment`` |
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.core.cost import COST_QUANT, CostCalculator
from app.core.model_gateway import (
    BREAKER_KEY_PREFIX,
    CircuitBreaker,
    GatewayCallResult,
    ModelGateway,
    ModelTarget,
    kind_of_scene,
)
from app.exceptions import ErrorCode


# ============================================================================
# 假 HTTP 客户端（把响应形状控制权拿回来，且**不产生真实网络调用**）
# ============================================================================
class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    """按 ``url`` 关键字分派响应；记录每次调用以便断言「发了几次」。"""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> _FakeResponse:
        self.calls.append((url, json))
        return self._handler(url, json, len(self.calls))

    def get(self, url: str, *, timeout: float) -> _FakeResponse:
        self.calls.append((url, {}))
        return self._handler(url, {}, len(self.calls))

    def close(self) -> None:  # pragma: no cover — 假客户端无需释放
        pass


def _target(code: str, **overrides: Any) -> ModelTarget:
    base: dict[str, Any] = {
        "id": abs(hash(code)) % 10_000 + 1,
        "scene": "resume_parse",
        "model_code": code,
        "display_name": code,
        "provider": "ollama",
        "deployment": "local",
        "base_url": f"http://fake-{code}:11434",
        "model_name": "qwen2.5:3b",
        "priority": 1,
        "max_retries": 0,
    }
    base.update(overrides)
    return ModelTarget(**base)


def _gateway(handler) -> tuple[ModelGateway, _FakeClient]:
    client = _FakeClient(handler)
    return ModelGateway(tenant_id=1, client=client), client  # type: ignore[arg-type]


def _ok_generate(model_name: str = "m") -> _FakeResponse:
    return _FakeResponse(200, {"response": f"答案来自 {model_name}", "prompt_eval_count": 10, "eval_count": 5})


# ============================================================================
# L1 · 场景 → 调用类型
# ============================================================================
@pytest.mark.parametrize(
    ("scene", "expected"),
    [
        ("resume_parse", "generate"),
        ("query_rewrite", "generate"),
        ("talent_ask", "generate"),
        ("tag_suggest", "generate"),
        ("judge", "generate"),
        ("embedding", "embed"),
        ("rerank", "rerank"),
        ("ocr", "generate"),
        ("unknown_scene", "generate"),  # 未知场景按最通用的生成处理，不抛错
    ],
)
def test_kind_of_scene(scene: str, expected: str) -> None:
    assert kind_of_scene(scene) == expected


# ============================================================================
# L1 · 出网判定（合规的**唯一**依据）
# ============================================================================
def test_is_cloud_depends_only_on_deployment() -> None:
    """🔴 ``is_cloud`` **只认 ``deployment``**，不看 ``provider``、不看场景。

    这是合规核查的地基：若改成「按 provider 猜」（如 ``provider != 'ollama'``），
    临时把某场景指到 OpenAI 协议的**本地**推理服务时会被误判成出网，
    从而产生假的脱敏违规告警。
    """
    local_openai = _target("a", provider="openai", deployment="local")
    cloud_ollama = _target("b", provider="ollama", deployment="cloud")

    assert local_openai.is_cloud is False
    assert cloud_ollama.is_cloud is True


def test_effective_timeout_is_clamped() -> None:
    """超时值必须被夹在安全区间内（0 会立刻超时，10^9 会让请求挂死）。"""
    assert _target("a", timeout_seconds=0.0001).effective_timeout > 0
    assert _target("b", timeout_seconds=10**9).effective_timeout < 10**9


def test_target_is_frozen() -> None:
    """``ModelTarget`` 不可变：路由表在**一次调用内**不会因为并发改配置而漂移。"""
    target = _target("frozen")
    with pytest.raises(Exception):  # noqa: B017 — dataclasses.FrozenInstanceError
        target.priority = 9  # type: ignore[misc]


# ============================================================================
# L1 · 主备链与降级（AC-33 / AC-34 / AC-35 / CR-7）
# ============================================================================
def test_no_targets_degrades_without_request() -> None:
    """场景下没有任何启用模型 ⇒ **直接降级且不发请求**（不能空转后崩）。"""
    gateway, client = _gateway(lambda url, body, n: _ok_generate())
    result = gateway.call([], prompt="hi")

    assert result.ok is False
    assert result.degraded is True
    assert result.attempts == []
    assert client.calls == [], "没有任何目标时不得发出 HTTP 请求"
    assert int(result.error_code or 0) == int(ErrorCode.AI_DEGRADED)


def test_primary_success_no_fallback() -> None:
    """**反向对照**：主路正常时不得标记 ``fallback_used``（否则「有无降级」失去意义）。"""
    gateway, client = _gateway(lambda url, body, n: _ok_generate("primary"))
    result = gateway.call([_target("primary"), _target("backup", priority=2)], prompt="hi")

    assert result.ok is True
    assert result.content == "答案来自 primary"
    assert result.fallback_used is False
    assert result.cloud_used is False
    assert len(client.calls) == 1, "主路成功就不该再打备路"
    assert result.tokens_in == 10
    assert result.tokens_out == 5


def test_primary_failure_falls_back_to_backup() -> None:
    """🔴 AC-33：主路失败 → 自动落备路，且 ``fallback_used=True``、``target`` 是备路。"""
    def handler(url: str, body: dict[str, Any], n: int) -> _FakeResponse:
        if "fake-primary" in url:
            return _FakeResponse(500, text="boom")
        return _ok_generate("backup")

    gateway, client = _gateway(handler)
    result = gateway.call([_target("primary"), _target("backup", priority=2)], prompt="hi")

    assert result.ok is True
    assert result.fallback_used is True
    assert result.target is not None and result.target.model_code == "backup"
    assert [a.ok for a in result.attempts] == [False, True]
    assert len(client.calls) == 2


def test_all_paths_failed_returns_degraded() -> None:
    """🔴 AC-34：全部失败 ⇒ ``ok=False`` + ``degraded=True``，**且不抛异常**。"""
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(503, text="down"))
    result = gateway.call([_target("a"), _target("b", priority=2)], prompt="hi")

    assert result.ok is False
    assert result.degraded is True
    assert result.target is None
    assert all(a.ok is False for a in result.attempts)
    # 降级必须给理由 —— 没有理由的 degraded 在排障时等于没说
    assert result.degraded_reason


def test_timeout_is_reported_as_504xx_not_50000() -> None:
    """超时 → ``MODEL_TIMEOUT``（50401 口径），**绝不包装成 50000**（CR-7）。"""

    def handler(url: str, body: dict[str, Any], n: int) -> _FakeResponse:
        raise httpx.TimeoutException("read timeout")

    gateway, _client = _gateway(handler)
    result = gateway.call([_target("t")], prompt="hi")

    assert result.ok is False
    assert int(result.error_code or 0) == int(ErrorCode.MODEL_TIMEOUT)
    assert int(result.error_code or 0) != 50000


def test_degraded_error_codes_are_third_party_codes() -> None:
    """降级用的错误码必须落在**第三方类**（50301/504xx），否则前端无法区分
    「业务失败」与「服务降级」，也无法统计降级率。"""
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(500, text="x"))
    result = gateway.call([_target("a")], prompt="hi")

    code = int(result.error_code or 0)
    assert code in {int(ErrorCode.AI_DEGRADED), int(ErrorCode.MODEL_TIMEOUT)}
    assert code not in {0, 50000, 40001}


def test_unexpected_exception_is_structured() -> None:
    """上游返回非法 JSON / 连接被重置等**任意异常**都要被结构化（网关是最外层边界）。"""

    def handler(url: str, body: dict[str, Any], n: int) -> _FakeResponse:
        raise RuntimeError("连接被重置")

    gateway, _client = _gateway(handler)
    result = gateway.call([_target("x")], prompt="hi")

    assert isinstance(result, GatewayCallResult)
    assert result.ok is False and result.degraded is True


# ============================================================================
# L1 · 重试策略（4xx 不重试 / 5xx 重试）
# ============================================================================
def test_4xx_is_not_retried() -> None:
    """4xx 是**确定性**失败（请求本身不合法 / 模型名不存在），重试只会拖慢失败反馈。"""
    gateway, client = _gateway(lambda url, body, n: _FakeResponse(400, text="bad request"))
    target = _target("bad", max_retries=3)
    result = gateway.call([target], prompt="hi")

    assert result.ok is False
    assert len(client.calls) == 1, f"4xx 被重试了 {len(client.calls)} 次（应为 1 次）"


def test_5xx_is_retried() -> None:
    """5xx 是瞬时故障，必须重试 —— **反向对照**（防「一律不重试」也算过上面那条）。"""
    gateway, client = _gateway(lambda url, body, n: _FakeResponse(502, text="gateway"))
    target = _target("flaky", max_retries=2)
    result = gateway.call([target], prompt="hi")

    assert result.ok is False
    assert len(client.calls) == 3, "应尝试 1 + max_retries(2) = 3 次"


def test_5xx_then_success_within_retries() -> None:
    """重试真的能救回来（第三次成功）。"""
    def handler(url: str, body: dict[str, Any], n: int) -> _FakeResponse:
        return _FakeResponse(503, text="warming up") if n < 2 else _ok_generate()

    gateway, client = _gateway(handler)
    result = gateway.call([_target("warming", max_retries=2)], prompt="hi")

    assert result.ok is True
    assert len(client.calls) == 2
    assert result.attempts[-1].ok is True


# ============================================================================
# L1 · 响应解析：HTTP 通了 ≠ 拿到结果
# ============================================================================
def test_empty_content_is_failure_not_success() -> None:
    """``{"done": true}`` 这种「没有正文」的响应必须判失败。

    只看 ``status_code < 400`` 就返回 ``ok=True`` 会让调用方拿到 ``content=None``
    并把它当成正常结果往下走 —— 最终表现为「AI 回答是空白」，
    而调用日志里全是成功（排障时方向完全错）。
    """
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(200, {"done": True}))
    result = gateway.call([_target("empty")], prompt="hi")

    assert result.ok is False
    assert result.content is None
    assert result.degraded is True


def test_openai_style_response_is_parsed() -> None:
    """OpenAI 兼容形状（``choices[0].message.content`` + ``usage``）也要能解析。"""
    payload = {
        "choices": [{"message": {"content": "来自云端"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    gateway, _client = _gateway(
        lambda url, body, n: _FakeResponse(200, payload)
    )
    result = gateway.call([_target("cloud", provider="openai", deployment="cloud")], prompt="hi")

    assert result.ok is True
    assert result.content == "来自云端"
    assert (result.tokens_in, result.tokens_out) == (100, 20)
    assert result.cloud_used is True


def test_embed_requires_embeddings_field() -> None:
    """向量化响应缺 ``embeddings`` ⇒ 失败（不能返回空向量列表冒充成功）。"""
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(200, {"model": "bge-m3"}))
    result = gateway.call([_target("e", scene="embedding")], texts=["a", "b"])

    assert result.ok is False
    assert result.vectors is None


def test_embed_parses_vectors() -> None:
    payload = {"embeddings": [[0.1, 0.2], [0.3, 0.4]], "prompt_eval_count": 7}
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(200, payload))
    result = gateway.call([_target("e", scene="embedding")], texts=["a", "b"])

    assert result.ok is True
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.tokens_in == 7


def test_rerank_accepts_both_score_field_names() -> None:
    """精排分数两种字段名（``relevance_score`` / ``score``）都要认 ——
    不同推理框架（TEI / Infinity / Xinference）返回的形状不同。"""
    for payload in (
        {"results": [{"relevance_score": 0.9}, {"relevance_score": 0.1}]},
        {"results": [{"score": 0.9}, {"score": 0.1}]},
    ):
        gateway, _client = _gateway(lambda url, body, n, p=payload: _FakeResponse(200, p))
        result = gateway.call(
            [_target("r", scene="rerank")], query="q", documents=["d1", "d2"]
        )
        assert result.ok is True, payload
        assert result.scores == [0.9, 0.1]


def test_missing_tokens_are_recorded_as_zero_not_estimated() -> None:
    """不返回 usage 的实现 ⇒ token 记 **0**，不估算。

    估算值会被当成真实用量进成本看板，累积起来是**看不出是估算的**系统性偏差。
    """
    gateway, _client = _gateway(lambda url, body, n: _FakeResponse(200, {"response": "ok"}))
    result = gateway.call([_target("no-usage")], prompt="hi")

    assert result.ok is True
    assert (result.tokens_in, result.tokens_out) == (0, 0)


# ============================================================================
# L1 · 熔断器（fail-open）
# ============================================================================
def test_breaker_key_namespace_is_cache_prefix() -> None:
    """熔断状态落在 ``cache:`` 命名空间 —— 它是**可丢数据**（Redis 只存可丢数据）。"""
    assert BREAKER_KEY_PREFIX.startswith("cache:")


def test_breaker_is_fail_open_when_backend_missing() -> None:
    """🔴 Redis 不可用时熔断器必须 **fail-open**（视为未熔断）。

    与限流同一条推理：**基础设施抖动不得升级为业务不可用**。
    若这里 fail-closed，Redis 一挂整个 AI 能力就全停了。
    """
    target = _target("x")
    # 灌一批失败进去（无论 Redis 是否可用都不该抛）
    for _ in range(10):
        CircuitBreaker.record_failure(1, target)

    # 无论 Redis 在不在，这两个调用都不许抛异常
    is_open = CircuitBreaker.is_open(1, target)
    remaining = CircuitBreaker.cooldown_remaining(1, target)
    assert isinstance(is_open, bool)
    assert remaining >= 0

    if not CircuitBreaker.backend_ok():
        assert is_open is False, "Redis 不可用时必须视为未熔断（fail-open）"


def test_breaker_never_raises_on_any_input() -> None:
    """熔断器是纯旁路：任何入口都不许抛（它的调用点全在主链路上）。"""
    target = _target("y")
    CircuitBreaker.record_success(1, target)
    CircuitBreaker.record_failure(-1, target)
    CircuitBreaker.record_failure(0, target)
    assert CircuitBreaker.failure_count(1, target) >= 0


def test_closed_breaker_skips_target() -> None:
    """熔断打开时，该路**被跳过且不发请求**（``skipped=True``）。

    ⚠️ 这里不依赖 Redis：先探一次，若后端不可用（fail-open）就跳过本条 ——
    「fail-open」与「真的跳过」是两条不同的契约，各有各的用例。
    """
    if not CircuitBreaker.backend_ok():
        pytest.skip("需要可用的 Redis 才能验证「熔断后跳过」这条真实行为")

    target = _target("blocked", breaker_threshold=2, breaker_cooldown_seconds=60)
    CircuitBreaker.record_success(1, target)  # 清掉残留计数
    for _ in range(2):
        CircuitBreaker.record_failure(1, target)
    assert CircuitBreaker.is_open(1, target) is True

    gateway, client = _gateway(lambda url, body, n: _ok_generate())
    result = gateway.call([target], prompt="hi")

    assert result.ok is False
    assert result.attempts and result.attempts[0].skipped is True
    assert client.calls == [], "熔断中的模型不得被真的请求"


# ============================================================================
# L1 · 成本计算（铁律 10：确定性组件）
# ============================================================================
def test_cost_uses_decimal_and_quantises_to_six_places() -> None:
    """成本必须用 ``Decimal`` 定点计算并量化到 6 位（与 ``DECIMAL(12,6)`` 对齐）。"""
    cost = CostCalculator.compute(
        tokens_in=1832, tokens_out=412,
        price_in_per_1k=Decimal("0.001"), price_out_per_1k=Decimal("0.002"),
    )
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.002656")
    # 量化到 6 位（位数不对会让落库时被 MySQL 静默截断）
    assert -cost.as_tuple().exponent == 6
    assert cost.quantize(COST_QUANT) == cost


def test_cost_rounds_half_up() -> None:
    """四舍五入用 ``ROUND_HALF_UP``（财务习惯）。

    Python 默认是 ``ROUND_HALF_EVEN``：``0.0000005`` 会变成 ``0.000000``，
    对账时解释不清（「为什么两个半分的费用一个进位一个不进位」）。
    """
    # 1 token × 0.0005 元/千 token = 0.0000005 元 → 应进位到 0.000001
    cost = CostCalculator.compute(
        tokens_in=1, tokens_out=0,
        price_in_per_1k=Decimal("0.0005"), price_out_per_1k=Decimal("0"),
    )
    assert cost == Decimal("0.000001")


def test_cost_float_prices_are_converted_via_str() -> None:
    """``float`` 单价必须**先转 str 再转 Decimal**（``Decimal(0.1)`` 是二进制浮点真值）。"""
    via_float = CostCalculator.compute(
        tokens_in=1000, tokens_out=0, price_in_per_1k=0.1, price_out_per_1k=0
    )
    assert via_float == Decimal("0.100000")


def test_cost_of_local_model_is_zero() -> None:
    """本地模型单价为 0 ⇒ 成本恒为 0（这是**默认值**，不是忘了配）。"""
    cost = CostCalculator.compute(
        tokens_in=9999, tokens_out=9999, price_in_per_1k=None, price_out_per_1k=Decimal("0")
    )
    assert cost == Decimal("0.000000")


def test_negative_tokens_raise() -> None:
    """负 token 一定意味着上游统计错了 —— **报错而不是取绝对值**。

    静默取绝对值会让成本看板长期偏低，且**没有任何迹象**表明统计有问题。
    """
    with pytest.raises(ValueError):
        CostCalculator.compute(
            tokens_in=-1, tokens_out=0, price_in_per_1k=Decimal("0.001"), price_out_per_1k=Decimal("0")
        )


def test_estimate_is_an_upper_bound() -> None:
    """预估成本是**上界**（顶格估）：输出按 ``max_output_tokens`` 全额计。"""
    estimate = CostCalculator.estimate(
        prompt_chars=1500, max_output_tokens=1000,
        price_in_per_1k=Decimal("0.001"), price_out_per_1k=Decimal("0.002"),
    )
    actual = CostCalculator.compute(
        tokens_in=1000, tokens_out=200,
        price_in_per_1k=Decimal("0.001"), price_out_per_1k=Decimal("0.002"),
    )
    assert estimate > actual
    assert estimate >= Decimal("0.002"), "输出顶格 1000 token × 0.002 应为 0.002 元"


def test_cost_is_additive_without_drift() -> None:
    """🔴 逐条累加与一次性计算必须**完全相等**（否则「列表合计」与「看板总额」会对不上）。

    这条正是当初不写 ``float`` 的理由：``float`` 版本在几百次累加后会出现分位误差。
    """
    unit = Decimal("0.0001")
    p_in = Decimal("0.003")
    total = sum(
        (CostCalculator.compute(tokens_in=3, tokens_out=0, price_in_per_1k=p_in, price_out_per_1k=0)
         for _ in range(500)),
        Decimal("0"),
    )
    single = (Decimal(3) * p_in / Decimal(1000)).quantize(COST_QUANT) * 500
    assert total == single
    assert unit > 0  # 保留常量引用，避免 lint 误删（本用例的价值在比较方式而非数值）
