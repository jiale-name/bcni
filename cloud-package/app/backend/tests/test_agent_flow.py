"""CU-13 · Agent 任务与人工介入（API-90 ~ API-95，F-A0.1 ~ F-A0.5）。

分层
----
- **L1 契约层**（不依赖 MySQL / Redis）：请求模型三态（``rejected`` 必填 ``comment``）、
  状态取值与词表、**三张表没有行级数据域**（这是 40401 而非 40301 的唯一理由）、
  ``agent_step`` 只追加、6 个端点在 OpenAPI 中的登记与幂等头的位置、权限矩阵静态钉死。
- **L2 集成层**（缺依赖时 skip 而非 fail）：用 ``AgentService.start_task`` 造出真实任务
  （**接口刻意不提供创建任务**，它是执行流水的入口，不是面向用户的 CRUD），
  再走 HTTP 覆盖 API-90 ~ 95 的端到端：40902 / 40903 三态、恢复执行的两条分支、
  重试的状态机约束、租户隔离、审计留痕。

🔴 本批（CU-13）在写测试时**抓出并修好三个真缺陷**，本文件把它们钉死
--------------------------------------------------------------
| 缺陷 | 现象 | 对应用例 |
| --- | --- | --- |
| ``_resume`` 返回 ``outcome.next_node`` 而非后继节点 | 恢复后 ``next_node`` 恒为 ``None``（终态节点没有后继）→ 前端显示「后面没有节点」；而 40903 回放路径走 ``successor_of`` 返回 ``persist_talent`` —— **同一操作两种答案** | ``test_next_node_is_consistent_between_first_call_and_replay`` |
| ``agent_step.step_index`` 是**本次 run 内**的相对序号 | 恢复/重试后第二次 run 的步骤与第一次**撞号**，回放时间线错乱（实测把 ``persist_talent`` 排到 ``ocr`` 前面） | ``test_steps_are_in_true_timeline_order_after_resume`` |
| 状态流转后回读未刷新（``expire_on_commit=False`` + Core UPDATE + Identity Map） | 首次调用返回 ``status=0 pending``（库里已是 approved），而重试时同一次操作返回 ``approved`` | ``test_resolve_returns_fresh_status_not_stale_pending`` |

每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.core.security import SecurityUtils
from app.core.tenant import CurrentPrincipal
from app.dao import agent_dao
from app.exceptions import ErrorCode
from app.models.agent import AgentInterrupt, AgentStep, AgentTask
from app.models.audit import AuditLog
from app.models.enums import (
    AgentInterruptStatus,
    AgentTaskStatus,
    RoleDataScope,
)
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.agent import (
    DECISION_TO_STATUS,
    AgentTaskFilterRequest,
    InterruptFilterRequest,
    InterruptResolveRequest,
    InterruptType,
)
from app.services.agent_service import RETRYABLE_STATUSES, AgentService, AiCallStatusLike
from tests.conftest import db_available

PWD = "Admin@123456"

#: 接口 §2.2 里 API-90~95 的权限码
AGENT_PERMS = (
    "agent:task:list",
    "agent:task:detail",
    "agent:task:replay",
    "agent:task:retry",
    "agent:interrupt:list",
    "agent:interrupt:resolve",
)
#: 矩阵里 HR 有的（**不含 retry** —— API-95 只给 ADM / AIOPS）
HR_PERMS = tuple(p for p in AGENT_PERMS if p != "agent:task:retry")

_UNKNOWN_ID = "7999999999999999999"

#: 一件「抽不出字段」的注入式抽取器 —— 让占位实现走「必须复核」分支，
#: 从而把任务稳定地推进到 ``field_review`` 中断（不依赖任何真实模型）
def _raise_extractor(_text: str, _state: dict) -> dict:
    raise RuntimeError("extractor 挂了（测试注入）")


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约：三态语义与词表
# ============================================================================
def test_rejected_requires_comment() -> None:
    """🔴 ``rejected`` 时 ``comment`` **必填**（接口 §3 备注：驳回不静默丢弃）。

    「为什么被驳回」是这条记录唯一的业务价值 —— 留空等于把待办变成黑洞：
    任务进了失败分支，而没人知道原因。

    反向对照（同一用例内）：``approved`` 不要求 comment。
    """
    with pytest.raises(ValidationError):
        InterruptResolveRequest(decision="rejected")
    with pytest.raises(ValidationError):
        InterruptResolveRequest(decision="rejected", comment="   ")

    assert InterruptResolveRequest(decision="rejected", comment="字段与原件不符").comment
    assert InterruptResolveRequest(decision="approved").comment is None


def test_decision_maps_to_interrupt_status() -> None:
    """``decision`` → ``agent_interrupt.status``：``approved=1`` / ``rejected=2``。"""
    assert DECISION_TO_STATUS == {"approved": 1, "rejected": 2}
    assert AgentInterruptStatus.name_of(0) == "pending"
    assert AgentInterruptStatus.name_of(DECISION_TO_STATUS["approved"]) == "approved"
    assert AgentInterruptStatus.name_of(DECISION_TO_STATUS["rejected"]) == "rejected"


def test_only_pending_and_failed_are_retryable() -> None:
    """🔴 API-95 只允许从 ``pending`` / ``failed`` 重试。

    对 ``success`` 重试会**重复执行副作用**（重复入库、重复向量化）；
    对 ``waiting_human`` 重试会**绕过人工确认** —— 那是合规红线
    （接口 §3：「关键决策点必须走人工确认」）。
    两者的共同点是「功能上跑得通，但破坏了一条更重要的规则」。
    """
    assert RETRYABLE_STATUSES == {
        int(AgentTaskStatus.PENDING),
        int(AgentTaskStatus.FAILED),
    }
    for forbidden in (
        AgentTaskStatus.SUCCESS,
        AgentTaskStatus.WAITING_HUMAN,
        AgentTaskStatus.RUNNING,
    ):
        assert int(forbidden) not in RETRYABLE_STATUSES


def test_task_filter_rejects_unknown_type() -> None:
    """``task_type`` 白名单（拼错 → 40001，而不是「筛出 0 条」）。"""
    with pytest.raises(ValidationError):
        AgentTaskFilterRequest(task_type="parse_resume")
    assert AgentTaskFilterRequest(task_type="parse").task_type == "parse"


def test_interrupt_filter_rejects_unknown_type() -> None:
    """``interrupt_type`` 白名单 —— 它**决定处理界面长什么样**，拼错会让人拿不到待办。"""
    with pytest.raises(ValidationError):
        InterruptFilterRequest(interrupt_type="field_revieww")
    assert InterruptFilterRequest(interrupt_type=InterruptType.FIELD_REVIEW).interrupt_type == "field_review"


def test_parse_graph_uses_declared_interrupt_vocabulary() -> None:
    """图定义里抛出的 ``interrupt_type`` 必须落在词表内（否则前端没有对应表单）。

    这条把「图定义」与「契约词表」连起来 —— 两处各写各的字符串字面量时，
    最容易出现「后端抛 ``field_reviews``、前端只认 ``field_review``」这类静默不匹配。
    """
    from app.agents.parse_graph import build_parse_graph

    graph = build_parse_graph()
    assert InterruptType.FIELD_REVIEW in graph.nodes, "复核节点名必须与词表一致"
    assert "field_review" in InterruptType.LABELS


# ============================================================================
# L1 · 结构：三张表都没有行级数据域（40401 而非 40301 的唯一理由）
# ============================================================================
def test_agent_tables_have_no_row_level_data_scope() -> None:
    """🔴 Agent 三张表**都没有 ``org_id``**。

    这正是「跨租户访问 = **40401**」而不是 40301 的理由：
    没有行级数据域 ⇒ 不存在「越权看别人部门的数据」这种情形 ⇒ 没有可被探测的信息差
    ⇒ 「本租户内查不到」就直接是「不存在」。与字典（CU-07）、审计（CU-08）同口径。

    ⚠️ 反过来若哪天给这些表加了 ``org_id``，口径必须立刻改成 40301（防资源探测）——
    这条测试会失败，逼着改的人重新想一遍。
    """
    for model in (AgentTask, AgentInterrupt, AgentStep):
        cols = set(model.__table__.c.keys())
        assert "org_id" not in cols, f"{model.__tablename__} 出现了行级数据域 org_id，40401 口径需要重审"
        assert "tenant_id" in cols, f"{model.__tablename__} 缺少 tenant_id（租户隔离的第一道闸门）"


def test_agent_step_is_append_only() -> None:
    """``agent_step`` 是**执行轨迹**：无 ``is_deleted`` / ``updated_at``。

    轨迹一旦可改可删，「这一步到底跑没跑、跑出什么」就无从追问 ——
    与 ``audit_log`` / ``ai_call_log`` 同源处理。
    """
    cols = set(AgentStep.__table__.c.keys())
    assert {"is_deleted", "updated_at", "deleted_at"} & cols == set()
    for col in (AgentTask, AgentInterrupt):
        assert "is_deleted" in set(col.__table__.c.keys()), "业务态表应当可软删"


def test_step_status_vocabulary_is_not_reused_from_ai_call_status() -> None:
    """``agent_step.status`` 的 0/1 名称**不复用** ``AiCallStatus``。

    当前两者标签恰好都是「失败 / 成功」，但语义归属不同（图节点执行 vs 模型调用）。
    复用一个枚举会让「以后给节点加一个 skipped 状态」时被迫改动模型调用的枚举。
    当前标签一致是巧合，不是耦合的理由。
    """
    from app.models.enums import AiCallStatus

    assert AiCallStatusLike.name_of(0) == AiCallStatus.name_of(0) == "失败"
    assert AiCallStatusLike.name_of(1) == AiCallStatus.name_of(1) == "成功"
    assert AiCallStatusLike is not AiCallStatus


def test_agent_status_labels_are_registered_as_english() -> None:
    """🔴 **现状登记（待拍板，本批刻意不改）**：两个 Agent 状态的 ``*_name`` 是英文状态名。

    全项目其余枚举的名称表都是中文（``待复核`` / ``有效`` / ``草稿`` …），
    唯独 ``agent_task_status`` / ``agent_interrupt_status`` 用了 ``pending`` / ``approved``。
    于是 API-90/93/94 的 ``status_name`` 在界面上会显示英文。

    ⚠️ **为什么本批不改**：这两个枚举经 ``_BUILTIN_ENUM_DICTS`` 落成**字典项**
    （``agent_task_status`` / ``agent_interrupt_status``），而字典项是
    「人工可维护、只停用从不软删」的数据。现在只改 ``LABELS`` 会让
    **代码返回中文、已种子租户的字典项仍是英文** —— 界面上的徽标与下拉框对不上，
    比统一英文更难排查。要改就得连字典项一起做数据修正（属数据变更，需评审），
    故登记为遗留项，与「组织/岗位 status_name 显示禁用」（归 CU-23）一并处理。
    """
    assert AgentTaskStatus.name_of(0) == "pending"
    assert AgentTaskStatus.name_of(2) == "waiting_human"
    assert AgentInterruptStatus.name_of(1) == "approved"


# ============================================================================
# L1 · 路由登记与幂等头
# ============================================================================
def test_agent_endpoints_are_registered(app) -> None:
    """6 个端点必须都在（路径 + 方法）。"""
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/agent/tasks": {"get"},
        "/api/v1/agent/tasks/{task_id}": {"get"},
        "/api/v1/agent/tasks/{task_id}/steps": {"get"},
        "/api/v1/agent/interrupts": {"get"},
        "/api/v1/agent/interrupts/{interrupt_id}/resolve": {"post"},
        "/api/v1/agent/tasks/{task_id}/retry": {"post"},
    }
    for path, methods in expected.items():
        assert path in paths, f"OpenAPI 缺少路径 {path}"
        actual = {m for m in paths[path] if m in {"get", "post", "put", "delete"}}
        assert methods <= actual, f"{path} 缺少方法 {methods - actual}"


def test_agent_has_no_task_creation_endpoint(app) -> None:
    """🔴 **没有创建任务的接口**：任务是执行流水，由导入/检索等业务动作触发。

    开放 ``POST /agent/tasks`` 等于允许「从 HTTP 凭空造一条执行流水」——
    那会让「这条任务是谁触发的」无从回答，也会绕过各业务场景的幂等键。
    """
    paths = app.openapi()["paths"]
    posts = {
        p for p, m in paths.items()
        if p.startswith("/api/v1/agent") and "post" in m and not p.endswith(("/resolve", "/retry"))
    }
    assert posts == set(), f"出现了创建型 POST 接口：{posts}"


def test_idempotency_key_declared_only_on_api_94(app) -> None:
    """🔴 ``Idempotency-Key`` 只声明在 **API-94**，API-95 刻意不接受。

    - API-94 是接口 §1.6 的三个**强制幂等**接口之一 → 必须有幂等键；
    - API-95 的并发安全由**状态机**保证（重试后状态离开可重试集合，第二次必然 42201）
      —— 这比幂等键更可靠：幂等键有 24 小时有效期，而「已成功的任务不许再跑」是**永久**约束。
    """
    paths = app.openapi()["paths"]

    def header_names(path: str, method: str) -> set[str]:
        params = paths[path][method]["parameters"]
        return {p["name"] for p in params if p["in"] == "header"}

    assert "Idempotency-Key" in header_names("/api/v1/agent/interrupts/{interrupt_id}/resolve", "post")
    assert header_names("/api/v1/agent/tasks/{task_id}/retry", "post") == set()
    assert "Idempotency-Key" not in header_names("/api/v1/agent/tasks/{task_id}/retry", "post")


# ============================================================================
# L1 · 权限矩阵静态钉死
# ============================================================================
def test_hr_has_agent_reads_but_not_retry() -> None:
    """🔴 矩阵里 API-90~94 有 HR（待办复核是 HR 的日常工作面），但 **API-95 没有 HR**。

    ⚠️ 这里守的是一个**已发生的缺口**：``ROLE_GRANTS["HR"]`` 原本没有任何 ``agent:`` 前缀，
    而矩阵里 API-90~94 明明给了 ✓ —— 结果是「人工介入漏斗」对唯一该用它的人不可见
    （HR 打开待办列表一律 40301）。本批补齐读取与处理权，并**刻意不补 retry**。
    """
    from scripts.seed_dev import ROLE_GRANTS

    prefixes = ROLE_GRANTS["HR"]
    for perm in HR_PERMS:
        assert any(perm.startswith(p) for p in prefixes), f"HR 缺少 {perm}（矩阵里应为 ✓）"
    assert not any("agent:task:retry".startswith(p) for p in prefixes), "HR 不该拿到 API-95 的权限"


def test_business_roles_without_agent_perms() -> None:
    """反向对照：MGR / EMP / EXEC 在矩阵里没有 Agent 权限，不得持有任何 ``agent:`` 前缀。"""
    from scripts.seed_dev import ROLE_GRANTS

    for role in ("MGR", "EMP", "EXEC"):
        assert not any(p.startswith("agent:") for p in ROLE_GRANTS[role]), f"{role} 不该有 agent 权限"


def test_agent_perms_exist_in_permission_tree() -> None:
    """6 个权限点必须都被种子建出来 —— 否则 HR/AIOPS 的授权是空转。"""
    from scripts.seed_dev import _PERM_TREE

    codes = {code for _menu, _name, children in _PERM_TREE for code, _cn in children}
    for perm in AGENT_PERMS:
        assert perm in codes, f"权限树缺少 {perm}"


# ============================================================================
# L2 · 端到端夹具（独立租户）
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """独立租户 + 3 个账号（ADM / HR 对齐矩阵 / 无权限）。

    任务由 ``AgentService.start_task`` 直接创建（**接口不提供创建**）——
    这正是「L2 集成」该有的形态：业务侧入口走服务层，用户侧入口走 HTTP。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("agent_task"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0004_ai_agent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu13_{suffix}", tenant_name=f"CU13测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in AGENT_PERMS:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
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

    role_adm = _mk_role("ADM", list(AGENT_PERMS))
    role_hr = _mk_role("HR", list(HR_PERMS))
    role_none = _mk_role("EMP", [])

    user_ids: dict[str, int] = {}
    tokens: dict[str, str] = {}
    for who, role in (("adm", role_adm), ("hr", role_hr), ("none", role_none)):
        username = f"{who}_{suffix}"
        user = SysUser(
            tenant_id=tid, username=username,
            password_hash=SecurityUtils.hash_password(PWD), real_name=username,
            status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        user_ids[who] = int(user.id)
        db.commit()
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["code"] == 0, payload
        tokens[who] = payload["data"]["access_token"]

    other_tid = tid + 999_999
    yield {
        "tenant_id": tid,
        "other_tenant_id": other_tid,
        "user_ids": user_ids,
        "tokens": tokens,
        # 服务层入口用的主体（与登录同一个人，故审计里的操作人一致）
        "principal": CurrentPrincipal(
            user_id=user_ids["adm"], tenant_id=tid, org_id=None,
            roles=("ADM",), data_scope=int(RoleDataScope.ALL),
        ),
        "hr_principal": CurrentPrincipal(
            user_id=user_ids["hr"], tenant_id=tid, org_id=None,
            roles=("HR",), data_scope=int(RoleDataScope.ALL),
        ),
    }

    db.rollback()
    ts = [tid, other_tid]
    db.execute(delete(AgentStep).where(AgentStep.tenant_id.in_(ts)))
    db.execute(delete(AgentInterrupt).where(AgentInterrupt.tenant_id.in_(ts)))
    db.execute(delete(AgentTask).where(AgentTask.tenant_id.in_(ts)))
    db.execute(
        delete(SysRolePermission).where(
            SysRolePermission.role_id.in_(select(SysRole.id).where(SysRole.tenant_id == tid))
        )
    )
    db.execute(delete(SysUserRole).where(SysUserRole.tenant_id == tid))
    db.execute(delete(SysUser).where(SysUser.tenant_id == tid))
    db.execute(delete(SysRole).where(SysRole.tenant_id == tid))
    db.execute(delete(SysPermission).where(SysPermission.tenant_id == tid))
    db.execute(delete(Tenant).where(Tenant.id == tid))
    db.commit()


def _adm(env: dict) -> dict[str, str]:
    return _h(env["tokens"]["adm"])


def _start(
    db: Session,
    env: dict,
    *,
    file_name: str = "resume.pdf",
    extractor=None,
    idempotent_key: str | None = None,
    who: str = "principal",
) -> AgentTask:
    """造一个真实任务并立刻推进到 ``field_review`` 中断。

    ⚠️ 用服务层而不是 HTTP：接口**刻意不提供**创建任务的入口（见 L1 用例）。
    """
    state: dict = {"file_name": file_name, "resume_id": 1}
    if extractor is not None:
        state["__extractor__"] = extractor
    task, _outcome, _replayed = AgentService.start_task(
        db,
        env[who],
        task_type="parse",
        state=state,
        idempotent_key=idempotent_key,
    )
    return task


def _pending_interrupt(db: Session, tenant_id: int, task_id: int) -> AgentInterrupt:
    rows = agent_dao.list_interrupts(db, tenant_id, task_id=int(task_id), limit=10)
    assert rows, f"任务 {task_id} 没有产生待办（夹具前提失效）"
    return rows[0]


def _steps(db: Session, tenant_id: int, task_id: int) -> list[AgentStep]:
    return list(agent_dao.list_steps(db, tenant_id, int(task_id), limit=100))


def _audit(db: Session, tenant_id: int, action: str) -> list[AuditLog]:
    """读审计行（审计走独立 Session，必须先 commit 丢掉旧快照才读得到）。"""
    db.commit()
    db.expire_all()
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        ).scalars()
    )


