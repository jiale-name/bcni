"""N0 报告渲染（CU-27）：Markdown + JSON。

报告是 N0 的**唯一交付形态**（需求 §12.1 交付物：环境验证报告 / 模型就绪验证报告 /
基线压测报告），所以渲染层承担三件不能省的事：

1. **把结论前置** —— 开头就是「哪些阻塞、哪些降级、哪些只是待办」，而不是让人
   自己去扫 10 行探针结果；
2. **把口径写在数字旁边** —— 「单连接串行」「TCP 建连」「逐轮换文本」这类限定词
   必须与数值同屏，否则数据一旦被拷出去就会失真；
3. **把「脚本做不到的」显式列出** —— 用 ``scope`` 区分可测 / 需压测 / 需金标准集，
   并给出 11 项 N0 交付物里**哪些不由本脚本负责**。

🔴 脱敏红线：``env`` 元信息只输出**主机、端口、库名、模型名与「凭据是否已配置」的布尔值**，
   ❌ 绝不输出密码 / API Key / AES 密钥（报告会被提交进仓库与邮件）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import settings
from scripts.n0.probes import GROUPS
from scripts.n0.result import Probe, ProbeStatus, summarize
from scripts.n0.targets import (
    METRIC_TARGETS,
    N0_DELIVERABLES,
    MetricTarget,
    deliverable_summary,
)

__all__ = [
    "ReportContext",
    "build_context",
    "render_markdown",
    "render_json",
    "SCRIPT_VERSION",
]

SCRIPT_VERSION = "v1.0"


@dataclass(slots=True)
class ReportContext:
    """一次 N0 实测的完整上下文。"""

    generated_at: datetime
    probes: list[Probe]
    env: dict[str, str] = field(default_factory=dict)
    script_version: str = SCRIPT_VERSION

    @property
    def blockers(self) -> list[Probe]:
        """阻塞后续阶段且当前不可用的项（最需要立刻处理）。"""
        return [p for p in self.probes if p.is_blocker]

    @property
    def degradations(self) -> list[Probe]:
        """可用但低于基线的项（需求 §6.1：N0 就要提降级或追加算力）。"""
        return [p for p in self.probes if p.status is ProbeStatus.DEGRADED]

    @property
    def pending(self) -> list[Probe]:
        """非阻塞的待就绪项（不影响开工，但要在 N0 内闭环）。"""
        return [p for p in self.probes if p.status is ProbeStatus.BLOCKED and not p.blocking]

    @property
    def failures(self) -> list[Probe]:
        return [p for p in self.probes if p.status is ProbeStatus.FAILED]


def collect_env_snapshot() -> dict[str, str]:
    """收集**可公开**的环境快照（含凭据的只输出「已配置 / 未配置」）。"""
    return {
        "APP_ENV": settings.APP_ENV,
        "APP_VERSION": settings.APP_VERSION,
        "API_V1_PREFIX": settings.API_V1_PREFIX,
        "MYSQL": f"{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}",
        "MYSQL_凭据": "已配置" if settings.MYSQL_PASSWORD else "未配置（连接会失败）",
        "REDIS": f"{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        f"（protocol={settings.REDIS_PROTOCOL}）",
        "REDIS_凭据": "已配置" if settings.REDIS_PASSWORD else "未配置（若服务端未设密码则正常）",
        "MILVUS": f"{settings.MILVUS_HOST}:{settings.MILVUS_PORT}/{settings.MILVUS_DATABASE}",
        "MINIO": settings.MINIO_ENDPOINT,
        "MINIO_凭据": "已配置" if settings.MINIO_ACCESS_KEY and settings.MINIO_SECRET_KEY else "未配置",
        "OLLAMA_BASE_URL": settings.OLLAMA_BASE_URL,
        "OLLAMA_MODEL_PARSE": settings.OLLAMA_MODEL_PARSE,
        "OLLAMA_MODEL_REASON": settings.OLLAMA_MODEL_REASON,
        "EMBEDDING_MODEL": f"{settings.EMBEDDING_MODEL}（期望维度 {settings.EMBEDDING_DIM}）",
        "RERANKER": f"{settings.RERANKER_MODEL} @ {settings.RERANKER_BASE_URL}",
        "DIFY_BASE_URL": settings.DIFY_BASE_URL,
        "DIFY_API_KEY": "已配置" if settings.DIFY_API_KEY else "未配置",
        "CLOUD_LLM_BASE_URL": settings.CLOUD_LLM_BASE_URL or "未配置（一期默认全本地）",
        "AES_KEY": "已配置" if settings.AES_KEY else "未配置（N2 加密 PII 前必须配置）",
        "CONTACT_HASH_SALT": (
            "已配置" if getattr(settings, "CONTACT_HASH_SALT", "") else "未配置（金标准集联系方式比对不可用）"
        ),
    }


def build_context(
    probes: list[Probe],
    generated_at: datetime | None = None,
    env: dict[str, str] | None = None,
) -> ReportContext:
    return ReportContext(
        generated_at=generated_at or datetime.now(),
        probes=list(probes),
        env=env if env is not None else collect_env_snapshot(),
    )


# ============================================================================
# Markdown
# ============================================================================
def _fmt_value(value: Any) -> str:
    """把 metrics 里的原始值格式化成表格里的短文本。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if abs(value) >= 1:
            return f"{value:,.2f}"
        return f"{value:.4f}"
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value) if value else "（空）"
    return str(value)


