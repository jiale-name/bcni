"""工程结构守卫：**「按文件分层」这件事必须可执行地校验**，而不是靠人工 review。

为什么需要这个文件
==================
架构 §3.3 的第一条铁律是「六层架构，依赖只能向下：``routers → services → dao → models``」。
但这条纪律有个特点：**违反它通常不会立刻报错**——
某个 Router 里直接 ``select()`` 一次，功能完全正常、测试也全绿，
直到某天要改一处权限逻辑时发现改了 5 个地方。

同理，「每个模块都要有 models/dao/schemas/services/routers 五件套」这种约定，
靠人记是记不住的（而且它本身就是本项目「按文件落地」的交付要求）。

因此把这两件事写成**可执行的断言**：违反时测试失败，并把「为什么」写在断言消息里。

覆盖
----
1. ``test_no_upward_layer_imports`` —— 不许反向依赖
2. ``test_only_allowed_core_to_service_exception`` —— 白名单必须**逐条列明**且真的是延迟导入
3. ``test_app_never_imports_scripts`` —— ``scripts → app`` 单向（脚本可用业务代码，反之不行）
4. ``test_routers_contain_no_business_logic`` —— Router 不得直接碰 ORM 模型
5. ``test_every_module_has_its_layer_files`` —— 「按文件」：模块与五件套的对应关系
6. ``test_rate_limit_module_is_wired_into_request_chain`` —— CU-09 的接线**真的存在**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import app as app_pkg

BACKEND_ROOT = Path(app_pkg.__file__).resolve().parent.parent
#: 仓库根 —— ``deploy/`` / ``frontend/`` / ``docs/`` 所在层级（架构 §7 目录结构）
PROJECT_ROOT = BACKEND_ROOT.parent
APP_DIR = BACKEND_ROOT / "app"

#: 层序号：**数字大 = 更靠上（更靠近 HTTP）**。依赖只允许 ``src → dst`` 且 ``dst 序号 ≤ src``。
LAYER_ORDER: dict[str, int] = {
    "models": 0,
    "dao": 1,
    "schemas": 1,
    "core": 1,  # 横切关注点：任何层都允许用（它不反向依赖业务）
    # ``agents`` 与 ``services`` 同层（架构 §4.3 的落位）：
    # 允许 agents → dao/models/schemas/core；允许 services → agents（编排用能力）。
    # ❌ 禁止 agents → routers；❌ 禁止 dao/models → agents。
    "agents": 2,
    "services": 2,
    "routers": 3,
}

#: 唯一允许的「向上」引用，且必须满足**两个**条件：① 在函数体内（延迟导入）；
#: ② 有明确注释说明为什么。见 :func:`test_only_allowed_core_to_service_exception`。
#:
#: ``core/security.py`` 的 ``require_perm`` 需要查权限点快照，而该查询在 Service 层。
#: 若在模块顶层导入就会形成 ``core.security → services.auth_service → core.security``
#: 的循环，因此**只能在函数内延迟导入**。
ALLOWED_UPWARD: dict[str, str] = {
    "app/core/security.py": "app.services.auth_service",
}


def _iter_imports(path: Path) -> list[tuple[int, str, bool]]:
    """返回 ``[(行号, 模块名, 是否在导入期执行), ...]``。

    ⚠️ 判定「是否模块级」时**必须排除函数体内的导入**。
    不能图省事写成「``ast.walk`` 顶层语句 + 看 id 在不在集合里」——
    ``ast.walk`` 会把顶层 ``FunctionDef`` 的**整棵子树**都走出来，
    于是函数体里的延迟导入也被标成模块级（本文件早期版本就踩了这个坑，
    导致「白名单必须是延迟导入」这条断言把正确的代码判成违规）。

    类体内的导入仍算模块级（它在 import 时就执行）；函数/方法体内的不算。
    """

    def _collect(node: ast.AST, *, inside_function: bool) -> list[tuple[int, str, bool]]:
        found: list[tuple[int, str, bool]] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 一旦进入函数体，其内部（含嵌套函数）的导入都不在导入期执行
                found.extend(_collect(child, inside_function=True))
                continue
            names: list[str] = []
            if isinstance(child, ast.ImportFrom) and child.module:
                names.append(child.module)
            elif isinstance(child, ast.Import):
                names.extend(alias.name for alias in child.names)
            if names:
                for name in names:
                    found.append((child.lineno, name, not inside_function))
                continue  # 导入语句没有需要下钻的子节点
            found.extend(_collect(child, inside_function=inside_function))
        return found

    return _collect(ast.parse(path.read_text(encoding="utf-8")), inside_function=False)


def _app_targets(module: str) -> str | None:
    """``app.dao.user_dao`` → ``"dao"``；不是 app 内部模块则返回 ``None``。"""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "app" and parts[1] in LAYER_ORDER:
        return parts[1]
    return None


# ============================================================================
# 1~2 · 依赖方向
# ============================================================================
def test_no_upward_layer_imports() -> None:
    """六层依赖只能向下（架构 §3.3 铁律 1）。"""
    violations: list[str] = []
    for layer, level in LAYER_ORDER.items():
        for path in sorted((APP_DIR / layer).glob("*.py")):
            rel = path.relative_to(APP_DIR.parent).as_posix()
            for lineno, module, _ in _iter_imports(path):
                target = _app_targets(module)
                if target is None or target == "core":
                    continue  # core 是横切，不算越层
                if LAYER_ORDER[target] > level and ALLOWED_UPWARD.get(rel) != module:
                    violations.append(f"{rel}:{lineno}  {layer} → {module}")
    assert violations == [], "出现反向依赖（上层被下层导入）：\n  " + "\n  ".join(violations)


def test_only_allowed_core_to_service_exception() -> None:
    """唯一的越层白名单必须**是延迟导入**且**真的存在** —— 防白名单腐烂。

    如果哪天 ``core/security.py`` 改成模块顶层导入 ``auth_service``，
    白名单会掩盖住一个**真实的循环导入风险**（运行时表现为
    ``partially initialized module`` 的诡异 ImportError）。
    因此这里额外验证「该导入出现在函数体内」。
    """
    for rel, module in ALLOWED_UPWARD.items():
        path = BACKEND_ROOT / rel
        hits = [item for item in _iter_imports(path) if item[1] == module]
        assert hits, f"白名单条目已失效（{rel} 不再导入 {module}），请从 ALLOWED_UPWARD 中删除"
        assert not any(module_level for _, _, module_level in hits), (
            f"{rel} 在**模块顶层**导入了 {module} —— 这会造成循环导入，"
            "必须放在函数体内延迟导入"
        )


def test_app_never_imports_scripts() -> None:
    """``scripts → app`` 是**单向**依赖：脚本可以用业务代码，业务代码不能反向用脚本。

    反向依赖的后果很实际：``scripts/seed_dev.py`` 之类的脚本一旦被 app 导入，
    它自身的依赖（如果哪天引入了只该在开发机装的包）就会变成**生产启动的硬依赖**。
    """
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for lineno, module, _ in _iter_imports(path):
            if module == "scripts" or module.startswith("scripts."):
                offenders.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}:{lineno} → {module}")
    assert offenders == [], "app 反向导入了 scripts：\n  " + "\n  ".join(offenders)


# ============================================================================
# 3 · Router 只能是「声明 + 绑定 + 包响应」
# ============================================================================
def test_routers_contain_no_business_logic() -> None:
    """Router 层不得直接使用 ORM 模型或业务 DAO（架构 §3.3）。

    判定方式具体、不靠猜：**router 文件里出现的 ``app.dao.*`` 只允许是 ``session``**
    （那是 ``get_db`` 依赖，本就属于接线），且**不得出现 ``app.models.*``**。
    一旦 Rule 里出现 ``select(DictItem)``，这条会失败。
    """
    allowed_dao = {"app.dao.session"}
    offenders: list[str] = []
    for path in sorted((APP_DIR / "routers").glob("*.py")):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for lineno, module, _ in _iter_imports(path):
            if module.startswith("app.models"):
                offenders.append(f"{rel}:{lineno} 直接导入 ORM 模型 {module}")
            elif module.startswith("app.dao") and module not in allowed_dao:
                offenders.append(f"{rel}:{lineno} 直接导入 DAO {module}")
    assert offenders == [], (
        "Router 越层（业务判定/数据访问必须在 Service）：\n  " + "\n  ".join(offenders)
    )


# ============================================================================
# 4 · 「按文件」：模块 ↔ 五件套的对应关系
# ============================================================================
#: 已交付模块 → 期望存在的文件（**相对 backend/ 的路径**）。
#: 这份表就是本项目「按文件落地」的**清单**，新增模块时必须同步补一行。
MODULE_FILES: dict[str, tuple[str, ...]] = {
    "CU-04/05 认证与角色权限": (
        "app/schemas/auth.py",
        "app/schemas/rbac.py",
        "app/services/auth_service.py",
        "app/services/rbac_service.py",
        "app/routers/auth_router.py",
        "app/routers/rbac_router.py",
        "app/dao/token_dao.py",
        "app/dao/rbac_dao.py",
    ),
    "CU-06 组织与用户": (
        "app/models/tenant.py",
        "app/schemas/org.py",
        "app/schemas/user_admin.py",
        "app/services/org_service.py",
        "app/services/org_scope_service.py",
        "app/services/user_admin_service.py",
        "app/routers/org_router.py",
        "app/routers/user_router.py",
        "app/dao/org_dao.py",
        "app/dao/user_admin_dao.py",
        "app/dao/user_dao.py",
    ),
    "CU-07 数据字典": (
        "app/models/dict.py",
        "app/dao/dict_dao.py",
        "app/schemas/dict.py",
        "app/services/dict_service.py",
        "app/routers/dict_router.py",
        "alembic/versions/0002_dict.py",
    ),
    "CU-08 审计日志": (
        "app/models/audit.py",
        "app/dao/audit_dao.py",
        "app/schemas/audit.py",
        "app/services/audit_service.py",
        "app/routers/audit_router.py",
        "alembic/versions/0003_audit.py",
        "scripts/audit_partition.py",
    ),
    "CU-10 模型网关": (
        "app/models/ai.py",
        "app/core/model_gateway.py",
        "app/core/cost.py",
        "app/dao/ai_dao.py",
        "app/services/ai_service.py",
    ),
    "CU-11 Prompt 版本管理": (
        "app/schemas/ai.py",
        "app/services/prompt_service.py",
    ),
    "CU-12 AI 调用日志与成本看板": (
        "app/services/ai_log_service.py",
    ),
    "CU-13 Agent 骨架与人工介入": (
        "app/models/agent.py",
        "app/dao/agent_dao.py",
        "app/agents/__init__.py",
        "app/agents/graph.py",
        "app/agents/checkpointer.py",
        "app/agents/parse_graph.py",
        "app/agents/registry.py",
        "app/core/threshold.py",
        "app/schemas/agent.py",
        "app/services/agent_service.py",
        "app/routers/ai_router.py",
        "app/routers/agent_router.py",
        "alembic/versions/0004_ai_agent.py",
    ),
    "CU-24 部署": (
        # ⚠️ 只列 ``backend/`` 内的产物：``deploy/`` 位于**仓库根**
        #    （架构 §7 的目录结构：``backend/`` 与 ``deploy/`` 同级），
        #    由下方 ``PROJECT_FILES`` 单独断言 —— 见那里的说明。
        "scripts/audit_partition.py",
    ),
    "CU-14 简历批量导入": (
        "app/models/talent.py",
        "app/dao/import_dao.py",
        "app/schemas/talent.py",
        "app/services/import_service.py",
        "app/routers/import_router.py",
        "alembic/versions/0005_talent.py",
        # 🆕 批次 21：导入账目（逐文件事务提交）的**真实路径**回归 ——
        #    旧用例把 `_process_one_file` monkeypatch 掉了，恰好绕开出问题的那段代码。
        "tests/test_import_accounting.py",
    ),
    "CU-15 字段抽取与归一": (
        "app/services/parse_pipeline.py",
        "app/dao/talent_dao.py",
        "app/agents/parse_graph.py",
        "tests/test_parse_text_extract.py",
    ),
    "CU-16 分层复核工作台": (
        "app/schemas/review.py",
        "app/services/review_service.py",
        "app/routers/review_router.py",
    ),
    "CU-17 受控标签词表": (
        "app/core/tagdict.py",
        "app/dao/tag_dao.py",
        "app/schemas/tag.py",
        "app/services/tag_service.py",
        "app/routers/tag_router.py",
    ),
    "CU-18 向量画像 + Embedding 流水线（抽象层）": (
        "app/retrieval/__init__.py",
        "app/retrieval/provider.py",
        "app/retrieval/milvus_provider.py",
        "app/retrieval/keyword_provider.py",
        "app/retrieval/profile.py",
        "scripts/rebuild_index.py",
    ),
    "CU-19 查重与合并（含回滚）": (
        "app/models/dup_pair.py",
        "app/dao/dup_pair_dao.py",
        "app/core/dup.py",
        "app/schemas/dedup.py",
        "app/services/dedup_service.py",
        "app/routers/dedup_router.py",
        "alembic/versions/0006_dedup.py",
    ),
    "CU-20 语义检索（混合召回 + RRF + Rerank）": (
        "app/retrieval/hybrid.py",
        "app/schemas/search.py",
        "app/services/search_service.py",
        "app/routers/search_router.py",
    ),
    "CU-21 档案 RAG 问答（脱敏回填 + 引用溯源）": (
        "app/core/masking.py",
        "app/services/ask_service.py",
    ),
    "CU-22 变更留痕与个人权利响应": (
        # TBL-30 是新表（上游接口 §2.2 把 API-73/76/80 指向 TBL-22，实际无法承载
        # 请求状态/时限/驳回理由 → 已登记为上游不一致，见评审文档批次 13）
        "app/models/privacy.py",
        "app/core/privacy.py",
        "app/dao/privacy_dao.py",
        "app/schemas/change.py",
        "app/schemas/privacy.py",
        "app/services/change_service.py",
        "app/services/privacy_service.py",
        "app/routers/change_router.py",
        "app/routers/privacy_router.py",
        "alembic/versions/0007_privacy.py",
        "tests/test_change_log.py",
        "tests/test_privacy_rights.py",
    ),
    "TBL-12/13 履历明细数据层": (
        "app/models/talent.py",
        "app/dao/talent_dao.py",
        "alembic/versions/0008_talent_detail.py",
        "tests/test_talent_detail_dao.py",
    ),
    "CU-27/28 N0 与金标准集（脚本层）": (
        "scripts/n0_probe.py",
        "scripts/n0/probes.py",
        "scripts/n0/result.py",
        "scripts/n0/targets.py",
        "scripts/n0/report.py",
        "scripts/goldset.py",
        "scripts/goldset/schema.py",
        "scripts/goldset/build.py",
        "scripts/goldset/validate.py",
        "scripts/goldset/seal.py",
    ),
    "CU-26 测试用例与验收脚本": (
        # 需求 §12.3 的验收承载物三件：
        # ① 越权检索专项（≥20 条，关闭 B-8）② 脱敏样例集专项（≥20 条）
        # ③ 「证据本身是否可信」的自检器（含「测试文件 ↔ 文档清单」双向对账）。
        "scripts/acceptance_check.py",
        "tests/test_search_scope.py",
        "tests/test_masking_samples.py",
    ),
    "CU-29 人才档案主线（API-30~35 / API-64~66，关闭 B-8/B-9）": (
        # §6.1 的 B-8/B-9：这 9 条接口此前**无人认领**、`openapi.json` 中实测不存在，
        # 前端却已按契约实现（`frontend/src/api/talent.ts` 一直挂着「后端未就绪」提示条）。
        # 本单元把它们补齐，故这里登记「契约 / 服务 / 路由 / 用例」四处落点。
        # ⚠️ DAO 未新增文件（复用既有 `talent_dao.py` / `tag_dao.py`，见批次 16 记录）。
        "app/schemas/talent_master.py",
        "app/services/talent_service.py",
        "app/services/mask_verify_service.py",
        "app/routers/talent_router.py",
        "tests/test_talent_master.py",
    ),
    "基座（CU-01~03）": (
        "app/config.py",
        "app/main.py",
        "app/core/snowflake.py",
        "app/core/security.py",
        "app/core/context.py",
        "app/core/middleware.py",
        "app/core/ratelimit.py",
        "app/core/logging.py",
        "app/core/crypto.py",
        "app/core/tenant.py",
        "app/models/base.py",
        "app/models/enums.py",
        "app/dao/session.py",
        "app/dao/redis_client.py",
        "app/exceptions/exception_handlers.py",
        "app/schemas/common.py",
        "alembic/versions/0001_init_basement.py",
    ),
}

#: 🔴 **仓库根**（``backend/`` 的上一级）下的交付物。
#:
#: 为什么单独一张表：架构 §7 的目录结构把 ``deploy/`` 放在**仓库根**
#: （与 ``backend/`` / ``frontend/`` / ``docs/`` 同级），而 ``MODULE_FILES`` 的
#: 所有条目都是**相对 ``backend/``** 解析的。把 ``../deploy/xxx`` 塞进
#: ``MODULE_FILES`` 会让失败信息变成一串 ``../``（"缺文件"到底缺在哪一级看不出来），
#: 于是拆成两张表、两条断言，各自语义单一。
PROJECT_FILES: dict[str, tuple[str, ...]] = {
    "CU-24 部署（仓库根产物）": (
        "deploy/docker-compose.dev.yml",
        "deploy/nginx.conf",
        "deploy/.env.example",
        "deploy/README.md",
        "deploy/.gitignore",
        # 「只追加」第三层保证的建号脚本（库级无 UPDATE/DELETE 权限）
        "deploy/mysql-init/01-audit-account.sh",
        # 分区补足定时任务（评审文档 §7.3 遗留 ① 的落地物）
        "deploy/partition-ensure.sh",
        "deploy/partition-ensure.bat",
        # 「先转储再删」（遗留 ⑥ 的落地物）
        "deploy/archive-partitions.sh",
    ),
}


@pytest.mark.parametrize("module", sorted(MODULE_FILES))
def test_every_module_has_its_layer_files(module: str) -> None:
    """每个已交付模块的文件必须**都在预期路径上**（防「代码写了但放错地方」）。

    这条同时是「有没有游离文件」的反向检查：若某模块的文件被挪走或改名，
    它在这里失败，而不是等到某个调用方 ImportError 才发现。
    """
    missing = [rel for rel in MODULE_FILES[module] if not (BACKEND_ROOT / rel).exists()]
    assert missing == [], f"{module} 缺少文件（或路径变了）：{missing}"


@pytest.mark.parametrize("module", sorted(PROJECT_FILES))
def test_project_level_artifacts_exist(module: str) -> None:
    """仓库根交付物（``deploy/`` 等）必须存在。

    ⚠️ 它断言的是**存在性**，不是内容 —— 但这两条恰好能挡住最常见的两种退化：
    ① 部署产物只写在文档里、目录是空的；② 有人重构目录时把 ``deploy/`` 挪进了
    ``backend/``（于是所有部署命令、README、cron 路径一起失效，而本地开发毫无感觉）。
    """
    missing = [rel for rel in PROJECT_FILES[module] if not (PROJECT_ROOT / rel).exists()]
    assert missing == [], f"{module} 缺少文件（或路径变了）：{missing}"


def test_no_stray_python_files_outside_known_dirs() -> None:
    """``backend/`` 根目录下不得出现散落的 ``.py``（除 ``alembic.ini`` 引用的 env 之外）。

    「按文件落地」的反面就是「随手在根目录建个 ``utils.py``」——
    那种文件不属于任何层，谁都能 import 它，于是分层名存实亡。
    """
    allowed_root_files = {"alembic/env.py"}
    stray: list[str] = []
    for path in sorted(BACKEND_ROOT.glob("*.py")):
        if path.name in {"conftest.py"}:  # pytest 根级 conftest 是合法的
            continue
        stray.append(path.name)
    assert stray == [], f"backend/ 根目录出现散落脚本：{stray}"
    for rel in allowed_root_files:  # 白名单条目必须真的存在（防腐烂）
        assert (BACKEND_ROOT / rel).exists()


def test_scripts_is_a_regular_package() -> None:
    """``scripts/`` 必须是常规包（有 ``__init__.py``），否则 ``python -m scripts.xxx`` 行为不一致。

    ⚠️ 这不是形式主义：没有 ``__init__.py`` 时，``python -m scripts.n0_probe``
    在某些 ``sys.path`` 配置下会**以脚本方式**执行，导致同一份代码出现
    「两次导入、两份模块级状态」—— 本项目的雪花 ID 生成器与日志配置都依赖单例。
    """
    for rel in ("scripts/__init__.py", "scripts/n0/__init__.py", "scripts/goldset/__init__.py"):
        assert (BACKEND_ROOT / rel).exists(), f"缺少 {rel}"


# ============================================================================
# 5 · CU-09 的接线是**真的**（不是「模块写好了但没挂上」）
# ============================================================================
def test_rate_limit_module_is_wired_into_request_chain() -> None:
    """``core/ratelimit.py`` 必须真的被中间件调用（CU-09 的验收点）。

    这条防的是一种很隐蔽的「假完成」：限流模块写好了、单测也过了，
    但**中间件没挂到应用上** —— 于是线上完全没有限流，而所有测试仍然全绿
    （因为没有任何用例从 HTTP 层触发过它）。
    """
    from app.core import middleware

    source = (APP_DIR / "core" / "middleware.py").read_text(encoding="utf-8")
    assert "check_rate_limit" in source
    assert "http_rate_key" in source
    assert hasattr(middleware, "RateLimitMiddleware")

    from app.main import create_app

    app = create_app()
    # Starlette 的中间件栈：``app.user_middleware`` 是我们 add 进去的那些
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "RateLimitMiddleware" in names, f"限流中间件没挂上：{names}"
    # 顺序：TraceId 必须在 RateLimit **外侧**，否则 429 响应里没有 trace_id
    assert names.index("TraceIdMiddleware") < names.index("RateLimitMiddleware")
    # CORS 最外层：否则 429 响应不带 CORS 头，前端只能看到一个被浏览器拦掉的失败
    assert names.index("CORSMiddleware") < names.index("TraceIdMiddleware")
    assert "RequestMetaMiddleware" in names, "审计的 ip/ua 采集中间件没挂上"
