"""CU-06 · 组织 / 岗位 / 用户 CRUD（接口 API-07 ~ API-17）。

分层
----
- **L1 契约层**（不依赖 MySQL / Redis，任何机器都能跑）：
  敏感字段加解密与脱敏、``dept_path`` 级联重算与环路检测（内存对象 + 打桩 DAO）、
  请求模型的「未传 / 显式 null / 0」三态语义、导入模板解析、响应模型不得含 ``password_hash``。
- **L2 集成层**（``@requires_db``，缺依赖时 skip 而非 fail）：
  11 个接口端到端、分页形状、唯一冲突、40904 引用校验、**最后一名管理员守卫**、
  以及 **CU-05 遗留的 AC-34 端点级 40401 断言**。

🔴 每条「拒绝」用例都配一条**反向对照**（同依赖在应放行时确实放行）——
   否则一个「永远抛 40301」的实现也能让所有用例通过。

覆盖的验收编号（延续评审文档 §7.4 的 ``AC-nn`` 体系）
----------------------------------------------------
| 编号 | 断言 | 位置 |
| --- | --- | --- |
| **AC-34 补** | 端点级「本域内不存在 → 40401」 | ``test_ac_34_endpoint_not_found_is_40401`` |
| **AC-36 补** | 40301 响应体不泄露资源是否存在 | ``test_denied_response_does_not_leak_existence`` |
| AC-09 补 | 部门移动后行级依据（``dept_path``）随子树重算 | ``test_update_org_moves_subtree_dept_path`` |

性能提示：bcrypt 校验很贵，故夹具用**同一个密码哈希**给全部测试账号复用
（``_pwd_hash()`` 走 ``lru_cache``，整个测试会话只算一次）。
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from functools import lru_cache

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.security import SecurityUtils
from app.core.tenant import CurrentPrincipal
from app.dao import org_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError
from app.models.enums import RoleDataScope
from app.models.tenant import OrgDept, Position, Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole
from app.schemas.org import OrgUpdateRequest
from app.schemas.user_admin import AdminUserOut, UserCreateRequest, UserImportError, UserUpdateRequest
from app.services.org_service import OrgService
from app.services.user_admin_service import UserAdminService
from tests.conftest import db_available, requires_db

L1_TENANT_ID = 7100000000000000001
_UNKNOWN_ID = "7999999999999999999"  # 雪花量级但库中必然不存在

PWD = "Admin@123456"


@lru_cache(maxsize=1)
def _pwd_hash() -> str:
    """测试账号共用的密码哈希 —— bcrypt(rounds=12) 约 0.3s，逐个算会让整轮慢几十秒。"""
    return SecurityUtils.hash_password(PWD)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(resp) -> dict:
    return resp.json()


# ============================================================================
# L1 · 敏感字段加解密与脱敏（app/core/crypto.py）
# ============================================================================
def test_crypto_roundtrip_with_configured_key() -> None:
    """有密钥时：加密 → 解密还原，且密文**不含明文子串**（真的加密了）。"""
    crypto.reset_key_cache()
    if not crypto.is_configured():
        pytest.skip("未配置 AES_KEY（.env）")

    plain = "13812348888"
    blob = crypto.encrypt_sensitive(plain)
    assert isinstance(blob, bytes)
    assert plain.encode() not in blob, "密文里出现明文子串，说明没有真正加密"
    assert crypto.decrypt_sensitive(blob) == plain


def test_crypto_same_plaintext_gives_different_ciphertext() -> None:
    """🔴 同一明文两次加密必须得到**不同**密文（nonce 随机）。

    否则攻击者能靠比对密文直接判断「两个用户手机号是否相同」，等于绕过了加密 ——
    这是 PII 加密最容易漏掉的一条性质。
    """
    crypto.reset_key_cache()
    if not crypto.is_configured():
        pytest.skip("未配置 AES_KEY（.env）")
    assert crypto.encrypt_sensitive("13812348888") != crypto.encrypt_sensitive("13812348888")


def test_crypto_empty_input_returns_none() -> None:
    """空值**不读密钥**即返回 ``None``。

    这条性质让「没配 AES_KEY 的环境」仍能创建不带手机号的用户 ——
    部署漏配密钥只影响必须加密的请求，而不是让整个用户管理瘫痪。
    """
    assert crypto.encrypt_sensitive(None) is None
    assert crypto.encrypt_sensitive("") is None
    assert crypto.encrypt_sensitive("   ") is None


def test_crypto_missing_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 无密钥时**必须抛错**，绝不静默退化成存明文。"""
    from app.config import settings

    monkeypatch.setattr(settings, "AES_KEY", "", raising=False)
    crypto.reset_key_cache()
    try:
        with pytest.raises(crypto.SensitiveFieldError):
            crypto.encrypt_sensitive("13812348888")
        assert crypto.is_configured() is False
    finally:
        crypto.reset_key_cache()


def test_crypto_bad_key_length_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥长度不对同样抛错（不是「凑合用」）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "AES_KEY", "deadbeef", raising=False)
    crypto.reset_key_cache()
    try:
        with pytest.raises(crypto.SensitiveFieldError):
            crypto.encrypt_sensitive("13812348888")
    finally:
        crypto.reset_key_cache()


def test_crypto_decrypt_garbage_returns_none_not_raise() -> None:
    """🔴 解密失败**返回 None**，不抛异常 —— 单条脏数据不得让整个列表接口 500。

    GCM 是认证加密：篡改密文会触发 ``InvalidTag``。正好用来验证
    「机器能发现密文被动过」，而不是解出一串乱码当手机号用。
    """
    crypto.reset_key_cache()
    if not crypto.is_configured():
        pytest.skip("未配置 AES_KEY（.env）")

    blob = crypto.encrypt_sensitive("13812348888")
    assert blob is not None
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    assert crypto.decrypt_sensitive(bytes(tampered)) is None
    assert crypto.decrypt_sensitive(b"short") is None
    assert crypto.decrypt_sensitive(None) is None


@pytest.mark.parametrize(
    ("plain", "expected"),
    [
        ("13812348888", "138****8888"),
        ("18600001111", "186****1111"),
        ("1234567", "123****4567"),
        ("12345", "*****"),  # 过短：整体打星，绝不透出原值
        (None, None),
        ("", None),
    ],
)
def test_mask_phone(plain: str | None, expected: str | None) -> None:
    """手机号脱敏：保留前 3 后 4，中间定长星号（接口 §1.7 示例口径）。"""
    assert crypto.mask_phone(plain) == expected


@pytest.mark.parametrize(
    ("plain", "expected"),
    [
        ("zhangsan@example.com", "z***@example.com"),
        ("a@b.cn", "a***@b.cn"),
        ("not-an-email", "********"),  # 非邮箱形状：整体打星，不猜测
        (None, None),
        ("", None),
    ],
)
def test_mask_email(plain: str | None, expected: str | None) -> None:
    """邮箱脱敏：只留首字符 + 域名。"""
    assert crypto.mask_email(plain) == expected


def test_admin_user_out_has_no_sensitive_columns() -> None:
    """🔴 响应模型里**根本没有** ``password_hash`` / ``phone_enc`` 这类字段。

    不是靠「记得别塞」，而是模型上不存在 —— 多塞一个键会被 ``extra="forbid"`` 顶回去。
    """
    names = set(AdminUserOut.model_fields)
    assert "password_hash" not in names
    assert "phone_enc" not in names
    assert "email_enc" not in names
    assert {"phone", "email"} <= names


