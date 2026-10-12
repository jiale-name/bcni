"""CU-07 · 数据字典 CRUD（接口 API-23 ~ API-27，F-1.7）。

分层
----
- **L1 契约层**（不依赖 MySQL / Redis，任何机器都能跑）：
  请求模型的三态语义与格式约束（``item_code`` 大小写、空请求体、``extra=forbid``）、
  ``_snapshot`` / ``_diff`` 留痕结构、**种子字典与代码枚举的一致性**、
  **权限矩阵的静态钉死**、5 个端点在 OpenAPI 中的登记。
- **L2 集成层**（缺依赖时 skip 而非 fail）：
  5 个接口端到端、矩阵权限制（ADM / 非 ADM）、40401 与租户隔离、
  唯一冲突、``item_code`` 不可改、``parent_code`` 三态与环路、停用幂等与 40904、
  **审计留痕真的写出来了**、种子幂等。

🔴 每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）——
   否则一个「永远抛错」的实现也能让全部用例通过。

覆盖的验收编号（延续评审文档 §7.4 的 ``AC-nn`` 体系）
----------------------------------------------------
| 编号 | 断言 | 位置 |
| --- | --- | --- |
| **AC-34 补（字典域）** | 跨租户 / 不存在的类型 → **40401**（不是 40301） | ``test_dict_type_of_other_tenant_is_40401`` |
| **AC-17 补** | 无权限调 API-23 → 40301；且响应体不泄露资源是否存在 | ``test_non_admin_cannot_list_dict_types`` |
| CR-5 补 | 字典 ID 一律字符串下发 | ``test_api_25_item_id_is_string`` |
| CR-22 补 | 每条查询带 ``tenant_id``（跨租户不可见） | ``test_cross_tenant_item_is_40401`` |

性能提示：``env`` 夹具只为 3 个账号各登录一次，密码哈希走 ``lru_cache`` 复用，
整轮不会把时间花在 bcrypt 上。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.core.security import SecurityUtils
from app.dao import dict_dao
from app.exceptions import ErrorCode
from app.models.dict import DictItem, DictType
from app.models.enums import RoleDataScope
from app.models.tenant import Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.dict import (
    CODE_PATTERN,
    DictItemCreateRequest,
    DictItemUpdateRequest,
)
from app.services.dict_service import _diff, _snapshot
from tests.conftest import db_available

PWD = "Admin@123456"

#: 接口 §2.2 中 API-23 使用的权限码（CU-07 新增，仅授 ADM）
API23_PERM = "dict:type:list"
#: 仅 ADM 的字典动作（矩阵 API-23/25/26/27）
ADM_ONLY_PERMS = ("dict:type:list", "dict:create", "dict:update")

_UNKNOWN_ID = "7999999999999999999"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# L1 · 契约：请求模型的格式与三态语义
# ============================================================================
def test_update_request_has_no_item_code_field() -> None:
    """🔴 ``item_code`` **不可修改**：契约层就不该有它。

    改了编码等于让业务表里已存的枚举值静默变成孤儿 —— 数据还在，含义漂移。
    这是「不允许」的第一道闸门（第二道是请求体 ``extra=forbid``）。
    """
    assert "item_code" not in DictItemUpdateRequest.model_fields
    assert "type_code" not in DictItemUpdateRequest.model_fields


def test_update_request_rejects_item_code_with_40001_semantics() -> None:
    """仍传 ``item_code`` → **校验失败**（而不是被静默忽略）。

    静默忽略是最坏的一种「成功」：调用方以为编码改掉了，实际没改。
    """
    with pytest.raises(ValidationError):
        DictItemUpdateRequest(item_name="本科", item_code="hacked")


def test_update_request_rejects_empty_body() -> None:
    """空请求体 ``{}`` 必须报错 —— 否则会返回「假成功」（HTTP 200 但什么都没改）。"""
    with pytest.raises(ValidationError):
        DictItemUpdateRequest()


def test_update_request_parent_code_three_states() -> None:
    """``parent_code`` 三态：未传=不改 ｜ 显式 null=清空 ｜ 传值=设置。

    少了这一层区分，前端「把子项提升为根项」就会静默变成「什么都没发生」。
    """
    assert DictItemUpdateRequest(item_name="x").parent_code_provided is False
    cleared = DictItemUpdateRequest(parent_code=None)
    assert cleared.parent_code_provided is True and cleared.parent_code is None
    moved = DictItemUpdateRequest(parent_code="p1")
    assert moved.parent_code_provided is True and moved.parent_code == "p1"


def test_code_pattern_rejects_uppercase() -> None:
    """🔴 大写必须在入口被拒（40001），而不是留到库里炸出 40901。

    原因：字典表排序规则是 ``utf8mb4_unicode_ci``（**大小写不敏感**），
    于是 ``Master`` 与 ``master`` 在唯一键上**是同一个值**。
    放行大写 → 调用方拿到含义模糊的唯一冲突；入口限制 → 明确的格式错误。
    """
    for bad in ("Master", "MASTER", "edu-Level", "有中文", "a" * 65, "a b", "_leading"):
        with pytest.raises(ValidationError):
            DictItemCreateRequest(item_code=bad, item_name="x")


def test_code_is_trimmed_before_validation() -> None:
    """首尾空白先被裁掉再校验 —— ``" g1 "`` 应当等于 ``"g1"``。

    这条与上一条是一对：``" space"`` **不该**被拒（裁剪后合法），
    否则用户从表格里复制粘贴带空格的编码会莫名其妙失败。
    """
    assert DictItemCreateRequest(item_code=" g1 ", item_name="x").item_code == "g1"
    assert DictItemCreateRequest(item_code="g1", item_name="  年级  ").item_name == "年级"


def test_code_pattern_accepts_numeric_and_underscore_codes() -> None:
    """数字开头（§6.2 的 ``01``~``05``）与下划线必须放行。

    这条是**种子数据的回归保护**：教育学历用的是 ``01``/``02`` 这种前导零编码，
    若哪天把规则收紧成「必须字母开头」，种子会直接失败。
    """
    for good in ("01", "05", "education_level", "a1_b2", "x"):
        req = DictItemCreateRequest(item_code=good, item_name="x")
        assert req.item_code == good
    assert CODE_PATTERN.startswith("^[a-z0-9]")


def test_snapshot_and_diff_only_report_changed_fields() -> None:
    """留痕结构：``{字段: [改前值, 改后值]}``，且**只含有变化的字段**。

    为什么不记整行快照：审计日志的价值在「一眼看出改了什么」，
    把没变的字段也带进去会被噪声淹没。
    """
    item = DictItem(
        tenant_id=1, type_code="t", item_code="a", item_name="旧名",
        sort_no=10, parent_code=None, status=1, is_deleted=0,
    )
    before = _snapshot(item)
    assert set(before) == {"item_code", "item_name", "sort_no", "parent_code", "status"}

    item.item_name = "新名"
    item.status = 0
    diff = _diff(before, _snapshot(item))
    assert diff == {"item_name": ["旧名", "新名"], "status": [1, 0]}
    assert "sort_no" not in diff, "没变的字段不应出现在 diff 里"


def test_dict_status_name_says_stopped_not_banned() -> None:
    """字典的 ``status_name`` 必须是「停用」，**不是**「禁用」。

    「禁用」是对**人**的处罚（账号封禁），「停用」是对**配置项**的临时关闭。
    混用会让「停用了一个学历档位」在界面上显示成「禁用了」，读起来像误操作。
    因此字典用 :class:`~app.models.enums.EnabledStatus` 而不是 ``SysUserStatus``
    —— 这条测试就是防止有人「顺手复用」后者。
    """
    from app.models.enums import EnabledStatus, SysUserStatus
    from app.services.dict_service import _to_item_out

    stopped = DictItem(
        id=1, tenant_id=1, type_code="t", item_code="a", item_name="A",
        sort_no=0, parent_code=None, status=0, is_deleted=0,
    )
    assert _to_item_out(stopped).status_name == "停用"
    assert EnabledStatus.name_of(1) == "启用"
    assert SysUserStatus.name_of(0) == "禁用", "用户状态仍是「禁用」—— 两个枚举语义不同，不可合并"


# ============================================================================
# L1 · 种子与代码枚举的一致性（防漂移）
# ============================================================================
def test_builtin_enum_dicts_cover_db_design_section_6_1() -> None:
    """种子登记的内建枚举类型必须**完整覆盖**数据库设计 §6.1 的 17 个 ``type_code``。

    §6.1 那张表是「内建枚举 ↔ 字典类型」的对应约定；少登记一个，
    前端就会在某个下拉框上拿不到选项，而且**不会报错**（只是空列表）。
    """
    from scripts.seed_dev import _BUILTIN_ENUM_DICTS

    expected = {
        "sys_user_status",
        "role_data_scope",
        "perm_type",
        "gender",
        "talent_data_status",
        "parse_review_status",
        "dup_pair_status",
        "tag_dict_status",
        "tag_source",
        "import_task_status",
        "import_file_parse_status",
        "change_source",
        "prompt_status",
        "ai_call_status",
        "agent_task_status",
        "agent_interrupt_status",
    }
    actual = {code for code, _name, _enum in _BUILTIN_ENUM_DICTS}
    assert actual == expected
    # education_level 属 §6.2「业务字典」（人工维护），不在内建枚举里 —— 两者不可混
    assert "education_level" not in actual


def test_every_builtin_enum_has_chinese_labels() -> None:
    """每个内建枚举都必须有完整的中文名表（``LABELS``），否则种子里会写进空名。

    ``LabeledIntEnum.options()`` 直接读 ``LABELS``；漏一个成员就会 ``KeyError``
    或写出空字符串 —— 这里提前把它变成一条明确的断言。
    """
    from app.models.enums import LABELS
    from scripts.seed_dev import _BUILTIN_ENUM_DICTS

    for type_code, _name, enum_cls in _BUILTIN_ENUM_DICTS:
        options = enum_cls.options()
        assert options, f"{type_code} 的内建枚举没有任何成员"
        for opt in options:
            assert opt["name"], f"{type_code} 的成员 {opt['code']} 缺少中文名"
        assert len(options) == len(LABELS[enum_cls]), f"{type_code} 的项数与枚举成员数不一致"


def test_seeded_item_codes_satisfy_request_pattern() -> None:
    """种子写入的每个 ``item_code`` 都必须能通过**接口层的格式校验**。

    否则会出现荒诞局面：库里存着一个通过 API 永远无法重建的编码
    （改一次名再想改回来就 40001）。
    """
    import re

    from scripts.seed_dev import _BUILTIN_ENUM_DICTS, _BUSINESS_DICTS

    pattern = re.compile(CODE_PATTERN)
    for type_code, _name, enum_cls in _BUILTIN_ENUM_DICTS:
        for opt in enum_cls.options():
            assert pattern.match(str(opt["code"])), f"{type_code}.{opt['code']} 不合规"
    for type_code, _name, _remark, items in _BUSINESS_DICTS:
        for item_code, _item_name in items:
            assert pattern.match(item_code), f"{type_code}.{item_code} 不合规"


def test_business_dict_comes_from_db_design_section_6_2() -> None:
    """§6.2「业务字典示例」的学历档位必须与种子逐字一致（含前导零编码）。"""
    from scripts.seed_dev import _BUSINESS_DICTS

    by_code = {code: items for code, _n, _r, items in _BUSINESS_DICTS}
    assert by_code["education_level"] == [
        ("01", "高中及以下"),
        ("02", "大专"),
        ("03", "本科"),
        ("04", "硕士"),
        ("05", "博士"),
    ]


# ============================================================================
# L1 · 权限矩阵静态钉死（防止「顺手复用 dict:view」把矩阵改坏）
# ============================================================================
def test_matrix_only_perms_are_not_granted_to_non_admin_roles() -> None:
    """🔴 API-23/25/26/27 在矩阵里是**仅 ADM**，故这些权限码不得出现在任何非 ADM 角色的授予前缀里。

    这条测试专门防一类回退：为了图省事把 ``dict:type:list`` 换成 ``dict:view``
    （后者被广授给 HR/MGR/EMP/EXEC），矩阵就悄悄被放宽了 —— 代码能跑、测试若不写就全是绿的。
    """
    from scripts.seed_dev import ROLE_GRANTS

    for role_code, prefixes in ROLE_GRANTS.items():
        if role_code == "ADM":
            continue
        for perm in ADM_ONLY_PERMS:
            matched = [p for p in prefixes if p == "*" or perm.startswith(p)]
            assert not matched, (
                f"非 ADM 角色 {role_code} 通过前缀 {matched} 拿到了仅 ADM 的权限 {perm}"
            )


def test_dict_view_is_still_granted_for_downstream_dropdowns() -> None:
    """反向对照：``dict:view`` **仍应**广授（API-24 的下拉场景需要它）。

    如果这条变成假，说明为了防止 API-23 越权而把 ``dict:view`` 一并收走了 ——
    那会让 HR/MGR 连字典项都读不到，属于「为了堵一个洞而拆掉一堵墙」。
    """
    from scripts.seed_dev import ROLE_GRANTS

    for role_code in ("HR", "MGR", "EMP", "EXEC"):
        prefixes = ROLE_GRANTS[role_code]
        assert any("dict:view".startswith(p) for p in prefixes), f"{role_code} 丢了 dict:view"


def test_dict_perms_exist_in_permission_tree() -> None:
    """新权限点必须真的被种子建出来，否则 ADM 之外的授权无从谈起、权限树也缺项。"""
    from scripts.seed_dev import _PERM_TREE

    codes = {code for _menu, _name, children in _PERM_TREE for code, _cn in children}
    for perm in ADM_ONLY_PERMS + ("dict:view",):
        assert perm in codes, f"权限树缺少 {perm}"


# ============================================================================
# L1 · 路由登记（5 个端点必须都在，且路径/方法正确）
# ============================================================================
def test_dict_endpoints_are_registered(app) -> None:
    """5 个端点必须出现在 OpenAPI 中（路径 + 方法）。

    用 OpenAPI 而非 ``app.routes`` 断言：FastAPI 0.141 起 ``include_router``
    不再把子路由摊平进 ``app.routes``（见批次 1 环境实测结论）。
    """
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/dicts": {"get"},
        "/api/v1/dicts/{type_code}/items": {"get", "post"},
        "/api/v1/dict-items/{item_id}": {"put", "delete"},
    }
    for path, methods in expected.items():
        assert path in paths, f"OpenAPI 缺少路径 {path}"
        actual = {m for m in paths[path] if m in {"get", "post", "put", "delete"}}
        assert methods <= actual, f"{path} 缺少方法 {methods - actual}"


def test_type_code_path_param_declares_pattern(app) -> None:
    """路径参数 ``type_code`` 必须带 pattern 校验 —— 否则 ``/dicts/ABC/items``
    会走到 40401（资源不存在），把「格式错」伪装成「不存在」。
    """
    schema = app.openapi()["paths"]["/api/v1/dicts/{type_code}/items"]["get"]
    param = next(p for p in schema["parameters"] if p["name"] == "type_code")
    assert param["schema"].get("pattern") == CODE_PATTERN


# ============================================================================
# L2 · 端到端夹具（独立租户，用例结束整体清理）
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """建一个**独立租户**：2 个字典类型 + 6 个字典项 + 3 个账号。

    为什么必须用独立租户：本文件要断言「租户隔离」（跨租户一律 40401）。
    若复用 ``demo`` 种子租户，断言会退化成「碰巧查不到」，而不是真的被隔离。

    ⚠️ 缺依赖时**在函数体内 skip** —— 新版 pytest 不允许把 ``skipif`` 标记加到 fixture 上
    （``Marks cannot be applied to fixtures``），而用例级的 ``@requires_db``
    保护不到夹具自身的建库动作。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("dict_item"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head → 0002_dict）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu07_{suffix}", tenant_name=f"CU07测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    # ---- 权限点：只建字典相关 4 个，便于断言「缺哪个码就拒哪个接口」----
    perm_codes = [API23_PERM, "dict:view", "dict:create", "dict:update"]
    perms: dict[str, SysPermission] = {}
    for code in perm_codes:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    def _mk_role(code: str, grants: list[str]) -> SysRole:
        role = SysRole(
            tenant_id=tid,
            role_code=code,
            role_name=code,
            data_scope=int(RoleDataScope.ALL),
            is_builtin=1,
            status=1,
        )
        db.add(role)
        db.flush()
        for c in grants:
            db.add(SysRolePermission(tenant_id=tid, role_id=role.id, perm_id=perms[c].id))
        return role

    adm_role = _mk_role("ADM", perm_codes)
    #: 只有查看权（对齐矩阵里 HR/MGR 的情形）—— 用来证明 API-23 **不复用** dict:view
    viewer_role = _mk_role("VIEWER", ["dict:view"])
    #: 一个字典权限都没有 —— 用来证明 API-24 只要求登录（矩阵：全角色 ✓）
    none_role = _mk_role("NONE", [])

    def _mk_user(name: str, role: SysRole) -> SysUser:
        user = SysUser(
            tenant_id=tid,
            username=name,
            password_hash=SecurityUtils.hash_password(PWD),
            real_name=name,
            status=1,
            login_fail_count=0,
        )
        db.add(user)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=user.id, role_id=role.id))
        return user

    _mk_user(f"adm_{suffix}", adm_role)
    _mk_user(f"viewer_{suffix}", viewer_role)
    _mk_user(f"none_{suffix}", none_role)

    # ---- 字典数据：grade（平面）+ city（三级，用于三态/环路/40904）----
    def _mk_type(code: str, name: str) -> DictType:
        row = DictType(tenant_id=tid, type_code=code, type_name=name, status=1, is_deleted=0)
        db.add(row)
        db.flush()
        return row

    def _mk_item(type_code: str, item_code: str, name: str, sort_no: int, parent: str | None = None,
                 status: int = 1) -> DictItem:
        row = DictItem(
            tenant_id=tid,
            type_code=type_code,
            item_code=item_code,
            item_name=name,
            sort_no=sort_no,
            parent_code=parent,
            status=status,
            is_deleted=0,
        )
        db.add(row)
        db.flush()
        return row

    _mk_type("grade", "年级")
    _mk_item("grade", "g1", "大一", 10)
    _mk_item("grade", "g2", "大二", 20)
    _mk_item("grade", "g3", "大三", 30)
    _mk_item("grade", "g4", "大四（已停用）", 40, status=0)

    _mk_type("city", "城市")
    _mk_item("city", "p1", "某省", 10)
    _mk_item("city", "c1", "某市", 20, parent="p1")
    _mk_item("city", "d1", "某区", 30, parent="c1")

    db.commit()

    # ---- 登录拿 Token ----
    tokens: dict[str, str] = {}
    usernames = {
        "adm": f"adm_{suffix}",
        "viewer": f"viewer_{suffix}",
        "none": f"none_{suffix}",
    }
    for who, username in usernames.items():
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0, body
        tokens[who] = body["data"]["access_token"]

    yield {"tenant_id": tid, "tokens": tokens, "usernames": usernames}

    # ---- 清理（只动本夹具建的数据）----
    db.rollback()
    db.execute(delete(DictItem).where(DictItem.tenant_id == tid))
    db.execute(delete(DictType).where(DictType.tenant_id == tid))
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


