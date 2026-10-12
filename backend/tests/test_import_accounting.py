"""批次 21 · 导入解析「逐文件事务」真实落库回归（F-3.1 / SM-2，API-36~43）。

🔴 本用例存在的原因（旧用例的盲区）
====================================
批次 20 的 C 修复把「任务终态计数」改成**用全新会话读已提交状态**，并留下回归用例
``tests/test_talent_import.py::test_process_task_status_reflects_real_file_outcomes``。
但那条用例为了构造「1 成功 / 2 失败」的场景，**monkeypatch 掉了 ``_process_one_file``** ——
而真正的缺陷**恰好在被替换掉的那个函数里**：

``process_task`` 用**主会话** ``list_files(db, ...)`` 加载 ``file_row``，再把它交给
``_process_one_file``；后者用 ``mark_file(file_db, file_row, ...)`` 落状态 ——
``file_row`` 属于**主会话**，``file_db.commit()`` 提交的是一个**空事务**：

1. 逐文件状态**没有**在 file_db 里提交（与 docstring 宣称的「每文件独立事务」不符）；
2. 于是「用全新会话读已提交状态」读到的仍是 ``PENDING`` ⇒ ``final_fail == 0``
   ⇒ 任务被判 ``COMPLETED``，``success_count = fail_count = 0``；
3. 文件状态直到主会话 ``db.commit()``（**在计数之后**）才落库。

真机表现（2026-09-24 IMP20260924001~003 实测）：任务列表显示
「已完成 / 0 / 0」或停在「解析中 0%」，而文件明细里那一份是「失败」；
日志里 ``导入任务完成 IMP20260924002 success=0 fail=0`` —— **账实不符**。

本用例**不替换任何被测函数**：直接调 ``ImportService.create_task(run_async=False)``
走真实的 ``_process_one_file``（含真实 ``rule_extract`` / 归一器 / 入库回调 / 解析图），
断言**任务行**与**文件行**在任务结束后必须一致。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, inspect as sa_inspect, select

from app.core.security import SecurityUtils
from app.core.tenant import CurrentPrincipal
from app.dao import import_dao
from app.dao.session import SessionLocal, engine
from app.models.agent import AgentInterrupt, AgentStep, AgentTask
from app.models.audit import AuditLog
from app.models.enums import ImportFileParseStatus, ImportTaskStatus
from app.models.talent import (
    ImportFile,
    ImportTask,
    TalentBase,
    TalentChangeLog,
    TalentParseField,
    TalentTagRel,
)
from app.models.tenant import Tenant
from app.models.user import SysUser
from app.schemas.talent import ImportCreateForm
from app.services.import_service import ImportService
from tests.conftest import db_available

PWD = "Admin@123456"

_DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
)


def _docx_bytes(text: str) -> bytes:
    """构造最小 docx（零依赖）：``extract_text`` 的 docx 分支读 ``word/document.xml`` 的 ``<w:t>``。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", _DOCX_XML.format(text=text))
    return buf.getvalue()


@pytest.fixture
def acc_env(db) -> Iterator[dict]:
    """独立租户（导入任务无行级数据域 → 用租户隔离即可，跨租户读不到）。"""
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")
    if not sa_inspect(engine).has_table("import_task"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0005_talent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu21_{suffix}", tenant_name=f"CU21_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)
    user = SysUser(
        tenant_id=tid,
        username=f"acc_{suffix}",
        password_hash=SecurityUtils.hash_password(PWD),
        real_name=f"acc_{suffix}",
        status=1,
        login_fail_count=0,
    )
    db.add(user)
    db.flush()
    uid = int(user.id)
    db.commit()

    yield {"tenant_id": tid, "user_id": uid}

    for model in (
        TalentTagRel,
        TalentParseField,
        TalentChangeLog,
        TalentBase,
        AgentInterrupt,
        AgentStep,
        AgentTask,
        ImportFile,
        ImportTask,
        AuditLog,
        SysUser,
        Tenant,
    ):
        col = model.id if model is Tenant else model.tenant_id
        db.execute(delete(model).where(col == tid))
    db.commit()
    # ⚠️ **不清理上传落盘的文件**（`data/uploads/<租户>/…`），故意如此：
    #    本机有**安全删除钩子**（`sitecustomize.py` 的 bulk-delete guard），
    #    它统计的是**本轮累计删除量**（`scope: turn`，阈值 50）—— 只要在用例里删文件，
    #    累计值就可能越线并让进程 `SystemExit(1)`；而 `SystemExit` 出现在 **teardown**，
    #    会连带把**下一条**用例的 setup 变成 "previous item was not torn down properly"。
    #    实测：整轮全量回归因此产出 **3 个 error**（本文件 2 条 + 无辜的 `test_layering` 1 条）。
    #    套件内其余导入用例（`test_talent_import.py`）同样只清 DB 行、不碰盘上文件，
    #    本条与之保持一致；盘上残留由用户在**轮次之外**按需清理。


