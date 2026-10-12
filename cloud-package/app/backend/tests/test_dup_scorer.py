"""CU-19 · 确定性查重比对（DupScorer）+ 查重阈值（ThresholdEngine，L1 纯函数）。

铁律 10：相似度结论**必须**来自确定性组件，不来自 LLM。本文件把
:class:`~app.core.dup.DupScorer` 与 :meth:`ThresholdEngine.is_dup_candidate`
的两条核心性质钉死：

1. **精确键豁免阈值**：``phone_hash`` / ``id_card_hash`` 一致 → 恒判重复（相似度 1.0），
   即使向量相似度低于阈值；
2. **空哈希不算命中**：两条都为 ``NULL`` 的联系方式 **不** 判成重复。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.dup import DupScore, DupScorer
from app.core.threshold import ThresholdEngine


def _talent(*, phone: str | None = None, idcard: str | None = None,
            name: str | None = None, school: str | None = None,
            major: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        phone_hash=phone, id_card_hash=idcard, name=name, school=school, major=major
    )


# ============================================================================
# DupScorer.exact_key_match
# ============================================================================
def test_exact_key_match_phone() -> None:
    a = _talent(phone="h1")
    b = _talent(phone="h1")
    assert DupScorer.exact_key_match(a, b) == ["phone_hash"]


def test_exact_key_match_idcard() -> None:
    a = _talent(idcard="c1")
    b = _talent(idcard="c1")
    assert DupScorer.exact_key_match(a, b) == ["id_card_hash"]


def test_exact_key_match_both() -> None:
    a = _talent(phone="h1", idcard="c1")
    b = _talent(phone="h1", idcard="c1")
    assert DupScorer.exact_key_match(a, b) == ["phone_hash", "id_card_hash"]


def test_null_hash_is_not_a_match() -> None:
    """两条都为 NULL 的联系方式**不算**命中（否则「都没填手机号」的人会被误判重复）。"""
    a = _talent(phone=None)
    b = _talent(phone=None)
    assert DupScorer.exact_key_match(a, b) == []


# ============================================================================
# DupScorer.field_matches / score
# ============================================================================
def test_field_matches_normalized() -> None:
    a = _talent(name="  Zhang San ", school="Tsinghua", major="CS")
    b = _talent(name="zhang san", school="tsinghua", major="cs")
    assert DupScorer.field_matches(a, b) == ["name", "school", "major"]


def test_score_exact_returns_one() -> None:
    score = DupScorer.score(_talent(phone="h1"), _talent(phone="h1"), vector_similarity=0.3)
    assert score.exact is True
    assert score.similarity == 1.0
    assert score.match_fields == ["phone_hash"]


def test_score_vector_clamped_to_01() -> None:
    score = DupScorer.score(_talent(name="a"), _talent(name="b"), vector_similarity=1.7)
    assert score.similarity == 1.0 and score.exact is False
    score2 = DupScorer.score(_talent(name="a"), _talent(name="b"), vector_similarity=-0.2)
    assert score2.similarity == 0.0


def test_score_no_vector_falls_back_to_fields() -> None:
    # 无向量、名字命中 → 保守低分（绝不与向量相似度混同）
    score = DupScorer.score(_talent(name="张三"), _talent(name="张三"), vector_similarity=None)
    assert score.exact is False and score.similarity == 0.6


# ============================================================================
# ThresholdEngine.is_dup_candidate
# ============================================================================
def test_is_dup_exact_key_bypasses_threshold() -> None:
    """精确键命中豁免阈值：相似度 0.1 也判重复（联系方式一致 = 同一人）。"""
    decision = ThresholdEngine.is_dup_candidate(similarity=0.1, exact_key_match=True)
    assert decision.is_dup is True and decision.similarity == 1.0 and decision.exact is True


def test_is_dup_similarity_above_threshold() -> None:
    decision = ThresholdEngine.is_dup_candidate(similarity=0.95, threshold=0.90)
    assert decision.is_dup is True and decision.exact is False
    assert "similarity_threshold" in decision.reasons


def test_is_dup_similarity_below_threshold() -> None:
    decision = ThresholdEngine.is_dup_candidate(similarity=0.85, threshold=0.90)
    assert decision.is_dup is False and decision.reasons == []


def test_is_dup_threshold_injectable() -> None:
    """阈值可注入（不依赖 settings，纯函数可测）。"""
    assert ThresholdEngine.is_dup_candidate(similarity=0.5, threshold=0.4).is_dup is True
    assert ThresholdEngine.is_dup_candidate(similarity=0.5, threshold=0.6).is_dup is False
