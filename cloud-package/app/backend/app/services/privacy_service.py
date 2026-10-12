"""个人权利响应（CU-22 后半，API-71~80，模块 N，F-3.11 / SM-3 / SQ-5）。

F-3.11 的四项权利在本文件落地：**查阅 / 更正 / 删除 / 导出**。
接口 §2.2 只给了一行摘要（方法 / 路径 / 权限 / 表），本文件与
``schemas/privacy.py``、``core/privacy.py`` 三者共同构成这十个接口的
**事实来源**（同 CU-05/07/08 的既有做法）。

🔴 五条贯穿全文件的决策（每一条都有可验证的后果）
=================================================

1. **本人视角不接受前端传 ``talent_id`` 作为定位依据**（API-71）——
   定位只走 ``user_id`` 关联（:meth:`PrivacyService._my_talent`）。
   带 ``talent_id`` 的三个写接口（72/75/78）**逐条校验其 ``user_id`` 等于本人**，
   不符一律 **40301**（:meth:`_assert_own`）。否则「本人权利」入口会退化成
   「传一个别人的 ID 就穿透」—— 这是本模块**唯一**可能造成跨人数据泄露的路径。

2. **API-77 的五步编排顺序不可调换**（SQ-5 / CR-18）——
   合并是「有快照可还原」的，故合并可以「先 MySQL 后向量」；
   **删除没有还原依据**，故必须「**先删向量、后软删 MySQL**」：
   反过来的话，Milvus 删除失败时档案已经软删了，而「已向本人承诺删除」
   与「换个入口还能检索到」同时成立 —— 比不删更糟。

3. **审批删除的全部数据库改动在**一个事务**内完成，失败即 ``rollback``**——
   步骤 ③ 要求「申请保持待处理」。若把「置处理中」单独提交，失败后还得再写一次
   「改回待处理」，多一次写就多一次可能失败的机会。放在同一事务里，
   ``rollback`` 天然把状态还原为待处理，且**并发双审仍被挡住**：
   两个 ADM 同时审批时，后者会在行锁上排队，待前者提交后条件更新影响行数为 0 → 40902。

4. **留痕里不写 PII 明文**（:meth:`_log_value`）——
   ``talent_change_log`` 是 ADM/HR/AUDIT 都能读的表。手机号/邮箱的更正若把明文写进留痕，
   等于把 PII 从加密列「搬」到明文列，AES 白做。故加密字段写**脱敏形态**。

5. **API-78 必须自动完成（``status=1``），不能停在待处理**——
   接口集里**没有**「审批导出」的接口（只有 API-74 处理更正、API-77 审批删除）。
   若导出停在待处理，它将**永远无法变成已通过**，API-79 也就永远下载不了 ——
   一条走不通的路比一条缺失的路更难排查。故提交即生成（SM-3：「导出 = 数据包已生成」）。
   代价：导出的处理时长恒为 0，会把跨类型平均处理时长拉低 →
   已在 API-80 的 ``summary.by_type`` 里**分类型**给出（这正是分类型存在的理由）。

⚠️ 本模块**不做**的两件事（均已登记为遗留）
==========================================

- **无站内信表**：通知以 ``privacy_request.notify_message`` + ``notified_at``
  （消息快照）+ 审计日志双落点表达。SM-3 红线「处理结果必须通知本人」在
  **数据层成立**（消息正文已生成并留档），但本人**不会收到推送** ——
  站内信表待需求确认后另立单元。
- **导出只有 JSON**：一期无 MinIO、无 PDF 生成库，故契约上不宣称支持 PDF。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import (
    ContactHashSaltError,
    SensitiveFieldError,
    decrypt_sensitive,
    encrypt_sensitive,
    hash_contact,
    mask_email,
    mask_encrypted_id_card,
    mask_phone,
)
from app.core.logging import get_logger
from app.core.privacy import (
    CORRECTABLE_FIELD_CODES,
    CORRECTABLE_FIELDS,
    CORRECTION_EXCLUDED,
    FIELD_LABELS,
    OPEN_STATUSES,
    build_notify_message,
    build_request_no,
    coerce_corrected_value,
    get_correctable,
    is_overdue,
    log_field_value,
    render_value,
    summarize_requests,
    waited_hours,
)
from app.core.tenant import CurrentPrincipal
from app.dao import privacy_dao, tag_dao, talent_dao, user_admin_dao
from app.exceptions import (
    BizError,
    ConflictError,
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
)
from app.models.enums import (
    ChangeSource,
    Gender,
    ParseReviewStatus,
    PrivacyRequestStatus,
    PrivacyRequestType,
    TalentDataStatus,
    label_of,
)
from app.models.privacy import PrivacyRequest
from app.models.talent import TalentBase, TalentChangeLog
from app.retrieval import MilvusRetrievalProvider, get_retrieval_provider
from app.retrieval.profile import build_profile_text
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.change import change_source_name_of
from app.schemas.common import DATETIME_FORMAT, normalize_per_page
from app.schemas.privacy import (
    CorrectionResolveOut,
    CorrectionResolveRequest,
    CorrectionSubmitOut,
    CorrectionSubmitRequest,
    DeletionApproveOut,
    DeletionApproveRequest,
    DeletionSubmitOut,
    DeletionSubmitRequest,
    ExportDownloadOut,
    ExportSubmitOut,
    ExportSubmitRequest,
    PrivacyLedgerOut,
    PrivacyRequestFilter,
    PrivacyRequestOut,
    SelfFieldOut,
    SelfProfileOut,
    SelfTagOut,
    request_status_name_of,
    request_type_name_of,
)
from app.schemas.review import review_status_name_of
from app.schemas.tag import tag_source_name_of
from app.services.audit_service import AuditService

logger = get_logger(__name__)

#: 更正申请的处理角色（ADM / HR）—— 见 :meth:`PrivacyService._require_manager`
MANAGER_ROLES: tuple[str, ...] = ("HR",)

#: API-80 台账统计的扫描上限：超时判定要按**每行自己的** ``sla_hours`` 逐行比较，
#: 无法在 SQL 里表达 → 只能取回后在内存里算。超限时 ``summary.truncated=true``。
SLA_SCAN_LIMIT = 5000

#: 本人查阅视角的字段展示序（可更正白名单 + 证件号）。
#: 🔴 证件号**只以脱敏形态**出现（口径 ③）：查阅权也受「最小必要」约束。
SELF_VIEW_FIELD_CODES: tuple[str, ...] = (*CORRECTABLE_FIELD_CODES, "id_card")


class PrivacyService:
    """个人权利响应（API-71~80）。"""

    # ==================================================================
    # API-71 查阅本人档案
    # ==================================================================
    @classmethod
    def self_profile(cls, db: Session, principal: CurrentPrincipal) -> SelfProfileOut:
        """返回**本人**档案全字段 + 每个字段的来源与置信度（F-3.11 查阅权）。

        🔴 定位只走 ``user_id`` 关联，接口**没有** ``talent_id`` 入参 ——
        见模块 docstring 口径 1。

        :raises NotFoundError: 当前账号未关联任何档案（40401）
        """
        talent = cls._my_talent(db, principal)
        fields, tags = cls._profile_fields(db, principal.tenant_id, talent)
        change_count = talent_dao.count_change_logs(
            db, principal.tenant_id, talent_id=int(talent.id)
        )

        open_rows = privacy_dao.list_requests(
            db,
            principal.tenant_id,
            request_type=int(PrivacyRequestType.CORRECTION),
            talent_id=int(talent.id),
            limit=50,
        )
        pending = [r.id for r in open_rows if int(r.status) in OPEN_STATUSES]

        AuditService.record(
            AuditAction.PRIVACY_SELF_VIEW,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            target_id=talent.id,
            after={"field_count": len(fields), "tag_count": len(tags), "change_count": change_count},
        )
        return SelfProfileOut(
            talent_id=talent.id,
            name=talent.name,
            gender=int(talent.gender) if talent.gender is not None else None,
            gender_name=cls._gender_name(talent.gender),
            birth_date=talent.birth_date.isoformat() if talent.birth_date else None,
            education_level=talent.education_level,
            school=talent.school,
            major=talent.major,
            work_years=int(talent.work_years) if talent.work_years is not None else None,
            # 手机号 / 邮箱：**本人可见明文**（schema 注释已声明）；
            # 证件号除外（口径 ③：明文核验走线下）
            phone=decrypt_sensitive(talent.phone_enc),
            email=decrypt_sensitive(talent.email_enc),
            id_card_masked=mask_encrypted_id_card(talent.id_card_enc),
            data_status=int(talent.data_status) if talent.data_status is not None else None,
            data_status_name=cls._data_status_name(talent.data_status),
            version_no=int(talent.version_no) if talent.version_no is not None else None,
            fields=fields,
            tags=tags,
            work_exps=cls._work_exps(db, principal.tenant_id, int(talent.id)),
            project_exps=cls._project_exps(db, principal.tenant_id, int(talent.id)),
            pending_corrections=pending,
            correction_field_codes=list(CORRECTABLE_FIELD_CODES),
            correction_excluded_reason=dict(CORRECTION_EXCLUDED),
            change_count=change_count,
        )

    # ==================================================================
    # API-72 提交更正申请
    # ==================================================================
    @classmethod
    def submit_correction(
        cls, db: Session, principal: CurrentPrincipal, req: CorrectionSubmitRequest
    ) -> CorrectionSubmitOut:
        """提交更正申请（入 F-3.3 复核队列，由 HR 在 API-74 处理）。

        :raises PermissionDeniedError: 档案不属于本人 → 40301（口径 1）
        :raises ConflictError(40902): ``current_value`` 与库中不一致（**乐观锁**）
        :raises BizError(40901): 同一字段已有待处理的更正申请
        """
        talent = cls._assert_own(db, principal, int(req.talent_id))
        cls._assert_correctable_now(talent)
        field_def = get_correctable(req.field_code)
        if field_def is None:  # 契约层已拦，这里只是兜底（防服务被直接调用）
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"字段 {req.field_code!r} 不在可更正白名单内",
                {"field_code": req.field_code, "allowed": list(CORRECTABLE_FIELD_CODES)},
            )

        # ---- 乐观锁：本人看到的值必须仍是库里的值（防「基于旧值覆盖 HR 刚改好的值」）----
        latest = cls._field_value(talent, field_def)
        if (req.current_value or "") != (latest or ""):
            raise ConflictError(
                "该字段当前值已变更，请刷新后重新提交",
                {
                    "field_code": field_def.code,
                    "latest_value": latest,
                    "latest_updated_at": cls._fmt(talent.updated_at),
                },
            )

        # ---- 防重复：同字段已有未终态的申请 → 40901（不做静默合并）----
        dup = cls._find_open_request(
            db, principal.tenant_id, int(talent.id), int(PrivacyRequestType.CORRECTION), field_def.code
        )
        if dup is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                "该字段已有待处理的更正申请，请等待处理结果",
                {
                    "request_id": str(dup.id),
                    "request_no": dup.request_no,
                    "status": int(dup.status),
                    "status_name": request_status_name_of(int(dup.status)),
                },
            )

        # ---- 目标值可写性**提前**预检：不要让 HR 批准时才发现填的是「五年」 ----
        try:
            coerce_corrected_value(field_def, req.expected_value)
        except ValueError as exc:
            raise BizError(
                ErrorCode.PARAM_INVALID, str(exc), {"field_code": field_def.code}
            ) from exc

        now = datetime.now()
        row = PrivacyRequest(
            tenant_id=principal.tenant_id,
            request_no=cls._next_request_no(db, principal.tenant_id),
            request_type=int(PrivacyRequestType.CORRECTION),
            talent_id=int(talent.id),
            applicant_user_id=principal.user_id,
            field_code=field_def.code,
            current_value=req.current_value,
            expected_value=req.expected_value,
            review_field_id=cls._link_review_field(
                db, principal.tenant_id, int(talent.id), field_def.code
            ),
            reason=req.reason,
            attachments=list(req.attachments) or None,
            status=int(PrivacyRequestStatus.PENDING),
            sla_hours=int(settings.PRIVACY_SLA_HOURS),
        )
        privacy_dao.insert_request(db, row)
        db.commit()

        AuditService.record(
            AuditAction.PRIVACY_CORRECT_SUBMIT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={
                "request_no": row.request_no,
                "talent_id": str(talent.id),
                "field_code": field_def.code,
                # ⚠️ 留痕只记「要改成什么」的**脱敏形态**（同口径 4）：
                #    审计日志同样是 HR/AUDIT 可读的表
                "expected_masked": cls._mask_for_audit(field_def, req.expected_value),
                "attachments": len(req.attachments),
            },
        )
        return CorrectionSubmitOut(
            request_id=row.id,
            request_no=row.request_no,
            status=int(row.status),
            status_name=request_status_name_of(int(row.status)),
            review_field_id=row.review_field_id,
            sla_hours=int(row.sla_hours),
            submitted_at=now,
        )

    # ==================================================================
    # API-73 / API-76 申请列表（更正 / 删除）
    # ==================================================================
    @classmethod
    def list_requests(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: PrivacyRequestFilter,
        *,
        request_type: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[PrivacyRequestOut], int, int, int]:
        """权利请求列表。``request_type`` 为 ``None`` 时不过滤类型（API-80 台账用）。

        ⚠️ **可见范围**（:meth:`_scope_kwargs`）：ADM/HR 看全租户，
        其余（EMP）**强制**只看自己提交的单子 —— 前端传的 ``applicant_user_id``
        在这个分支下被覆盖，杜绝「传别人的 ID 看别人的申请」。
        """
        kwargs = cls._scope_kwargs(principal, filters)
        if request_type is not None:
            kwargs["request_type"] = int(request_type)
        current = max(1, int(page))
        size = normalize_per_page(per_page)
        total = privacy_dao.count_requests(db, principal.tenant_id, **kwargs)
        rows = privacy_dao.list_requests(
            db, principal.tenant_id, **kwargs, limit=size, offset=(current - 1) * size
        )
        return cls._to_out_list(db, principal.tenant_id, rows), total, current, size

    @classmethod
    def list_corrections(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: PrivacyRequestFilter,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[PrivacyRequestOut], int, int, int]:
        """更正申请列表（API-73）。

        ⚠️ **这两个薄包装存在的理由不是「少写一行」**：``request_type`` 是
        **业务语义**，若让 Router 传 ``int(PrivacyRequestType.CORRECTION)``，
        等于把 ORM 枚举带进 Router 层 —— 结构守卫
        ``tests/test_layering.py::test_routers_contain_no_business_logic``
        会直接把路由判为越层（本批实测抓到过一次，见评审文档批次 13 缺陷 ⑫）。
        故类型选择留在 Service，Router 只表达「我要更正申请」。
        """
        return cls.list_requests(
            db,
            principal,
            filters,
            request_type=int(PrivacyRequestType.CORRECTION),
            page=page,
            per_page=per_page,
        )

    @classmethod
    def list_deletions(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: PrivacyRequestFilter,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[PrivacyRequestOut], int, int, int]:
        """删除申请列表（API-76）。筛选 / 排序 / 可见范围与 API-73 完全一致
        （同一套 :meth:`list_requests`，仅 ``request_type`` 不同）。"""
        return cls.list_requests(
            db,
            principal,
            filters,
            request_type=int(PrivacyRequestType.DELETION),
            page=page,
            per_page=per_page,
        )

    # ==================================================================
    # API-74 处理更正申请
    # ==================================================================
    @classmethod
    def resolve_correction(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        request_id: int,
        req: CorrectionResolveRequest,
    ) -> CorrectionResolveOut:
        """采纳或驳回更正申请；**采纳时把值真正写进档案并留痕**（F-3.10 衔接点）。

        :raises PermissionDeniedError: 非 ADM/HR → 40301（见 :meth:`_require_manager`）
        :raises ConflictError(40902): 申请已被他人处理
        """
        cls._require_manager(principal)
        row = cls._get_request(db, principal.tenant_id, request_id, PrivacyRequestType.CORRECTION)
        if int(row.status) in (int(PrivacyRequestStatus.APPROVED), int(PrivacyRequestStatus.REJECTED)):
            db.rollback()
            raise ConflictError(
                "该更正申请已处理",
                cls._request_conflict_data(db, principal.tenant_id, row),
            )

        now = datetime.now()
        if not req.approved:
            return cls._reject_correction(db, principal, row, reason=req.reason, now=now)

        field_def = get_correctable(row.field_code)
        if field_def is None:
            # 白名单在提交时已校验；能走到这里说明**历史数据**里的字段已被移出白名单
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                f"该申请涉及的字段 {row.field_code!r} 已不在可更正白名单内，无法采纳",
                {"field_code": row.field_code},
            )
        raw = req.applied_value if req.applied_value else (row.expected_value or "")
        try:
            value = coerce_corrected_value(field_def, raw)
        except ValueError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, str(exc), {"field_code": field_def.code}) from exc

        talent = talent_dao.get_talent(db, principal.tenant_id, int(row.talent_id))
        if talent is None:
            raise NotFoundError("档案不存在或已删除，无法采纳更正")

        before_masked = cls._log_value(talent, field_def)
        try:
            cls._apply_field(talent, field_def, value)
        except (SensitiveFieldError, ContactHashSaltError) as exc:
            # 部署配置问题（缺 AES_KEY / CONTACT_HASH_SALT）：**绝不能**降级成明文写入
            db.rollback()
            raise BizError(
                ErrorCode.INTERNAL_ERROR,
                f"敏感字段加密未正确配置，更正无法生效：{exc}",
                {"field_code": field_def.code},
            ) from exc
        db.flush()

        log = talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=int(talent.id),
                change_source=int(ChangeSource.MANUAL_REVIEW),
                field_name=field_def.code,
                before_value=before_masked,
                after_value=cls._log_value(talent, field_def),
                operator_id=principal.user_id,
            ),
        )
        db.flush()
        change_log_id = int(log.id)

        # 复核队列里那条（若有）同步收口 —— 否则工作台会一直显示「待复核」，
        # 而档案其实已经改完了（两处口径不一致是最伤信任的一类问题）
        cls._close_review_field(db, principal, row, field_def, value, now)

        message = build_notify_message(
            request_type=int(PrivacyRequestType.CORRECTION),
            request_no=str(row.request_no),
            status=int(PrivacyRequestStatus.APPROVED),
        )
        affected = privacy_dao.cas_resolve(
            db,
            principal.tenant_id,
            int(row.id),
            from_status=int(PrivacyRequestStatus.PENDING),
            to_status=int(PrivacyRequestStatus.APPROVED),
            processed_by=principal.user_id,
            processed_at=now,
            notify_message=message,
            notified_at=now,
        )
        if affected == 0:
            # 读时还是待处理、写时不是 → 只能是**别人**刚处理完（同一人的重试会被上面的状态前置检查拦下）
            db.rollback()
            handled = privacy_dao.get_request(db, principal.tenant_id, int(row.id))
            raise ConflictError(
                "该更正申请已被他人处理",
                cls._request_conflict_data(db, principal.tenant_id, handled),
            )
        db.commit()

        cls._reindex_talent(db, talent, principal.tenant_id)

        AuditService.record(
            AuditAction.PRIVACY_CORRECT_RESOLVE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            before={"status": int(PrivacyRequestStatus.PENDING)},
            after={
                "request_no": row.request_no,
                "approved": True,
                "talent_id": str(row.talent_id),
                "field_code": field_def.code,
                "change_log_id": str(change_log_id),
                "notified": True,
            },
        )
        return CorrectionResolveOut(
            request_id=row.id,
            status=int(PrivacyRequestStatus.APPROVED),
            status_name=request_status_name_of(int(PrivacyRequestStatus.APPROVED)),
            talent_id=row.talent_id,
            field_code=field_def.code,
            applied=True,
            applied_value=cls._field_value(talent, field_def),
            change_log_id=change_log_id,
            notified=True,
            processed_at=now,
        )

    # ==================================================================
    # API-75 提交删除申请
    # ==================================================================
    @classmethod
    def submit_deletion(
        cls, db: Session, principal: CurrentPrincipal, req: DeletionSubmitRequest
    ) -> DeletionSubmitOut:
        """提交删除申请（**审核权归 ADM**，本人只提交）。"""
        talent = cls._assert_own(db, principal, int(req.talent_id))
        cls._assert_correctable_now(talent)

        dup = cls._find_open_request(
            db, principal.tenant_id, int(talent.id), int(PrivacyRequestType.DELETION)
        )
        if dup is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                "该档案已有待处理的删除申请，请等待审批结果",
                {"request_id": str(dup.id), "request_no": dup.request_no},
            )

        now = datetime.now()
        row = PrivacyRequest(
            tenant_id=principal.tenant_id,
            request_no=cls._next_request_no(db, principal.tenant_id),
            request_type=int(PrivacyRequestType.DELETION),
            talent_id=int(talent.id),
            applicant_user_id=principal.user_id,
            reason=req.reason,
            status=int(PrivacyRequestStatus.PENDING),
            sla_hours=int(settings.PRIVACY_SLA_HOURS),
        )
        privacy_dao.insert_request(db, row)
        db.commit()

        AuditService.record(
            AuditAction.PRIVACY_DELETE_SUBMIT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={
                "request_no": row.request_no,
                "talent_id": str(talent.id),
                "reason_len": len(req.reason or ""),
            },
        )
        return DeletionSubmitOut(
            request_id=row.id,
            request_no=row.request_no,
            status=int(row.status),
            status_name=request_status_name_of(int(row.status)),
            talent_id=row.talent_id,
            sla_hours=int(row.sla_hours),
            submitted_at=now,
        )

    # ==================================================================
    # API-77 审批删除（🔴 跨库一致性：SQ-5 五步编排）
    # ==================================================================
    @classmethod
    def approve_deletion(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        request_id: int,
        req: DeletionApproveRequest,
        *,
        idempotent_key: str | None = None,
    ) -> DeletionApproveOut:
        """审批删除申请（接口 §1.6 **强制幂等**）。

        :raises NotFoundError: 申请不存在 → 40401
        :raises BizError(40903): 同一幂等键已审批过 → **返回首次结果**
        :raises ConflictError(40902): 申请已被他人处理
        :raises BizError(50000): 向量清理未完成 → 已回滚软删，申请**保持待处理**
        """
        # ---- 0. 幂等回放（**先于状态判定**，与 agent_service 同源约定）----
        if idempotent_key:
            replay = privacy_dao.get_request_by_idem(db, principal.tenant_id, idempotent_key)
            if replay is not None:
                raise BizError(
                    ErrorCode.IDEMPOTENT_REPLAY,
                    "该删除申请已审批，返回首次结果",
                    cls._replay_data(db, principal, replay, idempotent_key),
                )

        row = cls._get_request(db, principal.tenant_id, request_id, PrivacyRequestType.DELETION)

        # ---- 驳回分支：无跨库步骤，一次 CAS 0 → 2 即可 ----
        if not req.approved:
            return cls._reject_deletion(db, principal, row, reason=req.reason, idempotent_key=idempotent_key)

        if int(row.status) in (int(PrivacyRequestStatus.APPROVED), int(PrivacyRequestStatus.REJECTED)):
            raise ConflictError(
                "该删除申请已处理",
                cls._request_conflict_data(db, principal.tenant_id, row),
            )

        talent = talent_dao.get_talent_any_state(db, principal.tenant_id, int(row.talent_id))
        if talent is None:
            raise NotFoundError("档案不存在")
        if int(talent.is_deleted) == 1:
            # 档案已被删（上一次审批成功但申请状态未落，或合并等其它路径软删）——
            # 不重复执行删除，如实报冲突，避免写入第二条「删除」留痕
            raise ConflictError(
                "该档案已处于删除状态，请核对申请状态",
                {"talent_id": str(row.talent_id), "data_status": int(talent.data_status)},
            )

        now = datetime.now()
        prev_status = int(talent.data_status)

        # ---- ① CAS：置「处理中」（状态位防并发双审）----
        started = privacy_dao.cas_start_processing(
            db,
            principal.tenant_id,
            int(row.id),
            processed_by=principal.user_id,
            idempotent_key=idempotent_key,
        )
        if started == 0:
            db.rollback()
            handled = privacy_dao.get_request(db, principal.tenant_id, int(row.id))
            raise ConflictError(
                "该删除申请已被他人处理",
                cls._request_conflict_data(db, principal.tenant_id, handled),
            )

        # ---- ② 删除向量（COL-01~03）——**先于** MySQL 软删（口径 2 的核心）----
        provider = get_retrieval_provider()
        is_milvus = isinstance(provider, MilvusRetrievalProvider)
        cleaned: dict[str, int] = {}
        if is_milvus:
            cleaned = provider.delete_all(
                db, talent_id=int(row.talent_id), tenant_id=principal.tenant_id
            )
            failed = sorted(name for name, count in cleaned.items() if int(count) < 0)

            # ---- ③ 任一分片失败 → 不软删、回滚已删分片、返回 50000、申请保持待处理 ----
            if failed:
                db.rollback()  # 「置处理中」一并回滚 → 申请**回到待处理**
                compensated = cls._reindex_talent(db, talent, principal.tenant_id)
                logger.error(
                    "删除审批向量清理失败，已放弃软删 talent=%s request=%s failed=%s compensated=%s",
                    row.talent_id,
                    row.id,
                    failed,
                    compensated,
                )
                raise BizError(
                    ErrorCode.INTERNAL_ERROR,
                    "向量清理未完成，已回滚档案删除，请稍后重试（申请保持待处理）",
                    {
                        "request_id": str(row.id),
                        "status": int(PrivacyRequestStatus.PENDING),
                        "vector_cleaned": cleaned,
                        "failed_collections": failed,
                        "reindexed": compensated,
                    },
                )

        # ---- ④ 全部向量清理成功 → 软删 MySQL + 写留痕 ----
        talent.data_status = int(TalentDataStatus.DELETED)
        talent.is_deleted = 1
        talent.deleted_at = now
        talent.version_no = int(talent.version_no) + 1
        db.flush()
        log = talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=int(talent.id),
                change_source=int(ChangeSource.MANUAL_REVIEW),
                field_name="data_status",
                before_value=str(prev_status),
                after_value=str(int(TalentDataStatus.DELETED)),
                operator_id=principal.user_id,
            ),
        )
        db.flush()
        change_log_id = int(log.id)

        # ---- ⑤ 申请置「已通过」并通知本人 ----
        message = build_notify_message(
            request_type=int(PrivacyRequestType.DELETION),
            request_no=str(row.request_no),
            status=int(PrivacyRequestStatus.APPROVED),
        )
        resolved = privacy_dao.cas_resolve(
            db,
            principal.tenant_id,
            int(row.id),
            from_status=int(PrivacyRequestStatus.PROCESSING),
            to_status=int(PrivacyRequestStatus.APPROVED),
            processed_by=principal.user_id,
            processed_at=now,
            notify_message=message,
            notified_at=now,
            idempotent_key=idempotent_key,
        )
        if resolved == 0:  # pragma: no cover — 同一事务内刚置为处理中，理论上不可能
            db.rollback()
            raise ConflictError("该删除申请已被他人处理", {"request_id": str(row.id)})
        db.commit()

        AuditService.record(
            AuditAction.PRIVACY_DELETE_APPROVE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            before={"talent_status": prev_status},
            after={
                "request_no": row.request_no,
                "approved": True,
                "talent_id": str(row.talent_id),
                "change_log_id": str(change_log_id),
                "vector_cleaned": cleaned,
                "vector_degraded": not is_milvus,
                "idempotent_key": idempotent_key,
            },
        )
        return DeletionApproveOut(
            request_id=row.id,
            status=int(PrivacyRequestStatus.APPROVED),
            status_name=request_status_name_of(int(PrivacyRequestStatus.APPROVED)),
            talent_id=row.talent_id,
            talent_soft_deleted=True,
            vector_cleaned=cleaned,
            # 降级环境（关键词实现）：本来就没有向量库 → 不阻断删除，但**如实标记**，
            # 让「本次删除未清理任何向量」这件事在响应里可见，而不是看着像清理成功了
            vector_degraded=not is_milvus,
            change_log_id=change_log_id,
            notified=True,
            processed_at=now,
        )

    # ==================================================================
    # API-78 提交导出申请
    # ==================================================================
    @classmethod
    def submit_export(
        cls, db: Session, principal: CurrentPrincipal, req: ExportSubmitRequest
    ) -> ExportSubmitOut:
        """提交导出申请。**提交即完成**（``status=1``，口径 5）。"""
        talent = cls._assert_own(db, principal, int(req.talent_id))
        cls._assert_correctable_now(talent)

        now = datetime.now()
        row = PrivacyRequest(
            tenant_id=principal.tenant_id,
            request_no=cls._next_request_no(db, principal.tenant_id),
            request_type=int(PrivacyRequestType.EXPORT),
            talent_id=int(talent.id),
            applicant_user_id=principal.user_id,
            status=int(PrivacyRequestStatus.PENDING),
            sla_hours=int(settings.PRIVACY_SLA_HOURS),
            export_format=req.format,
        )
        privacy_dao.insert_request(db, row)
        db.flush()

        message = build_notify_message(
            request_type=int(PrivacyRequestType.EXPORT),
            request_no=str(row.request_no),
            status=int(PrivacyRequestStatus.APPROVED),
        )
        privacy_dao.cas_resolve(
            db,
            principal.tenant_id,
            int(row.id),
            from_status=int(PrivacyRequestStatus.PENDING),
            to_status=int(PrivacyRequestStatus.APPROVED),
            processed_by=principal.user_id,
            processed_at=now,
            notify_message=message,
            notified_at=now,
        )
        db.commit()

        AuditService.record(
            AuditAction.PRIVACY_EXPORT_SUBMIT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={"request_no": row.request_no, "talent_id": str(talent.id), "format": req.format},
        )
        return ExportSubmitOut(
            request_id=row.id,
            request_no=row.request_no,
            status=int(PrivacyRequestStatus.APPROVED),
            status_name=request_status_name_of(int(PrivacyRequestStatus.APPROVED)),
            format=req.format,
            download_url=f"/api/v1/privacy/exports/{int(row.id)}/download",
            sla_hours=int(row.sla_hours),
            submitted_at=now,
        )

    # ==================================================================
    # API-79 下载个人数据包
    # ==================================================================
    @classmethod
    def download_export(
        cls, db: Session, principal: CurrentPrincipal, request_id: int
    ) -> ExportDownloadOut:
        """生成并下发数据包（**下载行为写审计 + 计数**，F-3.11 要求留痕）。

        权限：本人（``applicant_user_id`` = 自己）或 ADM（接口 §4 矩阵：EMP/ADM）。

        🔴 内容放 ``data.content``、不返回文件流 —— 同 CU-08 API-29 的取舍：
        文件流会让统一响应壳（``code`` / ``trace_id``）失效，而**下载行为必须留痕**，
        一个「坏掉的包」比一次「查不到记录」更难排查。代价：一期仅 JSON（无 PDF）。
        """
        row = privacy_dao.get_request(db, principal.tenant_id, request_id)
        if row is None:
            raise NotFoundError("导出申请不存在")
        if int(row.request_type) != int(PrivacyRequestType.EXPORT):
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "该请求不是导出申请，无法下载数据包",
                {"request_type": int(row.request_type)},
            )
        is_owner = int(row.applicant_user_id) == int(principal.user_id)
        if not is_owner and not principal.is_admin:
            # 越权与不存在同码（CR-8）：不泄露「这个申请存在但不是你的」
            raise PermissionDeniedError("无权限下载该数据包")
        if int(row.status) != int(PrivacyRequestStatus.APPROVED):
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                f"导出申请尚未完成（当前状态：{request_status_name_of(int(row.status))}），无法下载",
                {"status": int(row.status)},
            )

        talent = talent_dao.get_talent(db, principal.tenant_id, int(row.talent_id))
        if talent is None:
            raise NotFoundError("档案不存在或已删除，数据包无法生成")

        now = datetime.now()
        content = cls._render_export(db, principal.tenant_id, talent, row, now)
        privacy_dao.mark_downloaded(
            db, principal.tenant_id, int(row.id), now=now, export_format=str(row.export_format or "json")
        )
        db.commit()

        # 重新读一次拿**本次之后**的累计下载次数（原子自增的结果只在库里）
        fresh = privacy_dao.get_request(db, principal.tenant_id, int(row.id))
        count = int(fresh.download_count) if fresh is not None else 1

        AuditService.record(
            AuditAction.PRIVACY_EXPORT_DOWNLOAD,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={
                "request_no": row.request_no,
                "talent_id": str(row.talent_id),
                "download_count": count,
                "by_admin": not is_owner,
            },
        )
        return ExportDownloadOut(
            request_id=row.id,
            talent_id=row.talent_id,
            filename=f"{row.request_no}_personal_data.json",
            format="json",
            content=content,
            download_count=count,
            generated_at=now,
        )

    # ==================================================================
    # API-80 权利请求统一台账（含处理时限统计）
    # ==================================================================
    @classmethod
    def ledger(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: PrivacyRequestFilter,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[PrivacyLedgerOut, int, int, int]:
        """台账列表 + 处理时限统计（F-3.11「处理时限可配置并统计」）。

        🔴 统计口径由 :func:`~app.core.privacy.summarize_requests` 决定（铁律 10），
        本方法只负责**取数**与**分页切片**：

        - 统计**永远覆盖全部匹配行**（不是当前页）—— 否则「翻页会改变合规数字」；
        - ``overdue_only`` 无法下推到 SQL（要按每行自己的 ``sla_hours`` 比较），
          故取回后在内存里按 :func:`~app.core.privacy.is_overdue` 过滤；
        - 扫描上限 :data:`SLA_SCAN_LIMIT`，超限时 ``summary.truncated=true``
          （**如实声明**近似，而不是悄悄给一个偏小的数字）。
        """
        kwargs = cls._scope_kwargs(principal, filters)
        scan = privacy_dao.list_requests(
            db, principal.tenant_id, **kwargs, pending_first=False, limit=SLA_SCAN_LIMIT, offset=0
        )
        now = datetime.now()

        if filters.overdue_only:
            scan = [
                r
                for r in scan
                if is_overdue(
                    status=int(r.status),
                    created_at=r.created_at,
                    sla_hours=int(r.sla_hours or settings.PRIVACY_SLA_HOURS),
                    now=now,
                    processed_at=r.processed_at,
                )
            ]

        summary = summarize_requests(
            [
                cls._to_record(r, sla_hours=int(settings.PRIVACY_SLA_HOURS))
                for r in scan
            ],
            now=now,
            sla_hours=int(settings.PRIVACY_SLA_HOURS),
            type_names={int(t): (PrivacyRequestType.name_of(int(t)) or "") for t in PrivacyRequestType},
        ).as_dict()
        summary["truncated"] = len(scan) >= SLA_SCAN_LIMIT
        summary["overdue_only"] = bool(filters.overdue_only)
        summary["scope"] = "tenant" if cls._is_manager(principal) else "self"

        current = max(1, int(page))
        size = normalize_per_page(per_page)
        start = (current - 1) * size
        window = scan[start : start + size]
        return (
            PrivacyLedgerOut(items=cls._to_out_list(db, principal.tenant_id, window), summary=summary),
            len(scan),
            current,
            size,
        )

    # ==================================================================
    # 内部：驳回分支
    # ==================================================================
    @classmethod
    def _reject_correction(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        row: PrivacyRequest,
        *,
        reason: str | None,
        now: datetime,
    ) -> CorrectionResolveOut:
        """驳回更正申请（**必须带理由**，SM-3 红线②）。"""
        text = (reason or "").strip() or None
        message = build_notify_message(
            request_type=int(PrivacyRequestType.CORRECTION),
            request_no=str(row.request_no),
            status=int(PrivacyRequestStatus.REJECTED),
            reject_reason=text,
        )
        affected = privacy_dao.cas_resolve(
            db,
            principal.tenant_id,
            int(row.id),
            from_status=int(PrivacyRequestStatus.PENDING),
            to_status=int(PrivacyRequestStatus.REJECTED),
            processed_by=principal.user_id,
            processed_at=now,
            reject_reason=text,
            notify_message=message,
            notified_at=now,
        )
        if affected == 0:
            db.rollback()
            handled = privacy_dao.get_request(db, principal.tenant_id, int(row.id))
            raise ConflictError(
                "该更正申请已被他人处理",
                cls._request_conflict_data(db, principal.tenant_id, handled),
            )
        db.commit()
        AuditService.record(
            AuditAction.PRIVACY_CORRECT_RESOLVE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={
                "request_no": row.request_no,
                "approved": False,
                "talent_id": str(row.talent_id),
                "reason": text,
                "notified": True,
            },
        )
        return CorrectionResolveOut(
            request_id=row.id,
            status=int(PrivacyRequestStatus.REJECTED),
            status_name=request_status_name_of(int(PrivacyRequestStatus.REJECTED)),
            talent_id=row.talent_id,
            field_code=row.field_code,
            applied=False,
            applied_value=None,
            change_log_id=None,
            notified=True,
            processed_at=now,
        )

    @classmethod
    def _reject_deletion(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        row: PrivacyRequest,
        *,
        reason: str | None,
        idempotent_key: str | None = None,
    ) -> DeletionApproveOut:
        """驳回删除申请（**不回滚任何数据** —— 因为什么都没做）。"""
        now = datetime.now()
        text = (reason or "").strip() or None
        message = build_notify_message(
            request_type=int(PrivacyRequestType.DELETION),
            request_no=str(row.request_no),
            status=int(PrivacyRequestStatus.REJECTED),
            reject_reason=text,
        )
        affected = privacy_dao.cas_resolve(
            db,
            principal.tenant_id,
            int(row.id),
            from_status=int(PrivacyRequestStatus.PENDING),
            to_status=int(PrivacyRequestStatus.REJECTED),
            processed_by=principal.user_id,
            processed_at=now,
            reject_reason=text,
            notify_message=message,
            notified_at=now,
            idempotent_key=idempotent_key,
        )
        if affected == 0:
            db.rollback()
            handled = privacy_dao.get_request(db, principal.tenant_id, int(row.id))
            raise ConflictError(
                "该删除申请已被他人处理",
                cls._request_conflict_data(db, principal.tenant_id, handled),
            )
        db.commit()
        AuditService.record(
            AuditAction.PRIVACY_DELETE_APPROVE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PRIVACY_REQUEST,
            target_id=row.id,
            after={
                "request_no": row.request_no,
                "approved": False,
                "talent_id": str(row.talent_id),
                "reason": text,
                "notified": True,
                "idempotent_key": idempotent_key,
            },
        )
        return DeletionApproveOut(
            request_id=row.id,
            status=int(PrivacyRequestStatus.REJECTED),
            status_name=request_status_name_of(int(PrivacyRequestStatus.REJECTED)),
            talent_id=row.talent_id,
            talent_soft_deleted=False,
            vector_cleaned={},
            vector_degraded=False,
            change_log_id=None,
            notified=True,
            processed_at=now,
        )

    # ==================================================================
    # 内部：事务与并发
    # ==================================================================
    @staticmethod
    def _require_manager(principal: CurrentPrincipal) -> None:
        """处理类动作（更正处理）要求 ADM / HR。

        ⚠️ **为什么权限点不够**：``privacy:correct`` 同时授给了 EMP（他要提交申请）
        与 HR（他要处理申请）—— 「提交」与「处理」共用一个权限码是上游矩阵的现状。
        若只靠 ``require_perm("privacy:correct")``，任何 EMP 都能**审批自己的申请**。
        故处理权必须在服务层再补一次角色判定（fail-closed：不匹配即 40301）。

        （删除审批不受此影响：``privacy:approve`` 只授给 ADM。）
        """
        if principal.is_admin or principal.has_role(*MANAGER_ROLES):
            return
        raise PermissionDeniedError("无权限处理个人权利申请（仅 ADM / HR）")

    @staticmethod
    def _is_manager(principal: CurrentPrincipal) -> bool:
        return bool(principal.is_admin or principal.has_role(*MANAGER_ROLES))

    @classmethod
    def _scope_kwargs(
        cls, principal: CurrentPrincipal, filters: PrivacyRequestFilter
    ) -> dict[str, Any]:
        """列表可见范围（**唯一**的收窄点，list / count / 台账共用）。"""
        kwargs: dict[str, Any] = {
            "request_type": int(filters.request_type) if filters.request_type is not None else None,
            "status": int(filters.status) if filters.status is not None else None,
            "talent_id": int(filters.talent_id) if filters.talent_id is not None else None,
            "applicant_user_id": (
                int(filters.applicant_user_id) if filters.applicant_user_id is not None else None
            ),
            "start_time": filters.start_time,
            "end_time": filters.end_time,
        }
        if not cls._is_manager(principal):
            # 🔴 非管理者：**覆盖**（不是「合并」）调用方传的申请人条件 ——
            #    传别人的 ID 试图查看他人的申请时，拿到的仍是他自己的单子（空列表）
            kwargs["applicant_user_id"] = int(principal.user_id)
        return kwargs

    @staticmethod
    def _get_request(
        db: Session, tenant_id: int, request_id: int, expected_type: int
    ) -> PrivacyRequest:
        row = privacy_dao.get_request(db, tenant_id, int(request_id))
        if row is None:
            # 权利请求单无独立行级数据域（挂在某份档案上）→ 跨租户 = 不存在（CR-8 的例外口径）
            raise NotFoundError("权利申请不存在")
        if int(row.request_type) != int(expected_type):
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"该申请类型为「{request_type_name_of(int(row.request_type))}」，"
                f"不能通过本接口处理",
                {"expected_type": int(expected_type), "actual_type": int(row.request_type)},
            )
        return row

    @classmethod
    def _request_conflict_data(
        cls, db: Session, tenant_id: int, row: PrivacyRequest | None
    ) -> dict[str, Any]:
        """40902 的 ``data``：带**处理人姓名与时间**（前端据此显示「已被谁处理」）。"""
        if row is None:  # pragma: no cover — 刚读过，理论上不可能
            return {}
        names = (
            user_admin_dao.list_usernames_by_ids(db, tenant_id, [int(row.processed_by)])
            if row.processed_by is not None
            else {}
        )
        return {
            "request_id": str(row.id),
            "request_no": row.request_no,
            "status": int(row.status),
            "status_name": request_status_name_of(int(row.status)),
            "processed_by_name": (
                names.get(int(row.processed_by)) if row.processed_by is not None else None
            ),
            "processed_at": cls._fmt(row.processed_at),
            "reject_reason": row.reject_reason,
        }

    @classmethod
    def _replay_data(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        replay: PrivacyRequest,
        idempotent_key: str,
    ) -> dict[str, Any]:
        """40903 的 ``data``：带**首次处理的结果**（前端按成功继续，与 agent_service 同源）。"""
        talent = talent_dao.get_talent_any_state(db, principal.tenant_id, int(replay.talent_id))
        return {
            "idempotent_key": idempotent_key,
            "request_id": str(replay.id),
            "request_no": replay.request_no,
            "status": int(replay.status),
            "status_name": request_status_name_of(int(replay.status)),
            "first_handled_at": cls._fmt(replay.processed_at),
            "talent_id": str(replay.talent_id),
            "talent_soft_deleted": bool(talent is not None and int(talent.is_deleted) == 1),
            "notified": replay.notified_at is not None,
        }

    # ==================================================================
    # 内部：本人定位与归属校验
    # ==================================================================
    @classmethod
    def _my_talent(cls, db: Session, principal: CurrentPrincipal) -> TalentBase:
        """当前账号的本人档案（API-71 的唯一入口）。"""
        talent = talent_dao.get_talent_by_user(db, principal.tenant_id, principal.user_id)
        if talent is None:
            raise NotFoundError("未找到与当前账号关联的本人档案")
        return talent

    @classmethod
    def _assert_own(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int
    ) -> TalentBase:
        """归属校验：``talent.user_id`` 必须等于登录用户，否则 **40301**。

        🔴 「不存在」与「不是你的」**同码**（CR-8）：区分二者等于提供了一个
        「这个 ID 对应一份真实档案但是别人的」的探测信号。
        """
        talent = talent_dao.get_talent(db, principal.tenant_id, int(talent_id))
        if talent is None or talent.user_id is None or int(talent.user_id) != int(principal.user_id):
            raise PermissionDeniedError("无权限访问该档案")
        return talent

    @staticmethod
    def _assert_correctable_now(talent: TalentBase) -> None:
        """档案必须处于「可发起权利请求」的状态（已删档案不再接受新申请）。"""
        if int(talent.is_deleted) == 1 or int(talent.data_status) == int(TalentDataStatus.DELETED):
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                "档案已删除，无法发起个人权利申请",
                {"data_status": int(talent.data_status)},
            )

    # ==================================================================
    # 内部：字段值与写库
    # ==================================================================
    @staticmethod
    def _field_value(talent: TalentBase, field_def: Any) -> str | None:
        """字段当前值（**可比较的字符串**）：加密列先解密，再统一渲染。

        ⚠️ 必须与 :func:`~app.core.privacy.coerce_corrected_value` 互为逆运算
        （见该函数的 docstring）—— 否则会出现「本人填的值与库里同值却判为不一致」。
        """
        if field_def.encrypted:
            return render_value(decrypt_sensitive(getattr(talent, field_def.column, None)))
        return render_value(getattr(talent, field_def.column, None))

    @staticmethod
    def _log_value(talent: TalentBase, field_def: Any) -> str | None:
        """**写入留痕 / 审计**的值形态（委托给 :func:`~app.core.privacy.log_field_value`）。

        ``talent_change_log`` 与 ``audit_log`` 都是 ADM/HR/AUDIT 可读的表；
        手机号/邮箱的明文写进去等于把 PII 从加密列搬到明文列，AES 就白做了。

        代价与收益：留痕只证明「变了」，不暴露「变成什么」——
        合规上这**恰好**是留痕该有的信息量（F-3.11 要求「更正可追溯」，
        不要求「更正内容对所有人可见」）。

        ⚠️ 脱敏规则**只有一份**（``core.privacy``）：``change_service.field_history``
        的「当前值」也走同一个函数，否则界面会出现
        「当前值 13812345678 / 变更前 138****5678」这种像数据损坏的对照。
        """
        if field_def.encrypted:
            plain = decrypt_sensitive(getattr(talent, field_def.column, None))
            return log_field_value(field_def.code, plain)
        return log_field_value(field_def.code, getattr(talent, field_def.column, None))

    @staticmethod
    def _mask_for_audit(field_def: Any, raw: str | None) -> str | None:
        """把「本人填的目标值」按字段类型处理成可入审计的形态。"""
        if not field_def.encrypted:
            return raw
        return mask_phone(raw) if field_def.code == "phone" else mask_email(raw)

    @staticmethod
    def _apply_field(talent: TalentBase, field_def: Any, value: Any) -> None:
        """把更正后的值写进档案列。

        🔴 加密字段必须**同时**重算 ``*_enc`` 与 ``*_hash``
        （只改其一 → 档案里存着新号、去重却按旧号比，是最难发现的一类脏数据）。
        """
        if field_def.code == "phone":
            talent.phone_enc = encrypt_sensitive(str(value))
            talent.phone_hash = hash_contact(str(value), "phone")
        elif field_def.code == "email":
            talent.email_enc = encrypt_sensitive(str(value))
        else:
            setattr(talent, field_def.column, value)
        talent.version_no = int(talent.version_no) + 1

    # ==================================================================
    # 内部：复核队列联动
    # ==================================================================
    @staticmethod
    def _link_review_field(
        db: Session, tenant_id: int, talent_id: int, field_code: str
    ) -> int | None:
        """找该档案该字段**已有的待复核条目**，把它作为申请的上下文指针。

        🔴 刻意**不新建** ``talent_parse_field`` 行
        ------------------------------------------
        F-3.3 的复核队列（API-44）只展示解析字段，而「确认」动作（API-46/47）
        **只改解析字段、不改 ``talent_base``** —— 若为更正申请新建一条待复核行，
        HR 在复核工作台点「确认」会得到一个**假生效**（队列清了、档案没变）。

        因此：更正的**唯一生效路径是 API-74**（它直接写 ``talent_base``）；
        这里只复用「同一字段已有的待复核行」作上下文（原文片段 / 置信度 / 来源文件），
        让 HR 在处理时能看到依据，而不是凭空看见一个字符串。
        """
        rows = talent_dao.list_fields_by_talent(db, tenant_id, talent_id)
        for row in rows:
            if str(row.field_name) == field_code and int(row.review_status) == int(
                ParseReviewStatus.PENDING
            ):
                return int(row.id)
        return None

    @staticmethod
    def _close_review_field(
        db: Session,
        principal: CurrentPrincipal,
        row: PrivacyRequest,
        field_def: Any,
        value: Any,
        now: datetime,
    ) -> None:
        """采纳后把关联的复核条目收口为「已修正」，避免工作台残留假待办。"""
        if row.review_field_id is None:
            return
        affected = talent_dao.update_parse_field_review(
            db,
            principal.tenant_id,
            int(row.review_field_id),
            new_status=int(ParseReviewStatus.CORRECTED),
            parsed_value=render_value(value),
            confidence=1.0,  # 人工更正 = 人担保（与 review_service 同口径）
            reviewed_by=principal.user_id,
            reviewed_at=now,
        )
        if affected == 0:
            # 已被 HR 在复核工作台处理过 —— 不是错误（两处入口都指向同一份档案），
            # 但要在日志里留一条，便于回溯「档案值与队列状态为何不同步」
            logger.info(
                "更正采纳时关联复核条目已处理，跳过收口 request=%s field_row=%s",
                row.id,
                row.review_field_id,
            )

    # ==================================================================
    # 内部：向量重索引（尽力而为）
    # ==================================================================
    @staticmethod
    def _reindex_talent(db: Session, talent: TalentBase, tenant_id: int) -> bool:
        """档案画像重索引（**尽力而为**，派生索引可全量重建）。

        两处用到：
        - 更正采纳后（画像内容变了，检索结果必须跟着变）；
        - 删除审批**向量清理部分失败**时的**补偿**（SQ-5 步骤 ③ 的「回滚已删分片」）——
          用同一条重索引把已被删掉的切片补回来，而不是宣称「回滚了」却什么都没做。
        """
        provider = get_retrieval_provider()
        if not isinstance(provider, MilvusRetrievalProvider):
            return False
        text = build_profile_text(talent)
        if not text:
            return False
        try:
            provider.ingest(
                db,
                talent_id=int(talent.id),
                tenant_id=tenant_id,
                org_id=int(talent.org_id) if talent.org_id is not None else None,
                field_name="profile",
                chunk_text=text,
                vector=None,  # 真实向量待 H-19 吞吐标定后接入（CU-18 缺口）
            )
            return provider.last_error is None
        except Exception:  # noqa: BLE001 — 派生索引，尽力而为
            logger.warning("档案重索引失败 talent=%s（派生索引，不影响本次结果）", talent.id)
            return False

    # ==================================================================
    # 内部：出参组装
    # ==================================================================
    @classmethod
    def _to_out_list(
        cls, db: Session, tenant_id: int, rows: list[PrivacyRequest]
    ) -> list[PrivacyRequestOut]:
        """批量补齐 ``talent_name`` / ``applicant_name`` / ``processed_by_name``。"""
        if not rows:
            return []
        talent_names = talent_dao.list_talent_names_by_ids(
            db, tenant_id, [int(r.talent_id) for r in rows]
        )
        user_ids = {int(r.applicant_user_id) for r in rows}
        user_ids |= {int(r.processed_by) for r in rows if r.processed_by is not None}
        usernames = user_admin_dao.list_usernames_by_ids(db, tenant_id, sorted(user_ids))
        now = datetime.now()
        return [
            cls._to_out(r, talent_names=talent_names, usernames=usernames, now=now) for r in rows
        ]

    @staticmethod
    def _to_out(
        row: PrivacyRequest,
        *,
        talent_names: dict[int, str],
        usernames: dict[int, str],
        now: datetime,
    ) -> PrivacyRequestOut:
        sla = int(row.sla_hours or settings.PRIVACY_SLA_HOURS)
        overdue = is_overdue(
            status=int(row.status),
            created_at=row.created_at,
            sla_hours=sla,
            now=now,
            processed_at=row.processed_at,
        )
        # 已终态的单子「已等待时长」算到处理时刻；未终态的算到今天
        end = row.processed_at or now
        return PrivacyRequestOut(
            id=row.id,
            request_no=str(row.request_no),
            request_type=int(row.request_type),
            request_type_name=request_type_name_of(int(row.request_type)),
            talent_id=row.talent_id,
            talent_name=talent_names.get(int(row.talent_id)),
            applicant_user_id=row.applicant_user_id,
            applicant_name=usernames.get(int(row.applicant_user_id)),
            field_code=row.field_code,
            field_label=FIELD_LABELS.get(str(row.field_code)) if row.field_code else None,
            current_value=row.current_value,
            expected_value=row.expected_value,
            review_field_id=row.review_field_id,
            reason=row.reason,
            status=int(row.status),
            status_name=request_status_name_of(int(row.status)),
            sla_hours=sla,
            overdue=overdue,
            waited_hours=waited_hours(row.created_at, end),
            processed_by=row.processed_by,
            processed_by_name=(
                usernames.get(int(row.processed_by)) if row.processed_by is not None else None
            ),
            processed_at=row.processed_at,
            reject_reason=row.reject_reason,
            notified_at=row.notified_at,
            download_count=int(row.download_count or 0),
            created_at=row.created_at,
        )

    @staticmethod
    def _to_record(row: PrivacyRequest, *, sla_hours: int) -> Any:
        """ORM 行 → :class:`~app.core.privacy.RequestRecord`（统计口径的输入形态）。"""
        from app.core.privacy import RequestRecord

        return RequestRecord(
            request_type=int(row.request_type),
            status=int(row.status),
            created_at=row.created_at,
            sla_hours=int(row.sla_hours or sla_hours),
            processed_at=row.processed_at,
        )

    @classmethod
    def _profile_fields(
        cls, db: Session, tenant_id: int, talent: TalentBase
    ) -> tuple[list[SelfFieldOut], list[SelfTagOut]]:
        """组装「本人视角」的字段明细与标签（API-71 与数据包**共用同一份**）。

        🔴 抽成一个方法而不是两处各写一遍：数据包（API-79）与查阅接口（API-71）
        必须是**同一份内容**。分别实现的话，「多下载了一个字段」这类差异
        永远不会有人发现 —— 而它恰好是合规审查会问的第一个问题。
        """
        rows = talent_dao.list_fields_by_talent(db, tenant_id, int(talent.id))
        by_field = {str(r.field_name): r for r in rows}
        sources = cls._field_sources(db, tenant_id, int(talent.id), list(SELF_VIEW_FIELD_CODES))

        fields: list[SelfFieldOut] = []
        for code in SELF_VIEW_FIELD_CODES:
            field_def = CORRECTABLE_FIELDS.get(code)
            if field_def is not None:
                value = cls._field_value(talent, field_def)
            else:  # 证件号：只下发脱敏形态（口径 ③）
                value = mask_encrypted_id_card(talent.id_card_enc)
            row = by_field.get(code)
            source = sources.get(code)
            fields.append(
                SelfFieldOut(
                    field_code=code,
                    field_label=FIELD_LABELS.get(code, code),
                    value=value,
                    source=source,
                    source_name=change_source_name_of(source),
                    confidence=(
                        float(row.confidence)
                        if row is not None and row.confidence is not None
                        else None
                    ),
                    review_status=int(row.review_status) if row is not None else None,
                    review_status_name=(
                        review_status_name_of(int(row.review_status)) if row is not None else None
                    ),
                    review_field_id=row.id if row is not None else None,
                    source_file=row.source_file if row is not None else None,
                )
            )
        return fields, cls._self_tags(db, tenant_id, int(talent.id))

    @staticmethod
    def _self_tags(db: Session, tenant_id: int, talent_id: int) -> list[SelfTagOut]:
        rels = tag_dao.list_talent_tags(db, tenant_id, talent_id)
        if not rels:
            return []
        name_map = {str(r.tag_code): r.tag_name for r in tag_dao.list_tags(db, tenant_id, limit=10_000)}
        return [
            SelfTagOut(
                tag_code=str(r.tag_code),
                tag_name=name_map.get(str(r.tag_code), str(r.tag_code)),
                source=int(r.source),
                source_name=tag_source_name_of(int(r.source)),
            )
            for r in rels
        ]

    @staticmethod
    def _field_sources(
        db: Session, tenant_id: int, talent_id: int, field_codes: list[str]
    ) -> dict[str, int]:
        """每个字段的**最近一次**变更来源（``ChangeSource``）。

        取最近 200 条留痕、按字段取首条（已按 ``created_at`` 倒序）——
        「这个值是怎么来的」在查阅权里必须答得出来，而来源只有留痕知道
        （``talent_base`` 本身没有「来源」列）。
        """
        rows = talent_dao.list_change_logs(db, tenant_id, talent_id=talent_id, limit=200)
        out: dict[str, int] = {}
        for row in rows:
            code = str(row.field_name or "")
            if code in field_codes and code not in out:
                out[code] = int(row.change_source)
        return out

    @classmethod
    def _render_export(
        cls,
        db: Session,
        tenant_id: int,
        talent: TalentBase,
        row: PrivacyRequest,
        now: datetime,
    ) -> str:
        """生成个人数据包正文（JSON 字符串）。

        内容包含四块（F-3.11 数据可携带权的「完整、结构化、通用格式」）：
        档案字段（**含来源**）/ 标签 / 变更摘要 / 申请元信息。

        🔴 证件号在数据包里**仍是脱敏形态**：数据包一旦落地到本人设备就脱离了平台管控，
        给一份完整证件号等于把「最小必要」的边界交给用户自己判断（口径 ③）。
        完整证件号的核验走线下。
        """
        fields, tags = cls._profile_fields(db, tenant_id, talent)
        work_exps = cls._work_exps(db, tenant_id, int(talent.id))
        project_exps = cls._project_exps(db, tenant_id, int(talent.id))
        payload = {
            "export_meta": {
                "request_no": str(row.request_no),
                "talent_id": str(talent.id),
                "generated_at": now.strftime(DATETIME_FORMAT),
                "format": "json",
                "note": "本数据包由 AI 数字化人才平台按《个人信息保护法》可携带权生成",
            },
            "profile": {
                "name": talent.name,
                "gender": int(talent.gender) if talent.gender is not None else None,
                "birth_date": talent.birth_date.isoformat() if talent.birth_date else None,
                "education_level": talent.education_level,
                "school": talent.school,
                "major": talent.major,
                "work_years": int(talent.work_years) if talent.work_years is not None else None,
                "phone": decrypt_sensitive(talent.phone_enc),
                "email": decrypt_sensitive(talent.email_enc),
                "id_card_masked": mask_encrypted_id_card(talent.id_card_enc),
                "data_status": int(talent.data_status),
                "version_no": int(talent.version_no),
            },
            "fields": [f.model_dump(mode="json") for f in fields],
            "tags": [t.model_dump(mode="json") for t in tags],
            "work_exps": [item.model_dump(mode="json") for item in work_exps],
            "project_exps": [item.model_dump(mode="json") for item in project_exps],
            "change_summary": cls._change_summary(db, tenant_id, int(talent.id)),
        }
        # ``ensure_ascii=False``：数据包是给人看的，中文转义成 \uXXXX 会让它难以核对
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _work_exps(db: Session, tenant_id: int, talent_id: int) -> list[Any]:
        from app.schemas.talent_master import WorkExpOut

        return [
            WorkExpOut(
                id=row.id,
                company=str(row.company),
                dept_name=row.dept_name,
                title=row.position_name,
                start_date=row.start_date.isoformat() if row.start_date else None,
                end_date=row.end_date.isoformat() if row.end_date else None,
                is_current=bool(row.is_current),
                description=row.description,
            )
            for row in talent_dao.list_work_exps(db, int(tenant_id), int(talent_id))
        ]

    @staticmethod
    def _project_exps(db: Session, tenant_id: int, talent_id: int) -> list[Any]:
        from app.schemas.talent_master import ProjectExpOut

        return [
            ProjectExpOut(
                id=row.id,
                project_name=str(row.project_name),
                role=row.role_name,
                start_date=row.start_date.isoformat() if row.start_date else None,
                end_date=row.end_date.isoformat() if row.end_date else None,
                description=row.description,
                tech_stack=[str(item) for item in (row.tech_stack or [])],
            )
            for row in talent_dao.list_project_exps(db, int(tenant_id), int(talent_id))
        ]

    @staticmethod
    def _change_summary(db: Session, tenant_id: int, talent_id: int) -> dict[str, Any]:
        """变更摘要（**不含前值/后值**，只给计数与时间范围）。

        为什么数据包里不给完整的变更明细：留痕里可能含他人信息
        （合并留痕的 ``operator_id`` 会指到别的员工）。本人有权知道
        「自己的档案被改过几次、什么时候改的、什么来源改的」——
        这已足以支持「我要对哪次变更提异议」，而不会把同事的操作明细交出去。
        """
        rows = talent_dao.list_change_logs(db, tenant_id, talent_id=talent_id, limit=200)
        by_source: dict[str, int] = {}
        for r in rows:
            key = change_source_name_of(int(r.change_source)) or str(int(r.change_source))
            by_source[key] = by_source.get(key, 0) + 1
        total = talent_dao.count_change_logs(db, tenant_id, talent_id=talent_id)
        return {
            "total": total,
            "by_source": by_source,
            "earliest": (
                min(r.created_at for r in rows).strftime(DATETIME_FORMAT) if rows else None
            ),
            "latest": (
                max(r.created_at for r in rows).strftime(DATETIME_FORMAT) if rows else None
            ),
        }

    # ==================================================================
    # 内部：杂项
    # ==================================================================
    @staticmethod
    def _next_request_no(db: Session, tenant_id: int) -> str:
        """``PRVyyyymmddNNN``（与导入任务 ``IMPyyyymmddNNN`` 同构）。

        ⚠️ 序号**不是唯一性防线**：并发撞号由 ``uk_tenant_privacy_no`` 兜底（40901）。
        与导入任务同一取舍 —— 为了一个纯展示用的流水号加一道分布式锁不值得。
        """
        day = datetime.now().strftime("%Y%m%d")
        seq = privacy_dao.count_requests_today(db, tenant_id, request_no_prefix=f"PRV{day}") + 1
        return build_request_no(day, seq)

    @classmethod
    def _find_open_request(
        cls,
        db: Session,
        tenant_id: int,
        talent_id: int,
        request_type: int,
        field_code: str | None = None,
    ) -> PrivacyRequest | None:
        """同一档案（同一字段）是否已有**未终态**的申请。"""
        rows = privacy_dao.list_requests(
            db,
            tenant_id,
            request_type=int(request_type),
            talent_id=int(talent_id),
            limit=50,
        )
        for row in rows:
            if int(row.status) not in OPEN_STATUSES:
                continue
            if field_code is not None and str(row.field_code or "") != field_code:
                continue
            return row
        return None

    @staticmethod
    def _fmt(value: datetime | None) -> str | None:
        return value.strftime(DATETIME_FORMAT) if value is not None else None

    @staticmethod
    def _gender_name(value: Any) -> str | None:
        return label_of(Gender, int(value)) if value is not None else None

    @staticmethod
    def _data_status_name(value: Any) -> str | None:
        return TalentDataStatus.name_of(int(value)) if value is not None else None


__all__ = ["MANAGER_ROLES", "SLA_SCAN_LIMIT", "PrivacyService"]
