"""CU-04 · 密码 / JWT / 请求主体（F-1.2、接口 §1.3）。"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.security import SecurityUtils, TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH
from app.exceptions import UnauthenticatedError
from app.models.enums import RoleDataScope


# ============================================================================
# 密码
# ============================================================================
def test_password_hash_is_bcrypt_and_verifiable() -> None:
    hashed = SecurityUtils.hash_password("Hr@2026pass")
    assert hashed.startswith("$2")
    assert hashed != "Hr@2026pass"
    assert SecurityUtils.verify_password("Hr@2026pass", hashed) is True
    assert SecurityUtils.verify_password("wrong-password", hashed) is False


def test_same_password_yields_different_hash() -> None:
    """bcrypt 自带盐 —— 同一口令两次哈希必须不同。"""
    assert SecurityUtils.hash_password("same-pass-123") != SecurityUtils.hash_password("same-pass-123")


def test_verify_password_never_raises_on_garbage_hash() -> None:
    """脏数据（手工改库、历史明文）不得让登录接口 500。"""
    assert SecurityUtils.verify_password("x", "not-a-bcrypt-hash") is False
    assert SecurityUtils.verify_password("x", "") is False


def test_password_longer_than_bcrypt_limit_is_rejected() -> None:
    """bcrypt 只取前 72 字节，超长必须显式报错而非静默截断（否则安全性被悄悄削弱）。"""
    with pytest.raises(ValueError):
        SecurityUtils.hash_password("x" * 73)


# ============================================================================
# Token
# ============================================================================
def _access(**overrides):
    kwargs = {
        "user_id": 1839204771234567890,
        "tenant_id": 1839204770000000001,
        "org_id": 1839204770000000100,
        "roles": ["HR"],
        "data_scope": int(RoleDataScope.ALL),
    }
    kwargs.update(overrides)
    return SecurityUtils.create_access_token(**kwargs)


def test_access_token_carries_all_required_claims() -> None:
    """接口 §1.3：claim 必须齐备 ``sub / tid / oid / rls / dsc``。"""
    payload = SecurityUtils.decode_token(_access(), expect_type=TOKEN_TYPE_ACCESS)
    assert payload["sub"] == "1839204771234567890"  # 🔴 sub 也是字符串
    assert payload["tid"] == 1839204770000000001
    assert payload["oid"] == 1839204770000000100
    assert payload["rls"] == ["HR"]
    assert payload["dsc"] == int(RoleDataScope.ALL)
    assert payload["typ"] == TOKEN_TYPE_ACCESS
    assert payload["ver"] == 0  # 会话版本（改密/强制下线后旧版本失效）
    assert payload["jti"]


def test_token_version_is_embedded_when_provided() -> None:
    """改密后新签发的 Token 必须带上**递增后**的版本号，否则新会话会被自己误杀。"""
    payload = SecurityUtils.decode_token(_access(token_version=3), expect_type=TOKEN_TYPE_ACCESS)
    assert payload["ver"] == 3


def test_refresh_token_has_no_permission_claims() -> None:
    """🔴 refresh_token **不得**带权限 claim —— 否则降权后仍可凭旧 Token 续期拿到旧权限。"""
    token = SecurityUtils.create_refresh_token(user_id=1, tenant_id=2)
    payload = SecurityUtils.decode_token(token, expect_type=TOKEN_TYPE_REFRESH)
    assert "rls" not in payload
    assert "dsc" not in payload
    assert payload["typ"] == TOKEN_TYPE_REFRESH


def test_token_type_is_enforced() -> None:
    """拿 refresh_token 当 access_token 用 → 40101。"""
    token = SecurityUtils.create_refresh_token(user_id=1, tenant_id=2)
    with pytest.raises(UnauthenticatedError):
        SecurityUtils.decode_token(token, expect_type=TOKEN_TYPE_ACCESS)


def test_tampered_token_is_rejected() -> None:
    token = _access()
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(UnauthenticatedError):
        SecurityUtils.decode_token(tampered)


def test_expired_token_is_rejected() -> None:
    """用负有效期构造过期 Token（不改系统时间）。"""
    token = SecurityUtils._create_token(  # noqa: SLF001 — 单测直接构造过期凭证
        user_id=1, tenant_id=2, token_type=TOKEN_TYPE_ACCESS, expires_in=-10
    )
    with pytest.raises(UnauthenticatedError):
        SecurityUtils.decode_token(token)


def test_jwt_secret_must_be_changed_outside_dev() -> None:
    """生产环境仍用默认密钥是重大风险 —— 此处做一次显式提醒（非 FAIL，避免阻塞本地开发）。"""
    from app.config import DEV_JWT_SECRET_PLACEHOLDER

    assert len(settings.JWT_SECRET_KEY.encode()) >= 32, "HS256 密钥建议 ≥32 字节（RFC 7518）"
    if settings.is_prod:
        assert (
            settings.JWT_SECRET_KEY != DEV_JWT_SECRET_PLACEHOLDER
        ), "生产环境必须配置 JWT_SECRET_KEY（见 .env.example）"


# ============================================================================
# claim → 请求主体
# ============================================================================
def test_principal_from_claims() -> None:
    payload = SecurityUtils.decode_token(_access(roles=["HR", "MGR"]), expect_type=TOKEN_TYPE_ACCESS)
    principal = SecurityUtils.principal_from_claims(payload)
    assert principal.user_id == 1839204771234567890
    assert principal.tenant_id == 1839204770000000001
    assert principal.org_id == 1839204770000000100
    assert principal.roles == ("HR", "MGR")
    assert principal.data_scope == int(RoleDataScope.ALL)
    assert principal.is_admin is False


def test_admin_flag_only_for_adm_role() -> None:
    payload = SecurityUtils.decode_token(_access(roles=["ADM"]), expect_type=TOKEN_TYPE_ACCESS)
    assert SecurityUtils.principal_from_claims(payload).is_admin is True


def test_stats_only_principal_is_flagged() -> None:
    payload = SecurityUtils.decode_token(
        _access(roles=["AUDIT"], data_scope=int(RoleDataScope.STATS_ONLY)),
        expect_type=TOKEN_TYPE_ACCESS,
    )
    principal = SecurityUtils.principal_from_claims(payload)
    assert principal.is_stats_only is True
    assert principal.data_scope_name == "仅统计值"


def test_principal_missing_required_claim_raises() -> None:
    with pytest.raises(UnauthenticatedError):
        SecurityUtils.principal_from_claims({"sub": "1"})  # 缺 tid
