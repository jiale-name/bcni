"""查重与合并契约（CU-19，API-57~61，F-3.5 / SQ-4 / SM-1）。

🔴 **CR-5**：全部 ID 用 :data:`~app.schemas.common.SnowflakeId`（响应为字符串）。
🔴 **并排对比（API-58）的 PII 一律脱敏**：手机 / 邮箱只下发 ``*`` 脱敏形态，
   明文身份不进对比视图（HR 靠姓名 + 履历 + 标签判断重复，不需要明文联系方式）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DupPairStatus
from app.schemas.common import DateTimeStr, SnowflakeId


def pair_status_name_of(status: int | None) -> str | None:
    return DupPairStatus.name_of(status) if status is not None else None


# ============================================================================
# 人才简述（列表 / 对比共用）
# ============================================================================
class TalentBriefOut(BaseModel):
    """一对档案中某一方的简述（API-57/58 共用；PII 已脱敏）。"""

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    name: str
    school: str | None = None
    major: str | None = None
    education_level: str | None = None
    work_years: int | None = None
    phone_masked: str | None = None
    email_masked: str | None = None
    tags: list[str] = Field(default_factory=list)


# ============================================================================
# API-57 候选对列表
# ============================================================================
class DupCandidateOut(BaseModel):
    """API-57 重复候选对（相似度降序）。"""

    model_config = ConfigDict(extra="forbid")

    pair_id: SnowflakeId
    talent_a: TalentBriefOut
    talent_b: TalentBriefOut
    similarity: float | None = None
    match_fields: list[str] = Field(default_factory=list)
    status: int
    status_name: str | None = None


# ============================================================================
# API-58 并排对比
# ============================================================================
class DupCompareOut(BaseModel):
    """API-58 并排对比（比列表多完整的标签 / 脱敏联系方式，供 HR 逐项核对）。"""

    model_config = ConfigDict(extra="forbid")

    pair_id: SnowflakeId
    talent_a: TalentBriefOut
    talent_b: TalentBriefOut
    similarity: float | None = None
    match_fields: list[str] = Field(default_factory=list)
    status: int
    status_name: str | None = None


# ============================================================================
# API-59 合并
# ============================================================================
class DedupMergeRequest(BaseModel):
    """API-59 合并请求。

    - ``master_id``：保留的主档（另一档为从档，合并后软删）。
    - ``field_strategy``：``{字段名: master|slave}``；**未指定字段**按「非空优先」
      合并（主档为空才取从档）。⚠️ 「高版本优先」依赖字段级版本号，本批未建模，
      已登记遗留 —— 未指定字段一律走「非空优先」。
    - ``merge_detail``：本批恒为 no-op（TBL-12~15 明细表未建模，登记遗留）。
    """

    model_config = ConfigDict(extra="forbid")

    master_id: SnowflakeId
    field_strategy: dict[str, Literal["master", "slave"]] = Field(default_factory=dict)
    merge_tags: bool = True
    merge_detail: bool = True


class DedupMergeOut(BaseModel):
    """API-59 合并出参。

    🔴 ``vector_reindexed=False`` **不代表合并失败**：向量是派生索引（数据自有），
    主档重索引失败只是让该档案暂时检索不到，可经 ``scripts/rebuild_index`` 全量重建。
    """

    model_config = ConfigDict(extra="forbid")

    pair_id: SnowflakeId
    master_id: SnowflakeId
    slave_id: SnowflakeId
    vector_reindexed: bool = False
    merged_at: DateTimeStr


# ============================================================================
# API-60 忽略
# ============================================================================
class DupIgnoreOut(BaseModel):
    """API-60 忽略出参。"""

    model_config = ConfigDict(extra="forbid")

    pair_id: SnowflakeId
    status: int
    status_name: str | None = None


# ============================================================================
# API-61 回滚
# ============================================================================
class DedupRollbackOut(BaseModel):
    """API-61 合并回滚出参。"""

    model_config = ConfigDict(extra="forbid")

    merge_id: SnowflakeId
    master_id: SnowflakeId
    slave_id: SnowflakeId
    rolled_back_at: DateTimeStr
