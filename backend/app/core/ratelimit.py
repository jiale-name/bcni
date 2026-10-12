"""限流与计数（F-1.6 / F-1.2 登录锁定）。同步实现。

两个消费者
----------
1. **登录失败锁定**（F-1.2，CU-04 本次实现）：Redis ``rate:login:<tid>:<username>`` 计数，
   连续 5 次失败锁定 10 分钟（接口 API-01 备注）。**MySQL ``sys_user.locked_until`` 为权威**，
   Redis 只做快速计数。
2. **HTTP 接口限流**（F-1.6 / 接口 §1.4 第 1 层，**CU-09 接入中间件**）：
   按 ``uid``（未登录时按 IP）+ 接口维度计数，超限抛 42901。

🔴 **降级策略：fail-open**
Redis 属 DS-3「可丢数据」，抖动时**不得拖垮业务**：计数不可用时放行并在日志留痕。
登录锁定在 Redis 不可用时**回落到 MySQL ``locked_until`` 字段**判定，安全性不退让。
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.dao.redis_client import KEY_RATE, build_key, get_redis

logger = get_logger(__name__)

#: 视为「Redis 不可用」的异常集合
_REDIS_ERRORS = (RedisError, OSError)


# ============================================================================
# 通用计数
# ============================================================================
def incr_counter(key: str, ttl_seconds: int) -> int | None:
    """自增计数并在首次写入时设置 TTL。

    :return: 自增后的计数值；**Redis 不可用时返回 ``None``**（调用方须按 fail-open 处理）
    """
    try:
        client = get_redis()
        with client.pipeline(transaction=True) as pipe:
            pipe.incr(key, 1)
            pipe.ttl(key)
            count, ttl = pipe.execute()
        if int(ttl) < 0:  # -1 无过期 / -2 键不存在
            client.expire(key, ttl_seconds)
        return int(count)
    except _REDIS_ERRORS as exc:
        logger.warning("限流计数不可用（fail-open 放行）key=%s err=%s", key, exc)
        return None


def get_counter(key: str) -> int:
    """读取计数；不可用时返回 0。"""
    try:
        value = get_redis().get(key)
        return int(value) if value is not None else 0
    except _REDIS_ERRORS as exc:
        logger.warning("读取限流计数失败 key=%s err=%s", key, exc)
        return 0


def get_ttl(key: str) -> int:
    """剩余秒数；不可用或无过期返回 0。"""
    try:
        return max(0, int(get_redis().ttl(key)))
    except _REDIS_ERRORS:
        return 0


def reset_counter(key: str) -> None:
    """清零计数（登录成功后调用）。"""
    try:
        get_redis().delete(key)
    except _REDIS_ERRORS as exc:
        logger.warning("清空限流计数失败 key=%s err=%s", key, exc)


# ============================================================================
# 键构造
# ============================================================================
def login_fail_key(tenant_id: int | str, username: str) -> str:
    """登录失败计数键（接口 API-01 备注：``rate:login:<username>``）。"""
    return build_key(KEY_RATE, "login", tenant_id, username.lower())


def http_rate_key(identity: str, path: str) -> str:
    """HTTP 限流键：按「主体 + 接口」维度（接口 §1.4 第 1 层）。"""
    return build_key(KEY_RATE, "http", identity, path)


# ============================================================================
# 判定
# ============================================================================
@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int = 0


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> RateLimitDecision:
    """固定窗口判定。Redis 不可用时 **fail-open**（放行）。

    供 CU-09 的 HTTP 限流中间件调用。
    """
    count = incr_counter(key, window_seconds)
    if count is None:
        return RateLimitDecision(allowed=True, limit=limit, remaining=limit)
    if count > limit:
        retry_after = get_ttl(key)
        return RateLimitDecision(
            allowed=False, limit=limit, remaining=0, retry_after=retry_after or window_seconds
        )
    return RateLimitDecision(allowed=True, limit=limit, remaining=max(0, limit - count))
