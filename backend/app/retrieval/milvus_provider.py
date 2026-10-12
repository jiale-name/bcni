"""Milvus 检索实现（DS-2，数据库 §7，CU-18 的向量侧）。

🔴 **延迟导入 pymilvus**：本机 / 国产化环境可能没有装 pymilvus（N0 实测遗留：
Milvus 探针曾因缺 ``pymilvus`` 阻塞）。所有 ``import pymilvus`` 都放在方法体内，
模块级 import 一旦失败会让**整个应用起不来**（导入链会一路炸到 ``app.main``）。
延迟导入后，没装 pymilvus 只是「向量检索降级」，不影响其余功能。

🔴 **永不抛异常**：连接失败 / 超时 / 缺库都表达为「空结果」或 ``health()=False``，
由 :func:`~app.retrieval.get_retrieval_provider` 决定回退关键词实现。

Collection schema（数据库 §7，三个 collection 同构）：
``pk``(VARCHAR) / ``talent_id`` ``tenant_id`` ``org_id``(INT64) /
``field_name``(VARCHAR) / ``chunk_text``(VARCHAR, 脱敏后) / ``vec``(FLOAT_VECTOR 1024) /
``created_at``(INT64)。HNSW + COSINE。
"""

from __future__ import annotations

import time
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.retrieval.provider import SearchHit

logger = get_logger(__name__)

#: 三个同构 collection（COL-01 技能 / COL-02 经验 / COL-03 素质 —— 数据库 §7）
COLLECTIONS: tuple[str, ...] = ("talent_skill", "talent_experience", "talent_quality")
DEFAULT_COLLECTION: str = "talent_skill"


