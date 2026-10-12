"""Create frozen TBL-36 current assessment answers."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_answer",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="主键（雪花 ID，应用生成）",
        ),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"),
        sa.Column(
            "paper_question_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="试卷题目快照",
        ),
        sa.Column(
            "answer_version",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="当前答案对应的 Attempt 版本",
        ),
        sa.Column("answer", mysql.JSON(), nullable=True, comment="当前答案"),
        sa.Column("answered_at", mysql.DATETIME(fsp=3), nullable=True, comment="作答时间"),
        sa.Column(
            "objective_score",
            mysql.DECIMAL(8, 2),
            nullable=True,
            comment="规则评分结果",
        ),
        sa.Column(
            "subjective_ai_score",
            mysql.DECIMAL(8, 2),
            nullable=True,
            comment="AI 建议分",
        ),
        sa.Column(
            "final_score",
            mysql.DECIMAL(8, 2),
            nullable=True,
            comment="人工确认分",
        ),
        sa.Column("score_source", mysql.VARCHAR(24), nullable=True, comment="评分来源"),
        sa.Column("review_status", mysql.VARCHAR(24), nullable=False, comment="复核状态"),
        sa.Column(
            "review_comment",
            mysql.VARCHAR(1000),
            nullable=True,
            comment="人工复核意见",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_answer")),
        sa.UniqueConstraint(
            "attempt_id",
            "paper_question_id",
            name="uk_attempt_question_answer",
        ),
        **_TABLE_KW,
    )


def downgrade() -> None:
    op.drop_table("assessment_answer")
