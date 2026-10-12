"""语义人才检索服务（CU-20，API-62）—— L1 契约层（外部依赖全部打桩）。

🔴 覆盖重点（接口 API-62 的红线）：
1. 权限前置 —— ``filters.org_ids`` 只能收窄，越出数据域 40301；
2. 降级 —— Milvus 不可用自动落关键词，``meta.degraded=true`` 且**仍带权限过滤**；
3. 片段脱敏 —— ``matched_chunks[].snippet`` 姓名 → ``{{CANDIDATE}}``（前端不回填）；
4. 行级复核 —— 召回命中 ≠ 可见，最终以 MySQL 行级条件为准。
"""

from __future__ import annotations

import pytest

from app.core.tenant import CurrentPrincipal
from app.models.enums import RoleDataScope
from app.models.talent import TalentBase
from app.retrieval import SearchHit
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


def _principal(*, data_scope: int = int(RoleDataScope.ALL), dept_ids: tuple[int, ...] = ()) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=9001,
        tenant_id=1,
        org_id=10 if dept_ids else None,
        roles=("HR",),
        data_scope=data_scope,
        dept_ids=dept_ids,
    )


def _talent(tid: int, name: str, *, org_id: int | None = 10, work_years: int | None = None) -> TalentBase:
    return TalentBase(
        id=tid,
        tenant_id=1,
        name=name,
        org_id=org_id,
        work_years=work_years,
        data_status=1,
    )


