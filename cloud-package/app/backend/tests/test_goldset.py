"""CU-28（金标准集工具）的测试 —— 全部 L1：**无外部依赖，任何机器上都必须全绿**。

金标准集是「甲方专家用几天时间手工标注」的资产，工具链本身必须**先被证明可靠**：
如果质检脚本漏检，200 份标完才会在 N6 评估时暴露口径问题 —— 那时返工成本是人天级的。
因此这里对「口径常量、模板生成、质检规则、脱敏」逐项钉死。
"""

from __future__ import annotations

import csv

import pytest

from app.config import settings
from app.core.crypto import ContactHashSaltError, hash_contact
from scripts.goldset.build import (
    QUERY_CSV_NAME,
    RESUME_CSV_NAME,
    _assign_file_types,
    build_query_rows,
    build_resume_rows,
    run_build,
    write_csv,
)
from scripts.goldset.schema import (
    ANNOTATION_STATUS,
    CORE_FIELD_COLUMNS,
    FILE_TYPES,
    IMAGE_FILE_TYPES,
    LIST_SEPARATOR,
    MISSING_MARKER,
    QUERY_COLUMNS,
    QUERY_INTENTS,
    QUERY_MIN_COUNT,
    RESUME_COLUMNS,
    RESUME_FIELDS,
    RESUME_META_COLUMNS,
    RESUME_MIN_COUNT,
)
from scripts.goldset.seal import HASHED_COLUMNS, seal_resume_rows
from scripts.goldset.validate import run_validate

# ============================================================================
# 口径常量（schema）
# ============================================================================
def test_columns_are_meta_plus_fields_without_overlap() -> None:
    assert RESUME_COLUMNS == RESUME_META_COLUMNS + CORE_FIELD_COLUMNS
    assert len(set(RESUME_COLUMNS)) == len(RESUME_COLUMNS), "列名不得重复"
    assert len(CORE_FIELD_COLUMNS) == len(RESUME_FIELDS) == 13


def test_field_columns_unique_and_documented() -> None:
    columns = [f.column for f in RESUME_FIELDS]
    assert len(set(columns)) == len(columns)
    for field in RESUME_FIELDS:
        assert field.label and field.group and field.table_ref, f"{field.column} 口径不全"
        assert field.cardinality in ("scalar", "list")


def test_image_types_are_declared_in_file_types() -> None:
    """IDX-02 要求「图片件单列口径」，故这两个取值必须真实存在。"""
    assert IMAGE_FILE_TYPES
    for file_type in IMAGE_FILE_TYPES:
        assert file_type in FILE_TYPES


def test_missing_marker_is_explicit() -> None:
    assert MISSING_MARKER.strip()
    # 缺失标记绝不能是空串 —— 否则与「还没标」无法区分，整份金标准失去可比性
    assert MISSING_MARKER != ""


def test_annotation_status_contains_pending_and_done() -> None:
    assert "pending" in ANNOTATION_STATUS
    assert "done" in ANNOTATION_STATUS


def test_min_counts_match_requirement() -> None:
    """需求 §12.1 / §6.2 的硬下限（≥200 份 / ≥50 条）不得被调低。"""
    assert RESUME_MIN_COUNT == 200
    assert QUERY_MIN_COUNT == 50


def test_query_columns_and_intents_declared() -> None:
    assert "expected_resume_ids" in QUERY_COLUMNS
    assert "query_text" in QUERY_COLUMNS
    assert set(QUERY_INTENTS) == {"skill", "experience", "education", "composite", "negative"}


# ============================================================================
# 模板生成（build）
# ============================================================================
def test_assign_file_types_count_and_dispersion() -> None:
    types = _assign_file_types(200, 0.2)
    assert len(types) == 200
    assert all(t in FILE_TYPES for t in types)
    images = [t for t in types if t in IMAGE_FILE_TYPES]
    assert len(images) == 40
    # 图片件必须**等间隔分散**而非集中在开头：否则标注熟练度会与文件类型混淆，
    # 后期质检就无法区分「图片件确实更难」与「后面标得更快」。
    image_indexes = [i for i, t in enumerate(types) if t in IMAGE_FILE_TYPES]
    assert image_indexes == list(range(0, 200, 5))


def test_assign_file_types_rejects_bad_ratio() -> None:
    with pytest.raises(ValueError):
        _assign_file_types(200, 1.5)
    with pytest.raises(ValueError):
        _assign_file_types(200, -0.1)


def test_build_resume_rows_shape() -> None:
    rows = build_resume_rows(200)
    assert len(rows) == 200
    assert set(rows[0]) == set(RESUME_COLUMNS)
    assert rows[0]["resume_id"] == "RS-0001"
    assert rows[-1]["resume_id"] == "RS-0200"
    assert rows[0]["status"] == "pending"


