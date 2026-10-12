"""N0 实测的结果模型与统一执行器（CU-27）。

为什么是**四态**而不是「通 / 不通」
----------------------------------
二态会把三件性质完全不同的事混成同一句「失败」，而它们的处置动作毫无交集：

====================  ==========================================================  ==========================
状态                  含义                                                        处置动作
====================  ==========================================================  ==========================
``ok``                依赖可用且达到基线                                          —
``degraded``          可用但**低于基线**（如 Embedding 吞吐过低）                 按需求 §6.1：**N0 就提出降级或追加算力**，不得拖到 N6
``blocked``           **前置未就绪**（客户端未装 / 服务未部署 / 密钥未配）        列入待办清单，由人工按 ``remediation`` 执行
``failed``            **服务在、但行为异常**（连得上却报错）                      需人看日志，属真问题
====================  ==========================================================  ==========================

``blocking`` 与状态**刻意解耦**
------------------------------
「是否阻塞后续阶段」是**需求给的属性**（如 Milvus 是 N4 的前置），不是探测结果。
两者分开存，才能分清：

- 「阻塞项被 ``blocked``」→ 必须立刻处理，否则 N4 开不了工；
- 「非阻塞项 ``failed``」→ 记一笔，不影响继续编码。

本模块**纯数据 + 一个薄执行器**，不含任何 I/O：单测可以在没有 MySQL / Redis /
Milvus 的机器上断言「注册表完整性、状态判定、报告渲染」。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ProbeStatus",
    "ProbePayload",
    "Probe",
    "ProbeSpec",
    "ProbeFn",
    "run_spec",
    "ProbeRunError",
]


class ProbeStatus(str, Enum):
    """探针四态（``str`` 混入以便直接进 JSON）。"""

    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    FAILED = "failed"

    @property
    def label(self) -> str:
        """报告里用的中文标签（带符号，便于在 Markdown 表格里一眼扫过）。"""
        return _STATUS_LABEL[self]

    @property
    def rank(self) -> int:
        """严重度排序用（越大越该先看）。"""
        return _STATUS_RANK[self]


_STATUS_LABEL: dict[ProbeStatus, str] = {
    ProbeStatus.OK: "✅ 就绪",
    ProbeStatus.DEGRADED: "🟡 降级",
    ProbeStatus.BLOCKED: "⛔ 待就绪",
    ProbeStatus.FAILED: "❌ 异常",
}

_STATUS_RANK: dict[ProbeStatus, int] = {
    ProbeStatus.OK: 0,
    ProbeStatus.DEGRADED: 1,
    ProbeStatus.BLOCKED: 2,
    ProbeStatus.FAILED: 3,
}


class ProbeRunError(RuntimeError):
    """探针执行器的兜底异常（正常路径不会抛出，仅用于失败自检）。"""


@dataclass(slots=True)
class ProbePayload:
    """探针的**返回载荷**（与 ``ProbeSpec`` 的静态元信息分开）。

    - ``detail``：一句话结论，直接进报告表格的「结论」列，**必须自解释**
      （例如「MySQL 8.0.27 · 库 talent_platform_v2 · 10 张表」，而不是「成功」）；
    - ``metrics``：结构化原始值，供报告「基线实测值」表按路径取值；
    - ``remediation``：**人工可执行的处置清单**（非 ``ok`` 时应当非空）。
    """

    status: ProbeStatus
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""


ProbeFn = Callable[[], ProbePayload]


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """探针的**静态声明**（不随环境变化，因此可在无依赖环境下断言）。"""

    key: str
    title: str
    group: str
    requirement_ref: str
    blocking: bool
    fn: ProbeFn

    def __post_init__(self) -> None:
        if not self.key or not self.key.isidentifier():
            raise ValueError(f"探针 key 必须是合法标识符：{self.key!r}")
        if not self.requirement_ref:
            raise ValueError(f"探针 {self.key} 必须标注需求出处（不得凭空造项）")


@dataclass(slots=True)
class Probe:
    """探针的**执行结果**（静态声明 + 本次运行的载荷）。"""

    key: str
    title: str
    group: str
    requirement_ref: str
    blocking: bool
    status: ProbeStatus
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""

    # ------------------------------------------------------------------
    # 判定辅助（报告渲染与退出码都靠这几个属性，避免各处重复写条件）
    # ------------------------------------------------------------------
    @property
    def healthy(self) -> bool:
        """是否「可用」（``ok`` / ``degraded`` 都算可用，只是后者需在 N0 提降级）。"""
        return self.status in (ProbeStatus.OK, ProbeStatus.DEGRADED)

    @property
    def needs_action(self) -> bool:
        """是否需要人工动作（非 ``ok`` 一律要处置）。"""
        return self.status is not ProbeStatus.OK

    @property
    def is_blocker(self) -> bool:
        """是否构成**阶段阻塞**：需求标为 blocking 且当前不可用。"""
        return self.blocking and not self.healthy

    @property
    def status_label(self) -> str:
        return self.status.label

    def metric(self, *path: str, default: Any = None) -> Any:
        """按键路径取值（``probe.metric("rtt_ms_avg")``）。

        单层取值即可 —— ``metrics`` 刻意保持扁平，避免报告渲染层依赖嵌套结构。
        """
        if not path:
            return default
        value: Any = self.metrics
        for part in path:
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


def run_spec(spec: ProbeSpec) -> Probe:
    """执行一个探针，**保证不抛异常**（异常统一降级为 ``failed``）。

    这是「探针永不抛异常」红线的唯一落地点：与其在 10 个探针里各写一遍
    ``try/except``（总有人会漏），不如在调用点统一兜住 —— 探针内部仍应自己
    处理**预期内**的失败（连不上、未配置），本函数只兜**预期外**的缺陷。
    """
    try:
        payload = spec.fn()
    except Exception as exc:  # noqa: BLE001 — 兜底：任何探针缺陷都不该毁掉整份报告
        payload = ProbePayload(
            status=ProbeStatus.FAILED,
            detail=f"探针自身抛异常：{type(exc).__name__}: {exc}",
            remediation=(
                "这是**脚本缺陷**（预期外异常），不是环境问题。"
                "请把完整堆栈反馈给开发者；环境侧的可用性结论请以其他探针为准。"
            ),
        )

    return Probe(
        key=spec.key,
        title=spec.title,
        group=spec.group,
        requirement_ref=spec.requirement_ref,
        blocking=spec.blocking,
        status=payload.status,
        detail=payload.detail,
        metrics=dict(payload.metrics),
        remediation=payload.remediation,
    )


def summarize(probes: Sequence[Probe]) -> dict[str, int]:
    """按状态计数（报告摘要用）。"""
    counts = {status.value: 0 for status in ProbeStatus}
    for probe in probes:
        counts[probe.status.value] += 1
    return counts


def exit_code(probes: Sequence[Probe]) -> int:
    """CLI 退出码（供编排/CI 判定）。

    - ``0``：无 ``failed``，且**没有阻塞项不可用** —— 可以继续推进；
    - ``2``：存在 ``failed`` 或阻塞项不可用 —— 需要人工处置后再跑。

    ⚠️ 非阻塞项的 ``blocked``（如未配置云通道）**不返回 2**：那不是缺陷，
       N0 的价值恰恰是把它列出来，而不是让脚本报错。
    """
    if any(probe.status is ProbeStatus.FAILED for probe in probes):
        return 2
    if any(probe.is_blocker for probe in probes):
        return 2
    return 0
