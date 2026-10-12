"""DS-3 Redis 客户端与键规范（数据库设计 §7.3）。

> 目录树补充说明：架构 §5 把 Redis 访问归属 ``dao/`` 层（数据访问），故新增本文件 ——
> 与 ``dao/session.py``（MySQL 会话）并列。属目录树细化，不构成架构变更。

🔴 **DS-3 只存可丢数据**（数据库 §1）：``sess:*`` 会话、``cache:*`` 缓存、``rate:*`` 限流、
``agent_mem:*`` 短期记忆、``queue:*`` 队列。**Agent 权威状态必须落 MySQL**（ADR-11），
❌ 不得以 Redis 作为任务恢复依据。

技术选型说明：本项目统一使用**同步驱动**（SQLAlchemy 同步 Session + 同步 Redis），
路由以 ``def`` 声明，由 FastAPI 放入线程池执行 —— 既不会阻塞事件循环，
也让 Alembic 与业务代码共用同一套引擎配置。SSE / 流式接口（N2/N5）将用 ``async def``
并对 DB 调用做 ``run_in_threadpool`` 卸载。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import redis

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 键前缀（对齐数据库 §7.3）
KEY_SESSION = "sess:"
KEY_CACHE = "cache:"
KEY_RATE = "rate:"
KEY_AGENT_MEM = "agent_mem:"
KEY_QUEUE = "queue:"
KEY_IDEMPOTENT = "idem:"  # 幂等键（接口 §1.6，有效期 24h）


def build_key(prefix: str, *parts: Any) -> str:
    """拼装键：``build_key(KEY_RATE, "login", tenant_id, username)``。"""
    joined = ":".join(str(p) for p in parts if p is not None and p != "")
    if not joined:
        return f"{settings.REDIS_KEY_PREFIX}{prefix}".rstrip(":")
    return f"{settings.REDIS_KEY_PREFIX}{prefix}{joined}"


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """Redis 客户端单例（同步）。

    🔴 **失败必须可降级**：Redis 是「可丢数据」存储，读写异常时调用方应捕获
    ``RedisError`` 并走降级分支（如：限流放行、登录锁定改用 MySQL ``locked_until`` 判定），
    ❌ 不得让 Redis 抖动导致业务 500。

    ⚠️ **``protocol`` 由配置控制，默认 RESP2**（``REDIS_PROTOCOL=2``）：
    redis-py 8.x 的默认协议是 RESP3，连接时先发 ``HELLO 3`` 协商，而 ``HELLO`` 是
    Redis 6.0 才有的命令 —— 旧版 / Windows 版 / 部分国产 Redis 会直接返回
    ``unknown command `HELLO```，表现为「Redis 进程明明活着，应用却连不上」。
    本项目不需要 RESP3 的推送能力，固定 RESP2 换取最大兼容性。

    ⚠️ ``encoding_errors="replace"``：部分中文 Windows 版 Redis 的 ``INFO`` 等命令
    返回 GBK 文本，严格 UTF-8 解码会抛 UnicodeDecodeError。本项目存入的值均为
    ASCII / JSON（不会产生非法字节），故用替换策略兜住，避免读管理类命令时崩。
    """
    pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        decode_responses=True,
        encoding="utf-8",
        encoding_errors="replace",
        protocol=int(settings.REDIS_PROTOCOL),
        socket_connect_timeout=2,
        socket_timeout=2,
        max_connections=32,
        health_check_interval=30,
    )
    return redis.Redis(connection_pool=pool)


def ping() -> bool:
    """连通性探测（供 ``/health`` 与 N0 实测脚本使用）。"""
    try:
        return bool(get_redis().ping())
    except Exception as exc:  # noqa: BLE001 — 探测函数，任何异常都视为不可用
        logger.warning("Redis 不可用：%s", exc)
        return False


def server_version() -> str | None:
    """尽力探测服务端版本（供 ``/health`` 展示排障信息）。

    ⚠️ 不参与业务判断：旧版 Redis 的 ``INFO`` 可能返回非 UTF-8 文本，
    故整段吞掉异常，失败返回 ``None``。
    """
    try:
        info = get_redis().info("server")
        return str(info.get("redis_version") or "") or None
    except Exception:  # noqa: BLE001 — 纯诊断用途
        return None


def close_redis() -> None:
    """应用关闭时释放连接池。"""
    try:
        get_redis().close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("关闭 Redis 连接时忽略异常：%s", exc)
    finally:
        get_redis.cache_clear()
