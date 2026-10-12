"""CU-16 · 分层复核漏斗 + 复核工作台（F-3.3 / F-3.3.1，SQ-6，API-44~49）。

分层
----
- **L1 契约层**：``EDIT`` 必填 value、批量上限 200 / 硬上限 1000、
  ``estimated_minutes`` 口径、``mask_for_role`` 脱敏、权限矩阵钉死、
  OpenAPI 登记。
- **L2 集成层**（独立租户 + 造 2 条待复核字段行）：队列 meta（HIGH 分流 /
  estimated_minutes）、逐条处置（ACCEPT_AI / EDIT）、条件更新并发语义
  （已处理 → conflicted[]）、skip → 3 免复核、40902 三态、留痕
  （TBL-22 change_source=1）、dashboard。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect, select

from app.core.security import SecurityUtils
from app.models.enums import ParseReviewStatus, RoleDataScope
from app.models.talent import TalentBase, TalentChangeLog, TalentParseField
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.review import CONFIRM_BATCH_LIMIT, ReviewConfirmItem, estimated_minutes
from tests.conftest import db_available

PWD = "Admin@123456"
REVIEW_PERMS = ["review:queue", "review:detail", "review:confirm", "review:correct", "review:skip", "review:dashboard"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约
# ============================================================================
def test_edit_action_requires_value() -> None:
    """``action=EDIT`` 必填 value（不填 = 校验失败，而非静默接受空值）。"""
    assert ReviewConfirmItem(field_id="1", action="ACCEPT_AI").value is None
    with pytest.raises(ValidationError):
        ReviewConfirmItem(field_id="1", action="EDIT", value="  ")
    ok = ReviewConfirmItem(field_id="1", action="EDIT", value="张三丰")
    assert ok.value == "张三丰"


def test_confirm_batch_limit_pinned() -> None:
    assert CONFIRM_BATCH_LIMIT == 200


def test_estimated_minutes_rounding() -> None:
    """秒 → 分钟向上取整；0 条 = 0 分钟；正数至少 1 分钟。"""
    assert estimated_minutes(0, seconds_per_item=20.0) == 0
    assert estimated_minutes(1, seconds_per_item=20.0) == 1
    assert estimated_minutes(3, seconds_per_item=20.0) == 1  # 60s → 1 分钟
    assert estimated_minutes(4, seconds_per_item=20.0) == 2  # 80s → 2 分钟


def test_review_endpoints_registered_in_openapi(app) -> None:
    schema = app.openapi()
    for path, methods in (
        ("/api/v1/talent/review", {"get"}),
        ("/api/v1/talent/review/{field_id}", {"get"}),
        ("/api/v1/talent/review/confirm", {"post"}),
        ("/api/v1/talent/review/{field_id}/correct", {"post"}),
        ("/api/v1/talent/review/{field_id}/skip", {"post"}),
        ("/api/v1/talent/review/dashboard", {"get"}),
    ):
        assert path in schema["paths"], path
        assert methods <= set(schema["paths"][path]), (path, methods)


def test_review_permission_matrix_pinned() -> None:
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module("app.routers.review_router"))
    for perm in REVIEW_PERMS:
        assert f'require_perm("{perm}")' in src, perm


# ============================================================================
# L2 · 端到端夹具
# ============================================================================
@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    """独立租户：ADM + 1 份档案 + 2 条待复核字段（HIGH work_years / LOW major）。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("talent_parse_field"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0005_talent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu16_{suffix}", tenant_name=f"CU16测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in REVIEW_PERMS:
        p = SysPermission(tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1)
        db.add(p)
        perms[code] = p
    db.flush()

    role = SysRole(
        tenant_id=tid, role_code="ADM", role_name="ADM",
        data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
    )
    db.add(role)
    db.flush()
    for c in REVIEW_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=role.id, perm_id=perms[c].id))

    none_role = SysRole(
        tenant_id=tid, role_code="NONE", role_name="NONE",
        data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
    )
    db.add(none_role)
    db.flush()

    def _mk_user(name: str, r: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid, username=name, password_hash=SecurityUtils.hash_password(PWD),
            real_name=name, status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=r.id))
        return user

    adm = _mk_user(f"adm_{suffix}", role)
    _mk_user(f"none_{suffix}", none_role)

    talent = TalentBase(tenant_id=tid, name="张三", data_status=0)
    db.add(talent)
    db.flush()

    def _mk_field(name: str, value: str, impact: str, conf: float, raw: str = "原文片段") -> TalentParseField:
        row = TalentParseField(
            tenant_id=tid, talent_id=talent.id, field_name=name, raw_text=raw,
            parsed_value=value, confidence=conf, impact_level=impact,
            extract_model="rule-baseline-v1", source_file="resume.pdf",
            review_status=int(ParseReviewStatus.PENDING),
        )
        db.add(row)
        db.flush()
        return row

    f_high = _mk_field("work_years", "8", "HIGH", 0.45)
    f_low = _mk_field("major", "计算机", "LOW", 0.55)
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in {"adm": f"adm_{suffix}", "none": f"none_{suffix}"}.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {
        "client": client, "tokens": tokens, "tenant_id": tid,
        "talent_id": int(talent.id), "f_high": int(f_high.id), "f_low": int(f_low.id),
        "user_id": int(adm.id),
    }

    from sqlalchemy import delete

    from app.models.audit import AuditLog

    for model in (TalentParseField, TalentChangeLog, TalentBase, AuditLog,
                  SysUserRole, SysRolePermission, SysPermission, SysRole, SysUser, Tenant):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()