def _run_sync_import(
    env: dict, files: list[tuple[str, bytes]], *, auto_dedup: bool = True
) -> int:
    """**同步**跑一次导入（run_async=False）→ 返回 task_id。

    ⚠️ 走的是与生产**同一条**代码路径（``_process_one_file`` 未被替换），
    只有「线程/同步」这一点不同 —— 而计数缺陷与线程无关。
    """
    principal = CurrentPrincipal(
        user_id=env["user_id"], tenant_id=env["tenant_id"], org_id=None, roles=("HR",)
    )
    with SessionLocal() as s:
        out = ImportService.create_task(
            s,
            principal,
            idempotent_key=f"cu21-{uuid.uuid4().hex[:8]}",
            form=ImportCreateForm(auto_dedup=auto_dedup),
            uploads=files,
            run_async=False,
        )
        return int(out.task_id)


def _task_row(tid: int, task_id: int) -> tuple[ImportTask, list[ImportFile]]:
    with SessionLocal() as s:
        task = import_dao.get_task(s, tid, task_id)
        assert task is not None
        files = import_dao.list_files(s, tid, task_id)
        s.expunge_all()
        return task, files


# ============================================================================
# L2 · 成功路径：计数必须真的落到任务行
# ============================================================================
def test_success_path_persists_counts_and_talent(acc_env) -> None:
    """成功解析一份文本型简历 ⇒ success_count=1 / fail_count=0 / COMPLETED，且文件行=SUCCESS。

    旧缺陷下这里会看到 ``success_count=0, fail_count=0, status=COMPLETED``：
    任务说「已完成」，却一个成功文件都没有（前端「已完成 / 0 / 0」）。
    """
    tid = acc_env["tenant_id"]
    task_id = _run_sync_import(
        acc_env, [("张三简历.docx", _docx_bytes("姓名：张三 电话 13812348888 学历：本科"))]
    )
    task, files = _task_row(tid, task_id)

    assert len(files) == 1
    file_row = files[0]
    assert int(file_row.parse_status) == int(ImportFileParseStatus.SUCCESS), (
        f"文件状态未按 SUCCESS 落库：parse_status={int(file_row.parse_status)} "
        f"parse_error={file_row.parse_error!r}"
    )
    assert file_row.talent_id is not None, "成功解析必须写入 resume_id → talent_id 映射"
    assert int(task.success_count) == 1, "成功文件必须计入 success_count（旧缺陷：恒 0）"
    assert int(task.fail_count) == 0
    assert int(task.status) == int(ImportTaskStatus.COMPLETED), (
        "无失败文件时任务必须是 COMPLETED —— 若这里挂，说明计数口径又回落到了「过期/未提交快照」"
    )
    with SessionLocal() as s:
        assert import_dao.count_by_status(s, tid, task_id) == {int(ImportFileParseStatus.SUCCESS): 1}
        talent = s.get(TalentBase, int(file_row.talent_id))
        assert talent is not None and talent.name == "张三"


# ============================================================================
# L2 · 失败路径：失败也必须计数（不能「0/0 已完成」）
# ============================================================================
def test_failure_path_counts_are_not_zero(acc_env) -> None:
    """抽不到姓名 ⇒ 整份不进空档案（设计如此），但**任务账目必须记上这一份失败**。

    文件名故意不带中文姓名（``resume-v2.docx``）：否则「文件名兜底」会把姓名补上。
    """
    tid = acc_env["tenant_id"]
    task_id = _run_sync_import(
        acc_env, [("resume-v2.docx", _docx_bytes("电话 13812348888 邮箱 a@b.com"))]
    )
    task, files = _task_row(tid, task_id)

    assert len(files) == 1
    assert int(files[0].parse_status) == int(ImportFileParseStatus.FAILED)
    assert int(task.fail_count) == 1, "失败文件必须计入 fail_count（旧缺陷：恒 0 → 「已完成/0/0」）"
    assert int(task.success_count) == 0
    assert int(task.status) == int(ImportTaskStatus.FAILED), (
        "全部失败时任务必须是 FAILED，不能是 COMPLETED"
    )

    # ---- 重试路径也要记账（``only_pending=True`` 会重新走一遍 ``_process_one_file``）----
    principal = CurrentPrincipal(
        user_id=acc_env["user_id"], tenant_id=tid, org_id=None, roles=("HR",)
    )
    with SessionLocal() as s:
        retry = ImportService.retry_failed(s, principal, task_id, run_async=False)
    assert retry.retryable_count == 1, retry
    task2, files2 = _task_row(tid, task_id)
    assert int(task2.fail_count) == 1, "重试后的账目仍必须与文件行一致"
    assert int(files2[0].parse_status) == int(ImportFileParseStatus.FAILED)