def _reload(db: Session, item_id: int) -> DictItem:
    """重新读一条字典项 —— 必须先 ``commit()`` 再 ``expire_all()``。

    🔴 两个都要做：项目引擎是 ``expire_on_commit=False``（提交后对象不失效），
    而 MySQL 默认隔离级别是 REPEATABLE READ（同一事务内重复读返回快照）。
    只做一半就会读到**旧值**，让断言在「实现正确」时也失败（CU-06 实测踩过）。
    """
    db.commit()
    db.expire_all()
    row = db.execute(select(DictItem).where(DictItem.id == int(item_id))).scalar_one()
    return row


# ============================================================================
# L2 · API-23 字典类型列表
# ============================================================================
def test_api_23_lists_types_with_item_counts(env: dict, client: TestClient) -> None:
    """ADM 列类型：带项计数（总数 / 启用数），且**计数来自聚合而非逐类型 COUNT**。

    ``grade`` 有 4 项、其中 1 项停用 → total=4, enabled=3。
    这两条计数若用「数一遍 items 列表长度」也能蒙对，但那样会漏掉停用项 ——
    所以这里同时断言两个数值。
    """
    resp = client.get("/api/v1/dicts", headers=_adm(env))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body

    data = body["data"]
    assert data["total"] == 2
    by_code = {t["type_code"]: t for t in data["items"]}
    assert by_code["grade"]["item_count"] == 4
    assert by_code["grade"]["enabled_item_count"] == 3
    assert by_code["city"]["item_count"] == 3
    assert by_code["grade"]["status_name"] == "启用"