def _measured(probe_map: dict[str, Probe], target: MetricTarget) -> str:
    """按坐标取实测值（取不到就写「—」，**绝不臆造**）。"""
    if not target.probe_key:
        return "—"
    probe = probe_map.get(target.probe_key)
    if probe is None:
        return "—（本次未执行该探针）"
    if not target.metric_path:
        return "—（仅登记口径）"
    value = probe.metric(*target.metric_path)
    if value is None:
        return "—（探针未产出该指标）"
    text = _fmt_value(value)
    if target.measured_label:
        return f"{text}（{target.measured_label}）"
    return text


def render_markdown(ctx: ReportContext) -> str:
    """渲染完整 Markdown 报告。"""
    stamp = ctx.generated_at.strftime("%Y-%m-%d %H:%M:%S")
    counts = summarize(ctx.probes)
    probe_map = {p.key: p for p in ctx.probes}
    lines: list[str] = []

    # ---- 头部 ----
    lines += [
        "# N0 基线实测与模型就绪验证报告",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        "| 文档性质 | **自动生成**（`backend/scripts/n0_probe.py`）· N0 阶段交付物 |",
        f"| 生成时间 | {stamp} |",
        f"| 脚本版本 | {ctx.script_version} |",
        "| 上游依据 | 需求分析文档 v1.3 §12.1 N0 · §6.1 基线压测先行原则 · §5 指标表（IDX-01~12）|",
        f"| 探针覆盖 | 执行 {len(ctx.probes)} 项（注册表共 {len(GROUPS)} 组）|",
        "",
        "> 🔴 **本报告只写实测到的事实**。凡需求文档未给出目标值的项，本文档不补阈值；"
        "凡 N0 阶段测不了的项（需压测 / 需金标准集），本文档**只登记口径与复测时点**。",
        "",
        "---",
        "",
        "## 零、结论摘要",
        "",
        "| 状态 | 数量 | 含义 |",
        "| --- | :-: | --- |",
        f"| ✅ 就绪 | {counts['ok']} | 可用且达到基线 |",
        f"| 🟡 降级 | {counts['degraded']} | 可用但低于基线 —— **N0 就要提降级或追加算力**（§6.1）|",
        f"| ⛔ 待就绪 | {counts['blocked']} | 前置未就绪（未装 / 未部署 / 未配），列入待办 |",
        f"| ❌ 异常 | {counts['failed']} | 服务在但行为异常，需看日志 |",
        "",
    ]

    if ctx.blockers:
        lines.append("**🔴 阻塞后续阶段（必须处理，否则后续批次开不了工）**")
        lines.append("")
        for probe in ctx.blockers:
            lines.append(f"- `{probe.key}` {probe.title} —— {probe.detail}")
        lines.append("")
    else:
        lines += ["**✅ 无阻塞项** —— 所有标记为「阻塞后续阶段」的探针均可用。", ""]

    if ctx.degradations:
        lines.append("**🟡 低于基线（按 §6.1，须在本阶段提出降级或追加算力，不得拖到 N6）**")
        lines.append("")
        for probe in ctx.degradations:
            lines.append(f"- `{probe.key}` {probe.title} —— {probe.detail}")
        lines.append("")

    if ctx.pending:
        lines.append("**⛔ 非阻塞待办（不挡开工，应在 N0 内闭环）**")
        lines.append("")
        for probe in ctx.pending:
            lines.append(f"- `{probe.key}` {probe.title} —— {probe.detail}")
        lines.append("")

    if ctx.failures:
        lines.append("**❌ 异常（需人工看日志）**")
        lines.append("")
        for probe in ctx.failures:
            lines.append(f"- `{probe.key}` {probe.title} —— {probe.detail}")
        lines.append("")

    lines += ["---", "", "## 一、环境与模型就绪矩阵", ""]

    for group_key, group_title in GROUPS.items():
        group_probes = [p for p in ctx.probes if p.group == group_key]
        if not group_probes:
            continue
        lines += [f"### {group_key}. {group_title}", ""]
        lines += [
            "| 探针 | 状态 | 阻塞 | 需求出处 | 结论 |",
            "| --- | :-: | :-: | --- | --- |",
        ]
        for probe in group_probes:
            lines.append(
                f"| `{probe.key}` {probe.title} | {probe.status_label} | "
                f"{'🔴 是' if probe.blocking else '否'} | {probe.requirement_ref} | {probe.detail} |"
            )
        lines.append("")

    lines += ["---", "", "## 二、基线实测值（§6.1 六项 + §5 指标表）", ""]
    lines += [
        "| 指标 | 目标口径 | 实测 | 范围 | 出处 |",
        "| --- | --- | --- | :-: | --- |",
    ]
    for target in METRIC_TARGETS:
        lines.append(
            f"| **{target.code}** {target.name} | {target.target_desc} | "
            f"{_measured(probe_map, target)} | {target.scope_label} | {target.doc_ref} |"
        )
    lines += [
        "",
        "> **「范围」列怎么读**：`N0 单点可测` = 本次已给出实测值；`需 N4 / N6 压测` = "
        "必须按「数据规模 + 并发条件 + 统计分位 + 验证方法」四要素压测（§7 原则），"
        "N0 不得用单点探测结果回答；`需金标准集` = 交由 CU-28 标注、N6 评估。",
        "",
        "---",
        "",
        "## 三、需人工执行的处置清单",
        "",
    ]

    actionable = [p for p in ctx.probes if p.remediation]
    if not actionable:
        lines += ["_无 —— 全部探针均无需处置。_", ""]
    else:
        ordered = sorted(actionable, key=lambda p: (not p.is_blocker, -p.status.rank))
        for index, probe in enumerate(ordered, start=1):
            tag = "🔴 阻塞项" if probe.is_blocker else ("🟡 降级" if probe.status is ProbeStatus.DEGRADED else "⚪ 待办")
            lines += [
                f"**{index}. `{probe.key}` {probe.title}**（{tag}）",
                "",
                f"- 现状：{probe.detail}",
                f"- 处置：{probe.remediation}",
                "",
            ]

    lines += ["---", "", "## 四、N0 交付物对照（需求 §12.1 表格末尾清单，共 11 项）", ""]
    summary = deliverable_summary()
    lines += [
        f"本脚本覆盖 **{sum(1 for d in N0_DELIVERABLES if d.covered)}** 项；"
        f"人工 {summary['人工']} 项；甲方 {summary['甲方']} 项。",
        "",
        "| 交付物 | 责任方 | 本脚本是否覆盖 | 说明 |",
        "| --- | :-: | :-: | --- |",
    ]
    for item in N0_DELIVERABLES:
        mark = "✅" if item.covered else "—"
        lines.append(f"| {item.name} | {item.owner} | {mark} | {item.note} |")

    lines += [
        "",
        "> 🔴 表中「甲方」项是**关键路径上会 1:1 平移总工期**的动作（需求 §12.1 风险行）："
        "尤其「指标应答口径表（签字版）」未签之前，IDX-04/05/11 没有判定依据；"
        "「金标准集标注启动」必须在本阶段确认责任方与投入安排（D-11），否则 N6 硬阻塞。",
        "",
        "---",
        "",
        "## 五、口径与局限（读数字前必看）",
        "",
        "1. **单点探测 ≠ 压测结论**。本报告的 Redis 吞吐是**单连接串行**、"
        "Reranker 延迟是**单次调用**、云通道是 **TCP 建连 RTT**（不含 TLS / HTTP / 排队）。"
        "它们只用于建立基线量级，不能替代 IDX-04/05/11 的压测。",
        "2. **Embedding 吞吐的「份/分钟」需换算**。报告给的是「单条文本 / 秒」，"
        "乘以 CL-3 分块方案的「单元数 / 份」才是需求 §6.1④ 的「份/分钟」；"
        "分块方案定稿后请在本报告追加一行换算结果。",
        "3. **同一次运行内的数值不可横向比较**。首次调用含模型加载（bge-m3 在 CPU 上尤甚），"
        "本脚本通过逐轮换文本 + 多次采样尽量摊平；若数值明显异常，请加 `--embedding-rounds` 重跑。",
        "4. **本报告不含任何凭据**。凭据只输出「已配置 / 未配置」；如发现报告中出现密钥，"
        "属渲染层缺陷，请立即反馈。",
        "",
        "---",
        "",
        "## 六、下一步",
        "",
        "1. 按「三、需人工执行的处置清单」逐项闭环 🔴 阻塞项；",
        "2. 用本报告的实测值**反推**可承诺指标，与需求 §5 指标表逐条对照，产出「指标承诺表（修订版）」"
        "（§6.1 第二步：先测出能力，再承诺指标）；",
        "3. 同步启动 **CU-28 金标准集标注**（`backend/scripts/goldset.py`）—— 它以人天计，"
        "是 N6 的硬前置（需求 §12.1 前置强依赖）；",
        "4. 全部就绪后重跑本脚本，直至退出码为 0。",
        "",
        "---",
        "",
        "## 附录 A：环境元信息（脱敏）",
        "",
        "| 配置项 | 取值 |",
        "| --- | --- |",
    ]
    for key, value in ctx.env.items():
        lines.append(f"| `{key}` | {value} |")

    lines += [
        "",
        "## 附录 B：原始探针指标（机器可读）",
        "",
        "> 完整结构化数据见同目录的 `.json` 文件；此处仅列非空 ``metrics``。",
        "",
    ]
    for probe in ctx.probes:
        if not probe.metrics:
            continue
        lines += [f"**`{probe.key}`**", "", "```json", json.dumps(probe.metrics, ensure_ascii=False, indent=2), "```", ""]

    lines += [
        "---",
        "",
        f"_本报告由 `backend/scripts/n0_probe.py`（{ctx.script_version}）于 {stamp} 自动生成，"
        "重跑即可刷新。_",
        "",
    ]
    return "\n".join(lines)


