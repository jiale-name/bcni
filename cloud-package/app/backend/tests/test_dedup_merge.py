"""CU-19 · 查重与合并（L2 集成，F-3.5 / SQ-4 / SM-1，API-57~61）。

端到端钉死四条 SQ-4 决策：
1. 精确键扫描 → 候选对（相似度 1.0）；
2. 合并：主档字段按「非空优先」合并 + 从档软删（``data_status=2``）+ 快照留痕；
3. 幂等：同一对重复合并 → **40901**；
4. 回滚：靠 ``snapshot`` 完整还原（从档 ``data_status`` 回有效、``is_deleted=0``、
   主档字段回退）—— 唯一可逆的「删除类」迁移（SM-1）。

⚠️ 向量侧（Milvus）**在本文件里被显式置为「不可用」** —— 用例内用 ``monkeypatch``
把 provider 换成 Keyword 降级实现（见 ``test_merge_soft_deletes_slave_and_applies_field_strategy``），
以**确定性地**验收「派生索引不影响权威状态」这一设计点。

🔴 **批次 17 修订（环境耦合）**：原文写的是「向量（Milvus）**在本环境**不可用 →
``vector_reindexed=False``」—— 那是**对当时环境的快照**。用户建好 Milvus 库
``talent_vec`` 之后，``get_retrieval_provider()`` 返回 ``MilvusRetrievalProvider``
⇒ ``_reindex`` 返回 ``True`` ⇒ **环境一变用例就红**（实测：全量回归 974 用例 / **1 失败**）。
⚠️ 它与 ``acceptance_check.py`` 的 ``S-09`` 属**同一类**「**判据是对当时世界的快照**」问题，
处置也同一路子：**把隐含的环境依赖改成显式、可复现的注入** ——
**而不是**把断言放松成恒真（那是把红点藏起来）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect as sa_inspect, select

from app.core.security import SecurityUtils
from app.models.dup_pair import TalentDupPair
from app.models.enums import ChangeSource, DupPairStatus, RoleDataScope, TalentDataStatus
from app.models.talent import (
    TalentBase,
    TalentChangeLog,
    TalentProjectExp,
    TalentTagRel,
    TalentWorkExp,
)
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.services.dedup_service import DedupService
from tests.conftest import db_available

PWD = "Admin@123456"
DEDUP_PERMS = ["dedup:list", "dedup:compare", "dedup:merge", "dedup:ignore", "dedup:rollback"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    """独立租户：ADM + NONE 两用户 + 两份共享 phone_hash 的「有效」档案。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("talent_dup_pair"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0006_dedup）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu19_{suffix}", tenant_name=f"CU19测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in DEDUP_PERMS:
        p = SysPermission(tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1)
        db.add(p)
        perms[code] = p
    db.flush()

    adm_role = SysRole(tenant_id=tid, role_code="ADM", role_name="ADM",
                       data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1)
    db.add(adm_role)
    db.flush()
    for c in DEDUP_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=adm_role.id, perm_id=perms[c].id))

    none_role = SysRole(tenant_id=tid, role_code="NONE", role_name="NONE",
                        data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1)
    db.add(none_role)
    db.flush()

    def _mk_user(name: str, r: SysRole) -> SysUser:
        user = SysUser(tenant_id=tid, username=name,
                       password_hash=SecurityUtils.hash_password(PWD), real_name=name,
                       status=1, login_fail_count=0)
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=r.id))
        return user

    adm = _mk_user(f"adm_{suffix}", adm_role)
    _mk_user(f"none_{suffix}", none_role)

    # 两份共享 phone_hash 的有效档案（精确键重复）
    talent_a = TalentBase(
        tenant_id=tid, name="张三", phone_hash="DEDUP_HASH_1", school=None,
        major="计算机", data_status=int(TalentDataStatus.ACTIVE),
    )
    talent_b = TalentBase(
        tenant_id=tid, name="张三丰", phone_hash="DEDUP_HASH_1", school="清华大学",
        major="计算机", data_status=int(TalentDataStatus.ACTIVE),
    )
    db.add_all([talent_a, talent_b])
    db.flush()
    db.add_all(
        [
            TalentWorkExp(
                tenant_id=tid,
                talent_id=talent_a.id,
                seq_no=1,
                company="同一科技",
                position_name="工程师",
                start_date="2020-01-01",
                end_date="2022-12-31",
                description="主档 AI 解析",
                source=0,
            ),
            TalentWorkExp(
                tenant_id=tid,
                talent_id=talent_b.id,
                seq_no=1,
                company="同一科技",
                position_name="工程师",
                start_date="2020-01-01",
                end_date="2022-12-31",
                description="人工确认版本",
                source=1,
            ),
            TalentWorkExp(
                tenant_id=tid,
                talent_id=talent_b.id,
                seq_no=2,
                company="另一科技",
                position_name="负责人",
                start_date="2023-01-01",
                end_date=None,
                is_current=1,
                description="从档唯一经历",
                source=0,
            ),
            TalentProjectExp(
                tenant_id=tid,
                talent_id=talent_a.id,
                seq_no=1,
                project_name="统一平台",
                role_name="开发",
                start_date="2021-01-01",
                end_date="2022-12-31",
                description="主档项目",
                tech_stack=["Python"],
                source=0,
            ),
            TalentProjectExp(
                tenant_id=tid,
                talent_id=talent_b.id,
                seq_no=1,
                project_name="统一平台",
                role_name="开发",
                start_date="2021-01-01",
                end_date="2022-12-31",
                description="人工项目",
                tech_stack=["Python", "MySQL"],
                source=1,
            ),
            TalentProjectExp(
                tenant_id=tid,
                talent_id=talent_b.id,
                seq_no=2,
                project_name="新项目",
                role_name="负责人",
                start_date="2023-01-01",
                end_date=None,
                description="从档唯一项目",
                tech_stack=["Go"],
                source=0,
            ),
        ]
    )
    db.flush()
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in {"adm": f"adm_{suffix}", "none": f"none_{suffix}"}.items():
        resp = client.post("/api/v1/auth/login",
                           json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code})
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {
        "client": client, "tokens": tokens, "tenant_id": tid,
        "talent_a": int(talent_a.id), "talent_b": int(talent_b.id),
        "user_id": int(adm.id),
    }

    from app.models.audit import AuditLog

    for model in (TalentWorkExp, TalentProjectExp, TalentDupPair, TalentTagRel, TalentChangeLog, TalentBase, AuditLog,
                  SysUserRole, SysRolePermission, SysPermission, SysRole, SysUser, Tenant):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()


