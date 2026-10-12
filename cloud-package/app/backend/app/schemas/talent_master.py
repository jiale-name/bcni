"""人才档案主线契约（CU-29，API-30~35 / API-64~66，F-3.2 / F-3.4 / F-3.7 / F-3.9）。

为什么单开一个文件，而不并进 ``schemas/talent.py``
==================================================
``schemas/talent.py`` 是**导入域**（API-36~43）的契约；本文件是**档案主线**域
（列表/详情/编辑/归档/画像/导出）外加三个跨域接口（会话回看/清空/脱敏自检）。
两者共用 ``talent_base`` 表，却面向完全不同的使用者 —— 导入是 ADM/HR 的作业台，
档案主线是全角色的查询面。合并会让「导入的进度语义」与「档案的字段裁剪语义」
互相干扰。

🔴 本文件同时是下面三处**上游不一致**的口径来源（评审文档 §7.3 批次 16 登记）
================================================================================
① ``education_level`` 的形态
   接口示例写 ``"education_level": 4``、query 参数标 ``int``；而模型
   ``talent_base.education_level`` 是 ``String(32)``，字典码是 ``"01"``~``"05"``
   （见 ``scripts/seed_dev.py::_BUSINESS_DICTS``）。
   → **统一为「两位数字码」**：入参收 ``int``（前端按契约传 number），内部
     ``f"{v:02d}"`` 转字典码；出参回 ``int``（``int("04") == 4``，与示例逐字一致）。
   ⚠️ 该转换**依赖字典码为纯数字**。若甲方后续把码改成 ``BACHELOR`` 之类，
   :func:`education_code_to_int` 返回 ``None``（不抛异常），此时须同步调整出参形态。

② ``sensitive_visible=true`` 时**仍只下发脱敏值**
   API-31 示例里 ADM/HR 视角 ``sensitive_visible: true``，但同一条响应里
   ``id_card`` / ``phone`` / ``email`` 给的是**脱敏形态**（``3301**********1234``）。
   两处不可能同时为真。
   → **取安全侧**：一律只下发脱敏形态（``core.crypto`` 的 ``mask_id_card`` /
     ``mask_phone`` / ``mask_email``）；``sensitive_visible`` 仅表达
     「当前主体**本来有权**看明文」。明文查看不在本期范围（须单独的受控入口）。
   好处是出参与示例**逐字一致**，同时满足最小必要原则。

③ ``work_exps`` / ``project_exps`` 的数据层、导入落库和 API-31 详情装配已完成；
   技能 / 证书（TBL-14~15）仍未建模。API-31 的 ``detail_note`` 只说明这两个未建模数组，
   避免把「暂未交付」误读成「这个候选人没有技能或证书」。

沿用已确立的约定：雪花 ID 一律字符串（CR-5）、时间 ``YYYY-MM-DD HH:mm:ss``、
未填字段下发 ``null``、请求体 ``extra="forbid"``。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import MAX_PER_PAGE, DateTimeStr, SnowflakeId

# ============================================================================
# 常量（全部来自接口原文，禁止散落魔法数）
# ============================================================================
#: API-30 排序列白名单（接口原文：「白名单：updated_at / created_at / work_years」）
SORT_FIELDS: frozenset[str] = frozenset({"updated_at", "created_at", "work_years"})
DEFAULT_SORT: str = "-updated_at"

#: ``filters.org_ids`` 个数上限（接口 API-30：「逗号分隔 ≤50 个」）
MAX_FILTER_ORG_IDS: int = 50
#: ``filters.tag_codes`` 个数上限（接口 API-30：「逗号分隔 ≤10 个」）
MAX_FILTER_TAG_CODES: int = 10
#: 列表行下发标签数上限（接口备注：「``top_tags`` 最多返回 5 个」）
MAX_TOP_TAGS: int = 5

#: 导出上限 —— 接口 API-35 未给具体数，此处取 5000：一次导出 5000 行 CSV 约 1~2 MB，
#: 既够日常用，又不至于让一个请求把连接池占满（导出是**同步**接口，走线程池）。
MAX_EXPORT_ROWS: int = 5000

#: 脱敏自检最小样例数（需求 §12.3 安全专项：「脱敏样例 ≥20 条」）
MASK_MIN_SAMPLES: int = 20

#: API-31 里仍未建模明细的显式说明。
DETAIL_NOTE_NO_DETAIL_TABLE = (
    "技能/证书明细仍未建模（TBL-14/TBL-15），对应数组为空不代表该候选人没有技能或证书。"
)


# ============================================================================
# 字典码 ↔ int 的双向转换（口径见模块 docstring ①）
# ============================================================================
def education_code_to_int(code: str | None) -> int | None:
    """字典码 ``"04"`` → ``4``；非数字码返回 ``None``（**不抛异常**）。

    为什么不直接 ``int(code)``：字典可由 ADM 维护（F-1.7），哪天把码改成
    ``BACHELOR``，档案详情不该因此 500 —— 出参给 ``None``、名称照常下发，
    远比「整页打不开」好。
    """
    if code is None:
        return None
    text = str(code).strip()
    return int(text) if text.isdigit() else None


def education_int_to_code(value: int | None) -> str | None:
    """入参 ``4`` → 字典码 ``"04"``；``0`` / ``None``（不限）返回 ``None``。"""
    if value is None:
        return None
    number = int(value)
    return f"{number:02d}" if number > 0 else None


def month_str(value: date | datetime | None) -> str | None:
    """``date(1993, 6, 1)`` → ``"1993-06"``（API-31 的 ``birth_month``）。

    🔴 只下发到月：接口字段名就叫 ``birth_month``，且出生**日**对招聘场景无用
    （最小必要）。故这里刻意丢掉「日」，而不是原样渲染 ``1993-06-01``。
    """
    if value is None:
        return None
    return value.strftime("%Y-%m")


# ============================================================================
# API-30 档案列表
# ============================================================================
class TopTagOut(BaseModel):
    """列表行的标签（最多 :data:`MAX_TOP_TAGS` 个）。"""

    model_config = ConfigDict(extra="forbid")

    tag_code: str
    tag_name: str | None = None


class TalentListItemOut(BaseModel):
    """API-30 列表行。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    name: str
    gender: int | None = None
    gender_name: str | None = None
    org_id: SnowflakeId | None = None
    org_name: str | None = None
    education_level: int | None = Field(None, description="学历（两位数字码的整数形态，如 4=硕士）")
    education_level_name: str | None = None
    work_years: int | None = None
    top_tags: list[TopTagOut] = Field(default_factory=list)
    data_status: int = Field(..., description="档案状态 0 待复核 1 有效 2 已合并 3 已归档 4 已删除")
    data_status_name: str | None = None
    updated_at: DateTimeStr | None = None


