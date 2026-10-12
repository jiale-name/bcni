"""CU-15 · 字段抽取 + 字典归一 + 置信度（F-3.2，解析流水线）。

分层
----
- **L1 契约层**（无外部依赖，任何机器都能跑）：
  规则抽取器的确定性（同输入同输出）、强格式字段置信度分层、
  学历同义词折叠、性别只认显式标注、归一器「命中 → item_code / 未命中 →
  保留原值降置信」、``TagDictValidator``（编码格式 / 中文派生编码的稳定性 /
  ``normalize_to_dict`` 未命中返回 ``None`` 绝不回传 AI 原文）、
  两级复核路由（文件级 vs 字段级）、``PERSIST_FIELDS`` 白名单、
  ``__persist__`` 幂等锚与「无名不入库」。
- **DB 依赖仅在 persist 部分出现**（缺 MySQL 时 skip）。

🔴 铁律 10 落点：本文件钉死「抽取与归一是确定性组件」——
任何把 LLM 调用混进 ``rule_extract`` / 归一器的改动都会在这里被炸出来。
"""

from __future__ import annotations

import inspect

import pytest

from app.core.tagdict import TAG_CODE_PATTERN, TagDictValidator
from app.services.parse_pipeline import (
    EXTRACTOR_ID,
    PERSIST_FIELDS,
    rule_extract,
)

# ============================================================================
# ① 规则抽取器：确定性 + 置信度分层
# ============================================================================
_RESUME = (
    "姓名：张三\n性别：男\n出生：1990年5月\n手机 13812348888\n"
    "邮箱 zhangsan@example.com\n学历：本科\n专业：计算机科学与技术\n"
    "毕业院校：某某大学\n8年工作经验\n身份证 110101199005012345"
)


def test_extractor_is_deterministic() -> None:
    """同一输入两次抽取结果完全一致（铁律 10：可复算）。"""
    a = rule_extract(_RESUME, {})
    b = rule_extract(_RESUME, {})
    assert a == b


def test_extractor_shapes_match_graph_contract() -> None:
    """返回形状与 parse_graph 的 ``extract_fields`` 节点约定一致。"""
    out = rule_extract(_RESUME, {})
    assert set(out) >= {"fields", "confidences"}
    assert set(out["fields"]) == set(out["confidences"])
    for conf in out["confidences"].values():
        assert 0.0 <= conf <= 1.0


def test_strong_format_fields_get_exact_confidence() -> None:
    """手机/邮箱/身份证（强格式）→ 最高置信。"""
    from app.services.parse_pipeline import _CONF_EXACT  # noqa: PLC0415 — 测试专用窥探

    out = rule_extract(_RESUME, {})
    for name in ("phone", "email", "id_card"):
        assert out["confidences"][name] == _CONF_EXACT, name


def test_labeled_vs_inferred_confidence_layering() -> None:
    """带标签命中（0.85）> 无标签推断（0.65）。"""
    from app.services.parse_pipeline import _CONF_INFERRED, _CONF_LABELED

    out = rule_extract(_RESUME, {})
    assert out["confidences"]["name"] == _CONF_LABELED
    assert out["confidences"]["education_level"] == _CONF_INFERRED


def test_phone_rejects_substring_of_longer_digits() -> None:
    """电话号码嵌在更长数字串里 → 不命中（前后瞻断言）。"""
    out = rule_extract("单号 913812348888001", {})
    assert "phone" not in out["fields"]


def test_gender_only_from_explicit_label() -> None:
    """性别只能来自「性别：男/女」—— 从名字猜性别不可接受。"""
    assert rule_extract("性别：女", {})["fields"]["gender"] == "2"
    assert "gender" not in rule_extract("李丽 非常有女人味地完成了任务", {})["fields"]


def test_edu_synonym_folding() -> None:
    """「大学本科/大专」折叠到规范词（本科/专科）。"""
    assert rule_extract("学历：大学本科", {})["fields"]["education_level"] == "本科"
    assert rule_extract("大专学历", {})["fields"]["education_level"] == "专科"
    assert rule_extract("研究生学历", {})["fields"]["education_level"] == "硕士"


def test_extractor_id_is_stable() -> None:
    """抽取器标识稳定（``extract_model`` 用于金标准评估的来源区分）。"""
    assert EXTRACTOR_ID == "rule-baseline-v1"


