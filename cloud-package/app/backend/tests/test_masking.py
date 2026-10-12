"""脱敏与回填组件（CU-21，CL-4）—— L1 契约层，无外部依赖。

被测对象：``app.core.masking``（全项目风险最高的四条行为约定，逐条钉死）。
"""

from __future__ import annotations

from app.core.masking import (
    PLACEHOLDER_CANDIDATE,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_ID,
    PLACEHOLDER_PHONE,
    PlaceholderTable,
    mask_text,
    rehydrate,
    verify_outbound,
)


# ============================================================================
# 行为约定 1 · 先脱敏再出网（mask）
# ============================================================================
def test_mask_replaces_known_identity() -> None:
    table = PlaceholderTable()
    text = "李伟的数据治理经验丰富，手机 13812345678"
    masked = mask_text(text, table, name="李伟", phone="13812345678")
    assert "李伟" not in masked
    assert "13812345678" not in masked
    assert PLACEHOLDER_CANDIDATE in masked
    assert PLACEHOLDER_PHONE in masked


def test_mask_regex_fallback_registers_value_for_rehydrate() -> None:
    """兜底正则扫出的 PII（未显式登记）也要进占位符表 —— 否则回不来了。"""
    table = PlaceholderTable()
    masked = mask_text("联系邮箱 someone@example.com", table)
    assert "someone@example.com" not in masked
    assert PLACEHOLDER_EMAIL in masked
    # 回填能还原（占位符表里有值）
    assert rehydrate(masked, table) == "联系邮箱 someone@example.com"


def test_mask_id_card_never_survives() -> None:
    table = PlaceholderTable()
    masked = mask_text("证件号 110101199003077758", table)
    assert "110101199003077758" not in masked
    assert PLACEHOLDER_ID in masked


def test_same_value_reuses_same_token() -> None:
    """同一真值复用同一 token（否则模型把同一个人当成不同实体，指代被破坏）。"""
    table = PlaceholderTable()
    out1 = mask_text("李伟 主导项目", table, name="李伟")
    out2 = mask_text("李伟 的团队", table, name="李伟")
    assert out1 == out2.replace(" 的团队", " 主导项目")
    assert out1.count(PLACEHOLDER_CANDIDATE) == 1


# ============================================================================
# 行为约定 2 · 出网前必过二次扫描（verify_outbound）
# ============================================================================
def test_verify_outbound_clean_text_passes() -> None:
    assert verify_outbound("{{CANDIDATE}} 擅长数据治理").passed is True


def test_verify_outbound_catches_phone_and_email_and_id() -> None:
    result = verify_outbound("手机 13812345678 邮箱 a@b.com 证 110101199003077758")
    assert result.passed is False
    assert set(result.residual) == {"phone", "email", "id_card"}


def test_verify_outbound_catches_known_plaintext_name() -> None:
    """正则扫不出人名 —— 必须用「本次请求涉及的真值」反向找（CL-4 的关键补丁）。"""
    result = verify_outbound("李伟 擅长数据治理", known_names=["李伟"])
    assert result.passed is False
    assert "name" in result.residual


# ============================================================================
# 行为约定 3 · 回填只发生在服务端（rehydrate + PlaceholderTable 不落库）
# ============================================================================
def test_rehydrate_restores_all_placeholders() -> None:
    table = PlaceholderTable()
    masked = mask_text("李伟（13812345678）擅长数据治理", table, name="李伟", phone="13812345678")
    assert rehydrate(masked, table) == "李伟（13812345678）擅长数据治理"


def test_rehydrate_returns_none_on_unresolvable_token() -> None:
    """存在解析不了的占位符 → 返回 None（调用方必须拒答 MASK_REQUIRED）。"""
    table = PlaceholderTable()
    table.put("candidate", "李伟")
    assert rehydrate("李伟 的邮箱是 {{EMAIL}}", table) is None


def test_placeholder_table_is_in_memory_only() -> None:
    """占位符表是纯内存对象（❌ 不落库/不进 Redis —— CL-4 约定 3 的结构保证）。"""
    table = PlaceholderTable()
    table.put("candidate", "李伟")
    assert table.to_local_map() == {PLACEHOLDER_CANDIDATE: "李伟"}
    # 空值登记原样返回 None，不产生占位符
    assert table.put("phone", None) is None
    assert table.resolve(PLACEHOLDER_PHONE) is None
