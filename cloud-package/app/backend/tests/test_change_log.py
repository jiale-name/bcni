"""CU-22 前半 · 变更留痕与时间点回溯（L1 纯函数 + L2 集成，F-3.10，API-67~70）。

L1 部分（不需要任何外部依赖，**永远执行**）钉死本单元最重要的两件确定性结论：

1. **时间点快照的回退算法**（:func:`~app.core.privacy.build_snapshot`）——
   它是「档案在某天长什么样」的**合规证据**。这些用例把「T 之后最早那条变更的
   ``before_value``」这一等价关系逐条钉在具体时间点上，而不是只测「函数不报错」；
2. **处理时限统计的三条口径**（:func:`~app.core.privacy.summarize_requests`）——
   这些数字要写进对外合规报告，口径漂移等同于数据失真。

L2 部分（``@requires_db``）端到端走一遍 API-67~70，验收四件事：
词汇表校验（拼错 → 40001 而非静默空列表）、回溯早于建档 → 40001、
``untracked_fields`` 是声明而非错误、域外访问 40301。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect as sa_inspect

from app.core.privacy import (
    CORRECTABLE_FIELDS,
    ChangeRecord,
    RequestRecord,
    build_notify_message,
    build_request_no,
    build_snapshot,
    coerce_corrected_value,
    get_correctable,
    is_overdue,
    render_value,
    summarize_requests,
)
from app.core.security import SecurityUtils
from app.models.enums import (
    ChangeSource,
    PrivacyRequestStatus,
    PrivacyRequestType,
    RoleDataScope,
    TalentDataStatus,
)
from app.models.talent import TalentBase, TalentChangeLog
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.change import CHANGE_FIELD_CODES, ChangeFilterRequest
from app.schemas.privacy import request_status_name_of
from tests.conftest import db_available

PWD = "Admin@123456"
CHANGE_PERMS = ["change:list", "change:chain", "change:snapshot", "change:field"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 时间点快照的回退算法
# ============================================================================
#: 基准「现在」——故意用**固定时刻**而不是 ``datetime.now()``：
#: 回溯用例的每个断言都在比时间点，「当前时间」参与进来会让用例在
#: 跨天/跨时区执行时出现无法复现的偶发失败。
BASE = datetime(2026, 9, 23, 10, 0, 0)


def _record(log_id: int, days_ago: float, field: str | None, before, after) -> ChangeRecord:
    return ChangeRecord(
        log_id=log_id,
        created_at=BASE - timedelta(days=days_ago),
        field_name=field,
        before_value=before,
        after_value=after,
        change_source=int(ChangeSource.MANUAL_REVIEW),
    )


def _history() -> list[ChangeRecord]:
    """一条典型档案的留痕（升序）：

    - 60 天前：工作年限 5 → 6
    - 30 天前：院校 空 → 清华大学
    - 7 天前：工作年限 6 → 7
    """
    return [
        _record(1, 60, "work_years", "5", "6"),
        _record(2, 30, "school", None, "清华大学"),
        _record(3, 7, "work_years", "6", "7"),
    ]


CURRENT = {"name": "李伟", "work_years": 7, "school": "清华大学", "major": "计算机"}
CREATED_AT = BASE - timedelta(days=90)


def test_snapshot_rolls_back_to_oldest_change_after_at() -> None:
    """T 之后的变更**逐条回退**，等价于「T 之后最早那条变更的前值」。"""
    result = build_snapshot(
        current_values=CURRENT,
        records=_history(),
        at=BASE - timedelta(days=15),  # 在「30 天前」与「7 天前」之间
        created_at=CREATED_AT,
    )
    # 工作年限：T 之后只有 7 天前那条（6→7）→ 取它的前值 6
    assert result.values["work_years"] == 6
    # 院校：T 之后没有变更 → 取最后一条的后值（清华大学）
    assert result.values["school"] == "清华大学"
    # 相对当前值被回退过的字段只有 work_years
    assert result.restored_fields == ["work_years"]
    assert result.changes_after == 1


def test_snapshot_at_before_all_changes_falls_back_to_creation() -> None:
    """T 早于全部留痕 → 所有有留痕的字段取「第一次变更的前值」，生效起点 = 建档时间。"""
    result = build_snapshot(
        current_values=CURRENT,
        records=_history(),
        at=BASE - timedelta(days=80),
        created_at=CREATED_AT,
    )
    assert result.values["work_years"] == 5
    assert result.values["school"] is None
    assert result.effective_from == CREATED_AT
    assert result.changes_after == 3
    assert result.snapshot_from_change_log_id is None


def test_snapshot_effective_window_and_last_log_id() -> None:
    """``effective_from`` / ``effective_to`` / ``snapshot_from_change_log_id`` 必须闭合。"""
    result = build_snapshot(
        current_values=CURRENT,
        records=_history(),
        at=BASE - timedelta(days=15),
        created_at=CREATED_AT,
    )
    assert result.effective_from == BASE - timedelta(days=30)  # 30 天前那条
    assert result.effective_to == BASE - timedelta(days=7)  # 7 天前那条
    assert result.snapshot_from_change_log_id == 2  # 时间点之前的最后一条留痕


def test_snapshot_untracked_fields_is_declaration_not_error() -> None:
    """无留痕字段 → 取当前值并**列入 untracked_fields**（接口原文要求，不是错误）。"""
    result = build_snapshot(
        current_values=CURRENT,
        records=_history(),
        at=BASE - timedelta(days=15),
        created_at=CREATED_AT,
    )
    # major 全程无留痕 → 声明历史不完整 + 取当前值
    assert "major" in result.untracked_fields
    assert result.values["major"] == "计算机"
    # 有留痕的字段不应出现在 untracked 里
    assert "work_years" not in result.untracked_fields


def test_snapshot_unknown_field_code_is_reported_not_crashed() -> None:
    """未知字段（如 PII 密文列）不参与回溯，但必须**显式回报**而不是静默忽略。"""
    result = build_snapshot(
        current_values=CURRENT,
        records=_history(),
        at=BASE - timedelta(days=15),
        created_at=CREATED_AT,
        field_codes=["work_years", "phone_enc"],
    )
    assert result.values["phone_enc"] is None
    assert "phone_enc" in result.untracked_fields
    assert result.values["work_years"] == 6


def test_snapshot_tolerates_dirty_history_value() -> None:
    """留痕里的脏值（历史版本写入的中文状态）→ ``None``，**不抛错**。

    留痕是 10⁷ 量级的历史数据，允许存在早期版本写入的非规范值。一条脏值
    让整次回溯 500，等于「因为历史不完美而无法证明历史」。
    """
    records = [_record(1, 1, "data_status", "有效", "已删除")]
    result = build_snapshot(
        current_values={"data_status": 1},
        records=records,
        at=BASE + timedelta(days=1),
        created_at=CREATED_AT,
    )
    # 取「最后一条的后值」= "已删除"，转 int 失败 → None（而不是抛异常）
    assert result.values["data_status"] is None


def test_snapshot_parses_clean_history_value() -> None:
    """同一路径下的**规范**值必须正常还原 —— 上一条用例的兜底不能掩盖正常情况。"""
    records = [_record(1, 1, "data_status", "0", "4")]
    result = build_snapshot(
        current_values={"data_status": 4},
        records=records,
        at=BASE + timedelta(days=1),
        created_at=CREATED_AT,
    )
    assert result.values["data_status"] == 4


def test_snapshot_does_not_write_anything() -> None:
    """回溯是**纯函数**：输入不被修改（合规证据不能有副作用）。"""
    records = _history()
    snapshot_before = [(r.log_id, r.before_value, r.after_value) for r in records]
    current = dict(CURRENT)
    build_snapshot(
        current_values=current, records=records, at=BASE - timedelta(days=15), created_at=CREATED_AT
    )
    assert [(r.log_id, r.before_value, r.after_value) for r in records] == snapshot_before
    assert current == CURRENT


# ============================================================================
# L1 · 处理时限统计的三条口径
# ============================================================================
def _req(
    rtype: int, status: int, hours_ago: float, sla: int = 72, processed_hours_ago: float | None = None
) -> RequestRecord:
    return RequestRecord(
        request_type=int(rtype),
        status=int(status),
        created_at=BASE - timedelta(hours=hours_ago),
        sla_hours=sla,
        processed_at=(
            BASE - timedelta(hours=processed_hours_ago) if processed_hours_ago is not None else None
        ),
    )


def test_summarize_counts_overdue_only_for_open_requests() -> None:
    """口径 ①：**超时只算未终态**；已终态的单子即便当时超时也不再计为超时。"""
    records = [
        # 已通过，但处理用了 100h > 72h：**不计超时**
        _req(PrivacyRequestType.CORRECTION, PrivacyRequestStatus.APPROVED, 100, processed_hours_ago=0),
        # 待处理，已等 100h > 72h：计超时
        _req(PrivacyRequestType.DELETION, PrivacyRequestStatus.PENDING, 100),
        # 已驳回：不计超时
        _req(PrivacyRequestType.EXPORT, PrivacyRequestStatus.REJECTED, 200, processed_hours_ago=190),
    ]
    summary = summarize_requests(records, now=BASE, sla_hours=72)
    assert summary.total == 3
    assert summary.overdue == 1
    assert summary.approved == 1 and summary.rejected == 1 and summary.pending == 1


def test_summarize_avg_and_max_only_over_terminal_requests() -> None:
    """口径 ②：平均/最长处理时长**只统计终态**（未终态没有「处理时长」这个量）。"""
    records = [
        _req(PrivacyRequestType.CORRECTION, PrivacyRequestStatus.APPROVED, 10, processed_hours_ago=0),
        _req(PrivacyRequestType.DELETION, PrivacyRequestStatus.REJECTED, 4, processed_hours_ago=0),
        _req(PrivacyRequestType.EXPORT, PrivacyRequestStatus.PENDING, 500),  # 不参与时长统计
    ]
    summary = summarize_requests(records, now=BASE, sla_hours=72)
    assert summary.avg_processing_hours == 7.0  # (10 + 4) / 2
    assert summary.max_processing_hours == 10.0


def test_summarize_uses_per_request_sla_snapshot() -> None:
    """口径 ③：判超时用**该单自己提交时的时限快照**，不是当前配置。

    否则改一次配置会让历史单据的判定集体反转 —— 一份已经出过的合规报告
    会变得无法复核。
    """
    records = [
        _req(PrivacyRequestType.CORRECTION, PrivacyRequestStatus.PENDING, 50, sla=24),  # 超
        _req(PrivacyRequestType.DELETION, PrivacyRequestStatus.PENDING, 50, sla=720),  # 不超
    ]
    # 当前配置无论改成多少，两单的判定都不应变
    for configured in (1, 72, 10_000):
        summary = summarize_requests(records, now=BASE, sla_hours=configured)
        assert summary.overdue == 1, f"sla_hours={configured} 时口径漂移了"


def test_summarize_keeps_processing_separate_from_pending() -> None:
    """``3 处理中`` 是内部态：与 ``待处理`` **分列**（便于运维发现卡住的单子）。"""
    records = [
        _req(PrivacyRequestType.DELETION, PrivacyRequestStatus.PENDING, 1),
        _req(PrivacyRequestType.DELETION, PrivacyRequestStatus.PROCESSING, 200),
    ]
    summary = summarize_requests(records, now=BASE, sla_hours=72)
    assert summary.pending == 1
    assert summary.processing == 1
    # 处理中同样按「未终态」计超时
    assert summary.overdue == 1


def test_summarize_by_type_is_independent() -> None:
    """分类型统计（导出即时完成会把跨类型平均值拉低，故分类型才是合规口径）。"""
    records = [
        _req(PrivacyRequestType.EXPORT, PrivacyRequestStatus.APPROVED, 0, processed_hours_ago=0),
        _req(PrivacyRequestType.CORRECTION, PrivacyRequestStatus.APPROVED, 40, processed_hours_ago=0),
    ]
    summary = summarize_requests(
        records,
        now=BASE,
        sla_hours=72,
        type_names={0: "更正", 1: "删除", 2: "导出"},
    )
    assert summary.avg_processing_hours == 20.0  # 跨类型被拉低
    by = {t.request_type: t for t in summary.by_type}
    assert by[2].avg_processing_hours == 0.0
    assert by[0].avg_processing_hours == 40.0
    assert by[0].request_type_name == "更正"


def test_is_overdue_terminal_is_never_overdue() -> None:
    """行内超时标记的用途是「提醒现在该处理谁」，不是「记过」。"""
    assert (
        is_overdue(
            status=int(PrivacyRequestStatus.APPROVED),
            created_at=BASE - timedelta(hours=1000),
            sla_hours=72,
            now=BASE,
            processed_at=BASE - timedelta(hours=900),
        )
        is False
    )
    assert (
        is_overdue(
            status=int(PrivacyRequestStatus.PENDING),
            created_at=BASE - timedelta(hours=100),
            sla_hours=72,
            now=BASE,
        )
        is True
    )


# ============================================================================
# L1 · 可更正字段白名单与值转换
# ============================================================================
def test_correctable_whitelist_excludes_derived_and_unique_key_fields() -> None:
    """白名单的排除项是「刻意」而不是「漏了」—— 每一条都要有理由可查。"""
    assert get_correctable("id_card") is None
    assert get_correctable("data_status") is None
    assert get_correctable("version_no") is None
    assert get_correctable("profile_vec_id") is None
    # 联系方式在（且必须标记为加密列，否则更正文不会重算 *_enc / *_hash）
    assert CORRECTABLE_FIELDS["phone"].encrypted is True
    assert CORRECTABLE_FIELDS["phone"].column == "phone_enc"
    assert CORRECTABLE_FIELDS["email"].encrypted is True
    assert CORRECTABLE_FIELDS["name"].encrypted is False


def test_coerce_roundtrip_is_inverse_of_render() -> None:
    """``coerce_corrected_value`` 与 ``render_value`` 必须互为逆运算。

    否则会出现「本人填的值与库里同值却判为不一致」—— 乐观锁会把人挡在门外。
    """
    cases = [("work_years", "7"), ("gender", "1"), ("birth_date", "1998-06-15")]
    for code, raw in cases:
        field_def = CORRECTABLE_FIELDS[code]
        coerced = coerce_corrected_value(field_def, raw)
        assert render_value(coerced) == raw, f"{code} 往返不一致"


def test_coerce_rejects_bad_values_before_write() -> None:
    """类型转换失败必须**在写库之前**变成 ValueError。

    MySQL 非严格模式下 ``work_years="五年"`` 会**静默截断成 0** ——
    「更正」反而把数据改坏了，且没有任何报错。
    """
    with pytest.raises(ValueError):
        coerce_corrected_value(CORRECTABLE_FIELDS["work_years"], "五年")
    with pytest.raises(ValueError):
        coerce_corrected_value(CORRECTABLE_FIELDS["name"], "   ")
    with pytest.raises(ValueError):
        coerce_corrected_value(CORRECTABLE_FIELDS["birth_date"], "1998/13/45")
    # 合法的日期变体仍应接受
    assert render_value(coerce_corrected_value(CORRECTABLE_FIELDS["birth_date"], "1998/6/15")) == (
        "1998-06-15"
    )


def test_request_no_and_notify_message() -> None:
    """请求号格式 + 驳回通知**必须带理由**（SM-3 红线②）。"""
    assert build_request_no("20260923", 7) == "PRV20260923007"
    rejected = build_notify_message(
        request_type=int(PrivacyRequestType.CORRECTION),
        request_no="PRV20260923007",
        status=int(PrivacyRequestStatus.REJECTED),
        reject_reason="佐证材料无法辨认",
    )
    assert "佐证材料无法辨认" in rejected
    # 没给理由时不能悄悄生成一条「无理由驳回」
    assert "未说明" in build_notify_message(
        request_type=int(PrivacyRequestType.DELETION),
        request_no="PRV20260923008",
        status=int(PrivacyRequestStatus.REJECTED),
    )


def test_processing_status_is_not_exposed() -> None:
    """``3 处理中`` 对用户映射为「待处理」（秒级内部态不外泄）。"""
    assert request_status_name_of(int(PrivacyRequestStatus.PROCESSING)) == "待处理"
    assert request_status_name_of(int(PrivacyRequestStatus.PENDING)) == "待处理"
    assert request_status_name_of(int(PrivacyRequestStatus.APPROVED)) == "已通过"


def test_change_field_vocabulary_is_controlled() -> None:
    """``field_name`` 受控：拼错一个字母要在**入口**被拒（静默空列表是最难发现的问题）。"""
    with pytest.raises(ValueError):
        ChangeFilterRequest(field_name="wrk_years")
    ChangeFilterRequest(field_name="work_years")
    assert "work_years" in CHANGE_FIELD_CODES and "data_status" in CHANGE_FIELD_CODES


def test_change_filter_rejects_oversized_window() -> None:
    """时间窗上限 90 天（留痕 10⁷ 量级，不设上限会退化为全租户扫描）。"""
    with pytest.raises(ValueError):
        ChangeFilterRequest(start_time=BASE - timedelta(days=200), end_time=BASE)
    ChangeFilterRequest(start_time=BASE - timedelta(days=30), end_time=BASE)


# ============================================================================
# L2 · 端到端（API-67~70）
# ============================================================================
@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    """独立租户：ADM（有 change:*）+ NONE（无权限）+ 一份带 3 条留痕的档案。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("talent_change_log"):
        pytest.skip("数据库已连通但未执行迁移")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu22c_{suffix}", tenant_name=f"CU22变更_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in CHANGE_PERMS:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    adm_role = SysRole(
        tenant_id=tid, role_code="ADM", role_name="ADM",
        data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
    )
    none_role = SysRole(
        tenant_id=tid, role_code="NONE", role_name="NONE",
        data_scope=int(RoleDataScope.ALL), is_builtin=1, status=1,
    )
    db.add_all([adm_role, none_role])
    db.flush()
    for c in CHANGE_PERMS:
        db.add(SysRolePermission(tenant_id=tid, role_id=adm_role.id, perm_id=perms[c].id))

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
    _mk_user(f"none_{suffix}", none_role)

    now = datetime.now()
    # 🔴 建档时间必须**显式设到过去**：不设的话 ``created_at`` 取
    #    ``CURRENT_TIMESTAMP`` = 今天，于是「回溯到 15 天前」会被正确地
    #    判为「早于建档时间」→ 40001 —— 用例想验的回退逻辑就永远走不到。
    talent = TalentBase(
        tenant_id=tid, name="李伟", work_years=7, school="清华大学", major="计算机",
        data_status=int(TalentDataStatus.ACTIVE), created_at=now - timedelta(days=90),
    )
    db.add(talent)
    db.flush()

    rows = [
        TalentChangeLog(
            tenant_id=tid, talent_id=talent.id, change_source=int(ChangeSource.AI_PARSE),
            field_name="work_years", before_value="5", after_value="6",
            created_at=now - timedelta(days=60),
        ),
        TalentChangeLog(
            tenant_id=tid, talent_id=talent.id, change_source=int(ChangeSource.MANUAL_REVIEW),
            field_name="school", before_value=None, after_value="清华大学",
            created_at=now - timedelta(days=30), operator_id=adm.id,
        ),
        TalentChangeLog(
            tenant_id=tid, talent_id=talent.id, change_source=int(ChangeSource.REPARSE),
            field_name="work_years", before_value="6", after_value="7",
            created_at=now - timedelta(days=7),
        ),
    ]
    db.add_all(rows)
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
        "talent_id": int(talent.id), "user_id": int(adm.id),
        "now": now,
    }

    from app.models.audit import AuditLog

    for model in (
        AuditLog, TalentChangeLog, TalentBase, SysUserRole, SysRolePermission,
        SysPermission, SysRole, SysUser, Tenant,
    ):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()


