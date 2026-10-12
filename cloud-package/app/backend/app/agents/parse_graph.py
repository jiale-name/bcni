"""档案解析图（F-A0 骨架 + F-3.1/3.2/3.3 的流程编排，CU-13 交付骨架）。

图结构（与架构 §4.3 的「格式识别 → OCR → 抽取 → 归一 → 置信度 → 入库」一致）
==========================================================================

```text
entry
  │
  ▼
detect_format ──(router: 图片/扫描件?)──▶ ocr ─┐
  │                                           │
  └────(文本类 / PDF 先读文字层，A1)──────────┴──▶ extract_text
                                                          │
                              (router: 文字层是否为空？)    │
                        ┌─────────────────────────────────┴────────┐
                  PDF 文字层为空                                 有文字
                        ▼                                          ▼
                      ocr（降级）                             extract_fields
                        │                                        │
                        └────────────────────────────────────────┘
                                                          │
                                                          ▼
                                                  normalize_fields
                                                          │
                                                          ▼
                                                  score_confidence
                                                          │
                              (router: 两级复核判定，批次 10)│
                        ┌─────────────────────────────────┴──────────────┐
              文件级（no_fields / overall_low）                    仅个别字段低置信
                        ▼                                                ▼
                   field_review ⏸                                   persist_talent ✅
                    (interrupt)                                  （低置信字段入 TBL-16
                        │                                          字段级复核队列）
                        │
          (router: 人工结论)
        ┌───────────────┴────────────────┐
        ▼                                ▼
  persist_talent ✅                manual_reject ✅
```

🔴 **本批（N1）交付的是「骨架 + 中断链路」，三个节点是占位实现**
==========================================================
``ocr`` / ``extract_fields`` / ``normalize_fields`` 的**真实逻辑**属
CU-14（导入 + OCR 选型）与 CU-15（字段抽取 + 字典归一）。本批的理由：

1. 这三个节点的依赖（OCR 后端 / 抽取 Prompt / 字典归一器）**都还没就绪**
   （N0 实测：OCR 后端缺失、金标准集待标注）；
2. 而**编排结构**（哪一步在后、什么时候中断、恢复后从哪继续）恰恰是最容易改错、
   也最值得先固化下来的部分 —— 它与具体模型无关，却决定了 CU-14/15 的接口形态；
3. 占位实现的**行为是安全的**：抽不出字段时 ``ThresholdEngine`` 会判「必须复核」
   （见 ``NEEDS_REVIEW = no_fields``），于是任务走 ``field_review`` 中断进人工队列 ——
   **不会静默产出一份空档案**。这正是「骨架没写完时该有的表现」。

⚠️ 因此**不要**把三个占位节点改成「看起来能跑」的假数据：
假数据会一路走到 ``persist_talent`` 并被当成真实解析结果入库，
而 CU-14 接入真实现时很难区分「哪些是假数据、哪些是没解析成功」。

CU-14/15 的接入方式
==================
节点函数从 ``state`` 里取**可注入的依赖**（``__extractor__`` / ``__normalizer__`` /
``__ocr__``），缺省时用占位实现。这样：
- N1 阶段：不注入 → 占位 → 走人工复核；
- CU-14/15 阶段：由 ``parse_service`` 注入真实实现 → **本文件一行不改**；
- 测试：注入确定性假实现 → 可完整验证「通过 / 驳回」两条恢复路径（L1 用例）。

🔴 CU-14 ~ CU-17 接手时必读：中断节点的**副作用红线**
==================================================
``field_review`` 是**中断节点**。在自研运行时下它只执行一次；
但在 **LangGraph 下会被重放**（``interrupt()`` 之前的代码执行**两次**）—— 因此：

1. ``field_review`` 的函数体必须保持「**只读 + 返回**」（当前实现即是，没有写操作）；
2. 需要「落复核待办 / 发通知 / 计费 / 写变更留痕」时，写在**后继节点**里，
   或写在 ``interrupt`` **之后**并保证幂等 —— 三重选择里**只有前两者是安全的**；
3. 下面的 ``interrupts`` / ``side_effects`` 是**契约不是注释**：
   装配期互斥校验拦「中断节点 + 副作用」，``tests/test_graph_replay_safety.py``
   的 AST 用例拦「漏声明」与「中断前偷偷写库」。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.graph import (
    GraphDefinition,
    InterruptRequest,
    NodeContext,
    NodeResult,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 图名 = ``agent_task.task_type``
GRAPH_NAME = "parse"

#: 文件类型分流（按扩展名；只看文件名，不看 MIME —— 见 detect_format 注释）
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"})  # 图片/扫描件：必须走 OCR
_PDF_EXTENSIONS = frozenset({"pdf"})
_DOCX_EXTENSIONS = frozenset({"docx", "doc"})
_TEXT_EXTENSIONS = frozenset({"txt", "text", "md", "csv"})
#: 兜底：未知扩展名按「纯文本尝试读取」处理（extract_text 内部会优雅失败 → 转人工）

#: 占位实现的标记（写入 state，便于一眼看出「这一份没走真抽取」）
PLACEHOLDER_NOTE = "extractor_not_implemented(CU-15)"

#: 可注入依赖在 state 里的键（下划线前缀 = 运行时内部键，与业务字段区分开）
KEY_EXTRACTOR = "__extractor__"
KEY_NORMALIZER = "__normalizer__"
KEY_OCR = "__ocr__"
KEY_TEXT_READER = "__text_reader__"  # 文本层读取器（缺省用内置 _default_read_text，零强依赖）
KEY_PERSIST = "__persist__"  # CU-14：入库回调（state → talent_id，见 parse_pipeline）

ExtractorFn = Callable[[str, dict[str, Any]], dict[str, Any]]
NormalizerFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
OcrFn = Callable[[str, dict[str, Any]], str]


def _trace(state: dict[str, Any]) -> str:
    """日志前缀：多文件并发导入时，靠它区分「这条日志是哪一份文件」。

    🔴 只打印 **ID / 文件名 / 长度 / 字段名**，**绝不打印正文与字段值**：
    简历正文、手机号、证件号都是 PII，而日志是最容易被忽略的泄露面（铁律 14）。
    「哪一步没抽出来」用长度和字段名就够了，不需要把原文打出来。
    """
    return f"resume={state.get('resume_id')} file={state.get('file_name')}"


def _conf(value: Any) -> float:
    """取置信度为 float（**只用于日志**）—— 日志路径绝不能因为脏值把解析流程带崩。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _fn_name(fn: Any) -> str:
    """取注入依赖的名字（排障时先确认「到底跑的是占位还是真实现」）。"""
    return str(
        getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or type(fn).__name__
    )