class TalentListFilters(BaseModel):
    """API-30 的筛选条件（**已解析**形态：逗号串 → 列表、int → 字典码）。

    为什么不直接把 query 参数塞进 Service：把「字符串切分 / 上限校验 / 字典码转换」
    收在一处，Service 里就只剩业务；否则每个调用点都要重复一遍切分与容错。
    """

    model_config = ConfigDict(extra="forbid")

    org_ids: tuple[int, ...] | None = None
    education_code: str | None = Field(None, description="学历**字典码**（已由 int 转换）")
    work_years_min: int | None = None
    work_years_max: int | None = None
    tag_codes: tuple[str, ...] | None = None
    data_status: int | None = Field(
        None,
        description=(
            "None = 默认口径（批次 23：ADM/HR =「0 待复核 + 1 有效」，其余角色仅「1 有效」）；"
            "显式指定非 1 仍仅 ADM/HR 可查"
        ),
    )


class TalentListQuery(BaseModel):
    """API-30 查询参数（解析后的内部模型，由 Router 从 query 拼装）。"""

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = None
    filters: TalentListFilters = Field(default_factory=TalentListFilters)
    sort: str = DEFAULT_SORT
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=MAX_PER_PAGE)


def parse_sort(raw: str | None) -> tuple[str, str]:
    """``"-updated_at"`` → ``("updated_at", "desc")``。

    🔴 **白名单外的字段一律退回默认**，而不是报错：排序是展示层偏好，
    为一个拼错的列名让整页 400 不划算（与「筛选值拼错返回 0 条」不同 ——
    那个必须报错，因为用户会以为「真的没有数据」；排序错了结果**仍然完整可见**，
    只是顺序不同）。非法值回退默认是更温和也更实用的取舍。
    """
    text = (raw or DEFAULT_SORT).strip()
    desc = text.startswith("-")
    field = text[1:] if desc else text
    if field not in SORT_FIELDS:
        field = DEFAULT_SORT.lstrip("-")
        desc = True
    return field, ("desc" if desc else "asc")


