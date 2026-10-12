"""查重对表：TBL-17 talent_dup_pair（CU-19）

Revision ID: 0006_dedup
Revises: 0005_talent
Create Date: 2026-09-22

对应编码单元 **CU-19**（查重与合并含回滚，F-3.5 / SQ-4 / SM-1 / API-57~61）。

与数据库设计文档 DDL 的**有意偏离**（评审文档批次 11 登记）：

1. ``snapshot``（JSON）列 —— 合并回滚快照**存本表**而非 TBL-22 的 ``snapshot_ref``
   （``VARCHAR(512)`` 装不下两份档案字段 + 标签的完整快照）。TBL-22 仍写
   ``change_source=2`` 留痕，其 ``snapshot_ref`` 存 ``pair.id`` 作引用；
2. ``master_id`` 列 —— 合并后主档方向的可反查依据（回滚时无需从快照反推方向）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0006_dedup"
down_revision: str | None = "0005_talent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _snowflake_pk() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID，应用生成）")


def _tenant_col() -> sa.Column:
    return sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0"), comment="租户 ID")


def upgrade() -> None:
    op.create_table(
        "talent_dup_pair",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("talent_id_a", sa.BigInteger(), nullable=False, comment="人才 A（恒 < talent_id_b）"),
        sa.Column("talent_id_b", sa.BigInteger(), nullable=False, comment="人才 B"),
        sa.Column("similarity", mysql.DECIMAL(5, 4), nullable=True, comment="相似度 0.0000~1.0000（精确键命中=1.0）"),
        sa.Column("match_fields", sa.JSON(), nullable=True, comment="命中的比对字段（如 phone_hash）"),
        sa.Column(
            "status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="状态 0 待处理 1 已合并 2 已忽略",
        ),
        sa.Column("master_id", sa.BigInteger(), nullable=True, comment="合并主档 ID（合并后写入）"),
        sa.Column("snapshot", sa.JSON(), nullable=True, comment="合并回滚快照（完整 JSON）"),
        sa.Column("merged_by", sa.BigInteger(), nullable=True, comment="合并操作人"),
        sa.Column("merged_at", sa.DateTime(), nullable=True, comment="合并时间"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "talent_id_a", "talent_id_b", "is_deleted", name="uk_tenant_pair"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_status", "talent_dup_pair", ["tenant_id", "status"])
    op.create_index("idx_tenant_pair_a", "talent_dup_pair", ["tenant_id", "talent_id_a"])
    op.create_index("idx_tenant_pair_b", "talent_dup_pair", ["tenant_id", "talent_id_b"])


def downgrade() -> None:
    op.drop_index("idx_tenant_pair_b", table_name="talent_dup_pair")
    op.drop_index("idx_tenant_pair_a", table_name="talent_dup_pair")
    op.drop_index("idx_tenant_status", table_name="talent_dup_pair")
    op.drop_table("talent_dup_pair")
