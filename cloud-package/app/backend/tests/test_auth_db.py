"""CU-04 · 认证链路集成测试（API-01 ~ API-06，**需要 MySQL**）。

缺依赖时自动 skip（见 ``conftest.requires_db``），使契约类测试在任何机器上都能跑。

🔴 本文件覆盖的两条红线
- **CR-5**：登录响应里的 ``user.id`` 必须是**字符串**，且能原样用于后续请求；
- **F-1.2**：连续 5 次密码错误 → 40103 锁定；改密后旧 Token 全部失效。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.core.security import SecurityUtils
from app.models.enums import RoleDataScope, SysUserStatus
from app.models.tenant import OrgDept, Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from tests.conftest import requires_db

pytestmark = [requires_db]

PASSWORD = "Hr@2026pass"


# ============================================================================
# 夹具：种子数据（每用例独立租户，互不干扰）
# ============================================================================
@pytest.fixture
def seed(db):
    """建租户 + 组织 + 角色 + 权限点 + 用户，用例结束删除。"""
    from app.dao.session import engine

    if not inspect(engine).has_table("sys_user"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"test_{suffix}", tenant_name=f"测试租户_{suffix}", status=1)
    db.add(tenant)
    db.flush()

    org = OrgDept(
        tenant_id=tenant.id,
        parent_id=0,
        dept_code=f"org_{suffix}",
        dept_name="人力资源部",
        dept_level=1,
        dept_path=f"/{tenant.id}/",
        status=1,
    )
    db.add(org)

    role = SysRole(
        tenant_id=tenant.id,
        role_code="HR",
        role_name="人力",
        data_scope=int(RoleDataScope.ALL),
        is_builtin=1,
        status=1,
    )
    db.add(role)

    perm = SysPermission(
        tenant_id=tenant.id,
        perm_code="talent:view",
        perm_name="档案查看",
        perm_type=3,
        parent_id=0,
        status=1,
    )
    db.add(perm)
    db.flush()

    db.add(SysRolePermission(tenant_id=tenant.id, role_id=role.id, perm_id=perm.id))

    user = SysUser(
        tenant_id=tenant.id,
        username=f"hr_{suffix}",
        password_hash=SecurityUtils.hash_password(PASSWORD),
        real_name="张敏",
        org_id=org.id,
        status=int(SysUserStatus.ENABLED),
    )
    db.add(user)
    db.flush()
    db.add(SysUserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id))
    db.commit()

    yield {
        "tenant": tenant,
        "org": org,
        "role": role,
        "perm": perm,
        "user": user,
        "username": user.username,
        "tenant_code": tenant.tenant_code,
        "user_id": user.id,
        "tenant_id": tenant.id,
    }

    # 清理（先子后父；无外键约束，顺序仅为可读性）
    db.execute(select(SysUser))  # 触发一次查询，确保连接有效
    for stmt in (
        SysUserRole.__table__.delete().where(SysUserRole.tenant_id == tenant.id),
        SysRolePermission.__table__.delete().where(SysRolePermission.tenant_id == tenant.id),
        SysUser.__table__.delete().where(SysUser.tenant_id == tenant.id),
        SysPermission.__table__.delete().where(SysPermission.tenant_id == tenant.id),
        SysRole.__table__.delete().where(SysRole.tenant_id == tenant.id),
        OrgDept.__table__.delete().where(OrgDept.tenant_id == tenant.id),
        Tenant.__table__.delete().where(Tenant.id == tenant.id),
    ):
        db.execute(stmt)
    db.commit()


def _login(client: TestClient, seed: dict, password: str = PASSWORD) -> dict:
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "username": seed["username"],
            "password": password,
            "tenant_code": seed["tenant_code"],
        },
    )
    return {"status": resp.status_code, "body": resp.json()}


# ============================================================================
# API-01 登录
# ============================================================================
def test_login_success_returns_tokens_and_string_ids(client: TestClient, seed) -> None:
    result = _login(client, seed)
    assert result["status"] == 200
    body = result["body"]
    assert body["code"] == 0

    data = body["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["expires_in"] > 0
    assert data["token_type"] == "Bearer"

    user = data["user"]
    # 🔴 CR-5：ID 必须是字符串，且数值正确
    assert isinstance(user["id"], str)
    assert int(user["id"]) == seed["user_id"]
    assert isinstance(user["tenant_id"], str)
    assert isinstance(user["org_id"], str)
    assert user["org_name"] == "人力资源部"
    assert user["roles"] == ["HR"]
    assert user["data_scope"] == int(RoleDataScope.ALL)
    assert user["data_scope_name"] == "全部"
    # 🔴 密码绝不返回
    assert "password" not in user and "password_hash" not in user


def test_login_response_trace_id_matches_header(client: TestClient, seed) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": seed["username"], "password": PASSWORD, "tenant_code": seed["tenant_code"]},
    )
    assert resp.headers["X-Request-Id"] == resp.json()["trace_id"]


def test_login_wrong_password_returns_40102(client: TestClient, seed) -> None:
    result = _login(client, seed, password="WrongPass123")
    assert result["status"] == 401
    assert result["body"]["code"] == 40102


def test_login_unknown_user_returns_same_40102(client: TestClient, seed) -> None:
    """🔴 不区分「账号不存在」与「密码错误」—— 防用户名枚举。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "no_such_user_xyz", "password": PASSWORD, "tenant_code": seed["tenant_code"]},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 40102


