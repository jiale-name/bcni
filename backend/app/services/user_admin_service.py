"""用户管理业务编排（F-1.1 / F-1.3，接口 API-13 ~ API-17）。

分层位置：``routers → **services** → dao``。

🔴 本模块要防的三类事故
=======================
**一、「把自己锁在门外」（自锁）**
    禁用最后一个可用的超管、或把唯一的超管从 ``ADM`` 角色里摘出去，
    会让系统**再也没有人能进管理后台** —— 界面上没有任何入口能修回来，只能改库。
    因此 :meth:`UserAdminService.update_user` 与 :meth:`UserAdminService.assign_roles`
    都有前置守卫（42201）。守卫的判定统一走
    :func:`app.dao.user_admin_dao.count_enabled_admins`（带 ``exclude_user_id`` 预演）。

**二、改完角色/组织却没人通知前端**
    用户的 ``org_id`` 一变，``data_scope=2`` 的行级可见范围随之改变；角色一变，
    功能权限集合随之改变。两者都必须让该用户的**权限快照失效**并**递增用户级权限版本号**，
    否则用户会拿着旧快照越权/失权最长 ``ACCESS_TOKEN_EXPIRE_SECONDS``（2 小时）。
    ⚠️ CU-05 建好了 ``rbac_dao.bump_user_perm_version`` 却一直没有调用方 ——
    本模块是它的**首个真实使用者**（用户↔角色变更正是「用户级」版本号的用途）。

**三、PII 明文落库或明文下发**
    手机号/邮箱**只经** :mod:`app.core.crypto` 的加解密函数进出，本模块内**不出现**明文
    透传路径：写库一律 ``encrypt_sensitive``，下发一律 ``mask_encrypted_*``。
    密钥缺失时**拒绝写入**而不是退化成存明文。

⚠️ 与 CU-05 的对称性：那里改的是「角色↔权限」（影响该角色下**所有**用户 → 递增**全局**版本），
   这里改的是「用户↔角色」（只影响**一个**用户 → 递增**用户级**版本）。
"""

from __future__ import annotations

import csv
import io
import secrets
import string
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.core import crypto
from app.core.logging import get_logger
from app.core.security import PASSWORD_MIN_LENGTH, SecurityUtils
from app.core.snowflake import next_id
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import org_dao, rbac_dao, user_admin_dao, user_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError
from app.models.enums import RoleDataScope, SysUserStatus, label_of
from app.models.user import SysRole, SysUser, SysUserRole
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.schemas.user_admin import (
    AdminUserOut,
    AssignRolesRequest,
    UserCreateOut,
    UserCreateRequest,
    UserImportError,
    UserImportOut,
    UserRolesOut,
    UserUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)

#: 支持的导入文件扩展名（``.xlsx`` 需 openpyxl；``.csv`` 无额外依赖）
_EXCEL_EXT = {"xlsx", "xlsm"}
_CSV_EXT = {"csv"}
#: 模板列名 → 内部字段（中英文都认，避免管理员拿到英文模板就导不进来）
_HEADER_ALIASES: dict[str, str] = {
    "登录名": "username",
    "账号": "username",
    "username": "username",
    "姓名": "real_name",
    "real_name": "real_name",
    "name": "real_name",
    "手机号": "phone",
    "手机": "phone",
    "phone": "phone",
    "邮箱": "email",
    "email": "email",
    "组织编码": "org_code",
    "部门编码": "org_code",
    "org_code": "org_code",
    "岗位编码": "position_code",
    "position_code": "position_code",
    "角色码": "role_codes",
    "角色": "role_codes",
    "role_codes": "role_codes",
}

#: 模板**认得的**字段名集合 —— 表头里其他任何列一律丢弃。
#: 让 ``record`` 只含已知字段，下游就不必再逐个防御未知键。
_TEMPLATE_FIELDS = frozenset(_HEADER_ALIASES.values())


