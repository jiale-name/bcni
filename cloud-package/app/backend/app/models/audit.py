"""审计日志（TBL-28 ``audit_log``，F-1.5，接口 API-28 / API-29）。

定位
====
**只追加的操作流水**：谁、在什么时候、对什么对象、做了什么、改前改后是什么。
它与另外两张日志表的边界必须分清，否则会出现「同一件事记三遍、且三处不一致」：

| 表 | 记什么 | 与审计的关系 |
| --- | --- | --- |
| **TBL-28 `audit_log`（本表）** | **操作行为**（登录、导出、权限变更、删除…） | 本表 |
| TBL-24 `ai_call_log` | AI 调用（模型、token、耗时、成本） | CU-12，独立 |
| TBL-22 `talent_change_log` | **人才档案**字段级变更（`talent_id` 非空） | CU-22，独立 |

> 上游曾把「字典项留痕」指向 TBL-22（接口 §2.2），但该表 `talent_id NOT NULL`
> 且语义是「人才档案变更」—— 字典项落不进去，已改走本表（评审文档 §7.3 批次 4 已登记）。

三个必须写清的设计点
====================

① **复合主键 ``(id, created_at)``，不是单纯的 ``id``**
------------------------------------------------------
MySQL 的 **RANGE 分区要求分区列出现在每个唯一键中**，而本表按
``TO_DAYS(created_at)`` 分区，所以 ``created_at`` 必须进主键
（数据库 §4.7 的 DDL 也是这么写的）。

② **``created_at`` 在 Python 侧也给默认值**
------------------------------------------
只靠 ``server_default=CURRENT_TIMESTAMP`` 的话，插入后 ORM 对象上该字段仍是 ``None``
（MySQL 没有 ``RETURNING``）。而它是**主键的一部分** —— 对象处于「主键不完整」状态
在 flush 之后容易引发困惑（例如 `session.refresh` 前的 `obj.created_at` 是 None）。
故 Python 侧同时给 ``default=datetime.now``（本地 naive 时间，与
``CURRENT_TIMESTAMP`` 口径一致，见项目约定）。

③ **不继承 :class:`~app.models.base.AppendOnlyMixin`**
-----------------------------------------------------
该 mixin 提供的是**单列** ``id`` 主键，而本表需要**复合**主键（见 ①）。
其余「只追加」语义（无 ``is_deleted`` / 无 ``updated_at``）在本类里显式体现：
**本表根本没有这两列** —— 让「误更新/误软删」在 SQL 层就无从下手。

🔴 「只追加」是如何被保证的（三层，缺一层都不够）
================================================
1. **模型层**：无 ``is_deleted`` / ``updated_at`` 列（本文件）；
2. **DAO 层**：``audit_dao`` **只提供 insert 与 select，不提供任何 update/delete 方法**
   —— 这是最可靠的一层，因为「没有函数可调」比「有函数但约定别调」强得多；
3. **数据库层（⚠️ 尚未落地，已登记遗留）**：生产环境应给应用账号仅授予
   ``INSERT, SELECT`` on ``audit_log``（或加 ``BEFORE UPDATE/DELETE`` 触发器抛错）。
   当前本机用 ``root`` 单账号，无法做权限隔离。

分区与留存
==========
按月 RANGE 分区（``pYYYYMM`` + ``pmax``），**清历史用 ``DROP PARTITION`` 而非 ``DELETE``**
—— 后者会全表扫描 + 写 undo，在亿级日志表上等同于事故。

⚠️ **受控例外（已登记）**：分区语句**不在本模型里**，而在迁移 ``0003_audit`` 中
以 ``ALTER TABLE ... PARTITION BY`` 执行 —— SQLAlchemy 无法表达「命名分区列表」。
因此这一处**唯一事实来源是迁移文件**，不是 ORM。分区的日常补足由
``scripts/audit_partition.py`` 负责（否则 ``pmax`` 会不断膨胀，DROP PARTITION 也就无从谈起）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.snowflake import next_id
from app.models.base import MYSQL_TABLE_KWARGS, Base


class AuditLog(Base):
    """TBL-28 审计日志（只追加，按月分区，留存 ≥12 个月）。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        # 租户 + 时间：审计检索的主路径（分区裁剪 + 时间倒序分页）
        Index("idx_tenant_time", "tenant_id", "created_at"),
        # 按操作人检索
        Index("idx_tenant_user", "tenant_id", "user_id"),
        # 按动作类型检索。⚠️ 这里**不带** tenant_id 前缀：数据库 §4.7 的 DDL 就是这么建的，
        # 且审计查询永远是「租户内 + 时间窗」，action 只是二次过滤。
        # 保持与 DDL 一致，避免「文档与库不一致」的核对成本。
        Index("idx_action", "action"),
        MYSQL_TABLE_KWARGS,
    )

    #: 主键（雪花 ID）。与 ``created_at`` 组成复合主键 —— 分区列必须进主键。
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        default=next_id,
        comment="主键（雪花 ID，应用生成）",
    )
    #: 分区列，同时是复合主键的第二列（见模块 docstring ①）。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        primary_key=True,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="操作时间（分区键）",
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0"), comment="租户 ID"
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人")
    action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="动作：login/logout/change_password/export/perm_change/delete/...",
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="对象类型")
    target_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="对象 ID（字符串，兼容雪花 ID 与业务编码）"
    )
    #: 改前快照（JSON 字符串）。⚠️ 权限变更加密留痕的要求见接口 §1.7 —— 本表只存
    #: 业务字段的 diff，**不含密码 / Token**（写入侧由 ``audit_service`` 统一脱敏）。
    before_json: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="改前快照（JSON）")
    after_json: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="改后快照（JSON）")

    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源 IP")
    ua: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="User-Agent")
    trace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="链路 ID（与日志 / X-Request-Id 一致）"
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"target={self.target_type}:{self.target_id} uid={self.user_id}>"
        )