# ============================================================================
# L2 · API-90 任务列表 / API-91 详情
# ============================================================================
def test_api_90_list_tasks_hides_graph_state(env: dict, client: TestClient, db: Session) -> None:
    """列表能筛出任务，且**不返回 ``graph_state``**。

    它可能几十 KB（含中间抽取结果），塞进列表会让 20 条记录轻松超过 1MB。
    需要它请调 API-91 —— 「列表轻、详情重」是刻意的分工。
    """
    task = _start(db, env)
    db.commit()

    body = client.get("/api/v1/agent/tasks", headers=_adm(env)).json()
    assert body["code"] == 0
    assert body["page"]["total"] == 1
    item = body["data"][0]
    assert isinstance(item["id"], str), "CR-5"
    assert item["task_type"] == "parse" and item["task_type_name"] == "档案解析"
    assert item["status"] == int(AgentTaskStatus.WAITING_HUMAN)
    assert item["status_name"] == "waiting_human"
    assert "graph_state" not in item, "列表接口不得下发图状态"
    assert int(item["id"]) == int(task.id)


def test_api_90_filters_and_unknown_type(env: dict, client: TestClient, db: Session) -> None:
    """``task_type`` / ``status`` 筛选 + 未知类型 40001（反向对照）。"""
    _start(db, env)
    db.commit()

    ok = client.get("/api/v1/agent/tasks?task_type=parse&status=2", headers=_adm(env)).json()
    assert ok["page"]["total"] == 1
    miss = client.get("/api/v1/agent/tasks?status=3", headers=_adm(env)).json()
    assert miss["page"]["total"] == 0

    bad = client.get("/api/v1/agent/tasks?task_type=nope", headers=_adm(env))
    assert bad.status_code == 400, bad.text
    assert bad.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_api_91_detail_has_graph_state_and_step_count(
    env: dict, client: TestClient, db: Session
) -> None:
    """详情下发**解析后的** ``graph_state`` 对象 + ``step_count``。

    让前端拿到 JSON 字符串再自己 ``JSON.parse``，等于要求每个前端都处理一次
    「解析失败」分支，而写入侧保证它不可能失败（``_dump`` 用 ``default=str``）。
    """
    task = _start(db, env)
    db.commit()

    data = client.get(f"/api/v1/agent/tasks/{task.id}", headers=_adm(env)).json()["data"]
    assert isinstance(data["graph_state"], dict), "graph_state 必须是对象，不是字符串"
    assert data["graph_state"]["file_name"] == "resume.pdf"
    assert data["step_count"] == len(_steps(db, env["tenant_id"], int(task.id)))
    assert data["step_count"] >= 6, "detect→extract→normalize→score→review 至少 6 步"
    assert data["idempotent_key"] is None


