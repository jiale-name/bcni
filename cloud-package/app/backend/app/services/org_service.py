"""组织与岗位业务编排（F-1.1，接口 API-07 ~ API-12）。

分层位置：``routers → **services** → dao``。本模块是组织数据的**唯一写入口**
（除种子脚本外），因此也是 ``dept_path`` 不变式的**唯一维护者**。

🔴 CU-06 最关键的一件事：``dept_path`` 的级联维护
================================================
``dept_path``（形如 ``/1/12/125/``，含自身、以 ``/`` 收尾）是**行级权限
``data_scope=2 本部门`` 的唯一依据**（:func:`app.dao.rbac_dao.list_dept_subtree_ids`
靠它做前缀匹配）。它的正确性直接等于「部门经理能否看见本部门的人」。

由此推出三条实现纪律：

1. **新建部门必须在拿到 ID 之后再算路径** —— 路径里含自身 ID，
   所以不能用「交给 ORM 的 ``default=next_id`` 在 flush 时生成」那套，必须在
   构造实体前显式 :func:`app.core.snowflake.next_id`。
2. **移动部门必须重算「自身 + 全部子孙」** —— 只改自己会让子孙的路径前缀失效，
   于是「本部门可见子树」在移动后**静默丢失一半**。这是本项目最隐蔽的一类越权/失权缺陷：
   不报错、不崩溃，只是有人突然看不见了（或按错误的前缀看见了不该看的）。
3. **重算要用``parent_id`` 走 BFS，而不是拿旧路径做字符串替换** ——
   字符串替换依赖「旧路径已经是正确的」，一旦历史数据里有一个 ``dept_path`` 为 ``NULL``
   （种子脚本之前建的部门、或早期 API 写坏的），替换就会算出一串错误路径。
   而 ``parent_id`` 是**结构与生俱来**的父子关系，不依赖任何派生列 —— 用它重算，
   顺带把被移动子树里所有坏的 ``dept_path`` **自愈**成正确值。

⚠️ 顺带说清「为什么删除部门比看起来严」：部门一旦软删，
:class:`app.services.org_scope_service.OrgScopeService` 会对其返回空 ID 集合
（fail-closed 兜底）—— 挂在它下面的账号会**静默失去行级可见性**。因此删除前必须
把「还有账号挂着」的情况拦下来（40904），而不是让它事后表现为「某人的列表突然空了」。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.snowflake import next_id
from app.core.tenant import CurrentPrincipal
from app.dao import org_dao, user_admin_dao
from app.exceptions import (
    BizError,
    ErrorCode,
    IllegalStateError,
    NotFoundError,
)
from app.models.enums import SysUserStatus, label_of
from app.models.tenant import OrgDept, Position
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.org import (
    DEPT_LEVEL_MAX,
    DEPT_PATH_MAX,
    OrgCreateRequest,
    OrgDeleteOut,
    OrgNodeOut,
    OrgTreeOut,
    OrgUpdateRequest,
    PositionCreateRequest,
    PositionListOut,
    PositionOut,
)
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)


# ============================================================================
# 转换助手
# ============================================================================
def _to_node(dept: OrgDept, children: list[OrgNodeOut] | None = None) -> OrgNodeOut:
    return OrgNodeOut(
        id=dept.id,
        parent_id=dept.parent_id or 0,
        dept_code=dept.dept_code,
        dept_name=dept.dept_name,
        dept_level=int(dept.dept_level or 1),
        dept_path=dept.dept_path,
        leader_user_id=dept.leader_user_id,
        sort_no=int(dept.sort_no or 0),
        status=int(dept.status),
        status_name=label_of(SysUserStatus, dept.status),
        children=children or [],
    )


def _to_position_out(pos: Position) -> PositionOut:
    return PositionOut(
        id=pos.id,
        position_code=pos.position_code,
        position_name=pos.position_name,
        dept_id=pos.dept_id,
        position_level=pos.position_level,
        status=int(pos.status),
        status_name=label_of(SysUserStatus, pos.status),
    )


def _normalize_path(path: str | None) -> str | None:
    """把 ``dept_path`` 规范化成「以 ``/`` 收尾」的形式；空值返回 ``None``。"""
    text = (path or "").strip()
    if not text:
        return None
    return text if text.endswith("/") else f"{text}/"


#: 参与审计留痕的部门字段（**不含** ``id``/``is_deleted`` —— 前者标识对象本身，
#: 后者由删除动作单独补进 after；混进来会让 diff 噪音大于信息）
_AUDIT_DEPT_FIELDS = (
    "parent_id",
    "dept_code",
    "dept_name",
    "dept_level",
    "dept_path",
    "leader_user_id",
    "sort_no",
    "status",
)


def _dept_snapshot(dept: OrgDept) -> dict[str, object]:
    """取部门的可留痕字段快照（供审计的改前/改后值对比）。"""
    return {name: getattr(dept, name, None) for name in _AUDIT_DEPT_FIELDS}


def _position_snapshot(pos: Position) -> dict[str, object]:
    """取岗位的可留痕字段快照。"""
    return {
        "position_code": pos.position_code,
        "position_name": pos.position_name,
        "dept_id": pos.dept_id,
        "position_level": pos.position_level,
        "status": int(pos.status),
    }


class OrgService:
    """组织架构与岗位。**全部为无状态静态方法**（会话由 ``get_db`` 注入）。"""

    # ------------------------------------------------------------------
    # API-07 组织架构树
    # ------------------------------------------------------------------
    @staticmethod
    def tree(db: Session, principal: CurrentPrincipal) -> OrgTreeOut:
        """返回本租户的组织树（≥5 级）。

        - 只看**未软删**部门；父节点已被软删的子部门**提升为根**
          （与 CU-05 权限树同源：宁可在界面上多显示，也不要让子树凭空消失）；
        - 同级按 ``sort_no`` → ``id`` 稳定排序（DAO 已排好，此处只负责建树）。
        """
        rows = org_dao.list_depts(db, principal.tenant_id)
        return OrgTreeOut(total=len(rows), items=OrgService._build_tree(rows))

    # ------------------------------------------------------------------
    # API-08 新建部门
    # ------------------------------------------------------------------
    @staticmethod
    def create_dept(
        db: Session, principal: CurrentPrincipal, req: OrgCreateRequest
    ) -> OrgNodeOut:
        """新建部门，并计算 ``dept_path`` / ``dept_level``。

        :raises NotFoundError: 父部门不存在（40401）
        :raises BizError(40901): 组织编码在本租户内已存在
        :raises BizError(40002): 层级/路径超上限
        """
        tenant_id = principal.tenant_id
        parent_id = int(req.parent_id or 0)

        if parent_id:
            parent = org_dao.get_dept_by_id(db, tenant_id, parent_id)
            if parent is None or parent.is_deleted:
                raise NotFoundError("父部门不存在")
            parent_path: str | None = parent.dept_path
            dept_level = int(parent.dept_level or 1) + 1
        else:
            parent_path = None
            dept_level = 1

        if dept_level > DEPT_LEVEL_MAX:
            raise BizError(ErrorCode.PARAM_OVER_LIMIT, f"组织层级超过上限 {DEPT_LEVEL_MAX}")

        if org_dao.get_dept_by_code(db, tenant_id, req.dept_code) is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"组织编码已存在：{req.dept_code}",
                {
                    "fields": [
                        {"field": "dept_code", "message": "组织编码已存在", "type": "value_error"}
                    ]
                },
            )

        if req.leader_user_id is not None:
            OrgService._assert_user_exists(db, tenant_id, int(req.leader_user_id), "负责人")

        # 🔴 必须先有 ID 才能算路径（路径含自身 ID），故不能依赖 ORM 的 default 生成
        dept_id = next_id()
        dept_path = OrgScopeService.build_dept_path(parent_path, dept_id)
        if len(dept_path) > DEPT_PATH_MAX:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                f"组织路径长度超过 {DEPT_PATH_MAX}，请减少嵌套层级",
            )

        dept = OrgDept(
            id=dept_id,
            tenant_id=tenant_id,
            parent_id=parent_id,
            dept_code=req.dept_code,
            dept_name=req.dept_name,
            dept_level=dept_level,
            dept_path=dept_path,
            leader_user_id=req.leader_user_id,
            sort_no=req.sort_no,
            status=req.status,
            is_deleted=0,
        )
        org_dao.insert_dept(db, dept)
        db.commit()
        logger.info(
            "新建部门 tid=%s dept_id=%s code=%s path=%s by_uid=%s",
            tenant_id,
            dept.id,
            dept.dept_code,
            dept.dept_path,
            principal.user_id,
        )
        AuditService.record(
            AuditAction.DEPT_CREATE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.DEPT,
            target_id=dept.id,
            after=_dept_snapshot(dept),
        )
        return _to_node(dept)

    # ------------------------------------------------------------------
    # API-09 修改部门（含移动父节点）
    # ------------------------------------------------------------------
    @staticmethod
    def update_dept(
        db: Session, principal: CurrentPrincipal, dept_id: int, req: OrgUpdateRequest
    ) -> OrgNodeOut:
        """修改部门资料；若传了 ``parent_id`` 则**连带重算整个子树**的路径与层级。

        :raises NotFoundError: 部门 / 新父部门 / 负责人不存在（40401）
        :raises BizError(40901): 组织编码冲突
        :raises IllegalStateError(42201): 移到自己或自己的子孙下（会形成环）
        :raises BizError(40002): 移动后层级或路径超上限
        """
        tenant_id = principal.tenant_id
        dept = org_dao.get_dept_by_id(db, tenant_id, dept_id)
        if dept is None or dept.is_deleted:
            raise NotFoundError("部门不存在")

        # ⚠️ 必须在任何写操作**之前**取快照：下面的 _move_dept 会就地改 dept 的
        #    dept_path/dept_level，事后再取就只剩「改后值」，审计会变成两条一样的记录。
        before_dept = _dept_snapshot(dept)

        fields = req.model_fields_set
        values: dict[str, object] = {}

        if "dept_code" in fields and req.dept_code is not None:
            if req.dept_code != dept.dept_code:
                existing = org_dao.get_dept_by_code(db, tenant_id, req.dept_code)
                if existing is not None and int(existing.id) != int(dept.id):
                    raise BizError(
                        ErrorCode.UNIQUE_CONFLICT,
                        f"组织编码已存在：{req.dept_code}",
                        {
                            "fields": [
                                {
                                    "field": "dept_code",
                                    "message": "组织编码已存在",
                                    "type": "value_error",
                                }
                            ]
                        },
                    )
            values["dept_code"] = req.dept_code

        if "dept_name" in fields and req.dept_name is not None:
            values["dept_name"] = req.dept_name
        if "leader_user_id" in fields:
            if req.leader_user_id is not None:
                OrgService._assert_user_exists(db, tenant_id, int(req.leader_user_id), "负责人")
            values["leader_user_id"] = req.leader_user_id
        if "sort_no" in fields and req.sort_no is not None:
            values["sort_no"] = req.sort_no
        if "status" in fields and req.status is not None:
            values["status"] = req.status

        moved = False
        if "parent_id" in fields:
            # ⚠️ 用 model_fields_set 而非 `is not None`：parent_id=0 表示「移动到根」，
            #    是**有效变更**，用 `is not None` 会把它当成「没传」而静默忽略。
            moved = OrgService._move_dept(db, tenant_id, dept, req.parent_id)

        if values:
            org_dao.update_dept_fields(db, tenant_id, dept.id, **values)
        db.commit()

        refreshed = org_dao.get_dept_by_id(db, tenant_id, dept.id)
        actor = refreshed or dept
        if moved:
            logger.info(
                "移动部门 tid=%s dept_id=%s new_parent=%s path=%s by_uid=%s",
                tenant_id,
                dept.id,
                actor.parent_id,
                actor.dept_path,
                principal.user_id,
            )
        AuditService.record(
            AuditAction.DEPT_UPDATE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.DEPT,
            target_id=dept.id,
            before=before_dept,
            after=_dept_snapshot(actor),
        )
        return _to_node(actor)

    # ------------------------------------------------------------------
    # API-10 删除部门
    # ------------------------------------------------------------------
    @staticmethod
    def delete_dept(db: Session, principal: CurrentPrincipal, dept_id: int) -> OrgDeleteOut:
        """软删部门。**有子部门或有账号挂着时拒绝**（40904）。

        :raises NotFoundError: 部门不存在或**已软删**（40401 —— 与接口 §1.5 口径一致）
        :raises BizError(40904): 依赖前置未满足
        """
        tenant_id = principal.tenant_id
        dept = org_dao.get_dept_by_id(db, tenant_id, dept_id)
        if dept is None or dept.is_deleted:
            raise NotFoundError("部门不存在")

        # 软删前的快照（下面 update_dept_fields 会把 is_deleted/status 就地改掉）
        before_dept = _dept_snapshot(dept)

        child_count = org_dao.count_child_depts(db, tenant_id, dept.id)
        if child_count:
            raise BizError(
                ErrorCode.PRECONDITION_UNMET,
                f"该部门下还有 {child_count} 个子部门，请先删除或移走子部门",
                {"dept_id": str(dept.id), "child_count": child_count},
            )

        # 引用校验基于「自身 + 全部子孙」——虽然上面已保证无子部门，
        # 但用子树集合表达意图更稳（将来若放开「级联删除」不必重写这段）
        subtree_ids = OrgService._subtree_ids(db, tenant_id, dept)
        enabled = org_dao.count_users_in_depts(db, tenant_id, subtree_ids, enabled_only=True)
        total = org_dao.count_users_in_depts(db, tenant_id, subtree_ids, enabled_only=False)
        if total:
            disabled = total - enabled
            detail = f"其中在职 {enabled} 人"
            if disabled:
                detail += f"、已禁用 {disabled} 人"
            raise BizError(
                ErrorCode.PRECONDITION_UNMET,
                f"该部门下还有 {total} 个账号（{detail}），请先调整其归属组织",
                {"dept_id": str(dept.id), "user_count": total},
            )

        # 🔴 不做「级联软删子部门」：子部门上面已被拦住。
        #    也不动 position.dept_id —— 岗位的 dept_id 仅表示「建议归属」，
        #    TBL-08 本身允许为空、且不参与任何权限判定，悬挂引用不会造成越权或失权。
        org_dao.update_dept_fields(
            db,
            tenant_id,
            dept.id,
            is_deleted=1,
            deleted_at=datetime.now(),
            status=0,
        )
        db.commit()
        logger.info(
            "删除部门 tid=%s dept_id=%s code=%s by_uid=%s",
            tenant_id,
            dept.id,
            dept.dept_code,
            principal.user_id,
        )
        AuditService.record(
            AuditAction.DEPT_DELETE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.DEPT,
            target_id=dept.id,
            before=before_dept,
            after={**before_dept, "is_deleted": 1, "status": 0},
        )
        return OrgDeleteOut(
            dept_id=dept.id,
            dept_name=dept.dept_name,
            deleted=True,
            affected_children=0,
        )

    # ------------------------------------------------------------------
    # API-11 岗位列表
    # ------------------------------------------------------------------
    @staticmethod
    def list_positions(
        db: Session,
        principal: CurrentPrincipal,
        *,
        dept_id: int | None = None,
        status: int | None = None,
        keyword: str | None = None,
    ) -> PositionListOut:
        """岗位列表（非分页，与 ``/roles`` 同构：``data = {total, items}``）。"""
        rows = org_dao.list_positions(
            db, principal.tenant_id, dept_id=dept_id, status=status, keyword=keyword
        )
        return PositionListOut(total=len(rows), items=[_to_position_out(p) for p in rows])

    # ------------------------------------------------------------------
    # API-12 新建岗位
    # ------------------------------------------------------------------
    @staticmethod
    def create_position(
        db: Session, principal: CurrentPrincipal, req: PositionCreateRequest
    ) -> PositionOut:
        """新建岗位。

        :raises BizError(40901): 岗位编码已存在
        :raises NotFoundError: 所属组织不存在（40401）
        """
        tenant_id = principal.tenant_id
        if org_dao.get_position_by_code(db, tenant_id, req.position_code) is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"岗位编码已存在：{req.position_code}",
                {
                    "fields": [
                        {
                            "field": "position_code",
                            "message": "岗位编码已存在",
                            "type": "value_error",
                        }
                    ]
                },
            )

        if req.dept_id is not None:
            dept = org_dao.get_dept_by_id(db, tenant_id, int(req.dept_id))
            if dept is None or dept.is_deleted:
                raise NotFoundError("所属组织不存在")

        pos = Position(
            id=next_id(),
            tenant_id=tenant_id,
            position_code=req.position_code,
            position_name=req.position_name,
            dept_id=req.dept_id,
            position_level=req.position_level,
            status=req.status,
            is_deleted=0,
        )
        org_dao.insert_position(db, pos)
        db.commit()
        logger.info(
            "新建岗位 tid=%s position_id=%s code=%s by_uid=%s",
            tenant_id,
            pos.id,
            pos.position_code,
            principal.user_id,
        )
        AuditService.record(
            AuditAction.POSITION_CREATE,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.POSITION,
            target_id=pos.id,
            after=_position_snapshot(pos),
        )
        return _to_position_out(pos)

    # ------------------------------------------------------------------
    # 内部：组织树构件
    # ------------------------------------------------------------------
    @staticmethod
    def _build_tree(rows: list[OrgDept]) -> list[OrgNodeOut]:
        """扁平列表 → 树（父节点缺失的节点**提升为根**，不丢数据）。"""
        nodes: dict[int, OrgNodeOut] = {int(r.id): _to_node(r) for r in rows}
        roots: list[OrgNodeOut] = []
        for row in rows:
            node = nodes[int(row.id)]
            parent = nodes.get(int(row.parent_id or 0))
            if parent is not None and parent is not node:
                parent.children.append(node)
            else:
                roots.append(node)
        return roots

    @staticmethod
    def _children_index(db: Session, tenant_id: int) -> dict[int, list[OrgDept]]:
        """``parent_id → 子部门`` 索引（**一次查询**建好，供子树遍历复用）。

        用 ``parent_id`` 而不是 ``dept_path`` 前缀来遍历子树 —— 前者是结构本身，
        后者是从结构派生的列，可能因为历史数据或早期 bug 而失真。
        """
        index: dict[int, list[OrgDept]] = {}
        for row in org_dao.list_depts(db, tenant_id):
            index.setdefault(int(row.parent_id or 0), []).append(row)
        return index

    @staticmethod
    def _subtree(index: dict[int, list[OrgDept]], root: OrgDept) -> list[OrgDept]:
        """BFS 取「自身 + 全部子孙」（**自顶向下**顺序，便于逐层重算派生列）。"""
        out: list[OrgDept] = [root]
        queue: deque[OrgDept] = deque([root])
        while queue:
            current = queue.popleft()
            for child in index.get(int(current.id), []):
                out.append(child)
                queue.append(child)
        return out

    @staticmethod
    def _subtree_ids(db: Session, tenant_id: int, root: OrgDept) -> list[int]:
        """取「自身 + 全部子孙」的部门 ID（供删除前的引用校验）。"""
        nodes = OrgService._subtree(OrgService._children_index(db, tenant_id), root)
        return [int(n.id) for n in nodes]

    @staticmethod
    def _move_dept(
        db: Session, tenant_id: int, dept: OrgDept, new_parent_id: int | None
    ) -> bool:
        """把 ``dept`` 挂到 ``new_parent_id`` 下，并**自愈式重算整个子树**。

        :return: 是否真的发生了移动（``False`` 表示父节点未变，未写库）
        :raises IllegalStateError(42201): 移到自己或自己的子孙下
        """
        target = int(new_parent_id or 0)
        if target == int(dept.id):
            raise IllegalStateError("不能把部门移动到自己下面")
        if target == int(dept.parent_id or 0):
            return False  # 父节点没变，无需重算（避免无谓的全子树写库）

        index = OrgService._children_index(db, tenant_id)
        subtree = OrgService._subtree(index, dept)
        subtree_ids = {int(n.id) for n in subtree}

        if target:
            # 🔴 环路检测：新父节点若落在自己的子树里，移动后会出现自引用环，
            #    子树遍历与 dept_path 前缀匹配都会失效（行级权限随之错乱）。
            if target in subtree_ids:
                cycle_name = next(
                    (n.dept_name for n in subtree if int(n.id) == target), "子孙部门"
                )
                raise IllegalStateError(f"不能把部门移动到自己的子孙下（{cycle_name}）")
            parent = org_dao.get_dept_by_id(db, tenant_id, target)
            if parent is None or parent.is_deleted:
                raise NotFoundError("目标父部门不存在")
            new_parent_path: str | None = parent.dept_path
            new_level = int(parent.dept_level or 1) + 1
        else:
            new_parent_path = None
            new_level = 1

        new_own_path = OrgScopeService.build_dept_path(new_parent_path, dept.id)
        if len(new_own_path) > DEPT_PATH_MAX:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                f"移动后组织路径长度超过 {DEPT_PATH_MAX}，请减少嵌套层级",
            )

        # ---- 自顶向下重算：每层用「父节点算好的新路径 + 自身 ID」重新拼接 ----
        # BFS 顺序保证父节点先于子节点被处理，故可直接读父节点**已更新**的新值。
        # 用 id→节点 映射做父查找（O(1)），避免在循环里线性扫描子树。
        by_id: dict[int, OrgDept] = {int(n.id): n for n in subtree}
        dept.parent_id = target
        dept.dept_path = new_own_path
        dept.dept_level = new_level

        for node in subtree[1:]:  # 跳过自身
            parent = by_id.get(int(node.parent_id or 0))
            base_path = parent.dept_path if parent is not None else new_own_path
            node_path = OrgScopeService.build_dept_path(base_path, node.id)
            node_level = (int(parent.dept_level) + 1) if parent is not None else new_level + 1
            if len(node_path) > DEPT_PATH_MAX or node_level > DEPT_LEVEL_MAX:
                raise BizError(
                    ErrorCode.PARAM_OVER_LIMIT,
                    f"移动后部门「{node.dept_name}」的层级或路径超上限，请减少嵌套层级",
                )
            node.dept_path = node_path
            node.dept_level = node_level

        logger.info(
            "重算部门子树路径 tid=%s root=%s nodes=%s", tenant_id, dept.id, len(subtree)
        )
        return True

    @staticmethod
    def _assert_user_exists(db: Session, tenant_id: int, user_id: int, label: str) -> None:
        """校验用户存在（防止写入悬挂的 ``leader_user_id``）。"""
        if user_admin_dao.get_user_by_id(db, tenant_id, user_id) is None:
            raise NotFoundError(f"{label}不存在")


__all__ = ["OrgService"]
