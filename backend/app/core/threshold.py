"""确定性阈值引擎（ThresholdEngine）—— 铁律 10 的核心落地之一。

🔴 为什么置信度判定不能交给模型
============================
「这份简历的字段抽取结果要不要送人工复核」是一个**业务判定**，
它决定了人工工作量与数据责任归属。若让 LLM 自己说「我觉得需要复核」：

- **不可复算**：同一份输入换一次模型调用可能给出不同结论；
- **不可解释**：线上出现「这份为什么没送复核」时，没有任何可核对的过程；
- **不可调优**：想收紧阈值只能改写 Prompt，而 Prompt 的改动会同时影响抽取质量。

故本模块用**纯函数 + 常量阈值**实现，输入相同则结论必然相同。

判定的三条规则（来自需求文档，非自创）
====================================
| # | 规则 | 依据 |
| :-: | --- | --- |
| 1 | **高影响字段**（姓名 / 手机 / 邮箱 / 学历 / 工作经历）置信度低于阈值 → **必须复核** | 这类字段错了会直接影响业务（联系不上人、学历判断错） |
| 2 | **低影响字段**低置信 → **按比例抽样复核**（默认 10%） | 需求 §10.1 约束³：低影响字段 10% 抽样复核 |
| 3 | 整体平均置信度低于阈值 → **全量复核** | 「整体都很差」说明这一份的解析质量不可信，逐字段抽样没有意义 |

⚠️ 抽样必须**确定性**（不能每次随机）：否则同一份简历在上传时抽中、重跑时不抽中，
人工复核队列会「抖动」，而复核员会认为系统在重复派活。
故用 ``sha256(tenant_id:业务键:字段名)`` 取模 —— 同一个业务对象每次都抽中同样的字段。

⚠️ 本类当前只实现「解析复核」这一组规则。CU-16（复核漏斗）、CU-19（查重阈值）
会在各自单元里**扩展本类**（新增方法），而不是各写一份阈值比较 ——
「阈值散落在 5 个文件里」正是本项目要避免的形态。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.config import settings

#: 高影响字段（置信度不足**必须**人工复核）。
#: ⚠️ 这份清单与需求文档 §3 的「关键字段」口径一致；新增业务字段时必须同步。
HIGH_IMPACT_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "phone",
        "email",
        "education_level",
        "work_experience",
    }
)

#: 整体平均置信度低于该值时全量复核（比单字段阈值宽松 —— 它衡量的是「整份质量」）
OVERALL_CONFIDENCE_FLOOR: float = 0.60


@dataclass(slots=True)
class ReviewDecision:
    """复核判定结果（可解释：把命中的规则原样带出来）。"""

    needed: bool
    #: 需要复核的字段（命中规则 1 或规则 2）
    fields: list[str] = field(default_factory=list)
    #: 命中的规则标识（``high_impact`` / ``sampled`` / ``overall_low``），供排障与统计
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"needed": self.needed, "fields": list(self.fields), "reasons": list(self.reasons)}


@dataclass(slots=True)
class DupDecision:
    """查重候选判定结果（CU-19 扩展，铁律 10）。"""

    is_dup: bool
    #: 最终相似度（精确键命中恒为 1.0）
    similarity: float
    #: 是否命中精确键（``phone_hash`` / ``id_card_hash``）—— 豁免相似度阈值
    exact: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "is_dup": self.is_dup,
            "similarity": self.similarity,
            "exact": self.exact,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class AskDecision:
    """RAG 问答兜底判定结果（CU-21 扩展，铁律 10）。"""

    #: True = 有依据，可送大模型；False = 兜底拒答（**不调用大模型**）
    answerable: bool
    #: 参与判定的最高相关度分
    best_score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"answerable": self.answerable, "best_score": self.best_score, "reasons": list(self.reasons)}


class ThresholdEngine:
    """确定性阈值判定（纯函数，无 IO、无状态）。"""

    @staticmethod
    def needs_manual_review(
        confidences: dict[str, float],
        *,
        sample_key: str,
        threshold: float | None = None,
        sample_rate: float | None = None,
        high_impact: frozenset[str] = HIGH_IMPACT_FIELDS,
    ) -> ReviewDecision:
        """判定是否需要人工复核。

        :param confidences: ``{字段名: 置信度(0~1)}``
        :param sample_key: 抽样用的稳定业务键（如 ``f"{tenant_id}:{resume_id}"``）。
            🔴 **不含随机数** —— 抽样必须可复现，否则复核队列会抖动。
        :param threshold: 单字段阈值，缺省取 ``settings.PARSE_CONFIDENCE_THRESHOLD``
        :param sample_rate: 低影响字段抽样率，缺省取 ``settings.PARSE_LOW_IMPACT_SAMPLE_RATE``

        :return: :class:`ReviewDecision`（``needed=False`` 时 ``fields`` 为空）
        """
        thr = float(settings.PARSE_CONFIDENCE_THRESHOLD if threshold is None else threshold)
        rate = float(
            settings.PARSE_LOW_IMPACT_SAMPLE_RATE if sample_rate is None else sample_rate
        )
        rate = max(0.0, min(1.0, rate))

        decision = ReviewDecision(needed=False)
        if not confidences:
            # 一个字段都没抽出来 —— 不是「不需要复核」，而是**必须复核**
            return ReviewDecision(needed=True, fields=[], reasons=["no_fields"])

        # ---- 规则 0（批次 21）：姓名缺失 → **文件级**复核 ----
        # 姓名是建档的**唯一必需项**（``parse_pipeline`` 的「无名不入库」）。
        # 抽不到姓名时整份无法入库：必须走文件级人工介入（补录姓名后可继续入库），
        # 而不是「建档成空名」或「标记为解析失败」——后者会让本该被受理的简历
        # 以「失败」出现在导入结果里（2026-09-24 真机：3 份文本型 PDF 全部如此）。
        # 与 ``name`` 置信度不足（规则 1，字段级）是两件事：这里**根本没有值**。
        if not confidences.get("name"):
            decision.needed = True
            decision.reasons.append("name_missing")

        # ---- 规则 3：整体平均过低 → 全量复核 ----
        overall = sum(float(v) for v in confidences.values()) / len(confidences)
        if overall < OVERALL_CONFIDENCE_FLOOR:
            decision.needed = True
            decision.reasons.append("overall_low")
            decision.fields = sorted(confidences)
            return decision

        # ---- 规则 1：高影响字段低于阈值 → 必须复核 ----
        for name in sorted(confidences):
            if name in high_impact and float(confidences[name]) < thr:
                decision.needed = True
                decision.reasons.append("high_impact")
                decision.fields.append(name)

        # ---- 规则 2：低影响字段按比例确定性抽样 ----
        if rate > 0:
            for name in sorted(confidences):
                if name in high_impact:
                    continue
                if float(confidences[name]) >= thr:
                    continue  # 高置信的低影响字段无需复核（抽样只针对**可疑**的）
                if ThresholdEngine._sampled(sample_key, name, rate):
                    decision.needed = True
                    decision.reasons.append("sampled")
                    decision.fields.append(name)

        # 去重（同一字段可能同时命中规则 1 与规则 3，但规则 3 已提前 return）
        decision.fields = sorted(dict.fromkeys(decision.fields))
        decision.reasons = sorted(dict.fromkeys(decision.reasons))
        return decision

    # ------------------------------------------------------------------
    @staticmethod
    def _sampled(sample_key: str, field_name: str, rate: float) -> bool:
        """确定性抽样：``sha256(key:field)`` 落进前 ``rate`` 比例即为抽中。

        ⚠️ 为什么不用 ``random``：随机抽样在**重跑**时会给出不同结果，
        导致「上一次抽中的字段这次没抽中」—— 复核队列看起来在随机增删。
        ``hashlib`` 的结果与进程、平台、时间都无关，天然可复现。
        """
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(f"{sample_key}:{field_name}".encode()).digest()
        # 取前 4 字节作无符号整数，映射到 [0, 1)
        bucket = int.from_bytes(digest[:4], "big") / 0x1_0000_0000
        return bucket < rate

    # ------------------------------------------------------------------
    @staticmethod
    def confidence_of(
        field_values: dict[str, object], *, default: float = 1.0
    ) -> dict[str, float]:
        """从「带 ``confidence`` 的抽取结果」里摘出 ``{字段: 置信度}``。

        约定抽取结果形如 ``{"name": {"value": "张三", "confidence": 0.93}, ...}``；
        没有 ``confidence`` 的字段按 ``default`` 处理（**按 1.0 而不是 0** ——
        0 会让每一个未标注置信度的字段都触发复核，把人工队列瞬间塞满）。
        """
        out: dict[str, float] = {}
        for name, item in field_values.items():
            if isinstance(item, dict) and "confidence" in item:
                try:
                    out[name] = float(item["confidence"])
                except (TypeError, ValueError):
                    out[name] = default
            else:
                out[name] = default
        return out

    # ------------------------------------------------------------------
    # CU-19 查重阈值（扩展本类，而非另写一份阈值比较 —— 见模块 docstring）
    # ------------------------------------------------------------------
    @staticmethod
    def is_dup_candidate(
        *,
        similarity: float,
        exact_key_match: bool = False,
        threshold: float | None = None,
    ) -> DupDecision:
        """判定「一对档案是否构成重复候选」（F-3.5）。

        :param similarity: 相似度（0~1）。精确键命中时为 1.0，否则为向量余弦相似度
        :param exact_key_match: 是否命中精确键（``phone_hash`` / ``id_card_hash`` 一致）。
            🔴 精确键命中**豁免**相似度阈值 —— 联系方式/证件相同即同一人，
            无需再要求向量相似度达标（否则会把「手机号完全一致」的一对挡在阈值外）。
        :param threshold: 相似度阈值，缺省取 ``settings.DEDUP_SIMILARITY_THRESHOLD``

        :return: :class:`DupDecision`
        """
        thr = float(settings.DEDUP_SIMILARITY_THRESHOLD if threshold is None else threshold)
        sim = round(min(max(float(similarity), 0.0), 1.0), 4)
        if exact_key_match:
            return DupDecision(is_dup=True, similarity=1.0, exact=True, reasons=["exact_key_match"])
        if sim >= thr:
            return DupDecision(is_dup=True, similarity=sim, exact=False, reasons=["similarity_threshold"])
        return DupDecision(is_dup=False, similarity=sim, exact=False, reasons=[])

    # ------------------------------------------------------------------
    # CU-21 RAG 问答兜底阈值（扩展本类，而非另写一份阈值比较 —— 见模块 docstring）
    # ------------------------------------------------------------------
    @staticmethod
    def is_answerable(
        *,
        best_score: float,
        threshold: float | None = None,
    ) -> AskDecision:
        """判定「检索依据是否足以送大模型作答」（F-3.7 红线 5：无答案兜底）。

        :param best_score: 全部检索片段中的最高相关度分（0~1）
        :param threshold: 无答案阈值，缺省取 ``settings.RAG_NO_ANSWER_THRESHOLD``（0.35）

        :return: :class:`AskDecision` —— ``answerable=False`` 时调用方**必须**发
            ``refusal(LOW_SIMILARITY)`` 且**不调用大模型**（``tokens.in == 0``，
            接口示例可直接验证）—— 阈值判定在**模型调用之前**，不是事后。
        """
        thr = float(settings.RAG_NO_ANSWER_THRESHOLD if threshold is None else threshold)
        score = round(min(max(float(best_score), 0.0), 1.0), 4)
        if score >= thr:
            return AskDecision(answerable=True, best_score=score, reasons=["score_above_threshold"])
        return AskDecision(answerable=False, best_score=score, reasons=["low_similarity"])
