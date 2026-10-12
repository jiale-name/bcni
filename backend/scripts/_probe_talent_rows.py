"""只读核查：档案是否真的落库（**不经过 shell 管道**，避免 grep 把二进制输出吃掉）。

运行：.venv/Scripts/python.exe scripts/_probe_talent_rows.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.dao.session import SessionLocal  # noqa: E402

TENANT = 360790567225004032


def main() -> int:
    with SessionLocal() as s:
        print("== talent_base 全库计数 ==")
        print(s.execute(sa.text("SELECT COUNT(*) FROM talent_base")).scalar())

        print("\n== 本租户最新档案（含 data_status / parse_task_id）==")
        rows = s.execute(
            sa.text(
                "SELECT id, tenant_id, name, education_level, school, work_years, "
                "data_status, parse_task_id, is_deleted, created_at "
                "FROM talent_base WHERE tenant_id=:t ORDER BY id DESC LIMIT 10"
            ),
            {"t": TENANT},
        ).all()
        for r in rows:
            print("  ", tuple(r))

        print("\n== 全库最新 6 条档案所属租户 ==")
        for r in s.execute(
            sa.text("SELECT id, tenant_id, name, data_status FROM talent_base ORDER BY id DESC LIMIT 6")
        ).all():
            print("  ", tuple(r))

        print("\n== 文件行上的 talent_id ==")
        for r in s.execute(
            sa.text(
                "SELECT id, file_name, parse_status, talent_id FROM import_file "
                "WHERE tenant_id=:t ORDER BY id DESC LIMIT 5"
            ),
            {"t": TENANT},
        ).all():
            print("  ", tuple(r))

        print("\n== talent_parse_field 计数（本租户）==")
        print(
            "  ",
            s.execute(
                sa.text("SELECT COUNT(*) FROM talent_parse_field WHERE tenant_id=:t"), {"t": TENANT}
            ).scalar(),
        )

        print("\n== 变更留痕（本租户）==")
        for r in s.execute(
            sa.text(
                "SELECT id, talent_id, change_source, field_name, after_value, task_id "
                "FROM talent_change_log WHERE tenant_id=:t ORDER BY id DESC LIMIT 6"
            ),
            {"t": TENANT},
        ).all():
            print("  ", tuple(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
