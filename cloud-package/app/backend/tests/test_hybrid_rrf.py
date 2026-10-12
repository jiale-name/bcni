"""混合召回（CU-20，CL-3 HybridRetriever）—— L1 契约层，无外部依赖。

被测对象：``app.retrieval.hybrid``（RRF 融合 + 确定性词法分）。
🔴 铁律 10：融合与打分必须可复算 —— 本文件就是「同一输入必然同一输出」的证明。
"""

from __future__ import annotations

from app.retrieval.hybrid import DEFAULT_RRF_K, HybridRetriever, lexical_score, rrf


# ============================================================================
# RRF 融合
# ============================================================================
def test_rrf_consensus_beats_single_list() -> None:
    """两路都召回的候选应排在只被一路召回的候选之前（共识信号）。"""
    fused = rrf([["a", "b"], ["b", "c"]])
    ids = [item for item, _ in fused]
    assert ids[0] == "b"  # 两路共同命中
    assert set(ids) == {"a", "b", "c"}


def test_rrf_score_is_rank_based_not_score_based() -> None:
    """RRF 只吃排名：排第 1 的贡献 1/(k+1)。"""
    fused = rrf([["x", "y"]], k=DEFAULT_RRF_K)
    assert fused[0][1] == 1.0 / (DEFAULT_RRF_K + 1)


def test_rrf_first_position_beats_later() -> None:
    """同在两路命中时，排名更靠前者融合分更高。"""
    fused = rrf([["a", "b"], ["a", "b"]])
    assert fused[0][0] == "a"


def test_rrf_tie_break_is_stable_not_random() -> None:
    """完全对称的输入下，按首次出现顺序稳定排序（不引入随机性）。"""
    first = rrf([["p", "q"], ["q", "p"]])
    second = rrf([["p", "q"], ["q", "p"]])
    assert [i for i, _ in first] == [i for i, _ in second]


def test_rrf_key_of_dedups_by_identity() -> None:
    """同一候选以不同对象出现在多路时，用 ``key_of`` 去重，代表对象取首次出现。"""
    list_a = [{"tid": 1, "via": "vector"}, {"tid": 2, "via": "vector"}]
    list_b = [{"tid": 1, "via": "keyword"}]
    fused = rrf([list_a, list_b], key_of=lambda item: item["tid"])
    assert fused[0][0]["via"] == "vector"  # 代表 = 首次出现（信息最全的一路）
    assert fused[0][1] > fused[1][1]  # tid=1 两路命中排前


def test_rrf_rejects_non_positive_k() -> None:
    try:
        rrf([["a"]], k=0)
    except ValueError:
        return
    raise AssertionError("k=0 应拒绝（1/(0+rank) 会放大成噪声）")


def test_hybrid_retriever_finish_truncates() -> None:
    retriever = HybridRetriever(k=10)
    fused = retriever.rrf([["a", "b", "c"]])
    assert [i for i, _ in retriever.finish(fused, top_n=2)] == ["a", "b"]
    assert retriever.finish(fused, top_n=0) == []


# ============================================================================
# 确定性词法分（向量链路就绪前的兜底信号）
# ============================================================================
def test_lexical_score_identical_text_is_high() -> None:
    score = lexical_score("数据治理项目经验", "主导数据治理项目")
    assert score > 0.3


def test_lexical_score_unrelated_text_is_zero() -> None:
    assert lexical_score("量子物理", "川菜烹饪") == 0.0


def test_lexical_score_is_deterministic() -> None:
    a = lexical_score("数字化转型 AI 项目经验", "擅长数字化转型，有 AI 项目经验")
    b = lexical_score("数字化转型 AI 项目经验", "擅长数字化转型，有 AI 项目经验")
    assert a == b


def test_lexical_score_handles_empty_inputs() -> None:
    assert lexical_score("", "任何文本") == 0.0
    assert lexical_score("查询", "") == 0.0
    assert lexical_score(None, None) == 0.0