# ============================================================================
# L1 · 请求模型的三态语义
# ============================================================================
def test_org_update_parent_id_semantics() -> None:
    """🔴 ``parent_id`` 三态：**未传=不移动**、``0``=移到根、``<id>``=移到该部门下。

    用 ``model_fields_set`` 而非 ``is not None`` 判断 —— 后者会把「移到根」
    误判为「不移动」，表现为「接口返回 200 但树结构没变」，极难排查。
    """
    not_provided = OrgUpdateRequest.model_validate({"dept_name": "新名字"})
    assert "parent_id" not in not_provided.model_fields_set

    to_root = OrgUpdateRequest.model_validate({"parent_id": 0})
    assert "parent_id" in to_root.model_fields_set
    assert to_root.parent_id == 0

    assert OrgUpdateRequest.model_validate({"parent_id": 123456}).parent_id == 123456


def test_org_update_rejects_server_derived_columns() -> None:
    """``dept_path`` / ``dept_level`` 是服务端推导列，前端传了就报错（``extra=forbid``）。"""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        OrgUpdateRequest.model_validate({"dept_path": "/1/", "dept_level": 1})


def test_user_update_phone_semantics() -> None:
    """``phone``：**未传=不改动**、显式 ``null``=清空（用 ``model_fields_set`` 区分）。"""
    not_provided = UserUpdateRequest.model_validate({"real_name": "张三"})
    assert "phone" not in not_provided.model_fields_set

    cleared = UserUpdateRequest.model_validate({"phone": None})
    assert "phone" in cleared.model_fields_set
    assert cleared.phone is None


def test_user_update_rejects_username() -> None:
    """登录名不可改（审计关联键），模型层直接拒绝。"""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        UserUpdateRequest.model_validate({"username": "new_name"})


def test_user_create_email_shape_validation() -> None:
    """邮箱做宽松形状校验：合法地址放行，明显手误拦下。"""
    import pydantic

    ok = UserCreateRequest.model_validate({"username": "u001", "email": "a.b+c@x.co"})
    assert ok.email == "a.b+c@x.co"
    with pytest.raises(pydantic.ValidationError):
        UserCreateRequest.model_validate({"username": "u001", "email": "no-at-sign"})
    with pytest.raises(pydantic.ValidationError):
        UserCreateRequest.model_validate({"username": "u001", "email": "a@nodot"})


# ============================================================================
# L1 · ``dept_path`` 级联重算与环路检测（打桩 DAO + 内存对象）
# ============================================================================
def _mem_dept(
    dept_id: int, parent_id: int, code: str, level: int, path: str | None, name: str | None = None
) -> OrgDept:
    """构造一个**未持久化**的 ``OrgDept``（不碰数据库，仅供纯逻辑用例使用）。"""
    return OrgDept(
        id=dept_id,
        tenant_id=L1_TENANT_ID,
        parent_id=parent_id,
        dept_code=code,
        dept_name=name or f"部门{dept_id}",
        dept_level=level,
        dept_path=path,
        sort_no=0,
        status=1,
        is_deleted=0,
    )


@pytest.fixture
def mem_tree(monkeypatch: pytest.MonkeyPatch) -> dict[int, OrgDept]:
    """内存组织树 + 打桩的 ``org_dao``：

    .. code-block:: text

        A(1) 根                    X(9) 另一个根
        └── B(2)
            └── C(3)
                └── D(4)
    """
    nodes = {
        1: _mem_dept(1, 0, "A", 1, "/1/"),
        2: _mem_dept(2, 1, "B", 2, "/1/2/"),
        3: _mem_dept(3, 2, "C", 3, "/1/2/3/"),
        4: _mem_dept(4, 3, "D", 4, "/1/2/3/4/"),
        9: _mem_dept(9, 0, "X", 1, "/9/"),
    }
    monkeypatch.setattr(org_dao, "list_depts", lambda *a, **k: list(nodes.values()))
    monkeypatch.setattr(org_dao, "get_dept_by_id", lambda *a, **k: nodes.get(int(a[2])))
    return nodes


def test_move_dept_recomputes_subtree_paths(mem_tree: dict[int, OrgDept]) -> None:
    """🔴 把 B 移到 X 下：**B/C/D 三层的 ``dept_path`` 与 ``dept_level`` 必须全部重算**。

    只改 B 自己的话，C/D 的路径前缀 ``/1/2/`` 就失效了 ——
    ``data_scope=2`` 的前缀匹配会**静默**丢掉整个子孙子树（不报错、不崩溃，
    只是有人突然看不见了）。这是本单元风险最高的一段逻辑。
    """
    b = mem_tree[2]
    assert OrgService._move_dept(None, L1_TENANT_ID, b, 9) is True  # type: ignore[arg-type]

    assert (b.parent_id, b.dept_path, b.dept_level) == (9, "/9/2/", 2)
    assert (mem_tree[3].dept_path, mem_tree[3].dept_level) == ("/9/2/3/", 3)
    assert (mem_tree[4].dept_path, mem_tree[4].dept_level) == ("/9/2/3/4/", 4)
    assert mem_tree[9].dept_path == "/9/", "目标父节点自身不应被改动"


def test_move_dept_to_root_relevels_subtree(mem_tree: dict[int, OrgDept]) -> None:
    """把 C 移到根：C 降为 1 级，D 随之从 4 级变成 2 级。"""
    c = mem_tree[3]
    assert OrgService._move_dept(None, L1_TENANT_ID, c, 0) is True  # type: ignore[arg-type]

    assert (c.parent_id, c.dept_path, c.dept_level) == (0, "/3/", 1)
    assert (mem_tree[4].dept_path, mem_tree[4].dept_level) == ("/3/4/", 2)
    assert mem_tree[2].dept_path == "/1/2/", "被移出子树的部分不应受影响"


def test_move_dept_no_change_is_noop(mem_tree: dict[int, OrgDept]) -> None:
    """父节点没变时返回 ``False``（不写库），避免无谓的全子树更新。"""
    b = mem_tree[2]
    before = (b.parent_id, b.dept_path, b.dept_level)
    assert OrgService._move_dept(None, L1_TENANT_ID, b, 1) is False  # type: ignore[arg-type]
    assert (b.parent_id, b.dept_path, b.dept_level) == before


def test_move_dept_into_own_descendant_rejected(mem_tree: dict[int, OrgDept]) -> None:
    """🔴 环路检测：把 B 移到自己的子孙 D 下面必须拒绝（否则形成自引用环）。"""
    with pytest.raises(IllegalStateError) as exc:
        OrgService._move_dept(None, L1_TENANT_ID, mem_tree[2], 4)  # type: ignore[arg-type]
    assert "子孙" in str(exc.value)


def test_move_dept_into_self_rejected(mem_tree: dict[int, OrgDept]) -> None:
    """把自己移到自己下面同样拒绝。"""
    with pytest.raises(IllegalStateError):
        OrgService._move_dept(None, L1_TENANT_ID, mem_tree[2], 2)  # type: ignore[arg-type]


def test_move_dept_rebuilds_stale_paths(mem_tree: dict[int, OrgDept]) -> None:
    """🔴 **自愈**：子树里若有 ``dept_path`` 失真（历史数据 / 早期 bug），
    移动时会按 ``parent_id`` 重新算一遍，把错误值纠正回来。

    这正是不用「拿旧路径做字符串替换」的原因 —— 那种写法会忠实继承错误。
    """
    mem_tree[3].dept_path = "垃圾数据/"
    mem_tree[4].dept_path = None
    assert OrgService._move_dept(None, L1_TENANT_ID, mem_tree[2], 9) is True  # type: ignore[arg-type]
    assert mem_tree[3].dept_path == "/9/2/3/"
    assert mem_tree[4].dept_path == "/9/2/3/4/"


