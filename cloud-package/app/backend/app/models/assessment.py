"""Batch 1 database models for the talent assessment domain.

These models intentionally do not inherit the existing generic mixins.  The
frozen assessment database contract uses unsigned MySQL identifiers and
millisecond datetime columns, while the legacy mixins use signed identifiers
and ordinary datetime columns.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.snowflake import next_id
from app.models.base import Base, MYSQL_TABLE_KWARGS


def _id_column() -> Mapped[int]:
    return mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        default=next_id,
        comment="主键（雪花 ID，应用生成）",
    )


class _AssessmentLifecycleColumns:
    """The database document's common mutable business columns."""

    tenant_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围")
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"), comment="创建时间"
    )
    created_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="创建人"
    )
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
        comment="更新时间",
    )
    updated_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="更新人"
    )
    row_version: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, default=1, server_default=text("1"), comment="乐观锁版本"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="软删除时间"
    )


class AssessmentPaper(_AssessmentLifecycleColumns, Base):
    """TBL-32 assessment paper draft and published version."""

    __tablename__ = "assessment_paper"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "paper_code", "version_no", name="uk_paper_code_version"
        ),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    org_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围"
    )
    paper_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="试卷编号")
    version_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="试卷版本"
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="试卷名称")
    paper_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="试卷类型")
    target_level: Mapped[str] = mapped_column(String(64), nullable=False, comment="适用岗位层级")
    duration_min: Mapped[int] = mapped_column(
        mysql.SMALLINT(unsigned=True), nullable=False, comment="作答时长"
    )
    total_score: Mapped[float] = mapped_column(
        mysql.DECIMAL(8, 2), nullable=False, comment="总分"
    )
    random_seed: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="随机卷发布后冻结"
    )
    rules: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="组卷、评分和配额规则快照"
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, comment="试卷状态")
    published_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="发布时间"
    )
    snapshot_hash: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, comment="发布快照哈希"
    )


class AssessmentPaperQuestion(Base):
    """TBL-33 published paper question snapshot."""

    __tablename__ = "assessment_paper_question"
    __table_args__ = (
        UniqueConstraint(
            "paper_id", "seq_no", name="uk_paper_question_seq"
        ),
        UniqueConstraint(
            "paper_id",
            "question_id",
            "question_version",
            name="uk_paper_question_version",
        ),
        Index("idx_assessment_paper_question_order", "paper_id", "seq_no"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    paper_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="试卷版本"
    )
    question_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="题目版本"
    )
    question_version: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="题目版本号"
    )
    question_snapshot: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="完整题目快照"
    )
    seq_no: Mapped[int] = mapped_column(
        mysql.SMALLINT(unsigned=True), nullable=False, comment="题目顺序"
    )
    score: Mapped[float] = mapped_column(
        mysql.DECIMAL(8, 2), nullable=False, comment="本题分值"
    )
    dimension_snapshot: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="维度和权重快照"
    )
    answer_rule_snapshot: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="客观评分规则快照"
    )


class AssessmentAttempt(Base):
    """TBL-35 attempt snapshot and answer lifecycle state."""

    __tablename__ = "assessment_attempt"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "user_id", "attempt_no", name="uk_attempt_task_user_no"
        ),
        Index(
            "idx_attempt_task_status_saved",
            "task_id",
            "status",
            "last_saved_at",
        ),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    tenant_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"
    )
    task_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务"
    )
    talent_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="人才档案"
    )
    user_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="作答账号"
    )
    paper_snapshot: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="开始作答时的试卷题目快照"
    )
    attempt_no: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True), nullable=False, comment="候选人的第 N 次作答"
    )
    answer_version: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="答案版本",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="开始作答时间"
    )
    duration_min_snapshot: Mapped[int] = mapped_column(
        mysql.SMALLINT(unsigned=True), nullable=False, comment="开始时冻结的作答时长"
    )
    deadline_at_snapshot: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=False, comment="开始时冻结的截止时间"
    )
    last_saved_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="最近保存答案时间"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="提交时间"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="作答状态"
    )
    client_device_hash: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, comment="客户端设备哈希"
    )