# ============================================================================
# 节点
# ============================================================================
def detect_format(ctx: NodeContext) -> NodeResult:
    """① 格式识别：判断文件类型，并决定走哪条读文本的路径。

    ⚠️ 判定只看扩展名（``file_name``），**不看 MIME** ——
    上传接口（API-36）已经用 MIME 做过 41501 拦截，这里是**流程分流**不是安全校验。
    再校验一次 MIME 只会让「扩展名与 MIME 不一致」的文件在两处得到不同结论。

    ⚠️ 排障用日志（CU-21）：分流结果由本节点 INFO 输出（``解析① 格式识别``），
    下一步由 :func:`route_after_detect` 输出（``解析① 分流 → ocr/extract_text``）。

    分流逻辑（A1，CU-15 文本层抽取）：
    - 图片/扫描件 → 直接 OCR（``needs_ocr=True``）；
    - PDF / docx / 纯文本 → 先走 ``extract_text`` 读文字层，读不到再降级 OCR。
    这样**文本型简历/文档不再因为「文本源缺失」而恒转人工**（根因 A）。
    """
    file_name = str(ctx.state.get("file_name") or "")
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext in _IMAGE_EXTENSIONS:
        doc_type, needs_ocr = "image", True
    elif ext in _PDF_EXTENSIONS:
        doc_type, needs_ocr = "pdf", False
    elif ext in _DOCX_EXTENSIONS:
        doc_type, needs_ocr = "docx", False
    elif ext in _TEXT_EXTENSIONS:
        doc_type, needs_ocr = "text", False
    else:
        # 兜底：未知扩展名也尝试当纯文本读（extract_text 会优雅失败 → 转人工），
        # 不因为「不认识扩展名」就把文件直接丢进 OCR 占位。
        doc_type, needs_ocr = "unknown", False
    logger.info(
        "解析① 格式识别 [%s] ext=%s doc_type=%s needs_ocr=%s",
        _trace(ctx.state),
        ext or "unknown",
        doc_type,
        needs_ocr,
    )
    return NodeResult(
        update={"format": ext or "unknown", "doc_type": doc_type, "needs_ocr": needs_ocr},
    )


