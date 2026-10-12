"""CU-18 · 检索提供方抽象（RetrievalProvider，L1 —— 无外部依赖）。

架构 §4.4：业务只依赖协议，不依赖实现。本文件钉死三件事：

1. 两种实现（Milvus / Keyword）**都满足** ``RetrievalProvider`` 协议；
2. 关键词降级实现的方法是**空操作但语义正确**（``ingest`` 无独立索引、
   ``delete`` 无索引可删、``search`` 传空文本返回空）；
3. 工厂 ``get_retrieval_provider`` 永远返回一个**可用**的实现（Milvus 不可用 → Keyword）。
"""

from __future__ import annotations

from app.retrieval import (
    KeywordRetrievalProvider,
    MilvusRetrievalProvider,
    RetrievalProvider,
    get_retrieval_provider,
)


def test_both_providers_satisfy_protocol() -> None:
    assert isinstance(KeywordRetrievalProvider(), RetrievalProvider)
    assert isinstance(MilvusRetrievalProvider(), RetrievalProvider)


def test_keyword_ingest_is_idempotent_noop() -> None:
    provider = KeywordRetrievalProvider()
    pk = provider.ingest(None, talent_id=1, tenant_id=1, org_id=None,
                         field_name="profile", chunk_text="name:张三", vector=None)
    assert pk == "1:profile"  # 关键词模式无独立索引，返回合成 pk 即可


def test_keyword_search_empty_text_returns_empty() -> None:
    provider = KeywordRetrievalProvider()
    assert provider.search(None, tenant_id=1, query_text="") == []
    assert provider.search(None, tenant_id=1, vector=[0.1, 0.2]) == []  # 关键词不支持向量


def test_keyword_delete_is_noop_and_health_true() -> None:
    provider = KeywordRetrievalProvider()
    assert provider.delete(None, talent_id=1, tenant_id=1) == 0
    assert provider.health() is True


def test_factory_returns_a_provider() -> None:
    """工厂永不抛异常，且返回的是协议实现（Milvus 不可用 → Keyword）。"""
    provider = get_retrieval_provider()
    assert isinstance(provider, RetrievalProvider)
    assert isinstance(provider, (MilvusRetrievalProvider, KeywordRetrievalProvider))