def test_build_tree_promotes_orphans_to_root() -> None:
    """父节点缺失（已软删 / 数据异常）的部门**提升为根**，不让子树在界面上消失。"""
    rows = [
        _mem_dept(1, 0, "A", 1, "/1/"),
        _mem_dept(5, 999, "孤儿", 2, "/999/5/"),
        _mem_dept(2, 1, "B", 2, "/1/2/"),
    ]
    roots = OrgService._build_tree(rows)
    assert sorted(int(n.id) for n in roots) == [1, 5], "父节点缺失的节点应提升为根，而非被丢弃"
    a = next(n for n in roots if int(n.id) == 1)
    assert [int(c.id) for c in a.children] == [2]


def test_org_service_public_signatures_are_stable() -> None:
    """守卫：``OrgService`` 公开接口的参数个数（``routers`` 依赖它们，改名会静默 500）。"""
    for name, expected in [
        ("tree", 2),
        ("create_dept", 3),
        ("update_dept", 4),
        ("delete_dept", 3),
    ]:
        fn = getattr(OrgService, name)
        assert inspect.isfunction(fn)
        assert len(inspect.signature(fn).parameters) == expected, name


# ============================================================================
# L1 · 导入模板解析（不碰数据库）
# ============================================================================
def _dummy_principal() -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=L1_TENANT_ID + 101,
        tenant_id=L1_TENANT_ID,
        org_id=None,
        roles=("ADM",),
        data_scope=int(RoleDataScope.ALL),
    )


def test_import_rejects_unsupported_extension() -> None:
    """非 xlsx/csv 扩展名 → 41501（在碰数据库之前就拒绝）。"""
    with pytest.raises(BizError) as exc:
        UserAdminService.import_users(
            None, _dummy_principal(), filename="users.pdf", content=b"xx"  # type: ignore[arg-type]
        )
    assert exc.value.code == int(ErrorCode.UNSUPPORTED_MEDIA)


def test_import_rejects_oversize_file() -> None:
    """超过上传上限 → 41301。"""
    from app.config import settings

    big = b"x" * (settings.upload_max_file_size_bytes + 1)
    with pytest.raises(BizError) as exc:
        UserAdminService.import_users(
            None, _dummy_principal(), filename="users.csv", content=big  # type: ignore[arg-type]
        )
    assert exc.value.code == int(ErrorCode.UPLOAD_TOO_LARGE)


def test_import_csv_requires_username_column() -> None:
    """模板缺少「登录名」列 → 40001（比逐行报错更早、更明确）。"""
    with pytest.raises(BizError) as exc:
        UserAdminService.import_users(
            None,  # type: ignore[arg-type]
            _dummy_principal(),
            filename="users.csv",
            content="姓名,手机号\n张三,13812348888\n".encode("utf-8-sig"),
        )
    assert exc.value.code == int(ErrorCode.PARAM_INVALID)


def test_import_csv_header_aliases_and_row_numbers() -> None:
    """表头中英文都认；行号含表头从 1 起（便于按 Excel 行号定位）；空行不计入。"""
    rows = UserAdminService._read_csv(
        "登录名,姓名,手机号\nadmin,张三,13812348888\n,,\nu002,李四,\n".encode("utf-8-sig")
    )
    assert [r for r, _ in rows] == [2, 4], "空行应被跳过，且行号保留原始 Excel 行号"
    assert rows[0][1] == {"username": "admin", "real_name": "张三", "phone": "13812348888"}
    assert rows[1][1]["username"] == "u002"
    assert rows[1][1]["phone"] == ""


def test_import_csv_accepts_gbk() -> None:
    """Excel 另存的 GBK 编码 CSV 也能读（中文 Windows 的默认行为）。"""
    rows = UserAdminService._read_csv("登录名,姓名\nadmin,张三\n".encode("gbk"))
    assert rows[0][1]["real_name"] == "张三"


def test_zip_rows_ignores_unknown_and_duplicate_headers() -> None:
    """未知列忽略、重复列只取第一个 —— 不让模板里的多余列把解析搞崩。"""
    rows = UserAdminService._zip_rows(
        ["username", "unknown_col", "real_name", "real_name"],
        [["admin", "x", "张三", "覆盖值"]],
        base_row=2,
    )
    assert rows[0][1] == {"username": "admin", "real_name": "张三"}


def test_user_import_error_requires_reason() -> None:
    """错误项必须带 ``reason``（前端直接展示，不能是空字符串）。"""
    import pydantic

    assert UserImportError(row=3, username="u1", reason="登录名已存在").row == 3
    with pytest.raises(pydantic.ValidationError):
        UserImportError(row=3, username="u1")  # type: ignore[call-arg]