# ============================================================================
# L2 · API-44 队列
# ============================================================================
def test_queue_meta_and_high_first_ordering(env) -> None:
    """默认 HIGH 优先分流；``impact_level=ALL`` 看全量且 HIGH 在前；meta 带 estimated_minutes。"""
    c, t = env["client"], env["tokens"]["adm"]
    # 默认 = HIGH（漏斗：复核人首屏只见高影响面）
    r_high = c.get("/api/v1/talent/review", headers=_h(t))
    assert r_high.status_code == 200 and r_high.json()["code"] == 0, r_high.text
    meta = r_high.json()["meta"]
    assert meta["high_impact_total"] == 1 and meta["low_impact_sampled_total"] == 1
    assert isinstance(meta["estimated_minutes"], int) and meta["estimated_minutes"] >= 1
    high_items = r_high.json()["data"]
    assert {row["impact_level"] for row in high_items} == {"HIGH"}

    # ALL：两条都在，HIGH 在前（置信度升序 = 最可疑优先）
    r_all = c.get("/api/v1/talent/review", headers=_h(t), params={"impact_level": "ALL"})
    items = r_all.json()["data"]
    assert len(items) == 2
    assert items[0]["field_code"] == "work_years"
    # CR-5：ID 一律字符串，且雪花 ID 完整下发（不被 JS 精度截断）
    for row in items:
        assert isinstance(row["field_id"], str)
        assert int(row["field_id"]) > 2**53


