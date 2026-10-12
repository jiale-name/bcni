"""按月分区表的维护脚本（TBL-28 ``audit_log`` + TBL-24 ``ai_call_log``，运维脚本）。

📌 **文件名保留 ``audit_partition.py``**（CU-08 交付时的名字，README / 部署文档 /
评审文档都已引用它）。它现在管**两张表**，改名的收益抵不上「文档里的命令失效」的成本 ——
真正该看的是下面这张表：

| 表 | 谁在用 | 留存期 | 分区键 |
| --- | --- | --- | --- |
| ``audit_log``（TBL-28） | 审计日志 | ≥12 个月（数据库 §4.7） | ``TO_DAYS(created_at)`` |
| ``ai_call_log``（TBL-24） | AI 调用日志 / 成本看板 | 6 个月（``AGENT_STEP_RETENTION_MONTHS`` 同量级） | 同上 |

⚠️ **两张表必须一起维护**：只补 ``audit_log`` 而漏掉 ``ai_call_log`` 的话，
后者的 ``pmax`` 会悄悄吃掉未来所有月份的数据，等到想回收时才发现在
``pmax`` 里清不掉（见下）—— 而 AI 调用日志的**量级远高于审计日志**
（每次模型调用一条），膨胀速度会快一个数量级。

为什么必须有这个脚本
====================
两表都按 ``TO_DAYS(created_at)`` 做**按月 RANGE 分区**，末尾永远有一个
``pmax VALUES LESS THAN MAXVALUE`` 兜底分区（迁移 ``0003_audit`` / ``0004_ai_agent`` 建立）。

「有 ``pmax`` 兜底」听起来很安全，实际埋着两个坑，**都不报错**：

1. **pmax 会无限膨胀。** 一旦某个月的记录落进 ``pmax`` 而该月**没有**自己的分区，
   之后 ``DROP PARTITION`` 就清不掉这批数据 —— 清理历史只能退化成 ``DELETE``
   （全表扫 + 写 undo，在亿级表上等同事故）。
2. **补建分区的窗口会被关掉。** 见下条「MySQL 的硬约束」。

🔴 MySQL 的两条硬约束（本脚本围绕它们设计，且已实测确认）
=======================================================
① **不能用 ``ADD PARTITION`` 往含 ``MAXVALUE`` 分区的表里加分区。**
   实测报错：``(1481, 'MAXVALUE can only be used in last partition definition')``。
   （直觉上以为 MySQL 会自动把 ``pmax`` 排到最后 —— 并不会。）
   正确写法是 **``REORGANIZE PARTITION pmax INTO (新分区..., pmax ...)``**，
   一次把 ``pmax`` 拆成「若干真实分区 + 新的 pmax」。

② **``REORGANIZE`` 会重建表。** 若 ``pmax`` 里已有数据，这一步可能长时间锁表。
   ⇒ 必须**提前**补分区（本月就把未来几个月补上），而不是等 pmax 有数据才补。
   本脚本在 ``pmax`` 非空时会**显式告警**（见 :func:`ensure_next_partitions`）。

③ **分区上界必须严格递增** ⇒ 不能补「中间缺的月份」。若检测到空洞，
   脚本会**拒绝执行并列出缺口**，而不是硬塞一条必然失败的 SQL。

三种子命令
==========
| 子命令 | 作用 | 何时用 |
| --- | --- | --- |
| ``--list`` | 打印当前分区与各自行数 | 排查前先看现状 |
| ``--ensure-next`` | 补足「未来 N 个月」的分区 | **每月执行**（幂等） |
| ``--drop-before N`` | 删除上界早于 N 个月前（含）的分区 | 留存期外清理（**会丢数据**） |

🔴 ``--drop-before`` 是**不可逆**的破坏性操作：分区一 DROP，里面的审计记录
即刻消失、库内无法恢复。故：

- 默认只**打印**将执行什么（不加 ``--yes`` 即预览模式）；
- 真删必须**显式**加 ``--yes``；
- 且**永不删除 ``pmax``**（它不是「历史数据」，删掉会让后续插入直接报错）。

用法::

    python -m scripts.audit_partition --list
    python -m scripts.audit_partition --ensure-next --months 3
    python -m scripts.audit_partition --ensure-next --months 3 --table all
    python -m scripts.audit_partition --drop-before 12            # 只预览
    python -m scripts.audit_partition --drop-before 12 --yes       # 真删
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from app.dao.session import engine
from scripts._console import ensure_utf8_stdout

__all__ = [
    "AI_CALL_TABLE",
    "AUDIT_TABLE",
    "TABLES",
    "PartitionInfo",
    "drop_partitions",
    "droppable_partitions",
    "ensure_next_partitions",
    "list_partitions",
    "month_index",
    "partition_name",
    "shift_month",
]

#: TBL-28 审计日志（CU-08 建立分区）
AUDIT_TABLE = "audit_log"

#: TBL-24 AI 调用日志（CU-12 建立分区）
AI_CALL_TABLE = "ai_call_log"

#: 🔴 **白名单**：可维护的表就这两张。
#: 做成白名单（而不是 `--table` 自由字符串）的理由：
#: 本脚本的所有语句都含破坏性操作（``DROP PARTITION``），
#: 让它「可以指向任意表」等于把一次手滑放大成事故。
TABLES: tuple[str, ...] = (AUDIT_TABLE, AI_CALL_TABLE)

#: 缺省表 —— 保持与 CU-08 的行为一致（不加 ``--table`` 时仍是审计表）
TABLE = AUDIT_TABLE

#: 兜底分区名（与迁移 ``0003_audit`` 保持一致）
PMAX = "pmax"

#: 默认留存月数（数据库 §4.7：留存 ≥12 个月）
DEFAULT_RETENTION_MONTHS = 12

#: ``pYYYYMM`` 形态。**不匹配的分区一律视为「不是我们能管的」**：
#: 既不参与回收、也不参与补足排序 —— 见 :meth:`PartitionInfo.covered_month`。
_PARTITION_NAME_RE = re.compile(r"^p(\d{4})(\d{2})$")


@dataclass(frozen=True)
class PartitionInfo:
    """一个分区的现状。"""

    name: str
    #: MySQL 的 ``PARTITION_DESCRIPTION``：真实分区存 ``TO_DAYS()`` 值，``pmax`` 存 ``MAXVALUE``
    description: str
    #: ``information_schema.PARTITIONS.TABLE_ROWS``。
    #: ⚠️ InnoDB 下这是**估算值**，只用于「量级判断」（如「pmax 是不是已经有数据了」），
    #: 不要拿它当准确行数对外报数。
    approx_rows: int

    @property
    def is_pmax(self) -> bool:
        return self.name == PMAX

    @property
    def covered_month(self) -> tuple[int, int] | None:
        """该分区**覆盖的月份** ``(year, month)``；无法识别时返回 ``None``。

        🔴 **为什么从分区名解析，而不是把 ``TO_DAYS`` 反算成日期**
        ------------------------------------------------------
        第一版实现用 ``date.fromordinal(int(description) - 1)`` 反算，实测**偏了一个季度**
        —— MySQL 的 ``TO_DAYS`` 以「公元 0 年 1 月 1 日 = 1」起算，
        与 Python ``date.toordinal()``（公元 1 年 1 月 1 日 = 1）**不同源**，
        中间隔着 MySQL 自己的「0 年闰年」处理。这类偏移不会报错，
        只会让「该删哪些分区」静默错位 —— 而在本脚本里，
        错位的后果是**删掉本不该删的审计数据**。

        分区名 ``pYYYYMM`` 是**我们自己定的、且迁移与脚本共用同一套命名**
        （:func:`partition_name`），从名字解析没有任何换算歧义。
        ``description`` 仅用于 ``--list`` 时展示原始值以便人工核对。
        """
        match = _PARTITION_NAME_RE.match(self.name)
        if not match:
            return None
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            return None
        return year, month

    @property
    def upper_bound_label(self) -> str:
        """人类可读的上界：``2026-11-01（不含）`` / ``MAXVALUE``。"""
        month = self.covered_month
        if month is None:
            return self.description
        year, month_no = shift_month(*month, 1)
        return f"{year:04d}-{month_no:02d}-01（不含）"


def list_partitions(table: str = TABLE) -> list[PartitionInfo]:
    """读 ``table`` 当前分区列表（按分区顺序）。"""
    _assert_known_table(table)
    sql = text(
        """
        SELECT PARTITION_NAME, PARTITION_DESCRIPTION, COALESCE(TABLE_ROWS, 0)
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl
          AND PARTITION_NAME IS NOT NULL
        ORDER BY PARTITION_ORDINAL_POSITION
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"tbl": table}).all()
    return [PartitionInfo(str(r[0]), str(r[1]), int(r[2])) for r in rows]