def test_empty_text_yields_empty_fields() -> None:
    """空文本 → 空 fields（不得有默认值兜底）。"""
    out = rule_extract("", {})
    assert out["fields"] == {}


# ============================================================================
# ② 归一器（需要 DB；缺依赖时用替身验证纯逻辑）
# ============================================================================
def _fake_dict_session() -> pytest.MonkeyPatch:
    """给 ``make_normalizer`` 打一个假 ``dict_dao.list_items``。"""

    class _Item:
        def __init__(self, code: str, name: str) -> None:
            self.item_code = code
            self.item_name = name

    from app.dao import dict_dao

    items = [_Item("BEN_KE", "本科"), _Item("SHUO_SHI", "硕士")]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(dict_dao, "list_items", lambda *a, **kw: items)
    return monkey


def test_normalizer_maps_alias_to_item_code() -> None:
    """同义词「大学本科」→ ``item_code``（确定性归一）。"""
    from app.services.parse_pipeline import make_normalizer

    monkey = _fake_dict_session()
    try:
        normalize = make_normalizer(None, 1)  # db 已被替换，传 None
        out = normalize({"education_level": "大学本科"}, {"confidences": {"education_level": 0.65}})
        assert out["fields"]["education_level"] == "BEN_KE"
    finally:
        monkey.undo()


def test_normalizer_keeps_unknown_value_and_downgrades_confidence() -> None:
    """未命中字典 → **保留原值** + 降置信 0.5（不丢值）。"""
    from app.services.parse_pipeline import make_normalizer

    monkey = _fake_dict_session()
    try:
        normalize = make_normalizer(None, 1)
        out = normalize({"education_level": "夜大"}, {"confidences": {"education_level": 0.65}})
        assert out["fields"]["education_level"] == "夜大"
        assert out["confidences"]["education_level"] == 0.5
    finally:
        monkey.undo()


# ============================================================================
# ③ TagDictValidator（CU-17 的确定性核心，放本文件因为持久化路径也在用）
# ============================================================================
def test_tag_code_pattern() -> None:
    assert TAG_CODE_PATTERN.match("PYTHON")
    assert TAG_CODE_PATTERN.match("LLM_OPS")
    assert TAG_CODE_PATTERN.match("A")
    for bad in ("python", "PYTHON-", "1ABC", "_A", "A B", "标签", ""):
        assert not TAG_CODE_PATTERN.match(bad), bad


def test_derive_code_ascii_and_chinese() -> None:
    assert TagDictValidator.derive_code("LLM Ops") == "LLM_OPS"
    zh = TagDictValidator.derive_code("机器学习")
    assert zh.startswith("TAG_") and len(zh) == 12
    # 中文派生编码必须**稳定**（同一名字永远同一编码）
    assert TagDictValidator.derive_code("机器学习") == zh


def test_normalize_to_dict_never_returns_original_text() -> None:
    """🔴 归一未命中 → ``None``（绝不回传 AI 原文，否则非启用词绕过审核闸门）。"""
    entries = {
        "PYTHON": {"name": "Python", "synonyms": ["python3", "蟒蛇"]},
        "JAVA": {"name": "Java", "synonyms": []},
    }
    assert TagDictValidator.normalize_to_dict("Python", entries) == "PYTHON"
    assert TagDictValidator.normalize_to_dict("python3", entries) == "PYTHON"
    assert TagDictValidator.normalize_to_dict("Python", entries) == "PYTHON"
    assert TagDictValidator.normalize_to_dict("Rust", entries) is None
    assert TagDictValidator.normalize_to_dict("", entries) is None


def test_normalize_to_dict_casefold_loose_match() -> None:
    entries = {"LLM_OPS": {"name": "LLM Ops", "synonyms": []}}
    assert TagDictValidator.normalize_to_dict("llm ops", entries) == "LLM_OPS"


# ============================================================================
# ④ 两级复核路由（文件级 vs 字段级）
# ============================================================================
def test_route_after_scoring_file_level_reasons() -> None:
    """``no_fields`` / ``overall_low`` → 文件级中断（``field_review``）。"""
    from app.agents.parse_graph import route_after_scoring

    assert route_after_scoring({"review": {"needed": True, "reasons": ["no_fields"], "fields": []}}) == "field_review"
    assert route_after_scoring({"review": {"needed": True, "reasons": ["overall_low"], "fields": []}}) == "field_review"


