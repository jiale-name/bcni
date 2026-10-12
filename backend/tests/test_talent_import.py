"""CU-14 · 简历批量导入 + SSE 进度（F-3.1 / F-3.8，DF-2/SM-2，API-36~43）。

分层
----
- **L1 契约层**：扩展名白名单、单批上限配置存在、OpenAPI 登记、
  权限矩阵钉死、``Idempotency-Key`` 强制语义。
- **L2 集成层**（独立租户）：缺幂等头 40001、上传建任务（``IMPyyyymmddNNN``）、
  幂等回放 40903（先于校验 —— 重复提交合法返回首次任务）、
  不支持的扩展名 41501、批内重复内容拒绝、report 在未完成时 42201、
  跨租户任务 40401（导入任务无行级数据域口径）。
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect

from app.config import settings
from app.core.security import SecurityUtils
from app.models.enums import RoleDataScope
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.talent import SUPPORTED_EXTENSIONS
from tests.conftest import db_available

PWD = "Admin@123456"
IMPORT_PERMS = ["import:view", "import:report", "import:retry"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


#: 最小 docx（零依赖）—— ``extract_text`` 的 docx 分支读 ``word/document.xml`` 的 ``<w:t>``。
#: 🔴 为什么必须用**真** docx：旧夹具上传的是「纯文本字节 + .doc 后缀」，
#: 解析必然失败；而批次 21 修好账目之前，任务仍会被错判成 COMPLETED（0/0），
#: 于是「拿一个解析失败的任务去出质量报告」也能过 —— 那是**假绿**。
#: 详见 ``tests/test_import_accounting.py`` 的缺陷说明。
_DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
)


def _docx_bytes(text: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", _DOCX_XML.format(text=text))
    return buf.getvalue()


# ============================================================================
# L1 · 契约
# ============================================================================
def test_supported_extensions_pinned() -> None:
    assert SUPPORTED_EXTENSIONS == frozenset({"pdf", "doc", "docx", "jpg", "jpeg", "png", "zip"})


def test_upload_limits_configured() -> None:
    """单批 ≤100 / 单文件 ≤50MB（配置三处同步由 test_config_env 守）。"""
    assert settings.UPLOAD_MAX_FILES_PER_BATCH == 100
    assert settings.UPLOAD_MAX_FILE_SIZE_MB == 50


def test_import_endpoints_registered_in_openapi(app) -> None:
    schema = app.openapi()
    for path, methods in (
        ("/api/v1/talent/import", {"get", "post"}),
        ("/api/v1/talent/import/{task_id}", {"get"}),
        ("/api/v1/talent/import/{task_id}/files", {"get"}),
        ("/api/v1/talent/import/{task_id}/progress", {"get"}),
        ("/api/v1/talent/import/{task_id}/report", {"get"}),
        ("/api/v1/talent/import/{task_id}/report/export", {"get"}),
        ("/api/v1/talent/import/{task_id}/retry", {"post"}),
    ):
        assert path in schema["paths"], path
        assert methods <= set(schema["paths"][path]), (path, methods)


def test_import_permission_matrix_pinned() -> None:
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module("app.routers.import_router"))
    for perm in IMPORT_PERMS:
        assert f'require_perm("{perm}")' in src, perm


def test_idempotency_header_declared_in_openapi(app) -> None:
    """API-36 强制幂等：``Idempotency-Key`` 必须出现在 OpenAPI 参数里。"""
    schema = app.openapi()
    post = schema["paths"]["/api/v1/talent/import"]["post"]
    params = post.get("parameters") or []
    names = {p.get("name") for p in params}
    assert "Idempotency-Key" in names, names


# ============================================================================
# L2 · 端到端夹具
# ============================================================================
@pytest.fixture
def env(db, client: TestClient) -> Iterator[dict]:
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("import_task"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0005_talent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu14_{suffix}", tenant_name=f"CU14测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in IMPORT_PERMS:
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

    _mk_user(f"adm_{suffix}", _mk_role("ADM", IMPORT_PERMS))
    none_role = _mk_role("NONE", [])
    _mk_user(f"none_{suffix}", none_role)
    db.commit()

    tokens: dict[str, str] = {}
    for who, username in {"adm": f"adm_{suffix}", "none": f"none_{suffix}"}.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        tokens[who] = resp.json()["data"]["access_token"]

    yield {"client": client, "tokens": tokens, "tenant_id": tid}

    from sqlalchemy import delete

    from app.models.audit import AuditLog
    from app.models.agent import AgentInterrupt, AgentStep, AgentTask
    from app.models.talent import ImportFile, ImportTask, TalentBase, TalentChangeLog, TalentParseField, TalentTagRel

    for model in (TalentTagRel, TalentParseField, TalentChangeLog, TalentBase,
                  AgentInterrupt, AgentStep, AgentTask,
                  ImportFile, ImportTask, AuditLog,
                  SysUserRole, SysRolePermission, SysPermission, SysRole, SysUser, Tenant):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()


def _upload(client: TestClient, token: str, key: str, files: list[tuple[str, bytes]]):
    return client.post(
        "/api/v1/talent/import",
        headers=_h(token) | {"Idempotency-Key": key},
        files=[("files", (n, c, "application/octet-stream")) for n, c in files],
    )


def _wait_terminal(client: TestClient, token: str, task_id: str, timeout: float = 30.0) -> dict:
    """轮询任务到终态（后台 daemon 线程异步解析）。"""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/v1/talent/import/{task_id}", headers=_h(token)).json()
        status = int(last["data"]["status"])
        if status >= 2:  # 2 已完成 3 部分失败 4 失败
            return last
        time.sleep(0.3)
    return last


# ============================================================================
# L2 · API-36 创建任务
# ============================================================================
def test_missing_idempotency_key_rejected(env) -> None:
    """强制幂等：缺 ``Idempotency-Key`` → 40001（进不了任何校验/落库）。"""
    c, t = env["client"], env["tokens"]["adm"]
    r = c.post("/api/v1/talent/import", headers=_h(t),
               files=[("files", ("a.doc", b"x", "application/octet-stream"))])
    assert r.json()["code"] == 40001, r.text


def test_create_task_and_poll_terminal(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = _upload(c, t, f"cu14-{uuid.uuid4().hex[:8]}",
                [("zhang.doc", _docx_bytes("姓名：张三 电话 13812348888")),
                 ("li.doc", _docx_bytes("姓名：李四 电话 13987654321"))])
    assert r.json()["code"] == 0, r.text
    body = r.json()["data"]
    assert body["total_count"] == 2
    assert re.fullmatch(r"IMP\d{8}\d{3}", body["task_no"]), body["task_no"]
    assert isinstance(body["task_id"], str) and int(body["task_id"]) > 2**53

    done = _wait_terminal(c, t, body["task_id"])
    assert int(done["data"]["status"]) >= 2, done
    # 两份都是可解析的文本型简历 ⇒ 成功数必须真的落到任务行（账目口径的端到端复核）
    assert int(done["data"]["success_count"]) == 2, done
    assert int(done["data"]["fail_count"]) == 0, done
    # 文件明细已登记
    files = c.get(f"/api/v1/talent/import/{body['task_id']}/files", headers=_h(t)).json()["data"]
    assert len(files) == 2
    assert {f["file_name"] for f in files} == {"zhang.doc", "li.doc"}
    assert {int(f["parse_status"]) for f in files} == {1}, files


def test_idempotent_replay_returns_first_task(env) -> None:
    """重复提交同幂等键 → 40903 + 首次 task_id（先于校验：不需要再传合法文件）。"""
    c, t = env["client"], env["tokens"]["adm"]
    key = f"cu14-{uuid.uuid4().hex[:8]}"
    r1 = _upload(c, t, key, [("a.doc", b"aa")])
    assert r1.json()["code"] == 0, r1.text
    first_id = r1.json()["data"]["task_id"]
    # 第二次提交：即使文件不同（甚至校验会拒绝的空扩展名），也返回首次任务
    r2 = c.post("/api/v1/talent/import",
                headers=_h(t) | {"Idempotency-Key": key},
                files=[("files", ("b.doc", b"bb"))])
    assert r2.json()["code"] == 40903, r2.text
    assert r2.json()["data"]["task_id"] == first_id


def test_unsupported_extension_rejected(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = _upload(c, t, f"cu14-{uuid.uuid4().hex[:8]}", [("evil.exe", b"MZ")])
    assert r.json()["code"] == 41501, r.text
    assert "exe" in r.json()["data"]["unsupported"]


def test_duplicate_content_in_batch_rejected(env) -> None:
    """批内去重：同内容两份文件 → 拒绝（不是静默去重后照单全收）。"""
    c, t = env["client"], env["tokens"]["adm"]
    r = _upload(c, t, f"cu14-{uuid.uuid4().hex[:8]}",
                [("a.doc", b"same-content"), ("b.doc", b"same-content")])
    assert r.json()["code"] == 40002, r.text
    assert r.json()["data"]["duplicate_files"] == ["b.doc"]


def test_unauthenticated_and_permissionless_rejected(env) -> None:
    c = env["client"]
    assert c.post("/api/v1/talent/import").status_code == 401
    r = c.get("/api/v1/talent/import", headers=_h(env["tokens"]["none"]))
    assert r.json()["code"] == 40301


# ============================================================================
# L2 · API-40/42 报告与重试
# ============================================================================
def test_report_and_retry_after_terminal(env) -> None:
    c, t = env["client"], env["tokens"]["adm"]
    r = _upload(c, t, f"cu14-{uuid.uuid4().hex[:8]}", [("zhang.doc", _docx_bytes("姓名：张三"))])
    task_id = r.json()["data"]["task_id"]
    _wait_terminal(c, t, task_id)

    rep = c.get(f"/api/v1/talent/import/{task_id}/report", headers=_h(t))
    assert rep.json()["code"] == 0, rep.text
    # 报告**不含准确率数字**（金标准未定稿前不得给出误导性数字，批次 10 决策）：
    # accuracy_note 是说明文字（指向金标准离线评估），但全文不得出现「N%」形式的准确率
    import re as _re

    note = str(rep.json()["data"].get("accuracy_note") or "")
    assert "金标准" in note
    assert not _re.search(r"准确率[^\n。]*\d+(\.\d+)?\s*%", str(rep.json()["data"]))

    csv_resp = c.get(f"/api/v1/talent/import/{task_id}/report/export", headers=_h(t))
    assert csv_resp.json()["code"] == 0, csv_resp.text
    export = csv_resp.json()["data"]
    assert export["content"].startswith("\ufeff"), "CSV 必须带 BOM（Excel 中文兼容）"
    assert export["filename"].endswith(".csv") and export["row_count"] >= 1

    retry = c.post(f"/api/v1/talent/import/{task_id}/retry", headers=_h(t))
    # 🔴 本用例上传的是**能解析成功**的文件 ⇒ 没有可重试的失败文件。
    #    旧断言写的是 ``in (0, 40001, 40002)`` —— 那是在「账目缺陷让文件恒为失败」的
    #    世界里写的：只要文件永远失败，重试就总能拿到 0。账目修正后，
    #    正确契约是 **42201**（``ILLEGAL_STATE``：没有可重试的失败文件）。
    #    正向的重试路径由 ``tests/test_import_accounting.py`` 覆盖（那里有真失败文件）。
    assert retry.json()["code"] == 42201, retry.text
    assert "没有可重试" in retry.json()["message"], retry.text


def test_task_of_other_tenant_is_40401(env) -> None:
    """导入任务/文件行无行级数据域 → 跨租户/不存在 = 40401（CR-8 口径）。"""
    c, t = env["client"], env["tokens"]["adm"]
    r = c.get("/api/v1/talent/import/7999999999999999999", headers=_h(t))
    assert r.json()["code"] == 40401, r.text


# ============================================================================
# L2 · 批次 20 C 修复回归：任务状态必须来自真实结果，
#      而不是主会话的 REPEATABLE-READ 过期快照（「已完成 / 0 / 3」缺陷）
# ============================================================================
def test_process_task_status_reflects_real_file_outcomes(db, monkeypatch) -> None:
    """🔴 旧缺陷：``process_task`` 里 ``count_by_status(db)`` 读到的是**循环前**的快照
    （各文件已在各自 file_db 会话提交，主会话在 REPEATABLE-READ 下看不见），
    ``final_fail`` 恒 0 → 「1 成功 / 2 失败」的任务被错判成 COMPLETED，
    前端呈现「已完成 / 0 / 3」的自相矛盾。

    修复：计数改用**全新会话**读取已提交的真实状态。
    本用例的假 ``_process_one_file`` 像真实实现一样**逐文件独立会话提交** ——
    修复后的代码必须读到提交后的真实计数；若回落成旧快照口径，这里会挂。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao import import_dao
    from app.dao.session import SessionLocal, engine
    from app.models.enums import ImportFileParseStatus, ImportTaskStatus
    from app.models.talent import ImportFile, ImportTask
    from app.services.import_service import ImportService

    if not sa_inspect(engine).has_table("import_task"):
        pytest.skip("数据库已连通但未执行迁移")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu14c_{suffix}", tenant_name=f"CU14C_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)
    task = ImportTask(tenant_id=tid, task_no=f"IMPC{suffix}", total_count=3, auto_dedup=0)
    db.add(task)
    db.flush()
    outcomes: dict[int, bool] = {}
    for i in (1, 2, 3):
        row = ImportFile(
            tenant_id=tid,
            task_id=int(task.id),
            resume_id=i,
            file_name=f"resume{i}.txt",
            file_ext="txt",
        )
        db.add(row)
        db.flush()
        outcomes[int(row.id)] = i == 1  # 1 成功 / 2 失败
    db.commit()

    def _fake_process_one_file(cls, _db, t, _u, _task, row):
        ok = outcomes[int(row.id)]
        status = int(ImportFileParseStatus.SUCCESS if ok else ImportFileParseStatus.FAILED)
        s = SessionLocal()
        try:
            fresh_row = import_dao.get_file(s, t, int(row.id))
            assert fresh_row is not None
            import_dao.mark_file(s, fresh_row, parse_status=status)
            s.commit()
        finally:
            s.close()
        return ok

    monkeypatch.setattr(ImportService, "_process_one_file", classmethod(_fake_process_one_file))

    result = ImportService.process_task(int(task.id), tid, 0)
    assert result == {"success": 1, "failed": 2}, result

    # 夹具会话持有任务写入前的快照 → 先 commit+expire 再读（test_agent_flow 同款手法）
    db.commit()
    db.expire_all()
    fresh = import_dao.get_task(db, tid, int(task.id))
    assert fresh is not None
    assert int(fresh.status) == int(ImportTaskStatus.PARTIAL_FAILED), (
        "1 成功 / 2 失败时任务必须是 PARTIAL_FAILED —— "
        "判成 COMPLETED 说明又回落到了「过期快照」口径"
    )
    assert int(fresh.success_count) == 1
    assert int(fresh.fail_count) == 2