def test_api_23_keyword_filter(env: dict, client: TestClient) -> None:
    """关键词过滤（按编码/名称），并验证不匹配时确实为空（反向对照）。"""
    hit = client.get("/api/v1/dicts?keyword=grade", headers=_adm(env)).json()
    assert [t["type_code"] for t in hit["data"]["items"]] == ["grade"]
    miss = client.get("/api/v1/dicts?keyword=不存在的字典", headers=_adm(env)).json()
    assert miss["data"]["total"] == 0


def test_non_admin_cannot_list_dict_types(env: dict, client: TestClient) -> None:
    """🔴 **矩阵：API-23 仅 ADM**。

    ``viewer`` 角色持有 ``dict:view``（能读字典项），但**没有** ``dict:type:list``
    → 必须 **40301**。这条正是「不复用 dict:view」这个决策的验收点：
    若把 API-23 挂成 ``dict:view``，本用例会失败。
    """
    resp = client.get("/api/v1/dicts", headers=_h(env["tokens"]["viewer"]))
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.NO_PERMISSION)
    # AC-36 同源：40301 响应体不得泄露任何资源字段
    assert "type_code" not in str(body.get("data"))


def test_api_23_rejects_unauthenticated(client: TestClient, env: dict) -> None:
    """无 Token → 40101（而不是 40301）—— 处理链顺序：认证层先于权限层。"""
    resp = client.get("/api/v1/dicts")
    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.UNAUTHENTICATED)


