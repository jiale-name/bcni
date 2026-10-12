"""混合召回（CU-20，F-3.6，CL-3 的 ``HybridRetriever``）。

RRF（Reciprocal Rank Fusion）为什么用「排名」而不是「分数」
========================================================
向量余弦相似度与关键词匹配度**不可通约**（一个是 0~1 的语义距离，一个是命中与否）。
直接加权求和需要先做分数归一，而归一本身又是一层任意假设。RRF 只用**排名**：

.. math::

    score(d) = \\sum_{r \\in lists} \\frac{1}{k + rank_r(d)}

- 出现在多路召回前列的候选得分高（共识信号）；
- 只在某一路出现的候选也有基本盘（互补信号）；
- ``k=60``（配置 ``RAG_RRF_K``）压制头部排名的过大影响。

🔴 **确定性**（铁律 10）：RRF 是纯函数 —— 相同输入必然相同输出，
融合结果「为什么这个候选人排前面」可以被逐项复算，这是检索可解释性的地基。

词法打分为什么也在这里
====================
向量链路（H-19 闭环前）不可用时，兜底检索需要一个**确定性**的相关度信号。
词法重合率（字符 bigram Jaccard）粗糙但**可复算、可解释** —— 比让 LLM
自己说「这段相关」可靠得多（无答案兜底阈值判定依赖它，见
:meth:`app.core.threshold.ThresholdEngine.is_answerable`）。
"""

from __future__ import annotations

import re
from typing import Any

#: 默认 RRF 常数（配置 ``RAG_RRF_K`` 的缺省值，与需求 §10.2 一致）
DEFAULT_RRF_K = 60

_STRIP_RE = re.compile(r"[\s，,。；;：:？！?!、\"'（）()\[\]{}·…—\-]")


def _tokenize(text: str | None) -> set[str]:
    """字符 bigram 分词（中文无空格分隔，bigram 是最朴素的确定性切分）。"""
    if not text:
        return set()
    cleaned = _STRIP_RE.sub("", text)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def lexical_score(query: str | None, text: str | None) -> float:
    """确定性词法相关度：查询与文本的 bigram Jaccard 重合率（0~1）。

    🔴 只做**兜底信号**：它衡量「字面上像不像」，回答不了「能力画像像不像」
    （那是向量检索的职责）—— 与 :mod:`app.retrieval.keyword_provider` 的
    语义边界声明一致。
    """
    q = _tokenize(query)
    if not q:
        return 0.0
    t = _tokenize(text)
    if not t:
        return 0.0
    return round(len(q & t) / len(q | t), 4)


def rrf(
    lists: list[list[Any]],
    *,
    k: int = DEFAULT_RRF_K,
    key_of: Any = None,
) -> list[tuple[Any, float]]:
    """RRF 融合多路召回（**纯函数**）。

    :param lists: 每路已按相关度**降序**排好的候选列表；候选需可哈希
        （或传 ``key_of`` 给出去重键）。
    :param k: RRF 常数（越大头部排名的权重越平缓）。
    :param key_of: 候选 → 去重键（缺省用候选自身）。**取第一个出现的候选对象**为代表
        —— 同一候选在多路出现时，携带信息最全的那一路（通常排更前）胜出。
    :return: ``[(候选, 融合分), ...]`` 按融合分降序；同分按首次出现顺序稳定排序
        （Python ``sorted`` 的稳定性保证 —— 不引入随机性）。
    """
    if k <= 0:
        raise ValueError("RRF 常数 k 必须为正")
    key_fn = key_of or (lambda item: item)
    scores: dict[Any, float] = {}
    order: dict[Any, int] = {}
    rep: dict[Any, Any] = {}
    seq = 0
    for one_list in lists:
        for rank, item in enumerate(one_list, start=1):
            identity = key_fn(item)
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
            if identity not in order:
                order[identity] = seq
                seq += 1
                rep[identity] = item
    fused = sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return [(rep[identity], score) for identity, score in fused]


class HybridRetriever:
    """CL-3 的 ``HybridRetriever``：融合 + 收尾（rrf / finish）。

    ⚠️ 本类**不做 IO**（不调网关、不碰 Provider）—— 召回是调用方的事，
    这里只负责把「多路已召回的结果」变成「一个有序列表」。
    好处：全部逻辑可被 L1 用例钉死，不需要任何外部依赖。
    """

    def __init__(self, *, k: int = DEFAULT_RRF_K) -> None:
        self.k = int(k)

    def rrf(self, lists: list[list[Any]], *, key_of: Any = None) -> list[tuple[Any, float]]:
        return rrf(lists, k=self.k, key_of=key_of)

    def finish(self, candidates: list[tuple[Any, float]], *, top_n: int) -> list[tuple[Any, float]]:
        """收尾：截断到 ``top_n``（精排前的候选集或最终 Top-N）。"""
        return candidates[: max(0, int(top_n))]
