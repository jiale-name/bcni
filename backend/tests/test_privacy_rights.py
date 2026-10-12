"""CU-22 后半 · 个人权利响应（L1 契约 + L2 集成，F-3.11 / SM-3 / SQ-5，API-71~80）。

L2 部分端到端验收**本单元风险最高的三件事**：

1. **归属校验**（API-71/72/75/78 只服务本人）—— 传别人的 ``talent_id`` 必须 **40301**；
   本人视角的定位只走 ``user_id`` 关联，没有任何「按 ID 查别人档案」的旁路；
2. **乐观锁与幂等**（API-72 的 ``current_value``、API-77 的 ``Idempotency-Key``）——
   前者防「基于旧值覆盖 HR 刚修好的值」，后者防「网络重试变成重复删除」；
3. **SQ-5 五步编排**（API-77）—— 向量清理与 MySQL 软删的顺序、失败时的回滚与
   「申请保持待处理」、``40902``（并发双审）与 ``40903``（幂等回放）的**判定顺序**。

⚠️ 本环境无 ``pymilvus`` → 检索走关键词降级 → ``vector_degraded=true`` 且
``vector_cleaned={}``，**删除仍然成功**（这正是「派生索引不阻断权威状态变更」的设计意图：
档案必须删得掉，否则删除权落空）。用例对两个分支都做了断言。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect as sa_inspect, select

from app.core.crypto import decrypt_sensitive, encrypt_sensitive, hash_contact
from app.core.security import SecurityUtils
from app.models.audit import AuditLog
from app.models.enums import ChangeSource, RoleDataScope, TalentDataStatus
from app.models.privacy import PrivacyRequest
from app.models.talent import TalentBase, TalentChangeLog
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.privacy import (
    CorrectionResolveRequest,
    CorrectionSubmitRequest,
    ExportSubmitRequest,
    PrivacyRequestFilter,
)
from tests.conftest import db_available

PWD = "Admin@123456"
EMP_PERMS = ["privacy:self_view", "privacy:correct", "privacy:delete", "privacy:export", "privacy:list"]
HR_PERMS = ["privacy:list", "privacy:correct", "privacy:delete", "privacy:export"]
ADM_PERMS = [*EMP_PERMS, "privacy:approve"]

PHONE = "13812345678"
EMAIL = "liwei@example.com"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约层（无需外部依赖）
# ============================================================================
def test_correction_request_rejects_non_whitelisted_field() -> None:
    """证件号等**不开放更正**的字段要在提交时就被拒，且**说明理由**（不是静默忽略）。"""
    with pytest.raises(ValueError) as exc:
        CorrectionSubmitRequest(
            talent_id=1, field_code="id_card", current_value="110101199003071234",
            expected_value="110101199003079999", reason="证件号填错了需要更正",
        )
    assert "证件号" in str(exc.value)


def test_correction_request_rejects_same_value() -> None:
    """``expected_value`` 与 ``current_value`` 相同 → 无需提交（防无意义工单塞满队列）。"""
    with pytest.raises(ValueError):
        CorrectionSubmitRequest(
            talent_id=1, field_code="school", current_value="  清华大学 ",
            expected_value="清华大学", reason="重复提交同一个值的更正申请",
        )


def test_correction_request_limits_attachments_and_reason() -> None:
    """佐证材料 ≤5；理由 10~500 字符（理由太短等于没写，HR 无法据此判断）。"""
    with pytest.raises(ValueError):
        CorrectionSubmitRequest(
            talent_id=1, field_code="school", current_value="A", expected_value="B",
            reason="理由足够长的更正申请描述", attachments=[str(i) for i in range(6)],
        )
    with pytest.raises(ValueError):
        CorrectionSubmitRequest(
            talent_id=1, field_code="school", current_value="A", expected_value="B", reason="太短",
        )


def test_reject_requires_reason() -> None:
    """🔴 驳回**必须**带理由（≥10 字符，SM-3 红线②）—— 不带理由的驳回等于无限重提的起点。"""
    with pytest.raises(ValueError):
        CorrectionResolveRequest(approved=False)
    with pytest.raises(ValueError):
        CorrectionResolveRequest(approved=False, reason="不行")
    ok = CorrectionResolveRequest(approved=False, reason="佐证材料与申请内容不一致，请补充后重提")
    assert ok.reason


def test_export_format_does_not_claim_pdf() -> None:
    """契约上**不宣称**支持 PDF（一期无 MinIO / 无 PDF 生成库）—— 比「收下再返回 JSON」诚实。"""
    with pytest.raises(ValueError):
        ExportSubmitRequest(talent_id=1, format="pdf")
    ExportSubmitRequest(talent_id=1, format="json")


def test_filter_rejects_reversed_range() -> None:
    now = datetime.now()
    with pytest.raises(ValueError):
        PrivacyRequestFilter(start_time=now, end_time=now.replace(year=now.year - 1))


def test_request_status_name_maps_processing_to_pending() -> None:
    """内部态 ``3`` 不外泄（秒级窗口里显示一个随后消失的状态会让人以为系统在重试）。"""
    from app.schemas.privacy import request_status_name_of

    assert request_status_name_of(3) == "待处理"


# ============================================================================
# L2 · 端到端（API-71~80）
# ============================================================================
@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    """独立租户：ADM / HR / EMP(有本人档案) / EMP2(无档案) / NONE。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("privacy_request"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0007_privacy）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu22p_{suffix}", tenant_name=f"CU22权利_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    all_perms = sorted(set(ADM_PERMS) | set(HR_PERMS))
    perms: dict[str, SysPermission] = {}
    for code in all_perms:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    def _role(code: str, scope: int) -> SysRole:
        role = SysRole(
            tenant_id=tid, role_code=code, role_name=code,
            data_scope=int(scope), is_builtin=1, status=1,
        )
        db.add(role)
        db.flush()
        return role

    adm_role = _role("ADM", RoleDataScope.ALL)
    hr_role = _role("HR", RoleDataScope.ALL)
    emp_role = _role("EMP", RoleDataScope.SELF)
    none_role = _role("NONE", RoleDataScope.SELF)

    for code in ADM_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=adm_role.id, perm_id=perms[code].id))
    for code in HR_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=hr_role.id, perm_id=perms[code].id))
    for code in EMP_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=emp_role.id, perm_id=perms[code].id))

    def _mk_user(name: str, r: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid, username=name, password_hash=SecurityUtils.hash_password(PWD),
            real_name=name, status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=r.id))
        return user

    adm = _mk_user(f"adm_{suffix}", adm_role)
    hr = _mk_user(f"hr_{suffix}", hr_role)
    emp = _mk_user(f"emp_{suffix}", emp_role)
    _mk_user(f"emp2_{suffix}", emp_role)  # 无关联档案
    _mk_user(f"none_{suffix}", none_role)

    talent = TalentBase(
        tenant_id=tid, user_id=int(emp.id), name="李伟", gender=1,
        birth_date=datetime(1998, 6, 15).date(), education_level="本科",
        school="清华大学", major="计算机", work_years=7,
        phone_enc=encrypt_sensitive(PHONE), phone_hash=hash_contact(PHONE, "phone"),
        email_enc=encrypt_sensitive(EMAIL),
        data_status=int(TalentDataStatus.ACTIVE),
    )
    db.add(talent)
    db.flush()
    # 一条既有留痕 → API-71 的 source 与 change_count 才有可断言的值
    db.add(
        TalentChangeLog(
            tenant_id=tid, talent_id=talent.id, change_source=int(ChangeSource.AI_PARSE),
            field_name="school", before_value=None, after_value="清华大学",
        )
    )
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in {
        "adm": f"adm_{suffix}", "hr": f"hr_{suffix}", "emp": f"emp_{suffix}",
        "emp2": f"emp2_{suffix}", "none": f"none_{suffix}",
    }.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {
        "client": client, "tokens": tokens, "tenant_id": tid, "talent_id": int(talent.id),
        "emp_user_id": int(emp.id), "hr_user_id": int(hr.id), "adm_user_id": int(adm.id),
    }

    for model in (
        AuditLog, PrivacyRequest, TalentChangeLog, TalentBase, SysUserRole,
        SysRolePermission, SysPermission, SysRole, SysUser, Tenant,
    ):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()