def test_task_detail_is_40401_for_unknown_or_cross_tenant(
    env: dict, client: TestClient, db: Session
) -> None:
    """任务不存在 / 跨租户 → **40401**（不是 40301）。

    Agent 表没有行级数据域，故「别人租户的任务」在本域内就是「不存在」——
    与字典、审计日志同口径。这里用一个**真实存在于别的租户**的任务来验证，
    而不是一个随手编的 ID（后者只能证明「查不到」，证明不了「被隔离」）。
    """
    other = AgentTask(
        tenant_id=env["other_tenant_id"], task_type="parse", status=2,
        current_node="field_review", retry_count=0,
    )
    db.add(other)
    db.commit()

    for task_id in (str(other.id), _UNKNOWN_ID):
        resp = client.get(f"/api/v1/agent/tasks/{task_id}", headers=_adm(env))
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-92 步骤回放（含「撞号」缺陷的回归保护）
# ============================================================================
def test_steps_are_in_true_timeline_order_after_resume(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 **撞号缺陷的回归用例**：恢复执行后 ``step_index`` 仍必须全局单调。

    图运行时的 ``step_index`` 是「本次 run 内」从 1 开始的相对序号。恢复执行是**新的
    一次 run**，若不按「任务内已有步数」做偏移，就会与第一次的步骤撞号 ——
    API-92 承诺「按 ``step_index`` 升序 = 回放的时间线顺序」，
    实测的错乱结果是 ``detect_format(1), persist_talent(1), ocr(2), extract_fields(2) …``，
    即「入库」被排到了「OCR」**前面**：回放界面显示一条不可能发生的时间线。

    做对照的还有：数据本身必须完整（两次 run 的步骤都在，且按真实次序）。
    """
    task = _start(db, env)
    before = [s.node_name for s in _steps(db, env["tenant_id"], int(task.id))]
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    assert client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "approved", "comment": "字段已核对"},
    ).status_code == 200

    body = client.get(f"/api/v1/agent/tasks/{task.id}/steps", headers=_adm(env)).json()
    indices = [s["step_index"] for s in body["data"]]
    names = [s["node_name"] for s in body["data"]]

    assert indices == sorted(indices), f"步骤序号必须升序（回放顺序），实际 {indices}"
    assert len(set(indices)) == len(indices), f"步骤序号出现重复（撞号）：{indices}"
    assert names[: len(before)] == before, "第一次 run 的步骤次序不得被打乱"
    assert names[-1] == "persist_talent", "恢复执行的那一步必须排在**最后**（时间上确实最后发生）"
    assert body["data"][0]["input"] is not None, "input/output 是解析后的对象"


def test_api_92_requires_task_visibility(env: dict, client: TestClient, db: Session) -> None:
    """跨租户的 ``task_id`` 取步骤 → **40401**（先校验任务可见性，再取步骤）。

    若不先校验，就会泄露「步骤存在但任务不可见」这种信息差。
    """
    other = AgentTask(
        tenant_id=env["other_tenant_id"], task_type="parse", status=3, retry_count=0
    )
    db.add(other)
    db.flush()
    agent_dao.insert_step(
        db,
        AgentStep(
            tenant_id=env["other_tenant_id"], task_id=int(other.id), node_name="detect_format",
            step_index=1, status=1,
        ),
    )
    db.commit()

    resp = client.get(f"/api/v1/agent/tasks/{other.id}/steps", headers=_adm(env))
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-93 人工待办
# ============================================================================
def test_api_93_lists_pending_interrupt_with_payload(
    env: dict, client: TestClient, db: Session
) -> None:
    """待办列表：``payload`` 解析为对象（**形状由 ``interrupt_type`` 决定**）+ 指派角色。"""
    _start(db, env)
    db.commit()

    body = client.get("/api/v1/agent/interrupts", headers=_adm(env)).json()
    assert body["code"] == 0 and body["page"]["total"] == 1
    item = body["data"][0]
    assert isinstance(item["id"], str), "CR-5"
    assert item["interrupt_type"] == "field_review"
    assert item["interrupt_type_name"] == "字段复核"
    assert item["status"] == 0 and item["status_name"] == "pending"
    assert item["assignee_role"] == "HR"
    assert isinstance(item["payload"], dict), "payload 必须是对象"
    assert item["payload"]["file_name"] == "resume.pdf"
    assert item["payload"]["reasons"] == ["no_fields"], "占位抽取器的判定依据要能被人看到"
    assert item["decided_by"] is None


def test_api_93_filters(env: dict, client: TestClient, db: Session) -> None:
    """``status`` / ``interrupt_type`` / ``assignee_role`` / ``task_id`` 筛选 + 反向对照。"""
    task = _start(db, env)
    db.commit()

    def total(query: str) -> int:
        return client.get(f"/api/v1/agent/interrupts?{query}", headers=_adm(env)).json()["page"]["total"]

    assert total("status=0") == 1
    assert total("status=1") == 0
    assert total("interrupt_type=field_review") == 1
    assert total("interrupt_type=merge_confirm") == 0
    assert total("assignee_role=HR") == 1
    assert total("assignee_role=ADM") == 0
    assert total(f"task_id={task.id}") == 1
    assert total(f"task_id={_UNKNOWN_ID}") == 0


# ============================================================================
# L2 · ⭐API-94 处理待办（三态并发 + 恢复执行）
# ============================================================================
def test_resolve_returns_fresh_status_not_stale_pending(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 **陈旧回显缺陷的回归用例**：首次处理的响应必须是 ``approved``，不能是 ``pending``。

    根因：状态流转走 **Core 条件更新**（不经过 ORM），而引擎配了
    ``expire_on_commit=False`` → commit 后内存里的实体仍是 ``status=0``；
    此时再 ``select`` 同一主键，SQLAlchemy **默认直接返回内存里的旧对象**
    （不重新填充字段）。于是接口告诉前端「还是待处理」，而库里其实已经是 approved。

    最刺眼的地方是它**自相矛盾**：40903 幂等回放是新请求、新 Session，读到的是真值
    （``approved``）—— 同一次操作的首次提交与网络重试给出两个不同的结论。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    resp = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "approved", "comment": "字段已核对"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == int(AgentInterruptStatus.APPROVED)
    assert data["status_name"] == "approved", f"回显了陈旧状态：{data}"
    assert data["handled_by"] is not None and data["handled_at"] is not None

    # 库里与响应必须一致（这条是上一行的「事实依据」）
    db.commit()
    db.expire_all()
    row = db.execute(
        select(AgentInterrupt).where(AgentInterrupt.id == int(interrupt.id))
    ).scalar_one()
    assert int(row.status) == int(AgentInterruptStatus.APPROVED)


def test_next_node_is_consistent_between_first_call_and_replay(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 **``next_node`` 缺陷的回归用例**：首次调用的 ``next_node`` 必须与幂等回放一致。

    缺陷形态：``_resume`` 曾返回 ``outcome.next_node``（跑完之后的下一个节点）。
    而 ``persist_talent`` 是**终态节点、没有后继** → 恒为 ``None``，
    于是首次调用告诉前端「这个中断后面没有后续节点」，而 40903 回放返回
    ``persist_talent``（它走 ``successor_of``）——同一操作两种答案。

    正确语义（接口 §3 + ``_resume`` 自己的 docstring）：``next_node`` 是
    **即将执行的那个后继节点**，由 ``successor_of`` 求值（与图运行时同一份逻辑）。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    first = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        # 幂等键走请求头（接口 §1.6）
        headers={**_adm(env), "Idempotency-Key": "cu13-approve-1"},
        json={"decision": "approved"},
    ).json()["data"]
    assert first["next_node"] == "persist_talent"
    assert first["resumed"] is True
    assert first["task_status"] == int(AgentTaskStatus.SUCCESS)

    replay = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers={**_adm(env), "Idempotency-Key": "cu13-approve-1"},
        json={"decision": "approved"},
    )
    assert replay.status_code == 409, replay.text
    body = replay.json()
    assert body["code"] == int(ErrorCode.IDEMPOTENT_REPLAY)
    assert body["data"]["result"]["next_node"] == first["next_node"], "首次与回放必须给同一个 next_node"
    assert body["data"]["result"]["status"] == first["status"]
    assert body["data"]["decision"] == "approved"
    assert body["data"]["first_handled_at"]


def test_reject_routes_to_manual_reject(env: dict, client: TestClient, db: Session) -> None:
    """🔴 ``rejected`` **不静默丢弃**：走 ``manual_reject`` 终态。

    任务置 ``success`` 而不是 ``failed`` 是刻意的：「被人工驳回」是一个**正常结束**，
    不是系统故障（``failed`` 会触发重试与告警，把一个业务判断变成运维事件）。
    驳回原因留在 ``comment`` 与图状态里。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    data = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "rejected", "comment": "扫描件太糊，请重新上传"},
    ).json()["data"]
    assert data["status"] == int(AgentInterruptStatus.REJECTED)
    assert data["status_name"] == "rejected"
    assert data["next_node"] == "manual_reject"
    assert data["resumed"] is True
    assert data["task_status"] == int(AgentTaskStatus.SUCCESS)

    detail = client.get(f"/api/v1/agent/tasks/{task.id}", headers=_adm(env)).json()["data"]
    assert detail["graph_state"]["rejected"] is True
    assert detail["graph_state"]["reject_comment"] == "扫描件太糊，请重新上传"
    assert detail["graph_state"]["finished_at_node"] == "manual_reject"