def route_after_detect(state: dict[str, Any]) -> str:
    """条件边：图片/扫描件直接走 OCR；其余（PDF/docx/纯文本）先读文字层。

    ⚠️ 非 OCR 文件不再「直跳 extract_fields」—— 那样 ``state['text']`` 永远是空的
    （根因 A：文本源缺失）。改走 ``extract_text`` 把文字层填充进 state。
    """
    target = "ocr" if state.get("needs_ocr") else "extract_text"
    logger.info("解析① 分流 [%s] → %s", _trace(state), target)
    return target


def ocr(ctx: NodeContext) -> NodeResult:
    """② OCR 识别（**占位**）。

    真实实现（CU-14）需按架构 §9 的降级链：本地 PaddleOCR → 云端 OCR →
    都失败则标记该文件需人工处理（``OCR_TIMEOUT 50402``）。
    """
    fn: OcrFn | None = ctx.state.get(KEY_OCR)
    source = str(ctx.state.get("file_path") or ctx.state.get("file_name") or "")
    if fn is None:
        logger.warning(
            "解析② OCR [%s] OCR 实现未注入 → 走占位（text 为空，必然进人工复核）",
            _trace(ctx.state),
        )
        return NodeResult(
            update={"text": "", "note": PLACEHOLDER_NOTE, "ocr_placeholder": True}
        )
    text = fn(source, ctx.state)
    logger.info(
        "解析② OCR [%s] 识别器=%s 输出长度=%s",
        _trace(ctx.state),
        _fn_name(fn),
        len(text or ""),
    )
    return NodeResult(update={"text": text, "note": None, "ocr_placeholder": False})


