"""语义人才检索（CU-20，API-62，F-3.6 / DF-3 / CL-3）。

🔴 **权限前置是本模块存在的第一理由**（接口 API-62 备注 / B-8 整改核心落点）
==========================================================================
检索条件（``tenant_id`` + 允许的 ``org_id`` 集合）在**召回之前**下发：
向量路由经 :class:`~app.core.tenant.DataScopeFilter.milvus_expr` 生成的过滤字段，
关键词路由经同一 principal 推导的 SQL 条件 —— 两条路由**同源同权**，
「列表接口过滤了、检索接口忘了加 expr」这类越权漏洞在结构上不可能出现
（:mod:`app.core.tenant` 的模块注释就是为这一刻写的）。

❌ **严禁「先召回全量再在应用层过滤」** —— 那会把无权数据放进上下文。
响应里 ``meta.permission_filter_applied`` 恒为 ``True``，一旦出现 False 即架构违规。

降级口径（接口 API-62 降级示例）
================================
- Milvus 不可用 / 查询向量化失败 → 自动降级为**关键词检索**（HTTP 200，``code=0``，
  ``meta.degraded=true`` + ``mode_used=keyword``）；降级后**仍带权限过滤**（红线）。
- Rerank 失败**不算降级**：精排是增强而非依赖，失败时保留 RRF 融合序（``rerank_used=false``）。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import get_logger
from app.core.masking import PlaceholderTable, mask_text
from app.core.model_gateway import ModelGateway
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import org_dao, tag_dao
from app.models.talent import TalentBase
from app.retrieval import MilvusRetrievalProvider, SearchHit, get_retrieval_provider
from app.retrieval.hybrid import HybridRetriever, lexical_score
from app.retrieval.keyword_provider import KeywordRetrievalProvider
from app.schemas.ai import AiScene
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.search import (
    MatchedChunkOut,
    PermissionScopeOut,
    SearchHitOut,
    SearchMetaOut,
    SearchOut,
    SearchRequest,
)
from app.services.ai_service import build_targets
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)

#: 检索单元在 Milvus 主键中的形态（数据库 §7：``<talent_id>:<field>:<n>``）
_CHUNK_PK_SEP = ":"


class SearchService:
    """API-62 语义人才检索（非流式）。"""

    # ------------------------------------------------------------------
    @classmethod
    def search(cls, db: Session, principal: CurrentPrincipal, req: SearchRequest) -> SearchOut:
        started = time.perf_counter()

        # ---- ① 权限推导（数据域 + 前端收窄校验）----
        dept_ids = cls._resolve_dept_ids(db, principal)
        requested_org_ids = list(req.filters.org_ids) if (req.filters and req.filters.org_ids) else None
        narrowed = DataScopeFilter.assert_scope_narrowing(
            principal, requested_org_ids, dept_ids=dept_ids
        )
        allowed_org_ids: list[int] | None
        if narrowed is not None:
            allowed_org_ids = [int(i) for i in narrowed]
        elif int(principal.data_scope) == 2:  # OWN_DEPT
            allowed_org_ids = [int(i) for i in dept_ids] or None
        else:
            allowed_org_ids = None  # ALL：租户级即可（org 条件由 SQL/expr 的租户子句承担）

        # ---- ② 模式判定与召回 ----
        mode_used, degraded, degraded_reason = cls._resolve_mode(db, principal, req.mode)
        candidates: dict[int, _Candidate] = {}

        if mode_used in ("vector", "hybrid"):
            hits, query_vec_ok = cls._vector_recall(db, principal, req, allowed_org_ids)
            if not query_vec_ok:
                # 向量化失败（无可用 embedding 配置 / 网关降级）→ 降级关键词
                mode_used = "keyword"
                degraded = True
                degraded_reason = degraded_reason or "查询向量化失败，已降级为关键词检索（语义相关度可能下降）"
            else:
                cls._absorb_hits(candidates, hits, source="vector")

        if mode_used in ("keyword", "hybrid"):
            kw_hits = cls._keyword_recall(db, principal, req, allowed_org_ids)
            cls._absorb_hits(candidates, kw_hits, source="keyword", query=req.query)

        # ---- ③ 行级权限与业务过滤（候选 talent 行级复核 + filters）----
        rows = cls._load_talent_rows(db, principal, list(candidates), dept_ids=dept_ids)
        rows = cls._apply_filters(db, principal, req, rows)
        for row in rows:
            candidates[int(row.id)].talent = row
        kept_ids = {int(row.id) for row in rows}

        # ---- ④ RRF 融合（hybrid）或单路排序 ----
        retriever = HybridRetriever(k=settings.RAG_RRF_K)
        if mode_used == "hybrid":
            vec_order = [
                tid
                for tid, c in sorted(
                    ((t, c) for t, c in candidates.items() if "vector" in c.sources),
                    key=lambda kv: kv[1].best_rank("vector"),
                )
            ]
            kw_order = [
                tid
                for tid, c in sorted(
                    ((t, c) for t, c in candidates.items() if "keyword" in c.sources),
                    key=lambda kv: kv[1].best_rank("keyword"),
                )
            ]
            fused = retriever.rrf([vec_order, kw_order], key_of=lambda tid: tid)
            ordered_ids = [int(tid) for tid, _ in fused]
        else:
            ordered_ids = sorted(
                candidates,
                key=lambda tid: (
                    -candidates[tid].final_score,
                    candidates[tid].best_rank("vector" if mode_used == "vector" else "keyword"),
                    tid,
                ),
            )
        ordered_ids = [tid for tid in ordered_ids if tid in kept_ids]

        # ---- ⑤ Rerank 精排（增强，失败不降级）----
        rerank_used = False
        if req.rerank and ordered_ids:
            rerank_used, ordered_ids = cls._rerank(db, principal.tenant_id, req, candidates, ordered_ids)

        top_ids = ordered_ids[: int(req.top_k)]
        candidate_pool_size = len(ordered_ids)

        # ---- ⑥ 出参装配 ----
        org_names = cls._org_names(db, principal.tenant_id, {int(rows_map) for rows_map in []} or {
            int(candidates[tid].talent.id) and int(candidates[tid].talent.org_id or 0) for tid in top_ids
        } - {0})
        items: list[SearchHitOut] = []
        for tid in top_ids:
            cand = candidates[tid]
            items.append(cand.to_out(with_reason=req.with_reason, org_name=org_names.get(int(cand.talent.org_id or 0))))

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        meta = SearchMetaOut(
            mode_used=mode_used,  # type: ignore[arg-type]
            degraded=degraded,
            degraded_reason=degraded_reason,
            permission_filter_applied=True,
            permission_scope=PermissionScopeOut(
                tenant_id=principal.tenant_id, org_ids=allowed_org_ids
            ),
            candidate_pool_size=candidate_pool_size,
            rerank_used=rerank_used,
            elapsed_ms=elapsed_ms,
        )

        # ---- ⑦ 审计（检索行为留痕，F-1.5；query 原文用于金标准查询集 IDX-01）----
        AuditService.record(
            AuditAction.TALENT_SEARCH,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            after={
                "query": req.query,
                "mode": req.mode,
                "mode_used": mode_used,
                "degraded": degraded,
                "top_k": req.top_k,
                "result_count": len(items),
            },
        )

        return SearchOut(items=items, meta=meta)

    # ==================================================================
    # 内部
    # ==================================================================
    @staticmethod
    def _resolve_dept_ids(db: Session, principal: CurrentPrincipal) -> tuple[int, ...]:
        """``data_scope=2`` 时解析部门子树（含自身）；其余范围为空元组。"""
        if int(principal.data_scope) == 2 and not principal.dept_ids and principal.org_id is not None:
            return OrgScopeService.resolve_dept_ids(db, principal.tenant_id, principal.org_id)
        return tuple(principal.dept_ids)

    @staticmethod
    def _resolve_mode(
        db: Session, principal: CurrentPrincipal, mode: str
    ) -> tuple[str, bool, str | None]:
        """解析实际使用的模式（``auto`` 按可用性选择；不可用 → 关键词降级）。"""
        if mode == "keyword":
            return "keyword", False, None
        milvus = MilvusRetrievalProvider()
        milvus_ok = bool(milvus.health())
        if mode in ("vector", "hybrid") and not milvus_ok:
            return (
                "keyword",
                True,
                "Milvus 不可用，已降级为关键词检索（语义相关度可能下降）",
            )
        if mode == "auto":
            return ("hybrid", False, None) if milvus_ok else (
                "keyword",
                True,
                "向量检索不可用，已降级为关键词检索（语义相关度可能下降）",
            )
        return mode, False, None  # vector / hybrid 且 Milvus 可用

    @staticmethod
    def _vector_recall(
        db: Session,
        principal: CurrentPrincipal,
        req: SearchRequest,
        allowed_org_ids: list[int] | None,
    ) -> tuple[list[SearchHit], bool]:
        """向量召回。🔴 权限前置：``tenant_id`` + ``org_ids`` 在检索前下发（CR-11）。"""
        query_vec = SearchService._embed_query(db, principal.tenant_id, req.query)
        if query_vec is None:
            return [], False
        provider = get_retrieval_provider()
        try:
            hits = provider.search(
                db,
                tenant_id=principal.tenant_id,
                org_ids=allowed_org_ids,
                top_k=max(settings.RAG_RECALL_TOP_K, int(req.top_k)),
                vector=query_vec,
            )
        except Exception as exc:  # noqa: BLE001 — Provider 契约：不抛，但双保险
            logger.warning("向量检索失败 tid=%s: %s", principal.tenant_id, exc)
            return [], False
        return list(hits), True

    @staticmethod
    def _embed_query(db: Session, tenant_id: int, query: str) -> list[float] | None:
        """查询向量化（网关 ``scene=embedding``）。失败返回 ``None``（调用方降级）。"""
        targets = build_targets(db, tenant_id, AiScene.EMBEDDING)
        if not targets:
            return None
        gateway = ModelGateway(tenant_id)
        try:
            result = gateway.call(targets, texts=[query])
            if result.ok and result.vectors:
                return result.vectors[0]
            return None
        finally:
            gateway.close()

    @staticmethod
    def _keyword_recall(
        db: Session,
        principal: CurrentPrincipal,
        req: SearchRequest,
        allowed_org_ids: list[int] | None,
    ) -> list[SearchHit]:
        """关键词召回（MySQL LIKE）。降级路径同样带权限条件（🔴 架构红线）。"""
        provider = KeywordRetrievalProvider()
        try:
            hits = provider.search(
                db,
                tenant_id=principal.tenant_id,
                org_ids=allowed_org_ids,
                top_k=max(settings.RAG_RECALL_TOP_K, int(req.top_k)),
                query_text=req.query,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("关键词检索失败 tid=%s: %s", principal.tenant_id, exc)
            return []
        # 关键词实现的 similarity 恒为 1.0（无数值化能力）—— 用确定性词法分重排
        reranked: list[SearchHit] = []
        for hit in hits:
            score = lexical_score(req.query, hit.chunk_text or "")
            reranked.append(
                SearchHit(
                    talent_id=hit.talent_id,
                    similarity=score if score > 0 else hit.similarity,
                    field_name=hit.field_name,
                    chunk_text=hit.chunk_text,
                )
            )
        reranked.sort(key=lambda h: (-h.similarity, h.talent_id))
        return reranked

    @staticmethod
    def _absorb_hits(
        candidates: dict[int, "_Candidate"],
        hits: list[SearchHit],
        *,
        source: str,
        query: str | None = None,
    ) -> None:
        """把一路召回吸收进候选表（记录每路的**最优排名**与全部命中片段）。"""
        for rank, hit in enumerate(hits, start=1):
            tid = int(hit.talent_id)
            cand = candidates.setdefault(tid, _Candidate(talent_id=tid))
            cand.ranks.setdefault(source, rank)
            snippet = hit.chunk_text
            if source == "keyword" and snippet:
                # 关键词路径的 chunk_text 是姓名/字段原文 —— 分数用确定性词法分
                score = lexical_score(query or "", snippet)
            else:
                score = max(0.0, min(1.0, float(hit.similarity)))
            if snippet and score > 0:
                cand.chunks.append(
                    {
                        "field_name": hit.field_name,
                        "snippet": snippet,
                        "score": round(score, 4),
                        "source": source,
                    }
                )
            cand.sources.add(source)

    @staticmethod
    def _load_talent_rows(
        db: Session,
        principal: CurrentPrincipal,
        talent_ids: list[int],
        *,
        dept_ids: tuple[int, ...],
    ) -> list[TalentBase]:
        """按候选 ID 取档案行，**并套行级数据域条件**（检索召回 ≠ 可见，最终以 MySQL 行级为准）。"""
        if not talent_ids:
            return []
        scope_clause = DataScopeFilter.mysql_condition(
            TalentBase, principal, dept_ids=dept_ids, owner_column="user_id"
        )
        stmt = (
            select(TalentBase)
            .where(
                TalentBase.tenant_id == principal.tenant_id,
                TalentBase.id.in_(talent_ids),
                TalentBase.is_deleted == 0,
                TalentBase.data_status.in_((0, 1)),  # 待复核 / 有效可检索；已合并/归档/删除不可
                scope_clause,
            )
            .order_by(TalentBase.id)
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def _apply_filters(
        db: Session, principal: CurrentPrincipal, req: SearchRequest, rows: list[TalentBase]
    ) -> list[TalentBase]:
        """业务过滤：学历下限 / 工作年限下限 / 标签（确定性，逐条可解释）。"""
        filters = req.filters
        if filters is None:
            return rows

        out = rows
        if filters.work_years_min is not None:
            out = [r for r in out if (r.work_years or 0) >= int(filters.work_years_min)]

        if filters.education_level_min is not None:
            codes = SearchService._education_order(db, principal.tenant_id)
            if codes is None or filters.education_level_min not in codes:
                from app.exceptions import BizError, ErrorCode

                raise BizError(
                    ErrorCode.PARAM_INVALID,
                    "education_level_min 不是启用的学历字典码",
                    {"fields": [{"field": "filters.education_level_min", "message": "字典码不存在或未启用"}]},
                )
            min_idx = codes.index(filters.education_level_min)
            out = [r for r in out if r.education_level and r.education_level in codes and codes.index(r.education_level) >= min_idx]

        if filters.tag_codes:
            wanted = {str(c) for c in filters.tag_codes}
            kept: list[TalentBase] = []
            for row in out:
                owned = {str(rel.tag_code) for rel in tag_dao.list_talent_tags(db, principal.tenant_id, int(row.id))}
                if owned & wanted:  # 任意命中即保留（口径：标签是「信号」不是「硬性要求」）
                    kept.append(row)
            out = kept
        return out

    @staticmethod
    def _education_order(db: Session, tenant_id: int) -> list[str] | None:
        """学历字典码的**次序列表**（由启用项排序决定「下限」语义）；查不到 min 码 → None。"""
        from app.dao import dict_dao

        items = dict_dao.list_items(db, tenant_id, "education_level", status=1)
        codes = [str(item.item_code) for item in items]
        return codes or None

    @staticmethod
    def _rerank(
        db: Session,
        tenant_id: int,
        req: SearchRequest,
        candidates: dict[int, "_Candidate"],
        ordered_ids: list[int],
    ) -> tuple[bool, list[int]]:
        """Rerank 精排。失败保留原序（``rerank_used=False``，不算降级）。"""
        from app.schemas.ai import AiScene

        try:
            targets = build_targets(db, tenant_id, AiScene.RERANK)
        except Exception as exc:  # noqa: BLE001 — 精排是增强，配置/DB 异常都不应打断检索
            logger.warning("读取 rerank 模型配置失败（跳过精排）：%s", exc)
            return False, ordered_ids
        if not targets:
            return False, ordered_ids
        documents = [
            " ".join(str(c.get("snippet") or "") for c in candidates[tid].chunks)
            or (candidates[tid].talent.name if candidates[tid].talent is not None else "")
            for tid in ordered_ids
        ]
        if not any(documents):
            return False, ordered_ids
        gateway = ModelGateway(tenant_id)
        try:
            result = gateway.call(targets, query=req.query, documents=documents[: settings.RAG_RECALL_TOP_K])
        finally:
            gateway.close()
        if not (result.ok and result.scores):
            return False, ordered_ids
        scored = list(zip(ordered_ids[: len(result.scores)], result.scores))
        scored.sort(key=lambda kv: (-min(max(float(kv[1]), 0.0), 1.0), kv[0]))
        reranked_ids = [int(tid) for tid, _ in scored]
        for tid, score in scored:
            candidates[int(tid)].rerank_score = min(max(float(score), 0.0), 1.0)
        tail = [tid for tid in ordered_ids if tid not in set(reranked_ids)]
        return True, reranked_ids + tail

    @staticmethod
    def _org_names(db: Session, tenant_id: int, org_ids: set[int]) -> dict[int, str]:
        if not org_ids:
            return {}
        return {
            int(d.id): str(d.dept_name)
            for d in org_dao.list_depts(db, tenant_id)
            if int(d.id) in org_ids
        }


class _Candidate:
    """单个候选人才在本次检索中的聚合状态（**进程内临时对象**，不落库）。"""

    __slots__ = ("talent_id", "ranks", "chunks", "sources", "rerank_score", "talent")

    def __init__(self, *, talent_id: int) -> None:
        self.talent_id = talent_id
        #: 每路召回的最优排名（``{"vector": 3, "keyword": 1}``）
        self.ranks: dict[str, int] = {}
        #: 命中片段（``{field_name, snippet, score, source}``）
        self.chunks: list[dict[str, Any]] = []
        self.sources: set[str] = set()
        self.rerank_score: float | None = None
        #: 行级复核后回填的 ORM 行（不可见候选为 ``None``）
        self.talent: TalentBase | None = None

    def best_rank(self, source: str) -> int:
        return self.ranks.get(source, 10**9)

    @property
    def best_chunk_score(self) -> float:
        return max((float(c["score"]) for c in self.chunks), default=0.0)

    @property
    def final_score(self) -> float:
        """展示分：精排分 > 最优片段分。均为 0~1，口径见 SearchHitOut.score。"""
        if self.rerank_score is not None:
            return round(self.rerank_score, 4)
        return self.best_chunk_score

    def to_out(self, *, with_reason: bool, org_name: str | None) -> SearchHitOut:
        from app.schemas.common import SnowflakeId  # noqa: F401 — 仅为可读性

        talent = self.talent
        reasons: list[str] = []
        if with_reason:
            for chunk in sorted(self.chunks, key=lambda c: -float(c["score"]))[:3]:
                if chunk.get("source") == "vector":
                    reasons.append(
                        f"画像片段语义相关度 {float(chunk['score']):.2f}"
                        + (f"（{chunk['field_name']}）" if chunk.get("field_name") else "")
                    )
                else:
                    reasons.append(
                        "命中原文字段关键词"
                        + (f"「{chunk['field_name']}」" if chunk.get("field_name") else "")
                    )
            if talent is not None and talent.work_years is not None:
                reasons.append(f"工作年限 {talent.work_years} 年")
        # 🔴 片段脱敏（姓名→{{CANDIDATE}}）：检索结果面向列表场景，无需暴露真名
        table = PlaceholderTable()
        name = talent.name if talent is not None else None
        matched = [
            MatchedChunkOut(
                chunk_pk=f"{self.talent_id}{_CHUNK_PK_SEP}{c.get('field_name') or 'profile'}{_CHUNK_PK_SEP}{i}",
                field_name=c.get("field_name"),
                snippet=mask_text(c.get("snippet"), table, name=name) if c.get("snippet") else None,
                score=round(float(c["score"]), 4),
            )
            for i, c in enumerate(sorted(self.chunks, key=lambda c: -float(c["score"]))[:3])
        ]
        return SearchHitOut(
            talent_id=self.talent_id,
            name=name or "",
            org_id=(int(talent.org_id) if talent is not None and talent.org_id is not None else None),
            org_name=org_name,
            work_years=(int(talent.work_years) if talent is not None and talent.work_years is not None else None),
            score=self.final_score,
            hit_reasons=reasons,
            matched_chunks=matched,
        )
