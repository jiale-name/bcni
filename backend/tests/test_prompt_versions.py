"""CU-11 · Prompt 版本管理（API-84 ~ API-87，F-9.2）。

分层
----
- **L1 契约层**（不依赖 MySQL / Redis，任何机器都能跑）：请求模型的三条不变式
  （``extra=forbid``、``prompt_code`` 格式、``rejected`` 类语义）、``_snapshot`` 截断、
  状态名用词、4 个端点在 OpenAPI 中的登记、**权限矩阵的静态钉死**、种子与契约的一致性。
- **L2 集成层**（缺依赖时 skip 而非 fail）：4 个接口端到端、版本只追加、
  族内唯一启用、回滚=追加、40901 / 42201 / 40401 / 40301、**审计留痕真的落了库**。

🔴 本文件守护的三条不变式（都在 ``prompt_service`` 的模块 docstring 里写了理由）
--------------------------------------------------------------------------
| 不变式 | 破了会怎样 | 对应用例 |
| --- | --- | --- |
| 版本正文**只追加**，永不改写历史 | ``ai_call_log.prompt_version`` 指向一份变过的正文，故障无法复现 | ``test_update_creates_new_version_and_keeps_history`` |
| 同族内**最多一个启用版本** | 「当前生效版本」有歧义（最新 ≠ 启用） | ``test_single_enabled_version_invariant_after_update`` |
| 「改了但内容一样」拒绝（42201） | 版本号膨胀到失去信息量，追溯退化成翻几十个相同版本 | ``test_update_with_same_content_is_42201`` |

每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）——
否则一个「永远抛错」的实现也能让全部用例通过。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.core.security import SecurityUtils
from app.exceptions import ErrorCode
from app.models.ai import PromptTemplate
from app.models.audit import AuditLog
from app.models.enums import RoleDataScope
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.ai import (
    PROMPT_CODE_PATTERN,
    PromptCreateRequest,
    PromptRollbackRequest,
    PromptScene,
    PromptUpdateRequest,
)
from app.services.prompt_service import MAX_CONTENT_CHARS, PromptService
from tests.conftest import db_available

PWD = "Admin@123456"

#: 接口 §2.2 里 API-84~87 的权限码（CU-11 新增，授 AIOPS；ADM 由 ``require_perm`` 短路）
PROMPT_PERMS = (
    "ai:prompt:list",
    "ai:prompt:create",
    "ai:prompt:update",
    "ai:prompt:rollback",
)

#: 一个**不存在的雪花 ID**（比 number 安全整数大，且库里必然没有）
_UNKNOWN_ID = "7999999999999999999"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约：请求模型的三条不变式
# ============================================================================
def test_update_request_forbids_code_and_version() -> None:
    """🔴 API-86 的契约里**没有** ``prompt_code`` / ``version``，且多传即报错。

    - 改 ``prompt_code`` = 换了模板族（历史版本全部失去归属）；
    - 改 ``version`` = 篡改历史（版本号即时间线）。

    两者都属于「静默忽略最危险」的那一类：调用方以为改掉了，实际没改（或反过来）。
    """
    assert "prompt_code" not in PromptUpdateRequest.model_fields
    assert "version" not in PromptUpdateRequest.model_fields

    with pytest.raises(ValidationError):
        PromptUpdateRequest(content="x", prompt_code="HACKED")
    with pytest.raises(ValidationError):
        PromptUpdateRequest(content="x", version=99)


def test_update_request_requires_non_empty_content() -> None:
    """空正文 → 校验失败（HTTP 层的 40001）。

    放行的后果不是「少一个字段」，而是**新版本正文为空** ——
    而它会被置为启用，线上每一次调用都把空提示词发给模型。
    """
    with pytest.raises(ValidationError):
        PromptUpdateRequest(content="")
    with pytest.raises(ValidationError):
        PromptUpdateRequest()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("resume_extract", "RESUME_EXTRACT"),  # 小写 → 转大写（不拒绝）
        ("  RESUME_EXTRACT  ", "RESUME_EXTRACT"),  # 首尾空白被裁掉
        ("v2_extract", "V2_EXTRACT"),
    ],
)
def test_prompt_code_is_normalised(raw: str, expected: str) -> None:
    """合法编码的规范化：裁剪 + 转大写。

    ⚠️ 转大写而不是「拒绝小写」—— 编码是要手写进代码与配置的标识符，
    「输入小写被拒」在实践中只会让人反复试错。字典选择拒绝大写是因为那边的
    排序规则大小写不敏感且有历史数据，两处口径**有意不同**（理由各自写在契约里）。
    """
    assert PromptCreateRequest(prompt_code=raw, scene="extract", content="x").prompt_code == expected


@pytest.mark.parametrize(
    "bad",
    [
        "简历抽取",  # 🔴 关键一条：str.isalnum() 对汉字返回 True，必须靠正则拦住
        "resume-extract",
        "resume extract",
        "_leading",
        "a",
        "x" * 65,
    ],
)
def test_prompt_code_rejects_non_ascii_and_bad_shapes(bad: str) -> None:
    """🔴 编码必须匹配 :data:`PROMPT_CODE_PATTERN`。

    这条专门拦 ``"简历抽取"``：Python 的 ``"简历抽取".isalnum()`` 是 ``True``，
    早期实现用 ``isalnum()`` 判「只允许字母数字下划线」，于是**中文编码被放行**。
    编码是给日志筛、给前端下拉、给 grep 用的标识符，混进中文后
    「按编码查调用日志」这类日常运维动作会立刻失效。
    """
    with pytest.raises(ValidationError):
        PromptCreateRequest(prompt_code=bad, scene="extract", content="x")


def test_prompt_code_pattern_accepts_digits_and_underscores() -> None:
    """反向对照：数字开头、含下划线的编码必须放行（种子里的 ``JUDGE_SCORE`` 等即此类）。"""
    import re

    for good in ("V2", "JUDGE_SCORE", "A1_B2", "9X"):
        assert re.fullmatch(PROMPT_CODE_PATTERN, good)
        assert PromptCreateRequest(prompt_code=good, scene="extract", content="x").prompt_code == good


def test_create_request_scene_must_be_in_prompt_scene() -> None:
    """``prompt_template.scene`` 是受控词表（``extract/rewrite/ask/judge``）。

    与 :class:`~app.schemas.ai.AiScene` **不是同一套取值**（一组是调用场景、
    一组是提示词用途），本批刻意不合并。写错的后果是「Prompt 永远被检索不到」，
    而不是报错 —— 所以必须在入口拒绝。
    """
    for good in PromptScene.LABELS:
        assert PromptCreateRequest(prompt_code="X1", scene=good, content="x").scene == good
    with pytest.raises(ValidationError):
        # ``resume_parse`` 看着很像，它是 **AiScene** 的值，不是 PromptScene 的
        PromptCreateRequest(prompt_code="X1", scene="resume_parse", content="x")


def test_prompt_scene_and_ai_scene_are_deliberately_different() -> None:
    """两套场景词表**刻意不合并**：交集的取值只允许有 ``judge`` 一个。

    合并会让 Prompt 的 ``scene`` 语义被调用场景绑架（加一个调用场景就要加一条 Prompt 场景）。
    这条断言的作用是：若哪天有人「顺手统一」，测试立刻失败并要求他走评审。
    """
    from app.schemas.ai import AiScene

    assert set(PromptScene.LABELS) & set(AiScene.LABELS) == {"judge"}
    assert len(PromptScene.LABELS) == 4


def test_rollback_request_requires_positive_target_version() -> None:
    """``target_version`` 必须 ≥ 1（``v0`` 不存在；负数一定是调用方算错了）。"""
    with pytest.raises(ValidationError):
        PromptRollbackRequest(target_version=0)
    with pytest.raises(ValidationError):
        PromptRollbackRequest(target_version=-1)
    assert PromptRollbackRequest(target_version=3, remark="回退到 v3").target_version == 3


def test_rollback_request_rejects_extra_fields() -> None:
    """回滚请求只接受 ``target_version`` / ``remark``（``extra=forbid``）。

    多传字段被静默忽略是最坏的一种「成功」：调用方以为「按版本号回滚」
    这次调用还改了别的东西，而实际什么都没发生。
    """
    with pytest.raises(ValidationError):
        PromptRollbackRequest(target_version=2, prompt_code="X")


# ============================================================================
# L1 · 正文校验与快照（纯函数，不需要数据库）
# ============================================================================
def test_validate_content_rejects_blank_and_oversize() -> None:
    """正文校验：空白 → 40001；超长 → 40001（附 ``max_chars`` / ``actual_chars``）。

    ⚠️ 关键点是 ``strip``：一份「只由换行与空格组成」的正文不是空字符串，
    只用 ``if not content`` 判会漏过它，而它对模型来说等价于「什么都没给」。
    """
    from app.exceptions import BizError

    with pytest.raises(BizError) as blank:
        PromptService._validate_content("   \n\t ")
    assert blank.value.code == int(ErrorCode.PARAM_INVALID)

    with pytest.raises(BizError) as oversize:
        PromptService._validate_content("x" * (MAX_CONTENT_CHARS + 1))
    assert oversize.value.code == int(ErrorCode.PARAM_INVALID)
    assert oversize.value.data["max_chars"] == MAX_CONTENT_CHARS
    assert oversize.value.data["actual_chars"] == MAX_CONTENT_CHARS + 1


def test_validate_content_trims_and_accepts() -> None:
    """反向对照：正常正文被裁剪后**原样返回**（不截断、不改内容）。"""
    assert PromptService._validate_content("  抽取 {text}  ") == "抽取 {text}"
    long_ok = "y" * MAX_CONTENT_CHARS
    assert PromptService._validate_content(long_ok) == long_ok


def test_snapshot_truncates_content_but_keeps_length() -> None:
    """审计快照：正文**截断到 500 字符**，但 ``content_chars`` 记真实长度。

    审计列表要渲染、要导出 CSV。把整篇 Prompt（上限 2 万字符）塞进每条审计记录，
    会让「查一次审计」变成下载几 MB 文本。需要全文时按 ``target_id`` 回查
    ``prompt_template`` —— 历史版本永不删，所以**一定查得到**（这正是「只追加」的好处）。
    """
    row = PromptTemplate(
        id=1, tenant_id=1, prompt_code="X", scene="extract",
        version=7, content="z" * 800, status=1, created_by=None,
    )
    snap = PromptService._snapshot(row, remark="改了一版")

    assert len(snap["content_excerpt"]) == 501, "500 字符 + 省略号"
    assert snap["content_excerpt"].endswith("…")
    assert snap["content_chars"] == 800, "截断的是展示片段，长度必须是真实值"
    assert snap["version"] == 7 and snap["status_name"] == "启用"
    assert snap["remark"] == "改了一版"

    short = PromptService._snapshot(
        PromptTemplate(
            id=2, tenant_id=1, prompt_code="X", scene="extract",
            version=1, content="短正文", status=0,
        ),
        remark=None,
    )
    assert short["content_excerpt"] == "短正文", "短正文不截断也不加省略号"


def test_prompt_status_labels_are_draft_enabled_disabled() -> None:
    """``prompt_template.status`` 的三态用词：``0 草稿 / 1 启用 / 2 停用``。

    与 ``EnabledStatus``（0 停用 / 1 启用）**语义不同**：Prompt 多一个「草稿」态，
    而草稿与停用的区别是「还没上过线」vs「上过线又被撤下」——
    混用会让「这个模板有用过吗」无法回答。
    """
    from app.models.enums import EnabledStatus, PromptStatus

    assert PromptStatus.name_of(0) == "草稿"
    assert PromptStatus.name_of(1) == "启用"
    assert PromptStatus.name_of(2) == "停用"
    assert PromptStatus.name_of(0) != EnabledStatus.name_of(0)


# ============================================================================
# L1 · 路由登记（4 个端点必须都在，且路径/方法正确）
# ============================================================================
def test_prompt_endpoints_are_registered(app) -> None:
    """4 个端点必须出现在 OpenAPI 中（路径 + 方法），且 **health 式冲突不存在**。

    用 OpenAPI 而非 ``app.routes`` 断言：FastAPI 0.141 起 ``include_router``
    不再把子路由摊平进 ``app.routes``（批次 1 环境实测结论）。
    """
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/ai/prompts": {"get", "post"},
        "/api/v1/ai/prompts/{prompt_id}": {"put"},
        "/api/v1/ai/prompts/{prompt_id}/rollback": {"post"},
    }
    for path, methods in expected.items():
        assert path in paths, f"OpenAPI 缺少路径 {path}"
        actual = {m for m in paths[path] if m in {"get", "post", "put", "delete"}}
        assert methods <= actual, f"{path} 缺少方法 {methods - actual}"

    # ⚠️ API-86 是 PUT（接口 §2.2 的方法列），而语义是部分/衍生更新 ——
    #    「不是 PATCH」这个选择必须被钉死，否则有人会「顺手改成 PATCH」而前端联调清单对不上。
    assert "patch" not in paths["/api/v1/ai/prompts/{prompt_id}"]


def test_ai_endpoints_do_not_expose_call_log_writes(app) -> None:
    """🔴 ``ai_call_log`` **不得有任何写/删入口**。

    调用日志由 :class:`~app.services.ai_log_service.AiLogService` 在真实调用点写入，
    HTTP 写入口 = 「伪造 / 抹掉调用痕迹」的正门。这条在 API 形状层面钉死。
    """
    paths = app.openapi()["paths"]
    log_paths = {p: m for p, m in paths.items() if "/ai/call-logs" in p or "/ai/cost-dashboard" in p}
    assert log_paths, "调用日志相关路径没注册上（本用例前提失效）"
    for path, methods in log_paths.items():
        writes = {m for m in methods if m in {"post", "put", "delete", "patch"}}
        assert not writes, f"{path} 出现了写方法 {writes}（调用日志只可读）"


# ============================================================================
# L1 · 权限矩阵静态钉死（防止「顺手换成 ai:view」把矩阵改坏）
# ============================================================================
def test_prompt_perms_are_not_granted_to_business_roles() -> None:
    """🔴 接口 §4 的矩阵里 API-84~87 只有 **AIOPS**（ADM 短路）。

    HR / MGR / EMP / EXEC / AUDIT 都不得持有 ``ai:prompt:*`` ——
    AUDIT 尤其要注意：它能看调用日志（``ai:log:view``），但**不能改 Prompt**
    （改了 Prompt 就能改变历史日志所对应的行为，审计就失去意义）。
    """
    from scripts.seed_dev import ROLE_GRANTS

    for role_code, prefixes in ROLE_GRANTS.items():
        if role_code in ("ADM", "AIOPS"):
            continue
        for perm in PROMPT_PERMS:
            matched = [p for p in prefixes if p == "*" or perm.startswith(p)]
            assert not matched, (
                f"非 AIOPS 角色 {role_code} 通过前缀 {matched} 拿到了仅 AIOPS 的权限 {perm}"
            )


def test_aiops_covers_all_ai_and_agent_perms() -> None:
    """反向对照：AIOPS 必须覆盖 ``ai:`` 与 ``agent:`` 全部前缀。

    若这条变成假，说明为了收紧某个接口把 AIOPS 一并收窄了 ——
    矩阵里 AIOPS 是 AI 中台唯一的具体角色，它缺口权限等于整个模块不可运维。
    """
    from scripts.seed_dev import ROLE_GRANTS

    assert ROLE_GRANTS["AIOPS"] == ["ai:", "agent:"]
    for perm in PROMPT_PERMS:
        assert any(perm.startswith(p) for p in ROLE_GRANTS["AIOPS"]), perm


def test_prompt_perms_exist_in_permission_tree() -> None:
    """新权限点必须真的被种子建出来 —— 否则 AIOPS 的授权是**空转**（授予不存在的码）。"""
    from scripts.seed_dev import _PERM_TREE

    codes = {code for _menu, _name, children in _PERM_TREE for code, _cn in children}
    for perm in PROMPT_PERMS:
        assert perm in codes, f"权限树缺少 {perm}"


# ============================================================================
# L1 · 种子与契约的一致性（防漂移）
# ============================================================================
def test_seeded_prompts_satisfy_request_contract() -> None:
    """种子写入的每一条 Prompt 都必须能通过**接口层的校验**。

    否则会出现荒诞局面：库里存着一个通过 API 永远无法重建的模板
    （改一次正文再想改回来，编码格式都过不了 40001）。
    """
    import re

    from scripts.seed_dev import _PROMPT_TEMPLATES

    assert len(_PROMPT_TEMPLATES) >= 4, "四个 Prompt 场景（extract/rewrite/ask/judge）各应有一个骨架"
    scenes = set()
    codes = set()
    for code, scene, content in _PROMPT_TEMPLATES:
        assert re.fullmatch(PROMPT_CODE_PATTERN, code), f"种子编码 {code!r} 不合规"
        req = PromptCreateRequest(prompt_code=code, scene=scene, content=content)
        assert req.prompt_code == code, f"种子编码 {code!r} 会被规范化成别的值"
        assert scene in PromptScene.LABELS, f"种子场景 {scene!r} 不在 PromptScene 词表里"
        assert len(req.content) <= MAX_CONTENT_CHARS
        codes.add(code)
        scenes.add(scene)
    assert len(codes) == len(_PROMPT_TEMPLATES), "种子里的 prompt_code 有重复（第二次会撞 40901）"
    assert scenes == set(PromptScene.LABELS), f"Prompt 场景覆盖不全：缺 {set(PromptScene.LABELS) - scenes}"


def test_seeded_prompts_refuse_to_decide_numbers() -> None:
    """🔴 铁律 10 在 Prompt 文本里的落地：打分场景的骨架必须**显式禁止模型给数值结论**。

    这条看着像「文本断言」，但它守的是本项目最强的一条纪律：
    确定性结论只能来自 ThresholdEngine 等确定性组件。
    一旦哪个 Prompt 让模型「顺便给个置信度/合格线」，那个数值就会和
    ThresholdEngine 的判定并存，而两者必然不一致 —— 且没有仲裁机制。
    """
    from scripts.seed_dev import _PROMPT_TEMPLATES

    by_code = {code: content for code, _scene, content in _PROMPT_TEMPLATES}
    judge = by_code["JUDGE_SCORE"]
    assert "不要输出任何分数" in judge
    assert "ThresholdEngine" in judge


# ============================================================================
# L2 · 端到端夹具（独立租户，用例结束整体清理）
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """建一个**独立租户**：4 个 Prompt 权限点 + 3 个角色 + 3 个账号。

    为什么必须用独立租户：本文件要断言「跨租户一律 40401」。
    复用种子租户的话断言会退化成「碰巧查不到」，而不是真的被隔离。

    ⚠️ 缺依赖时**在函数体内 skip** —— 新版 pytest 不允许把 ``skipif`` 加到 fixture 上
    （``Marks cannot be applied to fixtures``），用例级的 ``@requires_db`` 保护不到夹具自身。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("prompt_template"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0004_ai_agent）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu11_{suffix}", tenant_name=f"CU11测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    perms: dict[str, SysPermission] = {}
    for code in PROMPT_PERMS:
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

    adm_role = _mk_role("ADM", list(PROMPT_PERMS))
    #: 只有列表权（对齐矩阵里「能看不能改」的情形）—— 用来证明 API-85/86/87 **不复用 list**
    viewer_role = _mk_role("VIEWER", ["ai:prompt:list"])
    #: 一个 Prompt 权限都没有
    none_role = _mk_role("NONE", [])

    def _mk_user(name: str, role: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid, username=name,
            password_hash=SecurityUtils.hash_password(PWD), real_name=name,
            status=1, login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        return user

    _mk_user(f"adm_{suffix}", adm_role)
    _mk_user(f"viewer_{suffix}", viewer_role)
    _mk_user(f"none_{suffix}", none_role)

    # ---- 一个**别的租户**的模板：用来证明「跨租户 = 40401」是真的被隔离，而不是碰巧查不到
    other = PromptTemplate(
        tenant_id=tid + 999_999, prompt_code="OTHER_TENANT", scene="extract",
        version=1, content="别的租户的模板", status=1, created_by=None,
    )
    db.add(other)
    db.flush()
    other_id = int(other.id)

    db.commit()

    tokens: dict[str, str] = {}
    usernames = {"adm": f"adm_{suffix}", "viewer": f"viewer_{suffix}", "none": f"none_{suffix}"}
    for who, username in usernames.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0, body
        tokens[who] = body["data"]["access_token"]

    yield {"tenant_id": tid, "tokens": tokens, "other_id": other_id}

    # ---- 清理（只动本夹具建的数据）----
    db.rollback()
    db.execute(delete(PromptTemplate).where(PromptTemplate.tenant_id.in_([tid, tid + 999_999])))
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


def _reload(db: Session, prompt_id: int) -> PromptTemplate:
    """重新读一条 Prompt 版本。

    🔴 必须先 ``commit()`` 再 ``expire_all()``：项目引擎是 ``expire_on_commit=False``，
    而 MySQL 默认 REPEATABLE READ（同一事务内重复读返回快照）。只做一半会读到旧值，
    让断言在「实现正确」时也失败（CU-06 实测踩过）。
    """
    db.commit()
    db.expire_all()
    return db.execute(select(PromptTemplate).where(PromptTemplate.id == int(prompt_id))).scalar_one()


def _family(db: Session, tenant_id: int, prompt_code: str) -> list[PromptTemplate]:
    """一个模板族的全部版本（按版本号升序）。"""
    db.commit()
    db.expire_all()
    return list(
        db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.tenant_id == tenant_id,
                PromptTemplate.prompt_code == prompt_code,
            )
            .order_by(PromptTemplate.version)
        ).scalars()
    )