def _default_read_text(source: str, doc_type: str) -> tuple[str, str, str | None]:
    """内置文本层读取器（**零强依赖、可注入替换**）。

    :return: ``(text, source_name, note)`` ——
        ``source_name`` ∈ ``{"txt","docx","pdf_text_layer","none"}``，
        便于在 state 里一眼看出「这份文本从哪来」；读不到时 ``text=""`` 并降级 OCR。

    🔴 设计要点（CU-15 文本层抽取，A1）：
    - txt：直接读文件（零依赖）；
    - docx：用 ``zipfile`` + ``xml.etree`` 抽 ``<w:t>`` 文本（**零依赖**，不引入 python-docx）；
    - pdf：懒导入 ``pypdf`` 读文字层（未安装则降级，避免强依赖拖垮环境）；
    - 任何异常都**吞掉并降级**，绝不抛出 —— 解析失败必须走「人工复核」而不是崩溃。
    """
    import os

    if not source or not os.path.isfile(source):
        # 测试夹具常只给 file_name 不给真实路径：当作「读不到」，交给 OCR/人工
        # 🔴 排障高频点：路径不存在是「文本层抽不到」最常见的原因，必须 WARNING 而非静默
        logger.warning(
            "解析②' 文本层不可用 source=%s exists=%s → 降级 OCR/人工",
            os.path.basename(source) if source else "<empty>",
            bool(source) and os.path.isfile(source),
        )
        return "", "none", "text_source_missing"

    try:
        if doc_type == "text":
            with open(source, "r", encoding="utf-8", errors="ignore") as fh:
                return fh.read(), "txt", None
        if doc_type == "docx":
            text = _read_docx_text(source)
            return text, "docx", None if text else "docx_text_empty"
        if doc_type in ("pdf", "unknown"):
            text = _read_pdf_text(source)
            return text, "pdf_text_layer", None if text else "pdf_text_layer_empty"
    except Exception as exc:  # noqa: BLE001 — 文本层读不到必须降级，不是崩溃
        logger.warning(
            "解析②' 文本层读取异常 doc_type=%s err=%s → 降级", doc_type, type(exc).__name__
        )
        return "", "none", f"text_layer_error:{type(exc).__name__}"
    logger.warning("解析②' 不支持的文本类型 doc_type=%s → 降级", doc_type)
    return "", "none", "unsupported_doc_type"


def _read_docx_text(path: str) -> str:
    """零依赖抽取 docx 正文：``word/document.xml`` 里的 ``<w:t>`` 文本拼接。"""
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    parts: list[str] = []
    for node in root.iter():
        # w:t 的 tag 形如 ``{namespace}w:t``
        if node.tag.endswith("}t") and node.text:
            parts.append(node.text)
    return "".join(parts)


def _read_pdf_text(path: str) -> str:
    """懒导入 ``pypdf`` 读 PDF 文字层；未安装或读不出时返回空串（调用方降级 OCR）。"""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover —— 依赖缺失时由 requirements 兜底安装
        try:
            from PyPDF2 import PdfReader  # 兼容旧名
        except ImportError:
            return ""
    try:
        reader = PdfReader(path)
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages)
    except Exception:  # noqa: BLE001 —— 加密/损坏 PDF 都当作「无文字层」
        return ""


def extract_text(ctx: NodeContext) -> NodeResult:
    """②' 文本层抽取（**A1 新增**，根因 A 的落点）。

    在「OCR 占位」之前先读文字层：txt/docx 直读，PDF 读文字层；
    读不到（扫描件/损坏）再由 ``route_after_extract_text`` 降级到 OCR。
    这样**文本型简历**能被规则抽取器正常解析，不再恒因 ``text=""`` 转人工。

    读取器可注入（``KEY_TEXT_READER``）：测试可注入确定性假实现验证路由。
    """
    reader = ctx.state.get(KEY_TEXT_READER)
    source = str(ctx.state.get("file_path") or ctx.state.get("file_name") or "")
    doc_type = str(ctx.state.get("doc_type") or "unknown")
    if reader is not None:
        text, source_name, note = reader(source, doc_type)
    else:
        text, source_name, note = _default_read_text(source, doc_type)
    logger.info(
        "解析②' 文本层抽取 [%s] doc_type=%s 读取器=%s 来源=%s 长度=%s note=%s",
        _trace(ctx.state),
        doc_type,
        _fn_name(reader) if reader is not None else "内置",
        source_name,
        len(text or ""),
        note,
    )
    if not text:
        logger.warning(
            "解析②' 文本层为空 [%s] note=%s → 按路由降级 OCR 或直接转人工",
            _trace(ctx.state),
            note,
        )
    return NodeResult(
        update={
            "text": text or "",
            "text_source": source_name,  # "txt" / "docx" / "pdf_text_layer" / "none"
            "ocr_placeholder": False,
            "note": note,
        }
    )


