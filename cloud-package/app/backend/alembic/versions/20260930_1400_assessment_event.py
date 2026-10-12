"""Create frozen TBL-37 assessment monitoring events."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_event",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="主键（雪花 ID，应用生成）",
        ),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"),
        sa.Column(
            "client_event_id",
            mysql.VARCHAR(64),
            nullable=False,
            comment="客户端事件幂等标识",
        ),
        sa.Column("event_type", mysql.VARCHAR(32), nullable=False, comment="监控事件类型"),
        sa.Column(
            "occurred_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="客户端事件时间",
        ),
        sa.Column(
            "received_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="服务端接收时间",
        ),
        sa.Column(
            "duration_ms",
            mysql.INTEGER(unsigned=True),
            nullable=True,
            comment="事件持续时长",
        ),
        sa.Column(
            "metadata_summary",
            mysql.JSON(),
            nullable=False,
            comment="脱敏事件元数据摘要",
        ),
        sa.Column("severity", mysql.VARCHAR(16), nullable=False, comment="事件严重程度"),
        sa.Column("review_status", mysql.VARCHAR(24), nullable=False, comment="事件复核状态"),
        sa.Column(
            "reviewed_by",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="复核人",
        ),
        sa.Column(
            "reviewed_at",
            mysql.DATETIME(fsp=3),
            nullable=True,
            comment="复核时间",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_event")),
        sa.UniqueConstraint(
            "tenant_id",
            "attempt_id",
            "client_event_id",
            name="uk_attempt_client_event",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_attempt_event_received",
        "assessment_event",
        ["attempt_id", "received_at", "event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attempt_event_received",
        table_name="assessment_event",
    )
    op.drop_table("assessment_event")