def split_csv(raw: str | None, *, limit: int) -> tuple[str, ...] | None:
    """``"a, b , c"`` → ``("a","b","c")``；空串 → ``None``；**超限截断**。

    超限截断而不是报错：接口只给了「≤50 个」这个上限，前端多勾几个部门
    不该直接失败；且截断后**查询仍在权限内**（收窄校验在后面照样执行），
    不存在越权风险。
    """
    if not raw:
        return None
    items = [part.strip() for part in str(raw).split(",")]
    kept = tuple(item for item in items if item)
    if not kept:
        return None
    return kept[:limit]


# ============================================================================
# API-31 档案详情
# ============================================================================
class WorkExpOut(BaseModel):
    """工作经历（TBL-12）。数据库列名与 API 展示名在 Service 层显式映射。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    company: str
    dept_name: str | None = None
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None


class ProjectExpOut(BaseModel):
    """项目经历（TBL-13）。数据库列名与 API 展示名在 Service 层显式映射。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    project_name: str
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    tech_stack: list[str] = Field(default_factory=list)


class SkillItemOut(BaseModel):
    """技能（TBL-14，尚未建模）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    skill_name: str
    proficiency: int | None = None
    proficiency_name: str | None = None


class CertItemOut(BaseModel):
    """证书（TBL-15，尚未建模）。"""

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    cert_name: str
    issue_date: str | None = None


class TalentDetailOut(BaseModel):
    """API-31 档案详情。

    🔴 ``with_detail=false`` 时只省掉四张明细表，**主档字段一律返回** ——
    接口原文限定的是「不含履历明细」，不是「返回一个更瘦的对象」。
    若连主档都裁，前端抽屉就得再发一次请求，反而更慢。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId
    name: str
    gender: int | None = None
    gender_name: str | None = None
    birth_month: str | None = Field(None, description="出生年月（YYYY-MM，只到月）")
    education_level: int | None = None
    education_level_name: str | None = None
    school: str | None = None
    major: str | None = None
    work_years: int | None = None
    #: 🔴 三个 PII 字段**恒为脱敏形态**（口径见模块 docstring ②）
    id_card: str | None = Field(None, description="身份证（脱敏，如 3301**********1234）")
    phone: str | None = Field(None, description="手机号（脱敏，如 138****8888）")
    email: str | None = Field(None, description="邮箱（脱敏，如 li***@example.com）")
    sensitive_visible: bool = Field(
        False, description="当前主体**本来有权**看明文（ADM/HR 为 true）；本期一律只下发脱敏值"
    )
    org_id: SnowflakeId | None = None
    org_name: str | None = None
    data_status: int
    data_status_name: str | None = None
    version_no: int = 1
    work_exps: list[WorkExpOut] = Field(default_factory=list)
    project_exps: list[ProjectExpOut] = Field(default_factory=list)
    skills: list[SkillItemOut] = Field(default_factory=list)
    certs: list[CertItemOut] = Field(default_factory=list)
    #: 履历明细缺口的显式说明（为空表示明细已交付；见模块 docstring ③）
    detail_note: str | None = None
    updated_at: DateTimeStr | None = None


