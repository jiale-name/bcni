"""金标准集质检脚本（CU-28）。

这是需求 §12.5 明确要求的「**乙方提供……质检脚本**」的落地物。

为什么质检必须自动化
--------------------
200 份 × 13 字段是人工产出，**人一定会犯错**，而且错法很有规律：

- 漏填（以为填了 / 跳过了）；
- 该填 ``缺失`` 的格子留空 —— 与「还没标」混在一起，事后无法区分；
- 查询集引用的 ``resume_id`` 打错一位（``RS-007`` vs ``RS-0007``）；
- 学历填了字典外的自由文本（``本科`` / ``大学本科`` / ``Bachelor``），
  评估时会被归一化成同一个码，但归一规则必须**在标注现场**就发现，
  而不是等 200 份标完、N6 评估跑出 78% 准确率时才回头查。

所以本脚本的定位是「**标注现场的护栏**」：每标 50 份跑一次，把口径错误就地拦住。

设计约束
--------
1. **不抛异常** —— 文件缺失、列缺失、编码错误全部转成 ``Issue``，
   让报告本身成为排障材料（质检脚本自己崩掉，比发现标注有问题更糟）。
2. **只读** —— 质检**绝不修改**标注文件（人工资产），要修由人或 ``build --force`` 重来。
3. **不猜** —— 填充率、比例这类指标只报告数值与阈值对照，不替标注人判断内容对错
   （内容对错属 N6 的金标准评估，不属质检）。
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scripts.goldset.build import QUERY_CSV_NAME, RESUME_CSV_NAME
from scripts.goldset.schema import (
    ANNOTATION_STATUS,
    CORE_FIELD_COLUMNS,
    FILE_TYPES,
    IMAGE_FILE_TYPES,
    LIST_SEPARATOR,
    MISSING_MARKER,
    QUERY_COLUMNS,
    QUERY_INTENTS,
    QUERY_MIN_COUNT,
    RESUME_COLUMNS,
    RESUME_MIN_COUNT,
)

__all__ = [
    "Issue",
    "ValidationReport",
    "read_csv_rows",
    "validate_resume_rows",
    "validate_query_rows",
    "run_validate",
]

IssueLevel = Literal["error", "warn", "info"]

#: 单行 status=done 但字段留空时，报告里最多列几个具体位置（避免报告被 500 行淹没）
MAX_DETAIL_SAMPLES = 8

#: 字段填充率低于此比例时告警（在「已完成」的行上统计）
FILL_RATE_WARN_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class Issue:
    """一条质检结论。"""

    level: IssueLevel
    code: str
    message: str
    where: str = ""

    @property
    def icon(self) -> str:
        return {"error": "❌", "warn": "⚠️", "info": "ℹ️"}[self.level]


@dataclass
class ValidationReport:
    """质检报告（同时服务控制台与 Markdown 报告）。"""

    resume_path: Path
    query_path: Path
    resume_total: int = 0
    query_total: int = 0
    resume_status: dict[str, int] = field(default_factory=dict)
    query_status: dict[str, int] = field(default_factory=dict)
    file_type_counts: dict[str, int] = field(default_factory=dict)
    field_fill: dict[str, float] = field(default_factory=dict)
    missing_ratio: dict[str, float] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warn"]

    @property
    def passed(self) -> bool:
        """是否通过质检（只看 ``error``；``warn`` 不阻塞，但应逐条确认）。"""
        return not self.errors

    @property
    def image_total(self) -> int:
        return sum(self.file_type_counts.get(t, 0) for t in IMAGE_FILE_TYPES)

    @property
    def resume_done(self) -> int:
        return self.resume_status.get("done", 0) + self.resume_status.get("reviewed", 0)

    @property
    def query_done(self) -> int:
        return self.query_status.get("done", 0) + self.query_status.get("reviewed", 0)

    # ------------------------------------------------------------------
    def summary_line(self) -> str:
        return (
            f"简历 {self.resume_total} 份（已完成 {self.resume_done} · 图片件 {self.image_total}）"
            f" · 查询 {self.query_total} 条（已完成 {self.query_done}）"
            f" · ❌ {len(self.errors)} · ⚠️ {len(self.warnings)}"
        )

    def to_markdown(self) -> str:
        lines = [
            "# 金标准集质检报告",
            "",
            f"- 简历标注集：`{self.resume_path}`",
            f"- 查询-人才集：`{self.query_path}`",
            "",
            "## 一、规模与进度",
            "",
            "| 项目 | 当前 | 需求下限 | 判定 |",
            "| --- | :-: | :-: | :-: |",
            f"| 简历标注集 | {self.resume_total} | ≥{RESUME_MIN_COUNT} | "
            f"{'✅' if self.resume_total >= RESUME_MIN_COUNT else '❌'} |",
            f"| 查询-人才集 | {self.query_total} | ≥{QUERY_MIN_COUNT} | "
            f"{'✅' if self.query_total >= QUERY_MIN_COUNT else '❌'} |",
            f"| 其中图片 / 扫描件 | {self.image_total} | 需 >0（IDX-02 单列口径） | "
            f"{'✅' if self.image_total else '⚠️'} |",
            "",
            f"- 简历标注完成（`done` + `reviewed`）：**{self.resume_done} / {self.resume_total}**",
            f"- 查询标注完成（`done` + `reviewed`）：**{self.query_done} / {self.query_total}**",
            "",
            "### 标注状态分布",
            "",
            "| 状态 | 简历 | 查询 |",
            "| --- | :-: | :-: |",
        ]
        for key in ANNOTATION_STATUS:
            lines.append(
                f"| `{key}` {ANNOTATION_STATUS[key]} | {self.resume_status.get(key, 0)} | "
                f"{self.query_status.get(key, 0)} |"
            )

        lines += ["", "### 文件类型分布", "", "| 类型 | 数量 |", "| --- | :-: |"]
        for key, label in FILE_TYPES.items():
            count = self.file_type_counts.get(key, 0)
            if count:
                lines.append(f"| `{key}` {label} | {count} |")
        lines.append("")

        lines += ["", "## 二、字段填充率", "", "> 填充率 = 非空格子 / 总行数（含填 `缺失` 的格子）。", ""]
        lines += ["| 字段 | 填充率 | 其中标 `缺失` |", "| --- | :-: | :-: |"]
        for column in CORE_FIELD_COLUMNS:
            fill = self.field_fill.get(column, 0.0)
            missing = self.missing_ratio.get(column, 0.0)
            lines.append(f"| `{column}` | {fill:.1%} | {missing:.1%} |")

        lines += ["", "## 三、质检结论", ""]
        if not self.issues:
            lines += ["✅ 未发现问题。", ""]
        else:
            for level, title in (("error", "❌ 错误（必须修）"), ("warn", "⚠️ 告警（建议核对）"), ("info", "ℹ️ 提示")):
                group = [i for i in self.issues if i.level == level]
                if not group:
                    continue
                lines += [f"### {title}", ""]
                for issue in group:
                    where = f"（{issue.where}）" if issue.where else ""
                    lines.append(f"- **{issue.code}** {issue.message}{where}")
                lines.append("")

        lines += [
            "## 四、口径提醒",
            "",
            f"- 字段列**留空 = 还没标**；简历上确实没有该信息请填 `{MISSING_MARKER}`。",
            "- 只有 `status=done` / `reviewed` 的行参与达标统计与 N6 评估。",
            f"- 查询集的 `expected_resume_ids` 必须引用简历集里**真实存在**的 `resume_id`（多条用 `{LIST_SEPARATOR}` 分隔）。",
            "- 联系方式为明文列时，**交付乙方前必须跑 `goldset.py seal`**。",
            "",
            "---",
            "",
            "_本报告由 `backend/scripts/goldset.py validate` 生成，只读、不改动标注文件。_",
            "",
        ]
        return "\n".join(lines)


# ============================================================================
# 读取
# ============================================================================
def read_csv_rows(path: Path, columns: tuple[str, ...]) -> tuple[list[dict[str, str]], list[Issue]]:
    """读 CSV，**不抛异常**：文件缺失 / 列缺失 / 编码异常统一转成 ``Issue``。"""
    issues: list[Issue] = []
    if not path.exists():
        issues.append(
            Issue("error", "F-001", f"文件不存在：{path.name}（先跑 `goldset.py build` 生成模板）", str(path))
        )
        return [], issues

    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            missing_columns = [c for c in columns if c not in header]
            if missing_columns:
                issues.append(
                    Issue(
                        "error",
                        "F-002",
                        f"缺少必需列：{', '.join(missing_columns)}（模板被改动过？可 `build --force` 重新生成）",
                        path.name,
                    )
                )
            rows = [{col: (row.get(col) or "").strip() for col in columns} for row in reader]
    except UnicodeDecodeError as exc:
        issues.append(
            Issue(
                "error",
                "F-003",
                f"编码无法解码（{exc}）：请用 Excel「另存为 CSV UTF-8」重新导出，勿存成 GBK。",
                path.name,
            )
        )
        return [], issues

    return rows, issues


# ============================================================================
# 简历集质检
# ============================================================================
def validate_resume_rows(rows: list[dict[str, str]], path: Path) -> tuple[ValidationReport, list[Issue]]:
    issues: list[Issue] = []
    report = ValidationReport(resume_path=path, query_path=Path(""))
    report.resume_total = len(rows)

    if len(rows) < RESUME_MIN_COUNT:
        issues.append(
            Issue(
                "error",
                "R-001",
                f"简历标注集规模 {len(rows)} 份 < 需求下限 {RESUME_MIN_COUNT} 份"
                f"（需求 §12.1 / §6.2：IDX-02 口径要求 ≥200 份）",
            )
        )

    ids = [row["resume_id"] for row in rows]
    duplicates = [rid for rid, count in Counter(ids).items() if count > 1 and rid]
    if duplicates:
        issues.append(
            Issue("error", "R-002", f"`resume_id` 重复 {len(duplicates)} 个", "、".join(duplicates[:MAX_DETAIL_SAMPLES]))
        )
    blank_ids = sum(1 for rid in ids if not rid)
    if blank_ids:
        issues.append(Issue("error", "R-003", f"有 {blank_ids} 行的 `resume_id` 为空"))

    report.resume_status = dict(Counter(row["status"] for row in rows))
    report.file_type_counts = dict(Counter(row["file_type"] for row in rows))

    bad_type = Counter(row["file_type"] for row in rows if row["file_type"] not in FILE_TYPES)
    if bad_type:
        issues.append(
            Issue(
                "error",
                "R-010",
                f"`file_type` 出现非法取值：{dict(bad_type)}（合法取值：{', '.join(FILE_TYPES)}）",
            )
        )
    bad_status = Counter(row["status"] for row in rows if row["status"] not in ANNOTATION_STATUS)
    if bad_status:
        issues.append(
            Issue(
                "error",
                "R-011",
                f"`status` 出现非法取值：{dict(bad_status)}（合法取值：{', '.join(ANNOTATION_STATUS)}）",
            )
        )

    done_rows = [row for row in rows if row["status"] in ("done", "reviewed")]
    if not done_rows:
        issues.append(
            Issue(
                "warn",
                "R-020",
                "尚无任何行标记为 `done` / `reviewed` —— 标注刚起步，填完一批后请把状态改掉，"
                "否则进度与达标统计都看不到（`pending` 的行不计入评估）",
            )
        )

    # 「已完成」的行里仍有空字段：这是最典型的漏填
    blank_cells: list[str] = []
    for row in done_rows:
        for column in CORE_FIELD_COLUMNS:
            if not row[column]:
                blank_cells.append(f"{row['resume_id']}.{column}")
    if blank_cells:
        issues.append(
            Issue(
                "warn",
                "R-021",
                f"`status=done` 的行中有 {len(blank_cells)} 个字段留空 —— "
                f"留空与「确实缺失」无法区分，请补填内容或填 `{MISSING_MARKER}`",
                "、".join(blank_cells[:MAX_DETAIL_SAMPLES]) + ("…" if len(blank_cells) > MAX_DETAIL_SAMPLES else ""),
            )
        )

    no_annotator = [row["resume_id"] for row in done_rows if not row["annotator"]]
    if no_annotator:
        issues.append(
            Issue(
                "warn",
                "R-022",
                f"有 {len(no_annotator)} 行已完成但未填 `annotator`（无法追溯标注人与核算工作量）",
                "、".join(no_annotator[:MAX_DETAIL_SAMPLES]),
            )
        )

    # 填充率（全量口径：反映进度，而非质量）
    total = len(rows) or 1
    for column in CORE_FIELD_COLUMNS:
        filled = sum(1 for row in rows if row[column])
        report.field_fill[column] = filled / total
        report.missing_ratio[column] = sum(1 for row in rows if row[column] == MISSING_MARKER) / total

    done_total = len(done_rows)
    if done_total:
        low_fill = [
            column
            for column in CORE_FIELD_COLUMNS
            if sum(1 for row in done_rows if row[column]) / done_total < FILL_RATE_WARN_THRESHOLD
        ]
        if low_fill:
            issues.append(
                Issue(
                    "warn",
                    "R-030",
                    f"已完成行的填充率低于 {FILL_RATE_WARN_THRESHOLD:.0%} 的字段：{', '.join(low_fill)}"
                    "（若确实普遍缺失可忽略，但请确认不是漏填）",
                )
            )

    image_count = sum(report.file_type_counts.get(t, 0) for t in IMAGE_FILE_TYPES)
    if rows and image_count == 0:
        issues.append(
            Issue(
                "warn",
                "R-031",
                "图片 / 扫描件样本为 0 —— IDX-02 要求「图片件单列口径」，无样本则该项无法评估",
            )
        )

    report.issues = issues
    return report, issues


# ============================================================================
# 查询集质检
# ============================================================================
def validate_query_rows(
    rows: list[dict[str, str]],
    path: Path,
    resume_ids: set[str],
) -> tuple[list[dict[str, Any]], list[Issue]]:
    issues: list[Issue] = []
    metrics: dict[str, Any] = {
        "total": len(rows),
        "status": dict(Counter(row["status"] for row in rows)),
        "intent": dict(Counter(row["intent"] for row in rows if row["intent"])),
    }

    if len(rows) < QUERY_MIN_COUNT:
        issues.append(
            Issue(
                "error",
                "Q-001",
                f"查询-人才集规模 {len(rows)} 条 < 需求下限 {QUERY_MIN_COUNT} 条"
                "（需求 §12.1 / §6.2：IDX-01 口径要求 ≥50 条）",
            )
        )

    ids = [row["query_id"] for row in rows]
    duplicates = [qid for qid, count in Counter(ids).items() if count > 1 and qid]
    if duplicates:
        issues.append(
            Issue("error", "Q-002", f"`query_id` 重复 {len(duplicates)} 个", "、".join(duplicates[:MAX_DETAIL_SAMPLES]))
        )

    texts = [row["query_text"] for row in rows if row["query_text"]]
    dup_texts = [text for text, count in Counter(texts).items() if count > 1]
    if dup_texts:
        issues.append(
            Issue(
                "warn",
                "Q-003",
                f"有 {len(dup_texts)} 条查询文本完全重复 —— 会重复计入 Recall 平均，需合并或改写",
                "；".join(t[:24] for t in dup_texts[:MAX_DETAIL_SAMPLES]),
            )
        )

    bad_intent = Counter(row["intent"] for row in rows if row["intent"] and row["intent"] not in QUERY_INTENTS)
    if bad_intent:
        issues.append(
            Issue(
                "warn",
                "Q-004",
                f"`intent` 出现未登记的取值：{dict(bad_intent)}（建议值：{', '.join(QUERY_INTENTS)}）",
            )
        )

    # 🔴 「还没标」不等于「标错了」：只有 status 已离开 `pending` 的行才纳入完整性检查。
    #    否则模板刚生成时会刷出一屏假 error，真正的问题反而被淹掉 ——
    #    质检报告一旦有惯常噪音，就没人会认真看了。
    started = [row for row in rows if row["status"] != "pending"]

    blank_text = [row["query_id"] for row in started if not row["query_text"]]
    if blank_text:
        issues.append(
            Issue("error", "Q-005", f"有 {len(blank_text)} 条查询的 `query_text` 为空", "、".join(blank_text[:MAX_DETAIL_SAMPLES]))
        )

    # 期望人才：必须非空，且必须引用简历集里真实存在的 resume_id
    no_expected: list[str] = []
    unknown_refs: list[str] = []
    for row in started:
        raw = row["expected_resume_ids"]
        if not raw:
            no_expected.append(row["query_id"])
            continue
        refs = [item.strip() for item in raw.split(LIST_SEPARATOR) if item.strip()]
        for ref in refs:
            if ref not in resume_ids:
                unknown_refs.append(f"{row['query_id']}→{ref}")

    if rows and not started:
        issues.append(
            Issue(
                "warn",
                "Q-009",
                "查询集尚未开始标注（全部为 `pending`）—— 这是**进度提示**而非错误，"
                "标完请把 `status` 改为 `done`",
            )
        )

    if no_expected:
        issues.append(
            Issue(
                "error",
                "Q-006",
                f"有 {len(no_expected)} 条查询未填 `expected_resume_ids` —— "
                "没有期望结果就无法计算 Recall@10（这是 IDX-01 的分母）",
                "、".join(no_expected[:MAX_DETAIL_SAMPLES]),
            )
        )
    if unknown_refs:
        issues.append(
            Issue(
                "error",
                "Q-007",
                f"有 {len(unknown_refs)} 处期望人才引用了简历集中不存在的 `resume_id` —— "
                "常见原因是编号补零位数不一致（应为 `RS-0007` 而非 `RS-007`）",
                "、".join(unknown_refs[:MAX_DETAIL_SAMPLES]),
            )
        )

    wrong_topk = [row["query_id"] for row in rows if row["top_k"].strip() not in ("", "10")]
    if wrong_topk:
        issues.append(
            Issue(
                "info",
                "Q-008",
                f"有 {len(wrong_topk)} 条的 `top_k` 不是 10 —— 指标是 `Recall@10`（需求 §5 IDX-01），"
                "如需按其它 K 评估请另开一列而非改 top_k",
                "、".join(wrong_topk[:MAX_DETAIL_SAMPLES]),
            )
        )

    return [metrics], issues


# ============================================================================
# 入口
# ============================================================================
def run_validate(goldset_dir: Path) -> ValidationReport:
    """对目录下的两个 CSV 做全量质检（只读）。"""
    resume_path = goldset_dir / RESUME_CSV_NAME
    query_path = goldset_dir / QUERY_CSV_NAME

    resume_rows, resume_issues = read_csv_rows(resume_path, RESUME_COLUMNS)
    query_rows, query_issues = read_csv_rows(query_path, QUERY_COLUMNS)

    # ⚠️ ``validate_resume_rows`` 已把自身产生的 issue 写进 ``report.issues``，
    #    这里**只再补文件级问题**（F-001 文件不存在 / F-002 缺列 / F-003 编码），
    #    否则会重复上报（曾经导致同一条告警在报告里出现两次）。
    report, _ = validate_resume_rows(resume_rows, resume_path)
    report.query_path = query_path
    report.issues.extend(resume_issues)

    query_metrics_list, query_issues2 = validate_query_rows(
        query_rows, query_path, {row["resume_id"] for row in resume_rows if row["resume_id"]}
    )
    if query_metrics_list:
        metrics = query_metrics_list[0]
        report.query_total = int(metrics["total"])
        report.query_status = dict(metrics["status"])
    report.issues.extend(query_issues)
    report.issues.extend(query_issues2)

    return report