def test_build_resume_rows_leaves_field_columns_empty() -> None:
    """字段列**绝不预填**：预填会把「标注」变成「校对」，直接污染金标准的客观性。"""
    rows = build_resume_rows(200)
    for row in rows:
        for column in CORE_FIELD_COLUMNS:
            assert row[column] == "", f"{row['resume_id']}.{column} 不应预填内容"


def test_build_resume_rows_rejects_below_minimum() -> None:
    with pytest.raises(ValueError):
        build_resume_rows(199)


def test_build_query_rows_defaults() -> None:
    rows = build_query_rows(50)
    assert len(rows) == 50
    assert rows[0]["query_id"] == "Q-001"
    assert rows[-1]["query_id"] == "Q-050"
    assert rows[0]["top_k"] == "10"  # 指标就叫 Recall@10
    assert rows[0]["query_text"] == ""


def test_build_query_rows_rejects_below_minimum() -> None:
    with pytest.raises(ValueError):
        build_query_rows(49)


def test_write_csv_is_excel_friendly(tmp_path) -> None:
    """``utf-8-sig``：Excel 双击不乱码（纯 utf-8 在 Excel 里会显示成乱码）。"""
    path = tmp_path / "x.csv"
    written = write_csv(path, RESUME_COLUMNS, build_resume_rows(200))
    assert written == 200
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "缺少 BOM，Excel 打开会乱码"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 200
    assert rows[0]["resume_id"] == "RS-0001"


def test_run_build_refuses_to_overwrite_without_force(tmp_path) -> None:
    """🔴 标注是人工资产：误覆盖等于几天白干，故默认拒绝覆盖。"""
    run_build(tmp_path, resume_count=200, query_count=50)
    with pytest.raises(FileExistsError):
        run_build(tmp_path, resume_count=200, query_count=50)
    # 显式 force 才允许重来
    paths = run_build(tmp_path, resume_count=200, query_count=50, force=True)
    assert paths["resume"].name == RESUME_CSV_NAME
    assert paths["query"].name == QUERY_CSV_NAME
    assert (tmp_path / "README.md").exists()


def test_run_build_validates_before_writing(tmp_path) -> None:
    """数量非法时**不应留下半成品文件**（否则下次 validate 会读到残缺数据）。"""
    with pytest.raises(ValueError):
        run_build(tmp_path, resume_count=10, query_count=50)
    assert not (tmp_path / RESUME_CSV_NAME).exists()


# ============================================================================
# 质检（validate）
# ============================================================================
def _write_pair(tmp_path, resume_rows, query_rows) -> None:
    write_csv(tmp_path / RESUME_CSV_NAME, RESUME_COLUMNS, resume_rows)
    write_csv(tmp_path / QUERY_CSV_NAME, QUERY_COLUMNS, query_rows)