def route_after_extract_text(state: dict[str, Any]) -> str:
    """条件边（A1）：文字层是否为空？

    - 有文字 → 直接 ``extract_fields``（不再浪费一次 OCR）；
    - PDF 文字层为空 → 降级 ``ocr``（扫描件场景）；
    - docx/纯文本 文字层为空 → 仍去 ``extract_fields``（规则抽取会判 no_fields → 转人工，安全）。
    """
    if state.get("text"):
        logger.info("解析②' 分流 [%s] 文字层非空 → extract_fields", _trace(state))
        return "extract_fields"
    if str(state.get("doc_type") or "") == "pdf":
        logger.info("解析②' 分流 [%s] PDF 文字层为空 → 降级 ocr", _trace(state))
        return "ocr"
    logger.info(
        "解析②' 分流 [%s] 文字层为空且非 PDF → extract_fields（无字段会转人工）",
        _trace(state),
    )
    return "extract_fields"


def extract_fields(ctx: NodeContext) -> NodeResult:
    """③ 字段抽取（**占位**）。

    真实实现（CU-15）走模型网关（``scene=resume_parse``，本地 Ollama，不出网）+ Prompt
    （``scene=extract``），产出 ``{字段: {"value":..., "confidence":...}}``。
    """
    fn: ExtractorFn | None = ctx.state.get(KEY_EXTRACTOR)
    text = str(ctx.state.get("text") or "")
    if fn is None:
        # 🔴 抽不出字段 → 交给 ThresholdEngine 判「必须复核」，**不伪造数据**（见模块 docstring）
        logger.warning(
            "解析③ 字段抽取 [%s] 抽取器未注入 → 走占位（fields={}，必然进人工复核）",
            _trace(ctx.state),
        )
        return NodeResult(
            update={
                "fields": {},
                "confidences": {},
                "work_exps": [],
                "project_exps": [],
                "note": PLACEHOLDER_NOTE,
            }
        )
    result = fn(text, ctx.state)
    fields = result.get("fields") or {}
    logger.info(
        "解析③ 字段抽取 [%s] 抽取器=%s 文本长度=%s 命中字段=%s note=%s",
        _trace(ctx.state),
        _fn_name(fn),
        len(text),
        sorted(fields),  # 只打字段名，不打值（PII）
        result.get("note"),
    )
    if not fields:
        logger.warning("解析③ 字段抽取 [%s] 未命中任何字段 → 转人工", _trace(ctx.state))
    return NodeResult(
        update={
            "fields": fields,
            "confidences": result.get("confidences") or {},
            "work_exps": result.get("work_exps") or [],
            "project_exps": result.get("project_exps") or [],
            "note": result.get("note"),
        }
    )


def normalize_fields(ctx: NodeContext) -> NodeResult:
    """④ 字典归一（**占位**）。

    真实实现（CU-15）用 :class:`app.core.threshold` 之外的另一组确定性组件
    （TagDictValidator / 字典映射）把「本科/大学本科/学士」归一到
    ``dict_item.education_level`` 的 ``item_code``。
    """
    fn: NormalizerFn | None = ctx.state.get(KEY_NORMALIZER)
    fields = dict(ctx.state.get("fields") or {})
    confidences = dict(ctx.state.get("confidences") or {})
    if fn is None:
        logger.info(
            "解析④ 字典归一 [%s] 归一器未注入 → 原样透传 字段=%s",
            _trace(ctx.state),
            sorted(fields),
        )
        return NodeResult(
            update={
                "fields": fields,
                "confidences": confidences,
                "work_exps": list(ctx.state.get("work_exps") or []),
                "project_exps": list(ctx.state.get("project_exps") or []),
            }
        )
    out = fn(fields, ctx.state)
    out_fields = out.get("fields", fields)
    logger.info(
        "解析④ 字典归一 [%s] 归一器=%s 结果=%s 降置信字段=%s",
        _trace(ctx.state),
        _fn_name(fn),
        sorted(out_fields),
        sorted(
            name
            for name in out_fields
            if _conf((out.get("confidences") or {}).get(name, 1.0))
            < _conf(confidences.get(name, 1.0))
        ),
    )
    return NodeResult(
        update={
            "fields": out_fields,
            "confidences": out.get("confidences", confidences),
            "work_exps": out.get("work_exps", ctx.state.get("work_exps") or []),
            "project_exps": out.get("project_exps", ctx.state.get("project_exps") or []),
        }
    )