class AssessmentAnswer(Base):
    """TBL-36 current answer and scoring fields for one paper question."""

    __tablename__ = "assessment_answer"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "paper_question_id",
            name="uk_attempt_question_answer",
        ),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    attempt_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"
    )
    paper_question_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="试卷题目快照"
    )
    answer_version: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="当前答案对应的 Attempt 版本"
    )
    answer: Mapped[dict | None] = mapped_column(
        mysql.JSON(), nullable=True, comment="当前答案"
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="作答时间"
    )
    objective_score: Mapped[float | None] = mapped_column(
        mysql.DECIMAL(8, 2), nullable=True, comment="规则评分结果"
    )
    subjective_ai_score: Mapped[float | None] = mapped_column(
        mysql.DECIMAL(8, 2), nullable=True, comment="AI 建议分"
    )
    final_score: Mapped[float | None] = mapped_column(
        mysql.DECIMAL(8, 2), nullable=True, comment="人工确认分"
    )
    score_source: Mapped[str | None] = mapped_column(
        String(24), nullable=True, comment="评分来源"
    )
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="复核状态"
    )
    review_comment: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="人工复核意见"
    )


class AssessmentEvent(Base):
    """TBL-37 client assessment monitoring event."""

    __tablename__ = "assessment_event"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "attempt_id",
            "client_event_id",
            name="uk_attempt_client_event",
        ),
        Index(
            "idx_attempt_event_received",
            "attempt_id",
            "received_at",
            "event_type",
        ),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    tenant_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围"
    )
    attempt_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例"
    )
    client_event_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="客户端事件幂等标识"
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="监控事件类型"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=False, comment="客户端事件时间"
    )
    received_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=False, comment="服务端接收时间"
    )
    duration_ms: Mapped[int | None] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=True, comment="事件持续时长"
    )
    metadata_summary: Mapped[dict] = mapped_column(
        mysql.JSON(), nullable=False, comment="脱敏事件元数据摘要"
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="事件严重程度"
    )
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="事件复核状态"
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="复核人"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="复核时间"
    )


class AssessmentTask(_AssessmentLifecycleColumns, Base):
    """TBL-34 assessment task."""

    __tablename__ = "assessment_task"
    __table_args__ = (
        CheckConstraint("start_at < deadline_at", name="ck_assessment_task_time"),
        UniqueConstraint("tenant_id", "task_code", name="uk_task_code"),
        Index("idx_assessment_task_tenant_org_status", "tenant_id", "org_id", "status", "start_at"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    org_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围")
    task_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务编号")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务名称")
    paper_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="已发布试卷")
    start_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=3), nullable=False, comment="开始时间")
    deadline_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=3), nullable=False, comment="截止时间")
    duration_min: Mapped[int] = mapped_column(
        mysql.SMALLINT(unsigned=True), nullable=False, comment="本任务作答时长"
    )
    attempt_limit: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True), nullable=False, default=1, server_default=text("1"), comment="最大作答次数"
    )
    target_scope: Mapped[dict] = mapped_column(mysql.JSON(), nullable=False, comment="候选人、部门或岗位范围")
    notify_rules: Mapped[dict] = mapped_column(mysql.JSON(), nullable=False, comment="通知规则")
    status: Mapped[str] = mapped_column(String(24), nullable=False, comment="任务状态")
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="撤回时间"
    )


class AssessmentScore(_AssessmentLifecycleColumns, Base):
    """TBL-38 assessment score snapshot."""

    __tablename__ = "assessment_score"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "rule_version", "score_key", name="uk_score_attempt_rule_key"
        ),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    attempt_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例")
    report_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="所属报告版本，可在报告生成后关联"
    )
    dimension_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="能力维度，NULL 表示总分"
    )
    score_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="维度编码或 __total__")
    score: Mapped[float] = mapped_column(mysql.DECIMAL(8, 2), nullable=False, comment="分数")
    max_score: Mapped[float] = mapped_column(mysql.DECIMAL(8, 2), nullable=False, comment="满分")
    score_source: Mapped[str] = mapped_column(String(24), nullable=False, comment="评分来源")
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, comment="规则版本")
    calculation_snapshot: Mapped[dict] = mapped_column(mysql.JSON(), nullable=False, comment="计算输入和明细")


