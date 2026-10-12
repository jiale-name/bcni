"""端到端联调脚本（**前端同款 HTTP 链路**）：登录 → 任务列表 → 重试 → 轮询 → 档案列表。

只调官方接口，不直接写库；DB 侧由命令行核对。
运行：.venv/Scripts/python.exe scripts/_e2e_import_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8002/api/v1"
USER, PWD = "admin", "Admin@12345"

# ``trust_env=False``：绕开本机 HTTP 代理（否则可能被伪造成 502，见 PITFALLS）
client = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)


def call(method: str, path: str, **kw):
    resp = client.request(method, path, **kw)
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"!! {method} {path} -> HTTP {resp.status_code} code={body.get('code')} {body.get('message')}")
    return body["data"]


def main() -> int:
    token = call("POST", "/auth/login", json={"username": USER, "password": PWD})["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    print(f"✅ 登录成功（前端同款接口 /auth/login）token={token[:20]}…")

    tasks = call("GET", "/talent/import", params={"page": 1, "per_page": 20})
    rows = tasks["list"] if isinstance(tasks, dict) and "list" in tasks else tasks
    print(f"\n✅ 任务列表 /talent/import：共 {len(rows)} 条（前端任务页看到的就是这个）")
    targets = []
    for t in rows:
        print(f"   {t['task_no']}  status={t['status']} status_name={t.get('status_name')} "
              f"total={t['total_count']} 成功={t['success_count']} 失败={t['fail_count']}")
        if str(t["task_no"]).startswith("IMP20260924") and int(t["task_no"][-3:]) <= 4:
            targets.append(t["task_id"])

    print(f"\n▶ 对 {len(targets)} 张 2026-09-24 失败任务调用「重试」（API-43）")
    for tid in targets:
        try:
            out = call("POST", f"/talent/import/{tid}/retry")
            print(f"   retry task_id={tid} -> retryable_count={out['retryable_count']} {out['message'] or ''}")
        except SystemExit as exc:
            print(f"   retry task_id={tid} -> {exc}")

    # ---- 轮询直到终态（后台线程解析）----
    print("\n▶ 轮询任务终态（PARSING=1 时继续等）")
    for _ in range(60):
        pending = []
        for tid in targets:
            detail = call("GET", f"/talent/import/{tid}")
            if int(detail["status"]) in (0, 1):
                pending.append(tid)
        if not pending:
            break
        time.sleep(2)
    else:
        print("   !! 超时仍有任务未到终态:", pending)

    print("\n✅ 任务终态与文件明细（前端「任务详情」页口径）")
    for tid in targets:
        detail = call("GET", f"/talent/import/{tid}")
        print(f"   {detail['task_no']}  status={detail['status']} 成功={detail['success_count']} "
              f"失败={detail['fail_count']} 完成率={detail.get('complete_rate')}")
        files = call("GET", f"/talent/import/{tid}/files", params={"page": 1, "per_page": 20})
        frows = files["list"] if isinstance(files, dict) and "list" in files else files
        for f in frows:
            print(f"      - {f['file_name']}  parse_status={f['parse_status']} "
                  f"原因={f.get('parse_error')} talent_id={f.get('talent_id')}")

    def envelope(method: str, path: str, **kw) -> dict:
        return client.request(method, path, **kw).json()

    env = envelope("GET", "/talents", params={"page": 1, "per_page": 20})
    trows = env["data"] or []
    print(f"\n✅ 档案列表 /talents（**默认口径 = 仅 `1 有效`**）：data={len(trows)} 条  page={env.get('page')}")
    for t in trows:
        print(f"   {t.get('name')}  学历={t.get('education_level')}  "
              f"状态={t.get('data_status_name')}  创建={t.get('created_at')}")

    # 新导入的档案落库状态是「0 待复核」 —— 必须能被显式查到（仅 ADM/HR 可用该筛选）
    env0 = envelope("GET", "/talents", params={"page": 1, "per_page": 20, "filters.data_status": 0})
    prows = env0["data"] or []
    print(f"\n✅ 档案列表 /talents?filters.data_status=0（待复核）：data={len(prows)} 条  page={env0.get('page')}")
    for t in prows:
        print(f"   {t.get('name')}  学历={t.get('education_level')}  "
              f"状态={t.get('data_status_name')}  创建={t.get('created_at')}")

    envr = envelope("GET", "/talent/review", params={"page": 1, "per_page": 20})
    qrows = envr["data"] or []
    print(f"\n✅ 复核工作台 /talent/review：队列 {len(qrows)} 条  meta={envr.get('meta')}")
    for item in qrows[:6]:
        print(f"      - {item.get('talent_name')} · {item.get('field_name')} = "
              f"{item.get('parsed_value')!r} conf={item.get('confidence')} "
              f"impact={item.get('impact_level')}")

    # ---- 可选：把**一份**档案走完「复核确认 → 转有效 → 出现在默认列表」 ----
    if "--confirm-one" in sys.argv:
        by_talent: dict[str, list[str]] = {}
        for item in qrows:
            by_talent.setdefault(str(item.get("talent_name")), []).append(str(item["field_id"]))
        # 取**待复核字段最少**的那一份（写动最小，其余留给人在界面上试）
        pick = min(by_talent, key=lambda n: len(by_talent[n])) if by_talent else None
        ids = by_talent.get(pick or "", [])
        print(f"\n▶ 对「{pick}」的 {len(ids)} 个待复核字段调用 /talent/review/confirm（ACCEPT_AI）")
        out = call(
            "POST",
            "/talent/review/confirm",
            json={"items": [{"field_id": fid, "action": "ACCEPT_AI"} for fid in ids]},
        )
        print(f"   confirmed={out.get('confirmed_count')} conflicted={len(out.get('conflicted') or [])}")

        env1 = envelope("GET", "/talents", params={"page": 1, "per_page": 20})
        rows1 = env1["data"] or []
        print(f"\n✅ 档案列表 /talents（默认口径）：data={len(rows1)} 条")
        for t in rows1:
            print(f"   {t.get('name')}  状态={t.get('data_status_name')}  学历={t.get('education_level')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
