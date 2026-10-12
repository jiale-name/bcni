"""用户、角色、权限（TBL-02 ~ TBL-06）。

类图 CL-1 对照表：``SysUser`` / ``SysRole`` / ``SysPermission`` → ``models/user.py``。

🔴 敏感字段（手机/邮箱）按数据库 §0 原则 10 **AES 加密存 ``VARBINARY``**，
另设 ``*_hash`` 供精确去重（本期先落 ``phone_enc`` / ``email_enc`` 两列，
去重用的 ``*_hash`` 待 N2 档案模块一并引入，避免现在建无用列）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT, VARBINARY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    MYSQL_TABLE_KWARGS,
    Base,
    IdTenantCommonMixin,
    SnowflakeIdMixin,
    TenantColumnMixin,
)


class SysUser(IdTenantCommonMixin, Base):
    """TBL-02 用户账号。

    ``locked_until`` / ``login_fail_count`` 是 F-1.2「连续 5 次失败锁定 10 分钟」的
    **权威字段**（Redis ``rate:login:*`` 仅作快速计数；Redis 不可用时本字段仍能兜住安全）。
    """

    __tablename__ = "sys_user"
    __table_args__ = (
        # ⚠️ 唯一索引必须带 is_deleted，否则软删后无法重建同名账号
        UniqueConstraint("tenant_id", "username", "is_deleted", name="uk_tenant_username"),
        Index("idx_tenant_org", "tenant_id", "org_id"),
        MYSQL_TABLE_KWARGS,
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="登录名")
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False, comment="密码 bcrypt 哈希")
    real_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="姓名")
    org_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="归属组织（行级权限字段）"
    )
    position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="岗位 ID")
    phone_enc: Mapped[bytes | None] = mapped_column(
        VARBINARY(256), nullable=True, comment="手机号（AES 加密）"
    )
    email_enc: Mapped[bytes | None] = mapped_column(
        VARBINARY(256), nullable=True, comment="邮箱（AES 加密）"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 禁用 1 启用"
    )
    login_fail_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="连续登录失败次数",
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="锁定截止时间（连错 5 次锁 10 分钟）"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近登录时间"
    )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # 🔴 绝不输出 password_hash
        return f"<SysUser id={self.id} username={self.username!r} status={self.status}>"


class SysRole(IdTenantCommonMixin, Base):
    """TBL-03 角色（7 个预置角色 + 自定义）。

    ``data_scope`` 取值见 :class:`app.models.enums.RoleDataScope`，
    是行级 + 字段级裁剪的依据（接口 §1.7）。
    """

    __tablename__ = "sys_role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role_code", "is_deleted", name="uk_tenant_role_code"),
        MYSQL_TABLE_KWARGS,
    )

    role_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="角色码：ADM/EXEC/HR/MGR/EMP/AIOPS/AUDIT 或自定义",
    )
    role_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名")
    data_scope: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=3,
        server_default=text("3"),
        comment="数据范围 1 全部 2 本部门 3 本人 4 仅统计值(不可见明细)",
    )
    is_builtin: Mapped[int] = mapped_column(
        TINYINT(1),
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="是否预置角色 0 否 1 是",
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<SysRole id={self.id} code={self.role_code!r} scope={self.data_scope}>"


class SysPermission(IdTenantCommonMixin, Base):
    """TBL-04 权限点（菜单 / 按钮 / 接口三类）。

    ``parent_id=0`` 为根，构成权限树（API-21 ``/permissions/tree``）。
    """

    __tablename__ = "sys_permission"
    __table_args__ = (
        UniqueConstraint("tenant_id", "perm_code", "is_deleted", name="uk_tenant_perm_code"),
        Index("idx_tenant_parent", "tenant_id", "parent_id"),
        MYSQL_TABLE_KWARGS,
    )

    perm_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="权限码，如 talent:view / talent:review"
    )
    perm_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限名")
    perm_type: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=3,
        server_default=text("3"),
        comment="类型 1 菜单 2 按钮 3 接口",
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="父权限 ID（0 为根）"
    )
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<SysPermission id={self.id} code={self.perm_code!r}>"


class SysUserRole(SnowflakeIdMixin, TenantColumnMixin, Base):
    """TBL-05 用户-角色关联（只追加的关联表，无软删/更新时间）。"""

    __tablename__ = "sys_user_role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "role_id", name="uk_tenant_user_role"),
        Index("idx_tenant_role", "tenant_id", "role_id"),
        MYSQL_TABLE_KWARGS,
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="用户 ID")
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="角色 ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def __repr__(self) -> str:
        return f"<SysUserRole user={self.user_id} role={self.role_id}>"


class SysRolePermission(SnowflakeIdMixin, TenantColumnMixin, Base):
    """TBL-06 角色-权限关联。"""

    __tablename__ = "sys_role_permission"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role_id", "perm_id", name="uk_tenant_role_perm"),
        Index("idx_tenant_perm", "tenant_id", "perm_id"),
        MYSQL_TABLE_KWARGS,
    )

    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="角色 ID")
    perm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="权限 ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def __repr__(self) -> str:
        return f"<SysRolePermission role={self.role_id} perm={self.perm_id}>"
