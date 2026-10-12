"""角色与权限业务编排（F-1.3，接口 API-18 ~ API-22）＋ 部门子树解析（数据域裁剪）。

分层位置：``routers → **services** → dao``。本模块是**唯一**允许同时做
「事务 + 校验 + 缓存失效编排」的地方。

本单元（CU-05）解决两件事
------------------------
**一、RBAC 功能权限的「管理侧」**（API-18 ~ API-22）
    角色列表 / 新建 / 修改、权限点树、角色-权限绑定。

**二、行级数据域 ``data_scope=2 本部门`` 的子树解析**
    CU-04 遗留：``CurrentPrincipal.dept_ids`` 一直为空，导致 ``data_scope=2`` 的行级过滤
    **锁死为不可见**（安全优先的临时兜底）。解析器在
    :class:`app.services.org_scope_service.OrgScopeService`（单独成文件以断开循环导入），
    由 :meth:`app.services.auth_service.AuthService.build_principal` 调用。

🔴 **权限变更必须级联失效**（本模块最容易漏的一步）
--------------------------------------------------
角色↔权限绑定一变，拥有该角色的用户手里的权限快照（Redis ``sess:perm:*``）就**过期了**。
若不失效，用户会拿着旧快照越权访问最长 ``ACCESS_TOKEN_EXPIRE_SECONDS``（2 小时）。
故 :meth:`RbacService.bind_role_permissions` 必须**同时**做两件事：

1. 删除受影响用户的权限快照（``invalidate_perm_snapshot``）→ 强制下次请求回查数据库；
2. 递增**租户全局**权限版本号（``bump_global_perm_version``）→ 通知前端重新拉 API-05。

⚠️ Redis 不可用时上述两步都会失败 —— 但**不阻断绑定本身**（属可丢数据 DS-3），
   仅告警。安全性由「快照未命中即回查数据库」兜底（快照失效只是加速手段，不是安全边界）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import rbac_dao, user_dao
from app.exceptions import (
    BizError,
    ErrorCode,
    IllegalStateError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models.enums import PermType, RoleDataScope, SysUserStatus, label_of
from app.models.user import SysPermission, SysRole, SysRolePermission
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.rbac import (
    BindPermissionsRequest,
    PermissionNodeOut,
    PermissionTreeOut,
    RoleCreateRequest,
    RoleListOut,
    RoleOut,
    RolePermissionsOut,
    RoleUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService

logger = get_logger(__name__)

#: 内置角色中**数据范围由权限矩阵固定**的角色（改掉会导致「无人可见全量」）
_IMMUTABLE_SCOPE_ROLE = "ADM"


def _to_role_out(role: SysRole) -> RoleOut:
    return RoleOut(
        id=role.id,
        role_code=role.role_code,
        role_name=role.role_name,
        data_scope=int(role.data_scope),
        data_scope_name=label_of(RoleDataScope, role.data_scope),
        is_builtin=bool(role.is_builtin),
        status=int(role.status),
        status_name=label_of(SysUserStatus, role.status),
    )


# ============================================================================
# RBAC 管理（API-18 ~ API-22）
# ============================================================================
class RbacService:
    """角色与权限点管理。**全部为无状态静态方法**（会话由 ``get_db`` 注入）。"""

    # ------------------------------------------------------------------
    # API-18 角色列表
    # ------------------------------------------------------------------
    @staticmethod
    def list_roles(db: Session, principal: CurrentPrincipal) -> RoleListOut:
        roles = rbac_dao.list_roles(db, principal.tenant_id)
        return RoleListOut(total=len(roles), items=[_to_role_out(r) for r in roles])

    # ------------------------------------------------------------------
    # API-19 新建角色
    # ------------------------------------------------------------------
    @staticmethod
    def create_role(db: Session, principal: CurrentPrincipal, req: RoleCreateRequest) -> RoleOut:
        """新建**自定义**角色（``is_builtin=0``）。

        :raises BizError(40901): 角色码在本租户内已存在
        """
        existing = rbac_dao.get_role_by_code(db, principal.tenant_id, req.role_code)
        if existing is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"角色码已存在：{req.role_code}",
                {"fields": [{"field": "role_code", "message": "角色码已存在", "type": "value_error"}]},
            )

        role = SysRole(
            tenant_id=principal.tenant_id,
            role_code=req.role_code,
            role_name=req.role_name,
            data_scope=int(req.data_scope),
            is_builtin=0,  # 🔴 只有种子脚本能建内置角色，接口永远建自定义角色
            status=int(req.status),
            is_deleted=0,
        )
        rbac_dao.insert_role(db, role)
        db.commit()
        logger.info(
            "新建角色 tid=%s role_code=%s by_uid=%s",
            principal.tenant_id,
            role.role_code,
            principal.user_id,
        )
        AuditService.record(
            AuditAction.ROLE_CREATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.ROLE,
            target_id=role.id,
            after={
                "role_code": role.role_code,
                "role_name": role.role_name,
                "data_scope": int(role.data_scope),
                "status": int(role.status),
            },
        )
        return _to_role_out(role)

    # ------------------------------------------------------------------
    # API-20 修改角色
    # ------------------------------------------------------------------
    @staticmethod
    def update_role(
        db: Session, principal: CurrentPrincipal, role_id: int, req: RoleUpdateRequest
    ) -> RoleOut:
        """修改角色名 / 数据范围 / 状态。

        :raises NotFoundError: 角色不存在（**本租户内**确实不存在 → 40401）
        :raises IllegalStateError: 试图修改 ``ADM`` 的数据范围（会破坏「有人可见全量」的前提）
        """
        role = rbac_dao.get_role_by_id(db, principal.tenant_id, role_id)
        if role is None:
            # ⚠️ 角色属于「配置类资源」，其存在性不构成敏感信息探测面，
            #    故按 40401 处理（与档案类资源的「越权=40301」口径不同，见 §7.4 铁律 2）
            raise NotFoundError("角色不存在")

        if (
            req.data_scope is not None
            and int(req.data_scope) != int(role.data_scope)
            and role.role_code.upper() == _IMMUTABLE_SCOPE_ROLE
        ):
            raise IllegalStateError("超管（ADM）的数据范围固定为「全部」，不可修改")

        values: dict[str, object] = {}
        if req.role_name is not None:
            values["role_name"] = req.role_name
        if req.data_scope is not None:
            values["data_scope"] = int(req.data_scope)
        if req.status is not None:
            values["status"] = int(req.status)
        if not values:
            return _to_role_out(role)

        before = {
            "role_code": role.role_code,
            "role_name": role.role_name,
            "data_scope": int(role.data_scope),
            "status": int(role.status),
        }
        rbac_dao.update_role_fields(db, principal.tenant_id, role.id, **values)
        db.commit()

        # 数据范围/状态变更会影响该角色下所有用户的行级可见性 → 失效其快照并递增全局版本
        if "data_scope" in values or "status" in values:
            RbacService._invalidate_role_users(db, principal.tenant_id, role.id)

        refreshed = rbac_dao.get_role_by_id(db, principal.tenant_id, role.id)
        actor = refreshed or role
        AuditService.record(
            AuditAction.ROLE_UPDATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.ROLE,
            target_id=role.id,
            before=before,
            after={
                "role_code": actor.role_code,
                "role_name": actor.role_name,
                "data_scope": int(actor.data_scope),
                "status": int(actor.status),
            },
        )
        return _to_role_out(actor)

    # ------------------------------------------------------------------
    # API-21 权限点树
    # ------------------------------------------------------------------
    @staticmethod
    def permission_tree(db: Session, principal: CurrentPrincipal) -> PermissionTreeOut:
        """返回权限点树（菜单 / 按钮 / 接口三类）。"""
        rows = rbac_dao.list_permissions(db, principal.tenant_id)
        return PermissionTreeOut(total=len(rows), items=RbacService._build_tree(rows))

    # ------------------------------------------------------------------
    # API-22 绑定权限点（全量替换）
    # ------------------------------------------------------------------
    @staticmethod
    def bind_role_permissions(
        db: Session, principal: CurrentPrincipal, role_id: int, req: BindPermissionsRequest
    ) -> RolePermissionsOut:
        """把角色的权限集合**全量替换**为 ``req.perm_ids``。

        事务内三步（要么全成，要么全不成）：

        1. 校验角色存在且属于本租户；
        2. 校验所有 ``perm_ids`` 都是**本租户的有效权限点**（否则 40001，防跨租户注入）；
        3. ``DELETE`` 旧绑定 → ``INSERT`` 新绑定。

        提交后（**事务外**）再失效缓存：即便 Redis 全挂，数据库里的事实也已生效。
        """
        role = rbac_dao.get_role_by_id(db, principal.tenant_id, role_id)
        if role is None:
            raise NotFoundError("角色不存在")

        requested = list(dict.fromkeys(int(p) for p in req.perm_ids))  # 去重且保序
        if requested:
            found = rbac_dao.list_permissions_by_ids(db, principal.tenant_id, requested)
            found_ids = {int(p.id) for p in found}
            invalid = [p for p in requested if p not in found_ids]
            if invalid:
                raise BizError(
                    ErrorCode.PARAM_INVALID,
                    "存在不属于本租户或已失效的权限点",
                    {
                        "fields": [
                            {
                                "field": "perm_ids",
                                "message": f"无效权限点 ID：{', '.join(str(i) for i in invalid)}",
                                "type": "value_error",
                            }
                        ]
                    },
                )

        # ⚠️ 必须先读改前集合：本接口是**全量替换**（下一步就 DELETE 旧绑定），
        #    读晚了改前集合就永久丢失 —— 而「这次回收了哪几个权限」正是审计要回答的。
        before_perm_ids = rbac_dao.list_role_permission_ids(db, principal.tenant_id, role.id)

        rbac_dao.delete_role_permissions(db, principal.tenant_id, role.id)
        rows = [
            SysRolePermission(
                tenant_id=principal.tenant_id,
                role_id=role.id,
                perm_id=pid,
            )
            for pid in requested
        ]
        inserted = rbac_dao.insert_role_permissions(db, rows)
        db.commit()
        logger.info(
            "角色权限绑定 tid=%s role_id=%s count=%s by_uid=%s",
            principal.tenant_id,
            role.id,
            inserted,
            principal.user_id,
        )

        affected = RbacService._invalidate_role_users(db, principal.tenant_id, role.id)
        codes = rbac_dao.list_permission_codes_by_ids(db, principal.tenant_id, requested)
        after_perm_ids = rbac_dao.list_role_permission_ids(db, principal.tenant_id, role.id)

        # 🔴 权限变更是审计里**价值最高**的一类记录（数据库 §4.7 的 before/after
        #    注释专门点名「权限变更加密留痕」）。除前后集合外还记
        #    ``granted``/``revoked`` 两个差集 —— 让「谁给了谁什么权限」一眼可读，
        #    而不需要读者自己去比对两个数组。
        AuditService.record(
            AuditAction.ROLE_PERM_BIND,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.ROLE,
            target_id=role.id,
            before={"role_code": role.role_code, "perm_ids": before_perm_ids},
            after={
                "role_code": role.role_code,
                "perm_ids": after_perm_ids,
                "perm_codes": sorted(codes),
                "granted": sorted(set(after_perm_ids) - set(before_perm_ids)),
                "revoked": sorted(set(before_perm_ids) - set(after_perm_ids)),
                "affected_users": affected,
            },
        )
        return RolePermissionsOut(
            role_id=role.id,
            perm_ids=requested,
            perm_codes=sorted(codes),
            affected_users=affected,
            perm_version=rbac_dao.get_perm_version(principal.tenant_id, principal.user_id),
        )

    # ------------------------------------------------------------------
    # 内部：权限树构件
    # ------------------------------------------------------------------
    @staticmethod
    def _build_tree(rows: list[SysPermission]) -> list[PermissionNodeOut]:
        """扁平列表 → 树。

        - 以 ``parent_id`` 建索引，**不假设父节点一定存在**：
          父节点缺失（被停用/软删）的节点**提升为根**，避免整棵子树在界面上消失；
        - 同级按 ``sort_no`` → ``id`` 稳定排序（与 DAO 的排序一致）。
        """
        nodes: dict[int, PermissionNodeOut] = {}
        for row in rows:
            nodes[int(row.id)] = PermissionNodeOut(
                id=row.id,
                perm_code=row.perm_code,
                perm_name=row.perm_name,
                perm_type=int(row.perm_type),
                perm_type_name=label_of(PermType, row.perm_type),
                parent_id=row.parent_id,
                sort_no=int(row.sort_no or 0),
                status=int(row.status),
                children=[],
            )

        roots: list[PermissionNodeOut] = []
        for row in rows:
            node = nodes[int(row.id)]
            parent = nodes.get(int(row.parent_id or 0))
            if parent is not None and parent is not node:
                parent.children.append(node)
            else:
                roots.append(node)
        return roots

    # ------------------------------------------------------------------
    # 内部：角色变更后的缓存失效
    # ------------------------------------------------------------------
    @staticmethod
    def _invalidate_role_users(db: Session, tenant_id: int, role_id: int) -> int:
        """失效该角色下所有用户的权限快照，并递增租户全局权限版本号。

        :return: 受影响的用户数（Redis 不可用时仍返回用户数，但失效动作未生效）

        ⚠️ **失败不抛出**：缓存失效属「加速手段」，数据库已提交的事实才是准据；
           抛异常会让一次成功的绑定变成 500，得不偿失。
        """
        try:
            user_ids = rbac_dao.list_user_ids_by_role(db, tenant_id, role_id)
        except Exception as exc:  # noqa: BLE001 — 失效失败不得阻断主流程
            logger.warning("查询角色用户失败（跳过快照失效）role_id=%s err=%s", role_id, exc)
            return 0

        for uid in user_ids:
            AuthService.invalidate_perm_snapshot(tenant_id, uid)
        rbac_dao.bump_global_perm_version(tenant_id)
        return len(user_ids)


__all__ = ["RbacService"]
