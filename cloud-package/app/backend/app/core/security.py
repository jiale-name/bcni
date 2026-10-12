"""JWT / 密码 / 权限依赖（类图 CL-6 的 ``SecurityUtils``）。

🔴 **权限数据只在 JWT 里**（接口 §1.3 红线）：``tenant_id`` / ``org_id`` / ``data_scope``
只从 Token 解析，**绝不接受前端传参**；前端传参只能用于**收窄**范围
（校验见 :meth:`app.core.tenant.DataScopeFilter.assert_scope_narrowing`）。

JWT claim 命名（与接口 §1.3 逐字对应）::

    sub  user_id        tid  tenant_id      oid  org_id
    rls  roles[]        dsc  data_scope     exp  exp          typ  access|refresh
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.context import set_user_id
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import PermissionDeniedError, UnauthenticatedError

logger = get_logger(__name__)

TOKEN_TYPE_ACCESS: Final[str] = "access"
TOKEN_TYPE_REFRESH: Final[str] = "refresh"

#: bcrypt 单次输入上限（超出部分会被静默截断，必须先拦）
_BCRYPT_MAX_BYTES: Final[int] = 72

#: 演示/开发用的明文密码最短长度（API-01：8~64 字符）
PASSWORD_MIN_LENGTH: Final[int] = 8
PASSWORD_MAX_LENGTH: Final[int] = 64


class SecurityUtils:
    """密码与 Token 工具集（纯函数，不碰数据库）。"""

    # ------------------------------------------------------------------
    # 密码
    # ------------------------------------------------------------------
    @staticmethod
    def hash_password(raw_password: str) -> str:
        """bcrypt 哈希（F-1.2：密码 bcrypt 存储）。"""
        pw_bytes = raw_password.encode("utf-8")
        if len(pw_bytes) > _BCRYPT_MAX_BYTES:
            raise ValueError(f"密码过长（> {_BCRYPT_MAX_BYTES} 字节）")
        return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)).decode("utf-8")

    @staticmethod
    def verify_password(raw_password: str, password_hash: str) -> bool:
        """恒定时比较，异常一律视为不匹配（不向调用方泄露失败原因）。"""
        try:
            return bcrypt.checkpw(
                raw_password.encode("utf-8"), password_hash.encode("utf-8")
            )
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Token
    # ------------------------------------------------------------------
    @staticmethod
    def _create_token(
        *,
        user_id: int,
        tenant_id: int,
        token_type: str,
        expires_in: int,
        token_version: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": str(user_id),  # 🔴 一律字符串，避免雪花 ID 精度问题
            "tid": int(tenant_id),
            "typ": token_type,
            "ver": int(token_version),  # 签发时的会话版本（改密/强制下线后旧版本立即失效）
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        if extra:
            payload.update(extra)
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @classmethod
    def create_access_token(
        cls,
        *,
        user_id: int,
        tenant_id: int,
        org_id: int | None,
        roles: list[str],
        data_scope: int,
        token_version: int = 0,
    ) -> str:
        """签发 access_token（含全部权限 claim，接口 §1.3）。"""
        return cls._create_token(
            user_id=user_id,
            tenant_id=tenant_id,
            token_type=TOKEN_TYPE_ACCESS,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            token_version=token_version,
            extra={
                "oid": int(org_id) if org_id is not None else None,
                "rls": list(roles),
                "dsc": int(data_scope),
            },
        )

    @classmethod
    def create_refresh_token(cls, *, user_id: int, tenant_id: int, token_version: int = 0) -> str:
        """签发 refresh_token（**不含权限 claim** —— 续期时必须回查数据库，防止权限降级后越权）。"""
        return cls._create_token(
            user_id=user_id,
            tenant_id=tenant_id,
            token_type=TOKEN_TYPE_REFRESH,
            expires_in=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
            token_version=token_version,
        )

    @staticmethod
    def decode_token(token: str, *, expect_type: str | None = None) -> dict[str, Any]:
        """校验签名与过期并返回 claim。

        :raises UnauthenticatedError: 过期 / 签名错误 / 类型不符（统一 40101）
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthenticatedError("登录已过期，请重新登录") from exc
        except jwt.PyJWTError as exc:
            logger.warning("Token 解析失败：%s", exc)
            raise UnauthenticatedError("凭证无效") from exc

        if expect_type is not None and payload.get("typ") != expect_type:
            raise UnauthenticatedError(f"凭证类型不符（需要 {expect_type}）")
        return payload

    @staticmethod
    def principal_from_claims(payload: dict[str, Any]) -> CurrentPrincipal:
        """把 access_token 的 claim 转成 :class:`CurrentPrincipal`。"""
        try:
            user_id = int(payload["sub"])
            tenant_id = int(payload["tid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UnauthenticatedError("凭证缺少必要字段") from exc

        org_id_raw = payload.get("oid")
        return CurrentPrincipal(
            user_id=user_id,
            tenant_id=tenant_id,
            org_id=int(org_id_raw) if org_id_raw is not None else None,
            roles=tuple(payload.get("rls") or ()),
            data_scope=int(payload.get("dsc") or 3),
        )


# ============================================================================
# FastAPI 依赖
# ============================================================================
#: auto_error=False —— 未带凭证时由我们抛统一错误码 40101，而不是 FastAPI 默认的 403
_bearer_scheme = HTTPBearer(auto_error=False)


def assert_token_alive(payload: dict[str, Any]) -> None:
    """校验 Token 是否被**提前吊销**（F-1.2）。

    两级判定（见 :mod:`app.dao.token_dao`）：

    1. ``jti`` 是否在否定名单中（API-03 单 Token 登出）；
    2. Token 的 ``ver``（签发时会话版本）是否**落后于**当前版本
       （API-06 改密 / API-03 全端注销会递增版本 —— 用版本号而非时间戳，
       避免秒级精度导致的「同秒误判」）。

    🔴 Redis 不可用时 **fail-open**（视为有效）—— Redis 抖动不得升级为全站 401。

    :raises UnauthenticatedError: 已被吊销（统一 40101，前端静默 refresh 失败后跳登录页）
    """
    # 延迟导入：core → dao 单向依赖，避免 import 期耦合
    from app.dao import token_dao

    if token_dao.is_token_revoked(payload.get("jti")):
        raise UnauthenticatedError("凭证已失效，请重新登录")

    sub, tid = payload.get("sub"), payload.get("tid")
    if sub is not None and tid is not None:
        current_version = token_dao.get_token_version(int(tid), int(sub))
        if current_version and int(payload.get("ver") or 0) < current_version:
            raise UnauthenticatedError("登录状态已失效，请重新登录")


def get_access_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """解析并校验 **access_token**，返回原始 claim（接口 §1.4 第 2 层：JWT 鉴权）。

    拆出本依赖是为了让 API-03 登出能拿到 ``jti`` / ``exp``（吊销需要），
    同时避免「登出接口再解一次 Token」造成重复校验。

    :raises UnauthenticatedError: 未带凭证 / 过期 / 签名错误 / 已被吊销（统一 40101）
    """
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError("未提供访问凭证")
    payload = SecurityUtils.decode_token(credentials.credentials, expect_type=TOKEN_TYPE_ACCESS)
    assert_token_alive(payload)
    return payload


def get_current_principal(
    claims: dict[str, Any] = Depends(get_access_claims),
) -> CurrentPrincipal:
    """把已校验的 claim 转成请求主体（接口 §1.4 第 2 层）。

    公开接口（API-01/02）**不要**声明本依赖。
    """
    principal = SecurityUtils.principal_from_claims(claims)
    set_user_id(principal.user_id)  # 供日志与限流使用（不作为权限依据）
    return principal


def require_perm(*perm_codes: str):
    """功能权限依赖工厂（接口 §1.4 第 3 层：RBAC 角色校验）。

    .. code-block:: python

        @router.post("/talents", dependencies=[Depends(require_perm("talent:create"))])

    判定顺序：

    1. ``ADM`` 直接放行（超级管理员）；
    2. 其余角色查权限点快照（Redis ``sess:perm:<uid>`` 命中则用缓存，未命中回查数据库）；
    3. **fail-closed**：取不到权限点快照时**拒绝**，而不是放行。

    ⚠️ 实现进度：权限点快照的**数据来源**（``sys_role_permission`` × ``sys_permission``）
    已在 CU-04 打通（API-05 需要）；权限点的**维护 CRUD**（API-18~22）属 CU-05，
    届时本函数**签名不变**，仅补齐缓存失效逻辑。
    """
    required = tuple(c for c in perm_codes if c)

    def _dependency(
        principal: CurrentPrincipal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> CurrentPrincipal:
        if principal.is_admin:
            return principal
        if not required:
            return principal

        # 延迟导入：避免 core → services 的循环依赖
        from app.services.auth_service import AuthService

        granted = AuthService.get_perm_codes(db, principal)
        missing = [code for code in required if code not in granted]
        if missing:
            raise PermissionDeniedError(f"缺少权限：{', '.join(missing)}")
        return principal

    return _dependency


class SecurityDependency:
    """与类图 CL-6 的 ``SecurityUtils.require(perm_code)`` 对应的另一种写法。

    保留为类方法形式，供不方便用依赖工厂的场景（如 Service 内部二次校验）调用。
    """

    @staticmethod
    def require(perm_code: str, *, granted: set[str], principal: CurrentPrincipal) -> None:
        if principal.is_admin or perm_code in granted:
            return
        raise PermissionDeniedError(f"缺少权限：{perm_code}")