def _gen_password(length: int = 16) -> str:
    """生成满足强度要求的随机初始密码（含大小写/数字/符号）。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


def _encrypt_or_fail(value: str | None, field: str) -> bytes | None:
    """加密敏感字段；密钥缺失时抛**可读的部署错误**而不是让栈冒到全局处理器。

    ``crypto.encrypt_sensitive`` 对 ``None`` / 空串**不读密钥**直接返回 ``None``，
    因此「不填手机号的用户」在未配置 ``AES_KEY`` 的环境里依然能创建 ——
    只有真的要写 PII 时才会因缺密钥而失败。这个顺序很重要：
    它让「部署漏配密钥」只影响必须加密的请求，而不是让整个用户管理瘫痪。
    """
    try:
        return crypto.encrypt_sensitive(value)
    except crypto.SensitiveFieldError as exc:
        logger.error("敏感字段加密失败 field=%s err=%s", field, exc)
        raise BizError(
            ErrorCode.INTERNAL_ERROR,
            f"服务端未正确配置敏感字段加密密钥（AES_KEY），无法保存{field}",
        ) from exc


def _user_snapshot(user: SysUser) -> dict[str, object]:
    """取用户的可留痕字段快照（供审计的改前/改后值对比）。

    🔴 **刻意不含任何 PII 明文**：
    - ``phone_enc`` / ``email_enc`` 是密文 BLOB，写进审计既无意义（不可读）又扩大暴露面
      —— 故只记**「是否已填写」**（``has_phone`` / ``has_email``），
      足以回答「这次改没改联系方式」，又不把密文扩散到第二张表；
    - ``password_hash`` **绝不出现**（改密另有 ``change_password`` 审计）。

    这条边界很重要：审计日志是**长期留存**（≥12 个月）且**只追加**
    （发现写错了也删不掉），因此「能不写就不写」比「写了再说」安全得多。
    """
    return {
        "username": user.username,
        "real_name": user.real_name,
        "org_id": user.org_id,
        "position_id": user.position_id,
        "status": int(user.status),
        "has_phone": user.phone_enc is not None,
        "has_email": user.email_enc is not None,
        "is_deleted": int(user.is_deleted),
    }


class UserAdminService:
    """用户管理。**全部为无状态静态方法**（会话由 ``get_db`` 注入）。"""

    # ------------------------------------------------------------------
    # API-13 用户列表（分页）
    # ------------------------------------------------------------------
    @staticmethod
    def list_users(
        db: Session,
        principal: CurrentPrincipal,
        *,
        keyword: str | None = None,
        org_id: int | None = None,
        position_id: int | None = None,
        status: int | None = None,
        role_id: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[AdminUserOut], int, int, int]:
        """分页检索用户。

        :return: ``(items, total, page, per_page)`` —— 由 Router 交给 ``paginated()``
            组装成「``data`` 放数组、``page`` 放分页信息」的标准形状（接口 §1.1）。

        :raises PermissionDeniedError: ``org_id`` 超出自身数据域（40301）
        :raises NotFoundError: ``org_id`` 指向的部门不存在（40401）
        """
        tenant_id = principal.tenant_id
        dept_ids: list[int] | None = None

        # 🔴 **调用方自身的**部门子树要显式解析，不能指望 `principal.dept_ids`：
        #    JWT 里只有 `oid`/`dsc`，部门子树是**派生数据**，由服务层按需回填
        #    （见 `CurrentPrincipal.dept_ids` 的注释与 `AuthService._resolve_scope_depts`）。
        #    直接用它做收窄校验会得到空集合 ⇒ 任何 `org_id` 都被判「超范围」，
        #    表现为 `data_scope=2` 的用户**永远查不到人**。
        own_dept_ids: tuple[int, ...] = ()
        if int(principal.data_scope) in (
            int(RoleDataScope.OWN_DEPT),
            int(RoleDataScope.SELF),
        ):
            try:
                own_dept_ids = OrgScopeService.resolve_dept_ids(
                    db, tenant_id, principal.org_id
                )
            except Exception as exc:  # noqa: BLE001 — 解析失败按「不可见」处理
                logger.warning("自身部门子树解析失败（收窄范围为空）err=%s", exc)
                own_dept_ids = ()

        if org_id is not None:
            # 前端传参**只能收窄**（接口 §1.3 红线）：先过收窄校验，再展开子树
            DataScopeFilter.assert_scope_narrowing(
                principal, [int(org_id)], dept_ids=own_dept_ids
            )
            dept = org_dao.get_dept_by_id(db, tenant_id, int(org_id))
            if dept is None or dept.is_deleted:
                raise NotFoundError("组织不存在")
            dept_ids = list(OrgScopeService.resolve_dept_ids(db, tenant_id, int(org_id)))

        per_page = normalize_per_page(per_page)
        page = max(1, int(page))
        offset = (page - 1) * per_page

        total = user_admin_dao.count_users(
            db,
            tenant_id,
            keyword=keyword,
            org_ids=dept_ids,
            position_id=position_id,
            status=status,
            role_id=role_id,
        )
        rows = user_admin_dao.list_users(
            db,
            tenant_id,
            keyword=keyword,
            org_ids=dept_ids,
            position_id=position_id,
            status=status,
            role_id=role_id,
            offset=offset,
            limit=per_page,
        )

        # 组织/岗位/角色名一次查全（组织与岗位是**配置类**小表，通常几十行；
        # 相比「按 20 个用户逐个查名字」的 N+1，一次全量扫描更划算且代码更短）
        org_names = {int(d.id): d.dept_name for d in org_dao.list_depts(db, tenant_id)}
        position_names = {
            int(p.id): p.position_name for p in org_dao.list_positions(db, tenant_id)
        }
        role_map = user_admin_dao.list_role_map_by_users(
            db, tenant_id, [int(r.id) for r in rows]
        )

        items = [
            UserAdminService._to_out(
                row,
                org_names=org_names,
                position_names=position_names,
                roles=role_map.get(int(row.id), []),
            )
            for row in rows
        ]
        return items, total, page, per_page

    # ------------------------------------------------------------------
    # API-14 新建用户
    # ------------------------------------------------------------------
    @staticmethod
    def create_user(
        db: Session, principal: CurrentPrincipal, req: UserCreateRequest
    ) -> UserCreateOut:
        """新建用户。

        :raises BizError(40901): 登录名已存在
        :raises NotFoundError: 组织 / 岗位 / 角色不存在（40401）
        """
        tenant_id = principal.tenant_id
        if user_admin_dao.username_exists(db, tenant_id, req.username):
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"登录名已存在：{req.username}",
                {
                    "fields": [
                        {"field": "username", "message": "登录名已存在", "type": "value_error"}
                    ]
                },
            )

        if req.org_id is not None:
            UserAdminService._assert_dept_exists(db, tenant_id, int(req.org_id))
        if req.position_id is not None:
            UserAdminService._assert_position_exists(db, tenant_id, int(req.position_id))

        roles = UserAdminService._resolve_roles(db, tenant_id, req.role_ids)

        password = req.password or _gen_password()
        if len(password) < PASSWORD_MIN_LENGTH:  # 兜底：Schema 已校验，防御性再查一次
            raise BizError(ErrorCode.PARAM_INVALID, f"密码长度不得少于 {PASSWORD_MIN_LENGTH} 位")

        user = SysUser(
            id=next_id(),
            tenant_id=tenant_id,
            username=req.username,
            password_hash=SecurityUtils.hash_password(password),
            real_name=req.real_name,
            org_id=req.org_id,
            position_id=req.position_id,
            phone_enc=_encrypt_or_fail(req.phone, "手机号"),
            email_enc=_encrypt_or_fail(req.email, "邮箱"),
            status=req.status,
            login_fail_count=0,
            is_deleted=0,
        )
        user_admin_dao.insert_user(db, user)
        if roles:
            user_admin_dao.insert_user_roles(
                db,
                [
                    SysUserRole(tenant_id=tenant_id, user_id=user.id, role_id=int(r.id))
                    for r in roles
                ],
            )
        db.commit()
        logger.info(
            "新建用户 tid=%s user_id=%s username=%s roles=%s by_uid=%s",
            tenant_id,
            user.id,
            user.username,
            [r.role_code for r in roles],
            principal.user_id,
        )
        AuditService.record(
            AuditAction.USER_CREATE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.USER,
            target_id=user.id,
            after={
                **_user_snapshot(user),
                "role_codes": sorted(r.role_code for r in roles),
                # ⚠️ 只记「密码是否为服务端生成」，**绝不记密码本身**
                #    （写入侧还有按键名黑名单的脱敏兜底，这里是第一道自律）
                "password_generated": not bool(req.password),
            },
        )

        org_names = {int(d.id): d.dept_name for d in org_dao.list_depts(db, tenant_id)}
        position_names = {
            int(p.id): p.position_name for p in org_dao.list_positions(db, tenant_id)
        }
        out = UserAdminService._to_out(
            user, org_names=org_names, position_names=position_names, roles=roles
        )
        # 🔴 仅当密码是服务端生成时才回显 —— 管理员自填的密码没必要（也不该）再抄一遍
        return UserCreateOut(user=out, initial_password=None if req.password else password)

    # ------------------------------------------------------------------
    # API-15 修改用户
    # ------------------------------------------------------------------
    @staticmethod
    def update_user(
        db: Session, principal: CurrentPrincipal, user_id: int, req: UserUpdateRequest
    ) -> AdminUserOut:
        """修改用户资料 / 组织 / 岗位 / 联系方式 / 启停。

        :raises NotFoundError: 用户 / 组织 / 岗位不存在（40401）
        :raises IllegalStateError(42201): 禁用自己，或禁用了最后一名可用超管
        """
        tenant_id = principal.tenant_id
        user = user_admin_dao.get_user_by_id(db, tenant_id, user_id)
        if user is None:
            raise NotFoundError("用户不存在")

        # ⚠️ 先取快照：下面的 update_user_fields 会就地改 user 的属性，
        #    事后再取就只剩「改后值」，审计会退化成两条相同记录。
        before_user = _user_snapshot(user)

        fields = req.model_fields_set
        values: dict[str, object] = {}
        scope_changed = False

        if "real_name" in fields:
            values["real_name"] = req.real_name

        if "org_id" in fields:
            if req.org_id is not None:
                UserAdminService._assert_dept_exists(db, tenant_id, int(req.org_id))
            if req.org_id != user.org_id:
                scope_changed = True
            values["org_id"] = req.org_id

        if "position_id" in fields:
            if req.position_id is not None:
                UserAdminService._assert_position_exists(db, tenant_id, int(req.position_id))
            values["position_id"] = req.position_id

        # ⚠️ 「不传该字段=不改动」与「显式传 null=清空」靠 model_fields_set 区分
        if "phone" in fields:
            values["phone_enc"] = _encrypt_or_fail(req.phone, "手机号")
        if "email" in fields:
            values["email_enc"] = _encrypt_or_fail(req.email, "邮箱")

        status_changed = False
        if "status" in fields and req.status is not None and int(req.status) != int(user.status):
            if int(req.status) == int(SysUserStatus.DISABLED):
                UserAdminService._assert_can_disable(db, tenant_id, principal, user)
            values["status"] = int(req.status)
            status_changed = True
            if int(req.status) == int(SysUserStatus.ENABLED):
                # 启用即解锁：管理员「重新启用」的意图通常就是「这号能用了」，
                # 若不清零失败计数，用户下次登录仍会被 40103 挡住，像没生效一样。
                values["login_fail_count"] = 0
                values["locked_until"] = None

        if not values:
            return UserAdminService._single_out(db, tenant_id, user)

        user_admin_dao.update_user_fields(db, tenant_id, user.id, **values)
        db.commit()

        # 组织或启停变化会改变该用户的行级可见性 → 失效快照 + 递增**用户级**版本号
        if scope_changed or status_changed:
            UserAdminService._invalidate_user(db, tenant_id, user.id)

        refreshed = user_admin_dao.get_user_by_id(db, tenant_id, user.id)
        logger.info(
            "修改用户 tid=%s user_id=%s fields=%s by_uid=%s",
            tenant_id,
            user.id,
            sorted(values),
            principal.user_id,
        )
        actor = refreshed or user
        AuditService.record(
            AuditAction.USER_UPDATE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.USER,
            target_id=actor.id,
            before=before_user,
            after={
                **_user_snapshot(actor),
                # 只记「本次实际改动的列名」：让审计一眼看出是改组织还是改状态，
                # 而不用逐字段比对（快照里其它字段值相同，比对是纯粹的阅读负担）
                "changed_fields": sorted(values),
            },
        )
        return UserAdminService._single_out(db, tenant_id, actor)

    # ------------------------------------------------------------------
    # API-16 分配角色（全量替换）
    # ------------------------------------------------------------------
    @staticmethod
    def assign_roles(
        db: Session, principal: CurrentPrincipal, user_id: int, req: AssignRolesRequest
    ) -> UserRolesOut:
        """把用户的角色集合**全量替换**为 ``req.role_ids``。

        事务内两步：校验角色合法 → ``DELETE`` 旧绑定 + ``INSERT`` 新绑定；
        提交后再失效快照并递增**用户级**权限版本号。

        :raises NotFoundError: 用户不存在（40401）
        :raises BizError(40001): 存在不属于本租户或已失效的角色
        :raises IllegalStateError(42201): 改自己的角色，或摘掉最后一名可用超管的 ``ADM``
        """
        tenant_id = principal.tenant_id
        user = user_admin_dao.get_user_by_id(db, tenant_id, user_id)
        if user is None:
            raise NotFoundError("用户不存在")

        # 🔴 禁止修改自己的角色：这是最容易被自己触发的自锁。
        #    管理员想调整权限时顺手点到自己那一行，一次点击就能把自己降权到登不进后台。
        if int(user.id) == int(principal.user_id):
            raise IllegalStateError("不能修改自己的角色，请由其他管理员操作")

        requested = list(dict.fromkeys(int(r) for r in req.role_ids))
        roles = UserAdminService._resolve_roles(db, tenant_id, requested)
        new_codes = {r.role_code.upper() for r in roles}

        # ⚠️ 先读改前角色集合：下面是**全量替换**（DELETE 旧绑定），读晚了就永久丢失。
        before_codes = sorted(
            r.role_code for r in user_dao.list_roles_by_user(db, tenant_id, int(user.id))
        )

        # 若本次会摘掉该用户的 ADM，先确认「摘完之后系统还有人能管」
        if "ADM" not in new_codes and user_admin_dao.user_has_role_code(
            db, tenant_id, int(user.id), "ADM"
        ):
            UserAdminService._assert_other_admin_exists(db, tenant_id, int(user.id))

        user_admin_dao.delete_user_roles(db, tenant_id, int(user.id))
        user_admin_dao.insert_user_roles(
            db,
            [
                SysUserRole(tenant_id=tenant_id, user_id=int(user.id), role_id=int(r.id))
                for r in roles
            ],
        )
        db.commit()
        logger.info(
            "分配角色 tid=%s user_id=%s roles=%s by_uid=%s",
            tenant_id,
            user.id,
            sorted(new_codes),
            principal.user_id,
        )

        version = UserAdminService._invalidate_user(db, tenant_id, int(user.id))
        # 🔴 角色分配直接决定权限面，是审计里与「角色权限绑定」同等重要的一类记录：
        #    除前后集合外还记 granted/revoked 差集，回答「这次到底给了谁什么权限」。
        AuditService.record(
            AuditAction.USER_ROLE_ASSIGN,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.USER,
            target_id=user.id,
            before={"username": user.username, "role_codes": before_codes},
            after={
                "username": user.username,
                "role_codes": sorted(new_codes),
                "granted": sorted(new_codes - set(before_codes)),
                "revoked": sorted(set(before_codes) - new_codes),
            },
        )
        return UserRolesOut(
            user_id=user.id,
            role_ids=[int(r.id) for r in roles],
            role_codes=sorted(new_codes),
            perm_version=version,
        )

    # ------------------------------------------------------------------
    # API-17 批量导入用户（Excel / CSV 模板）
    # ------------------------------------------------------------------
    @staticmethod
    def import_users(
        db: Session, principal: CurrentPrincipal, *, filename: str, content: bytes
    ) -> UserImportOut:
        """按模板批量建号。**逐行独立提交**（单行失败不影响其他行）。

        :raises BizError(41501): 扩展名不支持，或环境缺少 ``openpyxl`` 且传的是 ``.xlsx``
        :raises BizError(41301): 文件超限
        """
        tenant_id = principal.tenant_id
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in _EXCEL_EXT | _CSV_EXT:
            raise BizError(
                ErrorCode.UNSUPPORTED_MEDIA,
                "仅支持 .xlsx / .xlsm / .csv 模板",
                {"supported": sorted(_EXCEL_EXT | _CSV_EXT)},
            )
        if len(content) > settings.upload_max_file_size_bytes:
            raise BizError(
                ErrorCode.UPLOAD_TOO_LARGE,
                f"文件超过 {settings.UPLOAD_MAX_FILE_SIZE_MB}MB 上限",
            )

        raw_rows = UserAdminService._read_rows(ext, content)

        # ---- 预取字典：组织/岗位/角色各查一次，避免逐行查库 ----
        dept_by_code = {
            d.dept_code: d for d in org_dao.list_depts(db, tenant_id, enabled_only=False)
        }
        position_by_code = {
            p.position_code: p for p in org_dao.list_positions(db, tenant_id)
        }
        role_by_code = {
            r.role_code.upper(): r for r in rbac_dao.list_roles(db, tenant_id)
        }

        errors: list[UserImportError] = []
        success = 0
        seen: set[str] = set()

        for row_no, record in raw_rows:
            username = (record.get("username") or "").strip()
            try:
                if not username:
                    raise ValueError("登录名为空")
                if len(username) < 3:
                    raise ValueError("登录名长度不足 3 位")
                if username in seen:
                    raise ValueError("文件内登录名重复")
                if user_admin_dao.username_exists(db, tenant_id, username):
                    raise ValueError("登录名已存在")

                org_id = None
                org_code = (record.get("org_code") or "").strip()
                if org_code:
                    dept = dept_by_code.get(org_code)
                    if dept is None:
                        raise ValueError(f"组织编码不存在：{org_code}")
                    org_id = int(dept.id)

                position_id = None
                pos_code = (record.get("position_code") or "").strip()
                if pos_code:
                    pos = position_by_code.get(pos_code)
                    if pos is None:
                        raise ValueError(f"岗位编码不存在：{pos_code}")
                    position_id = int(pos.id)

                role_ids: list[int] = []
                codes_raw = (record.get("role_codes") or "").strip()
                if codes_raw:
                    for code in [c.strip() for c in codes_raw.replace("，", ",").split(",") if c.strip()]:
                        role = role_by_code.get(code.upper())
                        if role is None:
                            raise ValueError(f"角色码不存在：{code}")
                        role_ids.append(int(role.id))

                password = _gen_password()

                # 🔴 SAVEPOINT 逐行隔离：本行失败只回滚本行，已成功的行照常留在批次里
                #    （F-3.1「单条失败不阻塞整批」的同一取舍）
                with db.begin_nested():
                    user = SysUser(
                        id=next_id(),
                        tenant_id=tenant_id,
                        username=username,
                        password_hash=SecurityUtils.hash_password(password),
                        real_name=(record.get("real_name") or "").strip() or None,
                        org_id=org_id,
                        position_id=position_id,
                        phone_enc=_encrypt_or_fail(record.get("phone"), "手机号"),
                        email_enc=_encrypt_or_fail(record.get("email"), "邮箱"),
                        status=int(SysUserStatus.ENABLED),
                        login_fail_count=0,
                        is_deleted=0,
                    )
                    db.add(user)
                    db.flush()
                    if role_ids:
                        user_admin_dao.insert_user_roles(
                            db,
                            [
                                SysUserRole(
                                    tenant_id=tenant_id, user_id=user.id, role_id=rid
                                )
                                for rid in role_ids
                            ],
                        )
                seen.add(username)
                success += 1
            except Exception as exc:  # noqa: BLE001 — 单行失败必须被收集，不能中断整批
                errors.append(
                    UserImportError(row=row_no, username=username or None, reason=str(exc))
                )
                logger.info("导入行失败 row=%s username=%s err=%s", row_no, username, exc)

        db.commit()
        logger.info(
            "批量导入用户 tid=%s total=%s success=%s failed=%s by_uid=%s",
            tenant_id,
            len(raw_rows),
            success,
            len(errors),
            principal.user_id,
        )
        # 批量导入是**一次管理员操作产生 N 个账号**，故写**一条**汇总审计
        # 而不是 N 条建号记录：前者能回答「这笔操作是谁做的、影响到什么范围」，
        # 后者会把审计列表刷爆且丢失「同一批」这个信息。
        # 失败行只记行号，**不记原始单元格内容**（可能含手机/邮箱等 PII）。
        AuditService.record(
            AuditAction.USER_IMPORT,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.USER,
            target_id=None,
            after={
                "filename": filename,
                "total": len(raw_rows),
                "success": success,
                "failed": len(errors),
                "error_rows": [e.row for e in errors[:50]],
            },
        )
        return UserImportOut(
            total=len(raw_rows), success=success, failed=len(errors), errors=errors
        )

    # ------------------------------------------------------------------
    # 内部：表格解析
    # ------------------------------------------------------------------
    @staticmethod
    def _read_rows(ext: str, content: bytes) -> list[tuple[int, dict[str, str]]]:
        """把模板解析成 ``[(行号, {字段: 值})]``（行号含表头，从 1 起，便于管理员定位）。"""
        if ext in _CSV_EXT:
            return UserAdminService._read_csv(content)
        return UserAdminService._read_excel(content)

    @staticmethod
    def _read_csv(content: bytes) -> list[tuple[int, dict[str, str]]]:
        """读 CSV。``utf-8-sig`` 优先（Excel 导出的 CSV 带 BOM），回退 GBK。"""
        text: str | None = None
        for encoding in ("utf-8-sig", "gbk"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise BizError(ErrorCode.UNSUPPORTED_MEDIA, "CSV 编码无法识别，请另存为 UTF-8")

        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            raise BizError(ErrorCode.PARAM_INVALID, "模板为空")
        header = [_HEADER_ALIASES.get((c or "").strip().lower(), (c or "").strip().lower()) for c in rows[0]]
        if "username" not in header:
            raise BizError(ErrorCode.PARAM_INVALID, "模板缺少「登录名」列")
        return UserAdminService._zip_rows(header, rows[1:], base_row=2)

    @staticmethod
    def _read_excel(content: bytes) -> list[tuple[int, dict[str, str]]]:
        """读 xlsx（``openpyxl``，只读模式 + 值模式）。"""
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover — 取决于部署环境
            raise BizError(
                ErrorCode.UNSUPPORTED_MEDIA,
                "当前服务未安装 Excel 解析依赖（openpyxl），请改用 .csv 模板导入",
            ) from exc

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            sheet = wb.active
            if sheet is None:
                raise BizError(ErrorCode.PARAM_INVALID, "模板没有工作表")
            raw = [[("" if c is None else str(c)) for c in row] for row in sheet.iter_rows(values_only=True)]
        finally:
            wb.close()

        if not raw:
            raise BizError(ErrorCode.PARAM_INVALID, "模板为空")
        header = [_HEADER_ALIASES.get((c or "").strip().lower(), (c or "").strip().lower()) for c in raw[0]]
        if "username" not in header:
            raise BizError(ErrorCode.PARAM_INVALID, "模板缺少「登录名」列")
        return UserAdminService._zip_rows(header, raw[1:], base_row=2)

    @staticmethod
    def _zip_rows(
        header: Sequence[str], body: Sequence[Sequence[str]], *, base_row: int
    ) -> list[tuple[int, dict[str, str]]]:
        """表头 + 数据行 → ``[(行号, 记录)]``。

        - **跳过全空行**，且**不把它们计入 total**（否则统计数字会让管理员困惑）；
        - **未知列丢弃**（只保留 :data:`_TEMPLATE_FIELDS` 里的列）——
          模板里常有「工号」「备注」这类本功能不处理的列，丢弃比原样带下去更省心；
        - **重复列只取第一个**（Excel 里两个「姓名」列时行为可预期）。
        """
        out: list[tuple[int, dict[str, str]]] = []
        for offset, row in enumerate(body):
            cells = [str(c) if c is not None else "" for c in row]
            if not any(c.strip() for c in cells):
                continue
            record: dict[str, str] = {}
            for idx, name in enumerate(header):
                if name not in _TEMPLATE_FIELDS or name in record:
                    continue
                record[name] = cells[idx] if idx < len(cells) else ""
            out.append((base_row + offset, record))
        return out

    # ------------------------------------------------------------------
    # 内部：校验与转换
    # ------------------------------------------------------------------
    @staticmethod
    def _assert_dept_exists(db: Session, tenant_id: int, dept_id: int) -> None:
        dept = org_dao.get_dept_by_id(db, tenant_id, dept_id)
        if dept is None or dept.is_deleted:
            raise NotFoundError("组织不存在")

    @staticmethod
    def _assert_position_exists(db: Session, tenant_id: int, position_id: int) -> None:
        if org_dao.get_position_by_id(db, tenant_id, position_id) is None:
            raise NotFoundError("岗位不存在")

    @staticmethod
    def _resolve_roles(db: Session, tenant_id: int, role_ids: Sequence[int]) -> list[SysRole]:
        """批量校验角色 ID 属本租户且未软删。

        :raises BizError(40001): 存在无效角色 ID（防跨租户注入角色）
        """
        requested = list(dict.fromkeys(int(r) for r in role_ids))
        if not requested:
            return []
        found = user_admin_dao.list_roles_by_ids(db, tenant_id, requested)
        found_ids = {int(r.id) for r in found}
        invalid = [r for r in requested if r not in found_ids]
        if invalid:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "存在不属于本租户或已失效的角色",
                {
                    "fields": [
                        {
                            "field": "role_ids",
                            "message": f"无效角色 ID：{', '.join(str(i) for i in invalid)}",
                            "type": "value_error",
                        }
                    ]
                },
            )
        return found

    @staticmethod
    def _assert_other_admin_exists(db: Session, tenant_id: int, user_id: int) -> None:
        """确认「把 ``user_id`` 排除后仍有人能管」—— 否则拒绝操作。

        这是 :meth:`update_user`（禁用）与 :meth:`assign_roles`（摘 ADM）共用的守卫。
        """
        if user_admin_dao.count_enabled_admins(db, tenant_id, exclude_user_id=user_id) == 0:
            raise IllegalStateError(
                "系统必须保留至少一名启用的超级管理员，该操作会使后台无人可管"
            )

    @staticmethod
    def _assert_can_disable(
        db: Session, tenant_id: int, principal: CurrentPrincipal, user: SysUser
    ) -> None:
        """禁用前的自锁检查：不能禁用自己；也不能禁掉最后一名可用超管。

        ⚠️ 只对**当前就是启用超管**的账号做「最后一名」判定 ——
        否则在一个本来就没有超管的异常环境里，禁用一个普通账号也会被误拦。
        """
        if int(user.id) == int(principal.user_id):
            raise IllegalStateError("不能禁用自己的账号")
        is_admin = user_admin_dao.user_has_role_code(db, tenant_id, int(user.id), "ADM")
        if is_admin and int(user.status) == int(SysUserStatus.ENABLED):
            UserAdminService._assert_other_admin_exists(db, tenant_id, int(user.id))

    @staticmethod
    def _invalidate_user(db: Session, tenant_id: int, user_id: int) -> int:
        """失效该用户的权限快照 + 递增**用户级**权限版本号，返回新版本号。

        ⚠️ 与 CU-05 一样**失败不抛出**：缓存只是加速手段，数据库已提交的事实才是准据；
        抛异常会把一次成功的改号变成 500。
        """
        AuthService.invalidate_perm_snapshot(tenant_id, user_id)
        try:
            return rbac_dao.bump_user_perm_version(tenant_id, user_id)
        except Exception as exc:  # noqa: BLE001 — Redis 不可用不得阻断主流程
            logger.warning("用户级权限版本号递增失败 uid=%s err=%s", user_id, exc)
            return 0

    @staticmethod
    def _to_out(
        user: SysUser,
        *,
        org_names: dict[int, str],
        position_names: dict[int, str],
        roles: Sequence[SysRole],
    ) -> AdminUserOut:
        """实体 → 视图。**手机/邮箱在此脱敏**，明文不出本函数。"""
        return AdminUserOut(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            org_id=user.org_id,
            org_name=org_names.get(int(user.org_id)) if user.org_id else None,
            position_id=user.position_id,
            position_name=position_names.get(int(user.position_id)) if user.position_id else None,
            phone=crypto.mask_encrypted_phone(user.phone_enc),
            email=crypto.mask_encrypted_email(user.email_enc),
            status=int(user.status),
            status_name=label_of(SysUserStatus, user.status),
            role_ids=[int(r.id) for r in roles],
            roles=[r.role_code for r in roles],
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )

    @staticmethod
    def _single_out(db: Session, tenant_id: int, user: SysUser) -> AdminUserOut:
        """单条视图（改号/建号后回显用）。"""
        org_names = {int(d.id): d.dept_name for d in org_dao.list_depts(db, tenant_id)}
        position_names = {
            int(p.id): p.position_name for p in org_dao.list_positions(db, tenant_id)
        }
        role_map = user_admin_dao.list_role_map_by_users(db, tenant_id, [int(user.id)])
        return UserAdminService._to_out(
            user,
            org_names=org_names,
            position_names=position_names,
            roles=role_map.get(int(user.id), []),
        )


__all__ = ["UserAdminService"]
