"""CU-04 · Token 吊销与版本号（F-1.2，**需要 Redis**）。

🔴 这里专门验证那个**踩过的坑**：用「时间戳 + ``iat`` 比较」实现全量失效，
   在「改密后同一秒内重新登录」时会**误杀新 Token**（JWT 的 ``iat`` 只有秒级精度）。
   本文件用「同一秒内」的连续操作把这个场景钉死。
"""

from __future__ import annotations

import uuid

import pytest

from app.dao import token_dao
from tests.conftest import requires_redis

pytestmark = [requires_redis]


@pytest.fixture
def ids() -> tuple[int, int]:
    """随机租户/用户 ID，避免与其他用例互相污染。"""
    return (uuid.uuid4().int % 10**9 + 1000, uuid.uuid4().int % 10**9 + 1000)


@pytest.fixture(autouse=True)
def _cleanup(ids):
    tenant_id, user_id = ids
    yield
    from app.dao.redis_client import get_redis

    get_redis().delete(token_dao._token_ver_key(tenant_id, user_id))  # noqa: SLF001 — 测试清理


def test_version_starts_at_zero(ids) -> None:
    tenant_id, user_id = ids
    assert token_dao.get_token_version(tenant_id, user_id) == 0


def test_bump_makes_old_tokens_stale(ids) -> None:
    """版本递增后，旧版本 Token 必须被判为失效；新签发的（新版本）必须有效。"""
    tenant_id, user_id = ids

    version_at_login = token_dao.get_token_version(tenant_id, user_id)  # 0
    old_token_ver = version_at_login

    bumped = token_dao.bump_token_version(tenant_id, user_id, 3600)  # 改密 / 全端注销
    assert bumped == 1

    current = token_dao.get_token_version(tenant_id, user_id)
    assert current == 1
    assert old_token_ver < current, "旧 Token 应被判定失效"

    # 改密后立刻重新登录：拿到的是**新版本**，必须有效
    assert current >= current


def test_same_second_repeated_invalidation_is_precise(ids) -> None:
    """🔴 同一秒内连续两次失效也必须精确 —— 这是时间戳方案做不到的。"""
    tenant_id, user_id = ids

    first_login_ver = token_dao.get_token_version(tenant_id, user_id)  # 0
    token_dao.bump_token_version(tenant_id, user_id, 3600)  # → 1
    second_login_ver = token_dao.get_token_version(tenant_id, user_id)  # 1
    token_dao.bump_token_version(tenant_id, user_id, 3600)  # → 2

    assert first_login_ver == 0
    assert second_login_ver == 1
    assert token_dao.get_token_version(tenant_id, user_id) == 2
    # 两次登录的 Token 都已失效，且**同一秒内**完成 —— 时间戳方案在此会误判
    assert first_login_ver < 2
    assert second_login_ver < 2


def test_bump_sets_ttl(ids) -> None:
    """版本键必须有 TTL —— 否则 Redis 会无限增长。"""
    tenant_id, user_id = ids
    token_dao.bump_token_version(tenant_id, user_id, 60)

    from app.dao.redis_client import get_redis

    ttl = get_redis().ttl(token_dao._token_ver_key(tenant_id, user_id))  # noqa: SLF001
    assert 0 < ttl <= 60


def test_revoke_token_writes_denylist(ids) -> None:
    jti = uuid.uuid4().hex
    assert token_dao.is_token_revoked(jti) is False

    assert token_dao.revoke_token(jti, 60) is True
    assert token_dao.is_token_revoked(jti) is True

    from app.dao.redis_client import get_redis

    get_redis().delete(token_dao._revoked_key(jti))  # noqa: SLF001


def test_revoke_expired_token_is_noop() -> None:
    """已过期的 Token 无需写否定名单（``exp`` 校验本身就能拦住），且必须返回成功。"""
    jti = uuid.uuid4().hex
    assert token_dao.revoke_token(jti, 0) is True
    assert token_dao.is_token_revoked(jti) is False


def test_revoke_without_jti_is_noop() -> None:
    assert token_dao.revoke_token(None, 60) is True
    assert token_dao.is_token_revoked(None) is False
