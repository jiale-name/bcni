"""ORM 基类与通用列 Mixin。

🔴 **唯一事实来源**（数据库 §0）：以 ORM 模型（SQLAlchemy）+ Alembic 迁移为数据库结构的
唯一事实来源；数据库设计文档的 DDL **仅作字段参考**。任何结构变更必须
「改 ORM 模型 → 生成迁移 → Code Review → 发布」，**禁止手工改库**。

通用列组（数据库 §4「通用列组」）由本模块的 Mixin 统一提供，业务模型不得重复声明：

| 列 | 含义 | 提供者 |
| --- | --- | --- |
| ``id`` | 主键（雪花 ID，应用生成，有符号 BIGINT） | :class:`SnowflakeIdMixin` |
| ``tenant_id`` | 租户 ID（D-10 多租户预留） | :class:`TenantColumnMixin` |
| ``is_deleted`` / ``deleted_at`` | 软删除 | :class:`SoftDeleteMixin` |
| ``created_at`` / ``updated_at`` | 时间戳 | :class:`TimestampMixin` |

⚠️ **``tenant`` 表自身不带 ``tenant_id``**（TBL-01），故它不继承 :class:`TenantColumnMixin`。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.snowflake import next_id

#: 约束命名规范 —— 让 Alembic autogenerate 产出的约束名稳定可读
NAMING_CONVENTION: dict[str, str] = {
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uk_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

#: MySQL 表级参数（数据库 §0 原则 7：一律 utf8mb4 / utf8mb4_unicode_ci）
MYSQL_TABLE_KWARGS: dict[str, str] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ============================================================================
# Mixin
# ============================================================================
class SnowflakeIdMixin:
    """雪花 ID 主键（数据库 §0.1：应用生成，**不用 AUTO_INCREMENT**）。"""

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        default=next_id,
        comment="主键（雪花 ID，应用生成）",
    )


class TenantColumnMixin:
    """租户列。

    🔴 联合索引**最左列必须是 ``tenant_id``**（数据库 §0 原则 5）；
    所有查改删**必须带租户条件**（由 :mod:`app.core.tenant` 的 QueryFilter 自动注入）。
    """

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="租户 ID",
    )


class SoftDeleteMixin:
    """软删除（数据库 §0 原则 4）。

    ⚠️ **唯一索引必须带 ``is_deleted``** —— 否则软删后无法重建同名记录。
    """

    is_deleted: Mapped[int] = mapped_column(
        TINYINT(1),
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="软删除 0 否 1 是",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="删除时间",
    )


class TimestampMixin:
    """创建/更新时间。

    ``updated_at`` 由 **MySQL 的 ``ON UPDATE CURRENT_TIMESTAMP``** 维护，
    同时在 Python 侧设 ``onupdate`` 作为双保险（批量 ORM 更新时不依赖数据库行为）。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


class CommonColumnsMixin(TenantColumnMixin, SoftDeleteMixin, TimestampMixin):
    """业务表标准列组：``tenant_id`` + 软删 + 时间戳。"""


class IdTenantCommonMixin(SnowflakeIdMixin, CommonColumnsMixin):
    """最常用组合：雪花 ID + 业务标准列组。"""


#: 追加型日志表（审计 / AI 调用 / 变更留痕）：**只追加，不更新不软删**（数据库 §0 原则 11）
class AppendOnlyMixin(SnowflakeIdMixin, TenantColumnMixin):
    """只追加日志表：无 ``is_deleted`` / ``updated_at``。"""
