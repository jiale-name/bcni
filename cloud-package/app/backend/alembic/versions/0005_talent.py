"""人才档案域 7 表：TBL-11/16/18/19/20/21/22（CU-14 ~ CU-17）

Revision ID: 0005_talent
Revises: 0004_ai_agent
Create Date: 2026-09-22

对应编码单元 **CU-14 ~ CU-17**（导入 → 抽取 → 复核 → 标签，F-3.1 ~ F-3.4.1）。

与数据库设计文档 DDL 的**有意偏离**（评审文档批次 10 登记）：

1. ``talent_parse_field`` 增列：``impact_level``（影响面物化，队列高频过滤）、
   ``source_file`` / ``source_locator``（API-45 并排展示的承载列，DDL 缺失）；
2. ``import_task`` 增列：``parse_strategy`` / ``auto_dedup`` / ``auto_tag`` / ``remark``
   （API-36 表单字段，DDL 漏收）；
3. ``import_file`` 增列：``resume_id``（任务内序号；批次 5 遗留 ④ 要求保留
   resume_id → talent_id 映射，否则 N6 金标准评估无法对齐）、
   ``agent_task_id``（逐文件 Agent 任务的反查键 —— 一任务多文件 = 多 Agent 任务）；
4. ``talent_change_log`` **不分区**（量级 10⁶，12 个月留存；audit/ai_call_log 的
   10⁷ 量级才值得分区）—— 其「只追加」结构与审计日志同源（无软删列 + DAO 只读导出）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0005_talent"
down_revision: str | None = "0004_ai_agent"
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
    # ------------------------------------------------------------------ TBL-11
    op.create_table(
        "talent_base",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("user_id", sa.BigInteger(), nullable=True, comment="关联用户（外部候选人可空）"),
        sa.Column("org_id", sa.BigInteger(), nullable=True, comment="归属组织（行级权限字段）"),
        sa.Column("name", sa.String(64), nullable=False, comment="姓名"),
        sa.Column("gender", mysql.TINYINT(), nullable=True, comment="性别 0 未知 1 男 2 女"),
        sa.Column("birth_date", sa.Date(), nullable=True, comment="出生年月"),
        sa.Column("id_card_enc", mysql.VARBINARY(256), nullable=True, comment="身份证（AES-GCM 加密）"),
        sa.Column("id_card_hash", sa.CHAR(64), nullable=True, comment="身份证盐化哈希（精确去重）"),
        sa.Column("phone_enc", mysql.VARBINARY(256), nullable=True, comment="手机号（AES-GCM 加密）"),
        sa.Column("phone_hash", sa.CHAR(64), nullable=True, comment="手机号盐化哈希（精确去重）"),
        sa.Column("email_enc", mysql.VARBINARY(256), nullable=True, comment="邮箱（AES-GCM 加密）"),
        sa.Column("education_level", sa.String(32), nullable=True, comment="学历（dict_item 码）"),
        sa.Column("school", sa.String(128), nullable=True, comment="院校"),
        sa.Column("major", sa.String(128), nullable=True, comment="专业"),
        sa.Column("work_years", sa.Integer(), nullable=True, comment="工作年限"),
        sa.Column("profile_vec_id", sa.String(64), nullable=True, comment="Milvus 向量主键（CU-18）"),
        sa.Column(
            "data_status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="数据状态 0 待复核 1 有效 2 已合并 3 已归档 4 已删除",
        ),
        sa.Column("merged_into_id", sa.BigInteger(), nullable=True, comment="合并去向（F-3.5 回滚依据）"),
        sa.Column("parse_task_id", sa.BigInteger(), nullable=True, comment="来源导入任务 ID"),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default=sa.text("1"), comment="版本号（F-3.10 自增）"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id_card_hash", "is_deleted", name="uk_tenant_idcard"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_org", "talent_base", ["tenant_id", "org_id"])
    op.create_index("idx_tenant_status", "talent_base", ["tenant_id", "data_status"])
    op.create_index("idx_tenant_created", "talent_base", ["tenant_id", "created_at"])
    op.create_index("idx_tenant_phone", "talent_base", ["tenant_id", "phone_hash"])
    op.create_index("idx_parse_task", "talent_base", ["parse_task_id"])

    # ------------------------------------------------------------------ TBL-16
    op.create_table(
        "talent_parse_field",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="人才 ID"),
        sa.Column("field_name", sa.String(64), nullable=False, comment="字段名"),
        sa.Column("raw_text", sa.String(512), nullable=True, comment="原文片段"),
        sa.Column("parsed_value", sa.String(512), nullable=True, comment="抽取值"),
        sa.Column("confidence", mysql.DECIMAL(4, 3), nullable=True, comment="置信度 0.000~1.000"),
        sa.Column("impact_level", sa.String(8), nullable=False, server_default="LOW", comment="影响面 HIGH/LOW"),
        sa.Column("extract_model", sa.String(64), nullable=True, comment="抽取器标识与版本"),
        sa.Column("source_file", sa.String(255), nullable=True, comment="来源文件名（API-45 并排展示）"),
        sa.Column("source_locator", sa.String(128), nullable=True, comment="原文定位（如 page=1,block=3）"),
        sa.Column(
            "review_status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="复核状态 0 待复核 1 已确认 2 已修正 3 免复核",
        ),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True, comment="复核人"),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True, comment="复核时间"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_talent_field", "talent_parse_field", ["tenant_id", "talent_id", "field_name"])
    op.create_index("idx_tenant_review", "talent_parse_field", ["tenant_id", "review_status"])
    op.create_index("idx_tenant_talent_conf", "talent_parse_field", ["tenant_id", "talent_id", "confidence"])
    op.create_index(
        "idx_tenant_review_impact", "talent_parse_field",
        ["tenant_id", "review_status", "impact_level", "confidence"],
    )

    # ------------------------------------------------------------------ TBL-18
    op.create_table(
        "talent_tag_dict",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("tag_code", sa.String(64), nullable=False, comment="标签编码（大写+下划线）"),
        sa.Column("tag_name", sa.String(64), nullable=False, comment="标签名"),
        sa.Column("category_l1", sa.String(64), nullable=True, comment="一级分类"),
        sa.Column("category_l2", sa.String(64), nullable=True, comment="二级分类"),
        sa.Column("category_l3", sa.String(64), nullable=True, comment="三级分类"),
        sa.Column("synonyms", sa.JSON(), nullable=True, comment="同义词数组（选词归一用）"),
        sa.Column(
            "status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="状态 0 待审核 1 启用 2 停用",
        ),
        sa.Column("source", mysql.TINYINT(), nullable=False, server_default=sa.text("0"), comment="来源 0 人工 1 AI 建议"),
        sa.Column("suggested_by", sa.BigInteger(), nullable=True, comment="建议人（AI 触发为空）"),
        sa.Column("approved_by", sa.BigInteger(), nullable=True, comment="审核人"),
        sa.Column("approved_at", sa.DateTime(), nullable=True, comment="审核通过时间（未过审不得被引用）"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tag_code", "is_deleted", name="uk_tenant_tag_code"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_status", "talent_tag_dict", ["tenant_id", "status"])
    op.create_index("idx_tenant_category", "talent_tag_dict", ["tenant_id", "category_l1", "category_l2"])

    # ------------------------------------------------------------------ TBL-19
    op.create_table(
        "talent_tag_rel",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="人才 ID"),
        sa.Column("tag_code", sa.String(64), nullable=False, comment="标签编码（必须是启用项）"),
        sa.Column("confidence", mysql.DECIMAL(4, 3), nullable=True, comment="打标置信度（AI 打标才有）"),
        sa.Column("source", mysql.TINYINT(), nullable=False, server_default=sa.text("0"), comment="来源 0 AI 解析 1 人工指定"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "talent_id", "tag_code", "is_deleted", name="uk_tenant_talent_tag"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_tag", "talent_tag_rel", ["tenant_id", "tag_code"])

    # ------------------------------------------------------------------ TBL-20
    op.create_table(
        "import_task",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("task_no", sa.String(64), nullable=False, comment="任务号 IMPyyyymmddNNN"),
        sa.Column("source", mysql.TINYINT(), nullable=False, server_default=sa.text("0"), comment="来源 0 Web 上传 1 离线目录 2 API"),
        sa.Column("parse_strategy", sa.String(16), nullable=True, comment="解析策略 auto/ocr_force/text_only"),
        sa.Column("auto_dedup", mysql.TINYINT(1), nullable=False, server_default=sa.text("1"), comment="解析后自动查重"),
        sa.Column("auto_tag", mysql.TINYINT(1), nullable=False, server_default=sa.text("1"), comment="自动打标签"),
        sa.Column("remark", sa.String(200), nullable=True, comment="备注"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="文件总数"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="成功数"),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="失败数"),
        sa.Column(
            "status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="状态 0 待处理 1 解析中 2 已完成 3 部分失败 4 失败",
        ),
        sa.Column("idempotent_key", sa.String(64), nullable=True, comment="幂等键（API-36 强制）"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="发起人"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.Column("error_msg", sa.String(512), nullable=True, comment="整体失败原因"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "task_no", "is_deleted", name="uk_tenant_task_no"),
        sa.UniqueConstraint("tenant_id", "idempotent_key", "is_deleted", name="uk_tenant_idem"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_status", "import_task", ["tenant_id", "status"])
    op.create_index("idx_tenant_created", "import_task", ["tenant_id", "created_at"])

    # ------------------------------------------------------------------ TBL-21
    op.create_table(
        "import_file",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("task_id", sa.BigInteger(), nullable=False, comment="导入任务 ID"),
        sa.Column("resume_id", sa.Integer(), nullable=False, comment="文件序号（本任务内从 1 起；金标准对齐键）"),
        sa.Column("file_name", sa.String(255), nullable=False, comment="文件名"),
        sa.Column("file_ext", sa.String(16), nullable=True, comment="扩展名"),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="文件大小（字节）"),
        sa.Column("file_hash", sa.CHAR(64), nullable=True, comment="文件 SHA-256 指纹"),
        sa.Column("storage_path", sa.String(512), nullable=True, comment="存储路径（MinIO/本地）"),
        sa.Column(
            "parse_status", mysql.TINYINT(), nullable=False, server_default=sa.text("0"),
            comment="解析状态 0 待解析 1 成功 2 失败",
        ),
        sa.Column("parse_error", sa.String(512), nullable=True, comment="单文件失败原因（原因码）"),
        sa.Column("talent_id", sa.BigInteger(), nullable=True, comment="解析生成的档案 ID"),
        sa.Column("agent_task_id", sa.BigInteger(), nullable=True, comment="本文件的 Agent 任务 ID"),
        sa.Column("ocr_used", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="是否走 OCR（图片/扫描件）"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0"), comment="软删除 0 否 1 是"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "task_id", "file_hash", "is_deleted", name="uk_tenant_task_hash"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_task", "import_file", ["tenant_id", "task_id", "parse_status"])
    op.create_index("idx_talent", "import_file", ["talent_id"])

    # ------------------------------------------------------------------ TBL-22
    # 只追加：无 is_deleted / updated_at（同 audit_log 的结构保证，量级 10⁶ 不分区）
    op.create_table(
        "talent_change_log",
        _snowflake_pk(),
        _tenant_col(),
        sa.Column("talent_id", sa.BigInteger(), nullable=False, comment="人才 ID"),
        sa.Column("change_source", mysql.TINYINT(), nullable=False, comment="来源 0 AI 解析 1 人工复核 2 合并 3 重解析"),
        sa.Column("field_name", sa.String(64), nullable=True, comment="变更字段（整体变更可空）"),
        sa.Column("before_value", sa.String(512), nullable=True, comment="变更前值"),
        sa.Column("after_value", sa.String(512), nullable=True, comment="变更后值"),
        sa.Column("snapshot_ref", sa.String(512), nullable=True, comment="完整快照引用（大范围变更）"),
        sa.Column("operator_id", sa.BigInteger(), nullable=True, comment="操作人（AI 触发记系统账号）"),
        sa.Column("task_id", sa.BigInteger(), nullable=True, comment="关联 Agent 任务或导入任务 ID"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="变更时间"),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_talent_time", "talent_change_log", ["tenant_id", "talent_id", "created_at"])
    op.create_index("idx_tenant_source", "talent_change_log", ["tenant_id", "change_source"])


def downgrade() -> None:
    op.drop_index("idx_tenant_source", table_name="talent_change_log")
    op.drop_index("idx_tenant_talent_time", table_name="talent_change_log")
    op.drop_table("talent_change_log")
    op.drop_index("idx_talent", table_name="import_file")
    op.drop_index("idx_tenant_task", table_name="import_file")
    op.drop_table("import_file")
    op.drop_index("idx_tenant_created", table_name="import_task")
    op.drop_index("idx_tenant_status", table_name="import_task")
    op.drop_table("import_task")
    op.drop_index("idx_tenant_tag", table_name="talent_tag_rel")
    op.drop_table("talent_tag_rel")
    op.drop_index("idx_tenant_category", table_name="talent_tag_dict")
    op.drop_index("idx_tenant_status", table_name="talent_tag_dict")
    op.drop_table("talent_tag_dict")
    op.drop_index("idx_tenant_review_impact", table_name="talent_parse_field")
    op.drop_index("idx_tenant_talent_conf", table_name="talent_parse_field")
    op.drop_index("idx_tenant_review", table_name="talent_parse_field")
    op.drop_index("idx_tenant_talent_field", table_name="talent_parse_field")
    op.drop_table("talent_parse_field")
    op.drop_index("idx_parse_task", table_name="talent_base")
    op.drop_index("idx_tenant_phone", table_name="talent_base")
    op.drop_index("idx_tenant_created", table_name="talent_base")
    op.drop_index("idx_tenant_status", table_name="talent_base")
    op.drop_index("idx_tenant_org", table_name="talent_base")
    op.drop_table("talent_base")
