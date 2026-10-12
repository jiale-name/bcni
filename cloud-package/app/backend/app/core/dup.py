"""确定性查重比对器（DupScorer）—— 铁律 10 的确定性组件之一。

为什么「是不是重复」不能交给 LLM
================================
同 :class:`~app.core.threshold.ThresholdEngine` 的理由：「这份简历和那份是不是同一个人」，
直接决定**数据是否会被合并**（不可逆程度极高、涉及 PII 归属）。
若让模型来判，同一次输入换一次调用可能给出不同结论，且无法解释、无法复核。

本模块只做**确定性**比对：
1. **精确键**：``phone_hash`` / ``id_card_hash`` 一致 —— 同一联系方式 / 同一证件，
   这是「同一个人」的最强信号（哈希由 :func:`~app.core.crypto.hash_contact` 产出，
   同一归一化 → 同一哈希）；
2. **字段级**：``name`` / ``school`` / ``major`` 归一化后精确相等 —— 提供
   ``match_fields`` 供并排对比展示，让 HR **看到**「哪里像」再决定。

向量相似度是**测量值**（由检索提供方对向量算余弦得出），由调用方作为入参传入，
与字段比对结果一起交给 :meth:`ThresholdEngine.is_dup_candidate` 做最终判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 精确比对键（TBL-11 的不可逆哈希列）。二者任一相等即「精确重复」。
EXACT_KEY_FIELDS: tuple[str, ...] = ("phone_hash", "id_card_hash")

#: 字段级比对的字段（归一化后精确相等）。用于并排对比展示「像在哪」。
FIELD_MATCH_COLUMNS: tuple[str, ...] = ("name", "school", "major")

#: 参与合并的主档字段（API-59 的 ``field_strategy`` 可覆盖的键）。
#: 🔴 **不含**联系方式（``phone_hash`` / ``id_card_hash`` / ``email_enc`` 等）——
#: 它们是查重的**比对键**且带唯一约束，合并时把从档的联系方式写入主档可能撞第三份档案
#: 的唯一键；联系方式随其原档案保留，主档为空才允许显式 ``slave`` 取用（调用方自担）。
MERGEABLE_FIELDS: tuple[str, ...] = (
    "name",
    "gender",
    "birth_date",
    "education_level",
    "school",
    "major",
    "work_years",
)


@dataclass(frozen=True, slots=True)
class DupScore:
    """一次确定性比对结果。"""

    #: 相似度（0~1）。精确键命中恒为 1.0；否则取传入的向量相似度（无则为 0.0）
    similarity: float
    #: 命中的比对字段（``phone_hash`` / ``id_card_hash`` / ``name`` / ``school`` / ``major``）
    match_fields: list[str] = field(default_factory=list)
    #: 是否命中精确键（用于阈值引擎给「精确重复」豁免相似度阈值）
    exact: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "similarity": self.similarity,
            "match_fields": list(self.match_fields),
            "exact": self.exact,
        }


class DupScorer:
    """确定性查重比对（纯函数，无 IO、无状态）。"""

    @staticmethod
    def exact_key_match(a: Any, b: Any) -> list[str]:
        """返回命中的精确键字段名（``phone_hash`` / ``id_card_hash``）。

        任一哈希相等即认为「同一人」的强信号。⚠️ 空哈希不参与比较 ——
        两条都为 ``NULL`` 的 ``phone_hash`` **不算**命中（否则「都没填手机号」的人
        会被误判成重复）。
        """
        matched: list[str] = []
        for key in EXACT_KEY_FIELDS:
            va = getattr(a, key, None)
            vb = getattr(b, key, None)
            if va and vb and va == vb:
                matched.append(key)
        return matched

    @staticmethod
    def field_matches(a: Any, b: Any) -> list[str]:
        """返回归一化后精确相等的字段名（``name`` / ``school`` / ``major``）。

        归一化：去首尾空白 + 大小写折叠（英文）。中文名不做繁简转换（无依赖）。
        """
        matched: list[str] = []
        for key in FIELD_MATCH_COLUMNS:
            va = DupScorer._normalize(getattr(a, key, None))
            vb = DupScorer._normalize(getattr(b, key, None))
            if va and va == vb:
                matched.append(key)
        return matched

    @staticmethod
    def score(a: Any, b: Any, *, vector_similarity: float | None = None) -> DupScore:
        """产出一份确定性比对结果。

        :param vector_similarity: 调用方经检索提供方测得的余弦相似度（0~1）；
            ``None`` 表示「本环境无向量检索」（降级），相似度退回字段比对语义。
        """
        exact = DupScorer.exact_key_match(a, b)
        if exact:
            return DupScore(similarity=1.0, match_fields=exact, exact=True)
        fields = DupScorer.field_matches(a, b)
        if vector_similarity is not None:
            sim = round(min(max(float(vector_similarity), 0.0), 1.0), 4)
            return DupScore(similarity=sim, match_fields=fields, exact=False)
        # 无向量：以字段命中数给出一个确定性、保守的低分（不足以单独触发阈值判定）
        if fields:
            # name 命中权重最高；school/major 命中为辅。分数刻意压低 ——
            # 「名字像」不等于「同一个人」，绝不与向量相似度混同。
            sim = 0.6 if "name" in fields else 0.3
            return DupScore(similarity=sim, match_fields=fields, exact=False)
        return DupScore(similarity=0.0, match_fields=[], exact=False)

    @staticmethod
    def _normalize(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().casefold()

    @staticmethod
    def resolve_field(master_value: Any, slave_value: Any, strategy: str | None) -> Any:
        """合并时**单字段**取值判定（API-59 ``field_strategy`` 的确定性规则）。

        - ``"master"`` → 恒取主档；
        - ``"slave"`` → 恒取从档；
        - ``None``（未指定）→ **非空优先**：主档非空取主档，主档为空才取从档。

        ⚠️ 「高版本优先」依赖字段级版本号（``talent_base.version_no`` 是**记录级**，
        不是字段级），本批未建模 —— 未指定字段一律走「非空优先」，已登记遗留。
        """
        if strategy == "master":
            return master_value
        if strategy == "slave":
            return slave_value
        # 非空优先
        if master_value not in (None, ""):
            return master_value
        return slave_value