def score_confidence(ctx: NodeContext) -> NodeResult:
    """⑤ 置信度判定（**确定性**，铁律 10）。

    这里**只调用** :class:`app.core.threshold.ThresholdEngine`，
    不做任何自己的阈值比较 —— 阈值散落是「同一份数据两处结论不同」的根源。
    """
    # 局部导入：阈值引擎在 core/，agents 在 services 同层，本可顶层导入；
    # 但显式局部导入能让人一眼看出「这一步是确定性判定，不是模型行为」。
    from app.core.threshold import ThresholdEngine

    confidences = dict(ctx.state.get("confidences") or {})
    sample_key = f"{ctx.tenant_id}:{ctx.state.get('resume_id') or ctx.task_id}"
    decision = ThresholdEngine.needs_manual_review(confidences, sample_key=sample_key)
    logger.info(
        "解析⑤ 置信度判定 [%s] needed=%s reasons=%s 低置信字段=%s",
        _trace(ctx.state),
        decision.needed,
        decision.reasons,
        decision.fields,
    )
    return NodeResult(update={"review": decision.as_dict()})


#: **文件级**复核原因（批次 10 决策）：命中这些 → 整份走 ``field_review`` 中断。
#: 其余情况（个别字段低置信）→ 直接 ``persist_talent``，低置信字段以
#: ``review_status=0`` 入 TBL-16 队列（API-44~49 逐字段批量处置）。
#: 理由：字段级问题用文件级中断会让复核人「为 1 个字段打断一次」；
#: 而整份无字段时档案都不存在，字段级队列里没有可处置的行 —— 两级各管各的形态。
#:
#: ⚠️ 批次 21 新增 ``name_missing``（**这是有意反转批次 10 的「只有两条」口径**）：
#: 姓名缺失时档案**建不出来**（无名不入库），字段级队列里同样没有可处置的行，
#: 所以它必须走文件级人工介入 —— 人工补录姓名后 ``persist_talent`` 才能继续。
#: 判据反转处留痕：``tests/test_parse_pipeline.py::test_file_level_reasons_frozenset_is_pinned``
#: 已随本批次**同步更新**（不是删项，是加项并写明理由）。
FILE_LEVEL_REVIEW_REASONS: frozenset[str] = frozenset({"no_fields", "overall_low", "name_missing"})


def route_after_scoring(state: dict[str, Any]) -> str:
    """条件边（**两级复核**，批次 10 细化）：

    - 命中**文件级**原因（无字段 / 整体过低）→ ``field_review``（中断，API-94）；
    - 仅个别字段低置信 → ``persist_talent``（低置信字段入 TBL-16 字段级队列）。
    """
    review = state.get("review") or {}
    reasons = set(review.get("reasons") or [])
    if not review.get("needed"):
        logger.info("解析⑤ 分流 [%s] 无需复核 → persist_talent", _trace(state))
        return "persist_talent"
    if reasons & FILE_LEVEL_REVIEW_REASONS:
        logger.info(
            "解析⑤ 分流 [%s] 文件级原因=%s → field_review（挂起）",
            _trace(state),
            sorted(reasons & FILE_LEVEL_REVIEW_REASONS),
        )
        return "field_review"
    logger.info(
        "解析⑤ 分流 [%s] 仅字段级低置信=%s → persist_talent（入 TBL-16 队列）",
        _trace(state),
        sorted(review.get("fields") or []),
    )
    return "persist_talent"


