"""请求处理链中间件（接口 §1.4）。

链序（不可调换）::

    CORS + 限流 → JWT 鉴权 → 功能权限 → 租户/行级过滤 → routers → services → dao → 统一响应

分层实现说明（与 §1.4 的对应关系）
----------------------------------
| §1.4 层 | 本项目落点 | 原因 |
| --- | --- | --- |
| 1 CORS + 限流 | **ASGI 中间件**（本模块 + :mod:`app.core.ratelimit`） | 必须早于一切，且要能在鉴权前拿到 IP |
| 2 JWT 鉴权 | **依赖注入** ``Depends(get_current_principal)`` | 需要按路由粒度声明（公开接口不鉴权） |
| 3 功能权限 | **依赖注入** ``Depends(require_perm(...))`` | 权限码是路由属性，用中间件得另建映射表 |
| 4 租户/行级过滤 | **依赖注入 + ORM 事件**（:mod:`app.core.tenant`） | 与 SQLAlchemy Session 生命周期绑定 |

> 用依赖注入而非中间件承载 2/3/4 层，是 FastAPI 的实现惯例；**语义与 §1.4 完全一致**
> （顺序由 FastAPI 的依赖解析与中间件栈共同保证），不是偏离。
"""

from __future__ import annotations

import re

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import new_trace_id, set_request_meta, set_trace_id
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 响应头 / 请求头名（接口 §1.1）
TRACE_HEADER = "X-Request-Id"

#: 代理链头（取**最左**的非空项 = 真实客户端；见 :class:`RequestMetaMiddleware`）
_FORWARDED_FOR = b"x-forwarded-for"
_REAL_IP = b"x-real-ip"

#: ``ip`` 列长度为 VARCHAR(64)（数据库 §4.7），超长直接截断而不是让插入报错
MAX_IP_LEN = 64
#: ``ua`` 列长度为 VARCHAR(255)
MAX_UA_LEN = 255


class TraceIdMiddleware:
    """为每个请求建立 ``trace_id``，并回写同名响应头。

    三处一致（F-1.4 全链路串联）：响应体 ``trace_id`` == 日志 ``trace_id`` == 响应头 ``X-Request-Id``。

    用**纯 ASGI 中间件**而非 ``BaseHTTPMiddleware``：后者会把下游放到独立任务里执行，
    contextvars 的传播在部分 Starlette 版本上不可靠，会导致日志丢 trace_id。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 客户端可自带 X-Request-Id（跨端串联，接口 §1.1 标记为可选）
        incoming: str | None = None
        for key, value in scope.get("headers") or []:
            if key == b"x-request-id":
                incoming = value.decode("latin-1").strip() or None
                break
        trace_id = incoming or new_trace_id()
        set_trace_id(trace_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[TRACE_HEADER] = trace_id
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestMetaMiddleware:
    """采集客户端 IP / User-Agent 到请求上下文（供 TBL-28 审计留痕）。

    为什么不放进 :class:`TraceIdMiddleware`
    ----------------------------------------
    两者虽都在链首，但**关注点不同**：trace_id 是「链路串联」，ip/ua 是「取证信息」。
    合并后任何一方要加逻辑（比如将来给 trace_id 加采样）都要动同一个类，
    且测试无法单独构造「只有 trace_id、没有 ip/ua」的场景。故拆成两个类，顺序相邻即可。

    🔴 关于 `X-Forwarded-For` 的正确取法
    -----------------------------------
    ``XFF`` 形如 ``真实客户端, 代理1, 代理2``，**取最左**的非空项。
    但请注意：该头**可被客户端伪造**（本项目一期无强制可信代理白名单），
    因此它只用于**审计展示与排障**，绝不作为权限或风控的唯一依据。
    当没有 ``XFF`` 时依次退化：``X-Real-IP`` → ``scope["client"]``。

    ⚠️ 截断而非报错：``ip`` 列 VARCHAR(64)、``ua`` 列 VARCHAR(255)。
    畸形 XFF（如塞入一整行）若直接入库会因超长**抛异常**，
    而审计采集失败绝不该影响业务响应 —— 故在此层就按列宽截断。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        client_ip = _pick_ip(headers, scope.get("client"))
        ua = _pick_header(headers, b"user-agent")
        set_request_meta(
            client_ip=_truncate(client_ip, MAX_IP_LEN),
            user_agent=_truncate(ua, MAX_UA_LEN),
        )

        await self.app(scope, receive, send)


