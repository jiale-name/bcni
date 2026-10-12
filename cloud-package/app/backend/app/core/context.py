"""请求上下文。

接口 §1.4 第 8 层要求「任何异常都不得裸抛 —— 由 exception_handlers 兜成统一结构」，
而统一响应体里的 ``trace_id`` 必须与日志、与响应头 ``X-Request-Id`` **三者一致**。
用 :mod:`contextvars` 而不是 ``threading.local`` —— 后者在 async 场景下不安全。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

#: 当前请求的 trace_id；未进入请求上下文时为 None
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

#: 当前请求的用户 ID（鉴权后填充，仅用于日志与限流，**不作为权限依据**）
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

#: 客户端 IP —— **只用于审计留痕与限流计数，绝不作为权限依据**
#: （来源见 :class:`app.core.middleware.RequestMetaMiddleware`；可被 ``X-Forwarded-For`` 影响，
#: 故仅作展示/排障，不能拿它做安全判定。）
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)

#: 客户端 User-Agent —— 同上，仅供审计留痕
user_agent_var: ContextVar[str | None] = ContextVar("user_agent", default=None)


def new_trace_id() -> str:
    """生成 trace_id：32 位 hex，去掉分隔符（接口示例为 ``9f2a1c7e4b0d`` 风格）。"""
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    return trace_id_var.get()


def set_trace_id(value: str | None) -> None:
    trace_id_var.set(value)


def set_user_id(value: str | int | None) -> None:
    """写入当前用户 ID（字符串形式，避免雪花 ID 精度问题）。"""
    user_id_var.set(None if value is None else str(value))


def get_user_id() -> str | None:
    return user_id_var.get()


def set_request_meta(*, client_ip: str | None, user_agent: str | None) -> None:
    """写入客户端 IP / UA（由 :class:`~app.core.middleware.RequestMetaMiddleware` 在链首调用）。

    🔴 这两个值来自请求头，**不可信**：
    - 仅用于**审计留痕**（TBL-28 的 ``ip`` / ``ua`` 两列）与排障；
    - ❌ 不得用于权限判定、不得用于限流主体标识的唯一依据
      （限流主体优先取已鉴权的 ``uid``，未登录才退化为 IP）。
    """
    client_ip_var.set(client_ip)
    user_agent_var.set(user_agent)


def get_client_ip() -> str | None:
    return client_ip_var.get()


def get_user_agent() -> str | None:
    return user_agent_var.get()
