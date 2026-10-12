"""语义检索与 RAG 问答契约（CU-20 / CU-21，API-62 / API-63）。

🔴 两处与接口文档的**有意偏离**（评审文档「上游不一致」登记）：

1. 接口 API-62 示例把 ``meta`` 画在响应**顶层**（``code/message/data/meta/trace_id``），
   而 :class:`~app.schemas.common.CommonResp` 是 ``extra="forbid"``（五件套固定）——
   故 ``meta`` 收进 ``data`` 内（``data.items`` + ``data.meta``），字段本身与示例一一对应；
2. 接口写 ``filters.education_level_min`` 为「int 字典码」，但 ``talent_base.education_level``
   是**字典码字符串**（CU-14 建模口径）—— 契约按字符串收，避免「传 int 永远过滤为空」。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import SnowflakeId

# ============================================================================
# API-62 语义人才检索
# ============================================================================
class SearchFilters(BaseModel):
    """检索过滤条件（前端传参**只能收窄**，越出数据域 → 40301，CR-11 / 接口 §1.3）。"""

    model_config = ConfigDict(extra="forbid")

    org_ids: list[SnowflakeId] | None = Field(None, max_length=50, description="组织过滤（只收窄不放宽）")
    education_level_min: str | None = Field(None, max_length=32, description="学历下限（字典码）")
    work_years_min: int | None = Field(None, ge=0, le=60, description="工作年限下限")
    tag_codes: list[str] | None = Field(None, max_length=10, description="标签过滤（取启用项）")


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=2, max_length=500, description="自然语言查询")
    top_k: int = Field(10, ge=1, le=50, description="返回条数")
    mode: Literal["auto", "vector", "keyword", "hybrid"] = Field("auto", description="检索模式")
    filters: SearchFilters | None = None
    with_reason: bool = Field(True, description="是否返回命中理由")
    rerank: bool = Field(True, description="是否启用 Rerank 精排")


class MatchedChunkOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_pk: str = Field(..., description="向量库主键（<talent_id>:<field>:<n>）")
    field_name: str | None = None
    #: 🔴 **脱敏文本**（姓名→{{CANDIDATE}}）；前端不做本地回填（接口 API-62 备注）
    snippet: str | None = None
    score: float


class SearchHitOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    name: str
    org_id: SnowflakeId | None = None
    org_name: str | None = None
    work_years: int | None = None
    score: float = Field(..., description="融合（或精排）后的相关度分")
    hit_reasons: list[str] = Field(default_factory=list)
    matched_chunks: list[MatchedChunkOut] = Field(default_factory=list)


class PermissionScopeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: SnowflakeId
    org_ids: list[SnowflakeId] | None = None


class SearchMetaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_used: Literal["vector", "keyword", "hybrid"]
    degraded: bool = False
    degraded_reason: str | None = None
    #: 🔴 恒为 True —— 检索前置过滤（CR-11）已生效；False 即架构违规
    permission_filter_applied: bool = True
    permission_scope: PermissionScopeOut
    candidate_pool_size: int = 0
    rerank_used: bool = False
    elapsed_ms: int = 0


class SearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SearchHitOut]
    meta: SearchMetaOut


# ============================================================================
# API-63 档案 RAG 问答
# ============================================================================
class AskScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["single", "batch"]
    talent_ids: list[SnowflakeId] = Field(..., min_length=1, max_length=50)

    @model_validator(mode="after")
    def _check_single(self) -> "AskScope":
        if self.type == "single" and len(self.talent_ids) != 1:
            raise ValueError("single 模式下 talent_ids 只能包含 1 个档案")
        return self


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: AskScope
    question: str = Field(..., min_length=2, max_length=1000)
    session_id: str | None = Field(None, max_length=64, description="多轮会话 ID；不传则新建")
    stream: bool = Field(True, description="true 返回 SSE；false 返回整包")
    top_k: int = Field(5, ge=1, le=10, description="送模型的片段数")


class ScopeEffectiveOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: int
    allowed: int
    #: 🔴 true 时前端必须提示「已按您的权限裁剪范围」（接口红线 1）
    clipped: bool = False
    denied_talent_ids: list[SnowflakeId] = Field(default_factory=list)


class AskCitationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    talent_id: SnowflakeId
    field_code: str
    field_name: str
    #: 引用片段（**回填后**的真值形态 —— 引用与回答同口径，接口示例即真名）
    snippet: str
    chunk_pk: str


class AskRefusalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["LOW_SIMILARITY", "MASK_REQUIRED", "NER_RESIDUAL"]
    message: str


class AskDoneOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finish_reason: Literal["stop", "refused", "error"]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_yuan: str = "0.0000"
    citation_count: int = 0
    clipped: bool = False
    fallback: bool = False


class AskResultOut(BaseModel):
    """问答**整包**（``stream=false`` 直接返回；``stream=true`` 时由 Router 拆成 SSE 事件）。

    🔴 SSE 事件与整包**同源**（同一对象拆分），杜绝「流式与非流式结果不一致」：
    ``meta``/``delta``/``citation``/``refusal``/``done`` 六类事件逐字段对应本模型。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    request_id: str
    model: str | None = None
    mask_applied: bool = False
    degraded: bool = False
    degraded_reason: str | None = None
    scope_effective: ScopeEffectiveOut
    #: 已**本地回填**真值的完整回答（refusal 时为 None）
    answer: str | None = None
    citations: list[AskCitationOut] = Field(default_factory=list)
    refusal: AskRefusalOut | None = None
    done: AskDoneOut
