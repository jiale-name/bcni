"""金标准集模板生成（CU-28）。

产出三份文件（默认写入 ``<仓库根>/data/goldset/``）：

=======================  ==========================================================
文件                      用途
=======================  ==========================================================
``resume_annotations.csv``  简历标注集：**200 行**骨架（一列一个待标字段），交甲方 HR 专家填写
``query_set.csv``           查询-人才集：**50 行**骨架，交甲方业务专家填写
``README.md``               给标注人的一页纸说明（含 PII 红线与质检方法）
=======================  ==========================================================

三条刻意的设计
--------------
1. **CSV 而非 xlsx** —— 用 ``utf-8-sig``（带 BOM）编码，Excel 双击**不乱码**；
   而 xlsx 需要 openpyxl 读写、版本间还可能丢列宽，对「几十行 × 二十列」的标注表没必要。
2. **默认不覆盖已有文件** —— 标注是**以人天计的人工资产**，误覆盖等于几天白干；
   要重来必须显式 ``--force``。
3. **模板里绝不预填任何字段内容** —— 只给编号、文件名、文件类型等「事实列」，
   字段列一律留空。预填会让标注人「顺着改」，把标注变成校对，直接污染金标准的客观性。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from scripts.goldset.schema import (
    ANNOTATION_STATUS,
    FILE_TYPES,
    MISSING_MARKER,
    QUERY_COLUMNS,
    QUERY_MIN_COUNT,
    RESUME_COLUMNS,
    RESUME_FIELDS,
    RESUME_MIN_COUNT,
)

__all__ = [
    "build_resume_rows",
    "build_query_rows",
    "write_csv",
    "write_readme",
    "run_build",
    "RESUME_CSV_NAME",
    "QUERY_CSV_NAME",
    "README_NAME",
]

RESUME_CSV_NAME = "resume_annotations.csv"
QUERY_CSV_NAME = "query_set.csv"
README_NAME = "README.md"

#: 默认图片件比例。需求未规定（只说「图片件单列口径」），取 20% 作为**起点**，
#: 由甲方按实际简历构成调整 —— 该比例直接影响图片件口径的样本量。
DEFAULT_IMAGE_RATIO = 0.2


def _assign_file_types(count: int, image_ratio: float) -> list[str]:
    """把文件类型**等间隔**铺开（而不是前 40 份全是图片件）。

    等间隔是为了打散标注难度：若图片件全堆在开头，标注人的熟练度曲线会与
    文件类型混淆，后期质检就无法区分「图片件确实更难」还是「后面标得更快」。
    """
    if not 0.0 <= image_ratio <= 1.0:
        raise ValueError(f"image_ratio 必须在 [0, 1] 内，实际 {image_ratio}")

    image_count = round(count * image_ratio)
    image_indexes: set[int] = set()
    if image_count > 0:
        step = count / image_count
        for k in range(image_count):
            image_indexes.add(min(count - 1, int(k * step)))

    types: list[str] = []
    text_toggle = 0
    for index in range(count):
        if index in image_indexes:
            types.append("scan_image")
            continue
        # 文本件在 pdf / docx 之间交替，让两种解析路径都有样本
        types.append("text_pdf" if text_toggle % 2 == 0 else "text_docx")
        text_toggle += 1
    return types


def build_resume_rows(
    count: int = RESUME_MIN_COUNT,
    image_ratio: float = DEFAULT_IMAGE_RATIO,
    id_prefix: str = "RS",
) -> list[dict[str, str]]:
    """生成简历标注集的空骨架（``count`` 行）。

    字段列一律留空（❌ 不预填 ``缺失``）：**「还没标」与「确实缺失」必须能区分**，
    否则质检脚本无法判断是「忘填了」还是「本来就该填缺失标记」。
    """
    if count < RESUME_MIN_COUNT:
        raise ValueError(
            f"简历标注集规模不足需求下限：需求 §12.1 要求 ≥{RESUME_MIN_COUNT} 份，当前 {count}"
        )

    file_types = _assign_file_types(count, image_ratio)
    rows: list[dict[str, str]] = []
    for index in range(count):
        resume_id = f"{id_prefix}-{index + 1:04d}"
        file_type = file_types[index]
        # 文件名按类型给一个自洽的扩展名，避免标注人拿到模板后自己乱猜
        suffix = {
            "text_pdf": ".pdf",
            "text_docx": ".docx",
            "text_other": ".txt",
            "scan_image": ".jpg",
            "scan_pdf": ".pdf",
        }[file_type]
        row = {column: "" for column in RESUME_COLUMNS}
        row.update(
            {
                "resume_id": resume_id,
                "file_name": f"{resume_id}{suffix}",
                "file_type": file_type,
                "source_note": "待填：来源渠道（招聘网站 / 内部人才库 / 校招 / 历史归档）",
                "status": "pending",
            }
        )
        rows.append(row)
    return rows


def build_query_rows(count: int = QUERY_MIN_COUNT, id_prefix: str = "Q") -> list[dict[str, str]]:
    """生成查询-人才集的空骨架（``count`` 行）。

    ``top_k`` 预填 10 —— 因为指标本身就叫 ``Recall@10``（需求 §5 IDX-01），
    留空反而会让人填出 5 / 20 这类与指标不匹配的值。
    """
    if count < QUERY_MIN_COUNT:
        raise ValueError(
            f"查询集规模不足需求下限：需求 §12.1 要求 ≥{QUERY_MIN_COUNT} 条，当前 {count}"
        )

    rows: list[dict[str, str]] = []
    for index in range(count):
        row = {column: "" for column in QUERY_COLUMNS}
        row.update(
            {
                "query_id": f"{id_prefix}-{index + 1:03d}",
                "top_k": "10",
                "status": "pending",
            }
        )
        rows.append(row)
    return rows


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> int:
    """写 CSV（``utf-8-sig``：Excel 双击不乱码）。

    :returns: 写入的数据行数
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    # newline="" 是 csv 模块的硬要求：否则 Windows 上会写出 \r\r\n
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def write_readme(path: Path, resume_count: int, query_count: int, image_ratio: float) -> None:
    """写「给标注人的一页纸」—— 只放必要信息，详细口径指向规范文档。"""
    image_count = round(resume_count * image_ratio)
    content = f"""# 金标准集（N0 交付物）

> 🔴 **本目录是甲方内部资产，含个人信息，禁止提交到任何代码仓库 / 云盘 / 外部工具。**
> 交付乙方前先跑 `goldset.py seal` 把明文联系方式替换为盐化哈希。

## 本批规模

| 项目 | 数量 | 用途 |
| --- | :-: | --- |
| 简历标注集 | {resume_count} 份（其中图片/扫描件 {image_count} 份） | IDX-02 字段级准确率 ≥95% |
| 查询-人才集 | {query_count} 条 | IDX-01 检索 Recall@10 ≥85% |

## 怎么用

1. **先读口径**：`docs/金标准集标注规范.md`（字段怎么填、缺失怎么标、分母怎么算）。**不看口径直接标，200 份标完基本要返工。**
2. 用 Excel / WPS 打开 `{RESUME_CSV_NAME}`，按行填字段列。
   - 字段列**留空 = 还没标**；简历上确实没有该信息请填 `{MISSING_MARKER}`。
   - `status` 从 `pending` → `annotating` → `done`（只有 `done` 的行参与达标统计）。
   - 填完一行就写 `annotator`（姓名）与 `annotated_at`（日期），便于追溯与工作量核算。
3. `{QUERY_CSV_NAME}` 同法填写；`expected_resume_ids` 填**简历集的 `resume_id`**
   （如 `RS-0007`，多条用 `|` 分隔），❌ 不要填数据库 ID —— 标注阶段档案尚未入库。
4. 每完成 50 份跑一次质检：

```bash
cd backend
.venv/Scripts/python.exe scripts/goldset.py validate
```

## 标注工作量（需求 §12.5 估算，供排期参考）

- 简历标注集：**6~8 人天**（约 1.5~2 人天 / 50 份，含逐字段核对）
- 查询-人才集：**2~3 人天**
- 责任方：甲方 HR 专家 + 业务专家（**乙方无业务判断力**，D-11）

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `{RESUME_CSV_NAME}` | 简历标注集（工具生成骨架，人工填写） |
| `{QUERY_CSV_NAME}` | 查询-人才集（工具生成骨架，人工填写） |
| `RESUME_CHECK.md` / `QUERY_CHECK.md` | 质检报告（`goldset.py validate --report` 生成） |
| `sealed/` | `goldset.py seal` 产出的可交付版本（明文联系方式已替换为哈希） |

## 文件类型取值

{chr(10).join(f"- `{key}`：{value}" for key, value in FILE_TYPES.items())}

## 状态取值

{chr(10).join(f"- `{key}`：{value}" for key, value in ANNOTATION_STATUS.items())}

## 字段清单（{len(RESUME_FIELDS)} 项）

{chr(10).join(f"- `{f.column}` {f.label}（{f.group} → {f.table_ref}）" for f in RESUME_FIELDS)}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def run_build(
    out_dir: Path,
    resume_count: int = RESUME_MIN_COUNT,
    query_count: int = QUERY_MIN_COUNT,
    image_ratio: float = DEFAULT_IMAGE_RATIO,
    force: bool = False,
) -> dict[str, Path]:
    """生成模板三件套。

    🔴 **默认不覆盖**：两个 CSV 已存在且未指定 ``force`` 时直接报错返回 ——
    标注是人工资产，覆盖一次可能就是几天的工作量。

    :raises FileExistsError: 目标 CSV 已存在且 ``force=False``
    :raises ValueError: 数量低于需求下限，或比例非法
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    resume_path = out_dir / RESUME_CSV_NAME
    query_path = out_dir / QUERY_CSV_NAME

    existing = [p.name for p in (resume_path, query_path) if p.exists()]
    if existing and not force:
        raise FileExistsError(
            f"目标文件已存在：{', '.join(existing)}。"
            "标注是人工资产，默认不覆盖 —— 确实要重新生成请显式加 `--force`。"
        )

    # 先构造（可能抛 ValueError），再落盘 —— 避免「数量校验失败却已经把头文件写进去」
    resume_rows = build_resume_rows(resume_count, image_ratio)
    query_rows = build_query_rows(query_count)

    resume_written = write_csv(resume_path, RESUME_COLUMNS, resume_rows)
    query_written = write_csv(query_path, QUERY_COLUMNS, query_rows)
    if resume_written != resume_count or query_written != query_count:
        raise RuntimeError(
            f"写入行数与预期不符：简历 {resume_written}/{resume_count}，查询 {query_written}/{query_count}"
        )

    readme_path = out_dir / README_NAME
    write_readme(readme_path, resume_count, query_count, image_ratio)

    return {"resume": resume_path, "query": query_path, "readme": readme_path}
