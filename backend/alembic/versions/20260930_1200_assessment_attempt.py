"""Create frozen TBL-35 assessment attempts."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_attempt",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="主键（雪花 ID，应用生成）",
        ),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("task_id", mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务"),
        sa.Column("talent_id", mysql.BIGINT(unsigned=True), nullable=False, comment="人才档案"),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答账号"),
        sa.Column(
            "paper_snapshot",
            mysql.JSON(),
            nullable=False,
            comment="开始作答时的试卷题目快照",
        ),
        sa.Column(
            "attempt_no",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            comment="候选人的第 N 次作答",
        ),
        sa.Column(
            "answer_version",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="答案版本",
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=3), nullable=True, comment="开始作答时间"),
        sa.Column(
            "duration_min_snapshot",
            mysql.SMALLINT(unsigned=True),
            nullable=False,
            comment="开始时冻结的作答时长",
        ),
        sa.Column(
            "deadline_at_snapshot",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="开始时冻结的截止时间",
        ),
        sa.Column("last_saved_at", mysql.DATETIME(fsp=3), nullable=True, comment="最近保存答案时间"),
        sa.Column("submitted_at", mysql.DATETIME(fsp=3), nullable=True, comment="提交时间"),
        sa.Column("status", mysql.VARCHAR(24), nullable=False, comment="作答状态"),
        sa.Column(
            "client_device_hash",
            mysql.CHAR(64),
            nullable=True,
            comment="客户端设备哈希",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_attempt")),
        sa.UniqueConstraint(
            "task_id", "user_id", "attempt_no", name="uk_attempt_task_user_no"
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_attempt_task_status_saved",
        "assessment_attempt",
        ["task_id", "status", "last_saved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attempt_task_status_saved",
        table_name="assessment_attempt",
    )
    op.drop_table("assessment_attempt")