@pytest.fixture
def no_milvus(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 ``MilvusRetrievalProvider.health`` 恒为 False（本机无 Milvus 的确定性等价）。"""
    from app.retrieval import milvus_provider

    monkeypatch.setattr(milvus_provider.MilvusRetrievalProvider, "health", lambda self: False)


@pytest.fixture
def fake_keyword_hits(monkeypatch: pytest.MonkeyPatch):
    """打桩关键词召回（绕开真实 SQL；返回按相关度排好序的 SearchHit）。"""

    def _install(hits: list[SearchHit]) -> list[SearchHit]:
        from app.retrieval import keyword_provider

        monkeypatch.setattr(keyword_provider.KeywordRetrievalProvider, "search", lambda self, *a, **kw: list(hits))
        return hits

    return _install


@pytest.fixture
def fake_talent_rows(monkeypatch: pytest.MonkeyPatch):
    """打桩行级复核（召回命中 → 可见行）。"""

    def _install(rows: list[TalentBase]) -> list[TalentBase]:
        monkeypatch.setattr(SearchService, "_load_talent_rows", classmethod(lambda cls, db, principal, ids, **kw: rows))
        monkeypatch.setattr(
            "app.dao.tag_dao.list_talent_tags", lambda db, tenant_id, talent_id: []
        )
        monkeypatch.setattr("app.dao.org_dao.list_depts", lambda db, tenant_id, **kw: [])
        return rows

    return _install


# ============================================================================
# 权限（🔴 红线：只能收窄不能放宽）
# ============================================================================
def test_search_org_ids_out_of_scope_40301(no_milvus) -> None:  # noqa: ARG001
    principal = _principal(data_scope=int(RoleDataScope.OWN_DEPT), dept_ids=(10,))
    req = SearchRequest(query="数据治理人才", filters={"org_ids": [999]})
    with pytest.raises(Exception) as exc_info:  # noqa: PT011 — PermissionDeniedError
        SearchService.search(None, principal, req)
    assert getattr(exc_info.value, "code", None) == 40301


def test_search_narrowing_within_scope_passes(no_milvus, fake_talent_rows, fake_keyword_hits) -> None:
    principal = _principal(data_scope=int(RoleDataScope.OWN_DEPT), dept_ids=(10, 11))
    fake_keyword_hits([])
    fake_talent_rows([])
    req = SearchRequest(query="数据治理人才", filters={"org_ids": [11]})
    out = SearchService.search(None, principal, req)
    assert out.meta.permission_filter_applied is True
    assert out.meta.permission_scope.org_ids == [11]


# ============================================================================
# 降级（🔴 降级不抛 500；降级仍带权限过滤）
# ============================================================================
def test_auto_mode_degrades_to_keyword_when_no_milvus(
    no_milvus, fake_talent_rows, fake_keyword_hits
) -> None:
    fake_keyword_hits([])
    fake_talent_rows([])
    out = SearchService.search(None, _principal(), SearchRequest(query="算法工程师"))
    assert out.meta.mode_used == "keyword"
    assert out.meta.degraded is True
    assert out.meta.degraded_reason is not None
    assert out.meta.permission_filter_applied is True  # 🔴 降级不放宽权限


def test_keyword_mode_not_degraded(no_milvus, fake_talent_rows, fake_keyword_hits) -> None:
    fake_keyword_hits([])
    fake_talent_rows([])
    out = SearchService.search(None, _principal(), SearchRequest(query="算法工程师", mode="keyword"))
    assert out.meta.mode_used == "keyword"
    assert out.meta.degraded is False


# ============================================================================
# 命中理由 / 片段脱敏 / 排序
# ============================================================================
def test_hits_ranked_by_lexical_and_snippet_masked(
    no_milvus, fake_talent_rows, fake_keyword_hits
) -> None:
    rows = [_talent(101, "李伟"), _talent(102, "王强")]
    fake_talent_rows(rows)
    # 李伟的片段与查询高度重合（且含真名 → 验证脱敏）；王强完全不相关（词法分 0 → 保留 1.0）
    fake_keyword_hits(
        [
            SearchHit(talent_id=101, similarity=1.0, field_name=None, chunk_text="李伟 数据治理 主数据平台"),
            SearchHit(talent_id=102, similarity=1.0, field_name=None, chunk_text="前端开发"),
        ]
    )
    out = SearchService.search(
        None, _principal(), SearchRequest(query="数据治理主数据平台", mode="keyword", with_reason=True)
    )
    assert len(out.items) == 2
    assert out.items[0].talent_id == 101  # Python 实例内为 int；JSON 序列化才转字符串
    assert out.items[0].score >= out.items[1].score
    # 🔴 片段是脱敏文本：姓名 → {{CANDIDATE}}（检索场景不暴露真名，真名只在 name 字段）
    assert out.items[0].matched_chunks, "李伟应有命中片段"
    assert out.items[0].matched_chunks[0].snippet is not None
    assert "李伟" not in (out.items[0].matched_chunks[0].snippet or "")
    assert out.items[0].hit_reasons  # with_reason=True 必有理由


def test_recall_hit_but_row_invisible_is_dropped(
    no_milvus, fake_talent_rows, fake_keyword_hits
) -> None:
    """召回命中但行级复核不可见（域外/已合并）→ 不出现在结果里（防越权泄漏）。"""
    fake_talent_rows([])  # 行级复核一行都不可见
    fake_keyword_hits([SearchHit(talent_id=101, similarity=1.0, chunk_text="数据治理")])
    out = SearchService.search(None, _principal(), SearchRequest(query="数据治理", mode="keyword"))
    assert out.items == []
    assert out.meta.candidate_pool_size == 0


def test_top_k_truncates(no_milvus, fake_talent_rows, fake_keyword_hits) -> None:
    rows = [_talent(200 + i, f"人才{i}") for i in range(5)]
    fake_talent_rows(rows)
    fake_keyword_hits(
        [SearchHit(talent_id=200 + i, similarity=1.0, chunk_text=f"数据治理方向{i}") for i in range(5)]
    )
    out = SearchService.search(None, _principal(), SearchRequest(query="数据治理", mode="keyword", top_k=3))
    assert len(out.items) == 3
    assert out.meta.candidate_pool_size == 5


def test_work_years_filter(no_milvus, fake_talent_rows, fake_keyword_hits) -> None:
    rows = [_talent(301, "资深", work_years=6), _talent(302, "新人", work_years=1)]
    fake_talent_rows(rows)
    fake_keyword_hits(
        [
            SearchHit(talent_id=301, similarity=1.0, chunk_text="数据治理"),
            SearchHit(talent_id=302, similarity=1.0, chunk_text="数据治理"),
        ]
    )
    out = SearchService.search(
        None, _principal(), SearchRequest(query="数据治理", mode="keyword", filters={"work_years_min": 3})
    )
    assert [item.talent_id for item in out.items] == [301]
    assert out.items[0].work_years == 6
