"""Add frozen TBL-33 assessment paper question snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_paper_question",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="关系 ID",
        ),
        sa.Column(
            "paper_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="已发布试卷版本",
        ),
        sa.Column(
            "question_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="题目版本",
        ),
        sa.Column(
            "question_version",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="绑定时冻结的题目版本号",
        ),
        sa.Column(
            "question_snapshot",
            mysql.JSON(),
            nullable=False,
            comment="发布时的完整题目快照",
        ),
        sa.Column(
            "seq_no",
            mysql.SMALLINT(unsigned=True),
            nullable=False,
            comment="题目顺序",
        ),
        sa.Column(
            "score",
            mysql.DECIMAL(8, 2),
            nullable=False,
            comment="本题分值",
        ),
        sa.Column(
            "dimension_snapshot",
            mysql.JSON(),
            nullable=False,
            comment="维度和权重快照",
        ),
        sa.Column(
            "answer_rule_snapshot",
            mysql.JSON(),
            nullable=False,
            comment="客观评分规则快照",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_paper_question")),
        sa.UniqueConstraint("paper_id", "seq_no", name="uk_paper_question_seq"),
        sa.UniqueConstraint(
            "paper_id",
            "question_id",
            "question_version",
            name="uk_paper_question_version",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_paper_question_order",
        "assessment_paper_question",
        ["paper_id", "seq_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_assessment_paper_question_order",
        table_name="assessment_paper_question",
    )
    op.drop_table("assessment_paper_question")