def test_second_resolver_gets_40902_with_handler_name(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 **别人已处理** → **40902**，``data`` 指出处理人姓名与时间。

    与 40903 的区别是前端行为完全不同：40902 要「刷新后重试」，
    40903 要「当作成功继续」。把两者混起来会让「别人已驳回」显示成「你的通过已生效」。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    ok = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers={**_adm(env), "Idempotency-Key": "first-person"},
        json={"decision": "approved"},
    )
    assert ok.status_code == 200

    # 另一个人（HR）用**不同的**幂等键提交
    conflict = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers={**_h(env["tokens"]["hr"]), "Idempotency-Key": "second-person"},
        json={"decision": "rejected", "comment": "我觉得不行"},
    )
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert body["code"] == int(ErrorCode.CONCURRENT_CONFLICT), "必须是 40902（并发冲突），不是 40903"
    assert body["data"]["handled_by_name"], "必须告诉前端「已被谁处理」"
    assert body["data"]["handled_at"]
    assert body["data"]["status"] == int(AgentInterruptStatus.APPROVED)


def test_blank_idempotency_key_is_not_a_key(env: dict, client: TestClient, db: Session) -> None:
    """空串幂等键视为**未提供** —— 第二次提交应得 40902（而不是 40903）。

    若把空串当成一个真实键，所有「未填幂等键」的请求会**共用同一个键**，
    于是第二个人会拿到第一个人的结果并被告知「你的操作已生效」。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    first = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers={**_adm(env), "Idempotency-Key": "   "},
        json={"decision": "approved"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers={**_adm(env), "Idempotency-Key": "   "},
        json={"decision": "approved"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == int(ErrorCode.CONCURRENT_CONFLICT), "空串不得触发幂等回放"


def test_resolve_rejected_without_comment_is_40001(
    env: dict, client: TestClient, db: Session
) -> None:
    """HTTP 层也要拦住「无意见的驳回」（契约校验 → 40001）。"""
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    resp = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "rejected"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_resolve_unknown_interrupt_is_40401(env: dict, client: TestClient) -> None:
    """待办不存在 → 40401。"""
    resp = client.post(
        f"/api/v1/agent/interrupts/{_UNKNOWN_ID}/resolve",
        headers=_adm(env),
        json={"decision": "approved"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


def test_resolve_cross_tenant_interrupt_is_40401(
    env: dict, client: TestClient, db: Session
) -> None:
    """跨租户的待办 → **40401**（与任务详情同口径；用真实存在的他租户记录验证隔离）。"""
    other_task = AgentTask(
        tenant_id=env["other_tenant_id"], task_type="parse", status=2, retry_count=0
    )
    db.add(other_task)
    db.flush()
    other_interrupt = AgentInterrupt(
        tenant_id=env["other_tenant_id"], task_id=int(other_task.id), node_name="field_review",
        interrupt_type="field_review", status=0,
    )
    db.add(other_interrupt)
    db.commit()

    resp = client.post(
        f"/api/v1/agent/interrupts/{other_interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "approved"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-95 重试
# ============================================================================
def _failed_task(db: Session, env: dict) -> AgentTask:
    """造一个**失败**任务：注入会抛错的抽取器（异常被图运行时结构化，不外抛）。"""
    task = _start(db, env, file_name="broken.pdf", extractor=_raise_extractor)
    assert int(task.status) == int(AgentTaskStatus.FAILED), "夹具前提：这次抽取必须失败"
    return task


def test_retry_failed_task_resumes_from_current_node(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 重试**从当前节点**开始，不从头 —— 已完成的步骤不重跑。

    断言两条：① ``retry_count`` 真的 +1；② ``detect_format`` / ``ocr`` 的步数**没有翻倍**
    （翻倍就说明从入口重跑了整条链，而「重跑已完成的外部调用」会重复烧钱、重复写库）。
    """
    task = _failed_task(db, env)
    before = [s.node_name for s in _steps(db, env["tenant_id"], int(task.id))]
    db.commit()

    body = client.post(f"/api/v1/agent/tasks/{task.id}/retry", headers=_adm(env)).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["retry_count"] == 1
    assert data["resumed"] is True
    assert data["status"] == int(AgentTaskStatus.FAILED), "注入的抽取器仍会失败"
    assert data["message"], "本次重试再次失败时必须给出原因（成功才为 null）"

    after = [s.node_name for s in _steps(db, env["tenant_id"], int(task.id))]
    assert after.count("detect_format") == before.count("detect_format") == 1, "不得从入口重跑"
    assert after.count("ocr") == before.count("ocr") == 1
    assert after.count("extract_fields") == before.count("extract_fields") + 1, "只重跑失败的那一步"