def _submit_correction(env, token: str, **over) -> dict:
    body = {
        "talent_id": str(env["talent_id"]),
        "field_code": "school",
        "current_value": "清华大学",
        "expected_value": "北京大学",
        "reason": "院校名称填写有误，已提供毕业证书佐证",
        "attachments": [],
    }
    body.update(over)
    resp = env["client"].post(
        "/api/v1/privacy/corrections", json=body, headers=_h(token)
    )
    return resp.json()


# ---------------------------------------------------------------- API-71
def test_api71_self_profile_never_exposes_id_card_plaintext(env) -> None:
    """API-71：本人视角 —— 手机/邮箱明文可见，**证件号只下发脱敏形态**。"""
    resp = env["client"].get("/api/v1/privacy/me/profile", headers=_h(env["tokens"]["emp"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["talent_id"] == str(env["talent_id"])
    assert data["phone"] == PHONE  # 本人可查自己的手机号
    assert data["email"] == EMAIL
    assert data["id_card_masked"] is None  # 本用例未填证件号
    assert "id_card" in [f["field_code"] for f in data["fields"]]
    assert "phone" in data["correction_field_codes"]
    # 证件号被排除、且**带着理由**下发（避免用户以为漏了）
    assert "id_card" in data["correction_excluded_reason"]
    assert data["change_count"] == 1
    # 字段来源来自留痕（talent_base 本身没有「来源」列）
    school = next(f for f in data["fields"] if f["field_code"] == "school")
    assert school["source"] is not None


def test_api71_without_linked_talent_is_40401(env) -> None:
    resp = env["client"].get("/api/v1/privacy/me/profile", headers=_h(env["tokens"]["emp2"]))
    assert resp.json()["code"] == 40401


def test_api71_requires_permission(env) -> None:
    resp = env["client"].get("/api/v1/privacy/me/profile", headers=_h(env["tokens"]["none"]))
    assert resp.json()["code"] == 40301


# ---------------------------------------------------------------- API-72 / 73 / 74
def test_api72_optimistic_lock_and_duplicate_guard(env) -> None:
    """乐观锁不一致 → 40902（带最新值）；同字段重复提交 → 40901。"""
    bad = _submit_correction(env, env["tokens"]["emp"], current_value="复旦大学")
    assert bad["code"] == 40902
    assert bad["data"]["latest_value"] == "清华大学"

    first = _submit_correction(env, env["tokens"]["emp"])
    assert first["code"] == 0
    assert first["data"]["status"] == 0
    assert first["data"]["status_name"] == "待处理"
    assert first["data"]["review_queue"] == "talent_review"
    assert first["data"]["sla_hours"] == 72

    dup = _submit_correction(env, env["tokens"]["emp"])
    assert dup["code"] == 40901


def test_api72_rejects_foreign_talent(env) -> None:
    """🔴 归属校验：EMP 提交**他人**档案的更正申请 → 40301（不是 40401）。"""
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        other = TalentBase(
            tenant_id=env["tenant_id"], name="别人", data_status=int(TalentDataStatus.ACTIVE)
        )
        s.add(other)
        s.commit()
        other_id = int(other.id)
    finally:
        s.close()

    body = _submit_correction(env, env["tokens"]["emp"], talent_id=str(other_id))
    assert body["code"] == 40301


def test_api73_hr_sees_and_api74_approve_applies_value(env) -> None:
    """完整闭环：提交 → HR 列表可见 → 采纳 → **档案真被改** + 留痕 + 复核条目收口。"""
    submitted = _submit_correction(env, env["tokens"]["emp"])
    request_id = submitted["data"]["request_id"]

    listed = env["client"].get(
        "/api/v1/privacy/corrections", headers=_h(env["tokens"]["hr"])
    ).json()
    assert listed["page"]["total"] == 1
    row = listed["data"][0]
    assert row["id"] == request_id
    assert row["request_type_name"] == "更正"
    assert row["field_label"] == "院校"
    assert row["applicant_user_id"] == str(env["emp_user_id"])
    assert row["talent_name"] == "李伟"
    assert row["overdue"] is False

    resolved = env["client"].post(
        f"/api/v1/privacy/corrections/{request_id}/resolve",
        json={"approved": True},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert resolved["code"] == 0, resolved
    data = resolved["data"]
    assert data["applied"] is True
    assert data["applied_value"] == "北京大学"
    assert data["change_log_id"]
    assert data["notified"] is True

    # 档案真的改了 + 留痕写了（F-3.10 与 F-3.11 的衔接点）
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        talent = s.get(TalentBase, env["talent_id"])
        assert talent.school == "北京大学"
        logs = (
            s.execute(
                select(TalentChangeLog)
                .where(TalentChangeLog.talent_id == env["talent_id"])
                .where(TalentChangeLog.field_name == "school")
            )
            .scalars()
            .all()
        )
        assert len(logs) == 2  # 原始导入 1 条 + 本次更正 1 条
        latest = [x for x in logs if x.before_value == "清华大学"][0]
        assert latest.after_value == "北京大学"
        assert int(latest.change_source) == int(ChangeSource.MANUAL_REVIEW)
    finally:
        s.close()


def test_api74_phone_correction_recomputes_enc_and_hash_and_masks_log(env) -> None:
    """🔴 手机号更正必须**同时重算** ``phone_enc`` 与 ``phone_hash``；留痕只写**脱敏**形态。"""
    new_phone = "13900001111"
    submitted = _submit_correction(
        env, env["tokens"]["emp"],
        field_code="phone", current_value=PHONE, expected_value=new_phone,
    )
    assert submitted["code"] == 0, submitted

    resolved = env["client"].post(
        f"/api/v1/privacy/corrections/{submitted['data']['request_id']}/resolve",
        json={"approved": True},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert resolved["code"] == 0, resolved

    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        talent = s.get(TalentBase, env["talent_id"])
        assert decrypt_sensitive(talent.phone_enc) == new_phone  # 密文换了
        assert talent.phone_hash == hash_contact(new_phone, "phone")  # 哈希也换了
    finally:
        s.close()

    # 留痕里**不能**出现明文手机号（加密列的意义不能被留痕抹掉）：
    # 按字段筛出来的变更链，当前值与链上值**同为脱敏形态**（才能逐行对照）
    history = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/fields/phone/history",
        headers=_h(env["tokens"]["adm"]),
    ).json()
    assert history["code"] == 0, history
    chain = history["data"]
    assert chain["total"] == 1
    assert new_phone not in json.dumps(chain, ensure_ascii=False)
    assert PHONE not in json.dumps(chain, ensure_ascii=False)
    assert chain["current_value"] == "139****1111"
    assert chain["items"][0]["before_value"] == "138****5678"
    assert chain["items"][0]["after_value"] == "139****1111"


def test_change_vocabulary_covers_phone_but_snapshot_never_returns_it(env) -> None:
    """🔴 「能被筛选」与「能被回溯」是两件事：手机号在筛选词表里，但快照**不下发**它。"""
    from app.schemas.change import CHANGE_FIELD_CODES
    from app.core.privacy import TRACKABLE_FIELDS

    assert "phone" in CHANGE_FIELD_CODES and "email" in CHANGE_FIELD_CODES
    # 但 PII 不参与回溯（快照出参里没有 PII 列）
    assert "phone" not in TRACKABLE_FIELDS and "email" not in TRACKABLE_FIELDS

    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snap = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/snapshot",
        params={"at": at},
        headers=_h(env["tokens"]["adm"]),
    ).json()
    assert snap["code"] == 0, snap
    dumped = json.dumps(snap["data"], ensure_ascii=False)
    assert PHONE not in dumped and EMAIL not in dumped


def test_api74_reject_sets_reason_and_terminates(env) -> None:
    """驳回：终态是「已驳回」而**不是**回到待处理（否则无限重提），且必须带理由。"""
    submitted = _submit_correction(env, env["tokens"]["emp"])
    request_id = submitted["data"]["request_id"]

    no_reason = env["client"].post(
        f"/api/v1/privacy/corrections/{request_id}/resolve",
        json={"approved": False},
        headers=_h(env["tokens"]["hr"]),
    )
    assert no_reason.json()["code"] == 40001  # 契约层拦住

    rejected = env["client"].post(
        f"/api/v1/privacy/corrections/{request_id}/resolve",
        json={"approved": False, "reason": "佐证材料无法辨认，请重新上传后再次提交申请"},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert rejected["code"] == 0
    assert rejected["data"]["status"] == 2
    assert rejected["data"]["applied"] is False

    # 再处理一次 → 40902（已被处理），且 data 带处理人
    again = env["client"].post(
        f"/api/v1/privacy/corrections/{request_id}/resolve",
        json={"approved": True},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert again["code"] == 40902
    assert again["data"]["processed_by_name"]

    # 驳回后同字段可以重新提交（不构成 40901）
    resubmit = _submit_correction(env, env["tokens"]["emp"])
    assert resubmit["code"] == 0


def test_api74_requires_manager_role(env) -> None:
    """🔴 ``privacy:correct`` 与「提交」共码 → EMP 若只靠权限点就能审批自己的申请。"""
    submitted = _submit_correction(env, env["tokens"]["emp"])
    resp = env["client"].post(
        f"/api/v1/privacy/corrections/{submitted['data']['request_id']}/resolve",
        json={"approved": True},
        headers=_h(env["tokens"]["emp"]),
    )
    assert resp.json()["code"] == 40301


# ---------------------------------------------------------------- API-75 / 76 / 77
def test_api77_delete_five_step_orchestration(env) -> None:
    """🔴 SQ-5 五步：先删向量 → 成功才软删 MySQL → 写留痕 → 最后置已通过。"""
    submitted = env["client"].post(
        "/api/v1/privacy/deletions",
        json={"talent_id": str(env["talent_id"]), "reason": "本人不再使用本平台，申请删除全部档案数据"},
        headers=_h(env["tokens"]["emp"]),
    ).json()
    assert submitted["code"] == 0, submitted
    request_id = submitted["data"]["request_id"]

    # HR 无权审批删除（privacy:approve 只授 ADM）
    forbidden = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve",
        json={"approved": True},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert forbidden["code"] == 40301

    approved = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve",
        json={"approved": True, "reason": "已核实本人意愿"},
        headers={"Idempotency-Key": f"del-{uuid.uuid4().hex}", **_h(env["tokens"]["adm"])},
    ).json()
    assert approved["code"] == 0, approved
    data = approved["data"]
    assert data["status"] == 1
    assert data["talent_soft_deleted"] is True
    assert data["change_log_id"]
    assert data["notified"] is True
    if data["vector_degraded"]:
        # 降级环境：没有向量库可清理 → 不阻断删除，但**如实标记**
        assert data["vector_cleaned"] == {}
    else:
        assert set(data["vector_cleaned"]) == {
            "talent_skill", "talent_experience", "talent_quality"
        }

    # 档案：软删 + data_status=4 + 留痕
    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        talent = s.get(TalentBase, env["talent_id"])
        assert int(talent.is_deleted) == 1
        assert int(talent.data_status) == int(TalentDataStatus.DELETED)
        logs = (
            s.execute(
                select(TalentChangeLog)
                .where(TalentChangeLog.talent_id == env["talent_id"])
                .where(TalentChangeLog.field_name == "data_status")
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1
        assert logs[0].after_value == "4"
    finally:
        s.close()


def test_api77_idempotent_replay_returns_first_result(env) -> None:
    """🔴 ``Idempotency-Key``：重复提交 → **40903 + 首次结果**（前端按成功继续），不是 40902。"""
    submitted = env["client"].post(
        "/api/v1/privacy/deletions",
        json={"talent_id": str(env["talent_id"]), "reason": "申请删除本人档案数据，请审批处理"},
        headers=_h(env["tokens"]["emp"]),
    ).json()
    request_id = submitted["data"]["request_id"]
    key = f"del-{uuid.uuid4().hex}"
    headers = {"Idempotency-Key": key, **_h(env["tokens"]["adm"])}

    first = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve", json={"approved": True}, headers=headers
    ).json()
    assert first["code"] == 0

    replay = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve", json={"approved": True}, headers=headers
    ).json()
    assert replay["code"] == 40903, replay
    assert replay["data"]["idempotent_key"] == key
    assert replay["data"]["talent_soft_deleted"] is True

    # ⚠️ 幂等回放**先于**状态判定：不带幂等键的重复审批才是 40902
    conflict = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve",
        json={"approved": True},
        headers=_h(env["tokens"]["adm"]),
    ).json()
    assert conflict["code"] == 40902


def test_api77_reject_does_not_touch_data(env) -> None:
    """驳回删除申请**不改动任何数据**（因为什么都没做）—— ``vector_cleaned`` 为空。"""
    submitted = env["client"].post(
        "/api/v1/privacy/deletions",
        json={"talent_id": str(env["talent_id"]), "reason": "希望删除本人全部档案与画像数据"},
        headers=_h(env["tokens"]["emp"]),
    ).json()
    request_id = submitted["data"]["request_id"]
    rejected = env["client"].post(
        f"/api/v1/privacy/deletions/{request_id}/approve",
        json={"approved": False, "reason": "尚在职期间档案需保留，离职后可再次申请"},
        headers=_h(env["tokens"]["adm"]),
    ).json()
    assert rejected["code"] == 0
    assert rejected["data"]["status"] == 2
    assert rejected["data"]["talent_soft_deleted"] is False
    assert rejected["data"]["change_log_id"] is None

    from app.dao.session import SessionLocal

    s = SessionLocal()
    try:
        talent = s.get(TalentBase, env["talent_id"])
        assert int(talent.is_deleted) == 0
        assert int(talent.data_status) == int(TalentDataStatus.ACTIVE)
    finally:
        s.close()


def test_api76_list_deletions_rejects_foreign_view_for_emp(env) -> None:
    """EMP 调列表接口只会看到**自己的**申请（传别人的 applicant_user_id 无效）。"""
    other_id = env["adm_user_id"]
    resp = env["client"].get(
        "/api/v1/privacy/deletions",
        params={"applicant_user_id": str(other_id)},
        headers=_h(env["tokens"]["emp"]),
    ).json()
    assert resp["code"] == 0
    assert resp["page"]["total"] == 0  # 被强制收窄为本人 → 本人没有删除申请


# ---------------------------------------------------------------- API-78 / 79
def test_api78_submit_then_api79_download_twice(env) -> None:
    """导出：提交即完成 → 本人可下载（内容可解析）→ 下载次数**原子自增**。"""
    submitted = env["client"].post(
        "/api/v1/privacy/exports",
        json={"talent_id": str(env["talent_id"]), "format": "json"},
        headers=_h(env["tokens"]["emp"]),
    ).json()
    assert submitted["code"] == 0, submitted
    data = submitted["data"]
    assert data["status"] == 1 and data["status_name"] == "已通过"
    request_id = data["request_id"]
    assert data["download_url"].endswith(f"/privacy/exports/{request_id}/download")

    first = env["client"].get(
        f"/api/v1/privacy/exports/{request_id}/download", headers=_h(env["tokens"]["emp"])
    ).json()
    assert first["code"] == 0, first
    assert first["data"]["download_count"] == 1
    payload = json.loads(first["data"]["content"])
    assert payload["profile"]["name"] == "李伟"
    assert payload["profile"]["phone"] == PHONE
    assert payload["export_meta"]["request_no"] == submitted["data"]["request_no"]
    # 数据包里**没有**完整的证件号字段（只有脱敏形态）
    assert "id_card" not in payload["profile"]

    second = env["client"].get(
        f"/api/v1/privacy/exports/{request_id}/download", headers=_h(env["tokens"]["emp"])
    ).json()
    assert second["data"]["download_count"] == 2

    # 另一名 EMP 不能下载别人的数据包（越权与不存在同码）
    other = env["client"].get(
        f"/api/v1/privacy/exports/{request_id}/download", headers=_h(env["tokens"]["emp2"])
    ).json()
    assert other["code"] == 40301


def test_api79_rejects_non_export_request(env) -> None:
    """把更正申请 ID 传给下载接口 → 40001（不能靠猜 ID 下载到别的单子的内容）。"""
    submitted = _submit_correction(env, env["tokens"]["emp"])
    resp = env["client"].get(
        f"/api/v1/privacy/exports/{submitted['data']['request_id']}/download",
        headers=_h(env["tokens"]["emp"]),
    ).json()
    assert resp["code"] == 40001


# ---------------------------------------------------------------- API-80
def test_api80_ledger_summary_scope_and_by_type(env) -> None:
    """台账：``data`` 是对象（items + summary）；统计**分类型**给出（导出拉低平均值）。"""
    _submit_correction(env, env["tokens"]["emp"])
    env["client"].post(
        "/api/v1/privacy/exports",
        json={"talent_id": str(env["talent_id"]), "format": "json"},
        headers=_h(env["tokens"]["emp"]),
    )

    resp = env["client"].get("/api/v1/privacy/requests", headers=_h(env["tokens"]["hr"])).json()
    assert resp["code"] == 0, resp
    assert resp["page"]["total"] == 2
    out = resp["data"]
    assert set(out.keys()) == {"items", "summary"}
    summary = out["summary"]
    assert summary["total"] == 2
    assert summary["scope"] == "tenant"
    assert summary["sla_hours"] == 72
    assert summary["by_type"], "必须分类型给出（跨类型平均值会被即时完成导出拉低）"
    types = {t["request_type"] for t in summary["by_type"]}
    assert types == {0, 2}  # 更正 + 导出

    # 非管理者视角：范围标记为 self（只能看到自己提交的）
    emp_view = env["client"].get(
        "/api/v1/privacy/requests", headers=_h(env["tokens"]["emp"])
    ).json()
    assert emp_view["data"]["summary"]["scope"] == "self"


def test_api80_overdue_only_filter(env) -> None:
    """``overdue_only`` 是「待办视图」：只返回**当前**超时（未终态）的单子。"""
    _submit_correction(env, env["tokens"]["emp"])
    not_overdue = env["client"].get(
        "/api/v1/privacy/requests",
        params={"overdue_only": "true"},
        headers=_h(env["tokens"]["hr"]),
    ).json()
    assert not_overdue["data"]["summary"]["overdue"] == 0
    assert not_overdue["page"]["total"] == 0
    # 不加过滤时同一批数据仍然返回
    all_rows = env["client"].get(
        "/api/v1/privacy/requests", headers=_h(env["tokens"]["hr"])
    ).json()
    assert all_rows["page"]["total"] == 1
