"""验收自检脚本（CU-26 · 把需求 §12.3 的「客观证据」变成可复算的输出）。

为什么需要它
============
需求 §12.3 的验收原则是「**用客观证据判定，不用主观感受判定**」。
但这条原则有个很常见的漏洞：**证据本身也可以是「人跑的、人念的」** ——
「我本地跑过，全绿」在验收桌上**无法复核**（既看不到原始数字，也无法重跑出同一结果）。

故把「可复核」机械化：本脚本把散落在六处的事实
（pytest junit / 前端构建产物 / 路由表 / `openapi` / Alembic 迁移链 / 测试文件清单）
一次性采集，**逐项给出 PASS/FAIL 与原始数字**，输出可直接贴进验收材料。

与 pytest 的分工（两者不可互相替代）
------------------------------------
* ``tests/`` 是**用例** —— 证明「某个行为是对的」；
* 本脚本是**自检** —— 证明「那些用例确实存在、确实覆盖了验收项、且**没有静默消失**」。

具体差在哪：`pytest` 全绿**不能**证明「有用例被删掉了」（删掉用例后依然全绿），
而本脚本的 S-08 正是拿「磁盘上的测试文件」与「《测试用例与验收脚本》登记的清单」做**双向对账**。

🔴 判绿口径（本机实测，勿改）
----------------------------
**只认 junit 的 `failures` / `errors` / `skipped`**，不认终端摘要（被本机钩子隐藏）、
**也不认退出码**（pytest 结束时清理临时目录会触发安全钩子，使退出码恒为 1，而用例实际全通过）。

用法
----
    python -m scripts.acceptance_check                 # 静态项 S-03 ~ S-09
    python -m scripts.acceptance_check --with-tests     # 追加 S-01 / S-02（跑 pytest，约 13 分钟）

判读：**以 stdout 的 `[FAIL]` 行为准**，不要只看退出码。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from scripts._console import ensure_utf8_stdout

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
ASSETS_DIR = DIST_DIR / "assets"
VIEWS_DIR = FRONTEND_DIR / "src" / "views"
ROUTES_TS = FRONTEND_DIR / "src" / "router" / "routes.ts"
TESTS_DIR = BACKEND_ROOT / "tests"
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
#: 测试资产清单的**事实来源**（CU-26 的设计文档）—— S-08 拿它与磁盘对账
TESTCASE_DOC = PROJECT_ROOT / "docs" / "AI数字化人才平台-测试用例与验收脚本.md"

#: 🔴 需求 §12.3 的三条安全验收专项下限（写死在此**便于复算**，不作为「上限」）
MIN_AC_CASES = 30  # F-1.3 越权拦截 ≥ 30 条
MIN_SCOPE_CASES = 20  # 向量检索权限前置 ≥ 20 条（关闭 B-8）
MIN_MASK_SAMPLES = 20  # 出网脱敏样例集 ≥ 20 条

#: 🔴 B-8 / B-9 曾登记的「9 条**无单元认领**接口」（实际落在下面 6 组路径上）。
#:
#: 🔁 **批次 16 语义反转（2026-09-23，CU-29 交付后）**：
#: 这 6 组路径已由 **CU-29** 补齐，评审文档 §6.1 的 **B-8 / B-9 随之关闭**，
#: 故本项判据由「这些路径**不应存在**」反转为「**必须持续存在**」。
#:
#: 为什么反转后仍然值得留一项：前端（批次 14）**早已按契约实现并调用**它们，
#: 而**删掉任一条不会让任何 pytest 用例变红** —— 只在联调时以 404 的面目出现
#: （`openapi` 少一条路径，前端 `talent.ts` 的调用全部落空）。
#: 于是本项从「文档滞后提醒」变成了**契约回归守卫**。
CLAIMED_API_PATHS: tuple[tuple[str, str], ...] = (
    ("/api/v1/talents", "API-30 档案列表"),
    ("/api/v1/talents/{talent_id}", "API-31/32/33 档案详情 / 编辑 / 归档"),
    ("/api/v1/talents/{talent_id}/profile", "API-34 数字画像"),
    ("/api/v1/talents/export", "API-35 档案导出"),
    ("/api/v1/talent/ask/sessions/{session_id}", "API-64/65 会话历史 / 清空会话"),
    ("/api/v1/talent/mask/verify", "API-66 脱敏自检"),
)

#: 路由表的两个固定哨兵：布局根 `/` 与重定向空路径 `''`（架构 §4.2 的 21 条业务路由之外）
ROUTE_SENTINELS = 2


@dataclass
class CheckResult:
    """单项自检结论。``detail`` 只放**原始数字**，便于人工复算。"""

    number: str
    title: str
    passed: bool | None  # None = SKIP（需 --with-tests / 依赖缺失）
    detail: str


# ============================================================================
# 采集器
# ============================================================================
def _count_files(pattern: str, base: Path) -> int:
    return len(list(base.glob(pattern)))


def _parse_junit(path: Path) -> tuple[int, int, int, int]:
    """返回 ``(tests, failures, errors, skipped)``。"""
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None, f"junit 结构异常：{path}"
    return (
        int(suite.get("tests") or 0),
        int(suite.get("failures") or 0),
        int(suite.get("errors") or 0),
        int(suite.get("skipped") or 0),
    )


def _read_routes_counts() -> tuple[int, int]:
    """返回 ``(name: 条目数, path: 条目数)``。

    ⚠️ 必须锚定**行首缩进**：`routes.ts` 的 `meta` 里也有 `name:` 字样，
    不锚定会把 `meta.name` 一起数进来（这类「多数了一个」最难被发现）。
    """
    text = ROUTES_TS.read_text(encoding="utf-8")
    names = len(re.findall(r"^\s+name: '", text, flags=re.MULTILINE))
    paths = len(re.findall(r"^\s+path: '", text, flags=re.MULTILINE))
    return names, paths


def _count_view_chunks() -> int:
    if not ASSETS_DIR.exists():
        return 0
    return len([p for p in ASSETS_DIR.iterdir() if "View-" in p.name])


def _migration_graph() -> tuple[dict[str, object], list[str], list[str]]:
    """解析 `alembic/versions/*.py` 的 revision / down_revision（AST，不用 alembic 运行时）。

    用 AST 而非 `ScriptDirectory` 的原因：**本脚本要能在没有数据库、没有 alembic
    上下文的情况下跑**（验收机上未必配好 `alembic.ini` 的 URL），而迁移链的
    「单头、无环」属性只取决于这几个字面量。
    """
    revisions: dict[str, object] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        rev: str | None = None
        down: object = None
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            # ⚠️ 必须同时支持两种形态：Alembic autogenerate 产出的是 **AnnAssign**
            # （``revision: str = "0001_basement"``），而手写的常是 `Assign`。
            # 只认 `Assign` 会在这里假失败（本脚本首版即踩此坑）。
            target: ast.expr | None = None
            value_node: ast.expr | None = None
            if isinstance(node, ast.AnnAssign):
                target, value_node = node.target, node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value_node = node.targets[0], node.value
            if value_node is None or not isinstance(target, ast.Name):
                continue
            if target.id not in ("revision", "down_revision"):
                continue
            try:
                value: object = ast.literal_eval(value_node)
            except Exception:  # noqa: BLE001 — 非字面量（罕见）按「无声明」处理
                value = None
            if target.id == "revision":
                rev = value if isinstance(value, str) else None
            else:
                down = value
        assert rev, f"{path.name} 未声明 revision"
        revisions[rev] = down

    referenced: set[str] = set()
    for down in revisions.values():
        if isinstance(down, str):
            referenced.add(down)
        elif isinstance(down, (tuple, list)):
            referenced.update(item for item in down if isinstance(item, str))
    heads = sorted(set(revisions) - referenced)
    dangling = sorted(referenced - set(revisions))
    return revisions, heads, dangling


def _openapi_paths() -> list[str]:
    from app.main import create_app  # 延迟导入：只在本项被检查时才构造应用

    return sorted(create_app().openapi()["paths"].keys())


def _doc_test_files() -> set[str]:
    """从《测试用例与验收脚本》抽出全部被提及的 ``test_xxx.py`` 文件名。"""
    if not TESTCASE_DOC.exists():
        return set()
    return set(re.findall(r"(test_[A-Za-z0-9_]+\.py)", TESTCASE_DOC.read_text(encoding="utf-8")))


# ============================================================================
# 检查项
# ============================================================================
def _run_pytest(junit_path: Path, only: str | None = None) -> tuple[int, int, int, int]:
    args = [sys.executable, "-m", "pytest", f"--junitxml={junit_path}", "-q"]
    if only:
        args.append(only)
    # 退出码不可信（见模块 docstring），故不检查 returncode，只解析 junit
    with open(junit_path.parent / "pytest.log", "w", encoding="utf-8") as log:
        subprocess.run(args, cwd=BACKEND_ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    return _parse_junit(junit_path)


def check_s01(with_tests: bool) -> CheckResult:
    if not with_tests:
        return CheckResult("S-01", "全量回归 junit 全绿", None, "SKIP（加 --with-tests 才跑）")
    junit = Path(tempfile.mkdtemp(prefix="acceptance_")) / "junit.xml"
    tests, failures, errors, skipped = _run_pytest(junit)
    passed = failures == 0 and errors == 0 and skipped == 0
    return CheckResult(
        "S-01",
        "全量回归 junit 全绿",
        passed,
        f"tests={tests} failures={failures} errors={errors} skipped={skipped}（junit: {junit}）",
    )


def check_s02(with_tests: bool) -> CheckResult:
    """结构守卫**单独再跑一次**。

    为什么与 S-01 重复也要留：§7.3 批次 13 的教训是「针对性复跑漏掉结构守卫，
    于是 Router 越层只在全量里现形」。这条把「改 Router / `core` / 新模块文件必带守卫」
    从**纪律**变成**一条命令**（`python -m scripts.acceptance_check --layering-only` 的立足点）。
    """
    if not with_tests:
        return CheckResult("S-02", "结构守卫（test_layering.py）", None, "SKIP（加 --with-tests 才跑）")
    junit = Path(tempfile.mkdtemp(prefix="layering_")) / "junit.xml"
    tests, failures, errors, skipped = _run_pytest(junit, only="tests/test_layering.py")
    passed = failures == 0 and errors == 0 and skipped == 0
    return CheckResult(
        "S-02",
        "结构守卫（test_layering.py）",
        passed,
        f"tests={tests} failures={failures} errors={errors} skipped={skipped}（含 MODULE_FILES 参数化）",
    )


def check_s03() -> CheckResult:
    index = DIST_DIR / "index.html"
    assets = _count_files("*", ASSETS_DIR) if ASSETS_DIR.exists() else 0
    passed = index.exists() and assets > 0
    return CheckResult(
        "S-03",
        "前端构建产物存在",
        passed,
        f"dist/index.html={'存在' if index.exists() else '缺失'} · assets/{assets} 个文件",
    )


def check_s04() -> CheckResult:
    """页面 chunk 数必须 == 页面文件数 × 2（每个 SFC 产出 js + css）。

    这条防的是「某个页面没进构建」这种**静默缺失** —— 页面写好了、路由也配了，
    但构建产物里没有它（多见于动态 import 路径写错），而 `vite build` 依旧成功。
    """
    views = _count_files("**/*.vue", VIEWS_DIR)
    chunks = _count_view_chunks()
    expected = views * 2
    return CheckResult(
        "S-04",
        "页面 chunk 数 = 页面文件数 × 2",
        chunks == expected,
        f"views={views} → 期望 chunk={expected}，实测={chunks}",
    )


def check_s05() -> CheckResult:
    names, paths = _read_routes_counts()
    views = _count_files("**/*.vue", VIEWS_DIR)
    passed = names == views and paths == names + ROUTE_SENTINELS
    return CheckResult(
        "S-05",
        "路由三口径自洽（views = name: = path: − 2）",
        passed,
        f"views={views} · name:={names} · path:={paths}（哨兵 {ROUTE_SENTINELS} 条：`/` 与 `''`）",
    )


def check_s06() -> CheckResult:
    """`openapi` 路径数**只增不减**。

    判据取「≥ 75」而非「== 75」：本项的目的是抓「路由被误删 / 没挂上」，
    而不是禁止新增。基准 75 来自批次 14 实测（评审文档 §7.3 已登记该数字）。
    """
    try:
        paths = _openapi_paths()
    except Exception as exc:  # noqa: BLE001 — 构造应用失败本身就是一项要报的失败
        return CheckResult("S-06", "openapi 路径数 ≥ 75（只增不减）", False, f"构造应用失败：{exc!r}")
    return CheckResult(
        "S-06",
        "openapi 路径数 ≥ 75（只增不减）",
        len(paths) >= 75,
        f"paths={len(paths)}（基准 75，批次 14 实测）",
    )


def check_s07() -> CheckResult:
    revisions, heads, dangling = _migration_graph()
    passed = len(heads) == 1 and not dangling
    return CheckResult(
        "S-07",
        "Alembic 迁移链单头且无断链",
        passed,
        f"迁移={len(revisions)} · heads={len(heads)} · 悬空引用={len(dangling)} · head={heads}",
    )


def check_s08() -> CheckResult:
    """磁盘上的测试文件 ↔ 文档登记清单**双向对账**（本脚本存在的核心理由）。

    单向不够：只查「文档提到的都存在」会漏掉「新写了用例但没登记」；
    只查「磁盘上的都登记了」会漏掉「文档写了个已删除的文件」。故**差集双向为空**才算过。
    """
    on_disk = {p.name for p in TESTS_DIR.glob("test_*.py")}
    in_doc = _doc_test_files()
    if not in_doc:
        return CheckResult("S-08", "测试文件 ↔ 文档清单双向登记", False, f"文档缺失或未登记：{TESTCASE_DOC.name}")
    only_disk = sorted(on_disk - in_doc)
    only_doc = sorted(in_doc - on_disk)
    passed = not only_disk and not only_doc
    detail = f"磁盘={len(on_disk)} · 文档={len(in_doc)}"
    if only_disk:
        detail += f" · 🔴 未登记（磁盘有、文档无）：{only_disk}"
    if only_doc:
        detail += f" · 🔴 幽灵条目（文档有、磁盘无）：{only_doc}"
    return CheckResult("S-08", "测试文件 ↔ 文档清单双向登记", passed, detail)


def check_s09() -> CheckResult:
    """B-8 / B-9 补齐的 9 条接口（6 组路径）**必须持续存在**（契约回归守卫）。

    🔁 原判据「这 6 组路径应当**不存在**」在 **CU-29 补齐后已失效**（见常量注释）：
    若继续沿用，本项会**恒为 FAIL** —— 一个永远红的检查项等于没有检查项
    （团队会习惯性忽略它，连带 S-01~S-08 的红也被一起忽略）。

    反向的失效模式同样真实：这 6 组路径**只在 `openapi` 里存在**才有人用得上，
    而「哪天有人重构 `routers/__init__.py` 时漏掉一行 `include_router`」
    不会让任何 pytest 用例变红，只会让前端在联调时全线 404。故保留本项。
    """
    try:
        present = set(_openapi_paths())
    except Exception as exc:  # noqa: BLE001
        return CheckResult("S-09", "已认领接口持续存在（B-8/B-9）", False, f"构造应用失败：{exc!r}")
    missing = [f"{path}（{label}）" for path, label in CLAIMED_API_PATHS if path not in present]
    passed = not missing
    detail = f"登记 {len(CLAIMED_API_PATHS)} 组 / 9 条 · 当前已存在={len(CLAIMED_API_PATHS) - len(missing)}"
    if missing:
        detail += f" · 🔴 接口缺失（前端将 404，且无用例会红）：{missing}"
    return CheckResult("S-09", "已认领接口持续存在（B-8/B-9）", passed, detail)


# ============================================================================
# 入口
# ============================================================================
def _emit(results: list[CheckResult]) -> int:
    width = max(len(label) for label in ("[SKIP]", "[PASS]", "[FAIL]"))
    print("=" * 78)
    print("CU-26 验收自检 · 需求 §12.3（客观证据，可复算）")
    print("=" * 78)
    for item in results:
        label = {True: "[PASS]", False: "[FAIL]", None: "[SKIP]"}[item.passed]
        print(f"{label:<{width}} {item.number}  {item.title}")
        print(f"{'':<{width}}       {item.detail}")
    counted = [r for r in results if r.passed is not None]
    n_pass = sum(1 for r in counted if r.passed)
    n_fail = len(counted) - n_pass
    n_skip = len(results) - len(counted)
    print("-" * 78)
    print(f"汇总：PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
    if n_fail:
        print("🔴 有未通过项 —— 请按上面的 `[FAIL]` 行逐项处理；**判读以本行为准，不要只看退出码**。")
    return 1 if n_fail else 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="CU-26 验收自检（需求 §12.3）")
    parser.add_argument("--with-tests", action="store_true", help="追加 S-01 / S-02（跑 pytest，约 13 分钟）")
    parser.add_argument("--layering-only", action="store_true", help="只跑结构守卫（§7.3 批次 13 的针对性复跑纪律）")
    args = parser.parse_args(argv)

    if args.layering_only:
        return _emit([check_s02(with_tests=True)])

    results = [
        check_s01(args.with_tests),
        check_s02(args.with_tests),
        check_s03(),
        check_s04(),
        check_s05(),
        check_s06(),
        check_s07(),
        check_s08(),
        check_s09(),
    ]
    return _emit(results)


if __name__ == "__main__":
    raise SystemExit(main())