# ============================================================================
# L2 · 端到端夹具（独立租户，用例结束整体清理）
# ============================================================================
@pytest.fixture
def env(db: Session, client: TestClient) -> Iterator[dict]:
    """建一个**独立租户**：3 层组织 + 3 角色 + 4 账号 + 11 个权限点。

    用独立租户而非复用种子租户，是为了让断言不受既有数据影响，
    也能安全地测「最后一名管理员」这类依赖**全租户计数**的逻辑
    （若复用 demo 租户，种子里那个 admin 会让守卫永远不触发）。

    ⚠️ 缺依赖时**在函数体内 skip** —— 新版 pytest 不允许把 ``skipif`` 标记
    加到 fixture 上（``Marks cannot be applied to fixtures``），
    而 ``@requires_db`` 加在每个用例上只能保护用例本身，保护不到夹具的建库动作。
    """
    if not db_available():
        pytest.skip("需要可用的 MySQL（DS-1）")

    from app.dao.session import engine

    if not sa_inspect(engine).has_table("sys_user"):
        pytest.skip("数据库已连通但未执行迁移（alembic upgrade head）")

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(tenant_code=f"cu06_{suffix}", tenant_name=f"CU06测试_{suffix}", status=1)
    db.add(tenant)
    db.flush()
    tid = int(tenant.id)

    # ---- 组织：root → child → grandchild，另加一个兄弟部门 ----
    root = OrgDept(
        tenant_id=tid, parent_id=0, dept_code=f"root_{suffix}", dept_name="总部",
        dept_level=1, status=1,
    )
    db.add(root)
    db.flush()
    root.dept_path = f"/{root.id}/"

    child = OrgDept(
        tenant_id=tid, parent_id=int(root.id), dept_code=f"child_{suffix}", dept_name="研发部",
        dept_level=2, status=1,
    )
    db.add(child)
    db.flush()
    child.dept_path = f"/{root.id}/{child.id}/"

    sibling = OrgDept(
        tenant_id=tid, parent_id=int(root.id), dept_code=f"sib_{suffix}", dept_name="市场部",
        dept_level=2, status=1,
    )
    db.add(sibling)
    db.flush()
    sibling.dept_path = f"/{root.id}/{sibling.id}/"

    grand = OrgDept(
        tenant_id=tid, parent_id=int(child.id), dept_code=f"grand_{suffix}", dept_name="前端组",
        dept_level=3, status=1,
    )
    db.add(grand)
    db.flush()
    grand.dept_path = f"/{root.id}/{child.id}/{grand.id}/"

    # ---- 权限点 ----
    perm_codes = [
        "org:view", "org:create", "org:update", "org:delete",
        "position:view", "position:create",
        "user:view", "user:create", "user:update", "user:role", "user:import",
    ]
    perms: dict[str, SysPermission] = {}
    for code in perm_codes:
        p = SysPermission(
            tenant_id=tid, perm_code=code, perm_name=code, perm_type=3, parent_id=0, status=1
        )
        db.add(p)
        perms[code] = p
    db.flush()

    # ---- 角色 ----
    def _mk_role(code: str, scope: int, grants: list[str]) -> SysRole:
        r = SysRole(
            tenant_id=tid, role_code=code, role_name=code, data_scope=scope,
            is_builtin=1, status=1,
        )
        db.add(r)
        db.flush()
        for c in grants:
            db.add(SysRolePermission(tenant_id=tid, role_id=r.id, perm_id=perms[c].id))
        return r

    adm = _mk_role("ADM", int(RoleDataScope.ALL), perm_codes)
    hr = _mk_role("HR", int(RoleDataScope.ALL), ["org:view", "position:view"])
    # ⚠️ 给 MGR 挂 user:* 是**测试需要**：矩阵里 /users 仅 ADM，
    #    但要验证「传参只能收窄」和「最后一名超管守卫」，必须有一个
    #    data_scope=2 且能调通这些接口的**非 ADM** 角色。
    mgr = _mk_role(
        "MGR", int(RoleDataScope.OWN_DEPT),
        ["org:view", "user:view", "user:update", "user:role", "position:view"],
    )

    # ---- 账号 ----
    def _mk_user(name: str, role: SysRole, org_id: int | None) -> SysUser:
        u = SysUser(
            tenant_id=tid, username=name, password_hash=_pwd_hash(),
            real_name=name, org_id=org_id, status=1, login_fail_count=0,
        )
        db.add(u)
        db.flush()
        db.add(SysUserRole(tenant_id=tid, user_id=u.id, role_id=role.id))
        return u

    admin1 = _mk_user(f"admin1_{suffix}", adm, int(root.id))
    admin2 = _mk_user(f"admin2_{suffix}", adm, int(root.id))
    hr1 = _mk_user(f"hr1_{suffix}", hr, int(child.id))
    mgr1 = _mk_user(f"mgr1_{suffix}", mgr, int(child.id))
    db.commit()

    def _token_for(who: str) -> str:
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": data["usernames"][who], "password": PWD, "tenant_code": tenant.tenant_code},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0, body
        return body["data"]["access_token"]

    def _reload(uid: int) -> SysUser:
        """从库里重新读一条用户，断言**真实落库结果**。

        🔴 这里必须**同时**做两件事，缺一个都会读到旧值：

        1. ``commit()`` —— 结束当前事务。MySQL 默认隔离级别是 **REPEATABLE READ**，
           同一事务内的后续 SELECT 会复用第一次读时的快照，接口侧（另一个会话）
           刚提交的改动在旧事务里根本看不见；
        2. ``expire_all()`` —— 项目把会话配成 ``expire_on_commit=False``
           （见 ``app/dao/session.py``：为让 Service 提交后还能读对象属性），
           于是 identity map 里已加载的属性**不会**被后续 SELECT 覆盖。
           不过期掉它，就永远读到第一次加载的值。

        少了任一条，都会得到「明明改了、断言却读旧值」这种极耗时的假失败。
        """
        db.commit()
        db.expire_all()
        return db.execute(
            select(SysUser).where(SysUser.tenant_id == tid, SysUser.id == int(uid))
        ).scalar_one()

    data: dict = {
        "tenant_id": tid,
        "tenant_code": tenant.tenant_code,
        "root_id": int(root.id),
        "child_id": int(child.id),
        "grand_id": int(grand.id),
        "sibling_id": int(sibling.id),
        "users": {
            "admin1": int(admin1.id),
            "admin2": int(admin2.id),
            "hr1": int(hr1.id),
            "mgr1": int(mgr1.id),
        },
        "usernames": {
            "admin1": admin1.username,
            "admin2": admin2.username,
            "hr1": hr1.username,
            "mgr1": mgr1.username,
        },
        "role_ids": {"ADM": int(adm.id), "HR": int(hr.id), "MGR": int(mgr.id)},
        "db": db,
        "reload": _reload,
        "token": _token_for,
    }
    try:
        yield data
    finally:
        _cleanup(db, tid)


def _cleanup(db: Session, tenant_id: int) -> None:
    """按依赖顺序清掉本用例建的全部数据（只碰本租户）。"""
    db.rollback()
    db.execute(delete(SysUserRole).where(SysUserRole.tenant_id == tenant_id))
    db.execute(delete(SysRolePermission).where(SysRolePermission.tenant_id == tenant_id))
    db.execute(delete(SysUser).where(SysUser.tenant_id == tenant_id))
    db.execute(delete(SysRole).where(SysRole.tenant_id == tenant_id))
    db.execute(delete(SysPermission).where(SysPermission.tenant_id == tenant_id))
    db.execute(delete(Position).where(Position.tenant_id == tenant_id))
    db.execute(delete(OrgDept).where(OrgDept.tenant_id == tenant_id))
    db.execute(delete(Tenant).where(Tenant.id == tenant_id))
    db.commit()


# ============================================================================
# L2 · API-07 / 08 / 09：组织树与增改
# ============================================================================
@requires_db
def test_org_tree_returns_nested_string_ids(client: TestClient, env: dict) -> None:
    """API-07：树结构正确，且**所有 ID 是字符串**（CR-5 雪花 ID 精度红线）。"""
    resp = client.get("/api/v1/orgs/tree", headers=_h(env["token"]("admin1")))
    assert resp.status_code == 200, resp.text
    body = _body(resp)
    assert body["code"] == 0
    data = body["data"]
    assert data["total"] >= 4

    root = next(n for n in data["items"] if int(n["id"]) == env["root_id"])
    assert isinstance(root["id"], str), "CR-5：ID 必须是字符串"
    assert isinstance(root["parent_id"], str)
    child = next(c for c in root["children"] if int(c["id"]) == env["child_id"])
    grand = next(c for c in child["children"] if int(c["id"]) == env["grand_id"])
    assert grand["dept_level"] == 3
    assert grand["dept_path"] == f"/{env['root_id']}/{env['child_id']}/{env['grand_id']}/"


@requires_db
def test_create_org_computes_path_and_level(client: TestClient, env: dict) -> None:
    """API-08：``dept_path`` / ``dept_level`` 由服务端推导，数值必须正确。"""
    resp = client.post(
        "/api/v1/orgs",
        headers=_h(env["token"]("admin1")),
        json={"parent_id": str(env["grand_id"]), "dept_code": "team_a", "dept_name": "A 组"},
    )
    assert resp.status_code == 200, resp.text
    node = _body(resp)["data"]
    assert node["dept_level"] == 4
    assert node["dept_path"] == (
        f"/{env['root_id']}/{env['child_id']}/{env['grand_id']}/{node['id']}/"
    )


@requires_db
def test_create_org_duplicate_code_conflicts(client: TestClient, env: dict) -> None:
    """API-08：组织编码重复 → 40901（并指明字段，便于前端标红）。"""
    token = env["token"]("admin1")
    payload = {"parent_id": "0", "dept_code": "dup_code", "dept_name": "重复"}
    assert client.post("/api/v1/orgs", headers=_h(token), json=payload).status_code == 200
    second = client.post("/api/v1/orgs", headers=_h(token), json=payload)
    assert second.status_code == 409
    body = _body(second)
    assert body["code"] == 40901
    assert body["data"]["fields"][0]["field"] == "dept_code"