def test_route_after_scoring_field_level_goes_to_persist() -> None:
    """仅个别字段低置信 → 直接 ``persist_talent``（低置信字段入 TBL-16 队列）。"""
    from app.agents.parse_graph import route_after_scoring

    state = {"review": {"needed": True, "reasons": ["low_confidence"], "fields": ["work_years"]}}
    assert route_after_scoring(state) == "persist_talent"


def test_route_after_scoring_not_needed_persists() -> None:
    from app.agents.parse_graph import route_after_scoring

    assert route_after_scoring({"review": {"needed": False}}) == "persist_talent"
    assert route_after_scoring({}) == "persist_talent"


def test_file_level_reasons_frozenset_is_pinned() -> None:
    """文件级原因集合被钉死 —— 加新原因必须显式改这里并说明理由。

    🔁 **批次 21 反转（不是放松判据）**：批次 10 时这里钉死「只有
    ``no_fields`` / ``overall_low`` 两条」。批次 21 真机暴露：**姓名缺失**时
    档案同样建不出来（``parse_pipeline`` 的「无名不入库」），字段级队列里
    没有可处置的行 ⇒ 它必须与那两条**同级**走文件级人工介入。
    故这里**加项**（``name_missing``）而**不是删项或改成空断言**；
    反转后判据仍然挡住东西：把任意别的原因（如 ``sampled`` / ``high_impact``）
    加进文件级集合，本条依旧失败。
    """
    from app.agents.parse_graph import FILE_LEVEL_REVIEW_REASONS

    assert FILE_LEVEL_REVIEW_REASONS == frozenset({"no_fields", "overall_low", "name_missing"})


# ============================================================================
# ⑤ PERSIST_FIELDS 白名单 + 注入键
# ============================================================================
def test_persist_fields_covers_talent_base_columns() -> None:
    """白名单必须覆盖 TBL-11 的业务列（少一个 = 数据静默丢失）。"""
    expected = {
        "name", "gender", "phone", "email", "education_level",
        "school", "major", "work_years", "birth_date", "id_card",
    }
    assert PERSIST_FIELDS == frozenset(expected)


def test_parse_graph_has_persist_injection_key() -> None:
    """``__persist__`` 注入键存在于 parse_graph（未注入 = 安全失败 persisted=False）。"""
    import app.agents.parse_graph as pg

    source = inspect.getsource(pg)
    assert "KEY_PERSIST" in source and '"__persist__"' in source


def test_build_parser_state_enables_auto_tag() -> None:
    from app.services.parse_pipeline import build_parser_state

    state = build_parser_state(
        resume_id=1, file_name="a.pdf", file_path="/tmp/a.pdf", ocr_used=False
    )
    assert state["__auto_tag__"] is True
    assert state["resume_id"] == 1


# ============================================================================
# ⑥ __persist__ 幂等锚 + 无名不入库（需要 DB）
# ============================================================================
def test_persist_rejects_missing_name() -> None:
    """🔴 无名不入库：空档案比少一份档案危险。

    批次 21 起抛的是**领域异常** ``PersistRejected``（带原因码）而不是裸 ``ValueError``
    —— 调用方要据此把文件标成「待人工补录 NAME_MISSING」，
    而不是把一行 Python 异常文本写进用户可见的 ``parse_error``。
    """
    from app.services.import_service import FAILURE_REASON_LABELS
    from app.services.parse_pipeline import PersistRejected, make_persist_callback

    class _FakeFile:
        talent_id = None

    cb = make_persist_callback(None, 1, None, task_id=1, file_row=_FakeFile(), source_file="a.pdf")
    with pytest.raises(PersistRejected, match="missing_name") as ei:
        cb({"fields": {"phone": "13812348888"}, "confidences": {}})
    assert ei.value.reason_code == "NAME_MISSING"
    assert "NAME_MISSING" in FAILURE_REASON_LABELS, "原因码必须在「唯一事实」常量表里可翻译"


def test_persist_idempotent_anchor_returns_old_id(db) -> None:
    """``file_row.talent_id`` 已存在 → 直接返回旧 ID（重试/重放不重复建档）。"""
    from tests.conftest import db_available

    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")
    from app.services.parse_pipeline import make_persist_callback

    class _FakeFile:
        talent_id = 123456789

    cb = make_persist_callback(db, 1, None, task_id=1, file_row=_FakeFile(), source_file="a.pdf")
    assert cb({"fields": {"name": "张三"}, "confidences": {}}) == 123456789


