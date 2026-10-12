"""检索提供方协议（RetrievalProvider）。

为什么需要一个「协议」而不是直接在 Service 里 import pymilvus
==============================================================
架构 §4.4 要求「业务只依赖协议，不依赖具体实现」，理由有三：

1. **可替换**：一期 Milvus、将来可能换 Qdrant / 自研 —— 换实现不动业务；
2. **可降级**：Milvus 不可用时回退关键词检索，业务代码不写 ``if milvus_down``；
3. **可测试**：单测用一个假实现（内存 dict）即可覆盖 Service 逻辑，
   不需要真的起一个 Milvus（本项目 CI / 国产化环境没有 Milvus）。

本协议的方法签名刻意**不绑定 Milvus 概念**（不提 collection / partition），
只用「写入一条可检索单元 / 检索 / 删除 / 健康」这四个动作表达。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一次检索命中的单元。

    ``similarity`` 语义：**越大越相关**（向量用余弦、关键词用匹配度），
    上限 1.0。调用方用 :class:`~app.core.threshold.ThresholdEngine` 判定是否成对，
   不在 Provider 里判阈值（铁律 10：确定性结论在确定性组件里）。
    """

    talent_id: int
    similarity: float
    field_name: str | None = None
    chunk_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "talent_id": self.talent_id,
            "similarity": self.similarity,
            "field_name": self.field_name,
            "chunk_text": self.chunk_text,
        }


@runtime_checkable
class RetrievalProvider(Protocol):
    """检索提供方协议（``routers → services → retrieval → models`` 中的「retrieval」）。

    🔴 所有方法都**不得抛异常**（对齐网关 / 降级的口径）：
    失败以返回值或空结果表达，由 Service 层决定要不要降级。
    """

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
        """写入（或更新）一条可检索单元，返回其在向量库中的主键标识。

        :param vector: 预计算的向量；``None`` 时由实现自行决定（关键词实现不需要）。
        :param chunk_text: **脱敏后**的画像文本（明文身份不入向量库）。
        """
        ...

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
        """检索。``vector`` 与 ``query_text`` **至少给一个**（向量检索给前者，
        关键词检索给后者）。返回按 ``similarity`` 降序的命中列表。

        🔴 权限前置过滤由**调用方**传入的 ``tenant_id`` / ``org_ids`` 承担，
        Provider 不感知数据域（它只执行「已经带好权限条件的检索」）。
        """
        ...

    def delete(self, db: Any, *, talent_id: int, tenant_id: int) -> int:
        """删除某档案的全部检索单元，返回删除条数（幂等：不存在返回 0）。"""
        ...

    def delete_all(self, db: Any, *, talent_id: int, tenant_id: int) -> dict[str, int]:
        """删除某档案在**全部检索单元集合**中的切片，返回 ``{集合名: 删除条数}``。

        为什么在 :meth:`delete` 之外单列一个方法（CU-22 / API-77 引入）
        --------------------------------------------------------------
        API-77（审批删除）要求「删除 Milvus 向量（COL-01~03），**记录已删 chunk_pk**，
        任一分片失败 → 不执行软删」。而 :meth:`delete` 的语义是「删这个档案的检索单元」，
        实现上只作用于**默认集合** —— 用它来支撑「删除权」会漏掉另外两个集合，
        表现为「档案已删除，但换个检索入口还能召回他」（比完全不删更糟：
        已向本人承诺删除却仍可被检索到）。

        :return: ``{集合名: 条数}``。**负数表示该集合删除失败**（沿用 :meth:`delete`
            的 ``-1`` 哨兵语义）—— 调用方据此判定「部分失败」：
            此时**不执行 MySQL 软删**，回滚已删切片（重新索引），保持申请待处理。
            降级实现（无向量库）返回 ``{}``，表示「没有向量切片需要清理」。
        """
        ...

    def health(self) -> bool:
        """是否可用（供 :func:`~app.retrieval.get_retrieval_provider` 选路）。"""
        ...