def test_api67_list_logs(env) -> None:
    """API-67：列表倒序 + 姓名反查 + 来源中文名。"""
    resp = env["client"].get(
        "/api/v1/talent/changes",
        params={"talent_id": str(env["talent_id"])},
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert body["page"]["total"] == 3
    first = body["data"][0]
    assert first["talent_id"] == str(env["talent_id"])  # 🔴 雪花 ID 必须字符串
    assert first["talent_name"] == "李伟"
    assert first["field_name"] == "work_years"
    assert first["change_source_name"]  # 中文名（枚举标签）
    assert first["field_label"] == "工作年限"


def test_api67_rejects_unknown_field_name(env) -> None:
    """受控词表：非法 field_name → 40001（**不是**「查询成功返回 0 条」）。"""
    resp = env["client"].get(
        "/api/v1/talent/changes",
        params={"field_name": "wrk_years"},
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code in (200, 400)
    assert resp.json()["code"] == 40001


def test_api68_talent_chain(env) -> None:
    """API-68：单档案变更链；路径 talent_id 优先于查询串。"""
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/changes",
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["page"]["total"] == 3


def test_api68_unknown_talent_is_40401(env) -> None:
    """档案不存在 → 40401（域外访问才是 40301，两者不可混用）。"""
    resp = env["client"].get(
        "/api/v1/talents/9999999999999999/changes",
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code in (200, 404)
    assert resp.json()["code"] == 40401


def test_api69_snapshot_rolls_back(env) -> None:
    """API-69：回溯到 15 天前 → 工作年限回到 6，并给出未留痕字段声明。"""
    at = (env["now"] - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/snapshot",
        params={"at": at},
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["talent"]["work_years"] == 6
    assert data["talent"]["id"] == str(env["talent_id"])
    assert data["changes_after"] == 1
    assert "work_years" in data["restored_fields"]
    assert "major" in data["untracked_fields"]  # 声明而非错误
    assert data["history_truncated"] is False


def test_api69_rejects_at_before_talent_creation(env) -> None:
    """回溯早于建档时间 → **40001** + ``data.created_at``（接口原文的失败响应）。"""
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/snapshot",
        params={"at": "2000-01-01 00:00:00"},
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001
    assert "created_at" in body["data"]


def test_api69_rejects_bad_at_format(env) -> None:
    """``at`` 只接受约定格式（接受 ``T`` 分隔会让前端发错格式永远暴露不出来）。"""
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/snapshot",
        params={"at": "2026-09-01T00:00:00"},
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.json()["code"] == 40001


def test_api70_field_history(env) -> None:
    """API-70：单字段完整变更链 + 当前值（同为字符串形态，便于逐行对照）。"""
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/fields/work_years/history",
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] == 2
    assert data["current_value"] == "7"
    assert data["field_label"] == "工作年限"
    assert [i["after_value"] for i in data["items"]] == ["7", "6"]


def test_api70_rejects_unknown_field_code(env) -> None:
    resp = env["client"].get(
        f"/api/v1/talents/{env['talent_id']}/fields/id_card/history",
        headers=_h(env["tokens"]["adm"]),
    )
    assert resp.json()["code"] == 40001


def test_api67_requires_permission(env) -> None:
    """无 ``change:list`` 权限 → 40301（fail-closed，不是空列表）。"""
    resp = env["client"].get(
        "/api/v1/talent/changes", headers=_h(env["tokens"]["none"])
    )
    assert resp.json()["code"] == 40301