def _assert_known_table(table: str) -> None:
    """表名白名单校验（**每个会执行 SQL 的入口都调一次**）。

    ⚠️ 不在 CLI 层「校验一次就算」：本模块的函数会被别的脚本 / 定时任务直接 import
    （部署文档里的 cron 就调 ``ensure_next_partitions``），CLI 校验挡不住那条路径。
    """
    if table not in TABLES:
        raise ValueError(f"未知的表 {table!r}；可维护的表：{', '.join(TABLES)}")


# ============================================================================
# 月份算术与命名
# ============================================================================
def month_index(year: int, month: int) -> int:
    """把 ``(year, month)`` 压成单调整数（用于比较与求差）。"""
    return year * 12 + (month - 1)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """把 ``(year, month)`` 平移 ``delta`` 个月（``delta`` 可为负）。"""
    index = month_index(year, month) + delta
    return index // 12, index % 12 + 1


def partition_name(year: int, month: int) -> str:
    """``(2026, 10)`` → ``"p202610"``。"""
    return f"p{year:04d}{month:02d}"


# ============================================================================
# 补足
# ============================================================================
def ensure_next_partitions(months: int, table: str = TABLE) -> list[str]:
    """确保「当月起连续 ``months`` 个月」都有独立分区。返回**本次新增**的分区名。

    幂等：已存在的月份跳过；若全部已存在，**不发任何 SQL**（避免无谓的表重建）。

    :raises RuntimeError: 检测到分区空洞（上界必须严格递增，无法回填中间月份）

    实现要点（全部来自实测，不是推测）：

    - 用 ``REORGANIZE PARTITION pmax INTO (...)``，**不能用 ``ADD PARTITION``**
      —— 后者在含 ``MAXVALUE`` 分区的表上必然报 1481；
    - 所有待补月份在**一条** ``REORGANIZE`` 里完成：每次 reorganize 都要重建
      ``pmax``，逐月执行 = 多次重建；
    - ``pmax`` 非空时打印告警（重建会变慢、可能锁表），提示尽快补齐并检查留存策略。
    """
    _assert_known_table(table)
    today = date.today()
    existing = list_partitions(table)
    names = {p.name for p in existing}

    targets: list[tuple[int, int]] = [
        shift_month(today.year, today.month, offset) for offset in range(max(1, months))
    ]
    missing = [(y, m) for y, m in targets if partition_name(y, m) not in names]
    if not missing:
        return []

    known = [p for p in existing if p.covered_month is not None]
    if known:
        last_index = max(month_index(*p.covered_month) for p in known if p.covered_month)
        first_missing = month_index(*missing[0])
        if first_missing <= last_index:
            holes = ", ".join(partition_name(y, m) for y, m in missing if month_index(y, m) <= last_index)
            raise RuntimeError(
                f"分区存在空洞：{holes} 早于或等于现有最新分区"
                f"（{known[-1].name}）。MySQL 要求分区上界严格递增，"
                "无法用 REORGANIZE pmax 回填中间月份。"
                "请人工处理（将该空洞月份与更晚的分区一起 REORGANIZE，"
                "或先 DROP 掉空洞之后的月份——后者会丢数据，需先确认）。"
            )

    pmax_rows = next((p.approx_rows for p in existing if p.is_pmax), 0)
    if pmax_rows:
        print(
            f"⚠️ 兜底分区 pmax 约含 {pmax_rows} 行数据：本次 REORGANIZE 需要重建它，"
            "数据量越大耗时越长且可能锁表。建议在低峰期执行。"
        )

    definitions = [
        f"PARTITION {partition_name(y, m)} "
        f"VALUES LESS THAN (TO_DAYS('{_month_upper_iso(y, m)}'))"
        for y, m in missing
    ]
    definitions.append(f"PARTITION {PMAX} VALUES LESS THAN MAXVALUE")
    sql = (
        f"ALTER TABLE `{table}` REORGANIZE PARTITION {PMAX} INTO "
        f"({', '.join(definitions)})"
    )
    with engine.begin() as conn:
        conn.execute(text(sql))

    return [partition_name(y, m) for y, m in missing]


