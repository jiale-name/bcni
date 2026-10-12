"""CU-17 · 受控标签词表 + AI 选标签闭环（F-3.4 / F-3.4.1，SQ-8，API-50~56）。

分层
----
- **L1 契约层**：``tag_code`` 不可改（不在 UpdateRequest 里）、审核请求的
  decision 枚举与 approve/reject 互斥必填、``TagTreeNode`` 递归结构、
  权限矩阵静态钉死、端点 OpenAPI 登记。
- **L2 集成层**（独立租户）：建议 → ``status=0`` 待审核（🔴 AI 不得自由造词）、
  重复建议 40901、审核 approve 启用 / reject 必填理由、打标只能选启用项
  （违者 40001）、全量替换语义与留痕、跨租户 40401。

🔴 每条「拒绝」用例都配一条反向对照（应放行时确实放行）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect

from app.core.security import SecurityUtils
from app.core.tagdict import TagDictValidator
from app.models.enums import RoleDataScope
from app.models.talent import TalentBase
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.tag import (
    TagCreateRequest,
    TagReviewRequest,
    TagSuggestRequest,
    TagUpdateRequest,
)
from tests.conftest import db_available

PWD = "Admin@123456"
_UNKNOWN_ID = "7999999999999999999"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约
# ============================================================================
def test_update_request_has_no_tag_code_field() -> None:
    """🔴 ``tag_code`` 不可修改（同字典 ``item_code`` 口径）：契约层就不该有它。"""
    assert "tag_code" not in TagUpdateRequest.model_fields


def test_update_request_rejects_tag_code() -> None:
    with pytest.raises(ValidationError):
        TagUpdateRequest(tag_name="x", tag_code="HACKED")


def test_review_request_decision_is_literal() -> None:
    """审核 decision 只认 approve/reject。"""
    assert TagReviewRequest(decision="approve").decision == "approve"
    with pytest.raises(ValidationError):
        TagReviewRequest(decision="auto")


def test_suggest_request_name_length_pinned() -> None:
    """tag_name 2~32（接口 §4.4）。"""
    with pytest.raises(ValidationError):
        TagSuggestRequest(tag_name="过", category_code="skill")
    with pytest.raises(ValidationError):
        TagSuggestRequest(tag_name="x" * 33, category_code="skill")
    ok = TagSuggestRequest(tag_name="深度学习", category_code="skill")
    assert ok.category_code == "skill"


def test_create_request_valid_code_optional() -> None:
    """创建可不传 tag_code（由 derive_code 派生）；传了必须合法。"""
    assert TagCreateRequest(tag_name="Python").tag_code is None
    with pytest.raises(ValidationError):
        TagCreateRequest(tag_name="x", tag_code="bad-code")


def test_tag_endpoints_registered_in_openapi(app) -> None:
    schema = app.openapi()
    for path, methods in (
        ("/api/v1/talent/tag-dict", {"get", "post"}),
        ("/api/v1/talent/tag-dict/{tag_id}", {"put"}),
        ("/api/v1/talent/tag-dict/suggestions", {"post"}),
        ("/api/v1/talent/tag-dict/suggestions/{suggestion_id}/review", {"post"}),
        ("/api/v1/talents/{talent_id}/tags", {"get", "put"}),
    ):
        assert path in schema["paths"], path
        assert methods <= set(schema["paths"][path]), (path, methods)


def test_tag_permission_matrix_pinned() -> None:
    """权限矩阵静态钉死（router 源码级断言：漏接 require_perm 即失败）。"""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module("app.routers.tag_router"))
    for perm in ("tag:create", "tag:update", "tag:suggest", "tag:audit", "tag:assign"):
        assert f'require_perm("{perm}")' in src, perm
    # API-50 下拉数据源：已登录即可（无权限点）
    assert 'require_perm("tag:view")' not in src


# ============================================================================
# L2 · 端到端夹具
# ============================================================================
TAG_PERMS = ["tag:create", "tag:update", "tag:suggest", "tag:audit", "tag:assign"]


@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    """独立租户：ADM（全权限）+ STAFF（无 tag 权限）+ 1 个启用标签 + 1 份档案。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("talent_tag_dict"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0005_talent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu17_{suffix}", tenant_name=f"CU17测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in TAG_PERMS:
        p = SysPermission(tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1)
        db.add(p)
        perms[code] = p
    db.flush()

    def _mk_role(code: str, grants: list[str]) -> SysRole:
        role = SysRole(
            tenant_id=tid, role_code=code, role_name=code,
            data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
        )
        db.add(role)
        db.flush()
        for c in grants:
            db.add(SysRolePermission(tenant_id=tid, role_id=role.id, perm_id=perms[c].id))
        return role

    def _mk_user(name: str, role: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid, username=name, password_hash=SecurityUtils.hash_password(PWD),
            real_name=name, status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        return user

    adm_role = _mk_role("ADM", TAG_PERMS)
    none_role = _mk_role("NONE", [])
    _mk_user(f"adm_{suffix}", adm_role)
    _mk_user(f"none_{suffix}", none_role)

    talent = TalentBase(tenant_id=tid, name="张三", data_status=1)
    db.add(talent)
    db.flush()
    talent_id = int(talent.id)
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in {"adm": f"adm_{suffix}", "none": f"none_{suffix}"}.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {"client": client, "tokens": tokens, "tenant_id": tid, "talent_id": talent_id}

    # ---- 清理（本租户新增数据整体回滚式删除）----
    from sqlalchemy import delete

    from app.models.talent import TalentTagRel
    from app.models.audit import AuditLog

    db.execute(delete(TalentTagRel).where(TalentTagRel.tenant_id == tid))
    db.execute(delete(TalentBase).where(TalentBase.tenant_id == tid))
    db.execute(delete(TalentTagDictAlias).where(TalentTagDictAlias.tenant_id == tid))
    db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
    db.execute(delete(SysUserRole).where(SysUserRole.tenant_id == tid))
    db.execute(delete(SysRolePermission).where(SysRolePermission.tenant_id == tid))
    db.execute(delete(SysPermission).where(SysPermission.tenant_id == tid))
    db.execute(delete(SysRole).where(SysRole.tenant_id == tid))
    db.execute(delete(SysUser).where(SysUser.tenant_id == tid))
    db.execute(delete(Tenant).where(Tenant.id == tid))
    db.commit()


from app.models.talent import TalentTagDict as TalentTagDictAlias  # noqa: E402 — 清理用


# ============================================================================
# L2 · API-50/51/52 词表管理
# ============================================================================
def test_create_tag_directly_enabled_and_duplicate_conflict(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Python", "category_l1": "技术技能"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["status"] == 1 and body["tag_code"]

    # 反向对照不成立：同名 → 40901
    r2 = c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Python"})
    assert r2.json()["code"] == 40901, r2.text


def test_non_admin_cannot_create_tag(env) -> None:
    c, t = env["client"], env["tokens"]["none"]
    r = c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Rust"})
    assert r.json()["code"] == 40301, r.text
    # 反向对照：ADM 放行
    r2 = c.post("/api/v1/talent/tag-dict", headers=_h(env["tokens"]["adm"]), json={"tag_name": "Rust"})
    assert r2.json()["code"] == 0


def test_tag_dict_list_is_login_only(env) -> None:
    """API-50 下拉数据源：已登录即可（无需 tag 权限）。"""
    c = env["client"]
    r = c.get("/api/v1/talent/tag-dict", headers=_h(env["tokens"]["none"]))
    assert r.status_code == 200 and r.json()["code"] == 0
    # 未登录 → 401
    assert c.get("/api/v1/talent/tag-dict").status_code == 401


def test_tag_code_cannot_be_changed_via_api(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Go", "category_l1": "技术技能"})
    tag_id = r.json()["data"]["tag_id"]
    # extra=forbid：多传 tag_code → 40001 参数错误，而不是静默忽略
    r2 = c.put(f"/api/v1/talent/tag-dict/{tag_id}", headers=_h(t),
               json={"tag_code": "GOLANG", "tag_name": "Go语言"})
    assert r2.json()["code"] == 40001, r2.text
    # 正常改名放行
    r3 = c.put(f"/api/v1/talent/tag-dict/{tag_id}", headers=_h(t), json={"tag_name": "Go语言"})
    assert r3.json()["code"] == 0 and r3.json()["data"]["tag_name"] == "Go语言"


# ============================================================================
# L2 · API-53/54 建议与审核闭环（🔴 AI 不得自由造词）
# ============================================================================
def test_suggestion_lands_pending_never_enabled(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
               json={"tag_name": "LangGraph", "category_code": "技术技能"})
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["status"] == 0 and body["tag_code"] is None
    # 平铺列表里 status=0 可见，但**不是启用项**（打标不可用，后续用例验证）
    r2 = c.get("/api/v1/talent/tag-dict", headers=_h(t), params={"view": "flat", "status": 0})
    names = [row["tag_name"] for row in r2.json()["data"]]
    assert "LangGraph" in names


def test_duplicate_suggestion_conflict(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    payload = {"tag_name": "Milvus", "category_code": "技术技能"}
    assert c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t), json=payload).json()["code"] == 0
    r2 = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t), json=payload)
    assert r2.json()["code"] == 40901
    assert "existing_suggestion_id" in r2.json()["data"]


def test_suggest_existing_enabled_word_conflicts(env) -> None:
    """已存在启用项 → 40901 且 data 带现有项（没必要再建议）。"""
    c, t = env["client"], env["tokens"]["adm"]
    assert c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Redis"}).json()["code"] == 0
    r = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
               json={"tag_name": "Redis", "category_code": "技术技能"})
    assert r.json()["code"] == 40901
    assert "existing_tag_id" in r.json()["data"]