def field_review(ctx: NodeContext) -> NodeResult:
    """⑥ 人工复核（**中断点**，F-A0.3 / ADR-11）。

    🔴 这是「关键决策点必须走人工确认」红线的落点（接口 §3 API-94 备注）。
    ``payload`` 里放**需要复核的字段与当前值**，让人工在同页面上看到「哪几项可疑、现在是什么」，
    而不是给一个空待办让人自己去翻档案。
    """
    review = ctx.state.get("review") or {}
    fields = dict(ctx.state.get("fields") or {})
    pending_fields = {
        name: fields.get(name)
        for name in (review.get("fields") or [])
    }
    # 🔴 只打字段名与原因，**不打值**（PII）——「哪几项可疑」看字段名就够了
    logger.info(
        "解析⑥ 挂起等待人工复核 [%s] reasons=%s 待复核字段=%s",
        _trace(ctx.state),
        review.get("reasons") or [],
        sorted(pending_fields),
    )
    return NodeResult(
        update={"awaiting_review": True},
        interrupt=InterruptRequest(
            interrupt_type="field_review",
            payload={
                "resume_id": ctx.state.get("resume_id"),
                "file_name": ctx.state.get("file_name"),
                "fields": pending_fields,
                "confidences": {
                    name: (ctx.state.get("confidences") or {}).get(name)
                    for name in (review.get("fields") or [])
                },
                "reasons": review.get("reasons") or [],
            },
            assignee_role="HR",
            note="存在低置信度字段，请复核后再入库",
        ),
    )


def route_after_review(state: dict[str, Any]) -> str:
    """条件边：人工结论决定分支。

    🔴 ``rejected`` **不静默丢弃**（接口 §3 备注）：走 ``manual_reject`` 终态，
    任务置 ``success`` 但带 ``rejected=True`` 标记 ——
    「被人工驳回」是一个**正常结束**，不是系统失败（``failed`` 会触发重试与告警）。
    """
    human = state.get("__human__") or {}
    entry = human.get("field_review") or {}
    return "manual_reject" if entry.get("decision") == "rejected" else "persist_talent"


def persist_talent(ctx: NodeContext) -> NodeResult:
    """⑦ 入库（**可注入**，CU-14 落地）。

    真实实现（:func:`parse_pipeline.make_persist_callback`）写 ``talent_base`` +
    ``talent_parse_field`` + ``talent_change_log``，并保留 ``resume_id → talent_id``
    映射。未注入时保持占位行为（``persisted=False``）—— 占位结果**不会**被当成
    真实解析结果入库（安全失败，见模块 docstring）。

    🔴 本节点在 ``side_effects`` 声明中：注入实现的幂等防线 = 以 ``import_file``
    为锚（已建档则返回旧 ID），不依赖「运行时只调一次」。
    """
    human = ctx.human_input("field_review") or {}
    persist_fn = ctx.state.get(KEY_PERSIST)
    if persist_fn is not None:
        talent_id = persist_fn(ctx.state)
        logger.info(
            "解析⑦ 入库 [%s] 回调=%s talent_id=%s 人工结论=%s",
            _trace(ctx.state),
            _fn_name(persist_fn),
            talent_id,
            human.get("decision"),
        )
        return NodeResult(
            update={
                "awaiting_review": False,
                "human_decision": human.get("decision"),
                "corrections": human.get("payload") or {},
                "talent_id": talent_id,
                "persisted": True,
                "note": None,
                "finished_at_node": ctx.node_name,
            }
        )
    logger.warning(
        "解析⑦ 入库 [%s] 入库回调未注入 → 占位（persisted=False，不写库）",
        _trace(ctx.state),
    )
    return NodeResult(
        update={
            "awaiting_review": False,
            "human_decision": human.get("decision"),
            "corrections": human.get("payload") or {},
            "persisted": False,  # 未注入（占位）—— 不会被当成真实解析结果
            "note": ctx.state.get("note") or "persist_placeholder(CU-14)",
            "finished_at_node": ctx.node_name,
        }
    )


