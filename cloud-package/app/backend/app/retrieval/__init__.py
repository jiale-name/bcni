"""向量检索提供方（RetrievalProvider）—— 架构 §4.4 关闭 H-3 的落点。

业务代码只依赖 :class:`~app.retrieval.provider.RetrievalProvider` 协议，
**不依赖具体实现**。一期提供两种实现：

- :class:`~app.retrieval.milvus_provider.MilvusRetrievalProvider`（pymilvus，延迟导入）
- :class:`~app.retrieval.keyword_provider.KeywordRetrievalProvider`（MySQL LIKE 降级）

:func:`get_retrieval_provider` 是**唯一入口**：Milvus 可用时返回向量实现，
否则返回关键词降级实现 —— Service 层不需要（也不应该）自己判断「该用哪个」。

🔴 **数据自有**：向量是 MySQL 权威数据的**派生索引**，可随时从主数据
全量重建（:mod:`scripts.rebuild_index`）。因此「删向量失败」不等于「丢数据」，
它只是让某条档案暂时检索不到 —— 这也正是 CR-18 跨库写顺序能成立的前提。
"""

from __future__ import annotations

from app.retrieval.keyword_provider import KeywordRetrievalProvider
from app.retrieval.milvus_provider import MilvusRetrievalProvider
from app.retrieval.provider import RetrievalProvider, SearchHit

__all__ = [
    "KeywordRetrievalProvider",
    "MilvusRetrievalProvider",
    "RetrievalProvider",
    "SearchHit",
    "get_retrieval_provider",
]


def get_retrieval_provider() -> RetrievalProvider:
    """返回当前可用的检索实现。

    判定规则：**先探测 Milvus**，可用即用向量实现；不可用（pymilvus 未装 /
    连接被拒 / collection 未建）则回退到关键词实现。

    ⚠️ 探测是**惰性但真实的**（尝试一次连接），结果由 :meth:`MilvusRetrievalProvider.health`
    决定。它不会缓存「不可用」这个结论 —— 进程运行中 Milvus 恢复后，下一次调用
    会重新探测到可用（否则运维拉起 Milvus 后还必须重启应用才能生效）。
    """
    milvus = MilvusRetrievalProvider()
    if milvus.health():
        return milvus
    return KeywordRetrievalProvider()