def _audit(db: Session, tenant_id: int, action: str) -> list[AuditLog]:
    """读审计行。

    ⚠️ 先 ``commit()`` 丢掉旧快照 —— 审计走**独立 Session**（另一个连接）写入，
    REPEATABLE READ 下不清快照就**读不到**它。这一步漏掉的表现是
    「审计明明写了、测试却断言不到」。
    """
    db.commit()
    db.expire_all()
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        ).scalars()
    )


def _create(client: TestClient, env: dict, **overrides) -> dict:
    body = {
        "prompt_code": "T_RESUME_EXTRACT",
        "scene": "extract",
        "content": "抽取 {text} 的字段",
        **overrides,
    }
    resp = client.post("/api/v1/ai/prompts", headers=_adm(env), json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["code"] == 0, payload
    return payload["data"]


# ============================================================================
# L2 · API-85 新建
# ============================================================================
def test_create_prompt_starts_at_v1_with_string_id(env: dict, client: TestClient, db: Session) -> None:
    """新建 → ``version`` 固定 1、``status`` 默认 **0 草稿**、ID 是**字符串**（CR-5）。

    ⚠️ 默认草稿而不是启用：新建即启用意味着一次「先建好占位、稍后填正文」的操作
    会**立刻改变线上行为**。默认前进一行才安全。
    """
    created = _create(client, env)
    assert isinstance(created["id"], str), "CR-5：Prompt ID 必须是字符串"
    assert created["version"] == 1
    assert created["status"] == 0 and created["status_name"] == "草稿"
    assert created["prompt_code"] == "T_RESUME_EXTRACT"
    assert created["scene_name"] == "字段抽取"
    assert _reload(db, int(created["id"])).content == "抽取 {text} 的字段"


def test_create_prompt_with_enabled_status(env: dict, client: TestClient) -> None:
    """``status=1`` 直接启用（新族只有这一个版本，天然满足「族内唯一启用」）。"""
    created = _create(client, env, status=1)
    assert created["status"] == 1 and created["status_name"] == "启用"


def test_create_prompt_duplicate_code_is_40901(env: dict, client: TestClient) -> None:
    """🔴 ``prompt_code`` 已存在 → **40901**，且 ``data`` 指出最新版本。

    **刻意不**「为了友好」自动变成「给已有族加一个版本」：调用方以为自己在建新模板、
    实际改了另一个模板的启用版本 —— 属于「界面上什么异常都没有、但线上 Prompt 已经换了」的事故。
    """
    first = _create(client, env)
    resp = client.post(
        "/api/v1/ai/prompts",
        headers=_adm(env),
        json={"prompt_code": "T_RESUME_EXTRACT", "scene": "extract", "content": "另一份正文"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.UNIQUE_CONFLICT)
    assert body["data"]["latest_version"] == 1
    assert body["data"]["next_id"] == first["id"], "应提示去改哪个版本（而不是让人自己找）"


def test_create_prompt_blank_body_is_40001(env: dict, client: TestClient) -> None:
    """``content`` 为空白 → 40001（不是「建一条空模板」）。"""
    resp = client.post(
        "/api/v1/ai/prompts",
        headers=_adm(env),
        json={"prompt_code": "T_BLANK", "scene": "extract", "content": ""},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_create_prompt_unknown_scene_is_40001(env: dict, client: TestClient) -> None:
    """未知 Prompt 场景 → 40001（而不是「建出来但永远检索不到」）。"""
    resp = client.post(
        "/api/v1/ai/prompts",
        headers=_adm(env),
        json={"prompt_code": "T_BAD_SCENE", "scene": "resume_parse", "content": "x"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ============================================================================
# L2 · API-86 修改 = 追加新版本
# ============================================================================
def test_update_creates_new_version_and_keeps_history(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 **本文件最重要的一条**：改版只追加，历史版本正文**一个字符都不变**。

    历史版本正文一旦可被原地改写，所有引用过它的 ``ai_call_log`` 记录都会指向
    一段从未被真正执行过的 Prompt —— 「这条回答当时是用什么提示词生成的」再也答不了。
    故障复现（同一个输入昨天答对今天答错）也就失去了依据。
    """
    v1 = _create(client, env, status=1)
    resp = client.put(
        f"/api/v1/ai/prompts/{v1['id']}",
        headers=_adm(env),
        json={"content": "抽取 {text} 的字段（v2：补充任职经历）", "remark": "补充任职经历"},
    )
    assert resp.status_code == 200, resp.text
    v2 = resp.json()["data"]
    assert v2["version"] == 2
    assert v2["status"] == 1, "缺省继承源版本状态（源是启用 → 新版本也启用）"

    # 历史版本原封不动
    old = _reload(db, int(v1["id"]))
    assert old.content == "抽取 {text} 的字段"
    assert old.version == 1
    # 族内两条（追加，不是覆盖）
    rows = _family(db, env["tenant_id"], "T_RESUME_EXTRACT")
    assert [int(r.version) for r in rows] == [1, 2]
    assert rows[0].content != rows[1].content


def test_update_with_same_content_is_42201(env: dict, client: TestClient) -> None:
    """🔴 「改了但内容一样」→ **42201**（无意义操作，不产生新版本）。

    允许它会让版本号迅速膨胀（试跑时反复提交），而版本号一旦失去信息量，
    追溯就退化成「翻几十个内容相同的版本看哪个是真改过的」。

    反向对照（同一用例内）：改一个字就应当放行。
    """
    v1 = _create(client, env)
    same = client.put(
        f"/api/v1/ai/prompts/{v1['id']}",
        headers=_adm(env),
        json={"content": "抽取 {text} 的字段"},  # 与 v1 完全一致
    )
    assert same.status_code == 422, same.text
    assert same.json()["code"] == int(ErrorCode.ILLEGAL_STATE)
    assert same.json()["data"]["source_version"] == 1

    # 反向对照：内容变了就应放行
    changed = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "抽取 {text} 的字段。"}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["version"] == 2


def test_update_trims_before_comparing_content(env: dict, client: TestClient, db: Session) -> None:
    """比较的是**裁剪后**的正文（``"  正文  "`` 与 ``"正文"`` 视为相同）。

    否则「前后各多打一个空格」就会生成一个毫无意义的版本 ——
    而版本号是给人看的追溯依据，不是噪声计数器。
    """
    v1 = _create(client, env)
    resp = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "  抽取 {text} 的字段  "}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)


def test_update_does_not_touch_status_when_omitted(env: dict, client: TestClient) -> None:
    """``status`` 缺省**继承源版本**。

    若默认落回「草稿」，改完线上行为不变，而调用方以为已经生效 ——
    这是最容易被忽略的一类「成功」。
    """
    v1 = _create(client, env, status=1)
    v2 = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "新正文 A"}
    ).json()["data"]
    assert v2["status"] == 1

    # 显式改成草稿则必须尊重
    v3 = client.put(
        f"/api/v1/ai/prompts/{v2['id']}", headers=_adm(env), json={"content": "新正文 B", "status": 0}
    ).json()["data"]
    assert v3["status"] == 0 and v3["status_name"] == "草稿"


def test_single_enabled_version_invariant_after_update(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 族内**最多一个启用版本**：新版本启用时，同族旧启用版本自动转 2（停用）。

    否则「取当前生效版本」这件事就有歧义 —— ``ORDER BY version DESC LIMIT 1``
    取的是「最新」而不是「启用」，而两者可以不同（草稿版本号更大）。
    """
    v1 = _create(client, env, status=1)
    v2 = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "v2 正文"}
    ).json()["data"]

    rows = _family(db, env["tenant_id"], "T_RESUME_EXTRACT")
    enabled = [int(r.version) for r in rows if int(r.status) == 1]
    assert enabled == [2], f"启用版本应恰好是 v2，实际 {enabled}"

    # 反向对照：存草稿**不该**把线上正在用的版本关掉
    v3 = client.put(
        f"/api/v1/ai/prompts/{v2['id']}", headers=_adm(env), json={"content": "v3 草稿", "status": 0}
    ).json()["data"]
    assert v3["status"] == 0
    rows = _family(db, env["tenant_id"], "T_RESUME_EXTRACT")
    enabled = [int(r.version) for r in rows if int(r.status) == 1]
    assert enabled == [2], "保存一个草稿不应停用正在生效的版本"


def test_update_unknown_prompt_is_40401(env: dict, client: TestClient) -> None:
    """源版本不存在 → **40401**。"""
    resp = client.put(
        f"/api/v1/ai/prompts/{_UNKNOWN_ID}", headers=_adm(env), json={"content": "x"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-87 回滚 = 追加一个「内容是旧版本」的新版本
# ============================================================================
def test_rollback_appends_new_version_with_old_content(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 回滚**不是**「把旧版本重新置为启用」，而是以旧正文追加 ``version = max + 1``。

    原地复活旧版本会破坏「版本号单调递增 ⇒ 版本号即时间线」这条性质，
    而 ``ai_call_log.prompt_version`` 的追溯、前端版本下拉、排序全都依赖它。
    更隐蔽的是：「v1 在 9 月启用过、又在 11 月重新启用」这件事在库里就**看不出来**了。
    """
    v1 = _create(client, env, status=1)
    v2 = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "v2 正文"}
    ).json()["data"]
    assert v2["version"] == 2

    resp = client.post(
        f"/api/v1/ai/prompts/{v2['id']}/rollback",
        headers=_adm(env),
        json={"target_version": 1, "remark": "v2 效果回退，先回到 v1"},
    )
    assert resp.status_code == 200, resp.text
    v3 = resp.json()["data"]
    assert v3["version"] == 3, "回滚产生的是**新版本号**，不是复活 v1"
    assert v3["content"] == "抽取 {text} 的字段", "正文取自目标版本"
    assert v3["status"] == 1, "回滚的语义就是让它重新生效"
    assert int(v3["id"]) != int(v1["id"]), "是追加一条新记录，不是改 v1 的状态"

    rows = _family(db, env["tenant_id"], "T_RESUME_EXTRACT")
    assert [int(r.version) for r in rows] == [1, 2, 3]
    assert [int(r.status) for r in rows] == [2, 2, 1], "v1/v2 停用、v3 启用"


def test_rollback_to_latest_content_is_42201(env: dict, client: TestClient) -> None:
    """目标版本正文与最新版本一致 → **42201**（回滚到「现在」没有意义）。

    反向对照：回滚到一个**内容不同**的历史版本应当放行（上一条用例已覆盖）。
    """
    v1 = _create(client, env, status=1)
    same = client.post(
        f"/api/v1/ai/prompts/{v1['id']}/rollback", headers=_adm(env), json={"target_version": 1}
    )
    assert same.status_code == 422, same.text
    assert same.json()["code"] == int(ErrorCode.ILLEGAL_STATE)
    assert same.json()["data"]["latest_version"] == 1


def test_rollback_unknown_version_is_40401_with_available_versions(
    env: dict, client: TestClient
) -> None:
    """目标版本在该族中不存在 → **40401**，``data.available_versions`` 列出全部可选值。

    这与「版本存在但内容相同」（42201）是两件事：前者要人「换个版本号」，
    后者要人「别白做」。数据形状不同才能让前端分开处理。
    """
    v1 = _create(client, env)
    resp = client.post(
        f"/api/v1/ai/prompts/{v1['id']}/rollback", headers=_adm(env), json={"target_version": 99}
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.NOT_FOUND)
    assert body["data"]["available_versions"] == [1]


def test_rollback_from_any_version_in_family(env: dict, client: TestClient) -> None:
    """回滚的入参 ``{id}`` 只用于**定位模板族** —— 传 v1 或 v2 都得到同样结果。"""
    v1 = _create(client, env, status=1)
    client.put(f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "v2 正文"})

    # 用 v1（旧版本）发起回滚，目标也是 v1 → 内容与最新（v2）不同，应放行
    resp = client.post(
        f"/api/v1/ai/prompts/{v1['id']}/rollback", headers=_adm(env), json={"target_version": 1}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["version"] == 3


def test_rollback_unknown_prompt_is_40401(env: dict, client: TestClient) -> None:
    """发起回滚的版本不存在 → 40401。"""
    resp = client.post(
        f"/api/v1/ai/prompts/{_UNKNOWN_ID}/rollback", headers=_adm(env), json={"target_version": 1}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-84 列表
# ============================================================================
def test_list_prompts_is_one_row_per_version(env: dict, client: TestClient) -> None:
    """🔴 **一个版本一条记录**（不是一个模板一条）—— 前端版本下拉直接用它。

    ⚠️ 响应形状是**分页式**（``data`` 是数组、``page`` 与它同级），
    与 ``/dicts`` 那种「非分页、``data={total,items}``」**不同** ——
    这也是接口 §1.1 的约定：分页接口 ``data`` 只放数组。
    """
    v1 = _create(client, env, status=1)
    client.put(f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "v2 正文"})

    body = client.get("/api/v1/ai/prompts?prompt_code=T_RESUME_EXTRACT", headers=_adm(env)).json()
    assert body["code"] == 0
    assert body["page"]["total"] == 2
    assert isinstance(body["data"], list), "分页接口的 data 必须只放数组（§1.1）"
    versions = sorted(item["version"] for item in body["data"])
    assert versions == [1, 2]
    assert all(isinstance(item["id"], str) for item in body["data"]), "CR-5"


def test_list_prompts_filters_by_scene_and_status(env: dict, client: TestClient) -> None:
    """``scene`` / ``status`` 筛选（并验证不匹配时确实为空 —— 反向对照）。"""
    _create(client, env, status=1)
    _create(client, env, prompt_code="T_JUDGE", scene="judge", content="判定正文")

    by_scene = client.get("/api/v1/ai/prompts?scene=judge", headers=_adm(env)).json()
    assert [i["prompt_code"] for i in by_scene["data"]] == ["T_JUDGE"]

    by_status = client.get("/api/v1/ai/prompts?status=0", headers=_adm(env)).json()
    assert [i["prompt_code"] for i in by_status["data"]] == ["T_JUDGE"], "草稿态只有 T_JUDGE"
    enabled = client.get("/api/v1/ai/prompts?status=1", headers=_adm(env)).json()
    assert [i["prompt_code"] for i in enabled["data"]] == ["T_RESUME_EXTRACT"]

    miss = client.get("/api/v1/ai/prompts?scene=ask", headers=_adm(env)).json()
    assert miss["page"]["total"] == 0


def test_list_prompts_unknown_scene_is_40001(env: dict, client: TestClient) -> None:
    """未知 ``scene`` 参数 → 40001（拼错会「查得到 0 条」，让人误以为「这个场景没模板」）。"""
    resp = client.get("/api/v1/ai/prompts?scene=nope", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ============================================================================
# L2 · 权限与租户隔离
# ============================================================================
def test_non_privileged_role_is_40301_on_writes(env: dict, client: TestClient) -> None:
    """🔴 持 ``ai:prompt:list`` 的角色**能看不能改** → 写接口一律 40301。

    这条是「不复用 ``ai:prompt:list``」这个决策的验收点：
    若把 API-85/86/87 挂成 ``ai:prompt:list``，本用例会失败。
    """
    v1 = _create(client, env)
    viewer = _h(env["tokens"]["viewer"])

    for label, resp in (
        ("create", client.post(
            "/api/v1/ai/prompts", headers=viewer,
            json={"prompt_code": "T_X", "scene": "extract", "content": "x"},
        )),
        ("update", client.put(
            f"/api/v1/ai/prompts/{v1['id']}", headers=viewer, json={"content": "越权"}
        )),
        ("rollback", client.post(
            f"/api/v1/ai/prompts/{v1['id']}/rollback", headers=viewer, json={"target_version": 1}
        )),
    ):
        assert resp.status_code == 403, f"{label}: {resp.text}"
        assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION), label
        assert "content" not in str(resp.json().get("data")), "40301 不得泄露任何资源字段"


def test_viewer_can_still_list(env: dict, client: TestClient) -> None:
    """反向对照：``viewer`` **能**列表（``ai:prompt:list`` 就是为它准备的）。

    若这条变成假，说明为了堵写接口把读权限一并收走了 —— 那会让 AIOPS 之外的
    观察者（如 AUDIT 想看 Prompt 换了没有）连列表都读不到。
    """
    _create(client, env)
    resp = client.get("/api/v1/ai/prompts", headers=_h(env["tokens"]["viewer"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["page"]["total"] == 1


def test_no_perm_role_gets_40301_on_list(env: dict, client: TestClient) -> None:
    """一个 Prompt 权限都没有 → 连列表也 40301（fail-closed）。"""
    resp = client.get("/api/v1/ai/prompts", headers=_h(env["tokens"]["none"]))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


def test_unauthenticated_is_40101(env: dict, client: TestClient) -> None:
    """无 Token → 40101（认证层先于权限层）。"""
    resp = client.get("/api/v1/ai/prompts")
    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.UNAUTHENTICATED)


def test_cross_tenant_prompt_is_40401(env: dict, client: TestClient) -> None:
    """🔴 拿**别的租户**的 v1 去改版 → **40401**（不是 40301）。

    Prompt 表**没有行级数据域**（没有 ``org_id``），故「别的租户的模板」
    在本租户看来就是**本域内不存在** —— 与字典（CU-07）、审计（CU-08）同口径。
    ⚠️ 这里的 ``other_id`` 是**真实存在于库里**的另一租户记录，
    所以这条断言证明的是「真的被 tenant_id 隔离」，而不是「ID 恰好不存在」。
    """
    resp = client.put(
        f"/api/v1/ai/prompts/{env['other_id']}", headers=_adm(env), json={"content": "越权改写"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)

    # 反向对照：本租户的 ID 是能改的（证明 40401 来自租户隔离而不是「PUT 坏了」）
    mine = _create(client, env)
    ok = client.put(
        f"/api/v1/ai/prompts/{mine['id']}", headers=_adm(env), json={"content": "本租户的新正文"}
    )
    assert ok.status_code == 200, ok.text


def test_list_never_returns_other_tenant_rows(env: dict, client: TestClient) -> None:
    """列表同样受租户隔离 —— 别的租户的 ``OTHER_TENANT`` 不得出现。"""
    _create(client, env)
    body = client.get("/api/v1/ai/prompts", headers=_adm(env)).json()
    codes = {i["prompt_code"] for i in body["data"]}
    assert "OTHER_TENANT" not in codes
    assert body["page"]["total"] == 1


# ============================================================================
# L2 · 审计留痕（F-9.2 的可审计性）
# ============================================================================
def test_prompt_changes_are_audited_with_before_after(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 新建 / 改版 / 回滚都必须留痕，且改版带**改前 + 改后**快照。

    只断言「接口返回 200」不能证明留痕存在 —— 这里真的去 ``audit_log`` 里读。
    """
    v1 = _create(client, env, status=1, remark="初版")
    v2 = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "v2 正文", "remark": "补充"}
    ).json()["data"]
    client.post(
        f"/api/v1/ai/prompts/{v2['id']}/rollback",
        headers=_adm(env),
        json={"target_version": 1, "remark": "效果不好回退"},
    )

    creates = _audit(db, env["tenant_id"], "ai_prompt_create")
    assert len(creates) == 1
    created = json.loads(creates[0].after_json)
    assert created["prompt_code"] == "T_RESUME_EXTRACT"
    assert created["version"] == 1 and created["remark"] == "初版"
    assert created["content_chars"] == len("抽取 {text} 的字段")

    updates = _audit(db, env["tenant_id"], "ai_prompt_update")
    assert len(updates) == 1
    before = json.loads(updates[0].before_json)
    after = json.loads(updates[0].after_json)
    assert before["version"] == 1 and after["version"] == 2, "改前/改后必须是两个不同版本"
    assert before["content_excerpt"] != after["content_excerpt"]
    # Prompt 的 ``prompt_template`` 表**没有 remark 列**，说明只能落在审计快照里
    assert after["remark"] == "补充"

    rollbacks = _audit(db, env["tenant_id"], "ai_prompt_rollback")
    assert len(rollbacks) == 1
    rb = json.loads(rollbacks[0].after_json)
    assert rb["version"] == 3
    assert rb["rollback_from_version"] == 1, "审计要能回答「回滚自哪个版本」"
    assert rb["replaced_version"] == 2, "以及「替换掉了哪个版本」"


def test_rejected_update_is_not_audited(env: dict, client: TestClient, db: Session) -> None:
    """被拒的操作**不产生审计**（42201 是「什么都没发生」）。

    审计记的是**已发生的事实**。给拒绝也记一条会把「改过几次」这个统计污染掉 ——
    运维看的是「这个 Prompt 被改了几版」，不是「用户点了几次」。
    这也反过来证明审计调用点确实在业务 ``commit`` 之后（失败路径提前退出）。
    """
    v1 = _create(client, env)
    resp = client.put(
        f"/api/v1/ai/prompts/{v1['id']}", headers=_adm(env), json={"content": "抽取 {text} 的字段"}
    )
    assert resp.status_code == 422
    assert _audit(db, env["tenant_id"], "ai_prompt_update") == []
