"""Batch 1 assessment database tables and constraints."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "85b0b1990abe"
down_revision: str | None = "0008_talent_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "assessment_event_review",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("org_id", mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围"),
        sa.Column("event_id", mysql.BIGINT(unsigned=True), nullable=False, comment="异常事件"),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"),
        sa.Column("decision", sa.String(24), nullable=False, comment="复核决定"),
        sa.Column("comment", sa.String(2000), nullable=False, comment="核查意见"),
        sa.Column("reviewer_id", mysql.BIGINT(unsigned=True), nullable=False, comment="核查人"),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=3), nullable=False, comment="核查时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_event_review")),
        sa.UniqueConstraint("event_id", "reviewer_id", "reviewed_at", name="uk_event_review"),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_event_review_event",
        "assessment_event_review",
        ["event_id", "reviewed_at"],
        unique=False,
    )

    op.create_table(
        "assessment_monitor_rule",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("org_id", mysql.BIGINT(unsigned=True), nullable=True, comment="组织范围"),
        sa.Column("rule_code", sa.String(64), nullable=False, comment="规则编码"),
        sa.Column("event_type", sa.String(32), nullable=False, comment="监控事件类型"),
        sa.Column("threshold_config", mysql.JSON(), nullable=False, comment="阈值和聚合窗口"),
        sa.Column("enabled", mysql.TINYINT(1), server_default=sa.text("1"), nullable=False, comment="是否启用"),
        sa.Column("version_no", mysql.INTEGER(unsigned=True), server_default=sa.text("1"), nullable=False, comment="规则版本"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False, comment="创建时间"),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"), nullable=False, comment="更新时间"),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=False, comment="修改人"),
        sa.Column("row_version", mysql.INTEGER(unsigned=True), server_default=sa.text("1"), nullable=False, comment="乐观锁版本"),
        sa.Column("deleted_at", mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_monitor_rule")),
        sa.UniqueConstraint("tenant_id", "org_id", "rule_code", "version_no", name="uk_monitor_rule_scope"),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_monitor_rule_scope",
        "assessment_monitor_rule",
        ["tenant_id", "org_id", "event_type", "enabled"],
        unique=False,
    )

    op.create_table(
        "assessment_notification",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("task_id", mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务"),
        sa.Column("task_candidate_id", mysql.BIGINT(unsigned=True), nullable=False, comment="候选人分配"),
        sa.Column("channel", sa.String(16), nullable=False, comment="通知渠道"),
        sa.Column("notification_type", sa.String(24), nullable=False, comment="通知类型"),
        sa.Column("status", sa.String(16), nullable=False, comment="投递状态"),
        sa.Column("idempotency_key", sa.String(128), nullable=False, comment="通知幂等键"),
        sa.Column("sent_at", mysql.DATETIME(fsp=3), nullable=True, comment="发送时间"),
        sa.Column("failure_reason", sa.String(1000), nullable=True, comment="失败原因"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_notification")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uk_notification_idempotency"),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_notification_delivery",
        "assessment_notification",
        ["task_candidate_id", "status", "channel"],
        unique=False,
    )

    op.create_table(
        "assessment_score",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"),
        sa.Column("report_id", mysql.BIGINT(unsigned=True), nullable=True, comment="所属报告版本，可在报告生成后关联"),
        sa.Column("dimension_id", mysql.BIGINT(unsigned=True), nullable=True, comment="能力维度，NULL 表示总分"),
        sa.Column("score_key", sa.String(64), nullable=False, comment="维度编码或 __total__"),
        sa.Column("score", mysql.DECIMAL(8, 2), nullable=False, comment="分数"),
        sa.Column("max_score", mysql.DECIMAL(8, 2), nullable=False, comment="满分"),
        sa.Column("score_source", sa.String(24), nullable=False, comment="评分来源"),
        sa.Column("rule_version", sa.String(64), nullable=False, comment="规则版本"),
        sa.Column("calculation_snapshot", mysql.JSON(), nullable=False, comment="计算输入和明细"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False, comment="创建时间"),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"), nullable=False, comment="更新时间"),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=True, comment="更新人"),
        sa.Column("row_version", mysql.INTEGER(unsigned=True), server_default=sa.text("1"), nullable=False, comment="乐观锁版本"),
        sa.Column("deleted_at", mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_score")),
        sa.UniqueConstraint("attempt_id", "rule_version", "score_key", name="uk_score_attempt_rule_key"),
        **_TABLE_KW,
    )

    op.create_table(
        "assessment_task",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("org_id", mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围"),
        sa.Column("task_code", sa.String(64), nullable=False, comment="任务编号"),
        sa.Column("name", sa.String(200), nullable=False, comment="任务名称"),
        sa.Column("paper_id", mysql.BIGINT(unsigned=True), nullable=False, comment="已发布试卷"),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False, comment="开始时间"),
        sa.Column("deadline_at", mysql.DATETIME(fsp=3), nullable=False, comment="截止时间"),
        sa.Column("duration_min", mysql.SMALLINT(unsigned=True), nullable=False, comment="本任务作答时长"),
        sa.Column("attempt_limit", mysql.TINYINT(unsigned=True), server_default=sa.text("1"), nullable=False, comment="最大作答次数"),
        sa.Column("target_scope", mysql.JSON(), nullable=False, comment="候选人、部门或岗位范围"),
        sa.Column("notify_rules", mysql.JSON(), nullable=False, comment="通知规则"),
        sa.Column("status", sa.String(24), nullable=False, comment="任务状态"),
        sa.Column("withdrawn_at", mysql.DATETIME(fsp=3), nullable=True, comment="撤回时间"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False, comment="创建时间"),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"), nullable=False, comment="更新时间"),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=True, comment="更新人"),
        sa.Column("row_version", mysql.INTEGER(unsigned=True), server_default=sa.text("1"), nullable=False, comment="乐观锁版本"),
        sa.Column("deleted_at", mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"),
        sa.CheckConstraint("start_at < deadline_at", name=op.f("ck_assessment_task_ck_assessment_task_time")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_task")),
        sa.UniqueConstraint("tenant_id", "task_code", name="uk_task_code"),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_task_tenant_org_status",
        "assessment_task",
        ["tenant_id", "org_id", "status", "start_at"],
        unique=False,
    )

    op.create_table(
        "assessment_task_candidate",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("org_id", mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围"),
        sa.Column("task_id", mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务"),
        sa.Column("talent_id", mysql.BIGINT(unsigned=True), nullable=False, comment="人才档案"),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False, comment="作答账号"),
        sa.Column("assignment_status", sa.String(24), nullable=False, comment="分配状态"),
        sa.Column("attempt_count", mysql.TINYINT(unsigned=True), server_default=sa.text("0"), nullable=False, comment="已作答次数"),
        sa.Column("progress", mysql.DECIMAL(5, 2), server_default=sa.text("0"), nullable=False, comment="作答进度百分比"),
        sa.Column("abnormal_count", mysql.INTEGER(unsigned=True), server_default=sa.text("0"), nullable=False, comment="异常事件数量"),
        sa.Column("deadline_at", mysql.DATETIME(fsp=3), nullable=False, comment="候选人最终截止时间"),
        sa.Column("reminder_status", sa.String(24), server_default=sa.text("'none'"), nullable=False, comment="提醒状态"),
        sa.Column("last_reminded_at", mysql.DATETIME(fsp=3), nullable=True, comment="最近提醒时间"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False, comment="创建时间"),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"), nullable=False, comment="更新时间"),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=True, comment="更新人"),
        sa.Column("row_version", mysql.INTEGER(unsigned=True), server_default=sa.text("1"), nullable=False, comment="乐观锁版本"),
        sa.Column("deleted_at", mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_task_candidate")),
        sa.UniqueConstraint("task_id", "user_id", name="uk_task_candidate"),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_assessment_task_candidate_progress",
        "assessment_task_candidate",
        ["task_id", "assignment_status", "deadline_at"],
        unique=False,
    )

    op.create_table(
        "outbox_event",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=False, nullable=False, comment="主键（雪花 ID，应用生成）"),
        sa.Column("tenant_id", mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"),
        sa.Column("aggregate_type", sa.String(64), nullable=False, comment="聚合类型"),
        sa.Column("aggregate_id", mysql.BIGINT(unsigned=True), nullable=False, comment="聚合 ID"),
        sa.Column("event_type", sa.String(64), nullable=False, comment="事件类型"),
        sa.Column("payload", mysql.JSON(), nullable=False, comment="脱敏事件内容"),
        sa.Column("idempotency_key", sa.String(128), nullable=False, comment="事件幂等键"),
        sa.Column("status", sa.String(16), nullable=False, comment="投递状态"),
        sa.Column("retry_count", mysql.INTEGER(unsigned=True), server_default=sa.text("0"), nullable=False, comment="重试次数"),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=3), nullable=True, comment="下次重试时间"),
        sa.Column("published_at", mysql.DATETIME(fsp=3), nullable=True, comment="发布时间"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uk_outbox_idempotency"),
        **_TABLE_KW,
    )
    op.create_index("idx_outbox_delivery", "outbox_event", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_outbox_delivery", table_name="outbox_event")
    op.drop_table("outbox_event")
    op.drop_index("idx_assessment_task_candidate_progress", table_name="assessment_task_candidate")
    op.drop_table("assessment_task_candidate")
    op.drop_index("idx_assessment_task_tenant_org_status", table_name="assessment_task")
    op.drop_table("assessment_task")
    op.drop_table("assessment_score")
    op.drop_index("idx_assessment_notification_delivery", table_name="assessment_notification")
    op.drop_table("assessment_notification")
    op.drop_index("idx_assessment_monitor_rule_scope", table_name="assessment_monitor_rule")
    op.drop_table("assessment_monitor_rule")
    op.drop_index("idx_assessment_event_review_event", table_name="assessment_event_review")
    op.drop_table("assessment_event_review")