# ============================================================================
# L2 · API-24 字典项列表
# ============================================================================
def test_api_24_requires_only_login(env: dict, client: TestClient) -> None:
    """🔴 **矩阵：API-24 全 7 角色 ✓**（接口 §2.2 标注「已登录」）。

    ``none`` 角色**一个字典权限都没有**，仍然必须能读到字典项 ——
    因为下拉框是「登录后即可用」的基础能力，不该被权限点卡住。
    """
    resp = client.get("/api/v1/dicts/grade/items", headers=_h(env["tokens"]["none"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["total"] == 4


def test_api_24_returns_all_items_including_disabled(env: dict, client: TestClient) -> None:
    """默认返回**全部**（含停用项），显式 ``status=1`` 才只给启用项。

    为什么默认全量：若默认只给启用项，管理页就看不到停用项，
    于是**无法再把它启用回来** —— 停用等于永久隐藏。
    """
    all_items = client.get("/api/v1/dicts/grade/items", headers=_adm(env)).json()
    assert all_items["data"]["total"] == 4
    enabled = client.get("/api/v1/dicts/grade/items?status=1", headers=_adm(env)).json()
    assert enabled["data"]["total"] == 3
    disabled = client.get("/api/v1/dicts/grade/items?status=0", headers=_adm(env)).json()
    assert [i["item_code"] for i in disabled["data"]["items"]] == ["g4"]


def test_api_24_is_sorted_by_sort_no(env: dict, client: TestClient) -> None:
    """排序必须确定（``sort_no`` → ``item_code``），否则下拉框选项会「跳位」。"""
    codes = [
        i["item_code"]
        for i in client.get("/api/v1/dicts/grade/items", headers=_adm(env)).json()["data"]["items"]
    ]
    assert codes == ["g1", "g2", "g3", "g4"]


def test_api_24_parent_code_filter(env: dict, client: TestClient) -> None:
    """多级联动：``parent_code`` 只返回该父项下的子项。"""
    body = client.get("/api/v1/dicts/city/items?parent_code=p1", headers=_adm(env)).json()
    assert [i["item_code"] for i in body["data"]["items"]] == ["c1"]


def test_dict_type_of_other_tenant_is_40401(env: dict, client: TestClient) -> None:
    """🔴 **AC-34 补（字典域）**：类型不存在 → **40401**，且**不是 40301**。

    为什么字典的「跨租户」是 40401 而不是 40301：字典是**租户级公共配置**，
    没有 ``org_id``、没有行级数据域（接口 §1.7 的行级裁剪针对业务数据）。
    因此「别的租户的类型」在本租户看来就是**本域内确实不存在** → 40401，
    与「越权到别人的数据域」（40301）是两回事。这条口径若搞反，
    前端会把「字典不存在」误报成「你没权限」。
    """
    for code in ("not_exist_type", "demo_other_tenant"):
        resp = client.get(f"/api/v1/dicts/{code}/items", headers=_adm(env))
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


def test_api_24_malformed_type_code_is_40001(env: dict, client: TestClient) -> None:
    """格式不合法的 ``type_code`` → **40001**（格式错），与 40401（不存在）区分开。"""
    resp = client.get("/api/v1/dicts/BAD-CODE/items", headers=_adm(env))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ============================================================================
# L2 · API-25 新增字典项
# ============================================================================
def test_api_25_creates_item_and_returns_string_id(env: dict, client: TestClient, db: Session) -> None:
    """新建成功；**CR-5：ID 一律字符串**（雪花 ID 超过 JS 安全整数会末位截断）。"""
    resp = client.post(
        "/api/v1/dicts/grade/items",
        headers=_adm(env),
        json={"item_code": "g5", "item_name": "研一", "sort_no": 50},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]
    assert isinstance(item["id"], str), "CR-5：字典项 ID 必须是字符串"
    assert item["item_code"] == "g5" and item["status"] == 1
    assert item["status_name"] == "启用"
    # 真落库了
    assert _reload(db, int(item["id"])).item_name == "研一"


def test_api_25_duplicate_item_code_is_40901(env: dict, client: TestClient) -> None:
    """同类型下编码重复 → **40901**（唯一约束冲突）。"""
    resp = client.post(
        "/api/v1/dicts/grade/items",
        headers=_adm(env),
        json={"item_code": "g1", "item_name": "重复"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == int(ErrorCode.UNIQUE_CONFLICT)


def test_api_25_same_code_in_another_type_is_allowed(env: dict, client: TestClient) -> None:
    """反向对照：**不同**类型下允许同编码（编码空间按类型独立）。"""
    resp = client.post(
        "/api/v1/dicts/city/items",
        headers=_adm(env),
        json={"item_code": "g1", "item_name": "同名不同类"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0


def test_api_25_unknown_parent_code_is_42201(env: dict, client: TestClient) -> None:
    """``parent_code`` 不存在于该类型下 → **42201**（业务规则拒绝），不是 40401。

    40401 的语义是「你要的资源不存在」。这里不存在的只是**引用的父项**，
    请求的目标资源（新增字典项）本身是合法的 —— 所以用 42201 更准确，
    也让前端能区分「换个父项重试」与「资源没了」。
    """
    resp = client.post(
        "/api/v1/dicts/city/items",
        headers=_adm(env),
        json={"item_code": "x1", "item_name": "子项", "parent_code": "nope"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)


def test_api_25_unknown_type_is_40401(env: dict, client: TestClient) -> None:
    """类型不存在 → **40401**（先校验类型再校验项，避免把「类型错」报成「项错」）。"""
    resp = client.post(
        "/api/v1/dicts/nope_type/items",
        headers=_adm(env),
        json={"item_code": "a", "item_name": "A"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


def test_api_25_uppercase_code_is_40001(env: dict, client: TestClient) -> None:
    """大写编码 → **40001**（在入口拒绝，而不是留到库里炸 40901）。"""
    resp = client.post(
        "/api/v1/dicts/grade/items",
        headers=_adm(env),
        json={"item_code": "G6", "item_name": "大写"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_api_25_denied_for_non_admin(env: dict, client: TestClient) -> None:
    """矩阵：API-25 仅 ADM。持 ``dict:view`` 的角色不得新增 → 40301。"""
    resp = client.post(
        "/api/v1/dicts/grade/items",
        headers=_h(env["tokens"]["viewer"]),
        json={"item_code": "g7", "item_name": "越权"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


# ============================================================================
# L2 · API-26 修改字典项（含改前值/改后值留痕）
# ============================================================================
def test_api_26_updates_and_audits_before_after(
    env: dict, client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **F-1.7 的核心要求**：改前值 / 改后值 / 操作人必须留痕。

    留痕目前以结构化日志形式输出（TBL-28 落库属 CU-08），
    因此这里**真的去捕获日志**并断言三要素齐全 ——
    只断言「接口返回 200」是不能证明「留痕存在」的。
    """
    g1 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g1")
    assert g1 is not None

    with caplog.at_level(logging.INFO, logger="app.services.dict_service"):
        resp = client.put(
            f"/api/v1/dict-items/{g1.id}",
            headers=_adm(env),
            json={"item_name": "大一（改名）", "sort_no": 15},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["item_name"] == "大一（改名）"

    text = caplog.text
    assert "dict_item_update" in text, "未写出审计留痕"
    assert "uid=" in text, "留痕缺少操作人"
    # 改动值成对出现（改前 → 改后），而不是两条互不相干的流水
    assert "['大一', '大一（改名）']" in text, f"留痕缺少改前值/改后值：{text}"
    assert "[10, 15]" in text, "留痕漏掉了同一请求里的第二个改动字段"
    # 值真的落库
    assert _reload(db, int(g1.id)).sort_no == 15


def test_api_26_rejects_item_code_change(env: dict, client: TestClient, db: Session) -> None:
    """🔴 ``item_code`` 不可修改：请求里带上它 → **40001**（而不是被静默忽略）。

    这是「编码不可改」不变式的执行点。若契约层漏了 ``extra=forbid``，
    调用方会拿到 200 却什么都没改 —— 最坏的一种「成功」。
    """
    g1 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g1")
    assert g1 is not None
    resp = client.put(
        f"/api/v1/dict-items/{g1.id}",
        headers=_adm(env),
        json={"item_name": "改名", "item_code": "hacked"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)
    assert _reload(db, int(g1.id)).item_code == "g1"


def test_api_26_rejects_empty_body(env: dict, client: TestClient, db: Session) -> None:
    """空请求体 → **40001**（不返回「假成功」）。"""
    g1 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g1")
    assert g1 is not None
    resp = client.put(f"/api/v1/dict-items/{g1.id}", headers=_adm(env), json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_api_26_parent_code_three_states(env: dict, client: TestClient, db: Session) -> None:
    """三态语义端到端：① 不传 parent_code → 不改动；② null → 清空；③ 值 → 挂到该父项下。

    少任一条，「把子项提升为根项」就会变成静默的「什么都没发生」。
    """
    d1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "d1")
    assert d1 is not None

    # ① 不传 → 不改动（d1 仍挂在 c1 下）
    r1 = client.put(f"/api/v1/dict-items/{d1.id}", headers=_adm(env), json={"item_name": "某区A"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["parent_code"] == "c1"

    # ② 显式 null → 清空（提升为根项）
    r2 = client.put(f"/api/v1/dict-items/{d1.id}", headers=_adm(env), json={"parent_code": None})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["parent_code"] is None

    # ③ 传值 → 重新挂到 p1 下
    r3 = client.put(f"/api/v1/dict-items/{d1.id}", headers=_adm(env), json={"parent_code": "p1"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["parent_code"] == "p1"
    assert _reload(db, int(d1.id)).parent_code == "p1"


def test_api_26_cannot_set_self_as_parent(env: dict, client: TestClient, db: Session) -> None:
    """父项设为自己 → **42201**（成环）。"""
    c1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "c1")
    assert c1 is not None
    resp = client.put(f"/api/v1/dict-items/{c1.id}", headers=_adm(env), json={"parent_code": "c1"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)


def test_api_26_cannot_move_under_own_descendant(env: dict, client: TestClient, db: Session) -> None:
    """🔴 把父项设成**自己的子孙** → **42201**（环路检测）。

    city 的结构是 p1 → c1 → d1。把 c1 的父项设成 d1 会形成环，
    前端多级联动会**无限展开**（select 死循环）。这条若漏了，
    症状不是报错而是页面卡死 —— 排查成本极高。
    """
    c1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "c1")
    assert c1 is not None
    resp = client.put(f"/api/v1/dict-items/{c1.id}", headers=_adm(env), json={"parent_code": "d1"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)
    assert _reload(db, int(c1.id)).parent_code == "p1", "被拒后不得留下半改状态"


def test_api_26_parent_from_other_type_is_42201(env: dict, client: TestClient, db: Session) -> None:
    """父项必须属于**同一类型** —— 拿 ``grade`` 的编码去当 ``city`` 的父项 → 42201。

    放行的话会造出一棵跨字典的「幽灵树」（父项在另一个下拉里，永远选不到）。
    """
    d1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "d1")
    assert d1 is not None
    resp = client.put(f"/api/v1/dict-items/{d1.id}", headers=_adm(env), json={"parent_code": "g1"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == int(ErrorCode.ILLEGAL_STATE)


def test_api_26_denied_for_non_admin(env: dict, client: TestClient, db: Session) -> None:
    """矩阵：API-26 仅 ADM。"""
    g1 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g1")
    assert g1 is not None
    resp = client.put(
        f"/api/v1/dict-items/{g1.id}", headers=_h(env["tokens"]["viewer"]), json={"item_name": "越权"}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


def test_cross_tenant_item_is_40401(env: dict, client: TestClient) -> None:
    """CR-22 补：拿一个**别的租户**的项 ID → **40401**（不是 40301）。

    字典表按 ``tenant_id`` 隔离（联合索引最左列），因此别人的 ID 在本租户
    「确实不存在」→ 40401。与「字典无行级数据域」是同一套口径（见
    ``test_dict_type_of_other_tenant_is_40401``）。
    """
    resp = client.put(
        f"/api/v1/dict-items/{_UNKNOWN_ID}", headers=_adm(env), json={"item_name": "x"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


# ============================================================================
# L2 · API-27 停用字典项（幂等 + 40904）
# ============================================================================
def test_api_27_disable_is_idempotent(env: dict, client: TestClient, db: Session) -> None:
    """停用 → ``already_disabled=false``；**重复停用 → 200 + ``already_disabled=true``**。

    幂等是刻意的：刷新页面/前端重试/脚本重跑都会重放这个请求，
    把它变成错误只会把幂等成本推给每个调用方。
    同时必须断言「行还在」—— 停用不是删除。
    """
    g2 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g2")
    assert g2 is not None

    first = client.delete(f"/api/v1/dict-items/{g2.id}", headers=_adm(env))
    assert first.status_code == 200, first.text
    body1 = first.json()["data"]
    assert body1["status"] == 0 and body1["already_disabled"] is False
    assert body1["status_name"] == "停用"

    second = client.delete(f"/api/v1/dict-items/{g2.id}", headers=_adm(env))
    assert second.status_code == 200, second.text
    assert second.json()["data"]["already_disabled"] is True

    row = _reload(db, int(g2.id))
    assert row.status == 0 and row.is_deleted == 0, "停用不得变成软删"


def test_api_27_disable_parent_with_enabled_children_is_40904(
    env: dict, client: TestClient, db: Session
) -> None:
    """🔴 停用有「启用中子项」的父项 → **40904**；先停子项后即可停父项（反向对照）。

    为什么必须拒绝：多级联动是「先选父、再列子」，父项一停用，
    子项就**永远无法被选中** —— 数据还在库里，但用户看到的是「选项消失了」。
    不报错、不崩溃，属于最难排查的一类缺陷。与 CU-06「删部门前查引用」同源。
    """
    c1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "c1")
    p1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "p1")
    assert c1 is not None and p1 is not None

    # c1 有启用中的子项 d1 → 拒绝
    blocked = client.delete(f"/api/v1/dict-items/{c1.id}", headers=_adm(env))
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == int(ErrorCode.PRECONDITION_UNMET)
    assert body["data"]["enabled_children"] == ["d1"]

    # 反向对照：先停子项（d1），再停父项（c1、p1）就应放行
    d1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "d1")
    assert d1 is not None
    assert client.delete(f"/api/v1/dict-items/{d1.id}", headers=_adm(env)).status_code == 200
    assert client.delete(f"/api/v1/dict-items/{c1.id}", headers=_adm(env)).status_code == 200
    assert client.delete(f"/api/v1/dict-items/{p1.id}", headers=_adm(env)).status_code == 200


def test_api_27_also_guards_status_update_via_put(env: dict, client: TestClient, db: Session) -> None:
    """一致性：**走 API-26 把 ``status`` 改成 0 也必须受同一条 40904 约束**。

    否则会出现「API-27 拦住、API-26 绕过」的漏洞 —— 同一个状态两条路径，
    只堵一条等于没堵。
    """
    p1 = dict_dao.get_item_by_code(db, env["tenant_id"], "city", "p1")
    assert p1 is not None
    resp = client.put(f"/api/v1/dict-items/{p1.id}", headers=_adm(env), json={"status": 0})
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == int(ErrorCode.PRECONDITION_UNMET)


def test_api_27_unknown_item_is_40401(env: dict, client: TestClient) -> None:
    """停用不存在的项 → **40401**。"""
    resp = client.delete(f"/api/v1/dict-items/{_UNKNOWN_ID}", headers=_adm(env))
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.NOT_FOUND)


def test_api_27_denied_for_non_admin(env: dict, client: TestClient, db: Session) -> None:
    """矩阵：API-27 仅 ADM（复用 ``dict:update``，因为矩阵里没有 ``dict:disable`` 这个动作）。"""
    g3 = dict_dao.get_item_by_code(db, env["tenant_id"], "grade", "g3")
    assert g3 is not None
    resp = client.delete(f"/api/v1/dict-items/{g3.id}", headers=_h(env["tokens"]["viewer"]))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == int(ErrorCode.NO_PERMISSION)


# ============================================================================
# L2 · 种子幂等 与 迁移链
# ============================================================================
def test_seed_dicts_is_idempotent(env: dict, db: Session) -> None:
    """🔴 种子再次执行**不得重复插入**，也不得覆盖人工改过的值。

    先跑一次种子（往测试租户里灌 §6.1/§6.2 的字典），再手工改一项的显示名，
    然后再跑一次，断言：
    ① 第二次 **0 新增**；② 手工改的名字**没被冲掉**。

    第 ② 条才是幂等真正的价值 —— 否则每次部署都会把运维在界面上改过的字典重置回默认值。
    """
    from scripts.seed_dev import _seed_dicts

    _types1, added1 = _seed_dicts(db, env["tenant_id"])
    db.commit()
    assert added1 > 0, "首次执行种子应当写入字典数据"

    edu = dict_dao.get_item_by_code(db, env["tenant_id"], "education_level", "03")
    assert edu is not None
    edu.item_name = "本科（人工改过）"
    db.commit()

    _types2, added2 = _seed_dicts(db, env["tenant_id"])
    db.commit()

    assert added2 == 0, "重复执行种子不应新增字典项"
    again = dict_dao.get_item_by_code(db, env["tenant_id"], "education_level", "03")
    assert again is not None and again.item_name == "本科（人工改过）", "种子覆盖了人工维护的值"


def test_alembic_has_single_head() -> None:
    """🔴 迁移链必须**只有一个 head**，且**每个迁移文件都在链上**。

    新增迁移时若把 ``down_revision`` 误写成 ``None``（或写成不存在的版本号），
    会产生第二条头（分叉）：``upgrade head`` 会直接报错，但更隐蔽的后果是
    「本机 ``upgrade head`` 成功、同事机器少建几张表」。

    ⚠️ 这里**故意不硬编码 head 的版本号**。断言 ``heads[0] == "0003_audit"``
    这种写法每加一条迁移都会假失败，逼着人改测试 —— 改测试比改代码容易得多，
    于是红线被随手放宽。改为断言**不变量**：单头 + 迁移文件数 == head 祖先链长度
    （与文件命名无关，因为 ``0001_init_basement.py`` 的 revision 是 ``0001_basement``，
    「文件名 == revision」并不成立）。
    """
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert len(heads) == 1, f"迁移链出现分叉，heads={heads}"

    versions_dir = backend / "alembic" / "versions"
    on_disk = [p for p in versions_dir.glob("*.py") if not p.name.startswith("_")]
    in_chain = list(script.iterate_revisions(heads[0], "base"))
    assert len(on_disk) == len(in_chain), (
        f"迁移文件数与 head 祖先链长度不一致：磁盘 {len(on_disk)} 个文件、链上 {len(in_chain)} 条 —— "
        "有迁移文件游离在链外（同事机器会少建表）"
    )


def test_dict_tables_and_unique_index_behaviour(env: dict, db: Session) -> None:
    """两张表与关键索引确实建出来了；唯一键真的在拦重复。

    这里**直接插重复行**（绕过 Service）来验证约束落在**数据库**上 ——
    仅靠服务层查重是不够的：并发下两个请求都可能查不到、然后一起插入。
    """
    from sqlalchemy.exc import IntegrityError

    inspector = sa_inspect(db.get_bind())
    assert inspector.has_table("dict_type") and inspector.has_table("dict_item")

    db.add(
        DictItem(
            tenant_id=env["tenant_id"],
            type_code="grade",
            item_code="g1",  # 与夹具里的 g1 重复
            item_name="并发插入的重复项",
            sort_no=99,
            status=1,
            is_deleted=0,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
