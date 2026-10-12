"""租户与组织（TBL-01 / TBL-07 / TBL-08）。

类图 CL-1 对照表：``Tenant`` / ``OrgDept`` / ``Position`` → ``models/tenant.py``
（架构 §5 把基座实体合并于 ``tenant.py`` / ``user.py`` / ``dict.py`` 三个文件）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.dialects.mysql import JSON as MysqlJSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    MYSQL_TABLE_KWARGS,
    Base,
    IdTenantCommonMixin,
    SnowflakeIdMixin,
)


class Tenant(SnowflakeIdMixin, Base):
    """TBL-01 租户（一期单行；**本表自身无 tenant_id**，D-10 多租户预留）。

    一期固定单租户值，但保留 ``tenant_code`` 以便后续接入第 2 个租户时无需改表。
    """

    __tablename__ = "tenant"
    __table_args__ = (
        UniqueConstraint("tenant_code", name="uk_tenant_code"),
        MYSQL_TABLE_KWARGS,
    )

    tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="租户编码")
    tenant_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="单位名称")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )
    config_json: Mapped[dict[str, Any] | None] = mapped_column(
        MysqlJSON, nullable=True, comment="租户级配置：出网策略/阈值等"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} code={self.tenant_code!r}>"


class OrgDept(IdTenantCommonMixin, Base):
    """TBL-07 组织架构（支持 ≥5 级）。

    ``dept_path`` 为祖级路径（如 ``/1/12/125/``），子树查询用它做前缀匹配 ——
    这是 MGR（``data_scope=2 本部门``）行级权限的实现基础（接口 §1.7）。
    """

    __tablename__ = "org_dept"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dept_code", "is_deleted", name="uk_tenant_dept_code"),
        Index("idx_tenant_parent", "tenant_id", "parent_id"),
        # 前缀索引 191：dept_path 为 VARCHAR(512)，utf8mb4 下全长索引超键长上限
        Index("idx_tenant_path", "tenant_id", "dept_path", mysql_length={"dept_path": 191}),
        MYSQL_TABLE_KWARGS,
    )

    parent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="父组织 ID（0 为根）"
    )
    dept_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="组织编码")
    dept_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="组织名称")
    dept_level: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="层级（1 起，支持 ≥5 级）"
    )
    dept_path: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="祖级路径，如 /1/12/125/（子树查询用）"
    )
    leader_user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="负责人用户 ID"
    )
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<OrgDept id={self.id} name={self.dept_name!r} level={self.dept_level}>"


class Position(IdTenantCommonMixin, Base):
    """TBL-08 岗位。"""

    __tablename__ = "position"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "position_code", "is_deleted", name="uk_tenant_position_code"
        ),
        Index("idx_tenant_dept", "tenant_id", "dept_id"),
        MYSQL_TABLE_KWARGS,
    )

    position_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="岗位编码")
    position_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="岗位名称")
    dept_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="所属组织 ID")
    position_level: Mapped[int | None] = mapped_column(TINYINT, nullable=True, comment="岗位层级")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<Position id={self.id} code={self.position_code!r}>"
