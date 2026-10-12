"""CU-29 · 人才档案主线（API-30~35 / API-64~66）—— 契约 · 权限 · 结构守卫。

为什么这个文件同时装「单元断言」与「结构断言」
==============================================
本批补齐的是 §6.1 **B-8 / B-9**：API-30~35 与 API-64~66 此前**无人认领**，
`openapi.json` 中实测不存在。因而本文件要守住两类**不同性质**的东西：

1. **行为**：排序白名单、筛选截断、学历码往返、脱敏自检的检出与漏检；
2. 🔴 **结构**：有些要求**光靠行为用例证不出来**，只能扫源码：

   - **T-49 越权与不存在必须同码**：一个「越权返 40301、不存在返 40401」的实现，
     在**功能上完全正确**，却把「这个 ID 是不是真的」变成了可探测的信道。
     故用 AST 断言 `_locate` 里**只有一种**拒绝方式。
   - **T-58 归档幂等**：`archive` 的「已归档」分支**不得 raise** ——
     否则重试会得到一个「失败」，而档案其实早就归档好了。

分层
----
**全部 L1**（不依赖 MySQL / Redis / Milvus）。唯一触网的是 T-41 / T-57，
走 ``TestClient`` 校验「未登录 401」与「端点已注册」，鉴权发生在 DB 访问之前。

⚠️ 每条「拒绝」用例都配**反向对照**（应放行时确实放行）——
否则一个「永远 40301」的实现也能让全部用例通过（同 ``test_search_scope`` 的策略）。
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.masking import verify_outbound
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.exceptions import ErrorCode, PermissionDeniedError
from app.models.enums import RoleDataScope
from app.schemas.talent_master import (
    DETAIL_NOTE_NO_DETAIL_TABLE,
    MASK_MIN_SAMPLES,
    MaskVerifyRequest,
    TalentUpdateRequest,
    education_code_to_int,
    education_int_to_code,
    month_str,
    parse_sort,
    split_csv,
)
from app.services.ask_service import _SESSION_STORE_LIMIT
from app.services.mask_verify_service import MaskVerifyService
from app.services.talent_service import TalentService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TALENT_SERVICE = BACKEND_ROOT / "app" / "services" / "talent_service.py"

TENANT_A = 7000000000000000001
USER_ID = 7000000000000000101
OWN_ORG = 7000000000000000201

#: 角色 → 数据域（与 ``seed_dev.BUILTIN_ROLES`` / 接口 §1.7 一致）
_SCOPE_BY_ROLE: dict[str, int] = {
    "ADM": int(RoleDataScope.ALL),
    "HR": int(RoleDataScope.ALL),
    "MGR": int(RoleDataScope.OWN_DEPT),
    "EMP": int(RoleDataScope.SELF),
    "EXEC": int(RoleDataScope.STATS_ONLY),
    "AIOPS": int(RoleDataScope.STATS_ONLY),
    "AUDIT": int(RoleDataScope.STATS_ONLY),
}

#: CU-29 落地的 9 个端点（路径 → 允许的方法集）
NEW_ENDPOINTS: dict[str, set[str]] = {
    "/api/v1/talents": {"get"},
    "/api/v1/talents/export": {"post"},
    "/api/v1/talents/{talent_id}": {"get", "put", "delete"},
    "/api/v1/talents/{talent_id}/profile": {"get"},
    "/api/v1/talent/ask/sessions/{session_id}": {"get", "delete"},
    "/api/v1/talent/mask/verify": {"post"},
}


def _principal(
    role_code: str,
    *,
    data_scope: int | None = None,
    tenant_id: int = TENANT_A,
    org_id: int | None = OWN_ORG,
    dept_ids: tuple[int, ...] = (),
    user_id: int = USER_ID,
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        roles=(role_code,),
        data_scope=_SCOPE_BY_ROLE[role_code] if data_scope is None else data_scope,
        dept_ids=dept_ids,
    )


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================================
# T-41 端点注册（S-09 的对账基准）
# ============================================================================
def test_t41_new_endpoints_are_registered(app) -> None:
    """本批 9 个端点必须真实出现在 ``openapi.json`` 中。

    🔴 这条用例的**唯一价值**是防「声称交付了但实际没注册」——
    批次 14 的前端就是按契约实现、后端却没接口，`TALENT_MASTER_API_READY`
    还挂着 `false`。端点存在性必须是**可机械复核**的，不能靠人工看 swagger。
    """
    paths = app.openapi()["paths"]
    missing: list[str] = []
    for path, methods in NEW_ENDPOINTS.items():
        if path not in paths:
            missing.append(f"{path}（整个路径缺失）")
            continue
        actual = {m.lower() for m in paths[path]}
        lack = methods - actual
        if lack:
            missing.append(f"{path} 缺方法 {sorted(lack)}")
    assert not missing, "以下端点未注册：" + "；".join(missing)


def test_t41b_six_endpoints_cover_nine_api_ids() -> None:
    """反向对照：6 个路径承载 **9 个 API ID**（合并方法后），别漏数。

    `DELETE/GET/PUT /talents/{id}` 是 3 个接口（31/32/33）、
    `GET/DELETE /talent/ask/sessions/{id}` 是 2 个（64/65）——
    只数「路径条数」会得出 6，而认领的是 9 条。这里把它钉住，
    避免后续有人用「路径数」冒充「接口数」。
    """
    api_ids = sum(len(methods) for methods in NEW_ENDPOINTS.values())
    assert api_ids == 9


# ============================================================================
# T-42 ~ T-45 契约解析（纯函数）
# ============================================================================
def test_t42_sort_whitelist_falls_back_instead_of_erroring() -> None:
    """排序字段不在白名单 → **退回默认**（不报错）。

    与「筛选值拼错必须报错」是**不同的取舍**：筛选拼错会让用户以为「真的没数据」，
    必须报错；而排序拼错时结果集**仍然完整可见**，只是顺序不同 ——
    为一个列名让整页 400 不划算。
    """
    assert parse_sort(None) == ("updated_at", "desc")
    assert parse_sort("-work_years") == ("work_years", "desc")
    assert parse_sort("created_at") == ("created_at", "asc")
    # 非法值 → 回退默认，且不抛
    assert parse_sort("password") == ("updated_at", "desc")
    assert parse_sort("-; DROP TABLE talent_base") == ("updated_at", "desc")


def test_t43_csv_filter_is_truncated_not_rejected() -> None:
    """逗号串超限 → **截断**（不报错）。

    截断后查询仍在权限内（收窄校验在 Service 层照跑），不存在越权；
    而「前端多勾了几个部门就直接 400」是很差的可用性。
    """
    assert split_csv(None, limit=50) is None
    assert split_csv("", limit=50) is None
    assert split_csv(" , ,", limit=50) is None
    assert split_csv("1, 2 ,3", limit=50) == ("1", "2", "3")
    assert len(split_csv(",".join(str(i) for i in range(60)), limit=50) or ()) == 50


def test_t44_education_code_round_trip() -> None:
    """学历「两位数字码」↔ int 必须无损往返（口径见 schemas/talent_master ①）。"""
    assert education_int_to_code(4) == "04"
    assert education_code_to_int("04") == 4
    assert education_code_to_int(education_int_to_code(1)) == 1
    # 0 / None = 不限
    assert education_int_to_code(0) is None
    assert education_int_to_code(None) is None


def test_t44b_non_numeric_education_code_degrades_to_none() -> None:
    """反向对照：字典码若被 ADM 改成非数字（如 ``BACHELOR``），**降级而非 500**。

    这是刻意留的容错口子：字典是可由 ADM 维护的（F-1.7），
    某天把码改成文字后，档案详情不该整页打不开 —— 出参给 ``None``、
    名称照常下发，比 500 好得多。
    """
    assert education_code_to_int("BACHELOR") is None
    assert education_code_to_int("") is None


def test_t45_birth_month_only_keeps_year_month() -> None:
    """``birth_date`` → ``birth_month`` **只到月**（最小必要，接口字段名即如此）。"""
    from datetime import date

    assert month_str(date(1993, 6, 1)) == "1993-06"
    assert month_str(date(1993, 6, 28)) == "1993-06"  # 「日」必须被丢掉
    assert month_str(None) is None


# ============================================================================
# T-46 ~ T-48 权限判定
# ============================================================================
def test_t46_non_privileged_role_cannot_query_non_active_status() -> None:
    """MGR 查「非有效」状态 → 40301（接口 API-30 备注：仅 ADM/HR 可查 2/4）。"""
    with pytest.raises(PermissionDeniedError):
        TalentService._resolve_data_status(_principal("MGR"), 2)


def test_t46b_privileged_role_can_query_non_active_status() -> None:
    """反向对照：ADM / HR 查「已合并(2)」必须放行，否则上一条会被「永远拒绝」蒙混。

    🔁 批次 23 判据反转：``_resolve_data_status`` 的返回从 ``int`` 改为 ``tuple[int, ...]``
    （默认口径从「仅 1 有效」扩为「ADM/HR = 0 待复核 + 1 有效」），断言随之从
    ``== 2`` 收紧为 ``== (2,)`` —— **不是放松**：元组相等比整数相等更严格。
    """
    assert TalentService._resolve_data_status(_principal("ADM"), 2) == (2,)
    assert TalentService._resolve_data_status(_principal("HR"), 2) == (2,)
    # 未传 → 批次 23 反转后的默认口径（见 T-46c）
    assert TalentService._resolve_data_status(_principal("MGR"), None) == (1,)


def test_t46c_default_status_includes_pending_for_privileged_roles() -> None:
    """🔴 批次 23 缺陷回归：「导入成功但档案列表只有一条」。

    根因：导入落库的档案是 ``0 待复核``，而旧默认口径只查 ``1 有效`` ⇒
    用户导入成功后档案页**默认一条都看不到**（连管理员也是）。
    反转后：**ADM/HR 未传 = (0, 1)**；其余角色维持 ``(1,)``（保护性规则不变 ——
    MGR 不应把未核对的数据当事实用于决策，这个意图的落点是「限角色」而非「藏数据」）。
    ⚠️ 若这里改回「对所有人仅 (1,)」而不同步修改前端文案与产品口径，
    「导入成功但列表为空」的缺陷会原样复现。
    """
    assert TalentService._resolve_data_status(_principal("ADM"), None) == (0, 1)
    assert TalentService._resolve_data_status(_principal("HR"), None) == (0, 1)
    assert TalentService._resolve_data_status(_principal("MGR"), None) == (1,)
    # 显式指定仍是单元素（且非 1 的权限规则已在 T-46 覆盖）
    assert TalentService._resolve_data_status(_principal("ADM"), 0) == (0,)


def test_t46d_data_status_sequence_becomes_in_condition() -> None:
    """DAO 层：``data_status`` 接受序列 ⇒ 生成 ``IN (0, 1)``；单值保持 ``=``。

    只测条件构造（L1，无 DB）：若这里退化成 ``= (0, 1)`` 这类错误拼接，
    SQL 会直接报错或在「默认口径」下查不到任何行。
    """
    import sqlalchemy as sa

    from app.dao.talent_dao import _talent_conditions

    conds = _talent_conditions(TENANT_A, scope_clause=sa.true(), data_status=(0, 1))
    stmt = sa.select(sa.literal(1)).where(*conds)
    text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "data_status IN (0, 1)" in text, text

    conds_single = _talent_conditions(TENANT_A, scope_clause=sa.true(), data_status=2)
    text_single = str(
        sa.select(sa.literal(1)).where(*conds_single).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "data_status = 2" in text_single, text_single


def test_t47_emp_scope_is_restricted_to_self() -> None:
    """EMP（``data_scope=3``）的行级条件必须是 ``user_id == 自己``（「仅本人」的落点）。

    🔴 这条断言的是「EMP 仅本人」**不靠角色名硬编码**，而由与检索侧同源的
    ``DataScopeFilter`` 推导 —— 若有人把 owner_column 写错（如用 ``id``），
    EMP 就会看到「ID 等于自己 UID 的档案」，行为上完全错但很难察觉。
    """
    from app.models.talent import TalentBase

    principal = _principal("EMP")
    clause = DataScopeFilter.mysql_condition(
        TalentBase, principal, owner_column="user_id"
    )
    text = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "user_id" in text
    assert str(USER_ID) in text
    assert "tenant_id" in text  # 租户条件永远第一位


@pytest.mark.parametrize("role", ["EXEC", "AIOPS", "AUDIT"])
def test_t48_stats_only_roles_are_denied_on_detail(role: str) -> None:
    """``data_scope=4 仅统计值`` 访问明细 → 40301（接口 §1.7）。

    这三条角色（EXEC/AIOPS/AUDIT）在档案主线上**一格都不该看到**，
    与矩阵里 API-30~35 的 ❌ 逐格对应。
    """
    with pytest.raises(PermissionDeniedError):
        DataScopeFilter.require_detail_access(_principal(role))
    # 且列表侧的 MySQL 条件也必须拒绝（不能「矩阵说不行、SQL 却放行」）
    from app.models.talent import TalentBase

    with pytest.raises(PermissionDeniedError):
        DataScopeFilter.mysql_condition(TalentBase, _principal(role))


def test_t48b_mgr_and_emp_are_allowed_on_detail() -> None:
    """反向对照：MGR / EMP 访问明细必须放行（否则上一条形同虚设）。"""
    DataScopeFilter.require_detail_access(_principal("MGR"))
    DataScopeFilter.require_detail_access(_principal("EMP"))


# ============================================================================
# T-49 结构：越权与不存在必须同码（AST）
# ============================================================================
def test_t49_locate_never_distinguishes_missing_from_denied() -> None:
    """🔴 ``_locate`` 对外**只能有一种拒绝方式**（同码 40301）。

    一个「越权 40301 / 不存在 40401」的实现功能上正确，却把状态码
    变成了**资源存在性的探测器** —— 拿一个 ID 字典把全库扫一遍就能画出
    「哪些 ID 是真的」。故这里机械断言：

    1. 函数内**不得**出现 ``NotFoundError``；
    2. 所有 ``raise PermissionDeniedError`` 的**消息字面量只有一个**。

    ⚠️ 内部日志**允许**区分（那是给运维看的），故只扫 ``raise`` 语句。
    """
    tree = ast.parse(_read_source(TALENT_SERVICE))
    locate = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_locate"
        ),
        None,
    )
    assert locate is not None, "talent_service._locate 不存在（被重命名？）"

    raised: list[str] = []
    for node in ast.walk(locate):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            raised.append(exc.func.id)
        elif isinstance(exc, ast.Name):
            raised.append(exc.id)

    assert "NotFoundError" not in raised, "40401 会让「档案是否存在」变成可探测信号"
    assert raised == ["PermissionDeniedError"], f"拒绝方式不唯一：{raised}"


# ============================================================================
# T-50 ~ T-52 脱敏自检（API-66）
# ============================================================================
@pytest.fixture
def no_audit(monkeypatch):
    """把审计写成空操作：自检的**判定逻辑**与「是否落审计」是两件事，分开测。"""
    from app.services.audit_service import AuditService

    monkeypatch.setattr(AuditService, "record", lambda *a, **k: None)


def test_t50_known_name_residue_is_detected(no_audit) -> None:
    """已知人名残留必须被检出（CL-4 第 2 条：出网前二次扫描）。"""
    out = MaskVerifyService.verify(
        MaskVerifyRequest(samples=["候选人李伟的联系方式如下"], known_names=["李伟"]),
        tenant_id=TENANT_A,
        user_id=USER_ID,
    )
    assert out.passed is False
    assert out.leak_count == 1
    assert "name" in out.leaked_types


def test_t50b_clean_sample_passes(no_audit) -> None:
    """反向对照：已脱敏的文本必须**通过**（否则上一条会被「永远判泄漏」蒙混）。"""
    out = MaskVerifyService.verify(
        MaskVerifyRequest(samples=["候选人{{CANDIDATE}}的联系方式如下"]),
        tenant_id=TENANT_A,
        user_id=USER_ID,
    )
    assert out.passed is True
    assert out.leak_count == 0


def test_t51_phone_writing_variants_are_a_registered_gap(no_audit) -> None:
    """**缺口固化（G-03）**：手机号的书写变体当前**漏检**。

    这不是「发现了新 bug」，而是把评审文档 §7.3 批次 15 的 G-03
    **变成会失败的断言** —— 一旦有人把归一化补上，这条用例会失败，
    提醒把断言反转（并同步 G-03 的状态）。
    """
    for variant in ["+8613800138000", "138-0013-8000", "138 0013 8000"]:
        out = MaskVerifyService.verify(
            MaskVerifyRequest(samples=[f"联系电话 {variant}"]),
            tenant_id=TENANT_A,
            user_id=USER_ID,
        )
        assert out.passed is True, (
            f"变体 {variant!r} 已被检出 —— 说明 G-03 已闭环，请反转本断言并更新缺口台账"
        )


def test_t52_verify_response_never_echoes_raw_samples(no_audit) -> None:
    """🔴🔴 **本文件最重要的一条**：自检出参**绝不回显原文**。

    本接口的输入就是「可能含明文的文本」。若出参把原文带回去，
    这个「查泄漏」的接口自己就成了泄漏源 —— 它会出现在响应体、访问日志、
    前端缓存、以及任何抓这个请求的中间设备里。

    断言方式：构造一条含真实手机号与姓名的样例，要求响应里
    **任何字段都不包含**这些原文片段。
    """
    raw_phone = "13800138000"
    raw_name = "李伟"
    out = MaskVerifyService.verify(
        MaskVerifyRequest(samples=[f"{raw_name} 的电话是 {raw_phone}"], known_names=[raw_name]),
        tenant_id=TENANT_A,
        user_id=USER_ID,
    )
    dumped = out.model_dump_json()
    assert raw_phone not in dumped, "响应里出现了明文手机号"
    assert raw_name not in dumped, "响应里出现了明文姓名"
    # 🔴 更强的**自洽不变量**：「查泄漏」接口的响应体，自己必须能过生产出网扫描。
    # 这条比逐字面量通用 —— 它不要求我们先猜到「哪句话里会夹带 PII」。
    # 实测价值：本用例首次运行时正是**靠全量扫描**抓出 ``_KNOWN_BOUNDARY`` 的一句
    # 「举例说明」文案里含形如手机号的数字串（子串命中生产正则）——
    # 逐字段白名单式断言会漏掉这种「无害文案造成的泄漏」。
    assert verify_outbound(dumped).passed, "自检接口的响应体自身未通过出网扫描"
    # 预览必须是脱敏形态（占位符 / 打星），而不是原文
    assert out.leaks, "该样例应当被判为漏检（人名精确匹配未命中时至少留个记录）"
    preview = out.leaks[0].preview
    assert "{{" in preview or "*" in preview


def test_t53_session_store_limit_meets_api64_contract() -> None:
    """API-64 承诺「≥10 轮」→ 存储上限必须真的 ≥10。

    🔴 修正前存储与 Prompt 窗口共用常量（4），API-64 的承诺**在实现上直接落空**。
    这条用例把它钉住：以后谁把 ``_SESSION_STORE_LIMIT`` 调回 4 就会红。
    """
    assert _SESSION_STORE_LIMIT >= 10


def test_t54_detail_note_only_when_detail_requested() -> None:
    """履历明细缺失的说明**只在 ``with_detail=true`` 时出现**。

    ``with_detail=false`` 时空数组是「参数决定不返回」，不是「后端没做」——
    那时挂一个「明细表未建模」的说明会误导前端。
    """
    assert DETAIL_NOTE_NO_DETAIL_TABLE.strip()
    assert "TBL-14" in DETAIL_NOTE_NO_DETAIL_TABLE


def test_t54b_detail_maps_work_and_project_rows(monkeypatch) -> None:
    """API-31 读取真实明细，并显式把数据库列名映射为接口列名。"""
    principal = _principal("ADM", tenant_id=TENANT_A)
    talent = SimpleNamespace(
        id=7000000000000000301,
        name="李伟",
        gender=1,
        birth_date=date(1993, 6, 1),
        education_level="04",
        school="某大学",
        major="计算机",
        work_years=6,
        id_card_enc=None,
        phone_enc=None,
        email_enc=None,
        org_id=None,
        data_status=1,
        version_no=2,
        updated_at=datetime(2026, 9, 27, 10, 0, 0),
    )
    work = SimpleNamespace(
        id=7000000000000000401,
        company="某某科技",
        dept_name="数据平台部",
        position_name="数据治理工程师",
        start_date=date(2020, 3, 1),
        end_date=date(2023, 8, 31),
        is_current=0,
        description="负责平台建设",
    )
    project = SimpleNamespace(
        id=7000000000000000501,
        project_name="主数据平台",
        role_name="技术负责人",
        start_date=date(2021, 1, 1),
        end_date=None,
        description="搭建主数据体系",
        tech_stack=["Python", "MySQL"],
    )
    monkeypatch.setattr(TalentService, "_education_names", lambda *_: {"04": "硕士"})
    monkeypatch.setattr(TalentService, "_org_names", lambda *_: {})
    monkeypatch.setattr("app.services.talent_service.talent_dao.list_work_exps", lambda *_: [work])
    monkeypatch.setattr("app.services.talent_service.talent_dao.list_project_exps", lambda *_: [project])

    out = TalentService._to_detail(object(), principal, talent, with_detail=True)

    assert out.detail_note and "TBL-14" in out.detail_note
    assert out.work_exps[0].model_dump(mode="json") == {
        "id": str(work.id),
        "company": "某某科技",
        "dept_name": "数据平台部",
        "title": "数据治理工程师",
        "start_date": "2020-03-01",
        "end_date": "2023-08-31",
        "is_current": False,
        "description": "负责平台建设",
    }
    assert out.project_exps[0].model_dump(mode="json")["role"] == "技术负责人"
    assert out.project_exps[0].tech_stack == ["Python", "MySQL"]


def test_t54c_detail_without_detail_does_not_query_exps(monkeypatch) -> None:
    """with_detail=false 只返回主档，不触发两张明细表查询。"""
    principal = _principal("ADM", tenant_id=TENANT_A)
    talent = SimpleNamespace(
        id=7000000000000000302, name="李伟", gender=None, birth_date=None,
        education_level=None, school=None, major=None, work_years=None,
        id_card_enc=None, phone_enc=None, email_enc=None, org_id=None,
        data_status=1, version_no=1, updated_at=None,
    )
    monkeypatch.setattr(TalentService, "_education_names", lambda *_: {})
    monkeypatch.setattr(TalentService, "_org_names", lambda *_: {})
    monkeypatch.setattr(
        "app.services.talent_service.talent_dao.list_work_exps",
        lambda *_: (_ for _ in ()).throw(AssertionError("不应查询工作经历")),
    )
    monkeypatch.setattr(
        "app.services.talent_service.talent_dao.list_project_exps",
        lambda *_: (_ for _ in ()).throw(AssertionError("不应查询项目经历")),
    )

    out = TalentService._to_detail(object(), principal, talent, with_detail=False)

    assert out.work_exps == [] and out.project_exps == []
    assert out.detail_note is None


def test_t55_mask_verify_rejects_empty_samples() -> None:
    """样例全为空串 → 参数错误（不能拿「0 条样例」冒充「通过」）。"""
    with pytest.raises(ValidationError):
        MaskVerifyRequest(samples=["   ", "\n"])


def test_t55b_mask_verify_marks_insufficient_sample_count(no_audit) -> None:
    """样例不足 20 条 → ``meets_minimum=false``（**不报错但显式标记**）。

    需求 §12.3 的下限是 20 条；若直接把少量样例判「通过」，
    会得到一份**看起来像验收证据**的东西。故返回报告 + 明确标记。
    """
    out = MaskVerifyService.verify(
        MaskVerifyRequest(samples=["干净文本"]), tenant_id=TENANT_A, user_id=USER_ID
    )
    assert out.sample_count == 1
    assert out.minimum_required == MASK_MIN_SAMPLES
    assert out.meets_minimum is False
    assert out.note and "不构成验收证据" in out.note


def test_t56_update_request_forbids_unknown_fields() -> None:
    """编辑请求的 ``extra="forbid"``：白名单外字段 → 40001，**不静默忽略**。

    静默忽略会让调用方以为「改成功了」（比如有人试图改 ``data_status``）——
    那是比报错危险得多的失败模式。
    """
    for bad in [{"data_status": 1}, {"version_no": 99}, {"id_card": "110101199003071234"}]:
        with pytest.raises(ValidationError):
            TalentUpdateRequest(**bad)
    # 反向对照：白名单内的字段正常
    payload = TalentUpdateRequest(name="张三", work_years=5)
    assert payload.provided() == {"name": "张三", "work_years": 5}


# ============================================================================
# T-57 端点级：未登录一律 40101（不依赖 DB）
# ============================================================================
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/talents"),
        ("get", "/api/v1/talents/1"),
        ("get", "/api/v1/talents/1/profile"),
        ("get", "/api/v1/talent/ask/sessions/s-abc"),
        ("delete", "/api/v1/talent/ask/sessions/s-abc"),
        ("post", "/api/v1/talent/mask/verify"),
    ],
)
def test_t57_endpoints_require_authentication(client, method: str, url: str) -> None:
    """未带 Token 调用 → 统一 40101（**认证先于授权与 DB 访问**）。

    这条同时是「端点真的挂上了」的**行为级**证据（T-41 只看 openapi 声明，
    而声明与运行时不符也是可能的：路由注册了但被中间件短路）。

    ⚠️ 写得**宽松**是刻意的：认证校验必须先于请求体校验发生，故这里
    GET/DELETE **不带** body（``TestClient.get()`` 也不接受 ``json=``），
    POST 才带一个空 body。否则「40001 参数错误」会被误当成认证失败。
    """
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(url, **kwargs)
    body = resp.json()
    assert body["code"] == int(ErrorCode.UNAUTHENTICATED), body


# ============================================================================
# T-58 结构：归档必须幂等（AST）
# ============================================================================
def test_t58_archive_branch_for_already_archived_never_raises() -> None:
    """🔴 ``archive`` 的「已归档」分支**不得 raise**（不可逆动作必须幂等）。

    一个「已归档 → 抛 42201」的实现看起来很合理，实际很糟：
    前端对不可逆动作会带 ``Idempotency-Key`` 重试，重试得到「失败」会让用户
    以为归档没成功而反复点击 —— 而是事实上档案**早就归档好了**。

    故机械断言：``ARCHIVED`` 分支体内**没有 Raise 节点**。
    """
    tree = ast.parse(_read_source(TALENT_SERVICE))
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "archive"
        ),
        None,
    )
    assert func is not None, "talent_service.archive 不存在（被重命名？）"

    archived_branches = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.If)
        and "ARCHIVED" in ast.dump(node.test)
    ]
    assert archived_branches, "未找到「已归档」判断分支（实现被改写？）"

    for branch in archived_branches:
        raises = [n for n in ast.walk(branch) if isinstance(n, ast.Raise)]
        assert not raises, "「已归档」分支抛异常 → 重试会拿到失败，破坏幂等语义"