def test_login_unknown_tenant_returns_40102(client: TestClient, seed) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": seed["username"], "password": PASSWORD, "tenant_code": "no-such-tenant"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 40102


def test_five_failures_lock_account_40103(client: TestClient, seed, db) -> None:
    """F-1.2：连续 5 次失败锁 10 分钟；第 5 次直接返回 40103。"""
    for _ in range(4):
        assert _login(client, seed, password="WrongPass123")["body"]["code"] == 40102

    fifth = _login(client, seed, password="WrongPass123")
    assert fifth["body"]["code"] == 40103
    assert fifth["body"]["data"]["locked_seconds"] > 0

    # 即便密码正确也进不去（锁定优先于校验）
    locked = _login(client, seed)
    assert locked["body"]["code"] == 40103

    # MySQL 是权威：锁定字段确实落库
    db.expire_all()
    user = db.get(SysUser, seed["user_id"])
    assert user is not None and user.locked_until is not None
    assert user.locked_until > datetime.now()


def test_login_success_resets_fail_counter(client: TestClient, seed, db) -> None:
    _login(client, seed, password="WrongPass123")
    _login(client, seed, password="WrongPass123")
    assert _login(client, seed)["body"]["code"] == 0

    db.expire_all()
    user = db.get(SysUser, seed["user_id"])
    assert user is not None
    assert user.login_fail_count == 0
    assert user.last_login_at is not None


# ============================================================================
# API-04 / API-05 当前用户与权限快照
# ============================================================================
def test_me_returns_current_user(client: TestClient, seed) -> None:
    token = _login(client, seed)["body"]["data"]["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert int(body["data"]["id"]) == seed["user_id"]
    assert body["data"]["roles"] == ["HR"]


def test_permissions_snapshot_contains_granted_codes(client: TestClient, seed) -> None:
    token = _login(client, seed)["body"]["data"]["access_token"]
    resp = client.get("/api/v1/auth/permissions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "talent:view" in data["perm_codes"]
    assert data["roles"] == ["HR"]
    assert data["is_admin"] is False


# ============================================================================
# API-02 刷新
# ============================================================================
def test_refresh_issues_new_access_token(client: TestClient, seed) -> None:
    tokens = _login(client, seed)["body"]["data"]
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"]
    # 🔴 刷新不延长 refresh 生命周期 —— 不返回新的 refresh_token
    assert "refresh_token" not in data

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.json()["code"] == 0


def test_refresh_with_access_token_is_rejected(client: TestClient, seed) -> None:
    """类型不符 → 40101（防止用 access_token 无限续期）。"""
    tokens = _login(client, seed)["body"]["data"]
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


# ============================================================================
# API-03 登出
# ============================================================================
def test_logout_revokes_current_token(client: TestClient, seed) -> None:
    tokens = _login(client, seed)["body"]["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert client.post("/api/v1/auth/logout", json={}, headers=headers).json()["code"] == 0
    # 再用同一 Token → 40101
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


def test_logout_is_idempotent(client: TestClient, seed) -> None:
    """登出重复调用也必须成功（幂等，避免前端重试报错）。"""
    tokens = _login(client, seed)["body"]["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/v1/auth/logout", json={}, headers=headers).json()["code"] == 0
    second = client.post("/api/v1/auth/logout", json={}, headers=headers)
    assert second.json()["code"] in (0, 40101)


# ============================================================================
# API-06 修改密码
# ============================================================================
def test_change_password_invalidates_old_sessions(client: TestClient, seed) -> None:
    tokens = _login(client, seed)["body"]["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = client.put(
        "/api/v1/auth/password",
        json={"old_password": PASSWORD, "new_password": "NewPass@2026"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"changed": True, "relogin_required": True}

    # 旧 Token 立即失效（F-1.2）
    assert client.get("/api/v1/auth/me", headers=headers).json()["code"] == 40101
    # 新密码可登录
    assert _login(client, seed, password="NewPass@2026")["body"]["code"] == 0
    # 旧密码不可登录
    assert _login(client, seed, password=PASSWORD)["body"]["code"] == 40102


def test_change_password_wrong_old_password(client: TestClient, seed) -> None:
    tokens = _login(client, seed)["body"]["data"]
    resp = client.put(
        "/api/v1/auth/password",
        json={"old_password": "WrongOld123", "new_password": "NewPass@2026"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 40102


def test_change_password_same_as_old_is_rejected(client: TestClient, seed) -> None:
    tokens = _login(client, seed)["body"]["data"]
    resp = client.put(
        "/api/v1/auth/password",
        json={"old_password": PASSWORD, "new_password": PASSWORD},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001
