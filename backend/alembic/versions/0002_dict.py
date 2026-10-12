"""数据字典：TBL-09 ``dict_type`` / TBL-10 ``dict_item``

Revision ID: 0002_dict
Revises: 0001_basement
Create Date: 2026-09-22

对应编码单元 **CU-07**（数据字典与系统配置，F-1.7，接口 API-23 ~ API-27）。

🔴 迁移原则（数据库 §0 / 架构 §5）
--------------------------------
- **唯一事实来源是 ORM 模型**（:mod:`app.models.dict`），本文件由 ORM 结构导出；
- **``tenant_id`` 必须是所有联合索引的最左列**（原则 5）；
- **唯一约束必须带 ``is_deleted``**（原则 4）；
- 主键为雪花 ID（应用生成，有符号 BIGINT），❌ 不用 ``AUTO_INCREMENT``。

⚠️ 两个刻意的实现选择
--------------------
1. **``down_revision = "0001_basement"``** —— 本迁移是 0001 之后的**第一条**。
   之所以到 CU-07 才出现第二条，是因为 CU-03/04/05/06 都没有新增表
   （CU-05 的角色/权限表在 0001 就建好了，CU-06 只加了业务代码）。
   **不要**把 ``down_revision`` 直接指向 ``None``，否则迁移链会分叉成两条头。

2. **不为字典表加额外索引** —— 唯一的读路径是「按 (tenant_id, type_code) 取项」，
   由 ``idx_tenant_type`` 覆盖；字典量级只有 10¹/10²，多建索引只会拖慢写入。

⚠️ 已知潜在缺陷（本期不触发，留痕备查）
------------------------------------
``uk_tenant_type_code`` / ``uk_tenant_type_item`` 都带 ``is_deleted``。
MySQL 下 ``is_deleted`` 只有 0/1 两个取值，因此**同一编码最多只能有一条软删行**
（第二次软删会撞唯一键报 1062）。本期安全，因为字典项只「停用」不「软删」
（``is_deleted`` 恒为 0）。**若将来要支持真删除，必须先改这两个唯一键** ——
详见评审文档 §7.3 批次 4 遗留。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0002_dict"
down_revision: str | None = "0001_basement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: MySQL 表级参数（数据库 §0 原则 7：一律 utf8mb4 / utf8mb4_unicode_ci）
_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

#: 雪花 ID 主键列（应用生成，绝不用 AUTO_INCREMENT）
_ID = sa.BigInteger()
#: 租户列（数据库 §0 原则 5：联合索引最左列）
_TENANT = sa.BigInteger()
#: 软删标记
_IS_DELETED = mysql.TINYINT(display_width=1)
#: 状态列（0 停用 1 启用）
_STATUS = mysql.TINYINT(display_width=1)


def _snowflake_pk() -> sa.Column:
    return sa.Column(
        "id",
        _ID,
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="主键（雪花 ID，应用生成）",
    )


def _tenant_id() -> sa.Column:
    return sa.Column(
        "tenant_id",
        _TENANT,
        nullable=False,
        server_default=sa.text("0"),
        comment="租户 ID",
    )


def _is_deleted() -> sa.Column:
    return sa.Column(
        "is_deleted",
        _IS_DELETED,
        nullable=False,
        server_default=sa.text("0"),
        comment="软删除 0 否 1 是",
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


def _status() -> sa.Column:
    return sa.Column(
        "status",
        _STATUS,
        nullable=False,
        server_default=sa.text("1"),
        comment="状态 0 停用 1 启用",
    )


def upgrade() -> None:
    # ========================================================================
    # TBL-09 字典类型（扁平枚举码表，区别于受控标签词表）
    # ========================================================================
    op.create_table(
        "dict_type",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column(
            "type_code", sa.String(64), nullable=False, comment="类型编码，如 education_level"
        ),
        sa.Column("type_name", sa.String(128), nullable=False, comment="类型名"),
        sa.Column("remark", sa.String(255), nullable=True, comment="备注"),
        _status(),
        sa.UniqueConstraint("tenant_id", "type_code", "is_deleted", name="uk_tenant_type_code"),
        **_TABLE_KW,
    )

    # ========================================================================
    # TBL-10 字典项
    # ========================================================================
    op.create_table(
        "dict_item",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column("type_code", sa.String(64), nullable=False, comment="字典类型编码"),
        sa.Column(
            "item_code",
            sa.String(64),
            nullable=False,
            comment="枚举值（业务数据引用它，创建后不可修改）",
        ),
        sa.Column("item_name", sa.String(128), nullable=False, comment="显示名"),
        sa.Column("sort_no", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="排序"),
        sa.Column("parent_code", sa.String(64), nullable=True, comment="父项编码（支持多级）"),
        _status(),
        sa.UniqueConstraint(
            "tenant_id", "type_code", "item_code", "is_deleted", name="uk_tenant_type_item"
        ),
        **_TABLE_KW,
    )
    # 唯一读路径：按 (tenant_id, type_code) 取某类型的全部项（下拉框）
    op.create_index("idx_tenant_type", "dict_item", ["tenant_id", "type_code"])


def downgrade() -> None:
    op.drop_index("idx_tenant_type", table_name="dict_item")
    op.drop_table("dict_item")
    op.drop_table("dict_type")