# ============================================================================
# API-32 编辑档案
# ============================================================================
class TalentUpdateRequest(BaseModel):
    """API-32 编辑请求（**只提交发生变化的字段**，后端据此写变更留痕）。

    🔴 **不接受的字段**（不是遗漏，是刻意）：
    - ``id_card`` / ``profile_vec_id`` / ``data_status`` / ``version_no`` /
      ``merged_into_id`` —— 前两者有专门的流程（证件号变更属 F-3.11 更正权，
      走 API-72/74；状态与版本由系统流转）。
    - ``user_id`` —— 把档案挂到别的用户是做「查阅权转移」，不是编辑。
    这些若从前端传进来，Pydantic 的 ``extra="forbid"`` 会直接 40001，
    比「静默忽略」安全：静默忽略会让调用方以为改成功了。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=64)
    gender: int | None = Field(None, ge=0, le=2)
    birth_month: str | None = Field(None, description="出生年月 YYYY-MM 或 YYYY-MM-DD")
    education_level: int | None = Field(None, ge=1, le=99, description="学历（两位数字码的整数形态）")
    school: str | None = Field(None, max_length=128)
    major: str | None = Field(None, max_length=128)
    work_years: int | None = Field(None, ge=0, le=60)
    phone: str | None = Field(None, max_length=20)
    email: str | None = Field(None, max_length=128)
    org_id: SnowflakeId | None = None

    @field_validator("name", "school", "major", "phone", "email", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """空串按「未提交」处理（前端清空输入框常传 ``""``）。"""
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return value

    def provided(self) -> dict[str, Any]:
        """只取**显式提交**的字段（``exclude_unset`` 语义）。

        🔴 必须区分「没传这个字段」与「传了 null」：前者是「不改」，后者是「清空」。
        但本接口的字段都有 NOT NULL 或业务非空语义（如 ``name``），
        清空一律不合法 —— 故传 ``null`` 等同于「不改」，并在出参的
        ``ignored_null_fields`` 里点名告知，而不是静默丢弃。
        """
        raw = self.model_dump(exclude_unset=True)
        return {k: v for k, v in raw.items() if v is not None}


# ============================================================================
# API-33 归档
# ============================================================================
class TalentArchiveOut(BaseModel):
    """API-33 归档出参（前端契约：``{talent_id, archived, vector_cleaned}``）。

    ``vector_cleaned`` 表达「向量侧的派生索引是否清干净」—— 它与 ``archived``
    **不是同一件事**：归档以 MySQL 为准（权威数据），向量只是派生索引。
    向量清理失败**不回滚归档**（否则「档案删不掉」会变成常态化故障），
    但必须如实告知，由运维按 ``vector_note`` 补跑重建/清理。
    """

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    archived: bool = True
    vector_cleaned: bool = False
    vector_note: str | None = None
    data_status: int
    data_status_name: str | None = None
    version_no: int = 1


# ============================================================================
# API-34 数字画像
# ============================================================================
class ProfileTagOut(BaseModel):
    """画像标签（含来源，人工覆盖 AI 时前端要能区分）。"""

    model_config = ConfigDict(extra="forbid")

    tag_code: str
    tag_name: str | None = None
    source: int | None = None
    source_name: str | None = None


class ProfileDimensionOut(BaseModel):
    """画像维度得分（供雷达图渲染；由标签分布**确定性**折算，非 LLM 生成）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    score: float


class TalentProfileOut(BaseModel):
    """API-34 数字画像。

    🔴 ``vector_summary`` 是**向量可用性**的诚实反映：
    - 向量已写入 → 给出摘要（片段数 / 模型 / 维度）；
    - 未做真实向量化（H-19 未闭环）→ ``null`` + ``vector_note`` 说明原因。
    ❌ 不用「拼一段看起来像 AI 生成的画像文字」来充数 —— 那是把未交付伪装成交付。
    """

    model_config = ConfigDict(extra="forbid")

    talent_id: SnowflakeId
    name: str | None = None
    org_id: SnowflakeId | None = None
    org_name: str | None = None
    vector_summary: str | None = None
    vector_note: str | None = None
    dimensions: list[ProfileDimensionOut] = Field(default_factory=list)
    tags: list[ProfileTagOut] = Field(default_factory=list)
    version_no: int | None = None
    updated_at: DateTimeStr | None = None


# ============================================================================
# API-35 导出
# ============================================================================
class TalentExportRequest(BaseModel):
    """API-35 导出请求。

    ⚠️ ``format`` 只实现 ``csv``：``xlsx`` 需要 openpyxl 走二进制流，
    而本项目的统一响应壳是 JSON（``content`` 为**文本**）。
    接口原文也只给了 CSV 形态的口径，故 ``xlsx`` 请求返回 40001 并说明，
    ❌ 不静默降级成 CSV（那会让调用方以为拿到了 xlsx）。
    """

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(None, max_length=64)
    filters: TalentListFilters = Field(default_factory=TalentListFilters)
    sort: str = DEFAULT_SORT
    format: Literal["csv", "xlsx"] = "csv"
    max_rows: int = Field(1000, ge=1, le=MAX_EXPORT_ROWS)


class TalentExportOut(BaseModel):
    """API-35 出参（CSV 文本走统一响应壳，与 API-29 / API-42 同口径）。"""

    model_config = ConfigDict(extra="forbid")

    filename: str
    format: str
    row_count: int
    content: str
    truncated: bool = Field(False, description="是否因 max_rows 截断（截断时必须显式告知）")
    generated_at: DateTimeStr | None = None