@requires_db
def test_update_org_moves_subtree_dept_path(client: TestClient, env: dict) -> None:
    """🔴 API-09：移动部门后**整棵子树**的 ``dept_path`` 都变了（行级权限的根基）。"""
    token = env["token"]("admin1")
    resp = client.put(
        f"/api/v1/orgs/{env['child_id']}",
        headers=_h(token),
        json={"parent_id": str(env["sibling_id"])},
    )
    assert resp.status_code == 200, resp.text
    node = _body(resp)["data"]
    assert node["dept_path"] == f"/{env['root_id']}/{env['sibling_id']}/{env['child_id']}/"

    tree = _body(client.get("/api/v1/orgs/tree", headers=_h(token)))["data"]
    root = next(n for n in tree["items"] if int(n["id"]) == env["root_id"])
    sib = next(c for c in root["children"] if int(c["id"]) == env["sibling_id"])
    child = next(c for c in sib["children"] if int(c["id"]) == env["child_id"])
    grand = next(c for c in child["children"] if int(c["id"]) == env["grand_id"])
    assert grand["dept_path"] == (
        f"/{env['root_id']}/{env['sibling_id']}/{env['child_id']}/{env['grand_id']}/"
    ), "子孙部门的路径也必须跟着重算"
    assert grand["dept_level"] == 4


@requires_db
def test_update_org_move_to_root(client: TestClient, env: dict) -> None:
    """``parent_id=0`` 表示**移到根**（不是「不移动」）—— 三态语义的端到端验证。"""
    resp = client.put(
        f"/api/v1/orgs/{env['child_id']}",
        headers=_h(env["token"]("admin1")),
        json={"parent_id": "0"},
    )
    assert resp.status_code == 200, resp.text
    node = _body(resp)["data"]
    assert int(node["parent_id"]) == 0
    assert node["dept_level"] == 1
    assert node["dept_path"] == f"/{env['child_id']}/"


@requires_db
def test_update_org_without_parent_id_keeps_position(client: TestClient, env: dict) -> None:
    """**反向对照**：只改名字、不传 ``parent_id`` 时，树结构必须**一点没动**。"""
    resp = client.put(
        f"/api/v1/orgs/{env['child_id']}",
        headers=_h(env["token"]("admin1")),
        json={"dept_name": "改个名字而已"},
    )
    assert resp.status_code == 200, resp.text
    node = _body(resp)["data"]
    assert node["dept_name"] == "改个名字而已"
    assert int(node["parent_id"]) == env["root_id"]
    assert node["dept_path"] == f"/{env['root_id']}/{env['child_id']}/"


@requires_db
def test_update_org_into_descendant_rejected(client: TestClient, env: dict) -> None:
    """🔴 移到自己的子孙下 → 42201（形成环会让行级权限整体错乱）。"""
    resp = client.put(
        f"/api/v1/orgs/{env['child_id']}",
        headers=_h(env["token"]("admin1")),
        json={"parent_id": str(env["grand_id"])},
    )
    assert resp.status_code == 422
    assert _body(resp)["code"] == 42201


# ============================================================================
# L2 · API-10：删除部门的引用校验
# ============================================================================
@requires_db
def test_delete_org_with_children_rejected(client: TestClient, env: dict) -> None:
    """有子部门 → 40904（而不是连子部门一起悄悄删掉）。"""
    resp = client.delete(f"/api/v1/orgs/{env['root_id']}", headers=_h(env["token"]("admin1")))
    assert resp.status_code == 409
    body = _body(resp)
    assert body["code"] == 40904
    assert body["data"]["child_count"] >= 2


@requires_db
def test_delete_org_with_users_rejected(client: TestClient, env: dict) -> None:
    """🔴 有账号挂着 → 40904。

    部门软删会让其下账号的 ``dept_path`` 解析退化为空（fail-closed），
    行级可见性**静默失效** —— 必须在删除时拦住，而不是让人事后发现「列表突然空了」。

    这里先把一个账号挪到「前端组」（叶子部门，无子部门），
    才能绕过「有子部门」那条检查、单独验证**引用校验**。
    """
    token = env["token"]("admin1")
    moved = client.put(
        f"/api/v1/users/{env['users']['hr1']}",
        headers=_h(token),
        json={"org_id": str(env["grand_id"])},
    )
    assert moved.status_code == 200, moved.text

    resp = client.delete(f"/api/v1/orgs/{env['grand_id']}", headers=_h(token))
    assert resp.status_code == 409
    body = _body(resp)
    assert body["code"] == 40904
    assert body["data"]["user_count"] >= 1


@requires_db
def test_delete_org_success_then_not_found(client: TestClient, env: dict) -> None:
    """空部门可以删；删完再删返回 40401（软删与「不存在」同口径，接口 §1.5）。"""
    token = env["token"]("admin1")
    created = _body(
        client.post(
            "/api/v1/orgs",
            headers=_h(token),
            json={"parent_id": "0", "dept_code": "to_del", "dept_name": "待删"},
        )
    )["data"]
    deleted = client.delete(f"/api/v1/orgs/{created['id']}", headers=_h(token))
    assert deleted.status_code == 200
    assert _body(deleted)["data"]["deleted"] is True

    again = client.delete(f"/api/v1/orgs/{created['id']}", headers=_h(token))
    assert again.status_code == 404
    assert _body(again)["code"] == 40401


# ============================================================================
# L2 · AC-34 补：端点级「本域内不存在 → 40401」
# ============================================================================
@requires_db
def test_ac_34_endpoint_not_found_is_40401(client: TestClient, env: dict) -> None:
    """**AC-34 端点级补测**（CU-05 遗留项）。

    铁律：**越权一律 40301，只有「本域内确实不存在/已软删」才 40401**。
    此处验证后者 —— 本租户内不存在的 ID 必须得到 40401（而非 200/403/500）。
    """
    token = env["token"]("admin1")
    cases = [
        ("put", f"/api/v1/orgs/{_UNKNOWN_ID}", {"dept_name": "x"}),
        ("delete", f"/api/v1/orgs/{_UNKNOWN_ID}", None),
        ("put", f"/api/v1/users/{_UNKNOWN_ID}", {"real_name": "x"}),
        ("put", f"/api/v1/users/{_UNKNOWN_ID}/roles", {"role_ids": []}),
    ]
    for method, url, payload in cases:
        resp = (
            client.put(url, headers=_h(token), json=payload)
            if method == "put"
            else client.delete(url, headers=_h(token))
        )
        assert resp.status_code == 404, f"{method.upper()} {url} → {resp.status_code}"
        assert _body(resp)["code"] == 40401, f"{method.upper()} {url} 应返回 40401"


@requires_db
def test_ac_34_unknown_parent_is_40401(client: TestClient, env: dict) -> None:
    """新建部门的父部门不存在 → 40401（不是静默挂成根部门）。"""
    resp = client.post(
        "/api/v1/orgs",
        headers=_h(env["token"]("admin1")),
        json={"parent_id": _UNKNOWN_ID, "dept_code": "orphan", "dept_name": "孤儿"},
    )
    assert resp.status_code == 404
    assert _body(resp)["code"] == 40401