# ============================================================================
# JSON
# ============================================================================
def render_json(ctx: ReportContext) -> dict[str, Any]:
    """渲染机器可读的结构化结果（供后续自动比对 / 归档 / 图表化）。"""
    probe_map = {p.key: p for p in ctx.probes}
    return {
        "script_version": ctx.script_version,
        "generated_at": ctx.generated_at.isoformat(),
        "summary": summarize(ctx.probes),
        "blockers": [p.key for p in ctx.blockers],
        "env": ctx.env,
        "probes": [
            {
                "key": p.key,
                "title": p.title,
                "group": p.group,
                "requirement_ref": p.requirement_ref,
                "blocking": p.blocking,
                "status": p.status.value,
                "healthy": p.healthy,
                "detail": p.detail,
                "metrics": p.metrics,
                "remediation": p.remediation,
            }
            for p in ctx.probes
        ],
        "metrics": [
            {
                "code": t.code,
                "name": t.name,
                "target_desc": t.target_desc,
                "doc_ref": t.doc_ref,
                "scope": t.scope,
                "measured": _measured(probe_map, t),
                "raw": (
                    probe_map[t.probe_key].metric(*t.metric_path)
                    if t.probe_key and t.probe_key in probe_map and t.metric_path
                    else None
                ),
            }
            for t in METRIC_TARGETS
        ],
        "deliverables": [
            {"name": d.name, "owner": d.owner, "covered": d.covered, "note": d.note}
            for d in N0_DELIVERABLES
        ],
    }