def _month_upper_iso(year: int, month: int) -> str:
    """分区上界 = **下月 1 日**（``LESS THAN`` 是开区间）。"""
    upper_year, upper_month = shift_month(year, month, 1)
    return date(upper_year, upper_month, 1).isoformat()


# ============================================================================
# 回收
# ============================================================================
def droppable_partitions(retention_months: int, table: str = TABLE) -> list[PartitionInfo]:
    """整段都超出留存期、可整块回收的分区。

    判定用「**分区覆盖的月份** 早于 ``retention_months`` 个月前」：

    一个 ``pYYYYMM`` 分区里的**每一条**记录都落在该自然月内，
    故「该月的下月 1 日 ≤ 截止月 1 日」⇔「该分区全部记录都超出留存期」。
    反过来只要有一条在留存期内，就不能动这个分区 ——
    ``DROP PARTITION`` 只能整块丢，没有「部分删除」。

    ⚠️ 名字不符合 ``pYYYYMM`` 的分区（``covered_month`` 为 ``None``）
    **一律不回收**：说不出它覆盖哪段时间，就不该删它（fail-safe）。
    """
    _assert_known_table(table)
    today = date.today()
    # 截止月：今天往前 retention_months 个月的**当月**。
    # 判定式推导：分区覆盖月 M 的全部记录都在 [M-01, M+1-01) 内，
    # 要「全部超出留存期」即需 M+1-01 ≤ cutoff-01 ⇔ month_index(M) + 1 ≤ cutoff_index
    # ⇔ month_index(M) < cutoff_index。
    cutoff_index = month_index(*shift_month(today.year, today.month, -abs(retention_months)))

    out: list[PartitionInfo] = []
    for part in list_partitions(table):
        if part.is_pmax:
            continue
        month = part.covered_month
        if month is None:
            continue
        if month_index(*month) < cutoff_index:
            out.append(part)
    return out


