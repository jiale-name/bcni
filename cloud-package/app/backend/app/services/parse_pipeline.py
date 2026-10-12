"""档案解析流水线（CU-15，F-3.2 / F-3.3 的字段级实现）。

🔴 **职责边界**（对齐 :mod:`app.agents.parse_graph` 的注入设计）
================================================================
本模块为解析图提供三个**可注入依赖** + 一个入库回调：

| 注入键 | 实现者 | 说明 |
| --- | --- | --- |
| ``__ocr__`` | ❌ **本批不注入** | N0 实测：OCR 后端缺失（选型待验证）。图片件走占位 → 空文本 → 文件级人工介入（安全行为） |
| ``__extractor__`` | :func:`rule_extract` | **确定性规则抽取器**（正则基线）。LLM 抽取待金标准集定稿后接入（登记遗留） |
| ``__normalizer__`` | :func:`normalize_fields` | 字典归一（``dict_item`` 同义词 → ``item_code``），**确定性** |
| ``__persist__`` | :func:`make_persist_callback` | 写主档、TBL-12/TBL-13 明细、解析字段与变更留痕 |

🔴 铁律 10 的落点：抽取与归一是**确定性组件**（同一输入永远同一输出），
不调 LLM —— F-3.2 的「准确率」必须能在金标准集上**可复算**，规则基线是可复算的；
LLM 抽取结果每次不同，接入时必须以「LLM 值 + 规则兜底」的混合形态并保留
``extract_model`` 标识以区分来源。

🔴 分层复核的两级判定（批次 10 决策）
====================================
:mod:`app.agents.parse_graph` 原把「任何 need_review」都路由到文件级中断
（``field_review``）。本批把 ``route_after_scoring`` 细化为：

- **文件级**（``no_fields`` / ``overall_low``）→ ``field_review`` 中断（API-94 人工处置整份）；
- **字段级**（仅个别字段低置信）→ 直接 ``persist_talent``，低置信字段以
  ``review_status=0`` 入 TBL-16 队列（API-44~49 逐字段处置）。

理由：字段级问题用文件级中断处置，会让复核人「为 1 个字段打断一次」，
而 API-46 的批量确认才是为逐字段复核设计的形态；反过来整份无字段时
逐字段队列里没有可处置的行（档案都不存在），必须文件级。
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.tagdict import TagDictValidator
from app.core.threshold import HIGH_IMPACT_FIELDS
from app.dao import talent_dao, tag_dao
from app.models.enums import (
    ChangeSource,
    ImportFileParseStatus,
    ParseReviewStatus,
    TagSource,
    TalentDataStatus,
)
from app.models.talent import (
    ImportFile,
    TalentBase,
    TalentChangeLog,
    TalentParseField,
    TalentProjectExp,
    TalentTagRel,
    TalentWorkExp,
)

logger = get_logger(__name__)


class PersistRejected(Exception):
    """入库**业务驳回**（不是程序错误）：带原因码，供调用方落「可读的失败原因」。

    🔴 为什么要有这个类型（批次 21）：入库回调的两条「安全失败」路径
    （无名不入库 / 人工驳回）此前直接 ``raise ValueError``，调用方只能把
    ``f"{type(exc).__name__}: {exc}"`` 原样写进 ``import_file.parse_error`` ——
    用户在前端看到的是一行 Python 异常文本。有了原因码，文件行可以写
    ``NAME_MISSING`` 这类**可翻译的原因码**（``FAILURE_REASON_LABELS`` 是唯一事实）。
    """

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code

#: 抽取器标识（``talent_parse_field.extract_model`` —— 区分「哪个实现抽的」）
EXTRACTOR_ID = "rule-baseline-v1"

#: 字段中文名（API-44 出参 ``field_name``；与 HIGH_IMPACT_FIELDS 同源扩展）
FIELD_LABELS: dict[str, str] = {
    "name": "姓名",
    "gender": "性别",
    "phone": "手机号",
    "email": "邮箱",
    "education_level": "学历",
    "school": "院校",
    "major": "专业",
    "work_years": "工作年限",
    "birth_date": "出生年月",
    "id_card": "身份证号",
}

#: 允许入库到 ``talent_base`` 的字段（抽取器可以多给，多余的只进 TBL-16 留痕？不 ——
#: 多余字段**直接丢弃**：TBL-16 的行是「档案字段」不是「抽取日志」，
#: 塞进未建模字段会让 talent_base 与 TBL-16 的口径漂移）
PERSIST_FIELDS: frozenset[str] = frozenset(
    {"name", "gender", "phone", "email", "education_level", "school", "major", "work_years", "birth_date", "id_card"}
)


# ============================================================================
# ① 规则抽取器（确定性）
# ============================================================================
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: 姓名标签命中。⚠️ 故意**宽捕获**（``{2,8}``）而不是 ``{2,4}``：
#: 折叠汉字间空白后，``姓名：付 智 超 电 话 139…`` 会变成 ``姓名：付智超电话 139…``，
#: 若上限卡在 4 就会切成 ``付智超电`` —— 标签词被截断 ⇒ 边界检测失效 ⇒ 错名字入库。
#: 宽捕获后由 :func:`_trim_name_candidate` 按标签词截断并校验 2~4 字形状。
_NAME_RE = re.compile(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5·]{2,8})")
#: 院校：**必须带标签**（毕业院校 / 毕业学校 / 学校 / 院校）。
#: ⚠️ 批次 21 起**不含「学历」** —— 文本层折叠后「学历：本科」会被当成院校，
#: 于是 ``school=本科`` 静默入库（值域错了比缺值更难被发现）。
_SCHOOL_RE = re.compile(
    r"(?:毕\s*业\s*(?:院\s*校|学\s*校)|学\s*校|院\s*校)\s*[:：]\s*([^\s，,。；;]{2,30})"
)
#: 院校值兜底排除项：命中这些词说明抽到的是「学历」，不是院校（宁可缺值，不可错值）
_EDU_WORDS: frozenset[str] = frozenset(
    {"博士", "硕士", "研究生", "本科", "大学本科", "专科", "大专", "中专", "高中", "职高", "技校", "MBA", "EMBA"}
)
_MAJOR_RE = re.compile(r"专\s*业\s*[:：]\s*([^\s，,。；;]{2,30})")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_BIRTH_RE = re.compile(r"(?:出\s*生|出\s*生\s*年\s*月)\s*[:：]?\s*(\d{4})[-/年.](\d{1,2})")
_EDU_RE = re.compile(
    r"(博士|硕士|研究生|本科|大学本科|专科|大专|中专|高中|职高|技校|MBA|EMBA)"
)
#: 工作年限的显式标签布局（如「工作时长：4 年」）。
#: 该形式在 PDF 抬头中很常见，不能依赖「4 年工作经验」的后置词形态。
_WORK_YEARS_LABELED_RE = re.compile(
    r"工\s*作\s*(?:时\s*长|年\s*限|经\s*验)\s*[:：]?\s*(\d{1,2})\s*年"
)
_WORK_YEARS_RE = re.compile(r"(\d{1,2})\s*年(?:工作|以上|经验|工作时间)")
#: 教育经历常见的单行布局：「起止时间 学校 学历」。
#: 只接受完整的年月范围和受控学历词，避免把正文中的普通句子误当成院校。
_EDUCATION_TIMELINE_RE = re.compile(
    r"(?m)^\s*\d{4}[./]\d{1,2}\s*[-~\u2013\u2014]\s*"
    r"\d{4}[./]\d{1,2}\s+([^\s，,。；;]+?)\s*"
    # 这里不放「大学本科」：它会把「河南理工大学本科」误切成「河南理工」；
    # 学历字段仍由 _EDU_RE 负责识别并折叠。
    r"(博士|硕士|研究生|本科|专科|大专|中专|高中|职高|技校|MBA|EMBA)\s*$"
)

#: 履历日期只接受年月，写入 DATE 时按「开始月 1 日 / 结束月最后一天」归一。
_EXPERIENCE_DATE_RE = re.compile(
    r"(?P<start>\d{4}[./-]\d{1,2})\s*"
    r"(?:[-~至到—–]\s*(?P<end>(?:\d{4}[./-]\d{1,2})|至今|现在|目前|今))?"
)
_WORK_SECTION_NAMES = frozenset({"工作经历", "工作经验", "职业经历", "任职经历", "实习经历"})
_PROJECT_SECTION_NAMES = frozenset({"项目经历", "项目经验", "项目实践"})
_EXPERIENCE_SECTION_NAMES = _WORK_SECTION_NAMES | _PROJECT_SECTION_NAMES
_COMPANY_SUFFIX_RE = re.compile(
    r"^(.+?(?:有限责任公司|股份有限公司|有限公司|科技公司|集团公司|集团|研究院|公司))\s*(.+)$"
)
_ROLE_SUFFIX_RE = re.compile(
    r"^(.+?)\s+(.+(?:工程师|负责人|经理|架构师|开发|顾问|专员|主管|总监|设计师))$"
)

#: 学历同义词 → 规范词（归一器的前置折叠；权威映射在 ``dict_item``，这里是纯文本折叠）
_EDU_FOLD: dict[str, str] = {
    "研究生": "硕士",
    "大学本科": "本科",
    "大专": "专科",
    "职高": "高中",
    "技校": "中专",
}

#: 规则命中强度 → 置信度（**可解释的启发式**，不是模型输出）
_CONF_EXACT = 0.95  # 强格式（手机/邮箱/身份证）完整命中
_CONF_LABELED = 0.85  # 「姓名：张三」带标签命中
_CONF_INFERRED = 0.65  # 无标签推断（学历关键词全文出现）

#: 姓名三个来源的置信度（批次 21）。阈值 ``PARSE_CONFIDENCE_THRESHOLD=0.85``，
#: 故后两者（0.70 / 0.65）**必然**触发规则 1（``name`` 是高影响字段）→ 进 TBL-16
#: 字段级复核队列 —— 「先建档、有人担保」而不是「整份失败」。
_CONF_NAME_LABELED = _CONF_LABELED  # 「姓名：张三」
_CONF_NAME_FIRST_LINE = 0.70  # 正文首行的裸姓名（PDF 文字层常见形态）
_CONF_NAME_FILENAME = 0.65  # 文件名片段（抬头是图片时唯一的姓名来源）

#: 裸姓名候选的形状：2~4 个汉字（可带间隔号，如少数民族姓名）
_CJK_NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")

#: 首行/文件名误判防线：这些词长得像「2~4 个汉字」，但不是姓名
_NAME_STOPWORDS: frozenset[str] = frozenset(
    {
        "简历", "个人简历", "求职简历", "个人信息", "基本信息", "联系方式",
        "教育背景", "教育经历", "工作经历", "项目经历", "项目经验", "实习经历",
        "核心技能", "专业技能", "技能特长", "自我评价", "自我介绍", "自我描述",
        "求职意向", "个人优势", "获奖情况", "校园经历", "研究经历", "论文专利",
    }
)
_NAME_STOP_SUFFIXES: tuple[str, ...] = (
    "简历", "经历", "背景", "技能", "意向", "信息", "评价", "优势", "描述", "情况", "专利",
)

#: 首行姓名只看正文最前面几行（简历抬头的形态），避免把正文里的短行当成姓名
_NAME_FIRST_LINES = 5

#: 字段标签词（批次 21 的第二道防线）：
#: 折叠汉字间空白会把「姓名」与紧跟的词粘起来（``姓名：张三 电话 138…`` → ``姓名：张三电话 138…``），
#: 于是 2~4 字捕获会取到 ``张三电话``。这些词一旦出现在候选里，说明**已经越过字段边界**
#: —— 截断到它的前面；若它就开在候选首位，则整条作废（宁可转人工，不可把错名字写进档案）。
_LABEL_WORDS: tuple[str, ...] = (
    "电话", "手机", "邮箱", "性别", "学历", "籍贯", "出生", "年龄", "民族", "政治",
    "专业", "院校", "学校", "身高", "婚姻", "求职", "应聘", "联系", "地址", "微信",
    "工作", "经验", "年限", "现居", "期望", "到岗", "身价", "状态",
    "个人", "简介", "介绍", "信息", "背景", "能力", "技能", "意向", "优势", "评价",
    "经历", "情况", "描述", "爱好", "特长",
)


def _trim_name_candidate(value: str) -> str | None:
    """把候选姓名截到第一个字段标签词之前；形状不合格则返回 ``None``。

    - ``张三电话`` → ``张三``（截断，保留正确信息）；
    - ``电话张三`` → ``None``（标签词在首位 = 这条候选整体不可信）；
    - ``张三`` → ``张三``（原样通过）。
    """
    cut = len(value)
    for word in _LABEL_WORDS:
        idx = value.find(word)
        if 0 <= idx < cut:
            cut = idx
    if cut == 0:
        return None
    trimmed = value[:cut]
    return trimmed if _CJK_NAME_RE.match(trimmed) else None


def _fold_cjk_spaces(text: str) -> str:
    """折叠**汉字之间**的空白（批次 21，根因：PDF 文字层的字间距被抽成空格）。

    🔴 只删「两侧都是汉字」的**行内**空白（空格 / 制表 / 全角空格）：

    - ``付 智 超`` → ``付智超``：否则 ``姓名``/``学校`` 等所有以汉字为值的正则
      **一条都命中不了**（2026-09-24 实测：3 份文本型 PDF 全部因无姓名转人工/失败）；
    - ``Python 语言`` → **不动**：英文/数字之间的空格是语义分隔，删了就改了内容；
    - ``\\n`` **绝不折叠**（⚠️ 第一版用 ``\\s+`` 把换行也吞了，实测把整块抬头
      拼成一行 ``付智超性别 ：男 | …``）——换行是**结构分隔**：
      首行姓名启发式、逐行取值、``source_locator`` 都依赖它。
    - 中文标点不是汉字 ⇒ ``姓 名 ：张三`` → ``姓名 ：张三``，
      而正则里的 ``\\s*[:：]`` 本就能容忍该空格，无需再折叠标点侧。

    纯函数、零依赖、可复算（铁律 10）。
    """
    return re.sub(r"(?<=[\u4e00-\u9fa5])[ \t\u3000]+(?=[\u4e00-\u9fa5])", "", text)


def _name_from_first_line(text: str) -> str | None:
    """裸姓名兜底 ①：正文前几行里**独占一行**的 2~4 字汉字。

    典型形态（PDF 文字层）：``付 智 超\\n性 别 ：男 | 工作经验 ：8 年``。
    命中失败即返回 ``None``（由下一级兜底接手），**绝不猜**。
    """
    checked = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        checked += 1
        if checked > _NAME_FIRST_LINES:
            break
        if not _CJK_NAME_RE.match(line):
            continue
        if line in _NAME_STOPWORDS or line.endswith(_NAME_STOP_SUFFIXES):
            continue
        return line
    return None


def _name_from_filename(file_name: str) -> str | None:
    """裸姓名兜底 ②：文件名片段（抬头是图片时**唯一**的姓名来源）。

    ``郭恒-重庆科技大学-13877954432简历.pdf`` → ``郭恒``；
    ``2026届-张三-简历.docx`` → ``张三``（跳过带数字的 ``2026届`` 与停用词 ``简历``）。
    仍是纯规则：逐片段做形状判定，命不中就返回 ``None``。
    """
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    for seg in re.split(r"[-_—–·\s]+", stem):
        seg = re.sub(r"(?:个人|求职)?简历$", "", seg.strip()).strip()
        if not _CJK_NAME_RE.match(seg):
            continue
        if seg in _NAME_STOPWORDS or seg.endswith(_NAME_STOP_SUFFIXES):
            continue
        return seg
    return None


def _month_date(value: str, *, end_of_month: bool) -> str:
    """把 ``YYYY.MM`` / ``YYYY-MM`` 统一为 ISO 日期字符串。"""
    year_text, month_text = re.split(r"[./-]", value, maxsplit=1)
    year, month = int(year_text), int(month_text)
    day = calendar.monthrange(year, month)[1] if end_of_month else 1
    return date(year, month, day).isoformat()


def _parse_experience_dates(match: re.Match[str]) -> tuple[str, str | None, int]:
    """返回 ``(start_date, end_date, is_current)``，输入必须来自日期正则。"""
    start = _month_date(match.group("start"), end_of_month=False)
    end_token = (match.group("end") or "").strip()
    if not end_token or end_token in {"至今", "现在", "目前", "今"}:
        return start, None, 1 if end_token else 0
    return start, _month_date(end_token, end_of_month=True), 0


def _section_lines(lines: list[str], names: frozenset[str]) -> list[str]:
    """取一个经历章节，直到下一个已知章节标题。"""
    normalized = [line.strip().rstrip(":：") for line in lines]
    start = next((i for i, line in enumerate(normalized) if line in names), None)
    if start is None:
        return []
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if normalized[i] in _EXPERIENCE_SECTION_NAMES or normalized[i] in {
            "教育经历", "教育背景", "项目经历", "项目经验", "技能特长", "专业技能",
        }:
            end = i
            break
    return lines[start + 1 : end]


def _clean_experience_text(value: str) -> str:
    """清理条目文本，不改变英文技术名和中文语义空格。"""
    return re.sub(r"\s+", " ", value).strip(" \t|｜")


def _split_work_identity(value: str) -> tuple[str | None, str | None]:
    value = _clean_experience_text(value)
    value = re.sub(r"^[：:|｜、,，]+", "", value).strip()
    if not value:
        return None, None
    match = _COMPANY_SUFFIX_RE.match(value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    parts = re.split(r"\s+", value, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return value, None


def _split_project_identity(value: str) -> tuple[str | None, str | None]:
    value = _clean_experience_text(value)
    value = re.sub(r"^(?:项目名称|项目名)\s*[:：]\s*", "", value)
    if not value:
        return None, None
    match = _ROLE_SUFFIX_RE.match(value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return value, None


def _tech_stack(value: str) -> list[str]:
    value = re.sub(r"^技术栈\s*[:：]\s*", "", value).strip()
    return [item for item in re.split(r"[、,，/|｜;；\s]+", value) if item]


def _sort_and_dedupe_experiences(rows: list[dict[str, Any]], identity_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """按开始日期倒序、原始顺序稳定排序，并丢弃同一条重复明细。"""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        identity = tuple(row.get(field) for field in identity_fields)
        if not identity[0] or identity in unique:
            continue
        unique[identity] = row
    ordered = sorted(unique.values(), key=lambda row: (row.get("start_date") or "0000-00-00"), reverse=True)
    for seq_no, row in enumerate(ordered, start=1):
        row["seq_no"] = seq_no
    return ordered


def _extract_work_experiences(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in lines:
        line = _clean_experience_text(raw_line)
        if not line:
            continue
        match = _EXPERIENCE_DATE_RE.match(line)
        if match:
            if current is not None:
                current["description"] = "".join(current.pop("_description_lines", [])) or None
                rows.append(current)
            company, position = _split_work_identity(line[match.end() :])
            if not company:
                current = None
                continue
            start_date, end_date, is_current = _parse_experience_dates(match)
            current = {
                "company": company,
                "dept_name": None,
                "position_name": position,
                "start_date": start_date,
                "end_date": end_date,
                "is_current": is_current,
                "_description_lines": [],
                "source": 0,
            }
            continue
        if current is None:
            continue
        dept = re.match(r"部门\s*[:：]\s*(.+)$", line)
        if dept:
            current["dept_name"] = dept.group(1).strip()
        else:
            current["_description_lines"].append(line)
    if current is not None:
        current["description"] = "".join(current.pop("_description_lines", [])) or None
        rows.append(current)
    return _sort_and_dedupe_experiences(rows, ("company", "start_date", "end_date", "position_name"))


def _extract_project_experiences(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in lines:
        line = _clean_experience_text(raw_line)
        if not line:
            continue
        match = _EXPERIENCE_DATE_RE.search(line)
        is_header = bool(match and not re.match(r"^(项目简介|项目描述|职责|技术栈)\s*[:：]", line))
        if is_header:
            if current is not None:
                current["description"] = "".join(current.pop("_description_lines", [])) or None
                rows.append(current)
            before = line[: match.start()].strip(" ：:|｜")
            after = line[match.end() :].strip(" ：:|｜")
            identity = before or after
            project_name, role_name = _split_project_identity(identity)
            if not project_name:
                current = None
                continue
            start_date, end_date, _is_current = _parse_experience_dates(match)
            current = {
                "project_name": project_name,
                "role_name": role_name,
                "start_date": start_date,
                "end_date": end_date,
                "description": None,
                "tech_stack": [],
                "_description_lines": [],
                "source": 0,
            }
            continue
        if current is None:
            continue
        tech = re.match(r"技术栈\s*[:：]\s*(.+)$", line)
        role = re.match(r"(?:担任角色|角色)\s*[:：]\s*(.+)$", line)
        if tech:
            current["tech_stack"] = _tech_stack(tech.group(1))
        elif role:
            current["role_name"] = role.group(1).strip()
        else:
            current["_description_lines"].append(line)
    if current is not None:
        current["description"] = "".join(current.pop("_description_lines", [])) or None
        rows.append(current)
    return _sort_and_dedupe_experiences(rows, ("project_name", "start_date", "end_date"))


def rule_extract(text: str, state: dict[str, Any]) -> dict[str, Any]:
    """确定性规则抽取器（``__extractor__`` 的缺省注入实现）。

    🔴 批次 21 两处修正（均为「真机跑出来的」缺陷，不是猜的）：

    1. **汉字间空白折叠**（``_fold_cjk_spaces``）：PDF 文字层的字间距被抽成空格，
       导致所有「以汉字为值」的正则一条都不命中 → 整份解析失败；
    2. **姓名三级来源**：``姓名：`` 标签 → 正文首行裸姓名 → 文件名片段。
       后两级置信度低于阈值 ⇒ 必然进字段级复核队列（既有「不静默建档」也有「别整份失败」）。

    :return: 标量字段、置信度以及标准化的 ``work_exps`` / ``project_exps`` 数组。
        （形状与 :mod:`app.agents.parse_graph` 的 ``extract_fields`` 节点约定一致；
        ``note`` 记为 ``name_source=...``，便于在 ``agent_task.graph_state`` 里一眼看出
        这份的姓名是哪来的 —— 排障时「为什么这条名字是文件名推的」不该靠猜）
    """
    fields: dict[str, str] = {}
    confidences: dict[str, float] = {}
    raw_text = _fold_cjk_spaces(text or "")

    def put(name: str, value: str | None, conf: float) -> None:
        if value and value.strip():
            fields[name] = value.strip()
            confidences[name] = round(min(1.0, max(0.0, conf)), 3)

    m = _PHONE_RE.search(raw_text)
    put("phone", m.group(0) if m else None, _CONF_EXACT)
    m = _EMAIL_RE.search(raw_text)
    put("email", m.group(0) if m else None, _CONF_EXACT)
    m = _ID_CARD_RE.search(raw_text)
    put("id_card", m.group(0) if m else None, _CONF_EXACT)

    # ---- 姓名（三级来源，批次 21）----
    name: str | None = None
    name_source = "none"
    name_conf = _CONF_NAME_LABELED
    m = _NAME_RE.search(raw_text)
    if m:
        name = _trim_name_candidate(m.group(1))
        if name:
            name_source = "labeled"
    if not name:
        candidate = _name_from_first_line(raw_text)
        if candidate:
            name, name_source, name_conf = candidate, "first_line", _CONF_NAME_FIRST_LINE
    if not name:
        candidate = _name_from_filename(str(state.get("file_name") or ""))
        if candidate:
            name, name_source, name_conf = candidate, "filename", _CONF_NAME_FILENAME
    put("name", name, name_conf)

    m = _SCHOOL_RE.search(raw_text)
    if m and m.group(1) not in _EDU_WORDS:  # 「学历：本科」不是院校（见 _SCHOOL_RE 注释）
        put("school", m.group(1), _CONF_LABELED)
    if "school" not in fields:
        m = _EDUCATION_TIMELINE_RE.search(raw_text)
        if m:
            # 时间轴行是结构化推断，不如显式「毕业院校」标签可靠。
            put("school", m.group(1), _CONF_INFERRED)
    m = _MAJOR_RE.search(raw_text)
    put("major", m.group(1) if m else None, _CONF_LABELED)
    m = _BIRTH_RE.search(raw_text)
    if m:
        put("birth_date", f"{m.group(1)}-{int(m.group(2)):02d}", _CONF_LABELED)
    m = _WORK_YEARS_LABELED_RE.search(raw_text)
    if m:
        put("work_years", m.group(1), _CONF_LABELED)
    else:
        m = _WORK_YEARS_RE.search(raw_text)
        put("work_years", m.group(1) if m else None, _CONF_INFERRED)
    m = _EDU_RE.search(raw_text)
    if m:
        put("education_level", _EDU_FOLD.get(m.group(1), m.group(1)), _CONF_INFERRED)

    # 性别：仅当出现「性别：男/女」才可信（从名字猜性别不可接受）
    g = re.search(r"性\s*别\s*[:：]\s*(男|女)", raw_text)
    put("gender", {"男": "1", "女": "2"}.get(g.group(1)) if g else None, _CONF_LABELED)

    # 履历明细：章节识别和条目归一均为确定性规则，空章节返回空数组。
    lines = raw_text.splitlines()
    work_exps = _extract_work_experiences(_section_lines(lines, _WORK_SECTION_NAMES))
    project_exps = _extract_project_experiences(_section_lines(lines, _PROJECT_SECTION_NAMES))

    # 🔴 排障日志：只打**字段名与姓名来源**，不打值（手机号/证件号是 PII）
    logger.info(
        "规则抽取 file=%s 文本长度=%s 命中字段=%s 姓名来源=%s",
        state.get("file_name"),
        len(raw_text),
        sorted(fields),
        name_source,
    )
    return {
        "fields": fields,
        "confidences": confidences,
        "work_exps": work_exps,
        "project_exps": project_exps,
        # 姓名来源留痕（排障用；不进 TBL-16 的 raw_text —— 那里存的是**原文**）
        "note": f"name_source={name_source}",
    }


# ============================================================================
# ② 字典归一器（确定性）
# ============================================================================
def make_normalizer(db: Session, tenant_id: int) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """构造 ``__normalizer__``：把抽取值归一到 ``dict_item`` 的 ``item_code``。

    🔴 只归一**有字典挂靠的字段**（当前 = ``education_level``）：
    值若匹配某启用项的 ``item_code`` 或 ``item_name``（或其常见别名）→ 替换为
    ``item_code``；匹配不上 → **保留原值但降置信度**（0.5）——
    ❌ 不因为「字典里没有」就丢值：丢了数据，复核人看到的是「没抽到」，
    保留原值 + 降置信，复核人能判断「是同义词没覆盖，还是真的抽错了」。
    """

    from app.dao import dict_dao

    edu_items = dict_dao.list_items(db, tenant_id, "education_level", status=1)
    edu_map: dict[str, str] = {}  # 规范词/别名（小写）→ item_code
    for item in edu_items:
        edu_map[item.item_code.casefold()] = item.item_code
        edu_map[item.item_name.casefold()] = item.item_code
        # 常见别名（避免每租户重复维护；真正的别名体系属字典维护范围）
        aliases = {
            "本科": {"bachelor", "本科及以上", "大学本科"},
            "硕士": {"master", "研究生", "硕士研究生"},
            "博士": {"doctor", "phd"},
            "专科": {"associate", "大专"},
            "高中": {"high school"},
        }
        for alias in aliases.get(item.item_name, set()) | aliases.get(item.item_code, set()):
            edu_map[alias.casefold()] = item.item_code

    def _normalize(fields: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        confidences = dict(state.get("confidences") or {})
        out_fields = dict(fields)
        work_exps = list(out_fields.pop("work_exps", None) or state.get("work_exps") or [])
        project_exps = list(out_fields.pop("project_exps", None) or state.get("project_exps") or [])
        edu = out_fields.get("education_level")
        if edu:
            code = edu_map.get(str(edu).strip().casefold())
            if code:
                out_fields["education_level"] = code
            else:
                confidences["education_level"] = 0.5
        return {
            "fields": out_fields,
            "confidences": confidences,
            "work_exps": work_exps,
            "project_exps": project_exps,
        }

    return _normalize


# ============================================================================
# ③ 入库回调（``__persist__``）
# ============================================================================
def make_persist_callback(
    db: Session,
    tenant_id: int,
    operator_id: int | None,
    *,
    task_id: int,
    file_row: ImportFile,
    source_file: str,
) -> Callable[[dict[str, Any]], int]:
    """构造 ``__persist__``：把解析产物落成主档、TBL-12/TBL-13 和解析留痕。

    🔴 **幂等性说明**（对应 ``side_effects`` 声明）：本回调只在**每文件一次**的
    Agent 任务内被调用一次；「文件级重试」（API-43）会创建**新的** Agent 任务重跑，
    故 ``file_row.talent_id`` 已存在时**拒绝重复建档**（返回旧 ID）——
    这就是它的幂等防线：以 ``import_file`` 为锚，而不是「期望运行时只调一次」。

    :return: ``talent_id``（新建或已存在的档案 ID）
    """

    def _persist(state: dict[str, Any]) -> int:
        # 幂等锚：文件已建档 → 不重复建（重试/重放路径）
        if file_row.talent_id is not None:
            return int(file_row.talent_id)

        fields: dict[str, str] = dict(state.get("fields") or {})
        confidences: dict[str, float] = dict(state.get("confidences") or {})
        review: dict[str, Any] = dict(state.get("review") or {})
        pending_fields: set[str] = set(review.get("fields") or [])

        # 人工修正优先于 AI 值（恢复路径：human payload 里带 corrected 值）
        human = (state.get("__human__") or {}).get("field_review") or {}
        corrections = dict(human.get("payload") or {}).get("corrections") or {}
        for name, value in corrections.items():
            if name in PERSIST_FIELDS and str(value).strip():
                fields[name] = str(value).strip()
                confidences[name] = 1.0  # 人工修正的置信度就是「人担保」

        name = fields.get("name")
        if not name:
            # 🔴 无名不入库：空档案比少一份档案危险（检索/合并都建立在「有一份档案」上）。
            # 抛**领域异常**（带原因码）而不是裸 ValueError —— 调用方据此把文件标成
            # 「待人工补录（NAME_MISSING）」，而不是把 Python 异常文本糊到用户界面上。
            raise PersistRejected("NAME_MISSING", "missing_name: 解析结果无姓名，待人工补录")

        talent = TalentBase(
            tenant_id=tenant_id,
            name=name[:64],
            parse_task_id=task_id,
            data_status=int(TalentDataStatus.PENDING_REVIEW),
        )
        _apply_scalar_fields(talent, fields, tenant_id)
        talent_dao.insert_talent(db, talent)

        # 明细与主档共用当前 Session：任一明细写入失败都会由上层事务整体回滚。
        talent_dao.replace_work_exps(
            db,
            tenant_id,
            int(talent.id),
            _build_work_exp_rows(tenant_id, int(talent.id), state.get("work_exps") or []),
        )
        talent_dao.replace_project_exps(
            db,
            tenant_id,
            int(talent.id),
            _build_project_exp_rows(tenant_id, int(talent.id), state.get("project_exps") or []),
        )

        for field_name in sorted(fields):
            if field_name not in PERSIST_FIELDS:
                continue
            talent_dao.insert_parse_field(
                db,
                TalentParseField(
                    tenant_id=tenant_id,
                    talent_id=talent.id,
                    field_name=field_name,
                    raw_text=(state.get("text") or "")[:512] or None,
                    parsed_value=str(fields[field_name])[:512],
                    confidence=round(float(confidences.get(field_name, 1.0)), 3),
                    impact_level="HIGH" if field_name in HIGH_IMPACT_FIELDS else "LOW",
                    extract_model=EXTRACTOR_ID,
                    source_file=source_file[:255],
                    source_locator=state.get("source_locator"),
                    review_status=(
                        int(ParseReviewStatus.PENDING)
                        if field_name in pending_fields
                        else int(ParseReviewStatus.SKIPPED)
                    ),
                ),
            )

        # 变更留痕（change_source=0 AI 解析）：AI 产出的初值留痕，操作人记系统（None）
        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=tenant_id,
                talent_id=talent.id,
                change_source=int(ChangeSource.AI_PARSE),
                field_name=None,
                before_value=None,
                after_value=name[:512],
                operator_id=None,
                task_id=task_id,
            ),
        )

        # AI 打标（F-3.4：只能选受控词表启用项）—— CU-17 的闭环在这里接上
        if state.get("__auto_tag__"):
            _auto_tag(db, tenant_id, talent, fields)

        # resume_id → talent_id 映射（批次 5 遗留 ④：金标准评估的对齐键）
        file_row.talent_id = talent.id
        file_row.parse_status = int(ImportFileParseStatus.SUCCESS)
        file_row.parse_error = None
        db.flush()
        return int(talent.id)

    return _persist


def _parse_iso_date(value: Any) -> date | None:
    """解析结构化经历日期；脏日期按缺失处理，交给人工复核而不是中断整批。"""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _build_work_exp_rows(
    tenant_id: int, talent_id: int, rows: list[dict[str, Any]]
) -> list[TalentWorkExp]:
    result: list[TalentWorkExp] = []
    for index, row in enumerate(rows, start=1):
        company = str(row.get("company") or "").strip()
        if not company:
            continue
        result.append(
            TalentWorkExp(
                tenant_id=tenant_id,
                talent_id=talent_id,
                seq_no=int(row.get("seq_no") or index),
                company=company[:128],
                dept_name=str(row["dept_name"]).strip()[:128] if row.get("dept_name") else None,
                position_name=(
                    str(row["position_name"]).strip()[:128] if row.get("position_name") else None
                ),
                start_date=_parse_iso_date(row.get("start_date")),
                end_date=_parse_iso_date(row.get("end_date")),
                is_current=1 if row.get("is_current") else 0,
                description=str(row["description"]).strip() if row.get("description") else None,
                source=int(row.get("source") or 0),
            )
        )
    return result


def _build_project_exp_rows(
    tenant_id: int, talent_id: int, rows: list[dict[str, Any]]
) -> list[TalentProjectExp]:
    result: list[TalentProjectExp] = []
    for index, row in enumerate(rows, start=1):
        project_name = str(row.get("project_name") or "").strip()
        if not project_name:
            continue
        tech_stack = [str(item).strip() for item in (row.get("tech_stack") or []) if str(item).strip()]
        result.append(
            TalentProjectExp(
                tenant_id=tenant_id,
                talent_id=talent_id,
                seq_no=int(row.get("seq_no") or index),
                project_name=project_name[:128],
                role_name=str(row["role_name"]).strip()[:64] if row.get("role_name") else None,
                start_date=_parse_iso_date(row.get("start_date")),
                end_date=_parse_iso_date(row.get("end_date")),
                description=str(row["description"]).strip() if row.get("description") else None,
                tech_stack=tech_stack or None,
                source=int(row.get("source") or 0),
            )
        )
    return result


def _apply_scalar_fields(talent: TalentBase, fields: dict[str, str], tenant_id: int) -> None:
    """把抽取字段映射到 ``talent_base`` 列（PII 走加密 + 盐化哈希）。"""
    from app.core.crypto import encrypt_sensitive, hash_contact
    from app.models.enums import Gender

    def _int_or_none(v: str | None) -> int | None:
        try:
            return int(v) if v else None
        except ValueError:
            return None

    talent.phone_enc = encrypt_sensitive(fields["phone"]) if fields.get("phone") else None
    talent.phone_hash = hash_contact(fields["phone"], "phone") if fields.get("phone") else None
    talent.email_enc = encrypt_sensitive(fields["email"]) if fields.get("email") else None
    if fields.get("education_level"):
        talent.education_level = fields["education_level"][:32]
    if fields.get("school"):
        talent.school = fields["school"][:128]
    if fields.get("major"):
        talent.major = fields["major"][:128]
    talent.work_years = _int_or_none(fields.get("work_years"))
    if fields.get("gender") in {"0", "1", "2"}:
        talent.gender = int(fields["gender"])
        Gender.name_of(talent.gender)  # 值域自检（越界会在测试里炸出来）


def _auto_tag(db: Session, tenant_id: int, talent: TalentBase, fields: dict[str, str]) -> None:
    """AI 打标：**词表归一**后写入 ``talent_tag_rel``（source=AI）。

    🔴 规则：抽取文本与技能型标签的同义词匹配 → 归一到启用项 → 写入。
    归一走 :func:`TagDictValidator.normalize_to_dict` —— 词表里没有的词**直接丢弃**
    （不是建议、不是新词：自动打标不产生待审核项，待审核只来自显式建议接口 API-53）。
    """
    haystack = " ".join(str(v) for v in fields.values()).casefold()
    if not haystack:
        return
    entries = tag_dao.list_enabled_tag_entries(db, tenant_id)
    for code, entry in entries.items():
        synonyms = [str(entry.get("name") or "")] + [str(s) for s in (entry.get("synonyms") or [])]
        hit = any(s and s.casefold() in haystack for s in synonyms)
        if not hit:
            continue
        normalized = TagDictValidator.normalize_to_dict(str(entry["name"]), entries)
        if normalized != code:
            continue  # 归一歧义防御：命中词归到别的编码去了 → 跳过本标签
        existing = tag_dao.list_talent_tags(db, tenant_id, talent.id)
        if any(str(r.tag_code) == code for r in existing):
            continue
        tag_dao.insert_tag_rel(
            db,
            TalentTagRel(
                tenant_id=tenant_id,
                talent_id=talent.id,
                tag_code=code,
                confidence=0.80,
                source=int(TagSource.AI),
            ),
        )
        logger.info("AI 打标 talent=%s tag=%s", talent.id, code)


def build_parser_state(
    *,
    resume_id: int,
    file_name: str,
    file_path: str,
    ocr_used: bool,
    source_locator: str | None = None,
) -> dict[str, Any]:
    """组装单文件解析图的初始 state（业务键 + 注入依赖分开放）。"""
    return {
        "resume_id": resume_id,
        "file_name": file_name,
        "file_path": file_path,
        "ocr_used": ocr_used,
        "source_locator": source_locator,
        # 注入依赖（下划线前缀 = 运行时内部键）：OCR 本批不注入（占位 → 图片件走人工）
        "__auto_tag__": True,
    }


__all__ = [
    "EXTRACTOR_ID",
    "FIELD_LABELS",
    "PERSIST_FIELDS",
    "PersistRejected",
    "build_parser_state",
    "make_normalizer",
    "make_persist_callback",
    "rule_extract",
]
