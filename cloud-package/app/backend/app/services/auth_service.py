"""认证与会话业务编排（F-1.2 / F-1.3，接口 API-01 ~ API-06）。

分层位置：``routers → **services** → dao``。本模块是**唯一**可以同时用
「数据库事务 + Redis 计数 + 安全工具」的地方 —— DAO 不得含判定，Router 不得含业务。

关键业务规则（逐条对应需求/接口）
--------------------------------
| 规则 | 出处 | 落点 |
| --- | --- | --- |
| 连续 5 次密码错锁 10 分钟 | F-1.2 / API-01 备注 | :meth:`AuthService.login`（MySQL 权威 + Redis 快速计数） |
| 权限数据只从 JWT 读 | 接口 §1.3 红线 | :meth:`AuthService.build_principal` |
| 刷新 Token **不含权限 claim**，续期必须回查 | 安全设计 | :meth:`AuthService.refresh` |
| 改密后旧会话全部失效 | F-1.2 | :meth:`AuthService.change_password` |
| 越权/不存在统一 40301 | 接口 §1.7 | 见 CU-05，本模块只做「本人」校验 |
| 密码明文永不入日志 | API-01 备注 | 全程不打印请求体；审计只写 username |

⚠️ **遗留项（已登记，不属本单元遗漏）**：

- 登录成功/失败写审计日志（TBL-28）**已由 CU-08 落地**：:meth:`AuthService._audit`
  现走 :class:`app.services.audit_service.AuditService`（独立事务 + fail-open），
  并保留原结构化日志。
- ~~部门子树解析（``data_scope=2`` 用）由 **CU-05** 提供~~ → **CU-05 已落地**：
  :meth:`AuthService.build_principal` 现经 :class:`app.services.org_scope_service.OrgScopeService`
  回填 ``CurrentPrincipal.dept_ids``；解析失败时仍**锁死为不可见**（安全优先）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import incr_counter, login_fail_key, reset_counter
from app.core.security import TOKEN_TYPE_REFRESH, SecurityUtils, assert_token_alive
from app.core.tenant import CurrentPrincipal
from app.dao import rbac_dao, token_dao, user_dao
from app.dao.redis_client import KEY_SESSION, build_key, get_redis
from app.exceptions import (
    AccountLockedError,
    BadCredentialsError,
    BizError,
    ErrorCode,
    UnauthenticatedError,
)
from app.models.enums import RoleDataScope, SysUserStatus, label_of
from app.models.tenant import Tenant
from app.models.user import SysUser
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.auth import (
    ChangePasswordOut,
    ChangePasswordRequest,
    LoginOut,
    LoginRequest,
    LogoutOut,
    LogoutRequest,
    PermissionsOut,
    RefreshOut,
    RefreshRequest,
    UserInfoOut,
)
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)

#: 本模块的 ``_audit`` 用的是「动作 + 结果」两段式（如 ``login`` + ``fail``），
#: 而审计表的 ``action`` 是**单值**受控词表 —— 本映射负责把前者翻成后者。
#: ⚠️ 用 ``.get(action, action)`` 兜底而不是严格校验：将来新增调用点时
#: 即便忘了登记映射，也只会写入一个「原样动作码」（仍可读、可查），
#: 而不是抛异常 —— 审计写入路径**不允许因为词表没更新就失败**。
_AUDIT_ACTIONS: dict[str, str] = {
    "login": AuditAction.LOGIN,
    "logout": AuditAction.LOGOUT,
    "change_password": AuditAction.CHANGE_PASSWORD,
}

#: 权限快照缓存键前缀（``sess:perm:<tid>:<uid>``）
_KEY_PERM = "perm"

#: 用户名不存在时用于「假比对」的 bcrypt 哈希 —— 消除「无此账号」与「密码错」的响应时间差
#: （惰性构造：进程内只算一次）
_DUMMY_HASH: str = ""


def _dummy_hash() -> str:
    global _DUMMY_HASH  # noqa: PLW0603 — 进程内一次性惰性初始化
    if not _DUMMY_HASH:
        _DUMMY_HASH = SecurityUtils.hash_password("dummy-password-for-timing-equalization")
    return _DUMMY_HASH


def _perm_cache_key(tenant_id: int, user_id: int) -> str:
    return build_key(KEY_SESSION, _KEY_PERM, tenant_id, user_id)


class AuthService:
    """认证与权限快照。**全部为无状态静态方法**（会话由 :func:`app.dao.session.get_db` 注入）。"""

    # ==================================================================
    # API-01 登录
    # ==================================================================
    @staticmethod
    def login(db: Session, req: LoginRequest, *, client_ip: str | None = None) -> LoginOut:
        """账号密码登录。

        :raises BadCredentialsError: 账号或密码错误（40102，**不区分**账号不存在与密码错）
        :raises AccountLockedError: 账号被锁定（40103，返回剩余秒数）
        """
        tenant = AuthService._resolve_tenant(db, req.tenant_code)
        user = user_dao.get_user_by_username(db, tenant.id, req.username)
        now = datetime.now()

        # ① 锁定判定：**MySQL ``locked_until`` 为权威**（Redis 只是快速计数，丢了也不影响安全）
        if user is not None and user.locked_until is not None and user.locked_until > now:
            remaining = int((user.locked_until - now).total_seconds())
            AuthService._audit("login", "lock", user, client_ip, detail={"locked_seconds": remaining})
            raise AccountLockedError(max(1, remaining))

        # ② 口令校验（含租户/状态判定）
        password_ok = SecurityUtils.verify_password(
            req.password, user.password_hash if user is not None else _dummy_hash()
        )
        account_usable = (
            user is not None
            and user.status == int(SysUserStatus.ENABLED)
            and user.is_deleted == 0
        )

        if not (password_ok and account_usable):
            AuthService._on_login_failed(db, tenant, user, req.username, client_ip)
            raise BadCredentialsError()

        # ③ 成功：清计数、记时间、签 Token
        assert user is not None  # 上分支已保证；显式收窄供类型检查
        reset_counter(login_fail_key(tenant.id, req.username))
        user_dao.update_user_fields(
            db,
            user.id,
            tenant.id,
            login_fail_count=0,
            locked_until=None,
            last_login_at=now,
        )
        db.commit()

        roles = user_dao.list_roles_by_user(db, tenant.id, user.id)
        role_codes = [r.role_code for r in roles]
        data_scope = AuthService._resolve_data_scope(roles)
        org_name = AuthService._org_name(db, user)

        access_token = SecurityUtils.create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            org_id=user.org_id,
            roles=role_codes,
            data_scope=data_scope,
            token_version=token_dao.get_token_version(tenant.id, user.id),
        )
        refresh_token = SecurityUtils.create_refresh_token(
            user_id=user.id,
            tenant_id=tenant.id,
            token_version=token_dao.get_token_version(tenant.id, user.id),
        )

        AuthService._cache_perm_snapshot(db, tenant.id, user.id, role_codes)

        AuthService._audit("login", "success", user, client_ip)

        return LoginOut(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            user=AuthService._to_user_info(user, role_codes, data_scope, org_name),
        )

    # ==================================================================
    # API-02 刷新
    # ==================================================================
    @staticmethod
    def refresh(db: Session, req: RefreshRequest) -> RefreshOut:
        """用 refresh_token 换新 access_token。

        🔴 refresh_token **不含 ``rls`` / ``dsc``**，因此必须**回查数据库**重新取角色与数据域 ——
        否则管理员降级后，用户可凭旧 refresh_token 无限续期，拿到已失效的高权限。
        """
        payload = SecurityUtils.decode_token(req.refresh_token, expect_type=TOKEN_TYPE_REFRESH)
        assert_token_alive(payload)  # 已登出/改密的 refresh_token 同样失效
        user_id = int(payload["sub"])
        tenant_id = int(payload["tid"])

        user = user_dao.get_user_by_id(db, tenant_id, user_id)
        if user is None or user.status != int(SysUserStatus.ENABLED):
            raise UnauthenticatedError("账号不可用，请重新登录")

        roles = user_dao.list_roles_by_user(db, tenant_id, user.id)
        role_codes = [r.role_code for r in roles]
        access_token = SecurityUtils.create_access_token(
            user_id=user.id,
            tenant_id=tenant_id,
            org_id=user.org_id,
            roles=role_codes,
            data_scope=AuthService._resolve_data_scope(roles),
            token_version=token_dao.get_token_version(tenant_id, user.id),
        )
        AuthService._cache_perm_snapshot(db, tenant_id, user.id, role_codes)
        return RefreshOut(
            access_token=access_token, expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS
        )

    # ==================================================================
    # API-03 登出
    # ==================================================================
    @staticmethod
    def logout(
        db: Session, principal: CurrentPrincipal, req: LogoutRequest, *, access_payload: dict
    ) -> LogoutOut:
        """吊销当前会话（可选连 refresh_token 一并吊销 / 全端注销）。

        :param access_payload: 当前 access_token 的原始 claim（Router 从 Token 解出后传入），
            用于取 ``jti`` 与剩余有效期
        """
        now = token_dao.now_epoch()

        if req.all_devices:
            token_dao.bump_token_version(
                principal.tenant_id, principal.user_id, settings.REFRESH_TOKEN_EXPIRE_SECONDS
            )
        else:
            # 当前 access_token
            exp = int(access_payload.get("exp") or 0)
            token_dao.revoke_token(access_payload.get("jti"), max(0, exp - now))

        # refresh_token 一并吊销（无论是否全端）
        if req.refresh_token:
            try:
                rp = SecurityUtils.decode_token(req.refresh_token, expect_type=TOKEN_TYPE_REFRESH)
                token_dao.revoke_token(rp.get("jti"), max(0, int(rp.get("exp") or 0) - now))
            except BizError:
                # 已过期 / 非法 refresh_token：无从吊销，但**不得阻断登出**（登出必须幂等成功）
                logger.info("登出时 refresh_token 不可解析，跳过吊销 uid=%s", principal.user_id)

        AuthService._clear_perm_snapshot(principal.tenant_id, principal.user_id)
        # ⚠️ 这里 user 是 None（登出只需要 uid），必须显式传租户才能落库
        AuthService._audit(
            "logout",
            "success",
            None,
            None,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
        return LogoutOut(revoked=True, all_devices=bool(req.all_devices))

    # ==================================================================
    # API-04 当前用户
    # ==================================================================
    @staticmethod
    def me(db: Session, principal: CurrentPrincipal) -> UserInfoOut:
        """返回当前用户信息与数据域。

        🔴 **以数据库为准**，不直接回显 JWT claim —— 否则改角色后需等 Token 过期才生效。
        """
        user = user_dao.get_user_by_id(db, principal.tenant_id, principal.user_id)
        if user is None or user.status != int(SysUserStatus.ENABLED):
            raise UnauthenticatedError("账号不可用，请重新登录")

        roles = user_dao.list_roles_by_user(db, user.tenant_id, user.id)
        role_codes = [r.role_code for r in roles]
        return AuthService._to_user_info(
            user,
            role_codes,
            AuthService._resolve_data_scope(roles),
            AuthService._org_name(db, user),
        )

    # ==================================================================
    # API-05 权限快照
    # ==================================================================
    @staticmethod
    def permissions(db: Session, principal: CurrentPrincipal) -> PermissionsOut:
        """按钮级权限快照。``ADM`` 视为拥有本租户全部启用权限点。"""
        codes = AuthService.get_perm_codes(db, principal)
        # ⚠️ 版本号自 CU-05 起改为纯 Redis 计数（只增不减），**不再需要 db 会话**
        #    —— 早期实现把它写成 `_perm_version(db, tid, uid)`，签名瘦身后调用点漏改，
        #    表现为「只要数据库可用、走到 API-05 就 500」。此处为唯一调用点。
        perm_version = AuthService._perm_version(principal.tenant_id, principal.user_id)
        return PermissionsOut(
            perm_codes=sorted(codes),
            roles=list(principal.roles),
            is_admin=principal.is_admin,
            perm_version=perm_version,
        )

    # ==================================================================
    # API-06 修改本人密码
    # ==================================================================
    @staticmethod
    def change_password(
        db: Session, principal: CurrentPrincipal, req: ChangePasswordRequest
    ) -> ChangePasswordOut:
        """修改本人密码。

        :raises BadCredentialsError: 原密码错误（40102，避免泄露「原密码是否正确」以外的信息）
        :raises BizError: 新密码与旧密码相同（40001）
        """
        user = user_dao.get_user_by_id(db, principal.tenant_id, principal.user_id)
        if user is None:
            raise UnauthenticatedError("账号不可用，请重新登录")

        if not SecurityUtils.verify_password(req.old_password, user.password_hash):
            raise BadCredentialsError("原密码错误")

        if req.old_password == req.new_password:
            raise BizError(ErrorCode.PARAM_INVALID, "新密码不能与原密码相同", {"fields": [
                {"field": "new_password", "message": "新密码不能与原密码相同", "type": "value_error"}
            ]})

        user_dao.update_user_fields(
            db, user.id, principal.tenant_id, password_hash=SecurityUtils.hash_password(req.new_password)
        )
        db.commit()

        # 🔴 改密后旧会话全部作废（F-1.2）：递增会话版本，使此前的 Token 一律 40101
        # （用版本号而非时间戳 —— JWT 的 iat 只有秒级精度，同秒会误判）
        token_dao.bump_token_version(
            principal.tenant_id, principal.user_id, settings.REFRESH_TOKEN_EXPIRE_SECONDS
        )
        AuthService._clear_perm_snapshot(principal.tenant_id, principal.user_id)
        AuthService._audit("change_password", "success", user, None)
        return ChangePasswordOut(changed=True, relogin_required=True)

    # ==================================================================
    # 权限快照读取（供 :func:`app.core.security.require_perm` 调用）
    # ==================================================================
    @staticmethod
    def get_perm_codes(db: Session, principal: CurrentPrincipal) -> set[str]:
        """取权限码集合：**Redis 快照优先，未命中回查数据库**。

        🔴 取不到时**返回空集**（调用方 ``require_perm`` 因此 fail-closed 拒绝），
        ⚠️ 绝不可返回「全量」—— 那等于把权限校验变成放行。

        🔴 **数据库异常也必须返回空集**（而非上抛）：若让异常冒泡，
        ``require_perm`` 会被全局处理器兜成 **50000**，而 50000 的语义是
        「服务内部错误，可重试」—— 前端与运维会把它当成故障重试，
        而不是「你没权限」。**fail-closed 的正确表现是 40301**：
        查不到权限认定 = 无权限，与「未解析子树时锁死不可见」同一思路。
        """
        cached = AuthService._read_perm_snapshot(principal.tenant_id, principal.user_id)
        if cached is not None:
            return cached

        try:
            if principal.is_admin:
                codes = user_dao.list_all_perm_codes(db, principal.tenant_id)
            else:
                codes = user_dao.list_perm_codes_by_user(db, principal.tenant_id, principal.user_id)
        except Exception as exc:  # noqa: BLE001 — 权限读取失败一律 fail-closed（返回空集）
            logger.warning(
                "权限码读取失败（fail-closed，按无权限处理）tid=%s uid=%s err=%s",
                principal.tenant_id,
                principal.user_id,
                exc,
            )
            return set()

        AuthService._write_perm_snapshot(principal.tenant_id, principal.user_id, codes)
        return codes

    # ==================================================================
    # 内部：租户 / 角色 / 数据域
    # ==================================================================
    @staticmethod
    def _resolve_tenant(db: Session, tenant_code: str | None) -> Tenant:
        """解析租户。一期单租户：``tenant_code`` 留空取默认租户。

        🔴 租户不存在时抛 **40102 而非 40401** —— 不向未认证请求暴露租户是否存在。
        """
        tenant = (
            user_dao.get_tenant_by_code(db, tenant_code)
            if tenant_code
            else user_dao.get_default_tenant(db)
        )
        if tenant is None:
            logger.warning("登录失败：租户不可用 tenant_code=%s", tenant_code)
            raise BadCredentialsError()
        return tenant

    @staticmethod
    def _resolve_data_scope(roles: list) -> int:
        """多角色时的数据域合并规则：**取最宽**（数值最小 = 范围最大）。

        理由：RBAC 对权限是**并集**语义（拥有 HR 与 MGR 两个角色的人，
        不应因为后者的「本部门」被削成只看本部门）；数据域是权限的派生，
        同样取并集。若后续要求「最小权限优先」，只改这一个函数即可。
        """
        scopes = [int(r.data_scope) for r in roles]
        if not scopes:
            return int(RoleDataScope.SELF)
        return min(scopes)

    @staticmethod
    def _org_name(db: Session, user: SysUser) -> str | None:
        if not user.org_id:
            return None
        org = user_dao.get_org_by_id(db, user.tenant_id, user.org_id)
        return org.dept_name if org is not None else None

    @staticmethod
    def _to_user_info(
        user: SysUser, role_codes: list[str], data_scope: int, org_name: str | None
    ) -> UserInfoOut:
        return UserInfoOut(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            tenant_id=user.tenant_id,
            org_id=user.org_id,
            org_name=org_name,
            roles=role_codes,
            data_scope=data_scope,
            data_scope_name=label_of(RoleDataScope, data_scope),
        )

    # ==================================================================
    # 内部：构建请求主体（供 CU-05 复用的标准入口）
    # ==================================================================
    @staticmethod
    def build_principal(db: Session, user: SysUser) -> CurrentPrincipal:
        """从数据库加载用户 → :class:`CurrentPrincipal`（**权限数据的唯一来源**）。

        CU-05 起：``data_scope=2`` 的**部门子树在此回填**（``dept_ids``），
        使行级过滤与检索前置过滤都能拿到正确的部门集合。
        """
        roles = user_dao.list_roles_by_user(db, user.tenant_id, user.id)
        role_codes = [r.role_code for r in roles]
        data_scope = AuthService._resolve_data_scope(roles)
        return CurrentPrincipal(
            user_id=user.id,
            tenant_id=user.tenant_id,
            org_id=user.org_id,
            roles=tuple(role_codes),
            data_scope=data_scope,
            username=user.username,
            real_name=user.real_name,
            org_name=AuthService._org_name(db, user),
            dept_ids=AuthService._resolve_scope_depts(db, user, data_scope),
        )

    @staticmethod
    def _resolve_scope_depts(db: Session, user: SysUser, data_scope: int) -> tuple[int, ...]:
        """按数据域解析部门子树（CU-05 补齐 CU-04 的遗留）。

        ==================== =========================================================
        ``data_scope``       是否解析 / 原因
        ==================== =========================================================
        ``1 全部``           不解析 —— 不需要部门集合，省一次查询
        ``2 本部门``         **必须** —— 行级过滤按 ``org_id in 子树`` 收窄
        ``3 本人``           解析 —— MySQL 侧虽按「归属人」收窄，但**检索侧**（Milvus）
                            需要一个粗粒度前置过滤，见
                            :meth:`app.core.tenant.DataScopeFilter.milvus_expr`
        ``4 仅统计值``       不解析 —— 明细接口一律 40301，不进明细链路
        ==================== =========================================================

        🔴 **异常时返回空元组**（而非上抛或放宽）：空 ``dept_ids`` 会让
        ``DataScopeFilter.mysql_condition`` 把结果**锁死为不可见**。
        宁可查不到，也不越权 —— 与「未解析子树时 fail-closed」的设计一致。
        """
        if int(data_scope) not in (int(RoleDataScope.OWN_DEPT), int(RoleDataScope.SELF)):
            return ()
        try:
            return OrgScopeService.resolve_dept_ids(db, user.tenant_id, user.org_id)
        except Exception as exc:  # noqa: BLE001 — 解析失败必须降级为「不可见」，不得上抛
            logger.warning(
                "部门子树解析失败（行级范围锁死为不可见）tid=%s uid=%s err=%s",
                user.tenant_id,
                user.id,
                exc,
            )
            return ()

    # ==================================================================
    # 内部：登录失败计数与锁定
    # ==================================================================
    @staticmethod
    def _on_login_failed(
        db: Session, tenant: Tenant, user: SysUser | None, username: str, client_ip: str | None
    ) -> None:
        """失败计数。**MySQL ``login_fail_count`` 为权威**，Redis 仅加速。

        达阈值时置 ``locked_until = now + LOGIN_LOCK_SECONDS`` 并抛 :class:`AccountLockedError`，
        使「第 5 次错误」的响应直接是 40103（与接口 API-01 失败示例一致）。
        """
        if user is None:
            # 账号不存在：仍计 Redis（防用户名爆破），但不写审计的 user 维度信息
            incr_counter(login_fail_key(tenant.id, username), settings.LOGIN_LOCK_SECONDS)
            AuthService._audit(
                "login", "fail", None, client_ip, tenant_id=tenant.id, detail={"username": username}
            )
            return

        mysql_count = int(user.login_fail_count or 0) + 1
        redis_count = incr_counter(login_fail_key(tenant.id, username), settings.LOGIN_LOCK_SECONDS)
        fail_count = max(mysql_count, redis_count or 0)  # 取两者较大值，任一可用即生效

        if fail_count >= settings.LOGIN_MAX_FAIL_COUNT:
            locked_until = datetime.now() + timedelta(seconds=settings.LOGIN_LOCK_SECONDS)
            user_dao.update_user_fields(
                db, user.id, tenant.id, login_fail_count=fail_count, locked_until=locked_until
            )
            db.commit()
            AuthService._audit(
                "login", "lock", user, client_ip, detail={"fail_count": fail_count}
            )
            raise AccountLockedError(settings.LOGIN_LOCK_SECONDS)

        user_dao.update_user_fields(db, user.id, tenant.id, login_fail_count=fail_count)
        db.commit()
        AuthService._audit(
            "login", "fail", user, client_ip, detail={"fail_count": fail_count}
        )

    # ==================================================================
    # 内部：权限快照缓存（Redis，fail-open）
    # ==================================================================
    @staticmethod
    def _read_perm_snapshot(tenant_id: int, user_id: int) -> set[str] | None:
        try:
            raw = get_redis().get(_perm_cache_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001 — Redis 可丢，任何异常都降级
            logger.warning("权限快照读取失败（回落数据库）uid=%s err=%s", user_id, exc)
            return None
        if not raw:
            return None
        try:
            return set(json.loads(raw))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _write_perm_snapshot(tenant_id: int, user_id: int, codes: set[str]) -> None:
        try:
            get_redis().set(
                _perm_cache_key(tenant_id, user_id),
                json.dumps(sorted(codes), ensure_ascii=False),
                ex=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("权限快照写入失败（不影响本次请求）uid=%s err=%s", user_id, exc)

    @staticmethod
    def _cache_perm_snapshot(db: Session, tenant_id: int, user_id: int, role_codes: list[str]) -> None:
        """登录/刷新时重建快照（含 ADM 全量展开）。"""
        try:
            if any(code.upper() == "ADM" for code in role_codes):
                codes = user_dao.list_all_perm_codes(db, tenant_id)
            else:
                codes = user_dao.list_perm_codes_by_user(db, tenant_id, user_id)
        except Exception as exc:  # noqa: BLE001 — 登录**不得**因权限快照读库失败而失败
            logger.warning("重建权限快照失败（登录继续）uid=%s err=%s", user_id, exc)
            return
        AuthService._write_perm_snapshot(tenant_id, user_id, codes)

    @staticmethod
    def _clear_perm_snapshot(tenant_id: int, user_id: int) -> None:
        """清除快照（登出 / 改密 / CU-05 角色权限变更后必须调用）。"""
        try:
            get_redis().delete(_perm_cache_key(tenant_id, user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("权限快照清除失败 uid=%s err=%s", user_id, exc)

    @staticmethod
    def invalidate_perm_snapshot(tenant_id: int, user_id: int) -> None:
        """对外暴露的快照失效入口（CU-05 角色/权限变更后调用）。"""
        AuthService._clear_perm_snapshot(tenant_id, user_id)

    # ==================================================================
    # 内部：权限版本号
    # ==================================================================
    @staticmethod
    def _perm_version(tenant_id: int, user_id: int) -> int:
        """权限快照版本号（CU-05 起为**精确**的 Redis 递增计数）。

        合成规则见 :func:`app.dao.rbac_dao.get_perm_version`：
        ``全局版本（角色↔权限绑定变更）× 1e9 + 用户级版本（用户↔角色变更）``。

        🔴 **为什么不用「角色数 × 1000 + 权限点数」**（CU-04 的临时实现）：
        该值会在两次**恰好互相抵消**的变更后退回原值（例：删 1 个角色 + 加 1 个权限点），
        前端据此以为权限未变、不重新拉取 API-05 —— 这是一次真实的越权窗口。
        版本号只增不减，任何一次变更都必然改变它。

        ⚠️ Redis 不可用时返回 0（可丢数据 DS-3，不得因缓存抖动使接口失败）。
        """
        return rbac_dao.get_perm_version(tenant_id, user_id)

    # ==================================================================
    # 内部：审计（CU-08 已落库 TBL-28）
    # ==================================================================
    @staticmethod
    def _audit(
        action: str,
        result: str,
        user: SysUser | None,
        client_ip: str | None,
        *,
        user_id: int | None = None,
        tenant_id: int | None = None,
        detail: dict | None = None,
    ) -> None:
        """写审计日志（TBL-28 落库 + 保留结构化日志）。

        CU-08 起**真实落库**（此前是 TODO 占位，只输出结构化日志）。

        🔴 **密码、Token 一律不写**（API-01 备注）：只记 username / user_id / IP / 结果。
        写入侧还有第二道防线 —— :func:`app.services.audit_service._sanitize`
        按键名黑名单兜底（防止将来有人把整个请求体丢进 ``detail``）。

        🔴 ``tenant_id`` 为什么是「可选 + 兜底推导」而不是必填：
        本函数的调用点分散在登录各分支，其中**失败分支拿不到 ``user`` 对象**
        （账号不存在时 ``user is None``）—— 那种情况下租户只能由调用方显式传入。
        因此签名保持「可选」，由 ``tenant_id`` → ``user.tenant_id`` 依次推导。

        独立事务与 fail-open 的理由见 :mod:`app.services.audit_service` 模块 docstring
        —— 关键一点：**登录失败分支本身会抛异常**，若审计共用请求 Session，
        记录会随业务回滚被抹掉，而它恰恰是最需要留痕的事件。
        """
        # 保留结构化日志：与审计落库**并存**（前者便于按 trace_id 检索，
        # 后者是对外可查的权威留痕；两者口径一致，不是重复建设）
        logger.info(
            "AUDIT action=%s result=%s uid=%s username=%s ip=%s detail=%s",
            action,
            result,
            user.id if user is not None else user_id,
            user.username if user is not None else None,
            client_ip,
            detail or {},
        )

        resolved_tenant = tenant_id if tenant_id is not None else (user.tenant_id if user else None)
        if resolved_tenant is None:
            # 既没显式传、也推不出 —— 只能放弃落库并告警（**不静默写 tenant_id=0**，
            # 那会让不同来源的脏数据混进租户 0，事后无从分辨）。
            logger.error("审计缺少 tenant_id，未落库 action=%s result=%s", action, result)
            return

        payload: dict[str, Any] = {"result": result}
        if user is not None:
            payload["username"] = user.username
        if detail:
            payload.update(detail)

        AuditService.record(
            _AUDIT_ACTIONS.get(action, action),
            tenant_id=resolved_tenant,
            user_id=user.id if user is not None else user_id,
            target_type=AuditTarget.SESSION,
            target_id=None,
            after=payload,
            ip=client_ip,
        )