# ============================================================================
# API-57 / 精确键扫描
# ============================================================================
def test_scan_creates_pair_and_list_sorts(env) -> None:
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        from app.core.tenant import CurrentPrincipal

        created = DedupService.scan_exact_duplicates(
            s, CurrentPrincipal(user_id=env["user_id"], tenant_id=env["tenant_id"],
                                org_id=None, roles=("ADM",))
        )
        assert created == 1
    finally:
        s.close()

    c, t = env["client"], env["tokens"]["adm"]
    r = c.get("/api/v1/talent/dedup", headers=_h(t))
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    items = r.json()["data"]
    assert len(items) == 1
    row = items[0]
    assert row["similarity"] == 1.0
    assert row["match_fields"] == ["phone_hash"]
    assert isinstance(row["pair_id"], str) and int(row["pair_id"]) > 2**53  # CR-5


def test_compare_returns_side_by_side(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    r = c.get(f"/api/v1/talent/dedup/{pair_id}", headers=_h(t))
    assert r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["talent_a"]["name"] == "张三" and d["talent_b"]["name"] == "张三丰"
    assert d["talent_a"]["school"] is None and d["talent_b"]["school"] == "清华大学"


# ============================================================================
# API-59 合并（幂等 + 软删 + 快照）
# ============================================================================
def test_merge_soft_deletes_slave_and_applies_field_strategy(env, monkeypatch) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    master = str(env["talent_a"])  # 主档 = 张三（school 为空）

    # 🔴 批次 17：**显式注入「向量不可用」**，不再依赖「本机恰好没建 Milvus 库」。
    # 生产 `merge` 用 `isinstance(provider, MilvusRetrievalProvider)` 判断走不走向量分支
    # （见 `app/services/dedup_service.py` 的 ``is_milvus``），故把它换成 Keyword 实现即可
    # **确定性地**走到「降级」路径 —— 无论本机 Milvus 是否已就绪，本用例结论都不变。
    from app.retrieval import KeywordRetrievalProvider
    from app.services import dedup_service

    monkeypatch.setattr(
        dedup_service, "get_retrieval_provider", lambda: KeywordRetrievalProvider()
    )

    r = c.post(f"/api/v1/talent/dedup/{pair_id}/merge", headers=_h(t),
               json={"master_id": master, "field_strategy": {}, "merge_tags": True})
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["master_id"] == master
    assert body["slave_id"] == str(env["talent_b"])
    # 向量不可用 → 降级为 False（**不是**合并失败）：派生索引不参与权威状态判定
    assert body["vector_reindexed"] is False

    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        master_row = s.get(TalentBase, env["talent_a"])
        slave_row = s.get(TalentBase, env["talent_b"])
        # 非空优先：主档 school 为空 → 取从档「清华大学」；name 主档非空 → 保留「张三」
        assert master_row.school == "清华大学" and master_row.name == "张三"
        # 从档软删（data_status=2 + is_deleted=1 + merged_into_id）
        assert slave_row.data_status == int(TalentDataStatus.MERGED)
        assert slave_row.is_deleted == 1
        assert slave_row.merged_into_id == env["talent_a"]
        # 快照留痕（TBL-22 change_source=2）
        logs = s.execute(select(TalentChangeLog).where(
            TalentChangeLog.tenant_id == env["tenant_id"],
            TalentChangeLog.change_source == int(ChangeSource.MERGE),
        )).scalars().all()
        assert len(logs) == 1 and logs[0].snapshot_ref == pair_id
        # pair 落合并态 + 快照
        pair = s.get(TalentDupPair, int(pair_id))
        assert int(pair.status) == int(DupPairStatus.MERGED)
        assert pair.snapshot["master_before"]["school"] is None
        work_rows = s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_a"],
            TalentWorkExp.is_deleted == 0,
        ).order_by(TalentWorkExp.seq_no)).scalars().all()
        assert len(work_rows) == 2
        assert work_rows[0].description == "从档唯一经历"
        assert work_rows[1].description == "人工确认版本"
        assert s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_b"],
            TalentWorkExp.is_deleted == 0,
        )).scalars().all() == []
        project_rows = s.execute(select(TalentProjectExp).where(
            TalentProjectExp.tenant_id == env["tenant_id"],
            TalentProjectExp.talent_id == env["talent_a"],
            TalentProjectExp.is_deleted == 0,
        )).scalars().all()
        assert len(project_rows) == 2
        assert any(row.description == "人工项目" for row in project_rows)
        assert pair.snapshot["detail_merged"] is True
        assert pair.snapshot["slave_work_exps_before"]
    finally:
        s.close()