# ============================================================================
# L2 · 权限：拒绝与放行的**双向**验证
# ============================================================================
@requires_db
def test_hr_can_view_org_but_cannot_create(client: TestClient, env: dict) -> None:
    """HR 在矩阵中有 ``org:view`` 无 ``org:create``：**能看不能建**。

    每一条「拒绝」都配一条「放行」对照 —— 否则一个永远 403 的实现也能骗过测试。
    """
    token = env["token"]("hr1")
    ok_resp = client.get("/api/v1/orgs/tree", headers=_h(token))
    assert ok_resp.status_code == 200, ok_resp.text
    assert _body(ok_resp)["code"] == 0

    denied = client.post(
        "/api/v1/orgs",
        headers=_h(token),
        json={"parent_id": "0", "dept_code": "hr_try", "dept_name": "HR 越权"},
    )
    assert denied.status_code == 403
    assert _body(denied)["code"] == 40301


@requires_db
def test_hr_cannot_list_users(client: TestClient, env: dict) -> None:
    """``/users`` 仅 ADM —— HR 调用一律 40301。"""
    resp = client.get("/api/v1/users", headers=_h(env["token"]("hr1")))
    assert resp.status_code == 403
    assert _body(resp)["code"] == 40301


@requires_db
def test_denied_response_does_not_leak_existence(client: TestClient, env: dict) -> None:
    """**AC-36 补**：40301 响应体**不含**任何资源字段（越权与不存在同码同形）。"""
    body = _body(client.get("/api/v1/users", headers=_h(env["token"]("hr1"))))
    assert body["code"] == 40301
    assert body["data"] is None, "40301 不得携带资源数据，否则可被用来探测资源存在性"


@requires_db
def test_authorization_layer_precedes_permission_layer(client: TestClient, env: dict) -> None:
    """无 Token → 40101（不是 40301）—— 实证处理链顺序「JWT 先于功能权限」。"""
    resp = client.get("/api/v1/orgs/tree")
    assert resp.status_code == 401
    assert _body(resp)["code"] == 40101


@requires_db
def test_request_org_id_must_narrow_own_scope(client: TestClient, env: dict) -> None:
    """🔴 API-13：``org_id`` 传参**只能收窄** —— MGR 传兄弟部门 → 40301。

    MGR 的 ``data_scope=2``，可见范围＝研发部 + 前端组；传市场部即为**放宽**，必须拒绝。
    """
    token = env["token"]("mgr1")
    inside = client.get(f"/api/v1/users?org_id={env['grand_id']}", headers=_h(token))
    assert inside.status_code == 200, inside.text
    assert _body(inside)["code"] == 0

    outside = client.get(f"/api/v1/users?org_id={env['sibling_id']}", headers=_h(token))
    assert outside.status_code == 403
    assert _body(outside)["code"] == 40301


@requires_db
def test_role_change_invalidates_perm_snapshot_immediately(client: TestClient, env: dict) -> None:
    """🔴 改完角色**立刻**生效（不是等 2 小时 Token 过期）。

    验证两件事：① ``assign_roles`` 确实失效了该用户的权限快照；
    ② 快照失效后回查数据库拿到的是**新**权限集合。
    ``mgr1`` 是**非 ADM**，权限判定走快照路径（ADM 走 JWT 短路，不在本用例范围）。
    """
    mgr_token = env["token"]("mgr1")
    assert client.get("/api/v1/users", headers=_h(mgr_token)).status_code == 200

    # 把 mgr1 换成 HR（HR 无 user:view / user:update）
    changed = client.put(
        f"/api/v1/users/{env['users']['mgr1']}/roles",
        headers=_h(env["token"]("admin1")),
        json={"role_ids": [str(env["role_ids"]["HR"])]},
    )
    assert changed.status_code == 200, changed.text

    after = client.get("/api/v1/users", headers=_h(mgr_token))
    assert after.status_code == 403, "旧 Token 也应立即失去 user:view"
    assert _body(after)["code"] == 40301


