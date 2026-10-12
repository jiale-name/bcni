"""CU-15 · A1 文本层抽取（批次 20）—— 「文本源缺失」根因的回归保护。

背景（批次 20 排查结论）
========================
批次 20 实测：6 个导入任务的文件**全部**走到 ``field_review`` 中断（用户看到
「解析失败 / PARSE_INTERRUPTED」），``graph_state`` 里 ``text=""``、``fields={}``、
``reasons=["no_fields"]``。根因不是简历有问题、也不是抽取器有问题 ——
用户随手给的真实文本型 PDF 用 pypdf 抽出 3509 字、规则抽取器正常出 4 字段 ——
**唯一坏的是「文字层从来没被读进 state」**：

1. 旧版 ``detect_format`` 把 ``pdf`` 划进 ``_OCR_EXTENSIONS`` → 一律走 OCR 占位；
2. ``ocr`` 是占位实现 → ``text`` 恒空；
3. docx/txt 虽然不走 OCR，但图上**没有「读文本」节点** → ``text`` 同样恒空。

⇒ 本文件钉住 A1 修复的三件事：
① 文件类型分流（图片→OCR；PDF/docx/纯文本→先读文字层）；
② ``extract_text`` 节点真的能把文字层填进 state（txt 直读 / docx 零依赖解析 /
   PDF pypdf 文字层），读不到时**优雅降级**而不是崩溃；
③ 「PDF 文字层为空 → 降级 OCR」的兜底路由仍然存在（扫描件场景）。

本文件全部是 L1 纯函数 / 节点级用例，**不需要数据库**。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.parse_graph import (
    KEY_TEXT_READER,
    _default_read_text,
    detect_format,
    extract_text,
    route_after_detect,
    route_after_extract_text,
)


def _ctx(state: dict) -> SimpleNamespace:
    """``detect_format`` / ``extract_text`` 只读 ``ctx.state``，桩即可（不依赖运行时）。"""
    return SimpleNamespace(state=state)


# ============================================================================
# ① 文件类型分流
# ============================================================================
@pytest.mark.parametrize(
    ("file_name", "doc_type", "needs_ocr"),
    [
        ("a.pdf", "pdf", False),  # PDF 不再强制走 OCR 占位（根因 A 的第一处修复）
        ("a.docx", "docx", False),
        ("a.doc", "docx", False),
        ("a.txt", "text", False),
        ("a.md", "text", False),
        ("a.png", "image", True),
        ("a.jpg", "image", True),
        ("a.webp", "image", True),
        ("no_ext", "unknown", False),  # 未知扩展名：尝试当纯文本读，而不是丢进 OCR
    ],
)
def test_detect_format_classifies_doc_types(file_name: str, doc_type: str, needs_ocr: bool) -> None:
    result = detect_format(_ctx({"file_name": file_name}))
    assert result.update["doc_type"] == doc_type
    assert result.update["needs_ocr"] is needs_ocr


def test_route_after_detect_images_go_ocr_docs_go_text_layer() -> None:
    assert route_after_detect({"needs_ocr": True}) == "ocr"
    assert route_after_detect({"needs_ocr": False}) == "extract_text"  # 不再直跳 extract_fields


def test_route_after_extract_text_fallbacks() -> None:
    # 有文字 → 直接抽取（不浪费一次 OCR）
    assert route_after_extract_text({"text": "姓名：张三", "doc_type": "pdf"}) == "extract_fields"
    # PDF 文字层为空 → 降级 OCR（扫描件场景的兜底必须保留）
    assert route_after_extract_text({"text": "", "doc_type": "pdf"}) == "ocr"
    # docx/纯文本文字层为空 → 仍去抽取（no_fields → 转人工，安全失败）
    assert route_after_extract_text({"text": "", "doc_type": "docx"}) == "extract_fields"
    assert route_after_extract_text({"text": "", "doc_type": "text"}) == "extract_fields"


# ============================================================================
# ② 内置读取器：txt / docx / pdf
# ============================================================================
def test_default_read_text_txt(tmp_path: Path) -> None:
    p = tmp_path / "resume.txt"
    p.write_text("姓名：张三\n手机：13800138000\n", encoding="utf-8")
    text, source, note = _default_read_text(str(p), "text")
    assert "13800138000" in text
    assert source == "txt"
    assert note is None


def test_default_read_text_docx_zero_dependency(tmp_path: Path) -> None:
    """docx 走 zipfile + XML（**不引入 python-docx**，requirements 保持轻净）。"""
    p = tmp_path / "resume.docx"
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>姓名：李四 13912345678</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    text, source, note = _default_read_text(str(p), "docx")
    assert "13912345678" in text
    assert source == "docx"
    assert note is None


def _build_minimal_pdf(path: Path, text: str) -> None:
    """手搓一个**合法**的最小 PDF（正确 xref 偏移），pypdf 可读出 Helvetica 文本。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