def _fresh(tmp_path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return build_resume_rows(200), build_query_rows(50)


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_validate_clean_template_passes_without_errors(tmp_path) -> None:
    """空模板（尚未开始标注）不该报 error —— 否则报告一开始就有噪音。"""
    resume_rows, query_rows = _fresh(tmp_path)
    _write_pair(tmp_path, resume_rows, query_rows)
    report = run_validate(tmp_path)
    assert report.errors == [], [i.message for i in report.errors]
    assert report.passed
    assert "R-020" in _codes(report.warnings)
    assert "Q-009" in _codes(report.warnings)


def test_validate_missing_files(tmp_path) -> None:
    report = run_validate(tmp_path)
    assert "F-001" in _codes(report.errors)
    assert not report.passed


def test_validate_flags_duplicate_resume_id(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    resume_rows[1]["resume_id"] = resume_rows[0]["resume_id"]
    _write_pair(tmp_path, resume_rows, query_rows)
    report = run_validate(tmp_path)
    assert "R-002" in _codes(report.errors)


def test_validate_flags_illegal_file_type_and_status(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    resume_rows[0]["file_type"] = "word"
    resume_rows[1]["status"] = "finished"
    _write_pair(tmp_path, resume_rows, query_rows)
    codes = _codes(run_validate(tmp_path).errors)
    assert "R-010" in codes
    assert "R-011" in codes


def test_validate_flags_insufficient_scale(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    _write_pair(tmp_path, resume_rows[:150], query_rows[:30])
    codes = _codes(run_validate(tmp_path).errors)
    assert "R-001" in codes
    assert "Q-001" in codes


def test_validate_flags_blank_cells_in_done_rows(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    resume_rows[0]["status"] = "done"  # 完成但字段全空
    resume_rows[0]["annotator"] = "张三"
    _write_pair(tmp_path, resume_rows, query_rows)
    codes = _codes(run_validate(tmp_path).warnings)
    assert "R-021" in codes


def test_validate_flags_missing_annotator(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    for row in resume_rows[:5]:
        row["status"] = "done"
        for column in CORE_FIELD_COLUMNS:
            row[column] = MISSING_MARKER
    _write_pair(tmp_path, resume_rows, query_rows)
    assert "R-022" in _codes(run_validate(tmp_path).warnings)


def test_validate_flags_no_image_samples(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    for row in resume_rows:
        row["file_type"] = "text_pdf"
    _write_pair(tmp_path, resume_rows, query_rows)
    assert "R-031" in _codes(run_validate(tmp_path).warnings)


def test_validate_flags_broken_resume_reference(tmp_path) -> None:
    """最常见的标注事故：编号补零位数不一致（``RS-007`` vs ``RS-0007``）。"""
    resume_rows, query_rows = _fresh(tmp_path)
    query_rows[0].update({"query_text": "找有 Milvus 经验的人", "expected_resume_ids": "RS-007"})
    query_rows[0]["status"] = "done"
    _write_pair(tmp_path, resume_rows, query_rows)
    assert "Q-007" in _codes(run_validate(tmp_path).errors)


def test_validate_accepts_valid_reference(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    query_rows[0].update({"query_text": "找有 Milvus 经验的人", "expected_resume_ids": "RS-0007"})
    query_rows[0]["status"] = "done"
    _write_pair(tmp_path, resume_rows, query_rows)
    codes = _codes(run_validate(tmp_path).errors)
    assert "Q-006" not in codes
    assert "Q-007" not in codes


def test_validate_supports_multiple_expected_ids(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    query_rows[0].update(
        {
            "query_text": "找 AI 项目经验的人",
            "expected_resume_ids": f"RS-0001{LIST_SEPARATOR}RS-0002{LIST_SEPARATOR}RS-0003",
        }
    )
    query_rows[0]["status"] = "done"
    _write_pair(tmp_path, resume_rows, query_rows)
    assert "Q-007" not in _codes(run_validate(tmp_path).errors)


def test_validate_flags_duplicate_query_text(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    for row in query_rows[:2]:
        row.update({"query_text": "找懂 RAG 的人", "expected_resume_ids": "RS-0001", "status": "done"})
    _write_pair(tmp_path, resume_rows, query_rows)
    assert "Q-003" in _codes(run_validate(tmp_path).warnings)


def test_validate_progress_reflects_done_rows(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    for row in resume_rows[:10]:
        row["status"] = "reviewed"
    _write_pair(tmp_path, resume_rows, query_rows)
    report = run_validate(tmp_path)
    assert report.resume_done == 10
    assert report.resume_total == 200
    assert report.image_total == 40


def test_validate_report_markdown_contains_sections(tmp_path) -> None:
    resume_rows, query_rows = _fresh(tmp_path)
    _write_pair(tmp_path, resume_rows, query_rows)
    md = run_validate(tmp_path).to_markdown()
    for section in ("## 一、规模与进度", "## 二、字段填充率", "## 三、质检结论", "## 四、口径提醒"):
        assert section in md
    assert MISSING_MARKER in md


# ============================================================================
# 脱敏（seal）
# ============================================================================
def _one_resume_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in RESUME_COLUMNS}
    row.update({"resume_id": "RS-0001", "file_type": "text_pdf", "status": "done"})
    row.update(overrides)
    return row


def test_seal_requires_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 没有盐就必须报错 —— 静默产出「看起来脱敏、实则弱哈希」是最危险的结果。"""
    monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "")
    with pytest.raises(ContactHashSaltError):
        seal_resume_rows([_one_resume_row(phone="13812348888")])


def test_seal_replaces_plaintext_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
    columns, sealed, hashed, problems = seal_resume_rows(
        [_one_resume_row(phone="+86 138-1234-8888", email=" Zhang@Example.COM ", cert=MISSING_MARKER)]
    )
    assert "phone" not in columns and "email" not in columns
    assert "phone_hash" in columns and "email_hash" in columns
    assert sealed[0]["phone_hash"] == hash_contact("13812348888", "phone")
    assert sealed[0]["email_hash"] == hash_contact("zhang@example.com", "email")
    # 缺失标记是语义符号，不参与哈希
    assert sealed[0]["cert"] == MISSING_MARKER
    assert hashed == 2
    assert problems == []


def test_seal_keeps_empty_cells_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONTACT_HASH_SALT", "unit-test-salt")
    _, sealed, hashed, _ = seal_resume_rows([_one_resume_row()])
    assert sealed[0]["phone_hash"] == ""
    assert sealed[0]["email_hash"] == ""
    assert hashed == 0


def test_seal_hash_columns_match_db_column_names() -> None:
    """输出列名必须与 TBL-11 的实际列名一致（``phone_hash`` / ``email_hash``）。"""
    assert HASHED_COLUMNS["phone"][0] == "phone_hash"
    assert HASHED_COLUMNS["email"][0] == "email_hash"
