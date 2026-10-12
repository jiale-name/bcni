"""审计日志：TBL-28 ``audit_log``（只追加 + 按月 RANGE 分区）

Revision ID: 0003_audit
Revises: 0002_dict
Create Date: 2026-09-22

对应编码单元 **CU-08**（审计日志，F-1.5，接口 API-28 / API-29）。

🔴 本迁移有**两处**与其它迁移根本不同，必须看明白再改
====================================================

① **分区语句只能写在迁移里，不能写在 ORM 里**
-------------------------------------------
SQLAlchemy 的 ``__table_args__`` **无法表达「命名分区列表」**
（``PARTITION BY RANGE (...) (PARTITION p1 VALUES LESS THAN (...), ...)``）。
因此本表存在**受控例外**：结构与索引的唯一事实来源仍是 ORM
（:mod:`app.models.audit`），但**分区的唯一事实来源是本文件**。

⚠️ 后果：若有人用 ``alembic revision --autogenerate`` 重新生成迁移，
autogenerate **不会**产出分区语句 —— 重建库后表会变成非分区表（功能正常、
性能退化，且**不会报错**）。故本文件顶部与 :mod:`app.models.audit` 的 docstring
都写明了这一点，并有测试（``tests/test_audit.py::test_migration_declares_partition``）
直接检查本文件含分区语句。

② **分区列必须出现在每个唯一键中 ⇒ 复合主键 ``(id, created_at)``**
---------------------------------------------------------------
MySQL 的硬性约束：``PARTITION BY`` 的表达式所引用的列，
必须是表上**每一个**唯一键（含主键）的组成部分。
本表按 ``TO_DAYS(created_at)`` 分区，故 ``created_at`` 必须进主键。
这不是设计选择，是**唯一可行的写法**。

分区的生成方式
==============
**按执行时刻动态生成**「当月 + 次月」两个分区，再接 ``pmax`` —— 而不是照抄
数据库 §4.7 示例里的 ``p202609`` / ``p202610``：

- 照抄字面量的话，在 2027 年建库会得到一个「早已过去的月份 + pmax」，
  所有数据落进 ``pmax``，``DROP PARTITION`` 清理历史也就无从谈起；
- 动态生成保证「执行后的两个月内无需人工干预」；
- 更早/更晚的月份由 ``scripts/audit_partition.py`` 补足与回收（已登记遗留：
  生产应挂定时任务，一期末接入调度器）。

⚠️ 代价：``alembic upgrade head --sql`` 生成的 DDL **含执行时的日期**，
两次执行结果不同。这是可接受的 —— 离线模式本用于「评审 DDL 是否合理」，
而分区边界随执行时间变化正是我们想要的语义。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0003_audit"
down_revision: str | None = "0002_dict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: MySQL 表级参数（数据库 §0 原则 7：一律 utf8mb4 / utf8mb4_unicode_ci）
_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

#: 初始化时预建的分区个数（当月 + 后续 n-1 个月）。2 个月 = 建库后至少有两个月缓冲。
INITIAL_PARTITIONS = 2

#: 兜底分区名。⚠️ 它必须**永远存在且排在最后**：
#: 一旦某个月的记录落进 pmax，同月的新分区就无法再 ``ADD``
#: （MySQL 不允许在已有更大边界的分区之前插入），
#: 只能 ``REORGANIZE PARTITION`` 拆分 —— 那是要锁表的运维动作。
#: 因此 ``scripts/audit_partition.py`` 要**提前**补下个月的分区，不能等 pmax 有数据才补。
PMAX_PARTITION = "pmax"


def _month_partitions(count: int = INITIAL_PARTITIONS) -> list[tuple[str, str]]:
    """生成 ``[(分区名, 上界日期), ...]``，从**执行月份**起连续 ``count`` 个月。

    分区命名 ``pYYYYMM``，其语义是「**该自然月**的数据」；
    因此下界隐式继承自前一个分区的上界，上界为**下月 1 日**。
    """
    out: list[tuple[str, str]] = []
    year, month = date.today().year, date.today().month
    for _ in range(max(1, count)):
        # 12 月时：month // 12 == 1 → 跨年；month % 12 + 1 == 1 → 1 月。正确。
        next_year, next_month = year + (month // 12), (month % 12) + 1
        out.append((f"p{year:04d}{month:02d}", date(next_year, next_month, 1).isoformat()))
        year, month = next_year, next_month
    return out


def _partition_clause() -> str:
    """拼出 ``PARTITION BY RANGE (TO_DAYS(created_at)) (...)`` 子句。"""
    parts = [
        f"PARTITION {name} VALUES LESS THAN (TO_DAYS('{upper}'))"
        for name, upper in _month_partitions()
    ]
    parts.append(f"PARTITION {PMAX_PARTITION} VALUES LESS THAN MAXVALUE")
    joined = ",\n  ".join(parts)
    return f"PARTITION BY RANGE (TO_DAYS(`created_at`)) (\n  {joined}\n)"


def upgrade() -> None:
    op.create_table(
        "audit_log",
        # ⚠️ 不写 primary_key=True，改用下面的 PrimaryKeyConstraint：
        #    复合主键必须一次声明，逐列声明会得到两个单列主键。
        sa.Column(
            "id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID，应用生成）"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="操作时间（分区键，必须进主键）",
        ),
        sa.Column(
            "tenant_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="租户 ID",
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True, comment="操作人"),
        sa.Column(
            "action",
            sa.String(64),
            nullable=False,
            comment="动作：login/logout/change_password/export/perm_change/delete/...",
        ),
        sa.Column("target_type", sa.String(32), nullable=True, comment="对象类型"),
        sa.Column("target_id", sa.String(64), nullable=True, comment="对象 ID"),
        sa.Column(
            "before_json",
            mysql.MEDIUMTEXT(),
            nullable=True,
            comment="改前快照（JSON；密码/Token 由写入侧脱敏后不入库）",
        ),
        sa.Column(
            "after_json",
            mysql.MEDIUMTEXT(),
            nullable=True,
            comment="改后快照（JSON；非变更类动作为动作结果上下文）",
        ),
        sa.Column("ip", sa.String(64), nullable=True, comment="来源 IP"),
        sa.Column("ua", sa.String(255), nullable=True, comment="User-Agent"),
        sa.Column("trace_id", sa.String(64), nullable=True, comment="链路 ID"),
        sa.PrimaryKeyConstraint("id", "created_at"),
        **_TABLE_KW,
    )

    # 三个二级索引与数据库 §4.7 的 DDL 逐字一致（含 idx_action **不带** tenant_id 前缀）
    op.create_index("idx_tenant_time", "audit_log", ["tenant_id", "created_at"])
    op.create_index("idx_tenant_user", "audit_log", ["tenant_id", "user_id"])
    op.create_index("idx_action", "audit_log", ["action"])

    # 🔴 唯一事实来源在本文件（见模块 docstring ①）：ORM 表达不了命名分区列表
    op.execute(f"ALTER TABLE `audit_log` {_partition_clause()}")


def downgrade() -> None:
    # 分区表 drop 与普通表无差别（分区随表一起消失）
    op.drop_index("idx_action", table_name="audit_log")
    op.drop_index("idx_tenant_user", table_name="audit_log")
    op.drop_index("idx_tenant_time", table_name="audit_log")
    op.drop_table("audit_log")