def test_retry_success_is_42201_with_retryable_list(
    env: dict, client: TestClient, db: Session
) -> None:
    """对 ``success`` 重试 → **42201**，``data.retryable`` 列出可重试状态。

    重跑一个已成功的任务会**重复执行副作用**（重复入库、重复向量化）——
    这是「功能上跑得通但破坏规则」的典型，必须显式拒绝并告诉调用方哪些状态可以重试。
    """
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()
    client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "approved"},
    )

    resp = client.post(f"/api/v1/agent/tasks/{task.id}/retry", headers=_adm(env))
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.ILLEGAL_STATE)
    assert set(body["data"]["retryable"]) == {"pending", "failed"}


def test_retry_waiting_human_is_42201(env: dict, client: TestClient, db: Session) -> None:
    """🔴 对 ``waiting_human`` 重试 → **42201**（会**绕过人工确认**，属合规红线）。

    接口 §3：「关键决策点必须走人工确认」。若重试能绕过它，
    整个「人工介入漏斗」（F-A0.3）就形同虚设 —— 而那正是本项目要交付的合规能力。
    """
    task = _start(db, env)
    db.commit()

    resp = client.post(f"/api/v1/agent/tasks/{task.id}/retry", headers=_adm(env))
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)
    assert resp.json()["data"]["status"] == int(AgentTaskStatus.WAITING_HUMAN)


