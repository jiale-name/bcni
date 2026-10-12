"""一期基座：TBL-01 tenant / TBL-02 sys_user / TBL-03 sys_role / TBL-04 sys_permission /
TBL-05 sys_user_role / TBL-06 sys_role_permission / TBL-07 org_dept / TBL-08 position

Revision ID: 0001_basement
Revises:
Create Date: 2026-09-21

对应编码单元 **CU-02**（ORM 基类 + Alembic 迁移链），范围＝架构 §4.1「基座·权限」8 张表。

🔴 迁移原则（数据库 §0 / 架构 §5）
--------------------------------
- **唯一事实来源是 ORM 模型**：本文件由 ORM 结构导出，禁止手工改库后再补迁移；
- **主键为雪花 ID**（应用生成，有符号 BIGINT），❌ 不用 ``AUTO_INCREMENT``；
- **``tenant_id`` 必须是所有联合索引的最左列**（原则 5）；
- **唯一约束必须带 ``is_deleted``**（原则 4：软删后仍可重建同名记录）；
- 时间列由 MySQL 维护 ``CURRENT_TIMESTAMP``，Python 侧 ``onupdate`` 仅作双保险。

⚠️ 本迁移**不含外键约束**：一期按「逻辑外键 + 应用层校验」实现（跨库 Milvus 无法建 FK，
   且软删与外键语义冲突），故此处不声明 ``ForeignKey``，与 ORM 模型保持一致。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0001_basement"
down_revision: str | None = None
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


def upgrade() -> None:
    # ========================================================================
    # TBL-01 租户（本表自身无 tenant_id —— D-10 多租户预留）
    # ========================================================================
    op.create_table(
        "tenant",
        _snowflake_pk(),
        sa.Column("tenant_code", sa.String(64), nullable=False, comment="租户编码"),
        sa.Column("tenant_name", sa.String(128), nullable=False, comment="单位名称"),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.Column(
            "config_json", mysql.JSON(), nullable=True, comment="租户级配置：出网策略/阈值等"
        ),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("tenant_code", name="uk_tenant_code"),
        **_TABLE_KW,
    )

    # ========================================================================
    # TBL-07 组织架构（≥5 级；dept_path 前缀索引支撑 MGR 行级权限）
    # ========================================================================
    op.create_table(
        "org_dept",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="父组织 ID（0 为根）",
        ),
        sa.Column("dept_code", sa.String(64), nullable=False, comment="组织编码"),
        sa.Column("dept_name", sa.String(128), nullable=False, comment="组织名称"),
        sa.Column(
            "dept_level",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="层级（1 起，支持 ≥5 级）",
        ),
        sa.Column(
            "dept_path", sa.String(512), nullable=True, comment="祖级路径，如 /1/12/125/（子树查询用）"
        ),
        sa.Column("leader_user_id", sa.BigInteger(), nullable=True, comment="负责人用户 ID"),
        sa.Column(
            "sort_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="排序",
        ),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.UniqueConstraint("tenant_id", "dept_code", "is_deleted", name="uk_tenant_dept_code"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_parent", "org_dept", ["tenant_id", "parent_id"])
    # 前缀索引 191：dept_path 为 VARCHAR(512)，utf8mb4 下全长索引超键长上限
    op.create_index(
        "idx_tenant_path", "org_dept", ["tenant_id", "dept_path"], mysql_length={"dept_path": 191}
    )

    # ========================================================================
    # TBL-08 岗位
    # ========================================================================
    op.create_table(
        "position",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column("position_code", sa.String(64), nullable=False, comment="岗位编码"),
        sa.Column("position_name", sa.String(128), nullable=False, comment="岗位名称"),
        sa.Column("dept_id", sa.BigInteger(), nullable=True, comment="所属组织 ID"),
        sa.Column("position_level", mysql.TINYINT(display_width=1), nullable=True, comment="岗位层级"),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.UniqueConstraint(
            "tenant_id", "position_code", "is_deleted", name="uk_tenant_position_code"
        ),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_dept", "position", ["tenant_id", "dept_id"])

    # ========================================================================
    # TBL-02 用户账号（locked_until / login_fail_count 是 F-1.2 的权威字段）
    # ========================================================================
    op.create_table(
        "sys_user",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column("username", sa.String(64), nullable=False, comment="登录名"),
        sa.Column("password_hash", sa.String(100), nullable=False, comment="密码 bcrypt 哈希"),
        sa.Column("real_name", sa.String(64), nullable=True, comment="姓名"),
        sa.Column("org_id", sa.BigInteger(), nullable=True, comment="归属组织（行级权限字段）"),
        sa.Column("position_id", sa.BigInteger(), nullable=True, comment="岗位 ID"),
        # 🔴 敏感字段 AES 加密后存 VARBINARY（数据库 §0 原则 10）
        sa.Column("phone_enc", mysql.VARBINARY(256), nullable=True, comment="手机号（AES 加密）"),
        sa.Column("email_enc", mysql.VARBINARY(256), nullable=True, comment="邮箱（AES 加密）"),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 禁用 1 启用",
        ),
        sa.Column(
            "login_fail_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="连续登录失败次数",
        ),
        sa.Column(
            "locked_until", sa.DateTime(), nullable=True, comment="锁定截止时间（连错 5 次锁 10 分钟）"
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True, comment="最近登录时间"),
        # ⚠️ 唯一索引必须带 is_deleted，否则软删后无法重建同名账号
        sa.UniqueConstraint("tenant_id", "username", "is_deleted", name="uk_tenant_username"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_org", "sys_user", ["tenant_id", "org_id"])

    # ========================================================================
    # TBL-03 角色（data_scope 是行级 + 字段级裁剪依据）
    # ========================================================================
    op.create_table(
        "sys_role",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column(
            "role_code",
            sa.String(32),
            nullable=False,
            comment="角色码：ADM/EXEC/HR/MGR/EMP/AIOPS/AUDIT 或自定义",
        ),
        sa.Column("role_name", sa.String(64), nullable=False, comment="角色名"),
        sa.Column(
            "data_scope",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("3"),
            comment="数据范围 1 全部 2 本部门 3 本人 4 仅统计值(不可见明细)",
        ),
        sa.Column(
            "is_builtin",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否预置角色 0 否 1 是",
        ),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.UniqueConstraint("tenant_id", "role_code", "is_deleted", name="uk_tenant_role_code"),
        **_TABLE_KW,
    )

    # ========================================================================
    # TBL-04 权限点（菜单 / 按钮 / 接口三类，parent_id=0 为根）
    # ========================================================================
    op.create_table(
        "sys_permission",
        _snowflake_pk(),
        _tenant_id(),
        _is_deleted(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        _created_at(),
        _updated_at(),
        sa.Column(
            "perm_code", sa.String(64), nullable=False, comment="权限码，如 talent:view / talent:review"
        ),
        sa.Column("perm_name", sa.String(64), nullable=False, comment="权限名"),
        sa.Column(
            "perm_type",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("3"),
            comment="类型 1 菜单 2 按钮 3 接口",
        ),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="父权限 ID（0 为根）",
        ),
        sa.Column(
            "sort_no", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="排序"
        ),
        sa.Column(
            "status",
            mysql.TINYINT(display_width=1),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.UniqueConstraint("tenant_id", "perm_code", "is_deleted", name="uk_tenant_perm_code"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_parent", "sys_permission", ["tenant_id", "parent_id"])

    # ========================================================================
    # TBL-05 用户-角色关联（只追加，无软删/更新时间）
    # ========================================================================
    op.create_table(
        "sys_user_role",
        _snowflake_pk(),
        _tenant_id(),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户 ID"),
        sa.Column("role_id", sa.BigInteger(), nullable=False, comment="角色 ID"),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "user_id", "role_id", name="uk_tenant_user_role"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_role", "sys_user_role", ["tenant_id", "role_id"])

    # ========================================================================
    # TBL-06 角色-权限关联
    # ========================================================================
    op.create_table(
        "sys_role_permission",
        _snowflake_pk(),
        _tenant_id(),
        sa.Column("role_id", sa.BigInteger(), nullable=False, comment="角色 ID"),
        sa.Column("perm_id", sa.BigInteger(), nullable=False, comment="权限 ID"),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "role_id", "perm_id", name="uk_tenant_role_perm"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_perm", "sys_role_permission", ["tenant_id", "perm_id"])


def downgrade() -> None:
    """逆序删除（先删依赖方，再删被依赖方）。

    ⚠️ 本迁移是**一期基座**，``downgrade`` 会连带清空账号与权限数据 ——
    生产环境执行前必须确认已备份（``deploy/backup.sh``）。
    """
    op.drop_table("sys_role_permission")
    op.drop_table("sys_user_role")
    op.drop_table("sys_permission")
    op.drop_table("sys_role")
    op.drop_table("sys_user")
    op.drop_table("position")
    op.drop_table("org_dept")
    op.drop_table("tenant")
