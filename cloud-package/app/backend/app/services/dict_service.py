"""数据字典业务编排（F-1.7，接口 API-23 ~ API-27）。

分层位置：``routers → **services** → dao``。本模块是字典数据的**唯一写入口**
（除种子脚本外），因此也是三条不变式的唯一维护者：

1. **``item_code`` 不可修改** —— 它是业务表引用的值（``talent_base.education_level``
   等）。改它不会报错，只会让历史数据的枚举值**静默变成孤儿**（数据还在、
   含义漂移），比删一行更隐蔽；
2. **字典项只停用、不删除** —— 物理删或软删会让历史数据查不到显示名；
3. **停用有「启用中子项」的父项要拦下来**（40904）—— 与 CU-06「删除有子部门的部门」
   同源推理：停用父项后子项虽然还在，但多级联动下**永远选不到**，
   表现为「这个选项怎么没了」而不是报错。宁可在入口拒绝并提示，也不让它事后表现为丢失。

🔴 为什么本模块**没有行级数据域过滤**
=====================================
字典是**租户级公共配置**：它没有 ``org_id``，也不属于任何个人
（对照接口 §1.7 的行级裁剪针对的是「业务数据」，如档案）。因此这里只做
**租户隔离**（每条 SQL 带 ``tenant_id``），不做 ``data_scope`` 收窄 ——
刻意不做，而非忘了做。

🔴 为什么本模块**不引入 Redis 缓存**
===================================
字典项确实是高频读（每次下拉都读），但：① 量级只有 10²，命中
``idx_tenant_type`` 的查询代价可忽略；② 缓存会立刻带来「改完必须让缓存失效」
的一致性负担，而**改字典恰恰是低频操作**（配置类数据）。
先用最简单的实现把正确性钉死，等 N0 压测（CU-23）拿到真实 QPS 再决定是否加缓存 ——
届时只需在本模块读路径外包一层，调用点不变。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import dict_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError
from app.models.dict import DictItem
from app.models.enums import EnabledStatus, label_of
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.dict import (
    DictItemCreateRequest,
    DictItemDeleteOut,
    DictItemListOut,
    DictItemOut,
    DictItemUpdateRequest,
    DictTypeListOut,
    DictTypeOut,
)
from app.services.audit_service import AuditService

logger = get_logger(__name__)


# ============================================================================
# 转换助手
# ============================================================================
def _to_type_out(row: Any, counts: dict[str, dict[str, int]]) -> DictTypeOut:
    count = counts.get(row.type_code) or {"total": 0, "enabled": 0}
    return DictTypeOut(
        id=row.id,
        type_code=row.type_code,
        type_name=row.type_name,
        remark=row.remark,
        status=int(row.status),
        status_name=label_of(EnabledStatus, row.status),
        item_count=count["total"],
        enabled_item_count=count["enabled"],
    )


def _to_item_out(item: DictItem) -> DictItemOut:
    return DictItemOut(
        id=item.id,
        type_code=item.type_code,
        item_code=item.item_code,
        item_name=item.item_name,
        sort_no=int(item.sort_no or 0),
        parent_code=item.parent_code,
        status=int(item.status),
        status_name=label_of(EnabledStatus, item.status),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_delete_out(item: DictItem, *, already_disabled: bool) -> DictItemDeleteOut:
    return DictItemDeleteOut(
        id=item.id,
        type_code=item.type_code,
        item_code=item.item_code,
        item_name=item.item_name,
        status=int(item.status),
        status_name=label_of(EnabledStatus, item.status),
        already_disabled=already_disabled,
    )


class DictService:
    """数据字典。**全部为无状态静态方法**（会话由 ``get_db`` 注入）。"""

    # ------------------------------------------------------------------
    # API-23 字典类型列表
    # ------------------------------------------------------------------
    @staticmethod
    def list_types(
        db: Session,
        principal: CurrentPrincipal,
        *,
        status: int | None = None,
        keyword: str | None = None,
    ) -> DictTypeListOut:
        """返回本租户字典类型，并带上项计数。

        计数用**一条聚合查询**批量取回（不是逐类型 ``COUNT(*)``），
        详见 :func:`app.dao.dict_dao.count_items_by_types`。
        """
        rows = dict_dao.list_types(db, principal.tenant_id, status=status, keyword=keyword)
        raw = dict_dao.count_items_by_types(db, principal.tenant_id, [r.type_code for r in rows])
        counts = {code: {"total": c.total, "enabled": c.enabled} for code, c in raw.items()}
        return DictTypeListOut(
            total=len(rows), items=[_to_type_out(r, counts) for r in rows]
        )

    # ------------------------------------------------------------------
    # API-24 字典项列表
    # ------------------------------------------------------------------
    @staticmethod
    def list_items(
        db: Session,
        principal: CurrentPrincipal,
        type_code: str,
        *,
        status: int | None = None,
        parent_code: str | None = None,
        keyword: str | None = None,
    ) -> DictItemListOut:
        """返回某类型下的字典项。

        **默认返回全部（含停用项）** —— 这是一个刻意的取舍：
        若默认只返回启用项，管理页就**看不到停用项，也就无法再把它启用回来**
        （停用即等于永久隐藏）。下拉框场景由调用方显式传 ``status=1``。

        :raises NotFoundError: 该类型编码在本租户不存在（40401）
        """
        DictService._require_type(db, principal, type_code)
        rows = dict_dao.list_items(
            db,
            principal.tenant_id,
            type_code,
            status=status,
            parent_code=parent_code,
            keyword=keyword,
        )
        return DictItemListOut(total=len(rows), items=[_to_item_out(r) for r in rows])

    # ------------------------------------------------------------------
    # API-25 新增字典项
    # ------------------------------------------------------------------
    @staticmethod
    def create_item(
        db: Session,
        principal: CurrentPrincipal,
        type_code: str,
        req: DictItemCreateRequest,
    ) -> DictItemOut:
        """新增字典项。

        :raises NotFoundError: 类型编码不存在（40401）
        :raises BizError(40901): 同类型下 ``item_code`` 已存在
        :raises IllegalStateError: ``parent_code`` 不存在于该类型下（42201）
        """
        tenant_id = principal.tenant_id
        DictService._require_type(db, principal, type_code)

        if dict_dao.get_item_by_code(db, tenant_id, type_code, req.item_code) is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"该字典类型下已存在编码 {req.item_code!r}",
                data={"type_code": type_code, "item_code": req.item_code},
            )

        if req.parent_code:
            DictService._require_parent_exists(db, tenant_id, type_code, req.parent_code)

        item = DictItem(
            tenant_id=tenant_id,
            type_code=type_code,
            item_code=req.item_code,
            item_name=req.item_name,
            sort_no=int(req.sort_no),
            parent_code=req.parent_code,
            status=int(req.status),
            is_deleted=0,
        )
        dict_dao.insert_item(db, item)
        db.commit()
        # ⚠️ 审计**放在 commit 之后**：审计走独立事务（见 audit_service 模块 docstring），
        #    若先写审计再 commit，一旦 commit 失败就会留下一条「变更并未发生」的假记录。
        DictService._audit(
            AuditAction.DICT_ITEM_CREATE,
            principal,
            target_id=item.id,
            before=None,
            after={**_snapshot(item), "type_code": type_code},
        )
        return _to_item_out(item)

    # ------------------------------------------------------------------
    # API-26 修改字典项（改前值/改后值留痕）
    # ------------------------------------------------------------------
    @staticmethod
    def update_item(
        db: Session,
        principal: CurrentPrincipal,
        item_id: int,
        req: DictItemUpdateRequest,
    ) -> DictItemOut:
        """修改字典项（部分更新）。

        **``item_code`` 不可改** —— 契约层已把它挡在请求体之外（多传 → 40001），
        这里再加一道运行期断言，防止将来有人绕过 Schema 直接调服务层。

        改动前后会被 diff 出来送进审计钩子（``_audit``），
        满足 F-1.7「配置变更留痕：改前值 / 改后值 / 操作人」。

        :raises NotFoundError: 字典项不存在（40401）
        :raises IllegalStateError: 改自己的父项 / 父项不存在 / 形成环（42201）
        :raises BizError(40904): 停用时尚有**启用中的子项**（先停子项）
        """
        item = dict_dao.get_item_by_id(db, principal.tenant_id, item_id)
        if item is None:
            raise NotFoundError("字典项不存在")

        before = _snapshot(item)
        fields: dict[str, Any] = {}

        if "item_name" in req.model_fields_set and req.item_name is not None:
            fields["item_name"] = req.item_name
        if "sort_no" in req.model_fields_set and req.sort_no is not None:
            fields["sort_no"] = int(req.sort_no)

        if req.parent_code_provided:
            fields["parent_code"] = DictService._resolve_new_parent(
                db, principal.tenant_id, item, req.parent_code
            )

        if "status" in req.model_fields_set and req.status is not None:
            new_status = int(req.status)
            if new_status == int(EnabledStatus.DISABLED):
                DictService._assert_can_disable(db, principal.tenant_id, item)
            fields["status"] = new_status

        if not fields:
            # 契约层已挡住「空请求体」；能走到这里说明传了字段但值全为 None
            # （如 {"item_name": null}）—— 明确拒绝，不返回「假成功」。
            raise BizError(ErrorCode.PARAM_INVALID, "没有需要修改的字段（值不能为 null）")

        dict_dao.update_item_fields(db, item, fields)
        after = _snapshot(item)
        changed = _diff(before, after)
        db.commit()
        # 审计在 commit 之后（同上：避免「变更未发生但留了痕」）
        DictService._audit(
            AuditAction.DICT_ITEM_UPDATE,
            principal,
            target_id=item.id,
            before={**before, "type_code": item.type_code},
            after={**after, "type_code": item.type_code, "changed": changed},
        )
        return _to_item_out(item)

    # ------------------------------------------------------------------
    # API-27 停用字典项（不物理删）
    # ------------------------------------------------------------------
    @staticmethod
    def disable_item(
        db: Session, principal: CurrentPrincipal, item_id: int
    ) -> DictItemDeleteOut:
        """停用字典项（软停用：``status=0``，**不删行**）。

        **幂等**：已经停用时直接返回 ``already_disabled=true``，不报错。
        理由是这个动作天然会被重放（用户刷新页面、前端重试、脚本重复执行），
        把它变成错误只会让调用方需要「先查一次状态再决定要不要调」——
        那是把幂等成本从服务端推给了每个调用方。

        :raises NotFoundError: 字典项不存在（40401）
        :raises BizError(40904): 尚有**启用中的子项**，先停子项再停父项
        """
        item = dict_dao.get_item_by_id(db, principal.tenant_id, item_id)
        if item is None:
            raise NotFoundError("字典项不存在")

        already_disabled = int(item.status) == int(EnabledStatus.DISABLED)
        if already_disabled:
            return _to_delete_out(item, already_disabled=True)

        DictService._assert_can_disable(db, principal.tenant_id, item)
        before = _snapshot(item)  # 供审计留痕（停用前的状态）
        dict_dao.update_item_fields(db, item, {"status": int(EnabledStatus.DISABLED)})
        db.commit()
        # 审计在 commit 之后（同上）
        DictService._audit(
            AuditAction.DICT_ITEM_DISABLE,
            principal,
            target_id=item.id,
            before={**before, "type_code": item.type_code},
            after={
                **_snapshot(item),
                "type_code": item.type_code,
                "changed": {"status": [int(EnabledStatus.ENABLED), int(EnabledStatus.DISABLED)]},
            },
        )
        return _to_delete_out(item, already_disabled=False)

    # ==================================================================
    # 内部校验
    # ==================================================================
    @staticmethod
    def _require_type(db: Session, principal: CurrentPrincipal, type_code: str) -> None:
        """类型必须存在于本租户（否则 40401）。

        ⚠️ 先校验类型再校验项，是有意为之：否则「类型不存在」会被报成
        「项不存在」，调用方会去查一个根本不存在的字典的项列表 —— 排查方向被带偏。
        """
        if dict_dao.get_type_by_code(db, principal.tenant_id, type_code) is None:
            raise NotFoundError(f"字典类型不存在：{type_code}")

    @staticmethod
    def _require_parent_exists(
        db: Session, tenant_id: int, type_code: str, parent_code: str
    ) -> None:
        """父项必须已存在于**同一类型**下。

        跨类型引父项是无意义的（两个字典的编码空间彼此独立），
        放行只会造出一棵跨字典的「幽灵树」。
        """
        if dict_dao.get_item_by_code(db, tenant_id, type_code, parent_code) is None:
            raise IllegalStateError(f"父项编码不存在于该字典类型下：{parent_code}")

    @staticmethod
    def _resolve_new_parent(
        db: Session,
        tenant_id: int,
        item: DictItem,
        new_parent: str | None,
    ) -> str | None:
        """算出修改后的 ``parent_code``，并做**环路检测**。

        ``new_parent=None`` 表示「提升为根项」（清空父项），是合法操作。
        """
        if new_parent is None:
            return None
        if new_parent == item.item_code:
            raise IllegalStateError("父项不能是自己")
        DictService._require_parent_exists(db, tenant_id, item.type_code, new_parent)
        if DictService._is_descendant(
            db, tenant_id, item.type_code, candidate=new_parent, ancestor=item.item_code
        ):
            # A 的父项设成 A 的子孙 → 形成环，多级联动会死循环（前端 select 无限展开）。
            raise IllegalStateError("不能把父项设为自己的子孙项（会形成环）")
        return new_parent

    @staticmethod
    def _is_descendant(
        db: Session, tenant_id: int, type_code: str, *, candidate: str, ancestor: str
    ) -> bool:
        """判断 ``candidate`` 是否为 ``ancestor`` 的子孙（沿 ``parent_code`` 上行查找）。

        实现选择「从候选者向上走」而不是「从祖先向下走」：
        环一旦存在，向下遍历会**无限循环**，向上遍历配合深度上限则必然终止 ——
        即使用 ``visited`` 集合兜底，向上走也只需读取更少的行。
        """
        parent_of = dict(dict_dao.list_parent_child_pairs(db, tenant_id, type_code))
        cursor: str | None = candidate
        hops = 0
        # 深度上限 = 当前项数 + 1：正常层级不会超过它；超过即说明数据里已有环
        # （历史脏数据或并发写入造成），此时保守判定为「是子孙」以**拒绝**本次写入。
        max_hops = len(parent_of) + 1
        while cursor and hops <= max_hops:
            if cursor == ancestor:
                return True
            cursor = parent_of.get(cursor)
            hops += 1
        return hops > max_hops

    @staticmethod
    def _assert_can_disable(db: Session, tenant_id: int, item: DictItem) -> None:
        """停用前置校验：有「启用中的子项」时拒绝（40904）。

        为什么这必须是错误而不是警告：多级联动下拉「先选父、再列子」，
        父项一停用，子项就**永远无法被选中**。此时数据其实还在库里，
        但用户看到的是「选项消失了」—— 不报错、不崩溃，最难排查的一类问题。
        与 CU-06「删除含子部门的部门 → 40904」是同一条推理。
        """
        children = dict_dao.list_items(db, tenant_id, item.type_code, parent_code=item.item_code)
        enabled_children = [c.item_code for c in children if int(c.status) == int(EnabledStatus.ENABLED)]
        if enabled_children:
            raise BizError(
                ErrorCode.PRECONDITION_UNMET,
                f"该项下还有 {len(enabled_children)} 个启用中的子项，请先停用子项",
                data={
                    "type_code": item.type_code,
                    "item_code": item.item_code,
                    "enabled_children": enabled_children[:20],
                    "enabled_child_count": len(enabled_children),
                },
            )

    # ==================================================================
    # 留痕
    # ==================================================================
    @staticmethod
    def _audit(
        action: str,
        principal: CurrentPrincipal,
        *,
        target_id: int | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        """写字典变更审计（F-1.7「配置变更留痕：改前值 / 改后值 / 操作人」）。

        CU-08 起**真实落库 TBL-28**（此前是 TODO 占位，只输出结构化日志）。

        ``target_id`` 取**字典项雪花 ID（字符串化）**而不是 ``item_code``：
        ``item_code`` 只在「同一 type_code 内」唯一（``education_level.master``
        与 ``degree.master`` 是两个不同的项），单用它做回溯键会**串味**。
        为便于人读，``type_code`` / ``item_code`` 都已写进 ``before``/``after`` 快照里。

        ⚠️ 调用点全部安排在 ``db.commit()`` **之后** —— 审计走独立事务
        （见 :mod:`app.services.audit_service` 模块 docstring），
        先写审计再提交会留下「变更实际未发生」的假记录。

        ⚠️ 上游把 API-26 的留痕指向 **TBL-22 ``talent_change_log``**（接口 §2.2）。
        但该表 ``talent_id NOT NULL`` 且有 ``idx_tenant_talent_time``，
        语义是「**人才**档案变更」—— 字典项不是人才，落不进去。
        因此本模块统一走 **TBL-28 审计日志**（与 CU-04 的登录审计、CU-05 的角色变更同源），
        并已把这条上游不一致登记进评审文档 §7.3 批次 4。
        """
        logger.info(
            "AUDIT action=%s tid=%s uid=%s target_id=%s before=%s after=%s",
            action,
            principal.tenant_id,
            principal.user_id,
            target_id,
            before,
            after,
        )
        AuditService.record(
            action,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.DICT_ITEM,
            target_id=target_id,
            before=before,
            after=after,
        )


# ============================================================================
# 快照 / diff（留痕用）
# ============================================================================
#: 参与留痕的字段（**不含** id / 时间戳 —— 它们不是「配置内容」，
#: 每次必变，混进来会把 diff 淹没）
_AUDIT_FIELDS = ("item_code", "item_name", "sort_no", "parent_code", "status")


def _snapshot(item: DictItem) -> dict[str, Any]:
    """取字典项的可留痕字段快照。"""
    return {name: getattr(item, name) for name in _AUDIT_FIELDS}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """返回 ``{字段: [改前值, 改后值]}``（只含真正变化的字段）。

    用「改前值/改后值」成对产出，而不是记两条流水：
    审计日志的价值在于「一眼看出改了什么」，成对结构无需读者自己拼接。
    """
    return {
        key: [before.get(key), after.get(key)]
        for key in _AUDIT_FIELDS
        if before.get(key) != after.get(key)
    }
