"""金标准集的交付前脱敏（``seal``）：明文联系方式 → 盐化哈希（CU-28）。

为什么需要这一步
----------------
标注人填手机号 / 邮箱时用**明文**最自然（对着简历抄），但标注集一旦离开甲方
内网、发给乙方做 N6 评估，明文 PII 就构成了**一次不必要的数据出境**。
而评估本身**根本不需要明文** —— IDX-02 比对联系方式时，只需把系统抽取的结果
与真值做**同一个哈希**，比哈希值相不相等即可（这正是 ``core.crypto.hash_contact`` 的用途）。

所以流程是：**甲方本地明文标注 → `seal` 转哈希 → 交付哈希版给乙方评估**。

🔴 三条约束
----------
1. **盐必须与入库一致** —— 复用 ``core.crypto.hash_contact``（而不是另写一套），
   否则乙方算出的哈希与甲方的不等，评估结果全错。
2. **盐不能泄露在交付物里** —— ``seal`` 只输出哈希列，❌ 不输出盐、不输出明文；
   乙方侧评估需要的盐由部署配置提供（是密钥，不是数据）。
3. **``缺失`` 标记原样保留** —— 它是「真值不存在」的语义标记，❌ 不参与哈希
   （否则所有缺失会被映射成同一个哈希值，反而看起来「都填了」）。
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.core.crypto import ContactHashSaltError, hash_contact
from scripts.goldset.build import RESUME_CSV_NAME, write_csv
from scripts.goldset.schema import MISSING_MARKER, RESUME_COLUMNS

__all__ = ["SEALED_CSV_NAME", "SEALED_DIR_NAME", "HASHED_COLUMNS", "seal_resume_rows", "run_seal"]

SEALED_DIR_NAME = "sealed"
SEALED_CSV_NAME = "resume_annotations_sealed.csv"

#: 列改名映射：明文列 → 哈希列（与 TBL-11 的列名一致）
HASHED_COLUMNS: dict[str, tuple[str, str]] = {
    # 明数列 → (输出列名, hash_contact 的 kind)
    "phone": ("phone_hash", "phone"),
    "email": ("email_hash", "email"),
}


def seal_resume_rows(rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]], int, list[str]]:
    """把明文联系方式列替换为哈希列。

    :returns: ``(输出列名, 输出行, 已哈希格子数, 异常明细)``
    :raises ContactHashSaltError: ``CONTACT_HASH_SALT`` 未配置 —— **不静默跳过**，
        否则会产出一份「看起来已脱敏、实际仍是明文」的交付版本，这是最危险的结果。
    """
    columns: list[str] = []
    for column in RESUME_COLUMNS:
        columns.append(HASHED_COLUMNS.get(column, (column, ""))[0])

    sealed: list[dict[str, str]] = []
    hashed = 0
    problems: list[str] = []

    for row in rows:
        out = {HASHED_COLUMNS.get(c, (c, ""))[0]: row.get(c, "") for c in RESUME_COLUMNS}
        for source_column, (_, kind) in HASHED_COLUMNS.items():
            value = row.get(source_column, "").strip()
            if not value or value == MISSING_MARKER:
                continue  # 空 → 保持空；缺失标记 → 原样保留语义
            try:
                digest = hash_contact(value, kind)  # type: ignore[arg-type]
            except ContactHashSaltError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单格异常不该毁掉整批
                problems.append(f"{row.get('resume_id', '?')}.{source_column}: {type(exc).__name__}: {exc}")
                continue
            out[HASHED_COLUMNS[source_column][0]] = digest or ""
            hashed += 1
        sealed.append(out)

    return columns, sealed, hashed, problems


def run_seal(goldset_dir: Path, out_dir: Path | None = None) -> dict[str, object]:
    """执行脱敏并写出交付版本。

    :raises FileNotFoundError: 源标注文件不存在
    :raises ContactHashSaltError: 盐未配置
    """
    source = goldset_dir / RESUME_CSV_NAME
    if not source.exists():
        raise FileNotFoundError(f"找不到标注文件：{source}")

    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]

    columns, sealed_rows, hashed, problems = seal_resume_rows(rows)

    target_dir = out_dir or (goldset_dir / SEALED_DIR_NAME)
    target = target_dir / SEALED_CSV_NAME
    write_csv(target, columns, sealed_rows)

    return {
        "source": source,
        "target": target,
        "rows": len(sealed_rows),
        "hashed_cells": hashed,
        "problems": problems,
    }
