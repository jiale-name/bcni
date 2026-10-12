"""N0 前置实测与模型就绪验证 · CLI 入口（CU-27）。

用法
----
.. code-block:: bash

    cd backend

    # 全量探针 + 打印摘要 + 写报告到 docs/（推荐，N0 交付物由此产出）
    .venv/Scripts/python.exe scripts/n0_probe.py

    # 只看有哪些探针（不执行任何 I/O，可在没环境的机器上跑）
    .venv/Scripts/python.exe scripts/n0_probe.py --list

    # 只跑指定的几项（排障时用，避免等 Embedding 采样）
    .venv/Scripts/python.exe scripts/n0_probe.py --only mysql,redis,ollama

    # 加大 Embedding 采样次数（CPU 推理偏慢时取更稳的均值）
    .venv/Scripts/python.exe scripts/n0_probe.py --only embedding --embedding-rounds 16

    # 只打印不落盘
    .venv/Scripts/python.exe scripts/n0_probe.py --no-report

退出码
------
- ``0``：无异常，且**没有标记为阻塞的探针不可用** —— 可以推进后续批次；
- ``2``：存在异常，或有阻塞探针未就绪 —— 按报告「三、需人工执行的处置清单」处理。

⚠️ 非阻塞项的 ``blocked``（如未配置云通道）**不会**让退出码变 2：
   那不是缺陷，正是 N0 要如实记录的现状。
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

# 允许 `python scripts/n0_probe.py` 直接运行（把 backend/ 加入 sys.path），
# 使 CLI 与 pytest 两种入口都解析到同一个 `scripts.n0.*` 模块。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._console import ensure_utf8_stdout  # noqa: E402
from scripts.n0.probes import KNOWN_KEYS, PROBE_SPECS, GROUPS, run_all, set_embedding_rounds  # noqa: E402
from scripts.n0.report import build_context, render_json, render_markdown  # noqa: E402
from scripts.n0.result import Probe, ProbeStatus, exit_code, summarize  # noqa: E402
from scripts.n0.targets import SCOPE_LABELS, METRIC_TARGETS  # noqa: E402

#: 终端图标（Windows 控制台需 UTF-8，见 ``_ensure_utf8_stdout``）
_ICON: dict[ProbeStatus, str] = {
    ProbeStatus.OK: "✅",
    ProbeStatus.DEGRADED: "🟡",
    ProbeStatus.BLOCKED: "⛔",
    ProbeStatus.FAILED: "❌",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n0_probe",
        description="N0 前置实测与模型就绪验证（需求 §12.1 N0 · §6.1 基线压测先行原则）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="只列出探针注册表，不执行任何探测")
    parser.add_argument("--only", default="", help=f"只跑指定探针（逗号分隔）：{', '.join(KNOWN_KEYS)}")
    parser.add_argument("--out", default="", help="Markdown 报告输出路径（默认 docs/N0-基线实测报告-<日期>.md）")
    parser.add_argument("--no-report", action="store_true", help="只打印，不写报告文件")
    parser.add_argument(
        "--embedding-rounds",
        type=int,
        default=None,
        help="Embedding 吞吐采样次数（不传则用内置默认 8；CPU 推理偏慢时可调小以缩短耗时）",
    )
    return parser


def default_report_path() -> Path:
    """默认报告路径：``<仓库根>/docs/N0-基线实测报告-YYYY-MM-DD.md``。"""
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "docs" / f"N0-基线实测报告-{datetime.now().strftime('%Y-%m-%d')}.md"


def _print_registry() -> None:
    print("N0 探针注册表（key · 分组 · 是否阻塞 · 需求出处）\n" + "-" * 78)
    for group_key, group_title in GROUPS.items():
        print(f"\n【{group_key}】{group_title}")
        for spec in PROBE_SPECS:
            if spec.group != group_key:
                continue
            flag = "阻塞" if spec.blocking else "  —"
            print(f"  {spec.key:<15} {flag}  {spec.title}\n{'':<19}{spec.requirement_ref}")
    print("\n指标基线（scope：N0 单点可测 / 需压测 / 需金标准集）\n" + "-" * 78)
    for target in METRIC_TARGETS:
        print(f"  {target.code:<14} [{SCOPE_LABELS[target.scope]}] {target.name}")


def main(argv: Sequence[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = _build_parser().parse_args(argv)

    if args.list:
        _print_registry()
        return 0

    only = [item.strip() for item in args.only.split(",") if item.strip()] if args.only else None
    if only:
        unknown = [item for item in only if item not in KNOWN_KEYS]
        if unknown:
            print(f"❌ 未知探针 key：{', '.join(unknown)}")
            print(f"   可用：{', '.join(KNOWN_KEYS)}")
            return 2

    # ⚠️ 用 ``is not None`` 而非真值判断：``--embedding-rounds 0`` 是**非法输入**，
    #    必须走到 ``set_embedding_rounds`` 里报错，而不能被当成「没传」而静默忽略。
    if args.embedding_rounds is not None:
        try:
            set_embedding_rounds(args.embedding_rounds)
        except ValueError as exc:
            print(f"❌ {exc}")
            return 2

    total = len([s for s in PROBE_SPECS if only is None or s.key in set(only)])
    counter = itertools.count(1)

    def on_done(probe: Probe) -> None:
        index = next(counter)
        print(f"[{index}/{total}] {_ICON[probe.status]} {probe.key:<14} {probe.detail}")

    print(f"N0 探针开始执行：{total} 项（key: {', '.join(only) if only else '全部'}）")
    print("-" * 78)
    probes = run_all(only, on_done=on_done)

    counts = summarize(probes)
    code = exit_code(probes)
    ctx = build_context(probes)

    print("-" * 78)
    print(
        f"结论：✅ 就绪 {counts['ok']} · 🟡 降级 {counts['degraded']} · "
        f"⛔ 待就绪 {counts['blocked']} · ❌ 异常 {counts['failed']}"
    )

    blockers = [p for p in probes if p.is_blocker]
    if blockers:
        print("\n🔴 阻塞后续阶段（必须处理）：")
        for probe in blockers:
            print(f"  - {probe.key}：{probe.detail}")

    pending = [p for p in probes if p.status is ProbeStatus.BLOCKED and not p.blocking]
    if pending:
        print("\n⛔ 非阻塞待办（不挡开工，应在 N0 内闭环）：")
        for probe in pending:
            print(f"  - {probe.key}：{probe.detail}")

    if not args.no_report:
        md_path = Path(args.out) if args.out else default_report_path()
        if not md_path.is_absolute():
            md_path = Path.cwd() / md_path
        json_path = md_path.with_suffix(".json")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n"：报告会进 git，固定 LF 避免 Windows 上产出 CRLF 造成整文件 diff
        md_path.write_text(render_markdown(ctx), encoding="utf-8", newline="\n")
        json_path.write_text(
            json.dumps(render_json(ctx), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"\n📄 Markdown 报告：{md_path}")
        print(f"📄 结构化结果：  {json_path}")

    print("\n" + "=" * 78)
    print(f"退出码 {code} —— {'✅ 可继续推进后续批次' if code == 0 else '⛔ 请先按报告处置阻塞项/异常项'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
