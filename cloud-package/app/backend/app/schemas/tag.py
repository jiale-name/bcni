"""受控标签词表契约（CU-17，API-50~56，模块 I「标签」，F-3.4 / F-3.4.1）。

🔴 **AI 不得自由造词**（SQ-8）：API-53 提交的建议落 ``status=0 待审核``，
只有 API-54 审核通过后才成为词表启用项 —— 由测试钉死（`test_tag_dict.py`）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TagDictStatus, TagSource
from app.schemas.common import DateTimeStr, SnowflakeId


def tag_status_name_of(status: int | None) -> str | None:
    return TagDictStatus.name_of(status) if status is not None else None


def tag_source_name_of(source: int | None) -> str | None:
    return TagSource.name_of(source) if source is not None else None


class TagDictOut(BaseModel):
    """标签词表行（API-50 / API-51 / API-52 / API-54 共用）。"""

    model_config = ConfigDict(extra="forbid")

    tag_id: SnowflakeId
    tag_code: str
    tag_name: str
    category_l1: str | None = None
    category_l2: str | None = None
    category_l3: str | None = None
    synonyms: list[str] = []
    status: int
    status_name: str | None
    source: int
    source_name: str | None
    approved_by: SnowflakeId | None = None
    approved_at: DateTimeStr | None = None
    created_at: DateTimeStr | None = None


class TagTreeNode(BaseModel):
    """API-50 三级分类树节点（``children`` 递归）。"""

    model_config = ConfigDict(extra="forbid")

    category: str
    level: int
    children: list["TagTreeNode"] = []
    tags: list[TagDictOut] = []


TagTreeNode.model_rebuild()


class TagCreateRequest(BaseModel):
    """API-51 新增标签（管理员直接启用）。"""

    model_config = ConfigDict(extra="forbid")

    tag_code: str | None = Field(None, max_length=64)
    tag_name: str = Field(..., min_length=2, max_length=32)
    category_l1: str | None = Field(None, max_length=64)
    category_l2: str | None = Field(None, max_length=64)
    category_l3: str | None = Field(None, max_length=64)
    synonyms: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("synonyms")
    @classmethod
    def _clip_synonyms(cls, v: list[str]) -> list[str]:
        return [s.strip()[:64] for s in v if s.strip()][:10]


class TagUpdateRequest(BaseModel):
    """API-52 修改标签（含启用/停用；``tag_code`` 不可改 —— 同字典 ``item_code`` 口径）。"""

    model_config = ConfigDict(extra="forbid")

    tag_name: str | None = Field(None, min_length=2, max_length=32)
    category_l1: str | None = Field(None, max_length=64)
    category_l2: str | None = Field(None, max_length=64)
    category_l3: str | None = Field(None, max_length=64)
    synonyms: list[str] | None = Field(None, max_length=10)
    #: 三态：未传 = 不改动；``enable``/``disable`` = 目标状态
    action: str | None = Field(None, pattern="^(enable|disable)$")

    @field_validator("synonyms")
    @classmethod
    def _clip_synonyms(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [s.strip()[:64] for s in v if s.strip()][:10]


class TagSuggestRequest(BaseModel):
    """API-53 提交 AI 建议标签。"""

    model_config = ConfigDict(extra="forbid")

    tag_name: str = Field(..., min_length=2, max_length=32)
    category_code: str = Field(..., min_length=1, max_length=64)
    synonyms: list[str] = Field(default_factory=list, max_length=10)
    sample_talent_ids: list[SnowflakeId] = Field(default_factory=list, max_length=20)
    reason: str | None = Field(None, max_length=200)

    @field_validator("synonyms")
    @classmethod
    def _clip_synonyms(cls, v: list[str]) -> list[str]:
        return [s.strip()[:64] for s in v if s.strip()][:10]


class TagSuggestOut(BaseModel):
    """API-53 出参。"""

    model_config = ConfigDict(extra="forbid")

    suggestion_id: SnowflakeId
    status: int
    status_name: str | None
    tag_code: str | None = None


class TagReviewRequest(BaseModel):
    """API-54 审核建议。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    tag_code: str | None = Field(None, max_length=64)
    reject_reason: str | None = Field(None, max_length=200)


class TagReviewOut(BaseModel):
    """API-54 出参。"""

    model_config = ConfigDict(extra="forbid")

    suggestion_id: SnowflakeId
    status: int
    status_name: str | None
    tag_code: str | None = None
    activated_at: DateTimeStr | None = None


class TalentTagOut(BaseModel):
    """API-55 人才标签行（含来源：人工 / AI）。"""

    model_config = ConfigDict(extra="forbid")

    tag_code: str
    tag_name: str
    confidence: float | None = None
    source: int
    source_name: str | None
    category_l1: str | None = None
    category_l2: str | None = None
    category_l3: str | None = None


class TalentTagsOut(BaseModel):
    """API-55 出参壳。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    tags: list[TalentTagOut]


class TalentTagsPutRequest(BaseModel):
    """API-56 人工调整标签（**只能选启用项**，全量替换语义）。"""

    model_config = ConfigDict(extra="forbid")

    tag_codes: list[str] = Field(..., min_length=0, max_length=50)

    @field_validator("tag_codes")
    @classmethod
    def _clip_codes(cls, v: list[str]) -> list[str]:
        return [c.strip()[:64] for c in v if c.strip()][:50]

    def to_out(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"tag_codes": self.tag_codes, **(extra or {})}
