"""只读探针：核实「档案列表默认口径」（批次 23 反转后的验收点）。

用户报障原文：「导入很多简历成功了，但是前端只显示一个人」。
本探针用**前端同款 HTTP 请求**打 `GET /talents`，证明默认口径已含「待复核(0) + 有效(1)」。

只读：不发 POST / 不写库 / 不改任何状态。
运行：.venv/Scripts/python.exe scripts/_probe_talent_list_scope.py
"""

from __future__ import annotations

from collections import Counter

import httpx

BASE = "http://127.0.0.1:8002/api/v1"
USER, PWD = "admin", "Admin@12345"

# trust_env=False：绕开本机 HTTP 代理（否则可能被伪造成 502，见 PITFALLS）
client = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)


def call(method: str, path: str, **kw):
    resp = client.request(method, path, **kw)
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"!! {method} {path} -> HTTP {resp.status_code} code={body.get('code')} {body.get('message')}")
    return body["data"]


def fetch(params: dict) -> tuple[int, Counter]:
    """返回 (命中总数, data_status 分布)。"""
    env = call("GET", "/talents", params=params)
    rows = env.get("list", []) if isinstance(env, dict) else env
    total = env.get("total") if isinstance(env, dict) else len(rows)
    dist = Counter(r.get("data_status") for r in rows)
    return int(total), dist


def main() -> int:
    token = call("POST", "/auth/login", json={"username": USER, "password": PWD})["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    print(f"✅ 登录成功（admin）token={token[:20]}…\n")

    base = {"page": 1, "per_page": 100}
    total_default, dist_default = fetch(base)
    print(f"① GET /talents                （默认口径，前端首屏）  total={total_default}  status分布={dict(dist_default)}")

    total_pending, dist_pending = fetch({**base, "filters.data_status": 0})
    print(f"② GET /talents?data_status=0  （待复核）              total={total_pending}  status分布={dict(dist_pending)}")

    total_valid, dist_valid = fetch({**base, "filters.data_status": 1})
    print(f"③ GET /talents?data_status=1  （有效）                total={total_valid}  status分布={dict(dist_valid)}")

    print("\n=== 判据（批次 23）===")
    ok_aligned = total_valid == dist_default.get(1, 0) and total_pending == dist_default.get(0, 0)
    print(f"· 默认 = 有效 + 待复核 ？ {total_default} == {total_valid} + {total_pending} -> "
          f"{'✅' if ok_aligned else '❌'}")
    print(f"· 默认结果里含待复核记录？ {dict(dist_default)} -> "
          f"{'✅' if dist_default.get(0, 0) > 0 else '⚠️ 当前无待复核记录（导入后应有）'}")
    print(f"· 默认结果里有非 0/1 状态混入？ "
          f"{'❌ ' + str([s for s in dist_default if s not in (0, 1)]) if [s for s in dist_default if s not in (0, 1)] else '✅ 无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
