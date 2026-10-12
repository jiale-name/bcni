"""个人权利请求表：**TBL-30** privacy_request（CU-22）

Revision ID: 0007_privacy
Revises: 0006_dedup
Create Date: 2026-09-23

对应编码单元 **CU-22**（变更留痕 + 个人权利响应，F-3.10 / F-3.11 / SQ-5 / SM-3 / API-67~80）。

🔴 **本迁移新增一张上游未定义的表**（评审文档 §7.3 批次 13 已登记为上游不一致）：
接口 §2.2 把 API-73/76/80 的「涉及表」写成 TBL-22 ``talent_change_log``，
而该表 ``talent_id NOT NULL``、无请求状态 / 申请人 / 处理时限 / 驳回理由 ——
三类权利请求单（更正 / 删除 / 导出）与其状态机（SM-3）**无处安放**。
故按 TBL-29 的先例新增 **TBL-30**，编号延续既有分配规则（§0.2「编号一经分配永不复用」）。

⚠️ 与数据库设计文档 §4 的 DDL 无冲突（文档里根本没有这张表），属**新增**而非偏离。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0007_privacy"
down_revision: str | None = "0006_dedup"
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
        "privacy_request",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("request_no", sa.String(64), nullable=False, comment="请求号 PRVyyyymmddNNN"),
        sa.Column(
            "request_type", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="类型 0 更正 1 删除 2 导出",
        ),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="档案 ID（被请求的对象）"),
        sa.Column("applicant_user_id", sa.BigInteger(), nullable=False, comment="申请人（本人）"),
        sa.Column("field_code", sa.String(64), nullable=True, comment="更正字段（白名单内）"),
        sa.Column("current_value", sa.String(500), nullable=True, comment="提交时的当前值（乐观锁）"),
        sa.Column("expected_value", sa.String(500), nullable=True, comment="申请更正后的值"),
        sa.Column("review_field_id", sa.BigInteger(), nullable=True, comment="关联 TBL-16 字段行"),
        sa.Column("reason", sa.String(500), nullable=True, comment="理由"),
        sa.Column("attachments", sa.JSON(), nullable=True, comment="佐证材料文件 ID 数组"),
        sa.Column(
            "status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="状态 0 待处理 1 已通过 2 已驳回 3 处理中（内部态）",
        ),
        sa.Column(
            "sla_hours", sa.Integer(), nullable=False, server_default=sa.text("72"),
            comment="提交时刻生效的处理时限（快照）",
        ),
        sa.Column("idempotent_key", sa.String(64), nullable=True, comment="幂等键（API-77 强制幂等）"),
        sa.Column("processed_by", sa.BigInteger(), nullable=True, comment="处理人"),
        sa.Column("processed_at", sa.DateTime(), nullable=True, comment="处理时间"),
        sa.Column("reject_reason", sa.String(500), nullable=True, comment="驳回理由"),
        sa.Column("notified_at", sa.DateTime(), nullable=True, comment="通知本人时间"),
        sa.Column("notify_message", sa.String(500), nullable=True, comment="通知正文快照"),
        sa.Column("export_format", sa.String(16), nullable=True, comment="数据包格式"),
        sa.Column(
            "download_count", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="下载次数"
        ),
        sa.Column("last_download_at", sa.DateTime(), nullable=True, comment="最近下载时间"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"), comment="提交时间",
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "request_no", "is_deleted", name="uk_tenant_privacy_no"),
        sa.UniqueConstraint("tenant_id", "idempotent_key", "is_deleted", name="uk_tenant_privacy_idem"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_type_status", "privacy_request", ["tenant_id", "request_type", "status"])
    op.create_index("idx_tenant_talent", "privacy_request", ["tenant_id", "talent_id"])
    op.create_index("idx_tenant_applicant", "privacy_request", ["tenant_id", "applicant_user_id"])
    op.create_index("idx_tenant_created", "privacy_request", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_tenant_created", table_name="privacy_request")
    op.drop_index("idx_tenant_applicant", table_name="privacy_request")
    op.drop_index("idx_tenant_talent", table_name="privacy_request")
    op.drop_index("idx_tenant_type_status", table_name="privacy_request")
    op.drop_table("privacy_request")