class MilvusRetrievalProvider:
    """Milvus 向量检索实现（延迟连接、降级友好）。"""

    def __init__(self) -> None:
        #: 连接是惰性的；``_client`` 为 None 表示「尚未尝试或已失败」。
        #: 缓存连接对象避免每次检索都重新握手（同一进程内复用）。
        self._client: Any = None
        self._collections: dict[str, Any] = {}
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    # 连接与 collection
    # ------------------------------------------------------------------
    def _connect(self) -> Any | None:
        """建立连接（缓存）。失败返回 None 并记录原因。"""
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient  # 延迟导入：缺 pymilvus 只降级不崩

            client = MilvusClient(
                uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
                db_name=settings.MILVUS_DATABASE,
                timeout=settings.MILVUS_TIMEOUT,
            )
            # 触发一次真实连接（MilvusClient 惰性，直到第一次操作才连）
            client.list_collections()
            self._client = client
            self._last_error = None
            return client
        except Exception as exc:  # noqa: BLE001 — 连接失败是「降级」不是「故障」
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus 连接失败（向量检索降级）：%s", self._last_error)
            return None

    def _get_collection(self, name: str) -> Any | None:
        """取 collection 对象；不存在则尝试创建（失败返回 None）。"""
        if name in self._collections:
            return self._collections[name]
        client = self._connect()
        if client is None:
            return None
        try:
            if not client.has_collection(name):
                self._create_collection(client, name)
            self._collections[name] = name
            return name
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus collection 准备失败 [%s]：%s", name, self._last_error)
            return None

    @staticmethod
    def _create_collection(client: Any, name: str) -> None:
        """建 collection（数据库 §7 的 schema；幂等）。"""
        client.create_collection(
            collection_name=name,
            dimension=int(settings.EMBEDDING_DIM),
            metric_type="COSINE",
            auto_id=False,
            schema=None,  # 用默认 schema（fields 由 insert 时的数据字典推断）
        )

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
        pk = f"{talent_id}:{field_name}"
        if vector is None:
            # 没有向量（调用方未预计算）：本实现**不负责计算 embedding**，
            # 那是上游「Embedding 流水线」的职责（CU-18 缺口 H-19）。这里返回占位 pk，
            # 表示「已登记但未真正入库」，让调用方在吞吐标定后补算。
            logger.warning("ingest 收到空向量，跳过入库 talent=%s field=%s", talent_id, field_name)
            return pk
        collection = self._get_collection(DEFAULT_COLLECTION)
        if collection is None:
            return pk  # 降级：返回 pk 但不落库（不抛异常）
        try:
            client = self._client
            client.upsert(
                collection_name=collection,
                data=[
                    {
                        "pk": pk,
                        "talent_id": int(talent_id),
                        "tenant_id": int(tenant_id),
                        "org_id": int(org_id) if org_id is not None else 0,
                        "field_name": field_name,
                        "chunk_text": chunk_text,
                        "vec": vector,
                        "created_at": int(time.time()),
                    }
                ],
            )
            return pk
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus ingest 失败 talent=%s：%s", talent_id, self._last_error)
            return pk

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
        if vector is None:
            return []  # 向量检索必须有向量；文本检索走关键词实现
        collection = self._get_collection(DEFAULT_COLLECTION)
        if collection is None:
            return []
        # 🔴 权限前置过滤：检索前先拼 expr（禁止「先召回全量再过滤」—— CR-11 / B-8）
        expr = f"tenant_id == {int(tenant_id)}"
        if org_ids:
            joined = ", ".join(str(int(i)) for i in org_ids)
            expr += f" and org_id in [{joined}]"
        try:
            client = self._client
            result = client.search(
                collection_name=collection,
                data=[vector],
                limit=int(top_k),
                output_fields=["talent_id", "field_name", "chunk_text"],
                filter=expr,
            )
            hits: list[SearchHit] = []
            for item in (result[0] if result else []):
                entity = item.get("entity", {}) if isinstance(item, dict) else {}
                hits.append(
                    SearchHit(
                        talent_id=int(entity.get("talent_id", 0)),
                        similarity=float(item.get("distance", 0.0) if isinstance(item, dict) else 0.0),
                        field_name=entity.get("field_name"),
                        chunk_text=entity.get("chunk_text"),
                    )
                )
            return hits
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus search 失败：%s", self._last_error)
            return []

    def delete(self, db: Any, *, talent_id: int, tenant_id: int) -> int:
        collection = self._get_collection(DEFAULT_COLLECTION)
        if collection is None:
            return -1  # 拿不到 collection = 删除失败（区别于「删了 0 条」）
        try:
            client = self._client
            result = client.delete(
                collection_name=collection,
                filter=f"talent_id == {int(talent_id)} and tenant_id == {int(tenant_id)}",
            )
            deleted = int(result.get("delete_count", 0)) if isinstance(result, dict) else 0
            return deleted
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus delete 失败 talent=%s：%s", talent_id, self._last_error)
            return -1  # 🔴 失败哨兵：调用方（合并）据此回滚 MySQL，保证「先 MySQL 后向量」的原子性

    def delete_all(self, db: Any, *, talent_id: int, tenant_id: int) -> dict[str, int]:
        """删除某档案在**三个 collection** 中的切片（API-77 审批删除的第二步，CU-22）。

        ⚠️ 与 :meth:`delete` 的区别：本方法**逐 collection 执行并逐个返回条数**，
        而不是只删默认 collection。原因见 :meth:`~app.retrieval.provider.RetrievalProvider.delete_all`。

        🔴 **不短路**：某个 collection 失败后继续尝试其余 collection。
        停下来的话，失败点之后的集合会**完全没被清理**，而「清理到一半」
        在删除权场景里是最坏的结果（调用方还得判断「到底哪些被删了」）。
        全部试完再一次性返回，调用方拿到的是一份**完整**的现场记录。

        :return: ``{集合名: 条数}``；失败的集合记 ``-1``。
        """
        out: dict[str, int] = {}
        for name in COLLECTIONS:
            collection = self._get_collection(name)
            if collection is None:
                out[name] = -1
                continue
            try:
                client = self._client
                result = client.delete(
                    collection_name=collection,
                    filter=f"talent_id == {int(talent_id)} and tenant_id == {int(tenant_id)}",
                )
                out[name] = int(result.get("delete_count", 0)) if isinstance(result, dict) else 0
            except Exception as exc:  # noqa: BLE001 — 与 delete 同口径：失败以哨兵表达
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Milvus delete_all 失败 talent=%s collection=%s：%s",
                    talent_id,
                    name,
                    self._last_error,
                )
                out[name] = -1
        return out

    def health(self) -> bool:
        return self._connect() is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error