def test_persist_human_correction_wins_over_ai_value() -> None:
    """人工修正优先于 AI 值，置信度 = 1.0（人担保）。"""
    # 纯逻辑部分通过 source 检查钉死（DB 路径在 L2 端到端里覆盖）
    import app.services.parse_pipeline as pp

    source = inspect.getsource(pp.make_persist_callback)
    assert "corrections" in source
    assert "confidences[name] = 1.0" in source


# ============================================================================
# ⑦ 批次 21 · 文本层字间距折叠 + 姓名三级来源（真机缺陷回归）
# ============================================================================
#: PDF 文字层的真实形态（2026-09-24 实测数据，来自 ``pypdf`` 抽出的原文）
_PDF_HEAD = "付 智 超\n性 别 ：男 | 工作经验 ：8 年\n手 机： 13979583933 | 学 历 ：本科"


def test_fold_cjk_spaces_only_folds_intra_cjk() -> None:
    """只折叠**汉字之间**的行内空白；英文间隔与换行必须保留。"""
    from app.services.parse_pipeline import _fold_cjk_spaces

    assert _fold_cjk_spaces("付 智 超") == "付智超"
    assert _fold_cjk_spaces("手 机： 13979583933") == "手机： 13979583933"
    # 英文/数字之间的空格是语义分隔，删了就改了内容
    assert _fold_cjk_spaces("熟练 使 用 Python 与 LangGraph") == "熟练使用 Python 与 LangGraph"
    # 🔴 换行是结构分隔：第一版用 ``\s+`` 把换行也吞了，整块抬头被拼成一行，
    #    首行姓名启发式随之全废（本条即那次缺陷的回归）。
    #    注意 ``性别 ：男`` 里「别」与「：」之间的空格**按设计保留**：``：`` 不是汉字
    #    （正则有 ``\s*[:：]`` 容忍它，无需折叠）。
    assert _fold_cjk_spaces("付 智 超\n性 别 ：男") == "付智超\n性别 ：男"


def test_name_labeled_trims_at_field_boundary() -> None:
    """带标签的姓名：折叠会把「姓名」与后续字段粘起来，必须按标签词截断。"""
    assert rule_extract("姓名：张三 电话 13812348888", {})["fields"]["name"] == "张三"
    assert rule_extract("姓名：付 智 超 电 话 13979583933", {})["fields"]["name"] == "付智超"
    # 标签词开在候选首位 → 整条作废（宁可转人工，不可把错名字写进档案）
    assert "name" not in rule_extract("电 话：张三", {})["fields"]


def test_name_from_first_line_for_pdf_layout() -> None:
    """PDF 抬头没有「姓名：」标签、姓名独占一行 → 首行兜底，置信度 0.70。"""
    from app.services.parse_pipeline import _CONF_NAME_FIRST_LINE

    out = rule_extract(_PDF_HEAD, {"file_name": "随便什么名字.pdf"})
    assert out["fields"]["name"] == "付智超"
    assert out["confidences"]["name"] == _CONF_NAME_FIRST_LINE
    assert out["note"] == "name_source=first_line"
    assert out["fields"]["gender"] == "1"  # 「性 别 ：男」也必须能抽到


def test_name_from_filename_when_header_is_image() -> None:
    """抬头是图片（文字层里没有姓名）→ 文件名兜底，置信度 0.65（必然进字段级复核）。"""
    from app.services.parse_pipeline import _CONF_NAME_FILENAME

    body = "编程 语 言  & 框 架： 熟练 使 用 Python\n多 智 能体与 Agent 开发： 掌 握 LangGraph"
    out = rule_extract(body, {"file_name": "郭恒-重庆科技大学-13877954432简历.pdf"})
    assert out["fields"]["name"] == "郭恒"
    assert out["confidences"]["name"] == _CONF_NAME_FILENAME
    assert out["note"] == "name_source=filename"
    # 文件名里的「重庆科技大学」（6 字）与手机号不是姓名
    out2 = rule_extract(body, {"file_name": "2026届-张三-简历.docx"})
    assert out2["fields"]["name"] == "张三"