# ============================================================================
# 批次 22 · 「重试」必须真的重新解析（幂等键不得复用）
# ============================================================================
def _parse_agent_tasks(tid: int, file_id: int) -> list[AgentTask]:
    """取某文件对应的 parse 型 Agent 任务（按 id 升序）。"""
    with SessionLocal() as s:
        rows = list(
            s.scalars(
                select(AgentTask)
                .where(
                    AgentTask.tenant_id == tid,
                    AgentTask.task_type == "parse",
                    AgentTask.biz_id == file_id,
                )
                .order_by(AgentTask.id)
            ).all()
        )
        s.expunge_all()
        return rows


def test_retry_really_reparses_instead_of_replaying_idempotent_result(acc_env) -> None:
    """🔴 批次 22 缺陷回归：**重试曾被幂等键「短路」成假动作**。

    缺陷链路（真机实测 IMP20260924001~004 全部点重试无效）：

    1. ``_process_one_file`` 以 ``idempotent_key=parse:{task_id}:{resume_id}`` 建 Agent 任务；
    2. ``retry_failed`` 只把文件的 ``agent_task_id`` 清空，**幂等键仍留在旧 agent_task 行上**；
    3. ``AgentService.start_task`` 命中幂等键 ⇒ **回放首次结果、不重跑解析图**
       （``replayed=True``）⇒ 文件被再标一次失败，``parse_error`` 还被写成回放指纹
       ``PARSE_FAILED``（真实原因码丢失），用户点多少次重试都没有变化。

    ⇒ 断言两条「非平凡」事实：**新增了一个 agent_task**（真跑过），
    且 ``parse_error`` 仍是**真实原因码**（不是回放指纹）。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")
    tid = acc_env["tenant_id"]
    # 文件名不带中文姓名 + 正文只有手机号 ⇒ 无姓名 ⇒ 文件级人工介入（NAME_MISSING）
    task_id = _run_sync_import(acc_env, [("note.docx", _docx_bytes("电话 13812348888"))])

    _task, files = _task_row(tid, task_id)
    file_id = int(files[0].id)
    assert int(files[0].parse_status) == int(ImportFileParseStatus.FAILED)
    assert files[0].parse_error == "NAME_MISSING", files[0].parse_error

    before = _parse_agent_tasks(tid, file_id)
    assert len(before) == 1, f"首次解析应恰好留下 1 个 parse 任务，实际 {len(before)}"

    principal = CurrentPrincipal(
        user_id=acc_env["user_id"], tenant_id=tid, org_id=None, roles=("HR",)
    )
    with SessionLocal() as s:
        ImportService.retry_failed(s, principal, task_id, run_async=False)

    after = _parse_agent_tasks(tid, file_id)
    assert len(after) == 2, (
        f"重试必须新建一个 Agent 任务（真的重跑解析图），实际 {len(after)} 个 —— "
        "若仍是 1，说明又被幂等键短路成了回放"
    )
    assert after[0].idempotent_key is None, (
        "旧 Agent 任务的幂等键必须被作废（重试属非幂等场景），否则它会继续短路后续重试"
    )
    assert after[1].idempotent_key == f"parse:{task_id}:{int(files[0].resume_id)}", (
        f"新任务应重新持有本轮的幂等键，实际 {after[1].idempotent_key!r}"
    )

    _task2, files2 = _task_row(tid, task_id)
    assert int(files2[0].parse_status) == int(ImportFileParseStatus.FAILED)
    assert files2[0].parse_error == "NAME_MISSING", (
        f"重试后必须保留**真实原因码**，实际 {files2[0].parse_error!r} —— "
        "``PARSE_FAILED`` 是幂等回放的指纹（说明没真跑）"
    )


# ============================================================================
# 批次 24 · 启动自愈：进程死亡留下的「解析中」僵尸任务必须能被重拉收敛
# ============================================================================
def test_startup_resume_finds_zombie_parsing_task_and_closes_it(acc_env) -> None:
    """🔴 批次 24 缺陷回归：进程被 kill 在解析中途 ⇒ 任务永远停在「解析中 0%」。

    缺陷链路（真机实测 IMP20260924010，前端截图 14:49 仍显示「解析中 0%」）：
    ``create_task`` 拉起后台线程 → 进程死亡（kill/重启）→ 文件停在 PENDING、
    任务停在 PARSING，**没有任何恢复路径**（``retry_failed`` 只管 FAILED 文件）。

    修复 = 启动自愈：``find_interrupted_tasks`` 必须找到僵尸任务；
    ``process_task(only_pending=True)`` 重入后必须把任务收敛到**与文件行一致的终态**。

    ⚠️ 本用例**故意只测「选择 + 重入收敛」**，不触发 ``_spawn`` 的后台线程
    （线程与 teardown 删行会竞争）；``resume_interrupted`` 只是把选择结果交给
    ``_spawn``，无独立逻辑。

    ⚠️ **僵尸状态必须仿真成真实形态**：``parse_status=0`` **且** ``talent_id=None``。
    批次 21 的每文件独立事务里，``talent_id`` 与 ``parse_status=SUCCESS`` 由
    ``start_task`` 的 ``db.commit()`` **原子提交**（``parse_pipeline.make_persist_callback``
    的幂等锚 ``talent_id`` 已设 ⇒ 直接返回旧 ID），故「``talent_id`` 非空但状态 PENDING」
    在生产中**不可达** —— 只拨 ``parse_status`` 不拨 ``talent_id`` 会造出不可能状态，
    把断言钉在假世界上。
    """
    tid = acc_env["tenant_id"]
    task_id = _run_sync_import(
        acc_env,
        [("张三简历.docx", _docx_bytes("姓名：张三 电话 13812348888 学历：本科"))],
        auto_dedup=False,  # 重入会再建一份同名档案，避免精确键查重产生额外副作用
    )
    _task, files0 = _task_row(tid, task_id)
    file_id = int(files0[0].id)
    assert len(_parse_agent_tasks(tid, file_id)) == 1, "首次解析应恰好留下 1 个 parse 任务"

    # ---- 人为制造「僵尸」：任务拨回 解析中(1)、文件拨回 待解析(0) + 抹掉 talent_id，
    #      等价于「进程死在解析中途、任何逐文件提交都没发生」----
    with SessionLocal() as s:
        task = import_dao.get_task(s, tid, task_id)
        assert task is not None
        import_dao.set_task_status(s, task, int(ImportTaskStatus.PARSING))
        frow = import_dao.list_files(s, tid, task_id)[0]
        frow.parse_status = int(ImportFileParseStatus.PENDING)
        frow.talent_id = None
        s.commit()

    found = ImportService.find_interrupted_tasks()
    assert (task_id, tid, acc_env["user_id"]) in found, (
        f"僵尸「解析中」任务必须被启动自愈找到，实际 {found}"
    )

    # ---- 重入收敛：只处理 PENDING 文件，终态计数用全新会话重算（批次 20 C 修复口径）----
    result = ImportService.process_task(task_id, tid, acc_env["user_id"], only_pending=True)
    task2, files2 = _task_row(tid, task_id)
    assert int(files2[0].parse_status) == int(ImportFileParseStatus.SUCCESS)
    assert int(task2.status) == int(ImportTaskStatus.COMPLETED)
    assert result == {"success": 1, "failed": 0}, result
    assert int(task2.success_count) == 1 and int(task2.fail_count) == 0

    # ---- 🔴 重入必须真的重跑图（批次 22 的幂等回放陷阱不得以「自愈」路径复发）----
    after = _parse_agent_tasks(tid, file_id)
    assert len(after) == 2, (
        f"重入必须新建一个 Agent 任务（真的重跑解析图），实际 {len(after)} 个 —— "
        "若仍是 1，说明又被旧幂等键短路成了回放（文件行会永远停在 PENDING）"
    )
    assert after[0].idempotent_key is None, "旧 Agent 任务的幂等键必须被作废"
    assert after[1].idempotent_key == f"parse:{task_id}:{int(files2[0].resume_id)}", (
        f"新任务应重新持有本轮幂等键，实际 {after[1].idempotent_key!r}"
    )

    # ---- 收敛后不再被自愈选中（否则会无限重拉）----
    assert (task_id, tid, acc_env["user_id"]) not in ImportService.find_interrupted_tasks()