def test_merge_same_pair_twice_is_40901(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    payload = {"master_id": str(env["talent_a"]), "field_strategy": {}}
    assert c.post(f"/api/v1/talent/dedup/{pair_id}/merge", headers=_h(t), json=payload).json()["code"] == 0
    r = c.post(f"/api/v1/talent/dedup/{pair_id}/merge", headers=_h(t), json=payload)
    assert r.json()["code"] == 40901, r.text


def test_merge_rejects_master_not_in_pair(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post(f"/api/v1/talent/dedup/{_pair_id(env)}/merge", headers=_h(t),
               json={"master_id": "7999999999999999999", "field_strategy": {}})
    assert r.json()["code"] == 40001, r.text


# ============================================================================
# API-61 回滚
# ============================================================================
def test_rollback_restores_both_sides(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    c.post(f"/api/v1/talent/dedup/{pair_id}/merge", headers=_h(t),
           json={"master_id": str(env["talent_a"]), "field_strategy": {}})

    r = c.post(f"/api/v1/talent/dedup/merges/{pair_id}/rollback", headers=_h(t))
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["slave_id"] == str(env["talent_b"])

    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        master_row = s.get(TalentBase, env["talent_a"])
        slave_row = s.get(TalentBase, env["talent_b"])
        # 主档字段回退（school 回到 None）
        assert master_row.school is None and master_row.name == "张三"
        # 从档还原（有效 + 未软删 + merged_into_id 清空）
        assert slave_row.data_status == int(TalentDataStatus.ACTIVE)
        assert slave_row.is_deleted == 0
        assert slave_row.merged_into_id is None
        master_work = s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_a"],
            TalentWorkExp.is_deleted == 0,
        )).scalars().all()
        slave_work = s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_b"],
            TalentWorkExp.is_deleted == 0,
        )).scalars().all()
        assert len(master_work) == 1 and master_work[0].description == "主档 AI 解析"
        assert {row.description for row in slave_work} == {"人工确认版本", "从档唯一经历"}
        master_project = s.execute(select(TalentProjectExp).where(
            TalentProjectExp.tenant_id == env["tenant_id"],
            TalentProjectExp.talent_id == env["talent_a"],
            TalentProjectExp.is_deleted == 0,
        )).scalars().all()
        assert len(master_project) == 1 and master_project[0].description == "主档项目"
        # pair 回到待处理 + 快照清空
        pair = s.get(TalentDupPair, int(pair_id))
        assert int(pair.status) == int(DupPairStatus.PENDING) and pair.snapshot is None
    finally:
        s.close()