def test_name_stopwords_are_rejected() -> None:
    """「个人简历」这类区块标题不是姓名 —— 三个来源都要挡住。"""
    for file_name in ("个人简历.pdf", "求职简历.docx"):
        out = rule_extract("个人简历\n联系方式：13812348888", {"file_name": file_name})
        assert "name" not in out["fields"], file_name
        assert out["note"] == "name_source=none"


def test_school_does_not_swallow_education_level() -> None:
    """「学历：本科」不是院校：折叠后可匹配到院校正则，必须由值域排除项挡住。"""
    out = rule_extract("学 历 ：本科 学 校 ：东北石油大学", {})
    assert out["fields"]["school"] == "东北石油大学"
    assert out["fields"]["education_level"] == "本科"


def test_work_years_accepts_labeled_duration_layout() -> None:
    """PDF 常把工作年限写成「工作时长：4 年」，不能只依赖「4 年工作经验」。"""
    out = rule_extract("工作时长：4 年 居住地址：深圳", {})
    assert out["fields"]["work_years"] == "4"


# ============================================================================
# ⑧ 履历明细结构化抽取与持久化回归（CU-14 / 第 4、5 步）
# ============================================================================
_EXPERIENCE_TEXT = """\
工作经历
2022.06-2026.07 星河智能科技有限公司 AI应用开发工程师
负责企业级 RAG 与 Agent 平台建设。
2020.03-2022.05 云图数据有限公司 算法工程师
负责知识库和模型服务开发。

项目经历
企业知识助手 AI应用开发工程师 2023.03-2026.07
项目简介：建设企业知识问答与智能检索平台。
技术栈：Python、FastAPI、Milvus、LangGraph
职责：负责 RAG 链路和 Agent 编排。
企业知识助手 AI应用开发工程师 2023.03-2026.07
项目简介：重复项目，不应重复入库。

客户服务 Agent 2021.01-2022.12
角色：技术负责人
技术栈：Python, LangGraph, Redis
负责多 Agent 协同流程设计。

教育经历
2017.09-2021.06 某某大学 本科
"""


def test_rule_extract_normalizes_work_and_project_experiences() -> None:
    """工作/项目经历按章节抽取、日期规范化、去重并按开始日期倒序。"""
    out = rule_extract(_EXPERIENCE_TEXT, {})

    assert out["work_exps"] == [
        {
            "seq_no": 1,
            "company": "星河智能科技有限公司",
            "dept_name": None,
            "position_name": "AI应用开发工程师",
            "start_date": "2022-06-01",
            "end_date": "2026-07-31",
            "is_current": 0,
            "description": "负责企业级 RAG 与 Agent 平台建设。",
            "source": 0,
        },
        {
            "seq_no": 2,
            "company": "云图数据有限公司",
            "dept_name": None,
            "position_name": "算法工程师",
            "start_date": "2020-03-01",
            "end_date": "2022-05-31",
            "is_current": 0,
            "description": "负责知识库和模型服务开发。",
            "source": 0,
        },
    ]
    assert out["project_exps"] == [
        {
            "seq_no": 1,
            "project_name": "企业知识助手",
            "role_name": "AI应用开发工程师",
            "start_date": "2023-03-01",
            "end_date": "2026-07-31",
            "description": "项目简介：建设企业知识问答与智能检索平台。职责：负责 RAG 链路和 Agent 编排。",
            "tech_stack": ["Python", "FastAPI", "Milvus", "LangGraph"],
            "source": 0,
        },
        {
            "seq_no": 2,
            "project_name": "客户服务 Agent",
            "role_name": "技术负责人",
            "start_date": "2021-01-01",
            "end_date": "2022-12-31",
            "description": "负责多 Agent 协同流程设计。",
            "tech_stack": ["Python", "LangGraph", "Redis"],
            "source": 0,
        },
    ]


def test_rule_extract_keeps_current_experience_open_ended() -> None:
    """「至今」统一为 end_date=None + is_current=1。"""
    out = rule_extract(
        "工作经历\n2024.01-至今 星河科技有限公司 后端工程师\n负责平台开发。",
        {},
    )
    assert out["work_exps"][0]["end_date"] is None
    assert out["work_exps"][0]["is_current"] == 1


