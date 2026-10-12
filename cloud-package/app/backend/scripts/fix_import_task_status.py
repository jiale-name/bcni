"""重算 import_task 的 status / success_count / fail_count（一次性数据修复）。

背景（2026-09-23「已完成但 0/N」排查）：
    批次 20 之前，``ImportService.process_task`` 在主会话（REPEATABLE-READ 快照）
    里 ``count_by_status``，读到的是循环前的过期状态 → 文件全失败却被判
    ``COMPLETED``，``success_count / fail_count`` 落 0/0。
    代码已修复（改用全新会话计数），但**修复前**创建的任务行是脏数据。

本脚本按 ``import_file.parse_status`` 的真实分布重算任务终态（与
``process_task`` 末尾同一套判定规则），**只走 ORM，不做手工 SQL 改库**（铁律 7）：

    final_fail == 0                  → COMPLETED(2)
    final_fail > 0 and final_success > 0 → PARTIAL_FAILED(3)
    final_fail > 0 and final_success == 0 → FAILED(4)

用法（backend/ 下）：
    .venv/Scripts/python.exe scripts/fix_import_task_status.py            # 试算（不写库）
    .venv/Scripts/python.exe scripts/fix_import_task_status.py --apply    # 实际写库

幂等：重复执行，第二次全部输出「已一致」。
"""

from __future__ import annotations

import sys
from datetime import datetime

from app.dao.session import SessionLocal
from app.models.enums import ImportFileParseStatus, ImportTaskStatus
from app.models.talent import ImportFile, ImportTask


def recompute(task: ImportTask, counts: dict[int, int]) -> tuple[int, int, int]:
    """返回 (success, fail, final_status)。判定规则与 process_task 末尾一致。"""
    success = counts.get(int(ImportFileParseStatus.SUCCESS), 0)
    fail = counts.get(int(ImportFileParseStatus.FAILED), 0)
    if fail == 0:
        final = int(ImportTaskStatus.COMPLETED)
    elif success > 0:
        final = int(ImportTaskStatus.PARTIAL_FAILED)
    else:
        final = int(ImportTaskStatus.FAILED)
    return success, fail, final


def main() -> int:
    apply = "--apply" in sys.argv
    fixed = 0
    with SessionLocal() as db:
        tasks = db.query(ImportTask).filter(ImportTask.is_deleted == 0).all()
        for task in tasks:
            rows = (
                db.query(ImportFile.parse_status, ImportFile.id)
                .filter(
                    ImportFile.tenant_id == task.tenant_id,
                    ImportFile.task_id == task.id,
                    ImportFile.is_deleted == 0,
                )
                .all()
            )
            counts: dict[int, int] = {}
            for status, _id in rows:
                counts[status] = counts.get(status, 0) + 1
            success, fail, final = recompute(task, counts)
            drift = (
                int(task.status) != final
                or int(task.success_count or 0) != success
                or int(task.fail_count or 0) != fail
            )
            flag = "需修复" if drift else "已一致"
            print(
                f"{task.task_no:<16} status={task.status}->{final} "
                f"success={task.success_count}->{success} fail={task.fail_count}->{fail}  [{flag}]"
            )
            if drift:
                fixed += 1
                if apply:
                    task.status = final
                    task.success_count = success
                    task.fail_count = fail
                    if task.finished_at is None:
                        task.finished_at = datetime.now()
        if apply:
            db.commit()
    print(f"\n{'已写入' if apply else '试算结束'}：共 {fixed} 条任务{'已修复' if apply else '需修复'}"
          + ("" if apply else "（加 --apply 实际写库）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
