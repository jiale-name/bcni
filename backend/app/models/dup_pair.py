"""查重对模型（TBL-17，CU-19，F-3.5 / SQ-4 / SM-1）。

🔴 本表是「重复候选对」与「合并记录」**共用一张表**：
- ``status=0 待处理``：候选对（待 HR 决定合并或忽略）；
- ``status=1 已合并``：合并已执行，``master_id`` / ``snapshot`` / ``merged_by`` /
  ``merged_at`` 记录合并去向与回滚快照（``pair.id`` 即 API-61 的 ``merge_id``）；
- ``status=2 已忽略``：HR 判定「不是重复」，不再出现在默认队列。

合并回滚（API-61）依赖 ``snapshot``（完整快照 JSON，存本表而非 TBL-22 的
``snapshot_ref`` —— 后者仅 ``VARCHAR(512)``，装不下两份档案的字段 + 标签快照）。
TBL-22 仍写一条 ``change_source=2 合并`` 的留痕，其 ``snapshot_ref`` 存 ``pair.id``
作「快照引用」，F-3.10 的变更轨迹与回滚依据由两张表各司其职。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DECIMAL, JSON, DateTime, Index, Integer, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTenantCommonMixin, MYSQL_TABLE_KWARGS
from app.models.enums import DupPairStatus


class TalentDupPair(IdTenantCommonMixin, Base):
    """TBL-17 查重对（``talent_id_a < talent_id_b`` 恒定，``uk_tenant_pair`` 防重）。"""

    __tablename__ = "talent_dup_pair"
    __table_args__ = (
        # 🔴 a<b 约定 + 唯一键：同一对只可能有一行，从库级杜绝「A-B」与「B-A」两条
        UniqueConstraint("tenant_id", "talent_id_a", "talent_id_b", "is_deleted", name="uk_tenant_pair"),
        Index("idx_tenant_status", "tenant_id", "status"),
        Index("idx_tenant_pair_a", "tenant_id", "talent_id_a"),
        Index("idx_tenant_pair_b", "tenant_id", "talent_id_b"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id_a: Mapped[int] = mapped_column(Integer, nullable=False, comment="人才 A（恒 < talent_id_b）")
    talent_id_b: Mapped[int] = mapped_column(Integer, nullable=False, comment="人才 B")
    similarity: Mapped[float | None] = mapped_column(
        DECIMAL(5, 4), nullable=True, comment="相似度 0.0000~1.0000（精确键命中=1.0）"
    )
    match_fields: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="命中的比对字段（如 phone_hash）")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(DupPairStatus.PENDING),
        server_default=text("0"), comment="状态 0 待处理 1 已合并 2 已忽略",
    )
    master_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="合并主档 ID（合并后写入）")
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="合并回滚快照（完整 JSON）")
    merged_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="合并操作人")
    merged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="合并时间")

    def __repr__(self) -> str:
        return f"<TalentDupPair {self.talent_id_a}~{self.talent_id_b} status={self.status}>"
