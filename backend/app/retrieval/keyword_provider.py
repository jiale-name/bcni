"""关键词检索实现（向量检索的降级路径，VECTOR_DEGRADED=50302）。

Milvus 不可用时，:func:`~app.retrieval.get_retrieval_provider` 返回本实现。
它直接在 ``talent_base`` 上做 MySQL ``LIKE`` 匹配（姓名 / 院校 / 专业 / 学历），
**不维护任何独立索引**（MySQL 已是权威数据源），故：

- ``ingest`` / ``delete`` 是**空操作**（没有要维护的派生索引）；
- ``search`` 只支持 ``query_text``，传 ``vector`` 时返回空（关键词无法算向量相似度）。

⚠️ 语义边界：关键词降级能回答「名字/院校像不像」，**回答不了**「能力画像像不像」——
那是向量检索的职责。降级只是「部分可用」，不是「等价替代」，调用方（查重服务）
须据此收窄判定口径（仅精确键去重，不做模糊向量去重）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select

from app.core.logging import get_logger
from app.models.talent import TalentBase
from app.retrieval.provider import SearchHit

logger = get_logger(__name__)

#: 参与关键词匹配的字段（姓名 / 院校 / 专业 / 学历）—— 与画像检索的可见字段一致
_SEARCH_COLUMNS = ("name", "school", "major", "education_level")


class KeywordRetrievalProvider:
    """MySQL 关键词检索（降级实现）。"""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # 协议实现
    # ------------------------------------------------------------------
    def ingest(
        self,
        db: Any,
        *,
        talent_id: int,
        tenant_id: int,
        org_id: int | None,
        field_name: str,
        chunk_text: str,
        vector: list[float] | None = None,
    ) -> str:
        # 关键词模式无独立索引：主数据已在 MySQL，无需（也不能）额外落库
        return f"{talent_id}:{field_name}"

    def search(
        self,
        db: Any,
        *,
        tenant_id: int,
        org_ids: list[int] | None = None,
        top_k: int = 10,
        vector: list[float] | None = None,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        if not (query_text or "").strip():
            return []  # 关键词检索必须有文本；向量检索本实现不支持
        keyword = f"%{query_text.strip()}%"
        conditions = [TalentBase.tenant_id == tenant_id, TalentBase.is_deleted == 0]
        # 🔴 权限前置过滤：org 条件在检索前拼进 WHERE（与向量侧的 expr 同源，CR-11）
        if org_ids:
            conditions.append(TalentBase.org_id.in_([int(i) for i in org_ids]))
        like_clause = or_(
            *[getattr(TalentBase, col).like(keyword) for col in _SEARCH_COLUMNS]
        )
        conditions.append(like_clause)
        stmt = (
            select(TalentBase)
            .where(*conditions)
            .order_by(TalentBase.id)
            .limit(int(top_k))
        )
        try:
            rows = list(db.execute(stmt).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("关键词检索失败：%s", exc)
            return []
        # 关键词命中的「相似度」无法做数值化，统一给 1.0 表示「命中」（判定交给阈值引擎）
        return [
            SearchHit(talent_id=int(row.id), similarity=1.0, field_name=None, chunk_text=row.name)
            for row in rows
        ]

    def delete(self, db: Any, *, talent_id: int, tenant_id: int) -> int:
        return 0  # 无独立索引，无可删

    def delete_all(self, db: Any, *, talent_id: int, tenant_id: int) -> dict[str, int]:
        """降级实现：**没有独立的向量索引**，故没有任何切片需要清理 → 返回 ``{}``。

        ⚠️ 空 dict 有两种截然不同的含义，调用方必须区分（CU-22 API-77 的
        ``vector_degraded`` 标记就是为此存在）：

        - **降级环境**（本实现）：本来就没有向量库 → 无切片可删，
          此时「审批删除」**不应被阻断**（档案必须删得掉，否则删除权落空）；
        - **Milvus 环境全删 0 条**：档案本来就没有向量切片 → 也返回 ``{集合: 0}``，
          **不是**空 dict。两者靠 ``{}`` 与 ``{名: 0}`` 区分。
        """
        return {}

    def health(self) -> bool:
        # 关键词检索依赖 MySQL（已在请求生命周期内注入 db），始终可用
        return True