def test_retry_unknown_task_is_40401(env: dict, client: TestClient) -> None:
    """任务不存在 / 跨租户 → 40401。"""
    resp = client.post(f"/api/v1/agent/tasks/{_UNKNOWN_ID}/retry", headers=_adm(env))
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · 权限矩阵端到端
# ============================================================================
def test_hr_can_list_and_resolve_but_not_retry(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 矩阵验收：HR 能读任务 / 读步骤 / 看并处理待办，但 **API-95 是 40301**。

    这条正是「补齐 HR 的 agent 读取权、但不给 retry」这个决策的验收点：
    任一方向反过来（漏补 → HR 打不开待办列表；多补 → HR 能绕过人工确认重跑任务），
    本用例都会失败。
    """
    hr = _h(env["tokens"]["hr"])
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    for path in (
        "/api/v1/agent/tasks",
        f"/api/v1/agent/tasks/{task.id}",
        f"/api/v1/agent/tasks/{task.id}/steps",
        "/api/v1/agent/interrupts",
    ):
        assert client.get(path, headers=hr).status_code == 200, path

    denied = client.post(f"/api/v1/agent/tasks/{task.id}/retry", headers=hr)
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == int(ErrorCode.NO_PERMISSION)
    assert "task_type" not in str(denied.json().get("data")), "40301 不得泄露资源字段"

    # 反向对照：HR 处理待办是允许的（这是矩阵里给 HR 的核心动作）
    ok = client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=hr,
        json={"decision": "approved", "comment": "HR 已核对"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["handled_by"] is not None


def test_role_without_agent_perms_gets_40301(env: dict, client: TestClient) -> None:
    """无 Agent 权限的角色 → 全部 40301（fail-closed）。"""
    headers = _h(env["tokens"]["none"])
    for path in ("/api/v1/agent/tasks", "/api/v1/agent/interrupts"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 403, f"{path}: {resp.text}"
        assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


def test_unauthenticated_is_40101(client: TestClient) -> None:
    """无 Token → 40101（认证层先于权限层）。"""
    assert client.get("/api/v1/agent/tasks").json()["code"] == int(ErrorCode.UNAUTHENTICATED)


# ============================================================================
# L2 · 审计留痕
# ============================================================================
def test_resolve_and_retry_are_audited(env: dict, client: TestClient, db: Session) -> None:
    """处理待办与重试任务都要留痕，且带**改前/改后**与「恢复了哪个节点」。"""
    task = _start(db, env)
    interrupt = _pending_interrupt(db, env["tenant_id"], int(task.id))
    db.commit()

    client.post(
        f"/api/v1/agent/interrupts/{interrupt.id}/resolve",
        headers=_adm(env),
        json={"decision": "approved", "comment": "ok", "payload": {"value": "7"}},
    )
    resolves = _audit(db, env["tenant_id"], "agent_interrupt_resolve")
    assert len(resolves) == 1
    before = json.loads(resolves[0].before_json)
    after = json.loads(resolves[0].after_json)
    assert before["status"] == 0 and after["status"] == int(AgentInterruptStatus.APPROVED)
    assert after["decision"] == "approved"
    assert after["comment"] == "ok"
    assert after["next_node"] == "persist_talent", "审计要能回答「恢复后跑了哪一步」"
    assert after["payload_keys"] == ["value"], "复核修正值的**键**留痕（值本身不进审计）"

    failed = _failed_task(db, env)
    db.commit()
    client.post(f"/api/v1/agent/tasks/{failed.id}/retry", headers=_adm(env))
    retries = _audit(db, env["tenant_id"], "agent_task_retry")
    assert len(retries) == 1
    body = json.loads(retries[0].after_json)
    assert body["retry_count"] == 1
    assert body["start_node"] == "extract_fields", "重试的起点必须留痕（否则无法解释为何没重跑全链）"
    assert body["outcome"] == "failed"
