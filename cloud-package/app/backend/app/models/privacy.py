"""个人信息权利响应域（**TBL-30** `privacy_request`，CU-22，F-3.11 / SM-3）。

🔴 为什么必须新增一张表（不是「偷懒建表」，是上游缺口）
=====================================================
接口设计 §2.2 把 API-73/76/80（更正申请列表 / 删除申请列表 / 权利请求台账）
的「涉及表」写成 **TBL-22 `talent_change_log`** —— 但那张表是**档案字段级变更留痕**：
``talent_id NOT NULL``、没有请求状态、没有申请人、没有处理时限、没有驳回理由。
三类权利请求（更正 / 删除 / 导出）与它们的**状态机（SM-3：待处理 / 处理中 / 已通过 / 已驳回）**
以及「处理时限可配置并统计」（API-80）在这个模型里**无处安放**。

因此本单元新增 **TBL-30 `privacy_request`**（表号延续 TBL-29 的分配规则，见评审文档 §0.2），
并把「API-73/76/80 涉及表 TBL-22」登记为**上游不一致**（以代码为准）。

与既有表的分工（❌ 不要混用）
------------------------------
| 表 | 记什么 | 谁写 |
| --- | --- | --- |
| **TBL-30 `privacy_request`（本文件）** | **权利请求单**：谁在何时提了什么、处理到哪一步 | CU-22 |
| TBL-22 `talent_change_log` | **档案字段级变更**（值本身变了什么） | CU-15/16/19 + CU-22（审批删除） |
| TBL-16 `talent_parse_field` | 字段值 + 复核状态（更正申请**复用**该队列，不另建审批流） | CU-15/16 + CU-22 |
| TBL-28 `audit_log` | **操作行为**（谁点了同意 / 下载了数据包） | CU-22 |

🔴 **状态位 `3 处理中` 是内部态**（SM-3 明确「不对外暴露」）：它只用于
「删除审批」的 **CAS**（compare-and-set）—— 两个 ADM 同时点「同意」时，
后到者的条件更新影响行数为 0，从而被识别为并发双审而非重复删除。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import MYSQL_TABLE_KWARGS, Base, IdTenantCommonMixin
from app.models.enums import PrivacyRequestStatus, PrivacyRequestType


class PrivacyRequest(IdTenantCommonMixin, Base):
    """TBL-30 个人信息权利请求单（F-3.11 四项权利的**统一载体**）。

    ``request_type`` 三值共用同一张表与同一条状态机（SM-3）——
    「同一状态机、不同『已通过』的含义」比「三张结构几乎一样的表」更好维护：
    台账（API-80）要按类型聚合统计处理时限，分表会让统计变成三次查询 + 手工合并。
    """

    __tablename__ = "privacy_request"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_no", "is_deleted", name="uk_tenant_privacy_no"),
        # 🔴 API-77 强制幂等（接口 §1.6）：「审批删除」与「处理更正」都写这张表，
        #    ``Idempotency-Key`` 落在本列 —— 唯一键兜住竞态穿透（两个并发重试同时查不到 → 同时插）。
        UniqueConstraint("tenant_id", "idempotent_key", "is_deleted", name="uk_tenant_privacy_idem"),
        Index("idx_tenant_type_status", "tenant_id", "request_type", "status"),
        Index("idx_tenant_talent", "tenant_id", "talent_id"),
        Index("idx_tenant_applicant", "tenant_id", "applicant_user_id"),
        Index("idx_tenant_created", "tenant_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )

    request_no: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="请求号 PRVyyyymmddNNN（租户内唯一）"
    )
    request_type: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=int(PrivacyRequestType.CORRECTION),
        server_default=text("0"),
        comment="类型 0 更正 1 删除 2 导出",
    )
    talent_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="档案 ID（被请求的对象）")
    applicant_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="申请人（本人；API-71/72/75/78 只能由本人发起）"
    )
    # ---- 更正申请专有（API-72）----
    field_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="更正字段（可更正字段白名单，见 core/privacy.py）"
    )
    current_value: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="提交时的当前值（乐观锁比对用）"
    )
    expected_value: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="申请更正后的值")
    review_field_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="关联 TBL-16 字段行（更正申请复用 F-3.3 复核队列）"
    )
    # ---- 三类共用 ----
    reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="理由（删除/更正必填）"
    )
    attachments: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="佐证材料文件 ID 数组（≤5）"
    )
    status: Mapped[int] = mapped_column(
        TINYINT,
        nullable=False,
        default=int(PrivacyRequestStatus.PENDING),
        server_default=text("0"),
        comment="状态 0 待处理 1 已通过 2 已驳回 3 处理中（内部态，仅删除审批的 CAS 期间存在）",
    )
    sla_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=72,
        server_default=text("72"),
        comment="提交时刻生效的处理时限（快照：配置改了不让历史请求的时限一起漂移）",
    )
    idempotent_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="幂等键（API-77 强制幂等；NULL 不参与唯一约束）"
    )
    processed_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="处理人（ADM/HR）")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="处理时间")
    reject_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="驳回理由（approved=false 时必填，须告知本人）"
    )
    # ---- 通知留痕（SM-3 红线：「处理结果必须通知本人」）----
    # ⚠️ 一期无「站内信」表（数据库设计未定义），故通知以**本行的消息快照 + 审计日志**
    #    双落点表达；站内信表待需求确认后另立单元（已登记遗留）。
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="通知本人时间")
    notify_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="通知正文快照（站内信本期无表，先落此处）"
    )
    # ---- 导出申请专有（API-78/79）----
    export_format: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据包格式 json")
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="下载次数（留痕）"
    )
    last_download_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近一次下载时间（留痕）"
    )

    def __repr__(self) -> str:
        return (
            f"<PrivacyRequest {self.request_no!r} type={self.request_type} "
            f"talent={self.talent_id} status={self.status}>"
        )