# ============================================================================
# L2 · API-11 / 12：岗位
# ============================================================================
@requires_db
def test_position_create_and_list(client: TestClient, env: dict) -> None:
    """API-12 建岗位 → API-11 能查到；形状为 ``{total, items}``（非分页列表约定）。"""
    token = env["token"]("admin1")
    resp = client.post(
        "/api/v1/positions",
        headers=_h(token),
        json={
            "position_code": "dev_senior",
            "position_name": "高级开发",
            "dept_id": str(env["child_id"]),
            "position_level": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    created = _body(resp)["data"]
    assert isinstance(created["id"], str)
    assert created["status_name"] == "启用"

    listed = client.get("/api/v1/positions", headers=_h(token))
    assert listed.status_code == 200
    data = _body(listed)["data"]
    assert set(data) == {"total", "items"}
    assert any(int(p["id"]) == int(created["id"]) for p in data["items"])

    dup = client.post(
        "/api/v1/positions",
        headers=_h(token),
        json={"position_code": "dev_senior", "position_name": "重复"},
    )
    assert dup.status_code == 409
    assert _body(dup)["code"] == 40901


@requires_db
def test_position_unknown_dept_is_40401(client: TestClient, env: dict) -> None:
    """岗位挂到不存在的组织 → 40401（不写悬挂引用）。"""
    resp = client.post(
        "/api/v1/positions",
        headers=_h(env["token"]("admin1")),
        json={"position_code": "ghost_pos", "position_name": "幽灵岗", "dept_id": _UNKNOWN_ID},
    )
    assert resp.status_code == 404
    assert _body(resp)["code"] == 40401


# ============================================================================
# L2 · API-13 / 14 / 15：用户
# ============================================================================
@requires_db
def test_user_list_uses_page_field_not_items_wrapper(client: TestClient, env: dict) -> None:
    """🔴 分页形状（接口 §1.1）：``data`` 是**数组**，分页信息在**同级** ``page``。

    ❌ 不是 ``data.items[]``。这条约定前端 axios 封装直接照抄，形状错了联调必炸。
    """
    resp = client.get(
        "/api/v1/users?page=1&per_page=2", headers=_h(env["token"]("admin1"))
    )
    assert resp.status_code == 200, resp.text
    body = _body(resp)
    assert isinstance(body["data"], list), "data 必须是数组，分页信息不得塞进 data"
    assert len(body["data"]) <= 2
    page = body["page"]
    assert set(page) == {"total", "page", "per_page", "pages"}
    assert page["total"] >= 4
    assert page["page"] == 1
    assert page["per_page"] == 2
    assert page["pages"] == (page["total"] + 1) // 2


@requires_db
def test_user_list_per_page_capped_at_100(client: TestClient, env: dict) -> None:
    """``per_page`` 超上限按 100 处理并在 ``page`` 中回显实际值（接口 §1.1）。"""
    resp = client.get("/api/v1/users?per_page=999", headers=_h(env["token"]("admin1")))
    assert resp.status_code == 200
    assert _body(resp)["page"]["per_page"] == 100


@requires_db
def test_user_list_filters_by_org_subtree_and_role(client: TestClient, env: dict) -> None:
    """``org_id`` 过滤**含子树**；``role_id`` 过滤生效。"""
    token = env["token"]("admin1")
    by_org = client.get(f"/api/v1/users?org_id={env['child_id']}&per_page=100", headers=_h(token))
    usernames = {u["username"] for u in _body(by_org)["data"]}
    assert env["usernames"]["hr1"] in usernames
    assert env["usernames"]["mgr1"] in usernames
    assert env["usernames"]["admin1"] not in usernames, "admin1 挂在总部，不在研发部子树上"

    by_role = client.get(
        f"/api/v1/users?role_id={env['role_ids']['ADM']}&per_page=100", headers=_h(token)
    )
    assert {u["username"] for u in _body(by_role)["data"]} == {
        env["usernames"]["admin1"],
        env["usernames"]["admin2"],
    }


@requires_db
def test_create_user_returns_initial_password_once_and_masks_pii(
    client: TestClient, env: dict
) -> None:
    """API-14：随机初始密码**仅回显一次**；手机/邮箱以**脱敏**形式返回。"""
    resp = client.post(
        "/api/v1/users",
        headers=_h(env["token"]("admin1")),
        json={
            "username": f"newbie_{uuid.uuid4().hex[:6]}",
            "real_name": "新人",
            "org_id": str(env["grand_id"]),
            "phone": "13812348888",
            "email": "newbie@example.com",
            "role_ids": [str(env["role_ids"]["HR"])],
        },
    )
    assert resp.status_code == 200, resp.text
    data = _body(resp)["data"]
    assert data["initial_password"], "未提供密码时服务端应生成并回显一次"

    user = data["user"]
    assert user["phone"] == "138****8888", "手机号必须脱敏下发"
    assert user["email"] == "n***@example.com", "邮箱必须脱敏下发"
    assert user["roles"] == ["HR"]
    assert isinstance(user["id"], str)
    assert user["created_at"] and "T" not in user["created_at"], "时间格式应为 YYYY-MM-DD HH:mm:ss"

    # 新账号能登录 —— 证明回显的密码**真实可用**，而不是随手拼的字符串
    login = client.post(
        "/api/v1/auth/login",
        json={
            "username": user["username"],
            "password": data["initial_password"],
            "tenant_code": env["tenant_code"],
        },
    )
    assert login.status_code == 200, login.text
    assert _body(login)["code"] == 0


@requires_db
def test_create_user_duplicate_username_conflicts(client: TestClient, env: dict) -> None:
    """登录名重复 → 40901。"""
    resp = client.post(
        "/api/v1/users",
        headers=_h(env["token"]("admin1")),
        json={"username": env["usernames"]["hr1"], "password": PWD},
    )
    assert resp.status_code == 409
    body = _body(resp)
    assert body["code"] == 40901
    assert body["data"]["fields"][0]["field"] == "username"


@requires_db
def test_create_user_with_explicit_password_does_not_echo_it(
    client: TestClient, env: dict
) -> None:
    """管理员自填密码时**不回显** —— 没必要也不该再抄一遍。"""
    resp = client.post(
        "/api/v1/users",
        headers=_h(env["token"]("admin1")),
        json={"username": f"explicit_{uuid.uuid4().hex[:6]}", "password": "SelfPwd@123"},
    )
    assert resp.status_code == 200, resp.text
    assert _body(resp)["data"]["initial_password"] is None


@requires_db
def test_update_user_clear_phone_vs_keep_phone(client: TestClient, env: dict) -> None:
    """🔴 ``phone`` 未传=保留、显式 ``null``=清空 —— 端到端验证三态语义。

    同时验证**落库的是密文**（而不是「接口脱敏了、库里还是明文」这种假安全）。
    """
    token = env["token"]("admin1")
    uid = _body(
        client.post(
            "/api/v1/users",
            headers=_h(token),
            json={
                "username": f"pii_{uuid.uuid4().hex[:6]}",
                "password": PWD,
                "phone": "13900001111",
            },
        )
    )["data"]["user"]["id"]

    # 库里的确是密文：非空、且不含明文子串
    raw = env["reload"](int(uid))
    assert raw.phone_enc is not None, "手机号应加密落库"
    assert b"13900001111" not in raw.phone_enc, "库里出现了明文手机号"

    kept = client.put(f"/api/v1/users/{uid}", headers=_h(token), json={"real_name": "改个名"})
    assert kept.status_code == 200
    assert _body(kept)["data"]["phone"] == "139****1111", "未传 phone 不应清空"

    cleared = client.put(f"/api/v1/users/{uid}", headers=_h(token), json={"phone": None})
    assert cleared.status_code == 200
    assert _body(cleared)["data"]["phone"] is None, "显式传 null 应清空"
    assert env["reload"](int(uid)).phone_enc is None, "清空应把密文列置 NULL"


@requires_db
def test_update_user_enable_clears_lock(client: TestClient, env: dict) -> None:
    """启用账号会**顺带清零失败计数并解锁** —— 否则用户下次登录仍被 40103 挡住。"""
    token = env["token"]("admin1")
    uid = env["users"]["hr1"]

    disabled = client.put(f"/api/v1/users/{uid}", headers=_h(token), json={"status": 0})
    assert disabled.status_code == 200
    assert _body(disabled)["data"]["status_name"] == "禁用"

    # 手工造一个「锁定中」的状态
    user = env["reload"](uid)
    user.login_fail_count = 5
    user.locked_until = datetime.now() + timedelta(minutes=10)
    env["db"].commit()

    enabled = client.put(f"/api/v1/users/{uid}", headers=_h(token), json={"status": 1})
    assert enabled.status_code == 200
    refreshed = env["reload"](uid)
    assert refreshed.login_fail_count == 0
    assert refreshed.locked_until is None


@requires_db
def test_user_cannot_disable_self(client: TestClient, env: dict) -> None:
    """🔴 不能禁用自己的账号（自锁防护的第一层）。"""
    resp = client.put(
        f"/api/v1/users/{env['users']['admin1']}",
        headers=_h(env["token"]("admin1")),
        json={"status": 0},
    )
    assert resp.status_code == 422
    assert _body(resp)["code"] == 42201


@requires_db
def test_user_cannot_change_own_roles(client: TestClient, env: dict) -> None:
    """🔴 不能修改自己的角色（一次误点就能把自己降权到登不进后台）。"""
    resp = client.put(
        f"/api/v1/users/{env['users']['admin1']}/roles",
        headers=_h(env["token"]("admin1")),
        json={"role_ids": []},
    )
    assert resp.status_code == 422
    assert _body(resp)["code"] == 42201


# ============================================================================
# L2 · API-16：分配角色 + 用户级权限版本号
# ============================================================================
@requires_db
def test_assign_roles_replaces_and_bumps_user_perm_version(
    client: TestClient, env: dict
) -> None:
    """API-16：全量替换语义；返回**用户级**权限版本号（CU-05 埋下、CU-06 首次使用的计数）。"""
    token = env["token"]("admin1")
    uid = env["users"]["hr1"]

    resp = client.put(
        f"/api/v1/users/{uid}/roles",
        headers=_h(token),
        json={"role_ids": [str(env["role_ids"]["MGR"])]},
    )
    assert resp.status_code == 200, resp.text
    data = _body(resp)["data"]
    assert data["role_codes"] == ["MGR"]
    assert data["perm_version"] >= 1, "改「用户↔角色」应递增用户级权限版本号"

    again = _body(
        client.put(
            f"/api/v1/users/{uid}/roles",
            headers=_h(token),
            json={"role_ids": [str(env["role_ids"]["HR"]), str(env["role_ids"]["MGR"])]},
        )
    )["data"]
    assert again["perm_version"] > data["perm_version"], "版本号只增不减，否则前端不会重拉快照"


@requires_db
def test_assign_roles_rejects_foreign_role_id(client: TestClient, env: dict) -> None:
    """跨租户 / 已失效的角色 ID → 40001（防越权注入角色）。"""
    resp = client.put(
        f"/api/v1/users/{env['users']['hr1']}/roles",
        headers=_h(env["token"]("admin1")),
        json={"role_ids": [_UNKNOWN_ID]},
    )
    assert resp.status_code == 400
    body = _body(resp)
    assert body["code"] == 40001
    assert body["data"]["fields"][0]["field"] == "role_ids"


# ============================================================================
# L2 · 最后一名管理员守卫（自锁防护的核心）
# ============================================================================
@requires_db
def test_last_enabled_admin_cannot_be_stripped(client: TestClient, env: dict) -> None:
    """🔴 **最后一名可用超管**不能被摘掉 ``ADM``。

    步骤（每步都带反向对照，证明守卫不是「永远拒绝」）：

    1. ``mgr1`` 摘掉 ``admin2`` 的 ADM → **允许**（此时 ``admin1`` 还是超管）；
    2. ``mgr1`` 再摘掉 ``admin1`` 的 ADM → **42201**（摘完就没人能管后台了）。
    """
    mgr_token = env["token"]("mgr1")

    first = client.put(
        f"/api/v1/users/{env['users']['admin2']}/roles",
        headers=_h(mgr_token),
        json={"role_ids": []},
    )
    assert first.status_code == 200, first.text
    assert _body(first)["data"]["role_codes"] == [], "还有另一个超管时，摘角色应当放行"

    second = client.put(
        f"/api/v1/users/{env['users']['admin1']}/roles",
        headers=_h(mgr_token),
        json={"role_ids": []},
    )
    assert second.status_code == 422, second.text
    body = _body(second)
    assert body["code"] == 42201
    assert "超级管理员" in body["message"]


@requires_db
def test_last_enabled_admin_cannot_be_disabled(client: TestClient, env: dict) -> None:
    """同样的守卫覆盖 API-15：禁用最后一名可用超管 → 42201。"""
    mgr_token = env["token"]("mgr1")

    # 反向对照：先禁用 admin2（admin1 还在）→ 放行
    first = client.put(
        f"/api/v1/users/{env['users']['admin2']}",
        headers=_h(mgr_token),
        json={"status": 0},
    )
    assert first.status_code == 200, first.text

    second = client.put(
        f"/api/v1/users/{env['users']['admin1']}",
        headers=_h(mgr_token),
        json={"status": 0},
    )
    assert second.status_code == 422, second.text
    assert _body(second)["code"] == 42201


@requires_db
def test_non_admin_cannot_reach_guard_returns_40301_first(client: TestClient, env: dict) -> None:
    """处理链顺序：**功能权限先于业务守卫**。

    把 ``admin2`` 的角色清空后，它的旧 Token 仍然带着 ``ADM``（JWT 里的角色在签发时固定），
    因此这里特意用 ``hr1``（从来没当过 ADM）来验证：
    没有 ``user:update`` 权限时得到的是 **40301**，而不是 42201 ——
    说明守卫位于权限层之后，不会把「权限不足」误报成「业务冲突」。
    """
    resp = client.put(
        f"/api/v1/users/{env['users']['admin1']}",
        headers=_h(env["token"]("hr1")),
        json={"status": 0},
    )
    assert resp.status_code == 403
    assert _body(resp)["code"] == 40301


# ============================================================================
# L2 · API-17：批量导入
# ============================================================================
@requires_db
def test_import_users_csv_partial_success(client: TestClient, env: dict) -> None:
    """API-17：合法行入库、非法行只记错误 —— **部分成功不整批回滚**。

    4 行数据：2 成功（含角色码解析）+ 2 失败（登录名重复 / 登录名为空）。
    """
    token = env["token"]("admin1")
    suffix = uuid.uuid4().hex[:6]
    csv_text = (
        "登录名,姓名,手机号,邮箱,组织编码,岗位编码,角色码\n"
        f"imp_a_{suffix},甲,13800001111,a@x.com,,,HR\n"
        f"{env['usernames']['hr1']},重复登录名,,,,\n"
        f"imp_c_{suffix},丙,,,,,\n"
        ",空登录名,,,,\n"
    ).encode("utf-8-sig")

    resp = client.post(
        "/api/v1/users/import",
        headers=_h(token),
        files={"file": ("users.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = _body(resp)
    assert body["code"] == 0, "部分失败不改变 code —— 与 F-3.1 同一取舍"
    data = body["data"]
    assert data["total"] == 4, data
    assert data["success"] == 2, data
    assert data["failed"] == 2, data
    assert data["total"] == data["success"] + data["failed"]

    reasons = " ".join(e["reason"] for e in data["errors"])
    assert "已存在" in reasons
    assert "为空" in reasons
    for e in data["errors"]:
        assert e["row"] >= 2, "行号应从 2 起（1 是表头）"

    # 成功的两行确实建成了
    listed = client.get("/api/v1/users?per_page=100", headers=_h(token))
    names = {u["username"] for u in _body(listed)["data"]}
    assert f"imp_a_{suffix}" in names
    assert f"imp_c_{suffix}" in names

    # 导入行的角色也落上了
    imported = next(u for u in _body(listed)["data"] if u["username"] == f"imp_a_{suffix}")
    assert imported["roles"] == ["HR"]


@requires_db
def test_import_users_rejects_bad_extension_endpoint(client: TestClient, env: dict) -> None:
    """端点级：非 xlsx/csv → 41501。"""
    resp = client.post(
        "/api/v1/users/import",
        headers=_h(env["token"]("admin1")),
        files={"file": ("users.txt", b"a,b", "text/plain")},
    )
    assert resp.status_code == 415
    assert _body(resp)["code"] == 41501


@requires_db
def test_import_users_unknown_org_code_reported(client: TestClient, env: dict) -> None:
    """组织编码不存在 → 该行报错（不静默塞个空组织）。"""
    csv_text = "登录名,组织编码\nimp_bad_zz,no_such_dept\n".encode("utf-8-sig")
    resp = client.post(
        "/api/v1/users/import",
        headers=_h(env["token"]("admin1")),
        files={"file": ("users.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 200
    data = _body(resp)["data"]
    assert data["success"] == 0
    assert data["failed"] == 1
    assert "组织编码不存在" in data["errors"][0]["reason"]


@requires_db
def test_import_users_xlsx_template(client: TestClient, env: dict) -> None:
    """API-17：``.xlsx`` 模板走 ``openpyxl`` 分支（与 CSV 是**两条独立代码路径**）。

    缺 ``openpyxl`` 时本用例自动跳过（``importorskip``）——
    服务端此时会返回 **41501 + 提示改用 CSV**，而不是 500。
    """
    openpyxl = pytest.importorskip("openpyxl", reason="未安装 openpyxl（xlsx 解析依赖）")
    from io import BytesIO

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["登录名", "姓名", "手机号", "组织编码", "岗位编码", "角色码"])
    suffix = uuid.uuid4().hex[:6]
    ws.append([f"xls_a_{suffix}", "甲", "13800002222", "", "", "HR"])
    ws.append(["", "缺登录名", "", "", "", ""])  # 有内容但登录名为空 → 该行报错
    buf = BytesIO()
    wb.save(buf)

    resp = client.post(
        "/api/v1/users/import",
        headers=_h(env["token"]("admin1")),
        files={
            "file": (
                "users.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    data = _body(resp)["data"]
    assert data["success"] == 1, data
    assert data["failed"] == 1, data
    assert "为空" in data["errors"][0]["reason"]

    listed = client.get(f"/api/v1/users?keyword=xls_a&per_page=100", headers=_h(env["token"]("admin1")))
    names = {u["username"] for u in _body(listed)["data"]}
    assert f"xls_a_{suffix}" in names
    # Excel 里的手机号是**数字单元格** → 解析后仍应被 AES 加密并脱敏回显
    imported = next(u for u in _body(listed)["data"] if u["username"] == f"xls_a_{suffix}")
    assert imported["phone"] == "138****2222"