# ============================================================================
# API-64 / API-65 问答会话
# ============================================================================
class AskTurnOut(BaseModel):
    """一轮问答（接口原文：会话历史回看 ≥10 轮）。

    🔴 ``question`` 是**问题原文**（业务数据）。接口 §2.2 权限矩阵明确
    「问题原文属业务数据，仅 ADM/HR 经 API-64 可见」—— 故本接口必须过权限点，
    且每次调用写审计（谁在什么时候回看了哪个会话）。
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str


class AskSessionOut(BaseModel):
    """API-64 会话历史。

    ``ttl_seconds`` 让前端知道「这个会话还能活多久」—— 会话存 Redis 且**可丢**
    （Redis 只存可丢数据，铁律 6），不告知 TTL 会让用户以为历史「自己消失了」。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn_count: int = 0
    max_turns: int = Field(..., description="存储侧保留上限（接口要求 ≥10 轮）")
    ttl_seconds: int | None = Field(None, description="剩余存活秒数；None = 未取到")
    items: list[AskTurnOut] = Field(default_factory=list)


class AskSessionClearOut(BaseModel):
    """API-65 清空会话出参。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    cleared: bool
    removed_turns: int = 0
    message: str | None = None


# ============================================================================
# API-66 脱敏自检
# ============================================================================
class MaskVerifyRequest(BaseModel):
    """API-66 脱敏自检请求（提交样例，返回漏检报告）。

    ⚠️ **入参可能含真实 PII**（这正是它要自检的东西）。因此：
    1. 本接口**只对 ADM / AIOPS 开放**（接口 §4 权限矩阵）；
    2. 出参**绝不回显原文**（见 :class:`MaskLeakOut`）；
    3. 审计只记 ``{sample_count, leak_count}``，**不记样例内容**。
    """

    model_config = ConfigDict(extra="forbid")

    samples: list[str] = Field(..., min_length=1, max_length=200)
    #: 该次自检是否需要「已知人名」参与判定（把人名一并提交，用于验证
    #: 「已知人名残留」这条规则）。留空则只做正则类检查。
    known_names: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("samples")
    @classmethod
    def _drop_empty(cls, value: list[str]) -> list[str]:
        kept = [s for s in value if isinstance(s, str) and s.strip()]
        if not kept:
            raise ValueError("samples 不能全为空串")
        return kept


class MaskLeakOut(BaseModel):
    """一条漏检记录。

    🔴 **不回显原文** —— 只给序号 + 命中类型 + **脱敏后**的预览。
    理由：本接口的输入就是「可能含明文的文本」，若把原文回显，
    等于用一个「脱敏自检」接口把 PII 重新写进响应体与日志 ——
    自检器本身成了泄漏源。预览用 ``mask_text`` 处理后的串，
    既能让人看出「是哪一条、漏的是什么」，又不会二次扩散明文。
    """

    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., description="样例序号（0 起）")
    residual: list[str] = Field(default_factory=list, description="命中的敏感类型")
    preview: str = Field(..., description="**脱敏后**的预览（非原文）")


class MaskVerifyOut(BaseModel):
    """API-66 出参：漏检报告。

    ``meets_minimum`` 对应需求 §12.3 的「脱敏样例 ≥20 条」——
    样例不足时仍返回报告（不报错），但显式标记，避免「跑了 3 条就说通过了」。
    """

    model_config = ConfigDict(extra="forbid")

    sample_count: int
    minimum_required: int = MASK_MIN_SAMPLES
    meets_minimum: bool
    passed: bool
    leak_count: int = 0
    leaked_types: list[str] = Field(default_factory=list, description="本次漏检涉及的敏感类型")
    leaks: list[MaskLeakOut] = Field(default_factory=list)
    note: str | None = None


__all__ = [
    "DEFAULT_SORT",
    "DETAIL_NOTE_NO_DETAIL_TABLE",
    "MASK_MIN_SAMPLES",
    "MAX_EXPORT_ROWS",
    "MAX_FILTER_ORG_IDS",
    "MAX_FILTER_TAG_CODES",
    "MAX_TOP_TAGS",
    "SORT_FIELDS",
    "AskSessionClearOut",
    "AskSessionOut",
    "AskTurnOut",
    "CertItemOut",
    "MaskLeakOut",
    "MaskVerifyOut",
    "MaskVerifyRequest",
    "ProfileDimensionOut",
    "ProfileTagOut",
    "ProjectExpOut",
    "SkillItemOut",
    "TalentArchiveOut",
    "TalentDetailOut",
    "TalentExportOut",
    "TalentExportRequest",
    "TalentListFilters",
    "TalentListItemOut",
    "TalentListQuery",
    "TalentProfileOut",
    "TalentUpdateRequest",
    "TopTagOut",
    "WorkExpOut",
    "education_code_to_int",
    "education_int_to_code",
    "month_str",
    "parse_sort",
    "split_csv",
]
