"""变更留痕与版本回溯（CU-22 前半，API-67~70，模块 M，F-3.10 / SQ-5 的读侧）。

四个接口都是**纯读**（接口 §2.2 API-67~70 全部 GET）—— 留痕的写入侧散落在
CU-15/16/19 与 CU-22 自己的「审批删除」里，本模块只负责把已写入的留痕**读出来、
还原回去、按字段串起来**。

🔴 三条实现口径（与文档/上游的对应关系）
=======================================

1. **回溯算法不在本文件**（铁律 10）—— :func:`~app.core.privacy.build_snapshot` 是纯函数，
   本文件只做两件 IO 事：把 ORM 行翻译成
   :class:`~app.core.privacy.ChangeRecord`、把结果适配成出参契约。
   于是「档案在 8 月 1 日长什么样」可以在**没有数据库**的情况下被单测钉死。
2. **回溯是近似值必须有标记**（``history_truncated``）—— 单档案留痕超过
   :data:`ChangeService.SNAPSHOT_LOG_LIMIT` 条时只取前 N 条参与重建，
   并在出参里显式声明「不可作为合规证据」。宁可说清不完整，
   ❌ 不要为了「看起来完整」而让接口在大档案上超时或 OOM。
3. **域外访问 40301、不存在 40401**（与 ``tag_service`` / ``dedup_service`` 同口径）——
   档案是行级数据域表，越权一律 40301；但「档案确实不存在」仍是 40401
   （本模块的四个接口都要求先定位到具体档案，与「列表侧查不到别人部门的数据」不同）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_sensitive
from app.core.logging import get_logger
from app.core.privacy import (
    CORRECTABLE_FIELDS,
    TRACKABLE_FIELDS,
    ChangeRecord,
    build_snapshot,
    field_label,
    log_field_value,
    render_value,
)
from app.core.tenant import CurrentPrincipal
from app.dao import tag_dao, talent_dao, user_admin_dao
from app.exceptions import BizError, ErrorCode, NotFoundError, PermissionDeniedError
from app.models.enums import RoleDataScope
from app.models.talent import TalentBase, TalentChangeLog
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.change import (
    CHANGE_FIELD_CODES,
    ChangeFilterRequest,
    ChangeLogOut,
    FieldHistoryOut,
    SnapshotOut,
    SnapshotTagOut,
    SnapshotTalentOut,
    change_source_name_of,
    snapshot_talent_dict,
)
from app.schemas.common import normalize_per_page
from app.schemas.tag import tag_source_name_of
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)


class ChangeService:
    """变更留痕查询与时间点回溯（API-67~70）。"""

    #: API-69 参与快照重建的留痕上限（超出 → ``history_truncated=true``）
    SNAPSHOT_LOG_LIMIT = 5000
    #: API-70 单字段变更链的返回上限（「完整链」在工程上仍要有硬顶）
    FIELD_HISTORY_LIMIT = 1000

    # ==================================================================
    # API-67 变更留痕列表（跨档案）
    # ==================================================================
    @classmethod
    def list_logs(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: ChangeFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChangeLogOut], int, int, int]:
        """全租户留痕列表（时间倒序）。返回 ``(items, total, page, per_page)``。

        ⚠️ **本接口不按 ``data_scope`` 收窄列表**：留痕是「操作流水」而非「人员明细」，
        与审计日志（``AuditService.query`` 的规则三）同一取舍 ——
        矩阵给的是 ADM/HR/AUDIT，AUDIT 的 ``data_scope=4`` 若在此被
        ``require_detail_access`` 拒掉，就会出现「矩阵说 ✓、接口说 40301」的自相矛盾。
        行级收窄在**单档案**接口（API-68/69/70）上做足，那里才是「看某个人的档案」。

        ⚠️ 传了 ``talent_id`` 时**逐条校验该档案可见性**：否则「按 ID 查留痕」
        会变成绕过 API-68 的越权入口（前端只需把列表接口换个参数）。
        """
        if filters.talent_id is not None:
            cls._get_visible_talent(db, principal, int(filters.talent_id))

        current = max(1, int(page))
        size = normalize_per_page(per_page)
        kwargs = cls._filter_kwargs(filters)
        total = talent_dao.count_change_logs(db, principal.tenant_id, **kwargs)
        rows = talent_dao.list_change_logs(
            db, principal.tenant_id, **kwargs, limit=size, offset=(current - 1) * size
        )
        return cls._to_out_list(db, principal.tenant_id, rows), total, current, size

    # ==================================================================
    # API-68 单档案变更链
    # ==================================================================
    @classmethod
    def talent_chain(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        talent_id: int,
        filters: ChangeFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ChangeLogOut], int, int, int, TalentBase]:
        """某档案的变更链（时间倒序）。

        🔴 ``talent_id`` 以**路径参数**为准（``filters.talent_id`` 被忽略）——
        路径与查询串给出两个不同 ID 时，按其中一个执行、把另一个回显给前端，
        是「接口返回 200 但查的不是你要的那份档案」这类最难发现的错误。
        """
        talent = cls._get_visible_talent(db, principal, talent_id)

        current = max(1, int(page))
        size = normalize_per_page(per_page)
        kwargs = cls._filter_kwargs(filters)
        kwargs.pop("talent_id", None)  # 路径参数优先，避免两个来源打架
        kwargs["talent_id"] = int(talent.id)
        total = talent_dao.count_change_logs(db, principal.tenant_id, **kwargs)
        rows = talent_dao.list_change_logs(
            db, principal.tenant_id, **kwargs, limit=size, offset=(current - 1) * size
        )
        return cls._to_out_list(db, principal.tenant_id, rows), total, current, size, talent

    # ==================================================================
    # API-69 时间点快照回溯
    # ==================================================================
    @classmethod
    def snapshot(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        talent_id: int,
        *,
        at: datetime,
        field_codes: list[str] | None = None,
    ) -> SnapshotOut:
        """重建档案在 ``at`` 时刻的字段值（**纯读**，不回写任何数据）。

        :raises NotFoundError: 档案不存在 → 40401
        :raises PermissionDeniedError: 域外访问 → 40301（CR-8）
        :raises BizError(40001): ``at`` 早于档案创建时间（接口 §2.2 API-69 的失败响应示例）
        """
        talent = cls._get_visible_talent(db, principal, talent_id)
        created_at = talent.created_at
        if created_at is not None and at < created_at:
            # 接口原文：「回溯时间点早于档案创建时间」→ 40001 + data.created_at
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "回溯时间点早于档案创建时间",
                {"created_at": created_at.strftime("%Y-%m-%d %H:%M:%S")},
            )

        limit = cls.SNAPSHOT_LOG_LIMIT
        rows = talent_dao.list_all_change_logs_asc(db, principal.tenant_id, int(talent.id), limit=limit)
        records = [cls._to_record(r) for r in rows]
        result = build_snapshot(
            current_values=cls._current_values(talent),
            records=records,
            at=at,
            created_at=created_at or at,
            field_codes=field_codes,
        )

        # ⚠️ 标签**不进留痕**（TBL-22 无标签行）：快照里的标签是**当前值**。
        #    这是与「字段值」最大的差别，出参注释里必须说清，否则会被当成「标签也回溯了」。
        talent_dict = snapshot_talent_dict(
            result.values, talent_id=int(talent.id), version_no=cls._int_or_none(talent.version_no)
        )

        # 🔴 查阅历史快照本身写审计（接口 §2.2 API-69：AUDIT 仅可查、不可导出）——
        #    回溯是合规证据，**谁在什么时候看了某人的历史**也是合规事实的一部分。
        #    与 API-29（导出审计）同源：越是「只读」的敏感动作，越需要留下读的痕迹。
        AuditService.record(
            AuditAction.CHANGE_SNAPSHOT_VIEW,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.CHANGE_LOG,
            target_id=talent.id,
            after={
                "at": at.strftime("%Y-%m-%d %H:%M:%S"),
                "field_codes": list(field_codes) if field_codes else None,
                "changes_after": result.changes_after,
                "untracked_fields": result.untracked_fields,
                "history_truncated": len(rows) >= limit,
            },
        )
        return SnapshotOut(
            talent_id=talent.id,
            at=at,
            snapshot_from_change_log_id=result.snapshot_from_change_log_id,
            talent=SnapshotTalentOut(**talent_dict),
            tags=cls._snapshot_tags(db, principal.tenant_id, int(talent.id)),
            effective_from=result.effective_from,
            effective_to=result.effective_to,
            changes_after=result.changes_after,
            untracked_fields=result.untracked_fields,
            restored_fields=result.restored_fields,
            history_truncated=len(rows) >= limit,
        )

    # ==================================================================
    # API-70 单字段完整变更链
    # ==================================================================
    @classmethod
    def field_history(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        talent_id: int,
        field_code: str,
    ) -> FieldHistoryOut:
        """某档案某字段的完整变更链（时间倒序）+ 字段当前值。

        ``field_code`` 用**受控词表**校验（同 API-67 的筛选口径）：
        留痕是自由文本列，拼错一个字母会「查询成功返回 0 条」——
        这是本项目已登记过的最难发现的一类问题，故在此 fail-loud。
        """
        if field_code not in CHANGE_FIELD_CODES:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"未知的 field_code={field_code!r}；合法取值：{', '.join(CHANGE_FIELD_CODES)}",
                {"field_code": field_code, "allowed": list(CHANGE_FIELD_CODES)},
            )
        talent = cls._get_visible_talent(db, principal, talent_id)

        rows = talent_dao.list_change_logs(
            db,
            principal.tenant_id,
            talent_id=int(talent.id),
            field_name=field_code,
            limit=cls.FIELD_HISTORY_LIMIT,
            offset=0,
        )
        total = talent_dao.count_change_logs(
            db, principal.tenant_id, talent_id=int(talent.id), field_name=field_code
        )
        return FieldHistoryOut(
            talent_id=talent.id,
            talent_name=talent.name,
            field_code=field_code,
            field_label=field_label(field_code),
            current_value=cls._current_value_for(talent, field_code),
            total=total,
            items=cls._to_out_list(db, principal.tenant_id, rows),
        )

    # ==================================================================
    # 内部：翻译与适配
    # ==================================================================
    @staticmethod
    def _filter_kwargs(filters: ChangeFilterRequest) -> dict[str, Any]:
        """把请求模型摊成 DAO 关键字参数（**唯一**的字段映射点）。

        单独抽出来是为了让 ``count`` 与 ``list`` 不可能用上不同的条件集 ——
        两者的参数由**同一行代码**产出（同 ``audit_service._filter_kwargs``）。
        """
        return {
            "talent_id": int(filters.talent_id) if filters.talent_id is not None else None,
            "field_name": filters.field_name,
            "change_source": filters.change_source,
            "operator_id": int(filters.operator_id) if filters.operator_id is not None else None,
            "task_id": int(filters.task_id) if filters.task_id is not None else None,
            "start_time": filters.start_time,
            "end_time": filters.end_time,
        }

    @staticmethod
    def _to_record(row: TalentChangeLog) -> ChangeRecord:
        """ORM 行 → :class:`~app.core.privacy.ChangeRecord`（core 不依赖 ORM）。"""
        return ChangeRecord(
            log_id=int(row.id),
            created_at=row.created_at,
            field_name=row.field_name,
            before_value=row.before_value,
            after_value=row.after_value,
            change_source=int(row.change_source),
            operator_id=int(row.operator_id) if row.operator_id is not None else None,
        )

    @staticmethod
    def _current_value_for(talent: TalentBase, field_code: str) -> str | None:
        """字段当前值（**与链上值的形态一致**，才能逐行对照）。

        🔴 两个坑（都在 CU-22 引入 ``phone`` / ``email`` 之后才暴露）：
        1. ``phone`` / ``email`` 在 ``talent_base`` 里**没有同名列** ——
           直接 ``getattr(talent, "phone")`` 会静默拿到 ``None``；
           真实列是 ``phone_enc`` / ``email_enc``（AES-GCM 密文，需先解密）；
        2. 留痕里存的是**脱敏形态**（见 :func:`~app.core.privacy.log_field_value`），
           若这里下发明文，界面就会出现「当前值 13812345678 / 变更前 138****5678」
           这种看起来像数据损坏的对照。
        """
        field_def = CORRECTABLE_FIELDS.get(field_code)
        if field_def is None:
            return render_value(getattr(talent, field_code, None))
        if field_def.encrypted:
            plain = decrypt_sensitive(getattr(talent, field_def.column, None))
            return log_field_value(field_code, plain)
        return log_field_value(field_code, getattr(talent, field_def.column, None))

    @classmethod
    def _to_out_list(
        cls, db: Session, tenant_id: int, rows: list[TalentChangeLog]
    ) -> list[ChangeLogOut]:
        """批量补齐 ``talent_name`` / ``operator_name``（**一次 IN 查询**，不做 N+1）。

        两个反查都**不过滤软删**：留痕必然引用「已删档案 / 已删用户」
        （删除本身就是一次变更），过滤掉会让最需要看清的那几行变空白。
        """
        names = talent_dao.list_talent_names_by_ids(db, tenant_id, [int(r.talent_id) for r in rows])
        operators = cls._operator_names(db, tenant_id, rows)
        return [
            ChangeLogOut(
                id=r.id,
                talent_id=r.talent_id,
                talent_name=names.get(int(r.talent_id)),
                change_source=int(r.change_source),
                change_source_name=change_source_name_of(int(r.change_source)),
                field_name=r.field_name,
                field_label=field_label(r.field_name),
                before_value=r.before_value,
                after_value=r.after_value,
                snapshot_ref=r.snapshot_ref,
                operator_id=r.operator_id,
                operator_name=(
                    operators.get(int(r.operator_id)) if r.operator_id is not None else None
                ),
                task_id=r.task_id,
                created_at=r.created_at,
            )
            for r in rows
        ]

    @staticmethod
    def _operator_names(
        db: Session, tenant_id: int, rows: list[TalentChangeLog]
    ) -> dict[int, str]:
        ids = {int(r.operator_id) for r in rows if r.operator_id is not None}
        if not ids:
            return {}
        return user_admin_dao.list_usernames_by_ids(db, tenant_id, sorted(ids))

    @staticmethod
    def _current_values(talent: TalentBase) -> dict[str, Any]:
        """摘出 :data:`TRACKABLE_FIELDS` 的**当前值**（回溯的起点）。

        🔴 只摘可回溯字段：``phone_enc`` / ``id_card_enc`` 之类的密文列
        **永不进入回溯**（它们既不在留痕里，也不该出现在快照出参中 ——
        证件号连本人视角都只下发脱敏形态，见 ``schemas/privacy.py`` 口径 ③）。
        """
        return {code: getattr(talent, code, None) for code in TRACKABLE_FIELDS}

    @staticmethod
    def _snapshot_tags(db: Session, tenant_id: int, talent_id: int) -> list[SnapshotTagOut]:
        """快照中的标签（⚠️ **当前值**，不是 ``at`` 时刻的值 —— 标签不进 TBL-22）。

        ⚠️ 停用标签的历史引用照常返回、词表名查不到时用**编码兜底**
        （同 ``tag_service.talent_tags``）：标签被停用不等于「这份档案没有这个标签」。
        """
        rels = tag_dao.list_talent_tags(db, tenant_id, talent_id)
        if not rels:
            return []
        all_rows = tag_dao.list_tags(db, tenant_id, limit=10_000)
        name_map = {str(r.tag_code): r.tag_name for r in all_rows}
        return [
            SnapshotTagOut(
                tag_code=str(r.tag_code),
                tag_name=name_map.get(str(r.tag_code), str(r.tag_code)),
                source=int(r.source),
                source_name=tag_source_name_of(int(r.source)),
            )
            for r in rels
        ]

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):  # pragma: no cover - 正常不会发生
            return None

    # ==================================================================
    # 内部：可见性
    # ==================================================================
    @staticmethod
    def _dept_ids(db: Session, principal: CurrentPrincipal) -> tuple[int, ...]:
        """本部门子树（含自身）。``principal.dept_ids`` 由登录时回填，缺失则现算。"""
        if principal.dept_ids:
            return tuple(int(i) for i in principal.dept_ids)
        return OrgScopeService.resolve_dept_ids(db, principal.tenant_id, principal.org_id)

    @classmethod
    def _can_access_talent(
        cls, db: Session, principal: CurrentPrincipal, talent: TalentBase
    ) -> bool:
        """行级数据域判定（**fail-closed**：未知 ``data_scope`` 一律不可见）。"""
        scope = int(principal.data_scope)
        if scope == int(RoleDataScope.ALL):
            return True
        if scope == int(RoleDataScope.OWN_DEPT):
            if talent.org_id is None:
                # 档案未归部门 → 本部门范围看不到它（不给「无归属=人人可见」的口子）
                return False
            return int(talent.org_id) in cls._dept_ids(db, principal)
        if scope == int(RoleDataScope.SELF):
            return talent.user_id is not None and int(talent.user_id) == int(principal.user_id)
        # STATS_ONLY（仅统计值）与未知取值：明细一律不可见
        return False

    @classmethod
    def _get_visible_talent(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int
    ) -> TalentBase:
        talent = talent_dao.get_talent(db, principal.tenant_id, int(talent_id))
        if talent is None:
            raise NotFoundError("档案不存在")
        if not cls._can_access_talent(db, principal, talent):
            # 档案是**有行级数据域**的表：域外访问统一 40301（CR-8，防资源探测）
            raise PermissionDeniedError("无权限访问该档案")
        return talent


__all__ = ["ChangeService"]
