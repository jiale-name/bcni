"""部门子树解析（行级权限 ``data_scope=2 本部门`` 的基础，属 CU-05）。

**为什么单独成文件**：``auth_service``（登录时回填 ``dept_ids``）需要它，
而 ``rbac_service`` 又依赖 ``auth_service`` 做快照失效 —— 若把它放在
``rbac_service`` 里就会形成 ``auth_service → rbac_service → auth_service`` 的**循环导入**。
本模块只依赖 ``dao``，处于依赖链最底层，两个 service 都能安全引用。

本模块**只读**，不写组织数据（组织 CRUD 归 CU-06，见接口 API-07~12）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.dao import rbac_dao, user_dao

logger = get_logger(__name__)


class OrgScopeService:
    """组织子树 → 行级权限 ID 集合。"""

    @staticmethod
    def build_dept_path(parent_path: str | None, dept_id: int) -> str:
        """构造 ``dept_path``：「从根到自身、**含自身**、以 ``/`` 收尾」。

        .. code-block:: text

            build_dept_path(None, 1)       -> "/1/"
            build_dept_path("/1/", 12)     -> "/1/12/"
            build_dept_path("/1/12/", 125) -> "/1/12/125/"

        🔴 **"含自身 + 尾斜杠"是子树前缀匹配正确性的前提**：
        ``/1/12/`` 作前缀**不会**误命中兄弟部门 ``/1/123/``（``/1/12`` 之后必须是 ``/``）。
        若省略尾斜杠，``/1/12`` 会同时命中 ``/1/120/``、``/1/123/`` —— 直接越权。

        供 CU-06（组织 CRUD）在新增/移动部门时调用；本单元不写组织数据。
        """
        base = (parent_path or "").strip()
        if base and not base.endswith("/"):
            base += "/"
        if not base:
            base = "/"  # 🔴 根部门必须有前导斜杠，否则得到 "1/" 而非 "/1/"
        return f"{base}{int(dept_id)}/"

    @staticmethod
    def resolve_dept_ids(db: Session, tenant_id: int, org_id: int | None) -> tuple[int, ...]:
        """解析某部门的子树（**含自身**），供 ``data_scope=2`` 的行级过滤使用。

        返回值语义与 :attr:`app.core.tenant.CurrentPrincipal.dept_ids` 一致。

        **三种兜底（一律「缩小」而非「放大」）**：

        ==================== ==================================================
        情况                 返回
        ==================== ==================================================
        ``org_id`` 为空       ``()`` —— 调用方据此锁死为不可见
        部门不存在 / 已软删    ``()``
        ``dept_path`` 未维护   ``(自身,)`` —— 看得见自己部门，但**不含子孙**
        ==================== ==================================================

        🔴 绝不返回「全部部门」：解析失败时的正确行为是**少看见**，不是多看见。
        """
        if org_id is None:
            return ()
        dept = user_dao.get_org_by_id(db, tenant_id, int(org_id))
        if dept is None:
            logger.info("部门子树解析：部门不存在或已删除 tid=%s org_id=%s", tenant_id, org_id)
            return ()

        own_id = int(dept.id)
        path = (dept.dept_path or "").strip()
        if not path:
            logger.warning(
                "部门子树解析：dept_path 未维护，退化为仅本部门 tid=%s org_id=%s", tenant_id, own_id
            )
            return (own_id,)
        if not path.endswith("/"):
            path += "/"

        descendants = rbac_dao.list_dept_subtree_ids(db, tenant_id, path)
        out = [own_id]
        out.extend(d for d in descendants if d != own_id)
        return tuple(out)


__all__ = ["OrgScopeService"]
