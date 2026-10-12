"""Token 吊销与会话状态（Redis ``sess:*``，F-1.2）。

职责边界
--------
JWT 是**无状态**的，Token 能否使用由「签名 + 过期时间」决定。本模块只解决
**「提前失效」**这一类需求：

1. **单 Token 吊销**（API-03 登出）：把 ``jti`` 写入否定名单，TTL = 该 Token 剩余有效期。
2. **用户级全量失效**（API-06 改密 / 强制下线）：维护一个**单调递增的 Token 版本号**，
   Token 里带签发时刻的版本 ``ver``，``ver < 当前版本`` 即失效。

> ⚠️ **为什么用版本号而不是时间戳**（踩坑记录）：
> 曾用「记录 ``logout_at`` 时间戳 + 比较 Token 的 ``iat``」实现，但 JWT 的 ``iat`` 是
> **秒级**精度 —— 在「改密后同一秒内重新登录」的场景下，新 Token 的 ``iat`` 与
> ``logout_at`` 相等，会被误判为失效（旧 Token 也可能因此漏判）。
> 版本号不受时钟精度影响，且天然支持「同秒多次失效」。

🔴 **Redis 属 DS-3「可丢数据」**：读写异常时一律 **fail-open**（视为未吊销）并告警。
   理由：把 Redis 抖动升级为「全站 401」的代价远大于「已登出的 Token 还能用几分钟」；
   且登出撤销不是本项目唯一的安全边界（密码校验 / 权限快照每请求校验仍在）。
   若后续需要 fail-closed，只需让 :func:`get_token_version` 在异常时返回一个**哨兵大值**。

⚠️ **不使用 Redis 存正向会话清单**（ADR-11 同理）：正向会话不可丢，Redis 丢了就没人能登录。
"""

from __future__ import annotations

import time

from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.dao.redis_client import KEY_SESSION, build_key, get_redis

logger = get_logger(__name__)

#: 视为「Redis 不可用」的异常集合
_REDIS_ERRORS = (RedisError, OSError)

_KEY_REVOKED = "revoked"  # sess:revoked:<jti>
_KEY_TOKEN_VER = "tok_ver"  # sess:tok_ver:<tid>:<uid>


def now_epoch() -> int:
    """当前 Unix 秒。集中在此处，便于测试打桩（不散落 ``time.time()``）。"""
    return int(time.time())


def _revoked_key(jti: str) -> str:
    return build_key(KEY_SESSION, _KEY_REVOKED, jti)


def _token_ver_key(tenant_id: int | str, user_id: int | str) -> str:
    return build_key(KEY_SESSION, _KEY_TOKEN_VER, tenant_id, user_id)


# ============================================================================
# 单 Token 吊销（登出）
# ============================================================================
def revoke_token(jti: str | None, ttl_seconds: int) -> bool:
    """把 ``jti`` 写入否定名单。

    :param ttl_seconds: 该 Token 的剩余有效期；≤0 表示已过期，无需写入（返回 ``True``）
    :return: 是否成功写入（Redis 不可用时 ``False``，调用方只记日志，**不阻断登出**）
    """
    if not jti:
        return True
    if ttl_seconds <= 0:
        return True  # 已过期，JWT 自身的 exp 校验即可拦住
    try:
        get_redis().set(_revoked_key(jti), "1", ex=int(ttl_seconds))
        return True
    except _REDIS_ERRORS as exc:
        logger.warning("Token 吊销写入失败（Redis 不可用）jti=%s err=%s", jti, exc)
        return False


def is_token_revoked(jti: str | None) -> bool:
    """``jti`` 是否在否定名单中。Redis 不可用时返回 ``False``（fail-open）。"""
    if not jti:
        return False
    try:
        return bool(get_redis().exists(_revoked_key(jti)))
    except _REDIS_ERRORS as exc:
        logger.warning("Token 吊销查询失败（fail-open）jti=%s err=%s", jti, exc)
        return False


# ============================================================================
# 用户级全量失效（改密 / 强制下线）
# ============================================================================
def get_token_version(tenant_id: int | str, user_id: int | str) -> int:
    """取该用户的当前 Token 版本。无记录（从未失效过）或 Redis 不可用返回 ``0``。

    Token 签发时把返回值写入 ``ver`` claim；校验时要求 ``ver >= 当前版本``。
    """
    try:
        value = get_redis().get(_token_ver_key(tenant_id, user_id))
        return int(value) if value else 0
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(
            "Token 版本查询失败（fail-open）tid=%s uid=%s err=%s", tenant_id, user_id, exc
        )
        return 0


def bump_token_version(tenant_id: int | str, user_id: int | str, ttl_seconds: int) -> int:
    """递增 Token 版本，使该用户**此前签发的全部 Token 立即失效**。

    调用点：API-06 改密、API-03 ``all_devices=true``。

    :return: 递增后的版本号；Redis 不可用时返回 ``-1``（表示本次未能撤销旧会话）
    """
    try:
        client = get_redis()
        key = _token_ver_key(tenant_id, user_id)
        with client.pipeline(transaction=True) as pipe:
            pipe.incr(key, 1)
            pipe.expire(key, max(1, int(ttl_seconds)))
            version, _ = pipe.execute()
        return int(version)
    except _REDIS_ERRORS as exc:
        logger.warning(
            "Token 版本递增失败（Redis 不可用，旧会话未撤销）tid=%s uid=%s err=%s",
            tenant_id,
            user_id,
            exc,
        )
        return -1