def test_rollback_non_merged_is_illegal_state(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post(f"/api/v1/talent/dedup/merges/{_pair_id(env)}/rollback", headers=_h(t))
    assert r.json()["code"] == 42201, r.text  # 未合并 → 非法流转


def test_merge_detail_false_leaves_both_detail_sets_untouched(env, monkeypatch) -> None:
    _scan(env)
    from app.retrieval import KeywordRetrievalProvider
    from app.services import dedup_service

    monkeypatch.setattr(dedup_service, "get_retrieval_provider", lambda: KeywordRetrievalProvider())
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    response = c.post(
        f"/api/v1/talent/dedup/{pair_id}/merge",
        headers=_h(t),
        json={"master_id": str(env["talent_a"]), "field_strategy": {}, "merge_detail": False},
    )
    assert response.json()["code"] == 0, response.text

    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        master_rows = s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_a"],
            TalentWorkExp.is_deleted == 0,
        )).scalars().all()
        slave_rows = s.execute(select(TalentWorkExp).where(
            TalentWorkExp.tenant_id == env["tenant_id"],
            TalentWorkExp.talent_id == env["talent_b"],
            TalentWorkExp.is_deleted == 0,
        )).scalars().all()
        assert len(master_rows) == 1 and len(slave_rows) == 2
        pair = s.get(TalentDupPair, int(pair_id))
        assert pair.snapshot["detail_merged"] is False
    finally:
        s.close()


# ============================================================================
# API-60 忽略 / 权限
# ============================================================================
def test_ignore_marks_pair_ignored(env) -> None:
    _scan(env)
    c, t = env["client"], env["tokens"]["adm"]
    pair_id = _pair_id(env)
    r = c.post(f"/api/v1/talent/dedup/{pair_id}/ignore", headers=_h(t))
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == int(DupPairStatus.IGNORED)
    # 忽略后再合并 → 40901
    r2 = c.post(f"/api/v1/talent/dedup/{pair_id}/merge", headers=_h(t),
                json={"master_id": str(env["talent_a"]), "field_strategy": {}})
    assert r2.json()["code"] == 40901


def test_dedup_requires_permission(env) -> None:
    c = env["client"]
    assert c.get("/api/v1/talent/dedup").status_code == 401
    r = c.get("/api/v1/talent/dedup", headers=_h(env["tokens"]["none"]))
    assert r.json()["code"] == 40301, r.text


# ============================================================================
# 辅助
# ============================================================================
def _scan(env) -> None:
    from app.dao.session import SessionLocal

    from app.core.tenant import CurrentPrincipal

    s = SessionLocal()
    try:
        DedupService.scan_exact_duplicates(
            s, CurrentPrincipal(user_id=env["user_id"], tenant_id=env["tenant_id"],
                                org_id=None, roles=("ADM",))
        )
    finally:
        s.close()


def _pair_id(env) -> str:
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        pair = s.execute(select(TalentDupPair).where(
            TalentDupPair.tenant_id == env["tenant_id"]
        )).scalars().first()
        return str(pair.id)
    finally:
        s.close()