def test_queue_sample_only_filters_to_low(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = c.get("/api/v1/talent/review", headers=_h(t), params={"sample_only": "true"})
    items = r.json()["data"]
    assert {row["impact_level"] for row in items} == {"LOW"}


def test_queue_requires_permission(env) -> None:
    c = env["client"]
    assert c.get("/api/v1/talent/review").status_code == 401
    r = c.get("/api/v1/talent/review", headers=_h(env["tokens"]["none"]))
    assert r.json()["code"] == 40301


# ============================================================================
# L2 · API-45/46/47 处置
# ============================================================================
def test_confirm_accept_ai_marks_field_and_writes_change_log(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    fid = str(env["f_high"])
    r = c.post("/api/v1/talent/review/confirm", headers=_h(t),
               json={"items": [{"field_id": fid, "action": "ACCEPT_AI"}]})
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["confirmed_count"] == 1 and body["conflicted"] == []
    # 留痕：TBL-22 change_source=1（人工复核）
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        logs = s.execute(
            select(TalentChangeLog).where(
                TalentChangeLog.tenant_id == env["tenant_id"],
                TalentChangeLog.talent_id == env["talent_id"],
                TalentChangeLog.change_source == 1,
            )
        ).scalars().all()
        assert len(logs) == 1 and logs[0].field_name == "work_years"
    finally:
        s.close()


def test_confirm_edit_value_overrides_parsed(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    fid = str(env["f_low"])
    r = c.post("/api/v1/talent/review/confirm", headers=_h(t),
               json={"items": [{"field_id": fid, "action": "EDIT", "value": "软件工程"}]})
    assert r.json()["code"] == 0, r.text
    detail = c.get(f"/api/v1/talent/review/{fid}", headers=_h(t)).json()["data"]
    assert detail["parsed_value"] == "软件工程"
    assert detail["confidence"] == 1.0


def test_confirm_already_processed_goes_conflicted(env) -> None:
    """API-94 三态：同字段重复处置 → affected=0 + conflicted[]（前端据此提示）。"""
    c, t = env["client"], env["tokens"]["adm"]
    fid = str(env["f_high"])
    payload = {"items": [{"field_id": fid, "action": "ACCEPT_AI"}]}
    assert c.post("/api/v1/talent/review/confirm", headers=_h(t), json=payload).json()["data"]["confirmed_count"] == 1
    r2 = c.post("/api/v1/talent/review/confirm", headers=_h(t), json=payload)
    body = r2.json()["data"]
    assert body["confirmed_count"] == 0 and fid in [str(x["field_id"]) for x in body["conflicted"]]


def test_correct_endpoint_conflict_is_40902(env) -> None:
    """单条修正接口：已处理 → 40902（API-94 他人已处理口径）。"""
    c, t = env["client"], env["tokens"]["adm"]
    fid = str(env["f_high"])
    c.post("/api/v1/talent/review/confirm", headers=_h(t),
           json={"items": [{"field_id": fid, "action": "ACCEPT_AI"}]})
    r = c.post(f"/api/v1/talent/review/{fid}/correct", headers=_h(t),
               json={"value": "10"})
    assert r.json()["code"] == 40902, r.text
    # 反向对照：未处置字段单条修正放行
    r2 = c.post(f"/api/v1/talent/review/{str(env['f_low'])}/correct", headers=_h(t),
                json={"value": "软件工程"})
    assert r2.json()["code"] == 0


def test_skip_marks_exempt_and_dashboard_counts(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    fid = str(env["f_low"])
    r = c.post(f"/api/v1/talent/review/{fid}/skip", headers=_h(t), json={"comment": "低价值字段"})
    assert r.json()["code"] == 0, r.text
    detail = c.get(f"/api/v1/talent/review/{fid}", headers=_h(t)).json()["data"]
    assert detail["review_status"] == 3
    # 处置完唯一待复核字段 → 档案 data_status 0→1（生效）
    r2 = c.post("/api/v1/talent/review/confirm", headers=_h(t),
                json={"items": [{"field_id": str(env["f_high"]), "action": "ACCEPT_AI"}]})
    assert r2.json()["code"] == 0
    dash = c.get("/api/v1/talent/review/dashboard", headers=_h(t)).json()["data"]
    assert dash["pending_total"] == 0


def test_confirm_unknown_field_goes_conflicted_not_silent(env) -> None:
    """不存在的 field_id → 部分成功语义：``conflicted[].reason=not_found``，
    不得计入 affected，也不得假成功（API-46 部分成功 + conflicted[] 口径）。"""
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post("/api/v1/talent/review/confirm", headers=_h(t),
               json={"items": [{"field_id": "7999999999999999999", "action": "ACCEPT_AI"}]})
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["confirmed_count"] == 0
    assert body["conflicted"][0]["reason"] == "not_found"