def drop_partitions(names: list[str], table: str = TABLE) -> None:
    """批量 DROP PARTITION（**破坏性**，调用方须已获用户确认）。"""
    _assert_known_table(table)
    if not names:
        return
    # 🔴 再挡一次 pmax：调用链上任何一处出错都不该导致「把兜底分区删掉」，
    #    那会让后续所有插入直接报错（表里没有能承接新数据的区）。
    safe = [n for n in names if n != PMAX]
    if len(safe) != len(names):
        raise SystemExit("拒绝执行：待删列表里包含 pmax（兜底分区不可删除）")
    joined = ", ".join(f"`{n}`" for n in safe)
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE `{table}` DROP PARTITION {joined}"))


# ============================================================================
# CLI
# ============================================================================
def _print_partitions(title: str, table: str = TABLE) -> None:
    parts = list_partitions(table)
    print(f"\n{title}")
    print(f"  {'分区':<10}{'覆盖月份':<12}{'上界':<22}{'约行数':>10}")
    for p in parts:
        month = p.covered_month
        covered = f"{month[0]}-{month[1]:02d}" if month else "（无法识别）"
        print(f"  {p.name:<10}{covered:<12}{p.upper_bound_label:<22}{p.approx_rows:>10}")
    if not parts:
        print("  （无分区 —— 表不存在，或尚未执行 alembic upgrade head）")


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="按月分区表维护（TBL-28 audit_log / TBL-24 ai_call_log：补足 / 回收 / 查看）"
    )
    parser.add_argument(
        "--table",
        default=TABLE,
        choices=[*TABLES, "all"],
        help=f"要维护的表；默认 {TABLE}；`all` = 两张表都做（**生产环境建议用 all**）",
    )
    parser.add_argument("--list", action="store_true", help="只打印当前分区，不做任何修改")
    parser.add_argument(
        "--ensure-next",
        action="store_true",
        help="补足「当月起未来 N 个月」的分区（幂等，建议每月执行一次）",
    )
    parser.add_argument(
        "--months", type=int, default=3, help="配合 --ensure-next：补足多少个月（默认 3）"
    )
    parser.add_argument(
        "--drop-before",
        type=int,
        metavar="N",
        help=f"删除覆盖月份早于 N 个月前（含）的分区；默认留存 {DEFAULT_RETENTION_MONTHS} 个月",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="🔴 确认执行 --drop-before（不加则只预览，不会删任何数据）",
    )
    args = parser.parse_args(argv)

    if not any((args.list, args.ensure_next, args.drop_before is not None)):
        parser.print_help()
        return 1

    tables = list(TABLES) if args.table == "all" else [args.table]

    for table in tables:
        rc = _run_for_table(table, args)
        if rc:
            return rc
    return 0