def test_normalizer_preserves_structured_experiences(monkeypatch) -> None:
    """字典归一不能丢掉抽取器输出的结构化明细。"""
    from app.services.parse_pipeline import make_normalizer
    import app.dao.dict_dao as dict_dao

    monkeypatch.setattr(dict_dao, "list_items", lambda *_args, **_kwargs: [])
    normalize = make_normalizer(None, 1)
    work = [{"company": "星河科技有限公司"}]
    projects = [{"project_name": "知识助手"}]
    out = normalize(
        {"name": "张三", "work_exps": work, "project_exps": projects},
        {"confidences": {"name": 0.95}},
    )
    assert out["work_exps"] == work
    assert out["project_exps"] == projects


def test_persist_replaces_detail_rows_in_same_callback(monkeypatch) -> None:
    """主档建档成功后，工作/项目明细必须走同一事务回调落库。"""
    from app.models.talent import TalentBase
    from app.services.parse_pipeline import make_persist_callback
    import app.services.parse_pipeline as pp

    captured: dict[str, object] = {}

    class _Db:
        def flush(self) -> None:
            return None

    class _File:
        talent_id = None
        parse_status = None
        parse_error = None

    def _insert_talent(_db, talent: TalentBase) -> TalentBase:
        talent.id = 9001
        return talent

    monkeypatch.setattr(pp.talent_dao, "insert_talent", _insert_talent)
    monkeypatch.setattr(pp.talent_dao, "insert_parse_field", lambda *_args: None)
    monkeypatch.setattr(pp.talent_dao, "insert_change_log", lambda *_args: None)
    monkeypatch.setattr(pp, "_auto_tag", lambda *_args: None)
    monkeypatch.setattr(
        pp.talent_dao,
        "replace_work_exps",
        lambda _db, tenant_id, talent_id, rows: captured.update(
            work=(tenant_id, talent_id, list(rows))
        ) or len(list(rows)),
    )
    monkeypatch.setattr(
        pp.talent_dao,
        "replace_project_exps",
        lambda _db, tenant_id, talent_id, rows: captured.update(
            projects=(tenant_id, talent_id, list(rows))
        ) or len(list(rows)),
    )

    callback = make_persist_callback(
        _Db(), 7, None, task_id=11, file_row=_File(), source_file="resume.txt"
    )
    talent_id = callback(
        {
            "fields": {"name": "张三"},
            "confidences": {"name": 0.95},
            "work_exps": [{"company": "星河科技有限公司", "seq_no": 1}],
            "project_exps": [{"project_name": "知识助手", "seq_no": 1}],
        }
    )

    assert talent_id == 9001
    assert captured["work"][0:2] == (7, 9001)
    assert captured["projects"][0:2] == (7, 9001)
    assert captured["work"][2][0].company == "星河科技有限公司"
    assert captured["projects"][2][0].project_name == "知识助手"


def test_persist_idempotent_anchor_skips_detail_replacement(monkeypatch) -> None:
    """重试命中 import_file.talent_id 时，不重复插入或替换明细。"""
    from app.services.parse_pipeline import make_persist_callback
    import app.services.parse_pipeline as pp

    called = False

    class _File:
        talent_id = 9001

    def _unexpected(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(pp.talent_dao, "replace_work_exps", _unexpected)
    monkeypatch.setattr(pp.talent_dao, "replace_project_exps", _unexpected)
    callback = make_persist_callback(None, 7, None, task_id=11, file_row=_File(), source_file="resume.txt")

    assert callback({"work_exps": [{"company": "不应重复"}]}) == 9001
    assert called is False


def test_school_accepts_education_timeline_layout() -> None:
    """教育背景常用「起止时间 学校 学历」一行表达，而不是「毕业院校：」标签。"""
    out = rule_extract("教育背景\n2017.09—2022.06 河南理工大学 本科", {})
    assert out["fields"]["school"] == "河南理工大学"
    assert out["fields"]["education_level"] == "本科"


def test_name_missing_becomes_file_level_review_not_failure() -> None:
    """姓名完全抽不到 → 文件级复核（``name_missing``），而不是「解析失败」。"""
    from app.agents.parse_graph import route_after_scoring
    from app.core.threshold import ThresholdEngine

    decision = ThresholdEngine.needs_manual_review(
        {"phone": 0.95, "email": 0.95}, sample_key="1:1"
    )
    assert decision.needed is True
    assert "name_missing" in decision.reasons
    assert route_after_scoring({"review": decision.as_dict()}) == "field_review"
