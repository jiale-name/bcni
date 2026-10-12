"""档案 RAG 问答服务（CU-21，API-63）—— L1 契约层（外部依赖全部打桩）。

🔴 覆盖重点 = API-63 五条红线：
1. 权限裁剪（全部越权 40301 不进流 / 部分越权 clipped=true）；
2. 脱敏出网（二次扫描残留 → NER_RESIDUAL 拒答，绝不降级为明文出网）；
3. 本地回填（占位符解析不了 → MASK_REQUIRED 拒答）；
4. 引用溯源（引用编号必须可定位；无引用 = 缺陷 → 拒答）；
5. 无答案兜底（阈值之下**不调用大模型** —— model=None 可直接验证）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from app.core.tenant import CurrentPrincipal
from app.models.enums import RoleDataScope
from app.models.talent import TalentBase
from app.schemas.search import AskRequest, ScopeEffectiveOut
from app.services.ask_service import AskService


def _principal(*, data_scope: int = int(RoleDataScope.ALL), dept_ids: tuple[int, ...] = ()) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=9001,
        tenant_id=1,
        org_id=10 if dept_ids else None,
        roles=("HR",),
        data_scope=data_scope,
        dept_ids=dept_ids,
    )


def _talent(tid: int, name: str = "李伟") -> TalentBase:
    return TalentBase(id=tid, tenant_id=1, name=name, org_id=10, data_status=1)


def _req(question: str, talent_ids: list[int] | None = None, **kw: Any) -> AskRequest:
    ids = talent_ids or [101]
    return AskRequest(
        scope={"type": "single" if len(ids) == 1 else "batch", "talent_ids": ids},
        question=question,
        stream=False,
        **kw,
    )

def _install_score_09(monkeypatch: pytest.MonkeyPatch) -> None:
    """全部片段置 0.9（越过兜底阈值，直达出网/回填/引用链路）。"""
    def _fake(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks
    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake))


def _install_chunks(monkeypatch: pytest.MonkeyPatch, *, text: str, masked: str) -> None:
    """打桩片段组装（含占位符登记 —— 与真实 _assemble_chunks 行为一致）。"""
    def _fake(cls, db, tid, talents, table):  # noqa: ANN001
        table.put("candidate", "李伟")
        return [{"talent_id": 101, "field_code": "profile", "field_name": "基本画像",
                 "text": text, "masked_text": masked, "chunk_pk": "101:profile:0"}]
    monkeypatch.setattr(AskService, "_assemble_chunks", classmethod(_fake))


# ============================================================================
# 打桩
# ============================================================================
@pytest.fixture
def clip_all(monkeypatch: pytest.MonkeyPatch):
    """打桩权限裁剪：返回全部请求的档案（通过）。"""

    def _install(rows: list[TalentBase], denied: list[int] | None = None) -> ScopeEffectiveOut:
        effective = ScopeEffectiveOut(
            requested=len(rows) + len(denied or []),
            allowed=len(rows),
            clipped=bool(denied),
            denied_talent_ids=[str(i) for i in (denied or [])],
        )
        monkeypatch.setattr(AskService, "_clip_scope", classmethod(lambda cls, db, principal, req: (rows, effective)))
        return effective

    return _install


@pytest.fixture
def stub_model_gateway(monkeypatch: pytest.MonkeyPatch):
    """打桩模型网关：不发出任何 HTTP 请求，返回预置内容。"""

    @dataclass
    class _FakeTarget:
        model_name: str = "fake-llm"
        provider: str = "ollama"
        deployment: str = "local"
        is_cloud: bool = False
        price_in_per_1k: Decimal = Decimal("0")
        price_out_per_1k: Decimal = Decimal("0")

    @dataclass
    class _FakeResult:
        ok: bool = True
        content: str | None = None
        tokens_in: int = 0
        tokens_out: int = 0
        latency_ms: int = 1
        fallback_used: bool = False
        cloud_used: bool = False
        target: _FakeTarget | None = field(default_factory=_FakeTarget)
        degraded: bool = False
        degraded_reason: str | None = None
        error_code: str | None = None
        error_msg: str | None = None

    class _FakeGateway:
        calls: list[dict[str, Any]] = []
        result: _FakeResult = _FakeResult(ok=True, content="")

        def __init__(self, tenant_id: int, **kw: Any) -> None:
            pass

        def call(self, targets: list, **kw: Any) -> "_FakeResult":
            _FakeGateway.calls.append(kw)
            return _FakeGateway.result

        def close(self) -> None:
            pass

    _FakeGateway._FakeResult = _FakeResult
    monkeypatch.setattr("app.services.ask_service.ModelGateway", _FakeGateway)
    return _FakeGateway


@pytest.fixture
def no_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    """问答的向量叠加退词法分（无 Milvus / 无 embedding 配置的确定性等价）。"""
    import app.retrieval as retrieval_pkg
    from app.retrieval import KeywordRetrievalProvider

    monkeypatch.setattr(retrieval_pkg, "get_retrieval_provider", lambda: KeywordRetrievalProvider())


@pytest.fixture
def no_parse_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dao import talent_dao

    monkeypatch.setattr(talent_dao, "list_fields_by_talent", lambda db, tenant_id, talent_id: [])


@pytest.fixture
def ask_targets(monkeypatch: pytest.MonkeyPatch):
    """只给 ``talent_ask`` 场景装一个假目标（供网关打桩命中；其余场景无配置）。"""
    from app.core.model_gateway import ModelTarget

    fake = ModelTarget(
        id=1,
        scene="talent_ask",
        model_code="fake",
        display_name="假模型",
        provider="ollama",
        deployment="local",
        base_url="http://127.0.0.1:1",
        model_name="fake-llm",
    )

    def _targets(db: Any, tenant_id: int, scene: str) -> list[ModelTarget]:
        return [fake] if scene == "talent_ask" else []

    monkeypatch.setattr("app.services.ask_service.build_targets", _targets)
    return fake


class _EmptySession:
    """恒返回空结果集的假会话（``_clip_scope`` 的 L1 等价：域内查无此档）。"""

    def execute(self, *a: Any, **kw: Any):  # noqa: ANN002, ANN003
        class _R:
            def scalars(self) -> "_R":
                return self

            def all(self) -> list:
                return []

        return _R()


# ============================================================================
# 红线 1 · 权限裁剪
# ============================================================================
def test_all_denied_40301_before_stream() -> None:
    """全部越权 → 40301，不进流（PermissionDeniedError 必须在握手期抛出）。"""
    from app.exceptions import PermissionDeniedError

    principal = _principal(data_scope=int(RoleDataScope.ALL))
    with pytest.raises(PermissionDeniedError) as exc_info:
        AskService._clip_scope(_EmptySession(), principal, _req("谁擅长数据治理", talent_ids=[999]))
    assert exc_info.value.data == {"denied_talent_ids": ["999"]}


def test_partial_clipping_marks_scope_effective(clip_all, no_vector, no_parse_fields) -> None:  # noqa: ARG001
    effective = clip_all([_talent(101)], denied=[999])
    assert effective.clipped is True
    assert effective.denied_talent_ids == [999]


# ============================================================================
# 红线 5 · 无答案兜底（不调用大模型）
# ============================================================================
def test_low_similarity_refusal_skips_model(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    clip_all([_talent(101, "李伟")])
    stub_model_gateway.calls.clear()
    # 问题与画像完全无关 → 词法分 0 < 0.35
    result = AskService.ask(None, _principal(), _req("量子力学的哥本哈根诠释是什么"))
    assert result.refusal is not None
    assert result.refusal.reason == "LOW_SIMILARITY"
    assert result.model is None, "兜底拒答不得调用大模型"
    assert result.done.fallback is True
    assert result.done.tokens_in == 0
    assert stub_model_gateway.calls == [], "网关调用次数必须为 0"


# ============================================================================
# 红线 2 · 脱敏出网（二次扫描残留 → 拒答）
# ============================================================================
def test_ner_residual_blocks_outbound(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    clip_all([_talent(101, "李伟")])
    stub_model_gateway.calls.clear()
    residual = "姓名：李伟，手机 13812345678，治理经验丰富"

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text=residual, masked=residual)
    result = AskService.ask(None, _principal(), _req("这个人擅长什么"))
    assert result.refusal is not None
    assert result.refusal.reason == "NER_RESIDUAL"
    assert result.model is None
    assert stub_model_gateway.calls == [], "残留明文身份必须拦截在网关之前"


# ============================================================================
# 红线 3 + 4 · 本地回填与引用溯源（正常链路）
# ============================================================================
def test_answer_rehydrated_and_citation_locatable(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, ask_targets, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    clip_all([_talent(101, "李伟")])
    masked = "姓名：{{CANDIDATE}}；擅长数据治理"

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text="姓名：李伟；擅长数据治理", masked=masked)
    # 模型只输出占位符形态 —— 回填后前端才能看到真名
    stub_model_gateway.result = stub_model_gateway._FakeResult(ok=True, content="{{CANDIDATE}} 擅长数据治理 [1]")

    result = AskService.ask(None, _principal(), _req("谁擅长数据治理"))
    assert result.refusal is None
    assert result.answer is not None
    assert "{{" not in result.answer, "占位符必须已在服务端回填"
    assert "李伟" in result.answer
    assert result.done.finish_reason == "stop"
    assert result.done.citation_count == 1
    assert result.citations[0].talent_id == 101
    assert result.citations[0].chunk_pk == "101:profile:0"
    assert result.citations[0].snippet.startswith("姓名：李伟"), "引用片段与回答同口径（回填后）"
    assert result.mask_applied is True


def test_answer_without_locatable_citation_is_refused(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, ask_targets, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    """红线 4：citation_count=0 的 done 视为缺陷 → 按无依据拒答（不把裸回答发给用户）。"""
    clip_all([_talent(101, "李伟")])

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text="姓名：李伟", masked="姓名：{{CANDIDATE}}")
    stub_model_gateway.result = stub_model_gateway._FakeResult(ok=True, content="李伟很适合。")  # 无 [n] 标记

    result = AskService.ask(None, _principal(), _req("谁擅长数据治理"))
    assert result.refusal is not None
    assert result.answer is None
    assert result.done.citation_count == 0


def test_hallucinated_citation_index_is_dropped(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, ask_targets, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    """引用编号超出送模型片段范围（幻觉引用）→ 剔除；剔除后为空则拒答。"""
    clip_all([_talent(101, "李伟")])

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text="姓名：李伟", masked="姓名：{{CANDIDATE}}")
    stub_model_gateway.result = stub_model_gateway._FakeResult(ok=True, content="李伟适合 [1]，王强适合 [9]")

    result = AskService.ask(None, _principal(), _req("谁适合"))
    # [9] 不在片段范围内 → 剔除；[1] 有效 → 正常作答
    assert result.done.citation_count == 1
    assert result.citations[0].index == 1


# ============================================================================
# 网关降级（永不抛 500）
# ============================================================================
def test_gateway_degradation_returns_error_result(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, ask_targets, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    clip_all([_talent(101, "李伟")])

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text="姓名：李伟", masked="姓名：{{CANDIDATE}}")
    stub_model_gateway.result = stub_model_gateway._FakeResult(
        ok=False, degraded=True, degraded_reason="备选链全部不可用", error_code="50301", error_msg="AI 服务降级"
    )

    result = AskService.ask(None, _principal(), _req("谁擅长数据治理"))
    assert result.degraded is True
    assert result.done.finish_reason == "error"
    assert result.answer is None


def test_rehydrate_failure_refuses_mask_required(
    clip_all, no_vector, no_parse_fields, stub_model_gateway, ask_targets, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ARG001
    """红线 3：回填失败（占位符解析不了）→ MASK_REQUIRED 拒答，绝不下发占位符。"""
    clip_all([_talent(101, "李伟")])

    def _fake_score(cls, db, principal, req, chunks):  # noqa: ANN001
        for chunk in chunks:
            chunk["score"] = 0.9
        return chunks

    monkeypatch.setattr(AskService, "_score_chunks", classmethod(_fake_score))
    _install_chunks(monkeypatch, text="姓名：李伟", masked="姓名：{{CANDIDATE}}")
    # 模型输出了**表中不存在**的占位符（如 {{PHONE}} 从未登记）→ 回填必然失败
    stub_model_gateway.result = stub_model_gateway._FakeResult(ok=True, content="他的联系方式是 {{PHONE}} [1]")

    result = AskService.ask(None, _principal(), _req("联系方式是什么"))
    assert result.refusal is not None
    assert result.refusal.reason == "MASK_REQUIRED"
    assert result.answer is None
