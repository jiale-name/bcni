"""联系方式比对哈希（``core.crypto.hash_contact``）的契约测试。

这块代码虽小，但**两端必须完全一致**才能用：甲方标注集在甲方环境算一次哈希，
乙方评估时对系统抽取结果再算一次 —— 归一化规则或盐任何一处不同，
IDX-02 的「联系方式」字段就会全判错，而现象是「准确率莫名其妙很低」，
极难定位。故在此把归一化与「无盐即报错」两条钉死。
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.crypto import (
    ContactHashSaltError,
    contact_hash_salt_configured,
    hash_contact,
    normalize_contact,
)


class TestNormalize:
    def test_phone_strips_formatting(self) -> None:
        for raw in ("13812348888", "138 1234 8888", "138-1234-8888", "+8613812348888", "8613812348888"):
            assert normalize_contact(raw, "phone") == "13812348888", raw

    def test_email_lowercases_and_trims(self) -> None:
        assert normalize_contact("  Zhang.San@Example.COM  ", "email") == "zhang.san@example.com"

    def test_id_card_uppercases(self) -> None:
        assert normalize_contact("11010119900307x", "id_card") == "11010119900307X"

    def test_phone_without_86_prefix_kept_intact(self) -> None:
        """11 位号码不得被误剥前两位（只有 >11 位且以 86 开头才剥）。"""
        assert normalize_contact("86123456789", "phone") == "86123456789"


class TestHashContact:
    def test_returns_64_hex_for_char64_column(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TBL-11 的 ``phone_hash`` 是 ``CHAR(64)`` —— 输出必须正好填满。"""
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        digest = hash_contact("13812348888", "phone")
        assert digest is not None
        assert len(digest) == 64
        assert all(ch in "0123456789abcdef" for ch in digest)

    def test_same_entity_same_hash_across_writing_styles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        assert hash_contact("+86 138-1234-8888", "phone") == hash_contact("13812348888", "phone")
        assert hash_contact("A@B.com", "email") == hash_contact(" a@b.com ", "email")

    def test_different_entities_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        assert hash_contact("13812348888", "phone") != hash_contact("13812348889", "phone")

    def test_kind_is_part_of_the_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一串在不同语义下不得撞哈希（防「手机号与身份证号同值」的荒谬碰撞）。"""
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        assert hash_contact("12345678901", "phone") != hash_contact("12345678901", "id_card")

    def test_salt_changes_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "salt-a")
        first = hash_contact("13812348888", "phone")
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "salt-b")
        assert hash_contact("13812348888", "phone") != first

    def test_none_and_blank_return_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        assert hash_contact(None, "phone") is None
        assert hash_contact("", "phone") is None
        assert hash_contact("   ", "phone") is None

    def test_missing_salt_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔴 未配置盐时必须抛错，❌ 绝不静默退化成不加盐的弱哈希。"""
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "")
        with pytest.raises(ContactHashSaltError):
            hash_contact("13812348888", "phone")

    def test_salt_configured_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "")
        assert contact_hash_salt_configured() is False
        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "  abc  ")
        assert contact_hash_salt_configured() is True

    def test_plain_sha256_would_be_brute_forceable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回归守卫：确认我们**没有**退化成裸 SHA256（手机号仅 10^11 空间，秒级可穷举）。"""
        import hashlib

        monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
        naive = hashlib.sha256("13812348888".encode()).hexdigest()
        assert hash_contact("13812348888", "phone") != naive