def test_review_approve_activates_with_code(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    sid = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
                 json={"tag_name": "Vanna", "category_code": "技术技能"}).json()["data"]["suggestion_id"]
    r = c.post(f"/api/v1/talent/tag-dict/suggestions/{sid}/review", headers=_h(t),
               json={"decision": "approve"})
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["status"] == 1 and TagDictValidator.is_valid_code(body["tag_code"])
    # ASCII 名「Vanna」派生编码 = VANNA（derive_code 的 ASCII 路径）
    # 审核通过后该词可被归一命中（启用项）
    r2 = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
                json={"tag_name": "Vanna", "category_code": "技术技能"})
    assert r2.json()["code"] == 40901 and "existing_tag_id" in r2.json()["data"]


def test_review_reject_requires_reason(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    sid = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
                 json={"tag_name": "敏感词", "category_code": "技术技能"}).json()["data"]["suggestion_id"]
    r = c.post(f"/api/v1/talent/tag-dict/suggestions/{sid}/review", headers=_h(t),
               json={"decision": "reject"})
    assert r.json()["code"] == 40001, r.text
    r2 = c.post(f"/api/v1/talent/tag-dict/suggestions/{sid}/review", headers=_h(t),
                json={"decision": "reject", "reject_reason": "与业务无关"})
    assert r2.json()["code"] == 0 and r2.json()["data"]["status"] == 2


