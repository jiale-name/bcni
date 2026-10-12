"""N0 指标基线台账（CU-27）。

**为什么单独一个文件**
--------------------
需求文档里有三类指标，判定逻辑完全不同，混在一起必然写歪：

====================  ============================================  ==================================
``scope``             含义                                          本脚本能否给出结论
====================  ============================================  ==================================
``probe``             N0 单点可测（Redis 吞吐 / Reranker 延迟…）      ✅ 能，直接填实测值
``load``              必须压测（P95 @100 万向量 / 200 并发…）          ❌ 不能，只登记口径与复测时点
``goldset``           必须金标准集（解析准确率 / Recall@10）           ❌ 不能，交由 CU-28 与 N6
====================  ============================================  ==================================

🔴 **纪律：本文件里的每一个数字都必须有文档出处**（``doc_ref``）。
   凡文档未给数值的，写「文档未给数值」而不是自己编一个 —— 编出来的阈值
   一旦进了报告，就会被当成承诺值用，这正是需求 §6.1 要防的事。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "MetricScope",
    "SCOPE_LABELS",
    "MetricTarget",
    "METRIC_TARGETS",
    "Deliverable",
    "N0_DELIVERABLES",
]

MetricScope = Literal["probe", "load", "goldset"]

SCOPE_LABELS: dict[str, str] = {
    "probe": "N0 单点可测",
    "load": "需 N4 / N6 压测",
    "goldset": "需金标准集（CU-28）",
}


@dataclass(frozen=True, slots=True)
class MetricTarget:
    """一条指标基线的「口径 + 目标 + 取值路径」。

    ``probe_key`` / ``metric_path`` 是**取实测值的机器可读坐标**：
    报告渲染时按坐标去对应探针的 ``metrics`` 里取值，取不到就写「—」。
    这样「指标表」与「探针表」不会各写一份、各自漂移。
    """

    code: str
    name: str
    target_desc: str
    doc_ref: str
    scope: MetricScope
    probe_key: str | None = None
    metric_path: tuple[str, ...] = ()
    measured_label: str = ""

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS[self.scope]


# ============================================================================
# 指标基线（逐条对齐需求文档，全部带出处）
# ============================================================================
METRIC_TARGETS: tuple[MetricTarget, ...] = (
    # ---- ① §6.1 基线压测的六项中，N0 可单点测到实测值的 4 项 ----
    MetricTarget(
        code="§6.1②",
        name="Redis 读写吞吐",
        target_desc="文档只要求「实测吞吐」并纳入基线报告，**未给具体目标值**",
        doc_ref="需求 §6.1 第 ①~⑥ 行 · 需求 §12.1 N0 ⑦②",
        scope="probe",
        probe_key="redis",
        metric_path=("ops_per_sec",),
        measured_label="SET+GET 往返折算（单连接串行，**非并发压测**）",
    ),
    MetricTarget(
        code="§6.1⑤",
        name="Reranker 单次精排延迟",
        target_desc="文档只要求「单次精排延迟」纳入基线，**未给具体目标值**",
        doc_ref="需求 §6.1⑤ · 需求 §12.1 N0 ④",
        scope="probe",
        probe_key="reranker",
        metric_path=("latency_ms",),
        measured_label="单次 /rerank 请求耗时（3 条候选文档）",
    ),
    MetricTarget(
        code="§6.1④",
        name="bge-m3 Embedding 吞吐",
        target_desc="文档要求「份/分钟」，**未给具体目标值**；本文档按「次/秒」记原始基线，"
        "「份/分钟」需乘 CL-3 分块方案的单元数/份",
        doc_ref="需求 §6.1④ · 需求 §12.1 N0 ④ · H-19 / D-19 / R-17",
        scope="probe",
        probe_key="embedding",
        metric_path=("calls_per_sec",),
        measured_label="单条文本 / 秒（逐轮换文本，避免 KV cache 虚高）",
    ),
    MetricTarget(
        code="§6.1⑥",
        name="云 GPU 通道网络往返延迟",
        target_desc="文档要求实测 RTT 并纳入基线，**未给具体目标值**",
        doc_ref="需求 §6.1⑥ · 需求 §12.1 N0 ⑩ · D-4",
        scope="probe",
        probe_key="cloud_channel",
        metric_path=("rtt_ms_avg",),
        measured_label="TCP 建连 RTT 均值（**非 HTTP 全链路**）",
    ),
    # ---- ② §6.1① Milvus：N0 只能验证可用性，规模结论必须等灌数据 ----
    MetricTarget(
        code="§6.1①",
        name="Milvus 100 万向量检索延迟与 QPS",
        target_desc="文档要求「100 万向量规模」下实测；N0 阶段**无存量数据**，"
        "故本脚本只能验证服务可用性与版本，规模结论留待 N4 灌数后复测",
        doc_ref="需求 §6.1① · 需求 §12.1 N0 ⑥",
        scope="load",
        probe_key="milvus",
        metric_path=("collection_count",),
        measured_label="N0 仅验证可用性（现有 collection 数量）",
    ),
    # ---- ③ 必须压测的四项（N0 不撒谎，只登记口径） ----
    MetricTarget(
        code="IDX-04",
        name="向量检索延迟",
        target_desc="**P95 ≤ 500ms** @ 100 万向量 / 200 并发（需求 §5 表 IDX-04 明确四要素）",
        doc_ref="需求 §5 IDX-04 · §6.2 · §12.3 验收",
        scope="load",
    ),
    MetricTarget(
        code="IDX-05 / NFR-2",
        name="普通接口延迟",
        target_desc="**P95 ≤ 1s**",
        doc_ref="需求 §5 IDX-05 · §4 NFR-2",
        scope="load",
    ),
    MetricTarget(
        code="IDX-11 / NFR-1",
        name="并发能力",
        target_desc="**200 并发下无错误**（实测口径）；2000 人为「在线」非「同时请求」（IDX-11 需澄清）",
        doc_ref="需求 §5 IDX-11 · §4 NFR-1 · Q-6",
        scope="load",
    ),
    MetricTarget(
        code="NFR-3 / F-2.7",
        name="LLM 首 token 延迟",
        target_desc="**首 token P95 ≤ 3s**（流式）",
        doc_ref="需求 §4 NFR-3 · §3.3 F-2.7",
        scope="load",
    ),
    MetricTarget(
        code="IDX-06 / NFR-2",
        name="复杂大数据查询",
        target_desc="**P95 ≤ 3s** @ 单表 1000 万行 / 返回 ≤1000 行",
        doc_ref="需求 §5 IDX-06 · §4 NFR-2",
        scope="load",
    ),
    MetricTarget(
        code="IDX-12 / NFR-2",
        name="页面首屏延迟",
        target_desc="**首屏 P95 ≤ 2s** @ 局域网（Lighthouse + 实测）",
        doc_ref="需求 §5 IDX-12 · §4 NFR-2",
        scope="load",
    ),
    # ---- ④ 依赖金标准集的两项（一期核心指标） ----
    # ⚠️ 这两项的 ``probe_key`` 刻意留空：它们**不是**本文档的探针能测的东西，
    #    由 ``scripts/goldset.py`` 产出标注模板、N6 阶段才评估。
    #    若随便挂一个不存在的 probe_key，报告会显示「本次未执行该探针」，
    #    读起来像「漏跑了」；留空才会如实显示为「仅登记口径」。
    MetricTarget(
        code="IDX-02",
        name="简历字段级准确率（一期核心）",
        target_desc="**≥ 95%**（图片 / 扫描件单列口径单独统计并报告）",
        doc_ref="需求 §5 IDX-02 · §3.3 F-3.2 · §6.2 · §12.3 验收",
        scope="goldset",
        measured_label="N0 只产出标注模板（CU-28），准确率待 CU-15 跑通后评估",
    ),
    MetricTarget(
        code="IDX-01",
        name="语义检索 Recall@10（一期核心）",
        target_desc="**≥ 85%**，基于 ≥50 条金标准查询集",
        doc_ref="需求 §5 IDX-01 · §3.3 F-3.6 · §6.2 · §12.3 验收",
        scope="goldset",
        measured_label="N0 只产出查询集模板（CU-28），Recall 待 CU-20 跑通后评估",
    ),
)


# ============================================================================
# N0 交付物清单（需求 §12.1 表格末尾的「交付物」行，共 11 项）
# ============================================================================
@dataclass(frozen=True, slots=True)
class Deliverable:
    """N0 交付物的一项。

    ``owner`` 只有三类：``本脚本`` / ``人工`` / ``甲方`` —— 需求 §12.1 已明确
    「关键路径上甲方决策延迟会 1:1 平移总工期」，所以**把责任方摆到明面上**
    比默默漏掉更有用。
    """

    name: str
    owner: Literal["本脚本", "人工", "甲方"]
    covered: bool
    note: str = ""


N0_DELIVERABLES: tuple[Deliverable, ...] = (
    Deliverable("环境验证报告", "本脚本", True, "探针表即环境验证结论（MySQL/Redis/Milvus/MinIO/Dify）"),
    Deliverable("模型就绪验证报告", "本脚本", True, "bge-m3 维度与吞吐、Reranker 延迟、Ollama 模型在册"),
    Deliverable("基线压测报告", "本脚本", True, "§6.1 六项中 4 项给出实测值；另 2 项注明复测时点"),
    Deliverable("OCR 选型验证报告", "人工", False, "本脚本只给「依赖是否完整」的清单，选型结论与信创环境实测需人工（§12.1⑧ / R-16）"),
    Deliverable("Milvus collection 规划", "人工", False, "本脚本给可用性与版本，collection/索引/度量方式需人工定稿（§12.1⑥）"),
    Deliverable(
        "金标准集标注启动确认",
        "甲方",
        False,
        "CU-28 提供标注模板与质检脚本；**标注本身必须由甲方 HR/业务专家完成**（D-11）",
    ),
    Deliverable("国产化清单或书面豁免", "甲方", False, "Q-3 未回；D-18 一期仅承诺国产 OS + CPU（H-18 / R-16）"),
    Deliverable("需求澄清纪要（档案模块矛盾结论）", "甲方", False, "M-2 无人化 / M-3 指标口径 / M-4 向量规模"),
    Deliverable("指标应答口径表（签字版）", "甲方", False, "**N0 必签**；未签则 IDX-04/05/11 无判定依据"),
    Deliverable("数据出网边界协议", "甲方", False, "决定 D-4 混合路由的可用形态（ADR-4 / D-15）"),
    Deliverable("技术偏离申报表", "人工", False, "投标有效性相关（§6.5），不编码但必须出"),
)


def targets_for(scope: MetricScope) -> tuple[MetricTarget, ...]:
    """按范围筛选（测试与报告都用它，避免各处重复写过滤条件）。"""
    return tuple(t for t in METRIC_TARGETS if t.scope == scope)


def deliverable_summary() -> dict[str, int]:
    """交付物按责任方计数（报告摘要用）。"""
    counts = {"本脚本": 0, "人工": 0, "甲方": 0}
    for item in N0_DELIVERABLES:
        counts[item.owner] += 1
    return counts
