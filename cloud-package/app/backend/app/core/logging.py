"""结构化日志 + trace_id（架构 §8）。

约定
----
1. 每行日志自动带 ``trace_id``（来自 :mod:`app.core.context`），与响应体 ``trace_id``、
   响应头 ``X-Request-Id`` 三处一致，可全链路串联（F-1.4）。
2. 🔴 **密码明文永不入日志**（接口 API-01 备注）。使用 :func:`safe_payload` 做脱敏，
   或直接不要打印请求体。
3. 生产环境输出 JSON（便于采集），开发环境输出可读单行文本。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.config import settings
from app.core.context import get_trace_id

#: 命中这些键名的值一律替换为 ``***``（大小写不敏感）
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "old_password",
        "confirm_password",
        "access_token",
        "refresh_token",
        "token",
        "authorization",
        "secret",
        "api_key",
        "id_card",
        "phone",
        "email",
    }
)

_REDACTED = "***"


def safe_payload(payload: Any, _depth: int = 0) -> Any:
    """递归脱敏：把敏感键的值替换为 ``***``。日志里要打印请求体时必须先过这里。"""
    if _depth > 6:
        return "<max-depth>"
    if isinstance(payload, dict):
        return {
            k: (_REDACTED if str(k).lower() in SENSITIVE_KEYS else safe_payload(v, _depth + 1))
            for k, v in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [safe_payload(item, _depth + 1) for item in payload]
    return payload


class TraceIdFilter(logging.Filter):
    """给每条日志注入 ``trace_id`` 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = get_trace_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """生产环境 JSON 格式。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """开发环境可读格式。"""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<7} "
            f"[{getattr(record, 'trace_id', '-')}] "
            f"{record.name} - {record.getMessage()}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str | None = None) -> None:
    """初始化根 logger。应用启动时调用一次（``app/main.py``）。"""
    log_level = level or ("INFO" if not settings.DEBUG else "DEBUG")
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(TraceIdFilter())
    handler.setFormatter(JsonFormatter() if settings.is_prod else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # 降噪：SQLAlchemy / uvicorn.access 由应用层日志替代
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DB_ECHO else logging.WARNING
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # 🔴 降噪：第三方 HTTP 库 httpx / httpcore 默认向 root 传播 DEBUG 日志
    # （connect / send headers / receive body / complete 等），在 DEBUG=true
    # 时会淹没业务日志。保留 httpx 的 INFO 级「请求→状态码」单行摘要
    # （排查外部 API 调用时有用），只砍掉 DEBUG 级握手/报文噪声；
    # httpcore 基本只有 DEBUG 噪声，直接压到 WARNING。
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
