"""TBL-12/TBL-13 structured work and project experience details.

Revision ID: 0008_talent_detail
Revises: 0007_privacy
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0008_talent_detail"
down_revision: str | None = "0007_privacy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _id_col() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID，应用生成）")


def _tenant_col() -> sa.Column:
    return sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0"), comment="租户 ID")


def _common_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "talent_work_exp",
        _id_col(),
        _tenant_col(),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="人才 ID"),
        sa.Column("seq_no", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="序号（时间倒序）"),
        sa.Column("company", sa.String(128), nullable=False, comment="公司名称"),
        sa.Column("dept_name", sa.String(128), nullable=True, comment="部门"),
        sa.Column("position_name", sa.String(128), nullable=True, comment="职位"),
        sa.Column("start_date", sa.Date(), nullable=True, comment="开始日期"),
        sa.Column("end_date", sa.Date(), nullable=True, comment="结束日期"),
        sa.Column("is_current", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="是否在职"),
        sa.Column("description", sa.Text(), nullable=True, comment="工作描述"),
        sa.Column("source", mysql.TINYINT(), nullable=False, server_default=sa.text("0"), comment="来源 0 AI 解析 1 人工"),
        *_common_columns(),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_talent", "talent_work_exp", ["tenant_id", "talent_id", "seq_no"])

    op.create_table(
        "talent_project_exp",
        _id_col(),
        _tenant_col(),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="人才 ID"),
        sa.Column("seq_no", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="序号"),
        sa.Column("project_name", sa.String(128), nullable=False, comment="项目名称"),
        sa.Column("role_name", sa.String(64), nullable=True, comment="担任角色"),
        sa.Column("start_date", sa.Date(), nullable=True, comment="开始日期"),
        sa.Column("end_date", sa.Date(), nullable=True, comment="结束日期"),
        sa.Column("description", sa.Text(), nullable=True, comment="项目描述"),
        sa.Column("tech_stack", sa.JSON(), nullable=True, comment="技术栈数组"),
        sa.Column("source", mysql.TINYINT(), nullable=False, server_default=sa.text("0"), comment="来源 0 AI 解析 1 人工"),
        *_common_columns(),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_talent", "talent_project_exp", ["tenant_id", "talent_id", "seq_no"])


def downgrade() -> None:
    op.drop_index("idx_tenant_talent", table_name="talent_project_exp")
    op.drop_table("talent_project_exp")
    op.drop_index("idx_tenant_talent", table_name="talent_work_exp")
    op.drop_table("talent_work_exp")