class AssessmentTaskCandidate(_AssessmentLifecycleColumns, Base):
    """TBL-43 task candidate assignment and progress."""

    __tablename__ = "assessment_task_candidate"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uk_task_candidate"),
        Index("idx_assessment_task_candidate_progress", "task_id", "assignment_status", "deadline_at"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    org_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围")
    task_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务")
    talent_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="人才档案")
    user_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="作答账号")
    assignment_status: Mapped[str] = mapped_column(String(24), nullable=False, comment="分配状态")
    attempt_count: Mapped[int] = mapped_column(
        mysql.TINYINT(unsigned=True), nullable=False, default=0, server_default=text("0"), comment="已作答次数"
    )
    progress: Mapped[float] = mapped_column(
        mysql.DECIMAL(5, 2), nullable=False, default=0, server_default=text("0.00"), comment="作答进度百分比"
    )
    abnormal_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, default=0, server_default=text("0"), comment="异常事件数量"
    )
    deadline_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=3), nullable=False, comment="候选人最终截止时间")
    reminder_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="none", server_default=text("'none'"), comment="提醒状态"
    )
    last_reminded_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="最近提醒时间"
    )


class AssessmentMonitorRule(_AssessmentLifecycleColumns, Base):
    """TBL-44 monitoring rule configuration."""

    __tablename__ = "assessment_monitor_rule"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "org_id", "rule_code", "version_no", name="uk_monitor_rule_scope"
        ),
        Index("idx_assessment_monitor_rule_scope", "tenant_id", "org_id", "event_type", "enabled"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    org_id: Mapped[int | None] = mapped_column(mysql.BIGINT(unsigned=True), nullable=True, comment="组织范围")
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="规则编码")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="监控事件类型")
    threshold_config: Mapped[dict] = mapped_column(mysql.JSON(), nullable=False, comment="阈值和聚合窗口")
    enabled: Mapped[int] = mapped_column(
        mysql.TINYINT(1), nullable=False, default=1, server_default=text("1"), comment="是否启用"
    )
    version_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, default=1, server_default=text("1"), comment="规则版本"
    )
    updated_by: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="修改人"
    )


class AssessmentNotification(Base):
    """TBL-45 notification delivery record."""

    __tablename__ = "assessment_notification"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uk_notification_idempotency"),
        Index("idx_assessment_notification_delivery", "task_candidate_id", "status", "channel"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    tenant_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围")
    task_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="测评任务")
    task_candidate_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=False, comment="候选人分配"
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, comment="通知渠道")
    notification_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="通知类型")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="投递状态")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="通知幂等键")
    sent_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=3), nullable=True, comment="发送时间")
    failure_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="失败原因")


class OutboxEvent(Base):
    """TBL-46 shared outbox event."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uk_outbox_idempotency"),
        Index("idx_outbox_delivery", "status", "next_attempt_at"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    tenant_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围")
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="聚合类型")
    aggregate_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="聚合 ID")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件类型")
    payload: Mapped[dict] = mapped_column(mysql.JSON(), nullable=False, comment="脱敏事件内容")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="事件幂等键")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="投递状态")
    retry_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, default=0, server_default=text("0"), comment="重试次数"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=3), nullable=True, comment="下次重试时间"
    )
    published_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=3), nullable=True, comment="发布时间")


class AssessmentEventReview(Base):
    """TBL-42 append-only abnormal event review record."""

    __tablename__ = "assessment_event_review"
    __table_args__ = (
        UniqueConstraint("event_id", "reviewer_id", "reviewed_at", name="uk_event_review"),
        Index("idx_assessment_event_review_event", "event_id", "reviewed_at"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[int] = _id_column()
    tenant_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="租户范围")
    org_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="组织范围")
    event_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="异常事件")
    attempt_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="作答实例")
    decision: Mapped[str] = mapped_column(String(24), nullable=False, comment="复核决定")
    comment: Mapped[str] = mapped_column(String(2000), nullable=False, comment="核查意见")
    reviewer_id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), nullable=False, comment="核查人")
    reviewed_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=3), nullable=False, comment="核查时间")