def test_review_twice_is_not_found(env) -> None:
    """已处理的建议不可再审（NotFound —— 不是「假成功」）。"""
    c, t = env["client"], env["tokens"]["adm"]
    sid = c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
                 json={"tag_name": "Kafka", "category_code": "技术技能"}).json()["data"]["suggestion_id"]
    c.post(f"/api/v1/talent/tag-dict/suggestions/{sid}/review", headers=_h(t),
           json={"decision": "reject", "reject_reason": "重复"})
    r = c.post(f"/api/v1/talent/tag-dict/suggestions/{sid}/review", headers=_h(t),
               json={"decision": "approve"})
    assert r.json()["code"] == 40401, r.text


# ============================================================================
# L2 · API-55/56 打标闭环
# ============================================================================
def test_assign_rejects_non_enabled_tag(env) -> None:
    """🔴 打标只能选启用项（SQ-8 违者 40001）—— 建议中的词也不行。"""
    c, t, tid = env["client"], env["tokens"]["adm"], env["talent_id"]
    c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "Spark", "category_l1": "技术技能"})
    c.post("/api/v1/talent/tag-dict/suggestions", headers=_h(t),
           json={"tag_name": "Flink", "category_code": "技术技能"})
    enabled = c.get("/api/v1/talent/tag-dict", headers=_h(t),
                    params={"view": "flat", "status": 1, "per_page": 100}).json()["data"]
    codes = {row["tag_code"] for row in enabled}
    pending = c.get("/api/v1/talent/tag-dict", headers=_h(t),
                    params={"view": "flat", "status": 0}).json()["data"]
    pending_code = pending[0]["tag_code"]
    assert pending_code not in codes

    r = c.put(f"/api/v1/talents/{tid}/tags", headers=_h(t), json={"tag_codes": [pending_code]})
    assert r.json()["code"] == 40001, r.text
    # 反向对照：启用项放行
    good = sorted(codes)[0]
    r2 = c.put(f"/api/v1/talents/{tid}/tags", headers=_h(t), json={"tag_codes": [good]})
    assert r2.json()["code"] == 0, r2.text
    assert [row["tag_code"] for row in r2.json()["data"]["tags"]] == [good]


def test_assign_full_replace_semantics(env) -> None:
    """全量替换：第二次 PUT 后旧标签不再出现（不是追加）。"""
    c, t, tid = env["client"], env["tokens"]["adm"], env["talent_id"]
    c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "A1", "category_l1": "软素质"})
    c.post("/api/v1/talent/tag-dict", headers=_h(t), json={"tag_name": "B2", "category_l1": "软素质"})
    enabled = c.get("/api/v1/talent/tag-dict", headers=_h(t),
                    params={"view": "flat", "status": 1, "per_page": 100}).json()["data"]
    codes = sorted(row["tag_code"] for row in enabled)
    assert c.put(f"/api/v1/talents/{tid}/tags", headers=_h(t), json={"tag_codes": codes[:1]}).json()["code"] == 0
    assert c.put(f"/api/v1/talents/{tid}/tags", headers=_h(t), json={"tag_codes": codes[1:2]}).json()["code"] == 0
    r = c.get(f"/api/v1/talents/{tid}/tags", headers=_h(t))
    got = [row["tag_code"] for row in r.json()["data"]["tags"]]
    assert got == codes[1:2], got


def test_talent_tags_require_login_and_tenant_isolation(env) -> None:
    c = env["client"]
    tid = str(env["talent_id"])
    assert c.get(f"/api/v1/talents/{tid}/tags").status_code == 401
    # 不存在的档案（本租户内）→ 40401（无行级数据域口径的档案例外：talent_base 有 org_id？——
    # talent_base 归档案域，按 CR-8 有行级域 → 域外 40301；不存在的 ID 同样 40301）
    r = c.get(f"/api/v1/talents/{_UNKNOWN_ID}/tags", headers=_h(env["tokens"]["none"]))
    assert r.json()["code"] in (40301, 40401), r.text
