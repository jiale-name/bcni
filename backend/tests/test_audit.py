"""CU-08 · 审计日志（TBL-28，API-28/29）· CU-09 · 限流熔断（42901）。

分层（沿用 CU-07 已确立的口径）
------------------------------
- **L1 契约层**（不依赖 MySQL / Redis，任何机器都能跑）：
  「只追加」的**结构**保证（DAO 无 update/delete、模型无 ``is_deleted``/``updated_at``、
  迁移含分区与复合主键）、动作词表、筛选参数校验、脱敏、CSV 注入防护、
  限流中间件与路径归一化、OpenAPI 登记、分区月份算术。
- **L2 集成层**（缺依赖时 skip 而非 fail）：
  审计**真的落库**（登录成功/失败、字典变更、导出自身留痕）、改前/改后快照、
  分页与筛选、租户隔离、矩阵权限制、导出超限 40002。

🔴 每个「拒绝」用例都配一条**反向对照**（应放行时确实放行）——
   否则一个「永远抛错」的实现也能让全部用例通过。

覆盖的验收编号（延续评审文档 §7.4 的 ``AC-nn`` 体系）
----------------------------------------------------
| 编号 | 断言 | 位置 |
| --- | --- | --- |
| **AC-17 补** | 无 ``audit:view`` 调 API-28 → 40301 | ``test_api_28_without_permission_is_40301`` |
| **AC-17 补** | 有 ``audit:view`` 但无 ``audit:export`` 调 API-29 → 40301 | ``test_api_29_needs_separate_permission`` |
| **AC-36 补（审计）** | 审计查询**不套行级数据域**（``data_scope=4`` 仍可查，矩阵给 ✓） | ``test_stats_only_role_can_read_audit`` |
| CR-22 补 | 跨租户审计不可见 | ``test_audit_is_tenant_isolated`` |
| F-1.5 | 登录失败/成功/配置变更/导出均留痕 | ``test_failed_login_writes_audit`` 等 |
| F-1.6 | 超限 → **42901** 且带 ``Retry-After`` | ``test_rate_limit_returns_42901_with_headers`` |
| 红线 | ``password`` / ``token`` 永不入审计 | ``test_audit_never_stores_password`` |

⚠️ 审计表**只追加**，测试写入的记录无法（也不应该）被清理 —— 那是「只追加」的语义。
因此所有断言都限定在**本用例自建的租户**内（租户码含随机后缀），不会互相干扰。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

import app as app_pkg
from app.config import settings
from app.core.context import get_client_ip, get_user_agent
from app.core.middleware import RequestMetaMiddleware, _pick_ip, normalize_rate_path
from app.core.security import SecurityUtils
from app.exceptions import ErrorCode, build_error_response
from app.models.audit import AuditLog
from app.models.dict import DictItem, DictType
from app.models.enums import RoleDataScope
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.audit import (
    MAX_EXPORT_ROWS,
    MAX_RANGE_DAYS,
    AuditAction,
    AuditExportRequest,
    AuditFilterRequest,
    AuditLogOut,
    AuditTarget,
)
from app.services.audit_service import AuditService, _csv_safe, _dump, _render_csv, _sanitize
from tests.conftest import db_available

PWD = "Admin@123456"

#: 本文件用到的权限码（与种子里的命名一致）
VIEW_PERM = "audit:view"
EXPORT_PERM = "audit:export"

#: 一个不存在的对象 ID（跨租户 / 不存在场景共用）
_UNKNOWN_ID = "7999999999999999999"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _adm(env: dict) -> dict[str, str]:
    return _h(env["tokens"]["adm"])


# ============================================================================
# L1 · 「只追加」是**结构**保证，不是口头约定
# ============================================================================
def test_audit_dao_exports_only_insert_and_select() -> None:
    """🔴 ``audit_dao`` 不得存在任何「改 / 删」函数。

    这是「只追加」三层保证里**最可靠**的一层：没有函数可以调，
    远强于「有函数但请别调」。若将来有人顺手加了 ``update_log``，
    这条测试会立刻失败，逼他走评审（正确做法是**再追加一条纠正记录**）。
    """
    from app.dao import audit_dao

    forbidden = ("update", "delete", "remove", "drop", "purge", "soft", "modify", "edit")
    offenders = [
        name
        for name, obj in vars(audit_dao).items()
        if callable(obj)
        and not name.startswith("__")
        and any(f in name.lower() for f in forbidden)
    ]
    assert offenders == [], f"audit_dao 出现了改/删函数：{offenders}（审计表只可追加）"
    assert set(audit_dao.__all__) == {"count_logs", "insert_log", "list_logs"}


def test_audit_dao_functions_never_emit_update_or_delete_sql() -> None:
    """上面那条按**名字**查；这条按**实际 SQL 类型**查 —— 防「改了名但仍是 UPDATE」。

    只对 ``audit_dao`` 的函数逐个体检（不去静态分析 SQLAlchemy 表达式树，
    那是另一个量级的工作量）；这里用最直接的方式：检查函数体内是否出现
    ``update(`` / ``delete(`` 调用。
    """
    import inspect as py_inspect

    from app.dao import audit_dao

    offenders: list[str] = []
    for name in audit_dao.__all__:
        source = py_inspect.getsource(getattr(audit_dao, name))
        if "update(" in source or "delete(" in source or ".delete()" in source:
            offenders.append(name)
    assert offenders == [], f"这些 DAO 函数里出现了 UPDATE/DELETE 调用：{offenders}"


def test_audit_model_has_no_soft_delete_or_updated_at() -> None:
    """模型层不含 ``is_deleted`` / ``updated_at``：「误更新 / 误软删」在 SQL 层无从下手。"""
    cols = set(AuditLog.__table__.c.keys())
    assert "is_deleted" not in cols
    assert "updated_at" not in cols
    assert "deleted_at" not in cols
    assert {"before_json", "after_json"} <= cols


def test_audit_model_primary_key_is_composite() -> None:
    """🔴 复合主键 ``(id, created_at)`` —— 这不是设计选择，是**分区约束的必然结果**。

    MySQL 要求 ``PARTITION BY`` 引用的列出现在**每一个**唯一键中。
    本表按 ``TO_DAYS(created_at)`` 分区，故 ``created_at`` 必须进主键（数据库 §4.7 同）。
    这条测试的意义是在**改代码的当下**就给出「为什么不能简化成单列主键」的答案。
    """
    pk = [c.name for c in AuditLog.__table__.primary_key.columns]
    assert pk == ["id", "created_at"]


def test_migration_declares_partition_clause() -> None:
    """🔴 分区语句的**唯一事实来源是迁移文件**（SQLAlchemy 表达不了命名分区列表）。

    没有这条测试的后果很隐蔽：用 ``--autogenerate`` 重建迁移后，
    表会变成**非分区表** —— 功能完全正常、不报任何错，
    只是 ``DROP PARTITION`` 清理历史的能力悄悄消失了（等发现时表已上亿行）。
    """
    backend_root = Path(app_pkg.__file__).resolve().parent.parent
    source = (backend_root / "alembic" / "versions" / "0003_audit.py").read_text(encoding="utf-8")
    assert "PARTITION BY RANGE" in source
    assert "TO_DAYS(`created_at`)" in source
    assert "MAXVALUE" in source
    assert "PARTITION p" in source, "分区名须为 pYYYYMM 形态（脚本按此解析，见下文）"


def test_migration_chain_is_linear_from_0002_dict() -> None:
    """迁移链必须是 ``0001 → 0002_dict → 0003_audit`` 单链（不能出现两个 head）。"""
    backend_root = Path(app_pkg.__file__).resolve().parent.parent
    source = (backend_root / "alembic" / "versions" / "0003_audit.py").read_text(encoding="utf-8")
    assert 'revision: str = "0003_audit"' in source
    assert 'down_revision: str | None = "0002_dict"' in source


# ============================================================================
# L1 · 动作词表（写入方与筛选方共用同一份）
# ============================================================================
def test_action_labels_cover_all_declared_actions() -> None:
    """``LABELS`` 必须覆盖 ``ALL`` —— 少一个标签，前端就会显示一个动作码给人看。"""
    missing = [code for code in AuditAction.ALL if code not in AuditAction.LABELS]
    assert missing == [], f"这些 action 没有中文标签：{missing}"
    assert AuditAction.ALL, "词表不能为空"
    # 词表内不得有重复（ALL 由 LABELS 派生，这条防的是「常量值手写重复」）
    assert len(set(AuditAction.ALL)) == len(AuditAction.ALL)


def test_label_of_unknown_action_returns_none_instead_of_raising() -> None:
    """🔴 未知取值必须**降级**而不是抛错 —— 否则一条历史脏数据能让整个列表接口 500。

    审计表要能读「十年前的数据」，而词表必然随版本演进（旧取值会被删掉）。
    读取侧对未知值的行为只能是「原样返回 + 标签为 ``None``」。
    """
    assert AuditAction.label_of("action_from_year_2019") is None
    assert AuditTarget.label_of("target_from_future") is None
    assert AuditAction.label_of(None) is None
    assert AuditAction.label_of(AuditAction.LOGIN) == "登录"


def test_audit_actions_used_by_services_are_all_in_vocabulary() -> None:
    """各 Service 实际写入的动作码必须都在词表内（防「写入方写了词表外的值」）。

    这类漂移的症状很讨厌：日志里**确实有**记录，但按 ``action`` 筛不出来
    （筛选侧受 ``Literal`` 校验，只能传词表内的值）。
    """
    used = {
        AuditAction.LOGIN,
        AuditAction.LOGOUT,
        AuditAction.CHANGE_PASSWORD,
        AuditAction.DICT_ITEM_CREATE,
        AuditAction.DICT_ITEM_UPDATE,
        AuditAction.DICT_ITEM_DISABLE,
        AuditAction.DEPT_CREATE,
        AuditAction.DEPT_UPDATE,
        AuditAction.DEPT_DELETE,
        AuditAction.POSITION_CREATE,
        AuditAction.USER_CREATE,
        AuditAction.USER_UPDATE,
        AuditAction.USER_ROLE_ASSIGN,
        AuditAction.USER_IMPORT,
        AuditAction.ROLE_CREATE,
        AuditAction.ROLE_UPDATE,
        AuditAction.ROLE_PERM_BIND,
        AuditAction.AUDIT_EXPORT,
    }
    assert used <= set(AuditAction.ALL), f"词表缺少：{used - set(AuditAction.ALL)}"


# ============================================================================
# L1 · 筛选参数校验
# ============================================================================
def test_filter_blank_string_is_treated_as_not_provided() -> None:
    """``?action=`` 必须等价于「未传」。

    前端表单清空后常发出 ``action=``；若当成「筛 action 等于空串」，
    结果会是 0 条 —— 使用者会以为「这段时间的日志丢了」，而不是「我没填条件」。
    """
    f = AuditFilterRequest(action="", target_type="  ", target_id="", keyword=" ")
    assert f.action is None and f.target_type is None and f.target_id is None and f.keyword is None


def test_filter_rejects_reversed_time_range() -> None:
    with pytest.raises(ValidationError):
        AuditFilterRequest(
            start_time=datetime(2026, 9, 22, 10, 0, 0),
            end_time=datetime(2026, 9, 21, 10, 0, 0),
        )


def test_filter_rejects_range_longer_than_max() -> None:
    """时间跨度上限 90 天：本表按月分区，跨月查询无法被分区裁剪。"""
    start = datetime(2026, 1, 1, 0, 0, 0)
    with pytest.raises(ValidationError) as exc:
        AuditFilterRequest(start_time=start, end_time=start + timedelta(days=MAX_RANGE_DAYS + 5))
    assert str(MAX_RANGE_DAYS) in str(exc.value)

    ok = AuditFilterRequest(start_time=start, end_time=start + timedelta(days=MAX_RANGE_DAYS))
    assert ok.start_time == start


def test_filter_rejects_unknown_action_and_target_type() -> None:
    """拼错的动作码必须**报错**而不是「查询成功但返回空」。

    ``action`` 是自由文本列，前端手输或旧版前端拼错一个字母
    （``dict_item_updated`` vs ``dict_item_update``）时，
    若放行就会得到 0 条 —— 使用者据此得出「这段时间没人改过字典」的**错误结论**。
    """
    with pytest.raises(ValidationError) as exc:
        AuditFilterRequest(action="dict_item_updated")
    assert "dict_item_update" in str(exc.value)

    with pytest.raises(ValidationError):
        AuditFilterRequest(target_type="not_a_target")

    assert AuditFilterRequest(action=AuditAction.LOGIN).action == "login"


def test_export_request_defaults_and_row_limit() -> None:
    """导出默认 1 万行，上限 5 万；超出上限 → 校验失败（40001）。"""
    default = AuditExportRequest()
    assert default.format == "csv"
    assert 0 < default.max_rows <= MAX_EXPORT_ROWS

    with pytest.raises(ValidationError):
        AuditExportRequest(max_rows=MAX_EXPORT_ROWS + 1)
    with pytest.raises(ValidationError):
        AuditExportRequest(max_rows=0)


def test_export_request_inherits_filter_validation() -> None:
    """🔴 导出与列表**共用同一套筛选校验**（否则会出现「页面筛得出来、导出的却是全量」）。"""
    with pytest.raises(ValidationError):
        AuditExportRequest(action="nope")
    with pytest.raises(ValidationError):
        AuditExportRequest(
            start_time=datetime(2026, 9, 22), end_time=datetime(2026, 8, 1)
        )
    assert AuditExportRequest(action=AuditAction.LOGIN).action == "login"


# ============================================================================
# L1 · 脱敏与截断（写入侧的第二道防线）
# ============================================================================
def test_sanitize_masks_sensitive_keys_at_any_depth() -> None:
    """🔴 按键名黑名单脱敏，且**递归到任意深度**。

    只在顶层脱敏是不够的：``{"user": {"new_password": "..."}}`` 这种嵌套
    在实际调用里非常常见（把整个请求体丢进 detail 就会这样）。
    """
    cleaned = _sanitize(
        {
            "username": "zhangsan",
            "password": "PlainText1!",
            "new_password": "Another1!",
            "oldPassword": "CamelCase1!",
            "password_hash": "$2b$12$xxxx",
            "access_token": "eyJhbGciOi...",
            "refresh_token": "eyJhbGciOi...",
            "Authorization": "Bearer abc",
            "api_secret": "s3cr3t",
            "AES_KEY": "0" * 64,
            "nested": {"token": "x", "keep": 1},
            "items": [{"pwd": "y", "ok": 2}],
        }
    )
    # 明文一律不出现
    flat = json.dumps(cleaned, ensure_ascii=False)
    for leaked in ("PlainText1!", "Another1!", "CamelCase1!", "eyJhbGciOi", "s3cr3t", "0" * 64):
        assert leaked not in flat, f"敏感值泄露到审计：{leaked[:12]}…"
    # 非敏感字段必须**原样保留**（否则审计就没用了）
    assert cleaned["username"] == "zhangsan"
    assert cleaned["nested"]["keep"] == 1
    assert cleaned["items"][0]["ok"] == 2
    # 被脱敏的键仍在，只是值被替换（让人看得出「这里原本有个敏感字段」）
    assert cleaned["password"].startswith("***")
    assert cleaned["nested"]["token"].startswith("***")
    assert cleaned["items"][0]["pwd"].startswith("***")


def test_sanitize_truncates_overlong_values_and_deep_nesting() -> None:
    """超长值截断 + 过深嵌套截断（避免把 MEDIUMTEXT 当数据仓库用，也避免递归打穿栈）。"""
    long_text = "字" * 5000
    cleaned = _sanitize({"blob": long_text})
    assert len(cleaned["blob"]) < len(long_text)
    assert "已截断" in cleaned["blob"]

    deep: dict[str, Any] = {"leaf": 1}
    for _ in range(12):
        deep = {"child": deep}
    dumped = _dump(deep) or ""
    assert "嵌套过深" in dumped


def test_dump_keeps_chinese_readable_and_handles_non_json_types() -> None:
    """``ensure_ascii=False``：审计内容多为中文，转义后人工根本读不了。

    同时验证 ``datetime`` 等非 JSON 类型不会让写入失败（``default=str`` 兜底）。
    """
    text = _dump({"dept_name": "研发一部", "at": datetime(2026, 9, 22, 10, 0, 0)}) or ""
    assert "研发一部" in text
    assert "\\u" not in text
    assert "2026-09-22" in text
    assert _dump(None) is None


# ============================================================================
# L1 · CSV 导出：BOM、表头、**公式注入防护**
# ============================================================================
def test_csv_safe_escapes_formula_prefixes() -> None:
    """🔴 CSV 公式注入（CWE-1236）防护。

    ``ua`` 列内容来自 ``User-Agent`` 请求头，**完全由客户端控制**。
    若某条记录的 UA 是 ``=cmd|'/c calc'!A0``，导出后使用者
    用 Excel 打开就会触发公式执行 —— 而审计导出恰恰是高权限人员会打开的文件。
    """
    assert _csv_safe("=cmd|'/c calc'!A0").startswith("'=")
    assert _csv_safe("+1+1").startswith("'+")
    assert _csv_safe("-2+3").startswith("'-")
    assert _csv_safe("@SUM(A1)").startswith("'@")
    assert _csv_safe("\tTAB").startswith("'")
    # 正常值不得被改动
    assert _csv_safe("Mozilla/5.0 (Windows NT 10.0)") == "Mozilla/5.0 (Windows NT 10.0)"
    assert _csv_safe("") == ""


def test_render_csv_has_bom_header_and_escapes_injection() -> None:
    """CSV 结构：BOM（Excel 直接正确显示中文）+ 固定表头 + 公式转义真的落到输出里。"""
    item = AuditLogOut(
        id=1234567890123456789,
        created_at=datetime(2026, 9, 22, 10, 0, 0),
        user_id=42,
        username="zhangsan",
        action=AuditAction.LOGIN,
        action_name="登录",
        target_type=AuditTarget.SESSION,
        target_name="会话",
        target_id=None,
        before=None,
        after={"result": "success"},
        ip="10.0.0.1",
        ua="=HYPERLINK(\"http://evil\",\"click\")",
        trace_id="abc123",
    )
    csv_text = _render_csv([item])
    assert csv_text.startswith("\ufeff"), "缺少 BOM，Excel 打开会中文乱码"
    lines = csv_text.lstrip("\ufeff").split("\r\n")
    header = lines[0].split(",")
    assert header[:6] == ["id", "created_at", "user_id", "username", "action", "action_name"]
    assert "before" in header and "after" in header
    assert "1234567890123456789" in lines[1], "雪花 ID 必须以字符串原样导出"
    assert "'=HYPERLINK" in lines[1], "UA 中的公式未转义"


def test_render_csv_export_uses_string_ids_not_scientific_notation() -> None:
    """雪花 ID 导出为**字符串**：否则 Excel 会把它显示成 ``1.23457E+18``（末位丢失）。"""
    item = AuditLogOut(
        id=1839204771234567890,
        created_at=datetime(2026, 9, 22, 10, 0, 0),
        action=AuditAction.LOGIN,
    )
    csv_text = _render_csv([item]).lstrip("\ufeff")
    assert "1839204771234567890" in csv_text
    assert "E+" not in csv_text.upper()


# ============================================================================
# L1 · 中间件：请求元信息（审计的 ip / ua 来源）
# ============================================================================
def _meta_probe_app() -> FastAPI:
    """一个最小应用：把中间件采集到的 ip/ua 回显出来（不依赖任何 DS）。"""
    probe = FastAPI()
    probe.add_middleware(RequestMetaMiddleware)

    @probe.get("/probe")
    def _probe() -> dict[str, str | None]:
        return {"ip": get_client_ip(), "ua": get_user_agent()}

    return probe


def test_request_meta_middleware_collects_ip_and_ua() -> None:
    with TestClient(_meta_probe_app()) as c:
        body = c.get("/probe").json()
    assert body["ua"], "默认 UA（testclient）应被采集到"
    assert body["ip"]


def test_request_meta_middleware_prefers_leftmost_xff() -> None:
    """``X-Forwarded-For`` 取**最左**的非空项（那才是真实客户端，右侧全是代理）。"""
    with TestClient(_meta_probe_app()) as c:
        body = c.get("/probe", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1, 10.0.0.2"}).json()
    assert body["ip"] == "203.0.113.7"


def test_request_meta_middleware_truncates_overlong_header_values() -> None:
    """畸形超长头必须**截断**而不是让入库报错（审计采集失败不该影响业务响应）。"""
    with TestClient(_meta_probe_app()) as c:
        body = c.get("/probe", headers={"X-Forwarded-For": "9" * 300}).json()
    assert body["ip"] is not None
    assert len(body["ip"]) <= 64


def test_pick_ip_falls_back_to_scope_client() -> None:
    """无任何代理头时退回 ``scope["client"]``（直连场景）。"""
    assert _pick_ip([], ("192.0.2.5", 12345)) == "192.0.2.5"
    assert _pick_ip([(b"x-real-ip", b"198.51.100.9")], ("192.0.2.5", 1)) == "198.51.100.9"
    # XFF 优先级高于 X-Real-IP
    assert (
        _pick_ip([(b"x-forwarded-for", b"203.0.113.1"), (b"x-real-ip", b"198.51.100.9")], None)
        == "203.0.113.1"
    )
    assert _pick_ip([], None) is None


# ============================================================================
# L1 · 中间件：限流（CU-09）
# ============================================================================
def test_normalize_rate_path_collapses_resource_ids() -> None:
    """🔴 路径归一化：不做的话，遍历 ID 就能把限流放大成「每个 ID 一个桶」。"""
    assert normalize_rate_path("/api/v1/talents/1839204771234567890") == "/api/v1/talents/{id}"
    assert normalize_rate_path("/api/v1/users/1") == "/api/v1/users/{id}"
    assert normalize_rate_path("/api/v1/dict-items/999") == "/api/v1/dict-items/{id}"
    # 非 ID 段必须保留（否则所有接口会共用一个桶，限流变成「全站共享额度」）
    assert normalize_rate_path("/api/v1/audit-logs") == "/api/v1/audit-logs"
    assert (
        normalize_rate_path("/api/v1/dicts/education_level/items")
        == "/api/v1/dicts/education_level/items"
    )
    # 空段不得被误当成 ID
    assert normalize_rate_path("/api/v1/x//y") == "/api/v1/x//y"


def test_rate_limit_middleware_returns_42901_with_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 超限 → HTTP 429 + ``code=42901`` + ``X-RateLimit-*`` / ``Retry-After``（接口 §1.1/§1.4）。

    直接对**判定结果**打桩（而不是靠真实阈值凑请求数）：既稳定、又不受默认阈值影响。
    这也是「中间件必须自己构造响应」的验证 —— 它抛异常会被兜成 500 而不是 42901。
    """
    from app.core import ratelimit as rl
    from app.main import create_app

    def _deny(key: str, limit: int, window_seconds: int = 60) -> rl.RateLimitDecision:
        return rl.RateLimitDecision(allowed=False, limit=limit, remaining=0, retry_after=42)

    monkeypatch.setattr(rl, "check_rate_limit", _deny)
    with TestClient(create_app()) as c:
        resp = c.get("/api/v1/dicts")

    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == int(ErrorCode.RATE_LIMITED) == 42901
    # 五件套必须齐（中间件复用 build_error_response，不能自己拼）
    assert set(body) == {"code", "message", "data", "page", "trace_id"}
    assert resp.headers["Retry-After"] == "42"
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert "X-RateLimit-Limit" in resp.headers
    # 🔴 trace_id 必须存在：限流响应也要能串链路（这就是 TraceId 中间件在限流外侧的原因）
    assert body["trace_id"]


def test_rate_limit_allows_when_decision_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向对照：判定放行时请求必须真的走到业务层（否则上面的 429 测试可能「永远 429」）。"""
    from app.core import ratelimit as rl
    from app.main import create_app

    calls: list[str] = []

    def _allow(key: str, limit: int, window_seconds: int = 60) -> rl.RateLimitDecision:
        calls.append(key)
        return rl.RateLimitDecision(allowed=True, limit=limit, remaining=limit - 1)

    monkeypatch.setattr(rl, "check_rate_limit", _allow)
    with TestClient(create_app()) as c:
        resp = c.get("/api/v1/dicts")
    assert calls, "限流判定根本没被调用 —— 中间件没挂上"
    assert resp.status_code != 429
    assert resp.json()["code"] != 42901


def test_rate_limit_exempts_probes_and_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针 / 文档路径不参与限流 —— 否则监控轮询会把额度吃掉，真正的人反而被限。"""
    from app.core import ratelimit as rl
    from app.main import create_app

    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("探针路径不应触发限流判定")

    monkeypatch.setattr(rl, "check_rate_limit", _fail_if_called)
    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/openapi.json").status_code == 200


def test_rate_limit_disabled_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RATE_LIMIT_ENABLED=false`` 时完全绕过（本地调试 / 压测用）。"""
    from app.core import ratelimit as rl
    from app.main import create_app

    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("限流已关闭，不应调用判定")

    monkeypatch.setattr(rl, "check_rate_limit", _fail_if_called)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/dicts").status_code != 429


def test_rate_limit_fails_open_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 Redis 抖动必须 **fail-open**：宁可短时限流失效，不可让缓存抖动演变成全站 429。"""
    from app.core import ratelimit as rl

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("redis is down")

    monkeypatch.setattr(rl, "get_redis", _boom)
    decision = rl.check_rate_limit("rate:http:test:x", 1)
    assert decision.allowed is True


def test_build_error_response_matches_unified_shape() -> None:
    """``build_error_response`` 是中间件侧**唯一的**响应格式来源（防五件套漂移）。"""
    resp = build_error_response(ErrorCode.RATE_LIMITED, data={"retry_after": 5}, headers={"A": "b"})
    body = resp.body
    import json as _json

    parsed = _json.loads(body)
    assert set(parsed) == {"code", "message", "data", "page", "trace_id"}
    assert parsed["code"] == 42901
    assert parsed["data"] == {"retry_after": 5}
    assert resp.status_code == 429
    assert resp.headers["A"] == "b"


# ============================================================================
# L1 · 分区维护脚本的月份算术（算错会**删掉不该删的审计数据**）
# ============================================================================
def test_partition_month_arithmetic() -> None:
    from scripts.audit_partition import month_index, partition_name, shift_month

    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 9, 3) == (2026, 12)
    assert partition_name(2026, 10) == "p202610"
    assert month_index(2027, 1) - month_index(2026, 12) == 1


def test_partition_info_parses_month_from_name_not_from_to_days() -> None:
    """🔴 覆盖月份从**分区名**解析，不从 ``TO_DAYS`` 反算。

    第一版实现用 ``date.fromordinal(to_days - 1)`` 反算，实测偏了一个季度
    （MySQL 的 ``TO_DAYS`` 以「公元 0 年」起算，与 Python ``toordinal`` 不同源）。
    这类偏移不报错，只会让「该删哪些分区」静默错位 —— 后果是**删掉不该删的审计数据**。
    """
    from scripts.audit_partition import PartitionInfo

    good = PartitionInfo("p202610", "740286", 0)
    assert good.covered_month == (2026, 10)
    assert good.upper_bound_label == "2026-11-01（不含）"
    assert good.is_pmax is False

    pmax = PartitionInfo("pmax", "MAXVALUE", 0)
    assert pmax.covered_month is None
    assert pmax.is_pmax is True

    # 名字不符规范 → 视为「不可识别」，任何回收逻辑都不会碰它（fail-safe）
    assert PartitionInfo("p_odd", "740286", 0).covered_month is None
    assert PartitionInfo("p202613", "740286", 0).covered_month is None


def test_droppable_partitions_never_includes_pmax_or_unknown_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 ``pmax`` 永不回收（删掉它后续插入会直接报错）；名字不识别的分区也不回收。

    ⚠️ **CU-24 把 ``list_partitions()`` 泛化成 ``list_partitions(table)``**（同时维护
    ``audit_log`` 与 ``ai_call_log``），故这里的替身**必须接受 table 参数** ——
    否则用例会以 ``TypeError`` 失败，而失败原因（替身签名过时）与被测语义
    （会不会误删 pmax）毫无关系，排查时容易被当成产品缺陷。

    顺带断言**目标表真的透传下去了**：分区维护若「永远只看 audit_log」，
    ``ai_call_log``（写入量高一个数量级）的膨胀会**静默**到无法归档。
    """
    from scripts import audit_partition as ap

    today = date.today()
    old_y, old_m = ap.shift_month(today.year, today.month, -20)
    parts = [
        ap.PartitionInfo(ap.partition_name(old_y, old_m), "1", 100),  # 20 个月前 → 该回收
        ap.PartitionInfo("p_odd", "1", 100),  # 不可识别 → 不回收
        ap.PartitionInfo("pmax", "MAXVALUE", 999),  # 兜底 → 不回收
    ]
    seen: list[str] = []

    def _fake_list(table: str = ap.TABLE) -> list[ap.PartitionInfo]:
        seen.append(table)
        return parts

    monkeypatch.setattr(ap, "list_partitions", _fake_list)
    targets = ap.droppable_partitions(12, table="ai_call_log")

    assert [p.name for p in targets] == [ap.partition_name(old_y, old_m)]
    assert seen == ["ai_call_log"], "目标表必须原样透传给 list_partitions（不能写死 audit_log）"


def test_drop_partitions_refuses_to_drop_pmax() -> None:
    """双保险：即便上游把 pmax 混进待删列表，也要**拒绝执行**（而不是照做）。"""
    from scripts.audit_partition import drop_partitions

    with pytest.raises(SystemExit):
        drop_partitions(["pmax"])


# ============================================================================
# L1 · 路由与权限登记
# ============================================================================
def test_openapi_registers_api_28_and_29(app: FastAPI) -> None:
    """两个接口必须在 OpenAPI 里（Router 是否真的被 include，看这里最可靠）。

    ⚠️ 不遍历 ``app.routes`` 找子路由：FastAPI ≥0.14 的 ``include_router``
    保留 ``_IncludedRouter`` 包装、不再摊平，遍历会「找不到」而误判。
    """
    paths = app.openapi()["paths"]
    assert "/api/v1/audit-logs" in paths
    assert "get" in paths["/api/v1/audit-logs"]
    assert "/api/v1/audit-logs/export" in paths
    assert "post" in paths["/api/v1/audit-logs/export"]

    summary = paths["/api/v1/audit-logs"]["get"]["summary"]
    assert "API-28" in summary
    assert "API-29" in paths["/api/v1/audit-logs/export"]["post"]["summary"]


def test_audit_endpoints_use_dedicated_permissions() -> None:
    """查看与导出用**两个不同**的权限点（导出会带走数据，权限粒度不该与查看相同）。

    ⚠️ 不能靠 ``str(route.dependencies)`` 找权限码 —— ``require_perm`` 返回的是
    **闭包**，``str()`` 只有 ``<function ...>``，权限码藏在 ``__closure__`` 里
    （这样断言会「永远失败」，而不是「永远通过」，但同样没测到东西）。
    故遍历依赖树、把闭包单元里的字符串取出来 —— 这样断言的是**真实声明**，
    不是源码文本。
    """
    from app.routers.audit_router import router as audit_router

    def _collect(dependency: Any, found: set[str]) -> None:
        for cell in getattr(getattr(dependency, "call", None), "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # 空单元
                continue
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, tuple):
                found.update(str(v) for v in value)
        for sub in getattr(dependency, "dependencies", ()) or ():
            _collect(sub, found)

    perms_by_path: dict[str, set[str]] = {}
    for route in audit_router.routes:
        found: set[str] = set()
        for dep in getattr(route.dependant, "dependencies", ()) or ():
            _collect(dep, found)
        perms_by_path[route.path] = found

    assert "audit:view" in perms_by_path["/audit-logs"]
    assert "audit:export" in perms_by_path["/audit-logs/export"]
    # 🔴 导出**不得**复用 audit:view（否则「能看」就等于「能导」）
    assert "audit:view" not in perms_by_path["/audit-logs/export"]


def test_audit_router_has_no_write_endpoints() -> None:
    """🔴 审计**不经任何 HTTP 入口写入**（接口 §4：「AUDIT 全列只读」）。

    架构上杜绝「调用 API 伪造审计记录」—— 这也是本模块只应有 GET / POST(export)
    两种方法的原因。
    """
    from app.routers.audit_router import router as audit_router

    methods = {m for route in audit_router.routes for m in (route.methods or set())}
    assert methods <= {"GET", "POST"}, f"出现了预期外的方法：{methods}"
    for route in audit_router.routes:
        if "POST" in (route.methods or set()):
            assert route.path.endswith("/export"), f"POST 只允许导出：{route.path}"


# ============================================================================
# L2 · 集成：审计真的落库
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """建一个**独立租户**：4 个权限点 + 3 个角色 + 3 个账号 + 1 个字典类型/项。

    为什么必须独立租户：本文件要断言「租户隔离」。
    若复用种子里那个租户，断言会退化成「碰巧查不到」，而不是真的被隔离。

    ⚠️ 缺依赖时**在函数体内 skip** —— pytest 不允许把 ``skipif`` 加到 fixture 上
    （``Marks cannot be applied to fixtures``），而用例级的 ``@requires_db``
    保护不到夹具自身的建库动作。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    inspector = sa_inspect(engine)
    if not inspector.has_table("audit_log"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0003_audit）")
    if not inspector.has_table("dict_item"):
        pytest.skip("数据库缺少 0002_dict 的表")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu08_{suffix}", tenant_name=f"CU08测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    # ---- 权限点：只有 audit:view / audit:export / dict:update 三个 ----
    perms: dict[str, SysPermission] = {}
    for code in (VIEW_PERM, EXPORT_PERM, "dict:update"):
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    def _mk_role(code: str, grants: list[str], scope: int) -> SysRole:
        role = SysRole(
            tenant_id=tid,
            role_code=code,
            role_name=code,
            data_scope=scope,
            is_builtin=1,
            status=1,
        )
        db.add(role)
        db.flush()
        for c in grants:
            db.add(SysRolePermission(tenant_id=tid, role_id=role.id, perm_id=perms[c].id))
        return role

    adm_role = _mk_role("ADM", list(perms), int(RoleDataScope.ALL))
    #: 对齐矩阵里的 **AUDIT**：能查不能导（且 data_scope=4 仅统计值）
    audit_role = _mk_role("AUDIT", [VIEW_PERM], int(RoleDataScope.STATS_ONLY))
    #: 一个审计权限都没有
    none_role = _mk_role("NONE", [], int(RoleDataScope.ALL))

    def _mk_user(name: str, role: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid,
            username=name,
            password_hash=SecurityUtils.hash_password(PWD),
            real_name=name,
            status=1,
            login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        return user

    _mk_user(f"adm_{suffix}", adm_role)
    _mk_user(f"audit_{suffix}", audit_role)
    _mk_user(f"none_{suffix}", none_role)

    # ---- 一个字典类型 + 一个字典项（用于「业务变更 → 审计落库」的端到端链路）----
    dtype = DictType(tenant_id=tid, type_code="grade", type_name="年级", status=1, is_deleted=0)
    db.add(dtype)
    db.flush()
    ditem = DictItem(
        tenant_id=tid,
        type_code="grade",
        item_code="g1",
        item_name="大一",
        sort_no=10,
        status=1,
        is_deleted=0,
    )
    db.add(ditem)
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in (
        ("adm", f"adm_{suffix}"),
        ("audit", f"audit_{suffix}"),
        ("none", f"none_{suffix}"),
    ):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {
        "tenant": tenant,
        "tenant_id": tid,
        "tenant_code": tenant.tenant_code,
        "adm_username": f"adm_{suffix}",
        "dict_item_id": int(ditem.id),
        "dict_item_code": ditem.item_code,
        "tokens": tokens,
    }

    # 清理：只删**本用例自建的**业务数据与账号；审计记录**刻意保留**
    #（只追加表不留删除路径，这正是它的设计意图）
    from sqlalchemy import delete, update

    db.execute(delete(DictItem).where(DictItem.tenant_id == tid))
    db.execute(delete(DictType).where(DictType.tenant_id == tid))
    db.execute(delete(SysUserRole).where(SysUserRole.tenant_id == tid))
    db.execute(delete(SysRolePermission).where(SysRolePermission.tenant_id == tid))
    db.execute(delete(SysPermission).where(SysPermission.tenant_id == tid))
    db.execute(update(SysUser).where(SysUser.tenant_id == tid).values(is_deleted=1, status=0))
    db.execute(update(SysRole).where(SysRole.tenant_id == tid).values(is_deleted=1))
    db.execute(update(Tenant).where(Tenant.id == tid).values(status=0))
    db.commit()


def _rows(db: Session, tenant_id: int, action: str) -> list[AuditLog]:
    """按租户 + 动作取审计行（时间倒序）。

    🔴 **读之前先 ``rollback()``，结束当前读事务** —— 这是本文件最关键的一个细节。

    MySQL 默认隔离级别是 **REPEATABLE READ**：一个会话的**第一次** ``SELECT``
    就固定了快照，此后即使其它连接提交了新数据，本会话**也看不到**。
    而审计是**独立事务**写入的（见 :mod:`app.services.audit_service`），
    因此「先读一次拿基线 → 发请求 → 再读一次」的写法会**恒等**，
    断言「审计是否落库」会永远失败（且看起来像「审计没写」，极易误判）。

    ``rollback()`` 释放当前读视图，下一次 ``SELECT`` 重新取快照。
    （会话上没有未提交改动时，``rollback()`` 无副作用。）
    """
    from sqlalchemy import select as _select

    db.rollback()
    stmt = (
        _select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def test_login_writes_audit(env: dict, db: Session) -> None:
    """登录成功必须留痕（F-1.5 / 接口 API-01 备注）。

    夹具已为 3 个账号各登录一次，故本租户至少有 3 条 ``login`` 记录。
    """
    rows = _rows(db, env["tenant_id"], AuditAction.LOGIN)
    assert len(rows) >= 3, "登录没有写审计"
    latest = rows[0]
    assert latest.user_id is not None
    assert latest.tenant_id == env["tenant_id"]
    payload = json.loads(latest.after_json or "{}")
    assert payload["result"] == "success"
    # username 落在 after 里（便于不回表也能读出「谁登录了」）
    assert payload["username"]
    assert "password" not in json.dumps(payload).lower()


def test_failed_login_writes_audit_and_never_stores_password(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 登录失败**必须留痕**，且密码**绝不入库**（接口 API-01 备注）。

    这条同时是「审计为什么必须独立事务」的回归保护：
    失败路径会 ``raise`` → 请求 Session 回滚。若审计共用那个 Session，
    这条记录会消失 —— 而「谁在爆破密码」恰恰是最需要留痕的事件。

    ⚠️ 刻意用**不存在的用户名**（而不是「存在的用户 + 错密码」）：
    后者会累加 ``login_fail_count``，达到阈值会把账号**真的锁 10 分钟**，
    污染后续用例（这正是本项目最讨厌的「测试互相踩」）。
    同时这个分支覆盖的正是「拿不到 ``user`` 对象 → 必须显式传 tenant_id」的路径。
    """
    before = len(_rows(db, env["tenant_id"], AuditAction.LOGIN))

    ghost = f"ghost_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": ghost, "password": "WrongPassword1!", "tenant_code": env["tenant_code"]},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == 40102

    rows = _rows(db, env["tenant_id"], AuditAction.LOGIN)
    assert len(rows) > before, "登录失败没有写审计（或记录随业务事务一起回滚了）"
    row = rows[0]
    payload = json.loads(row.after_json or "{}")
    assert payload["result"] == "fail"
    assert payload["username"] == ghost
    blob = (row.before_json or "") + (row.after_json or "")
    assert "WrongPassword1!" not in blob
    assert row.user_id is None, "账号不存在时不该编造 user_id"


def test_change_password_audit_has_no_secret(env: dict, client: TestClient, db: Session) -> None:
    """改密审计只记「发生过」，不记任何口令内容（旧口令/新口令都不行）。

    ⚠️ 用**本用例现建的临时账号**（而不是夹具里共用的 adm）：
    改密会递增 Token 版本、使旧 Token 全部失效 ——
    若动到共用账号，后续用例会莫名其妙 40101，而且很难看出是被谁改坏的。
    """
    suffix = uuid.uuid4().hex[:8]
    username = f"pw_{suffix}"
    db.add(
        SysUser(
            tenant_id=env["tenant_id"],
            username=username,
            password_hash=SecurityUtils.hash_password(PWD),
            real_name="改密测试",
            status=1,
            login_fail_count=0,
        )
    )
    db.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PWD, "tenant_code": env["tenant_code"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]

    old, new = PWD, "NewAdmin@123456"
    resp = client.put(
        "/api/v1/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": old, "new_password": new},
    )
    assert resp.status_code == 200, resp.text

    rows = _rows(db, env["tenant_id"], AuditAction.CHANGE_PASSWORD)
    assert rows, "改密没有写审计"
    blob = (rows[0].before_json or "") + (rows[0].after_json or "")
    assert old not in blob and new not in blob
    assert "password" not in blob.lower()
    assert json.loads(rows[0].after_json or "{}")["result"] == "success"

    # 反向对照：改密必须真的生效（新口令能登录、旧口令不能）
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": new, "tenant_code": env["tenant_code"]},
    )
    assert ok.status_code == 200, ok.text


def test_dict_update_writes_before_and_after_snapshots(env: dict, client: TestClient, db: Session) -> None:
    """🔴 业务变更 → 审计留痕的新增/变更路径（F-1.7 的「改前值 / 改后值 / 操作人」）。

    这是 CU-07 遗留「API-26 留痕落库随 CU-08」的验收点：
    此前只输出结构化日志，现在必须能在表里查到 before/after。
    """
    resp = client.put(
        f"/api/v1/dict-items/{env['dict_item_id']}",
        headers=_adm(env),
        json={"item_name": "大一（改名）", "status": 0},
    )
    assert resp.status_code == 200, resp.text

    rows = _rows(db, env["tenant_id"], AuditAction.DICT_ITEM_UPDATE)
    assert rows, "字典变更没有落审计"
    row = rows[0]
    assert row.target_type == AuditTarget.DICT_ITEM
    assert str(row.target_id) == str(env["dict_item_id"])
    before = json.loads(row.before_json or "{}")
    after = json.loads(row.after_json or "{}")
    assert before["item_name"] == "大一"
    assert after["item_name"] == "大一（改名）"
    assert after["changed"]["item_name"] == ["大一", "大一（改名）"]
    assert after["changed"]["status"] == [1, 0]


def test_create_dict_item_writes_audit(env: dict, client: TestClient, db: Session) -> None:
    """新增路径也要留痕（不能只覆盖「修改」）。"""
    resp = client.post(
        "/api/v1/dicts/grade/items",
        headers=_adm(env),
        json={"item_code": "g2", "item_name": "大二", "sort_no": 20},
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]

    rows = _rows(db, env["tenant_id"], AuditAction.DICT_ITEM_CREATE)
    assert rows
    assert str(rows[0].target_id) == str(new_id)
    assert json.loads(rows[0].after_json or "{}")["item_code"] == "g2"


# ============================================================================
# L2 · 集成：API-28 查询（权限 / 分页 / 筛选 / 隔离）
# ============================================================================
def test_api_28_returns_paginated_envelope(env: dict, client: TestClient) -> None:
    """形状：``data`` 是**数组**、分页信息在**同级** ``page``（接口 §1.1）。"""
    resp = client.get("/api/v1/audit-logs", headers=_adm(env))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert isinstance(body["data"], list)
    assert set(body["page"]) == {"total", "page", "per_page", "pages"}
    assert body["page"]["page"] == 1
    assert body["trace_id"]


def test_api_28_item_shape_and_parsed_snapshots(env: dict, client: TestClient, db: Session) -> None:
    """出参字段完整：``before``/``after`` 已**解析为对象**，``username`` 已反查。

    雪花 ID 一律**字符串**下发（CR-5）—— 数字下发会被 JS 截断，
    前端再拿它请求详情就稳定 404。
    """
    client.put(
        f"/api/v1/dict-items/{env['dict_item_id']}",
        headers=_adm(env),
        json={"item_name": "大一（再改）"},
    )
    resp = client.get(
        f"/api/v1/audit-logs?action={AuditAction.DICT_ITEM_UPDATE}", headers=_adm(env)
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"][0]
    for key in (
        "id",
        "created_at",
        "user_id",
        "username",
        "action",
        "action_name",
        "target_type",
        "target_name",
        "target_id",
        "before",
        "after",
        "ip",
        "ua",
        "trace_id",
    ):
        assert key in item, f"缺少字段 {key}"
    assert isinstance(item["id"], str), "雪花 ID 必须字符串下发（CR-5）"
    assert isinstance(item["user_id"], str)
    assert isinstance(item["after"], dict), "after 应为已解析的对象"
    assert item["action_name"] == "修改字典项"
    assert item["target_name"] == "字典项"
    assert item["username"], "操作人账号应已反查填充"


def test_api_28_filters_by_action_and_time_window(env: dict, client: TestClient) -> None:
    """按动作 + 时间窗筛选；反向对照：不存在的动作查不到、宽窗口能查到。"""
    now = datetime.now()
    window = f"start_time={(now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')}"
    window += f"&end_time={(now + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')}"

    hit = client.get(f"/api/v1/audit-logs?action={AuditAction.LOGIN}&{window}", headers=_adm(env))
    assert hit.status_code == 200, hit.text
    assert hit.json()["page"]["total"] >= 1

    # 反向对照：时间窗完全落在过去 → 查不到（证明过滤条件真的生效了）
    past = f"start_time={(now - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')}"
    past += f"&end_time={(now - timedelta(days=9)).strftime('%Y-%m-%d %H:%M:%S')}"
    miss = client.get(f"/api/v1/audit-logs?action={AuditAction.LOGIN}&{past}", headers=_adm(env))
    assert miss.status_code == 200
    assert miss.json()["page"]["total"] == 0


def test_api_28_rejects_unknown_action_with_40001(env: dict, client: TestClient) -> None:
    """拼错的 ``action`` → **40001**（而不是「200 + 空列表」）。"""
    resp = client.get("/api/v1/audit-logs?action=not_an_action", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_api_28_rejects_overlong_time_span(env: dict, client: TestClient) -> None:
    """跨度超上限 → 40001，且提示里带上限值（让调用方知道该怎么收窄）。"""
    start = (datetime.now() - timedelta(days=MAX_RANGE_DAYS + 30)).strftime("%Y-%m-%d %H:%M:%S")
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resp = client.get(
        f"/api/v1/audit-logs?start_time={start}&end_time={end}", headers=_adm(env)
    )
    assert resp.status_code == 400, resp.text
    assert str(MAX_RANGE_DAYS) in json.dumps(resp.json(), ensure_ascii=False)


def test_api_28_without_permission_is_40301(env: dict, client: TestClient) -> None:
    """一个审计权限都没有 → **40301**（不是 40401：越权与不存在统一口径）。"""
    resp = client.get("/api/v1/audit-logs", headers=_h(env["tokens"]["none"]))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION) == 40301


def test_stats_only_role_can_read_audit(env: dict, client: TestClient) -> None:
    """🔴 ``AUDIT`` 角色（``data_scope=4`` 仅统计值）**必须能查审计**。

    这是本模块最容易写错的一处：审计查询若套用 ``DataScopeFilter.require_detail_access``，
    ``data_scope=4`` 会被 40301 拒掉 —— 与接口 §4 矩阵「API-28 AUDIT ✓」冲突。
    审计日志是**操作流水**而非「人员明细」，两者的可见性规则不同，以矩阵为准。
    """
    resp = client.get("/api/v1/audit-logs", headers=_h(env["tokens"]["audit"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0


def test_audit_is_tenant_isolated(env: dict, client: TestClient, db: Session) -> None:
    """CR-22：租户条件由服务端强制注入，前端无法放宽。

    做法（双层验证）：
    1. HTTP 侧：用本租户 Token 拉全量，**逐条**核对 ``tenant_id`` 都是本租户；
    2. DAO 侧：直接调 ``audit_dao.list_logs`` 取 100 条，核对租户条件真的被施加
       （即使表里有种子租户的历史记录，也只能取出本租户的）。

    为什么不能只断言「看不到 demo 租户的记录」：那只是「碰巧查不到」，
    条件写错时也可能恰好为空。
    """
    resp = client.get("/api/v1/audit-logs?per_page=100", headers=_adm(env))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"], "本租户至少应有 3 次登录的审计记录"

    from app.dao import audit_dao
    from app.dao.session import SessionLocal

    probe = SessionLocal()
    try:
        rows = audit_dao.list_logs(probe, env["tenant_id"], limit=100)
        assert rows, "DAO 按租户取不到记录"
        assert all(int(r.tenant_id) == env["tenant_id"] for r in rows)
        # 反向对照：换一个不存在的租户 → 一条都取不到（证明条件真的在起作用）
        assert audit_dao.list_logs(probe, 7999999999999999999, limit=100) == []
        assert audit_dao.count_logs(probe, 7999999999999999999) == 0
    finally:
        probe.close()
    _ = db


def test_api_28_pagination_is_stable(env: dict, client: TestClient) -> None:
    """分页必须**不重不漏**：同秒内的多条记录靠 ``(created_at, id)`` 构成全序。

    只按 ``created_at`` 排序时，同一秒的记录顺序由 MySQL 自行决定，
    翻页会出现「第 2 页重复第 1 页的记录」。
    """
    page1 = client.get("/api/v1/audit-logs?page=1&per_page=2", headers=_adm(env)).json()
    page2 = client.get("/api/v1/audit-logs?page=2&per_page=2", headers=_adm(env)).json()
    ids1 = [r["id"] for r in page1["data"]]
    ids2 = [r["id"] for r in page2["data"]]
    if len(ids1) == 2 and ids2:
        assert not set(ids1) & set(ids2), "分页出现重叠（排序不是全序）"


def test_api_28_per_page_over_limit_is_clamped_not_rejected(env: dict, client: TestClient) -> None:
    """``per_page=999`` 应收敛为 100 并**在 page 中回显实际值**（接口 §1.1），而不是 422。"""
    resp = client.get("/api/v1/audit-logs?per_page=999", headers=_adm(env))
    assert resp.status_code == 200, resp.text
    assert resp.json()["page"]["per_page"] == 100


# ============================================================================
# L2 · 集成：API-29 导出（留痕 / 权限分离 / 超限）
# ============================================================================
def test_api_29_needs_separate_permission(env: dict, client: TestClient) -> None:
    """🔴 有 ``audit:view`` **不等于**有 ``audit:export``。

    导出会把数据带出系统边界，权限粒度不该与「查看」相同。
    """
    resp = client.post(
        "/api/v1/audit-logs/export", headers=_h(env["tokens"]["audit"]), json={}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 40301


def test_api_29_returns_csv_and_leaves_its_own_audit_trail(env: dict, client: TestClient, db: Session) -> None:
    """🔴 「导出行为自身留痕」（接口 §2.2）。

    除「谁在什么时候导出」外，还必须记下**本次实际生效的筛选条件**与条数 ——
    否则「谁把什么范围的数据导走了」这个最关键的审计问题答不上来。
    """
    resp = client.post(
        "/api/v1/audit-logs/export",
        headers=_adm(env),
        json={"action": AuditAction.LOGIN, "max_rows": 100},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["format"] == "csv"
    assert data["row_count"] >= 1
    assert data["content"].startswith("\ufeff")
    assert data["filename"].startswith("audit_log_") and data["filename"].endswith(".csv")
    assert data["filters"]["action"] == AuditAction.LOGIN, "响应里应回显生效的筛选条件"

    rows = _rows(db, env["tenant_id"], AuditAction.AUDIT_EXPORT)
    assert rows, "导出没有留痕"
    after = json.loads(rows[0].after_json or "{}")
    assert after["filters"]["action"] == AuditAction.LOGIN
    assert after["row_count"] == data["row_count"]
    assert rows[0].user_id is not None


def test_api_29_rejects_when_matching_rows_exceed_limit(env: dict, client: TestClient) -> None:
    """🔴 超限**报 40002 而不是截断**。

    「导出一半却看不出不完整」是审计场景里最危险的静默错误：
    使用者会拿这份文件去核查，而缺行并不显眼。
    """
    resp = client.post(
        "/api/v1/audit-logs/export", headers=_adm(env), json={"max_rows": 1}
    )
    body = resp.json()
    # 本租户已有 ≥3 条审计（3 次登录）；max_rows=1 必然超限
    assert resp.status_code == 400, resp.text
    assert body["code"] == int(ErrorCode.PARAM_OVER_LIMIT) == 40002
    assert body["data"]["max_rows"] == 1
    assert body["data"]["matched"] > 1


def test_api_29_csv_escapes_client_controlled_user_agent(env: dict, client: TestClient) -> None:
    """🔴 端到端的 CSV 注入防线：``ua`` 由客户端控制，导出时必须转义。

    若这条失效，受害者是**打开导出文件的高权限人员**（Excel 执行公式）。
    """
    from app.services.audit_service import AuditService

    hostile_ua = "=cmd|'/c calc'!A0"
    AuditService.record(
        AuditAction.LOGIN,
        tenant_id=env["tenant_id"],
        user_id=1,
        after={"result": "fail", "marker": "injection-probe"},
        ua=hostile_ua,
        ip="203.0.113.9",
    )

    resp = client.post(
        "/api/v1/audit-logs/export", headers=_adm(env), json={"action": AuditAction.LOGIN}
    )
    assert resp.status_code == 200, resp.text
    content = resp.json()["data"]["content"]
    assert hostile_ua in content, "原始 UA 应被保留（可读）"
    assert f"'{hostile_ua}" in content, "UA 的公式前缀未被转义 —— Excel 打开会执行公式"


def test_api_29_rejects_reversed_time_range(env: dict, client: TestClient) -> None:
    """导出与列表共用同一套校验 → 起止颠倒同样 40001（不会「列表拦得住、导出拦不住」）。"""
    resp = client.post(
        "/api/v1/audit-logs/export",
        headers=_adm(env),
        json={
            "start_time": "2026-09-22 10:00:00",
            "end_time": "2026-09-21 10:00:00",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_export_filename_is_unique_per_second(env: dict, client: TestClient) -> None:
    """文件名带时间戳到秒 —— 连续两次导出不应生成同名文件（否则浏览器会覆盖）。"""
    first = client.post("/api/v1/audit-logs/export", headers=_adm(env), json={"max_rows": 5})
    assert first.status_code == 200
    name = first.json()["data"]["filename"]
    assert len(name.split("_")[-1].removesuffix(".csv")) == 6, "文件名应含 HHMMSS"


# ============================================================================
# L2 · 集成：审计写入的降级行为
# ============================================================================
def test_record_failure_does_not_break_business(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **fail-open**：审计写失败只记 ERROR，绝不外抛（不得让「记日志」拖垮业务）。

    做法：让底层 DAO 插入直接抛错，验证 :meth:`AuditService.record` 仍然返回 ``None``。
    """
    from app.dao import audit_dao

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(audit_dao, "insert_log", _boom)
    # 不抛异常即通过
    assert AuditService.record(AuditAction.LOGIN, tenant_id=1, user_id=1) is None