def _run_for_table(table: str, args: argparse.Namespace) -> int:
    """对单张表执行子命令，返回退出码（0 = 成功）。"""
    _print_partitions(f"当前分区（{table}）：", table)

    if args.ensure_next:
        try:
            added = ensure_next_partitions(args.months, table)
        except Exception as exc:  # noqa: BLE001 — CLI 入口，需给出可读信息
            print(f"\n[FAIL] {table} 补分区失败：{exc}")
            print("       常见原因：① 表不存在（未跑 alembic upgrade head）；② MySQL 未启动。")
            return 1
        if added:
            print(f"\n[OK] {table} 已新增分区：{', '.join(added)}")
        else:
            print(f"\n[OK] {table} 当月起 {args.months} 个月的分区均已存在，无需新增。")

    if args.drop_before is not None:
        targets = droppable_partitions(args.drop_before, table)
        if not targets:
            print(f"\n[OK] {table} 没有超出 {args.drop_before} 个月留存期的分区，无需回收。")
            return 0
        print(f"\n⚠️ {table} 以下分区超出 {args.drop_before} 个月留存期，将被 **永久删除**：")
        for p in targets:
            print(
                f"   - {p.name}（覆盖 {p.covered_month}，上界 {p.upper_bound_label}，"
                f"约 {p.approx_rows} 行）"
            )
        print("   🔴 DROP PARTITION 不可逆：分区内的记录会即刻消失、库内无法恢复。")
        if not args.yes:
            print("\n（预览模式：未执行任何删除。确认无误后重跑并加 --yes）")
            return 0
        drop_partitions([p.name for p in targets], table)
        print(f"\n[OK] {table} 已删除 {len(targets)} 个分区：{', '.join(p.name for p in targets)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
