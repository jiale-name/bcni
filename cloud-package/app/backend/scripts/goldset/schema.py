"""金标准集的字段口径与数据结构（CU-28）。

🔴 本文件是**标注活动唯一的口径来源**
-------------------------------------
200 份简历 × 13 个字段 = 2600 个要人工判断的格子。如果口径只写在需求文档的
一句「字段级准确率 ≥95%」里，标完必然发现：有人把「缺失」填成空、有人填「无」；
有人把「某公司（外包）」写成公司名的一部分、有人只写主体名。
**分母定义不同，最终准确率可以差 20 个百分点** —— 所以口径必须先固化成常量。

口径来源与**两处必须登记的细化**
--------------------------------
======================  ==============================================  ==========================================
来源                    原文                                            本文件处置
======================  ==============================================  ==========================================
需求 §3.3 F-3.2         「抽取字段：姓名、性别、出生年月、学历、院校、     逐字段展开为 13 列（原文「联系方式」一项拆为
                        专业、工作年限、任职经历、项目经历、技能、证书、   手机号 / 邮箱两列，**属口径细化**）
                        联系方式」
需求 §5 IDX-02          「结构化字段级准确率 ≥95%（金标准集 ≥200 份）；     **分母定义原文未给** —— 见下「分母口径」，
                        图片件单列口径」                                   本文件给出推荐定义并标记为待甲方确认
======================  ==============================================  ==========================================

分母口径（**本文件定稿一次，N6 复用**）
-------------------------------------
需求 §5 的原问题（M-3）就是「分母是什么？」，本文档给三档并明确主口径：

- **主口径（用于验收）**：`正确字段数 / 已标注字段数` —— 分母是「**真值存在**的格子」。
  一份简历里「简历上确实没有证书」的字段**不进分母**（否则会因简历本身信息少而被判错）。
- **辅助口径（参考）**：`完全匹配份数 / 总份数` —— 整份 13 个字段全对才算对，
  比主口径严格得多，仅作观察，❌ 不用它做验收判定。
- **编造率（单列，不计入准确率）**：真值为「缺失」而系统**编出了值**的次数 / 真值缺失的格子数。
  需求 F-3.2 明确要求「缺失字段显式标记而非编造」，故此项必须单列统计 ——
  否则「编造」会以「正确」的形式混进准确率里。
- **图片件单列**：按 ``file_type=scan_image`` 分组**单独**统计主口径（需求 IDX-02 明确要求）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "AnnotField",
    "RESUME_FIELDS",
    "RESUME_META_COLUMNS",
    "RESUME_COLUMNS",
    "CORE_FIELD_COLUMNS",
    "FILE_TYPES",
    "ANNOTATION_STATUS",
    "QUERY_INTENTS",
    "QUERY_COLUMNS",
    "LIST_SEPARATOR",
    "MISSING_MARKER",
    "MISSING_RULES",
    "ACCURACY_DEFINITIONS",
    "RESUME_MIN_COUNT",
    "QUERY_MIN_COUNT",
    "queries_intent_note",
]

#: 多值列表字段的分隔符（段内子字段用 ``;``，段间用 ``|``）
LIST_SEPARATOR = "|"
SUB_SEPARATOR = ";"

#: 「简历上确实没有这个信息」的统一标记。
#: 🔴 必须是**显式标记**而不是留空 —— 留空与「标注人忘了填」无法区分，
#:    一旦混在一起，质检脚本无法判断「该填没填」还是「本来就没有」。
MISSING_MARKER = "缺失"

#: 需求硬指标（§12.1 金标准集表 / §6.2 一期核心指标）
RESUME_MIN_COUNT = 200
QUERY_MIN_COUNT = 50


# ============================================================================
# 简历标注集：字段定义
# ============================================================================
@dataclass(frozen=True, slots=True)
class AnnotField:
    """一个待标注字段的口径。"""

    column: str
    label: str
    group: str
    table_ref: str
    cardinality: Literal["scalar", "list"]
    note: str = ""
    example: str = ""

    @property
    def is_list(self) -> bool:
        return self.cardinality == "list"


#: 一期抽取字段（需求 F-3.2 逐项展开）
RESUME_FIELDS: tuple[AnnotField, ...] = (
    AnnotField(
        "name", "姓名", "基础信息", "TBL-11 talent_base.name", "scalar",
        note="照抄简历上的姓名；有英文名时以中文名为主，英文名写进备注列。",
        example="张三",
    ),
    AnnotField(
        "gender", "性别", "基础信息", "TBL-11 talent_base.gender", "scalar",
        note="只填 `男` / `女`；简历未写或无法判断填 `缺失`（**不得按姓名推断**）。"
             "系统侧落库为字典码 1/2/0，评估时由脚本做映射。",
        example="男",
    ),
    AnnotField(
        "birth_date", "出生年月", "基础信息", "TBL-11 talent_base.birth_date", "scalar",
        note="填 `YYYY-MM`（如 `1995-03`）；简历只写年龄时填 `Age:28`，并在备注列注明基准日期。"
             "⚠️ 年龄与出生年月**不可直接互换**，脚本会分列统计，勿自行折算。",
        example="1995-03",
    ),
    AnnotField(
        "education_level", "学历", "教育经历", "TBL-11 talent_base.education_level", "scalar",
        note="**必须使用字典值**（§6.2 学历字典 5 项：`high_school` / `college` / `bachelor` / "
             "`master` / `doctor`）。填中文名也可，质检脚本会归一；<b>填字典之外的取值会被质检拦下</b>。",
        example="bachelor",
    ),
    AnnotField(
        "school", "院校", "教育经历", "TBL-11 talent_base.school", "scalar",
        note="填学校**全称**（`华中科技大学` 而非 `华科`）；多个学历时填最高学历对应院校。",
        example="华中科技大学",
    ),
    AnnotField(
        "major", "专业", "教育经历", "TBL-11 talent_base.major", "scalar",
        note="填最高学历的专业全称。",
        example="计算机科学与技术",
    ),
    AnnotField(
        "work_years", "工作年限", "工作经历", "TBL-11 talent_base.work_years", "scalar",
        note="**整数年**（`5` 而非 `五年` / `5年`）；简历只给起止时间时按「最早入职 → 当前」折算，"
             "并在备注列写明折算依据。",
        example="5",
    ),
    AnnotField(
        "work_exp", "任职经历", "工作经历", "TBL-12 talent_work_exp", "list",
        note="每段一条，段内用 `;` 分隔六个子字段："
             "`公司全称;部门;职位;起始(YYYY-MM);结束(YYYY-MM 或 now);是否在职(是/否)`；"
             "按**时间倒序**排列；公司名取主体全称（外包/驻场在备注列说明，❌ 不并入公司名）。",
        example="某某科技有限公司;数据平台部;高级算法工程师;2020-07;now;是",
    ),
    AnnotField(
        "project_exp", "项目经历", "工作经历", "TBL-13 talent_project_exp", "list",
        note="每段一条，段内用 `;` 分隔五个子字段："
             "`项目名称;担任角色;起始;结束;技术栈(逗号分隔)`。",
        example="企业知识库问答平台;算法负责人;2022-03;2023-05;Python,LangChain,Milvus",
    ),
    AnnotField(
        "skill", "技能", "技能与证书", "TBL-14 talent_skill", "list",
        note="技能名用 `|` 分隔；同一技能的不同写法**取最通用写法**（`PostgreSQL` 而非 `pg`）。",
        example="Python|Milvus|RAG",
    ),
    AnnotField(
        "cert", "证书", "技能与证书", "TBL-15 talent_cert", "list",
        note="证书全称，`|` 分隔；**不要把「英语四级」这类无证书载体的等级当作证书**（写进备注）。",
        example="系统架构设计师（软考高级）",
    ),
    AnnotField(
        "phone", "手机号", "联系方式", "TBL-11 talent_base.phone_enc / phone_hash", "scalar",
        note="🔴 **本列是明文，只存在于甲方本地**。交付乙方前必须跑 `goldset.py seal` "
             "把明文替换为 `phone_hash`（脚本用与入库一致的盐化 SHA256）。"
             "简历未提供联系方式时填 `缺失`。",
        example="13812348888",
    ),
    AnnotField(
        "email", "邮箱", "联系方式", "TBL-11 talent_base.email_enc", "scalar",
        note="同上：明文列，交付前 seal。多个邮箱填最常联系的那个，其余写备注。",
        example="zhangsan@example.com",
    ),
)

#: 核心字段（进入 IDX-02 主口径分母的字段列）
CORE_FIELD_COLUMNS: tuple[str, ...] = tuple(f.column for f in RESUME_FIELDS)

#: 简历集的管理列（不由标注人填内容，由工具生成 / 标注人只更新 status 与 annotator）
RESUME_META_COLUMNS: tuple[str, ...] = (
    "resume_id",
    "file_name",
    "file_type",
    "source_note",
    "status",
    "annotator",
    "annotated_at",
    "remark",
)

#: 模板列顺序：管理列在前，字段列在后（便于标注人在 Excel 里冻结前几列）
RESUME_COLUMNS: tuple[str, ...] = RESUME_META_COLUMNS + CORE_FIELD_COLUMNS


#: 文件类型（图片件必须能单独挑出来 —— 需求 IDX-02 要求「图片件单列口径」）
FILE_TYPES: dict[str, str] = {
    "text_pdf": "文本层可直接提取的 PDF",
    "text_docx": "Word 文档（.docx；.doc 请先另存为 .docx）",
    "text_other": "其它文本格式（txt / 纯文本粘贴）",
    "scan_image": "图片件（jpg / png 截图或拍照）",
    "scan_pdf": "扫描件 PDF（无文本层，需 OCR）",
}

#: 图片/扫描件（IDX-02 要求单列统计的口径 = 这两个）
IMAGE_FILE_TYPES: tuple[str, ...] = ("scan_image", "scan_pdf")

#: 标注状态
ANNOTATION_STATUS: dict[str, str] = {
    "pending": "未开始",
    "annotating": "标注中（半成品，不计入质检达标）",
    "done": "已完成（质检只对 `done` 的行算达标）",
    "reviewed": "已复核（第二人抽查通过）",
}


# ============================================================================
# 查询-人才集（IDX-01）
# ============================================================================
#: 查询意图分类（**建议分类**，需求未规定，可按甲方习惯调整后重跑质检）
_QUERY_INTENTS: dict[str, str] = {
    "skill": "技能型：以技能/技术栈为主（「找熟悉 Milvus 的人」）",
    "experience": "经历型：以项目/任职经历为主（「做过金融风控项目」）",
    "education": "教育型：以学历/院校/专业为主（「985 硕士，计算机专业」）",
    "composite": "综合型：多个条件组合（「3 年以上 AI 项目经验的数字化转型骨干」）",
    "negative": "否定/排除型：含排除条件（「不限行业，但没做过外包」）",
}
QUERY_INTENTS = _QUERY_INTENTS


def queries_intent_note() -> str:
    """给规范文档用的一句话说明（避免文档与代码各写一份分类）。"""
    return "；".join(f"`{k}` = {v}" for k, v in QUERY_INTENTS.items())


#: 查询集列定义
QUERY_COLUMNS: tuple[str, ...] = (
    "query_id",
    "query_text",
    "intent",
    "expected_resume_ids",
    "top_k",
    "hard_filters",
    "notes",
    "status",
    "annotator",
    "annotated_at",
)


# ============================================================================
# 口径常量（文档与质检脚本共用，避免两处各写一份）
# ============================================================================
#: 「缺失」的判定规则 —— 评估时按此把系统输出与真值对齐
MISSING_RULES: tuple[str, ...] = (
    "真值为 `缺失`：系统也必须显式标记缺失，**输出任何具体值都判错**（并计入编造率）。",
    "真值有值：系统输出**归一化后相等**才判对（大小写、全半角、空白差异不算错）。",
    "列表字段（任职/项目/技能/证书）：按**集合**比对，顺序不计；"
    f"系统多出或漏掉的元素各计一次错（召回与准确率同权，因 F-3.2 只要求字段级准确率）。",
    "学历字段：系统落库为字典码、真值可能写中文名，评估前统一归一化到字典码再比对。",
    "联系方式：**不比对明文**，比对盐化 SHA256（`core.crypto.hash_contact`）；"
    "故标注集的明文列在评估前必须已 seal。",
)

ACCURACY_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("主口径（验收用）", "正确字段数 / 已标注字段数（分母只含**真值存在**的格子）"),
    ("辅助口径（参考）", "完全匹配份数 / 总份数（整份 13 字段全对才算对，❌ 不用于验收）"),
    ("编造率（单列）", "真值缺失却被系统填了值的格子数 / 真值缺失的格子数 —— 需求 F-3.2「不得编造」"),
    ("图片件口径（单列）", "在 `file_type ∈ {scan_image, scan_pdf}` 的子集上单独计算主口径（IDX-02 要求）"),
    ("查询集口径", "Recall@10 = 前 10 条结果中命中 `expected_resume_ids` 的比例，按查询取平均（IDX-01）"),
)
