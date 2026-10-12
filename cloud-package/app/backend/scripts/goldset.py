"""金标准集工具 · CLI 入口（CU-28）。

对应需求 §12.1 N0 第 ⑪ 项：**启动简历金标准集标注（≥200 份）与查询-人才集（≥50 条）**。

用法
----
.. code-block:: bash

    cd backend

    # 1) 生成标注模板（发给甲方 HR / 业务专家）
    .venv/Scripts/python.exe scripts/goldset.py build

    # 2) 标注过程中随时质检（每 50 份跑一次，把关口前移）
    .venv/Scripts/python.exe scripts/goldset.py validate --report

    # 3) 只看进度（不写报告）
    .venv/Scripts/python.exe scripts/goldset.py status

    # 4) 标注完成后脱敏（明文联系方式 → 盐化哈希），产出可交付版本
    .venv/Scripts/python.exe scripts/goldset.py seal

默认目录：``<仓库根>/data/goldset/``（**含 PII，勿提交到任何代码仓库**）。

退出码
------
- ``0``：操作成功 / 质检通过；
- ``2``：出现错误（规模不足、编号重复、引用不存在的样本、盐未配置……）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

# 允许 `python scripts/goldset.py ...` 直接运行（让 scripts.n0 / scripts.goldset 两个入口一致）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.crypto import ContactHashSaltError, contact_hash_salt_configured  # noqa: E402
from scripts._console import ensure_utf8_stdout  # noqa: E402
from scripts.goldset.build import (  # noqa: E402
    DEFAULT_IMAGE_RATIO,
    QUERY_CSV_NAME,
    README_NAME,
    RESUME_CSV_NAME,
    run_build,
)
from scripts.goldset.schema import (  # noqa: E402
    ACCURACY_DEFINITIONS,
    MISSING_MARKER,
    QUERY_COLUMNS,
    QUERY_MIN_COUNT,
    RESUME_COLUMNS,
    RESUME_MIN_COUNT,
)
from scripts.goldset.seal import run_seal  # noqa: E402
from scripts.goldset.validate import run_validate  # noqa: E402

__all__ = ["main", "default_dir"]


def default_dir() -> Path:
    """默认工作目录：``<仓库根>/data/goldset``。"""
    return Path(__file__).resolve().parents[2] / "data" / "goldset"


def _resolve_dir(raw: str) -> Path:
    return Path(raw) if raw else default_dir()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goldset",
        description="金标准集工具：模板生成 / 质检 / 脱敏（需求 §12.1 N0 ⑪ · §12.5）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="生成标注模板（简历集 + 查询集 + README）")
    p_build.add_argument("--count", type=int, default=RESUME_MIN_COUNT, help=f"简历份数（默认 {RESUME_MIN_COUNT}）")
    p_build.add_argument("--queries", type=int, default=QUERY_MIN_COUNT, help=f"查询条数（默认 {QUERY_MIN_COUNT}）")
    p_build.add_argument(
        "--image-ratio",
        type=float,
        default=DEFAULT_IMAGE_RATIO,
        help=f"图片/扫描件占比（默认 {DEFAULT_IMAGE_RATIO}；IDX-02 要求该类样本单列统计）",
    )
    p_build.add_argument("--dir", default="", help="输出目录（默认 data/goldset）")
    p_build.add_argument("--force", action="store_true", help="覆盖已存在的文件（⚠️ 会丢失已填内容）")

    p_validate = sub.add_parser("validate", help="质检（只读，不改动标注文件）")
    p_validate.add_argument("--dir", default="", help="标注目录（默认 data/goldset）")
    p_validate.add_argument("--report", action="store_true", help="额外写出 Markdown 质检报告")

    p_status = sub.add_parser("status", help="只看标注进度（等于 validate 的摘要版）")
    p_status.add_argument("--dir", default="", help="标注目录（默认 data/goldset）")

    p_seal = sub.add_parser("seal", help="脱敏：明文联系方式 → 盐化哈希，产出可交付版本")
    p_seal.add_argument("--dir", default="", help="标注目录（默认 data/goldset）")
    p_seal.add_argument("--out", default="", help="输出目录（默认 <dir>/sealed）")

    return parser


# ============================================================================
# 子命令
# ============================================================================
def _cmd_build(args: argparse.Namespace) -> int:
    out_dir = _resolve_dir(args.dir)
    try:
        paths = run_build(
            out_dir,
            resume_count=args.count,
            query_count=args.queries,
            image_ratio=args.image_ratio,
            force=args.force,
        )
    except FileExistsError as exc:
        print(f"⛔ {exc}")
        return 2
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2

    image_count = round(args.count * args.image_ratio)
    print(f"✅ 标注模板已生成：{out_dir}")
    print(f"   - {RESUME_CSV_NAME}（{args.count} 行 × {len(RESUME_COLUMNS)} 列；其中图片/扫描件 {image_count} 份）")
    print(f"   - {QUERY_CSV_NAME}（{args.queries} 行 × {len(QUERY_COLUMNS)} 列）")
    print(f"   - {README_NAME}（给标注人的一页纸）")
    print()
    print("下一步：")
    print("   1. 先读 docs/金标准集标注规范.md（口径不看就开标，200 份后基本要返工）")
    print("   2. 把两个 CSV 交给甲方 HR / 业务专家填写（需求 §12.5：标注必须由甲方完成）")
    print("   3. 每完成 ~50 份跑一次：python scripts/goldset.py validate")
    return 0


def _cmd_validate(args: argparse.Namespace, *, write_report: bool) -> int:
    goldset_dir = _resolve_dir(args.dir)
    report = run_validate(goldset_dir)

    print(f"金标准集质检：{goldset_dir}")
    print("-" * 78)
    print(report.summary_line())
    print(
        f"   简历状态：{report.resume_status or '（无数据）'}"
        f"\n   查询状态：{report.query_status or '（无数据）'}"
    )

    if report.errors:
        print("\n❌ 错误（必须修）：")
        for issue in report.errors:
            where = f"（{issue.where}）" if issue.where else ""
            print(f"   [{issue.code}] {issue.message}{where}")
    if report.warnings:
        print("\n⚠️ 告警（建议核对）：")
        for issue in report.warnings:
            where = f"（{issue.where}）" if issue.where else ""
            print(f"   [{issue.code}] {issue.message}{where}")

    if write_report:
        md_path = goldset_dir / "CHECK_REPORT.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(report.to_markdown(), encoding="utf-8", newline="\n")
        print(f"\n📄 质检报告：{md_path}")

    print("\n" + "=" * 78)
    if report.passed:
        print("✅ 质检通过（无 error）。注意 ⚠️ 告警项仍需逐条确认。")
        return 0
    print(f"⛔ 质检未通过：{len(report.errors)} 项错误，修完再跑一次。")
    return 2


def _cmd_status(args: argparse.Namespace) -> int:
    goldset_dir = _resolve_dir(args.dir)
    report = run_validate(goldset_dir)

    print(f"金标准集进度：{goldset_dir}")
    print("-" * 78)
    print(f"简历标注集：{report.resume_total} / {RESUME_MIN_COUNT} 份"
          f"（已完成 {report.resume_done} · 图片件 {report.image_total}）")
    print(f"查询-人才集：{report.query_total} / {QUERY_MIN_COUNT} 条（已完成 {report.query_done}）")
    print(f"错误 {len(report.errors)} · 告警 {len(report.warnings)}")

    if report.resume_total == 0 and report.query_total == 0:
        print("\n⛔ 还没生成模板：先跑 `python scripts/goldset.py build`")
        return 2
    if not report.passed:
        print("\n⛔ 存在错误，详见 `python scripts/goldset.py validate`")
        return 2
    return 0


def _cmd_seal(args: argparse.Namespace) -> int:
    goldset_dir = _resolve_dir(args.dir)
    if not contact_hash_salt_configured():
        print("❌ CONTACT_HASH_SALT 未配置，无法生成比对哈希。")
        print("   在 backend/.env 中填入随机盐：")
        print('   python -c "import secrets; print(secrets.token_hex(16))"')
        print("   ⚠️ 该盐必须与 N2 档案入库时使用的一致；一经使用不可更换。")
        return 2

    out_dir = Path(args.out) if args.out else None
    try:
        result = run_seal(goldset_dir, out_dir)
    except FileNotFoundError as exc:
        print(f"⛔ {exc}")
        return 2
    except ContactHashSaltError as exc:
        print(f"❌ {exc}")
        return 2

    problems = result["problems"]
    print(f"✅ 脱敏完成：{result['target']}")
    print(f"   行数 {result['rows']} · 已哈希格子 {result['hashed_cells']}")
    if problems:
        print(f"\n⚠️ {len(problems)} 个格子无法哈希（需人工检查）：")
        for item in problems[:10]:
            print(f"   - {item}")
    print()
    print("🔴 交付提醒：")
    print("   1. 交付版本只含哈希列，请核对无明文残留（尤其 `remark` 列可能被手写了联系方式）")
    print("   2. 盐是密钥，**不要**随标注集一起交付；乙方侧由部署配置提供")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = _build_parser().parse_args(argv)

    if args.command == "build":
        return _cmd_build(args)
    if args.command == "validate":
        return _cmd_validate(args, write_report=args.report)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "seal":
        return _cmd_seal(args)

    print(f"❌ 未知子命令：{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
