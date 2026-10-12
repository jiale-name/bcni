"""Add frozen TBL-32 assessment paper table."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "85b0b1990abe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_paper",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="主键（雪花 ID，应用生成）",
        ),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("org_id", mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围"),
        sa.Column("paper_code", sa.String(64), nullable=False, comment="试卷编号"),
        sa.Column("version_no", mysql.INTEGER(unsigned=True), nullable=False, comment="试卷版本"),
        sa.Column("name", sa.String(200), nullable=False, comment="试卷名称"),
        sa.Column("paper_type", sa.String(16), nullable=False, comment="试卷类型"),
        sa.Column("target_level", sa.String(64), nullable=False, comment="适用岗位层级"),
        sa.Column("duration_min", mysql.SMALLINT(unsigned=True), nullable=False, comment="作答时长"),
        sa.Column("total_score", mysql.DECIMAL(8, 2), nullable=False, comment="总分"),
        sa.Column("random_seed", sa.String(128), nullable=True, comment="随机卷发布后冻结"),
        sa.Column(
            "rules",
            mysql.JSON(),
            nullable=False,
            comment="组卷、评分和配额规则快照",
        ),
        sa.Column("status", sa.String(24), nullable=False, comment="试卷状态"),
        sa.Column("published_at", mysql.DATETIME(fsp=3), nullable=True, comment="发布时间"),
        sa.Column("snapshot_hash", mysql.CHAR(64), nullable=True, comment="发布快照哈希"),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=True, comment="更新人"),
        sa.Column(
            "row_version",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="乐观锁版本",
        ),
        sa.Column("deleted_at", mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_paper")),
        sa.UniqueConstraint(
            "tenant_id",
            "paper_code",
            "version_no",
            name="uk_paper_code_version",
        ),
        **_TABLE_KW,
    )


def downgrade() -> None:
    op.drop_table("assessment_paper")