# ----------------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------------
def _pick_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    """取首个匹配请求头的值（去空白，空串视为未提供）。"""
    for key, value in headers:
        if key.lower() == name:
            decoded = value.decode("latin-1").strip()
            return decoded or None
    return None


def _pick_ip(headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None) -> str | None:
    """按 ``X-Forwarded-For`` → ``X-Real-IP`` → ``scope["client"]`` 的优先级取客户端 IP。"""
    xff = _pick_header(headers, _FORWARDED_FOR)
    if xff:
        # ⚠️ 取**最左**一项：那是原始客户端；右侧全是中间代理。
        first = xff.split(",")[0].strip()
        if first:
            return first
    real_ip = _pick_header(headers, _REAL_IP)
    if real_ip:
        return real_ip.split(",")[0].strip() or None
    if client:
        return str(client[0]) or None
    return None


def _truncate(value: str | None, width: int) -> str | None:
    if value is None:
        return None
    return value[:width]


class RateLimitMiddleware:
    """接口限流（F-1.6 / 接口 §1.4 第 1 层，超限 **42901**）。

    🔴 为什么是「中间件 + 自己应答」而不是「依赖注入 + 抛异常」
    --------------------------------------------------------
    ① 位置：§1.4 要求限流**早于鉴权**（否则恶意流量要通过 JWT 校验才被拦下，
       白白消耗签名计算）；中间件在路由解析前执行，正好满足；
    ② 应答：中间件在 Starlette 异常处理中间件的**外侧**，
       从这里 ``raise`` 会被最外层兜成 500 而不是 42901 —— 故必须直接构造响应，
       并复用 :func:`~app.exceptions.build_error_response` 保证五件套格式一致。

    🔴 身份分桶：`uid` 优先，未登录退化为 IP
    ---------------------------------------
    §1.4 要求按 ``uid`` + 接口维度。但中间件执行时**鉴权依赖尚未运行**，
    上下文里还没有 uid。因此这里对 ``Authorization: Bearer`` 做一次
    **best-effort 解析**（验签通过才取 ``sub``，失败/缺失即当作匿名）：

    - 解析失败**不报错、不放行判断**，只是把桶退化为 IP —— 真正的 40101
      仍由第 2 层鉴权依赖给出，**两处职责不重叠**（这里为分桶，那里为放行）；
    - 为什么仍要验签：不验签的话，攻击者只要伪造 ``sub`` 就能换一个新桶，
      限流形同虚设。验签把「换桶」的成本拉回「无法伪造」。

    🔴 路径归一化：必须做，否则限流可被绕过
    --------------------------------------
    若直接用 ``scope["path"]``，则 ``/talents/123``、``/talents/124`` …
    每个 ID 都是一个**独立的桶**，攻击者遍历 ID 即可无限刷。
    故把「长得像 ID 的段」统一替换为 ``{id}``（纯数字 / 雪花 ID / UUID / 长十六进制），
    让桶真正对应**接口**而非**资源实例**。这是保守的启发式：
    归一化只会让多个实例共享同一个桶（更严），不会放宽。

    🔴 降级：**fail-open**
    Redis 属 DS-3「可丢数据」，抖动时放行并在日志告警（见 :mod:`app.core.ratelimit`）。
    代价是「Redis 挂掉期间无限流」—— 这是刻意的取舍：**宁可短时限流失效，
    不可让缓存抖动演变成全站 429**。
    """

    #: 不参与限流的路径前缀（探针 / 文档 / 静态），避免监控轮询把额度吃掉
    EXEMPT_PREFIXES: tuple[str, ...] = ("/health", "/docs", "/redoc", "/openapi.json")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 延迟导入：config / ratelimit 会拉起 Redis 客户端与设置，避免 import 期副作用
        from app.config import settings

        if not settings.RATE_LIMIT_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if path.startswith(self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        from app.core.ratelimit import check_rate_limit, http_rate_key

        identity = _rate_identity(scope)
        key = http_rate_key(identity, normalize_rate_path(path))
        decision = check_rate_limit(key, settings.RATE_LIMIT_PER_MINUTE)

        if decision.allowed:
            await self.app(scope, receive, send)
            return

        from app.exceptions import ErrorCode, build_error_response

        logger.warning(
            "触发限流 identity=%s path=%s limit=%s retry_after=%s",
            identity,
            path,
            decision.limit,
            decision.retry_after,
        )
        response = build_error_response(
            ErrorCode.RATE_LIMITED,
            data={"retry_after": decision.retry_after, "limit": decision.limit},
            headers={
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(decision.retry_after),
            },
        )
        await response(scope, receive, send)


# ----------------------------------------------------------------------------
# 限流身份与路径归一化
# ----------------------------------------------------------------------------
#: 视为「资源实例 ID」的段：**纯数字**（任意位数）/ 长十六进制 / UUID。
#:
#: ⚠️ 这里**不设位数下限**（不写 ``\d{4,}``）。曾考虑「太短的不像 ID」，
#: 但那会留下一个真实的口子：``/users/1`` … ``/users/999`` 会被当成 999 个不同的桶，
#: 攻击者遍历 ID 即可把限流放大近千倍。
#: 归一化的方向必须是**收紧**：哪怕把某个「碰巧纯数字的业务编码」（如 ``/dicts/2024/items``）
#: 也并进同一个桶，代价只是「该接口更容易触发限流」—— 这是可接受的一侧。
_ID_SEGMENT_RE = re.compile(r"^(?:\d+|[0-9a-fA-F]{16,}|\d{8}-\d{4}-\d{4}-\d{4}-\d{12})$")

#: 归一化后的路径长度上限（避免畸形超长路径把 Redis 键撑爆）
_MAX_RATE_PATH_LEN = 200


def _rate_identity(scope: Scope) -> str:
    """限流主体：已鉴权取 ``uid:<id>``，否则 ``ip:<addr>``（best-effort，见类 docstring）。"""
    uid = _best_effort_uid(scope)
    if uid:
        return f"uid:{uid}"
    from app.core.context import get_client_ip

    ip = get_client_ip() or (scope.get("client") or ("unknown", 0))[0]
    return f"ip:{ip}"


def _best_effort_uid(scope: Scope) -> str | None:
    """尽力从 ``Authorization`` 头解析出 ``sub``；任何失败都返回 ``None``（不抛异常）。"""
    token: str | None = None
    for key, value in scope.get("headers") or []:
        if key.lower() == b"authorization":
            raw = value.decode("latin-1").strip()
            if raw[:7].lower() == "bearer ":
                token = raw[7:].strip() or None
            break
    if not token:
        return None

    try:
        # 延迟导入：core.security → services 的延迟链在 import 期不必拉起
        from app.core.security import TOKEN_TYPE_ACCESS, SecurityUtils

        # ⚠️ 必须限定 ``typ=access``：否则拿 refresh_token 的 sub 会落到**另一个桶**，
        #    等于给攻击者多一份额度（两个 Token 类型各 300/min）。
        payload = SecurityUtils.decode_token(token, expect_type=TOKEN_TYPE_ACCESS)
    except Exception:  # noqa: BLE001 — best-effort：解析失败一律按匿名处理
        return None
    sub = payload.get("sub")
    return str(sub) if sub not in (None, "") else None


def normalize_rate_path(path: str) -> str:
    """把路径里的资源实例 ID 归一化为 ``{id}``（见类 docstring 的「路径归一化」）。"""
    # ⚠️ 用哨兵对象而不是空串：``/a//b`` 这类路径本身就有空段，
    #    若拿空串当「已归一化」的标记，会把真实的空段也写成 ``{id}``。
    sentinel = object()
    parts: list[object] = [sentinel if _ID_SEGMENT_RE.match(p) else p for p in path.split("/")]
    normalized = "/".join("{id}" if p is sentinel else str(p) for p in parts)
    return normalized[:_MAX_RATE_PATH_LEN]
