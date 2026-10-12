"""只读探针：用**修复后**的抽取链真实跑盘上的简历样本（不连 DB、不写任何东西）。

用途：在重启后端之前，先用新代码验证「文本层 + 姓名三级来源」对真实文件是否生效。
运行：.venv/Scripts/python.exe scripts/_probe_parse_real.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agents.parse_graph import _default_read_text  # noqa: E402
from app.services import parse_pipeline as pp  # noqa: E402

SAMPLES = [
    "data/uploads/360790567225004032/IMP20260924004/0001_何俏冬-全日制本科-个人简历.pdf",
    "data/uploads/360790567225004032/IMP20260924002/0001_付智超-8年工作经验-13979583933.pdf",
    "data/uploads/360790567225004032/IMP20260924001/0001_陈健鹏 - 4年工作经验 - 东北石油大学 - 17777555549.pdf",
    "data/uploads/360790567225004032/IMP20260924003/0001_郭恒-重庆科技大学-13877954432简历.pdf",
    "data/uploads/360790567225004032/IMP20260923005/0003_朱颂波-2年工作经验-15245543054.pdf",
    "data/uploads/360790567225004032/IMP20260923004/0001_埋雷-T01-固定期限2年-试用期超限且工资过低.docx",
]

_DOC_TYPE = {".pdf": "pdf", ".docx": "docx", ".doc": "doc", ".txt": "txt"}


def main() -> int:
    for rel in SAMPLES:
        path = BACKEND / rel
        print("=" * 78)
        print(f"[{path.name}]")
        if not path.exists():
            print("  !! 文件不存在，跳过")
            continue
        doc_type = _DOC_TYPE.get(path.suffix.lower(), "unknown")
        text, source_name, note = _default_read_text(str(path), doc_type)
        print(f"  doc_type={doc_type}  text_len={len(text)}  reader={source_name}  note={note}")
        if not text:
            print("  !! 文字层为空 → 会降级 OCR（占位）")
            continue
        # 折叠前后各看一眼：折叠是否把抬头拼坏（批次 21 踩过的坑）
        head_raw = "\n".join(text.splitlines()[:4])
        print(f"  ---- 原文前 4 行 ----\n{head_raw}")
        out = pp.rule_extract(text, {"file_name": path.name})
        print(f"  fields={out['fields']}")
        print(f"  confidences={out['confidences']}")
        print(f"  {out['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