def test_default_read_text_pdf_text_layer(tmp_path: Path) -> None:
    """🔴 根因 A 的正面回归：**文本型 PDF 的文字层必须能被读出来**。

    批次 20 实测用户给的真实简历（4 页文本型 PDF）pypdf 可抽 3509 字 ——
    这里用手搓的最小合法 PDF 钉住同一条链路。
    """
    p = tmp_path / "resume.pdf"
    _build_minimal_pdf(p, "Name: Zhang San 13800138000")
    text, source, note = _default_read_text(str(p), "pdf")
    assert "13800138000" in text
    assert source == "pdf_text_layer"
    assert note is None


def test_default_read_text_missing_file_degrades_gracefully(tmp_path: Path) -> None:
    """文件不存在 → 空文本 + ``text_source_missing``，**绝不抛异常**（降级转人工）。"""
    text, source, note = _default_read_text(str(tmp_path / "nope.pdf"), "pdf")
    assert text == ""
    assert source == "none"
    assert note == "text_source_missing"


def test_default_read_text_corrupt_pdf_degrades_gracefully(tmp_path: Path) -> None:
    """损坏的 PDF → 空文本 + 空文字层标记（调用方降级 OCR），不崩溃。"""
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"this is not a pdf at all")
    text, source, note = _default_read_text(str(p), "pdf")
    assert text == ""
    assert source == "pdf_text_layer"
    assert note == "pdf_text_layer_empty"


# ============================================================================
# ③ 节点级：extract_text 真的把文字层填进 state
# ============================================================================
def test_extract_text_node_populates_text_into_state(tmp_path: Path) -> None:
    p = tmp_path / "resume.txt"
    p.write_text("姓名：王五 13700001111", encoding="utf-8")
    result = extract_text(_ctx({"file_path": str(p), "doc_type": "text"}))
    assert result.update["text"] == "姓名：王五 13700001111"
    assert result.update["text_source"] == "txt"
    assert result.update["ocr_placeholder"] is False


def test_extract_text_node_prefers_injected_reader() -> None:
    """``KEY_TEXT_READER`` 注入优先 —— 测试可用确定性假实现验证路由。"""
    calls: list[tuple[str, str]] = []

    def fake_reader(source: str, doc_type: str) -> tuple[str, str, None]:
        calls.append((source, doc_type))
        return "injected-text", "txt", None

    result = extract_text(_ctx({"file_path": "x.txt", "doc_type": "text", KEY_TEXT_READER: fake_reader}))
    assert result.update["text"] == "injected-text"
    assert calls == [("x.txt", "text")]


def test_full_text_layer_route_end_to_end(tmp_path: Path) -> None:
    """把三个纯函数串起来走一遍：txt 文件 → detect → extract_text → 路由直取 extract_fields。"""
    p = tmp_path / "resume.txt"
    p.write_text("姓名：赵六 13611112222", encoding="utf-8")
    state: dict = {"file_name": "resume.txt", "file_path": str(p)}
    state.update(detect_format(_ctx(state)).update)
    next_node = route_after_detect(state)
    assert next_node == "extract_text"
    state.update(extract_text(_ctx(state)).update)
    assert route_after_extract_text(state) == "extract_fields"
    assert "13611112222" in state["text"]