def manual_reject(ctx: NodeContext) -> NodeResult:
    """⑦' 人工驳回终态。"""
    human = ctx.human_input("field_review") or {}
    logger.info("解析⑦' 人工驳回 [%s] 终态（正常结束，不计失败）", _trace(ctx.state))
    return NodeResult(
        update={
            "awaiting_review": False,
            "human_decision": "rejected",
            "rejected": True,
            "reject_comment": human.get("comment"),
            "finished_at_node": ctx.node_name,
        }
    )


# ============================================================================
# 图定义
# ============================================================================
def build_parse_graph() -> GraphDefinition:
    """构造档案解析图（每次调用返回**同一个结构**，节点函数是模块级纯函数）。"""
    return GraphDefinition(
        name=GRAPH_NAME,
        title="档案解析（F-3.1/3.2/3.3 + F-A0）",
        entry="detect_format",
        nodes={
            "detect_format": detect_format,
            "ocr": ocr,
            "extract_text": extract_text,
            "extract_fields": extract_fields,
            "normalize_fields": normalize_fields,
            "score_confidence": score_confidence,
            "field_review": field_review,
            "persist_talent": persist_talent,
            "manual_reject": manual_reject,
        },
        edges={
            # ocr → extract_fields（OCR 之后仍要抽取）
            "ocr": "extract_fields",
            "extract_fields": "normalize_fields",
            "normalize_fields": "score_confidence",
            # ⚠️ ``detect_format`` / ``extract_text`` / ``score_confidence`` / ``field_review``
            # 走条件边，故**不在此处**声明静态边（条件边优先于静态边，重复声明只会让人困惑）。
        },
        routers={
            "detect_format": route_after_detect,
            "extract_text": route_after_extract_text,
            "score_confidence": route_after_scoring,
            "field_review": route_after_review,
        },
        ends=frozenset({"persist_talent", "manual_reject"}),
        # 🔴 迁移控制项（1）（2）：中断点**必须显式声明**，且必须与「代码里真实构造
        #    ``InterruptRequest`` 的节点集合」一致 —— 由
        #    ``tests/test_graph_replay_safety.py`` 用 AST 比对，**漏声明即失败**。
        #    这样「新增一个中断节点」不会悄悄绕过契约。
        interrupts=frozenset({"field_review"}),
        # 🔴 迁移控制项（3）：会产生**外部副作用**的节点（写库 / 调模型 / 调 OCR / 发通知）。
        #
        # ⚠️ ``field_review`` **不在**此列，而且**不能**加进来（装配期会直接抛错）——
        #    它是中断节点，在 LangGraph 下会被**重放**（``interrupt()`` 之前的代码执行两次）。
        #    因此它的函数体必须保持「只读 + 返回」：
        #    CU-16 实现复核工作台时，若需要「落一条复核待办 / 发一条通知」，
        #    **必须**写在 ``field_review`` 的**后继节点**里（或在中断之后 + 幂等）。
        #
        # 声明 ``side_effects`` 的第三个用途：将来若启用「从任意检查点重放 / 时间旅行」，
        # **所有**列在这里的节点都必须保证幂等 —— 这就是那份审计清单。
        side_effects=frozenset(
            {
                "ocr",  # 调 OCR 后端（本地 PaddleOCR → 云端 OCR 降级链）
                "extract_fields",  # 走模型网关：**计费** + 落 ai_call_log
                "persist_talent",  # 写 talent_base / talent_parse_field + 变更留痕
                "manual_reject",  # 写人工驳回终态 + 变更留痕
            }
        ),
        retries={
            # OCR 与抽取是最容易受下游抖动影响的节点（超时 / 5xx），各重试 1 次
            "ocr": 1,
            "extract_fields": 1,
        },
        default_retries=0,
    )
