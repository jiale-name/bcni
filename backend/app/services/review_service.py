"""分层复核工作台（CU-16，API-44~49，F-3.3 / F-3.3.1）。

🔴 队列默认只给**高影响**低置信字段（接口 §4.2）：低影响字段不入默认队列，
``sample_only=true`` 时才返回抽样集 —— F-3.3 漏斗的落点在**查询侧**就分流，
而不是「全查出来再过滤」（那样分页和总数都会被污染）。

🔴 并发冲突 = **部分成功**（code=0）：被他人抢先的条目进 ``conflicted[]``，
❌ 不整批回滚 —— 一个冲突让 HR 丢掉整批复核结果，比冲突本身糟糕得多。
状态流转用条件更新（compare-and-set）：``review_status == 0`` 才允许流转。

🔴 每次确认/修正写 ``talent_change_log``（TBL-22，``change_source=1``），
``change_log_ids`` 回传 —— 供前端做「撤销本次确认」入口（F-3.10）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import CurrentPrincipal
from app.dao import talent_dao
from app.exceptions import BizError, ErrorCode, NotFoundError
from app.models.enums import (
    ChangeSource,
    ParseReviewStatus,
    TalentDataStatus,
)
from app.models.talent import TalentChangeLog
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.schemas.review import (
    CONFIRM_BATCH_LIMIT,
    ReviewConfirmItem,
    ReviewConfirmOut,
    ReviewCorrectOut,
    ReviewDashboardOut,
    ReviewDetailOut,
    ReviewQueueFilter,
    ReviewQueueItem,
    ReviewQueueMeta,
    estimated_minutes,
    mask_for_role,
    review_status_name_of,
)
from app.services.audit_service import AuditService

#: ``impact_level`` → 展示名
IMPACT_LABELS = {"HIGH": "高影响", "LOW": "低影响"}


class ReviewService:
    """复核队列与工作台（API-44~49）。"""

    # ------------------------------------------------------------------
    # API-44 复核队列
    # ------------------------------------------------------------------
    @classmethod
    def queue(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        filters: ReviewQueueFilter,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ReviewQueueItem], ReviewQueueMeta, int, int, int]:
        can_see_pii = cls._can_see_pii(principal)
        confidence_max = filters.confidence_max
        if confidence_max is None:
            confidence_max = float(settings.PARSE_CONFIDENCE_THRESHOLD)

        # 影响面分流：HIGH / LOW(sample_only) / ALL
        if filters.sample_only:
            impact_levels: list[str] | None = ["LOW"]
        elif filters.impact_level == "HIGH":
            impact_levels = ["HIGH"]
        elif filters.impact_level == "LOW":
            impact_levels = ["LOW"]
        else:
            impact_levels = None

        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = talent_dao.list_pending_fields(
            db,
            principal.tenant_id,
            task_id=int(filters.task_id) if filters.task_id else None,
            impact_levels=impact_levels,
            field_codes=filters.code_list or None,
            confidence_max=confidence_max,
            limit=size,
            offset=(current - 1) * size,
        )
        total = talent_dao.count_pending_fields(
            db,
            principal.tenant_id,
            task_id=int(filters.task_id) if filters.task_id else None,
            impact_levels=impact_levels,
            field_codes=filters.code_list or None,
            confidence_max=confidence_max,
        )
        high_total = talent_dao.count_pending_fields(
            db,
            principal.tenant_id,
            task_id=int(filters.task_id) if filters.task_id else None,
            impact_levels=["HIGH"],
            field_codes=filters.code_list or None,
            confidence_max=confidence_max,
        )
        low_total = talent_dao.count_pending_fields(
            db,
            principal.tenant_id,
            task_id=int(filters.task_id) if filters.task_id else None,
            impact_levels=["LOW"],
            field_codes=filters.code_list or None,
            confidence_max=confidence_max,
        )

        warning = None
        if high_total > CONFIRM_BATCH_LIMIT:
            warning = f"本轮待复核 {high_total} 条，超过 {CONFIRM_BATCH_LIMIT} 条建议拆批处理"
        meta = ReviewQueueMeta(
            high_impact_total=high_total,
            low_impact_sampled_total=max(0, low_total),
            warning=warning,
            estimated_minutes=estimated_minutes(
                high_total, seconds_per_item=settings.REVIEW_AVG_SECONDS_PER_ITEM
            ),
        )
        items = [
            ReviewQueueItem(
                field_id=f.id,
                talent_id=t.id,
                talent_name=t.name,
                field_code=f.field_name,
                field_name=f.field_name,
                raw_value=mask_for_role(f.field_name, f.raw_text, can_see_pii=can_see_pii),
                parsed_value=mask_for_role(f.field_name, f.parsed_value, can_see_pii=can_see_pii),
                confidence=float(f.confidence) if f.confidence is not None else None,
                impact_level=f.impact_level,
                impact_level_name=IMPACT_LABELS.get(f.impact_level, f.impact_level),
                review_status=int(f.review_status),
                review_status_name=review_status_name_of(int(f.review_status)),
                source_file=f.source_file,
                source_locator=f.source_locator,
            )
            for f, t in rows
        ]
        return items, meta, total, current, size

    # ------------------------------------------------------------------
    # API-45 单条复核详情
    # ------------------------------------------------------------------
    @classmethod
    def get_detail(
        cls, db: Session, principal: CurrentPrincipal, field_id: int
    ) -> ReviewDetailOut:
        can_see_pii = cls._can_see_pii(principal)
        field = talent_dao.get_field(db, principal.tenant_id, field_id)
        if field is None:
            # ⚠️ 口径：字段行不可见 = 「本域内不存在」→ 40401（DAO 强制注入 tenant_id，
            #    跨租户读取自然收敛为 None）。字段挂在档案上，档案的域内越权
            #    由列表侧（API-44 的 DataScopeFilter 收窄）承担 —— 单条接口不重复放大语义。
            raise NotFoundError("复核条目不存在")
        talent = talent_dao.get_talent(db, principal.tenant_id, int(field.talent_id))
        return ReviewDetailOut(
            field_id=field.id,
            talent_id=field.talent_id,
            talent_name=talent.name if talent else "",
            field_code=field.field_name,
            field_name=field.field_name,
            raw_value=mask_for_role(field.field_name, field.raw_text, can_see_pii=can_see_pii),
            parsed_value=mask_for_role(field.field_name, field.parsed_value, can_see_pii=can_see_pii),
            confidence=float(field.confidence) if field.confidence is not None else None,
            impact_level=field.impact_level,
            impact_level_name=IMPACT_LABELS.get(field.impact_level, field.impact_level),
            review_status=int(field.review_status),
            review_status_name=review_status_name_of(int(field.review_status)),
            source_file=field.source_file,
            source_locator=field.source_locator,
        )

    # ------------------------------------------------------------------
    # API-46 批量确认（核心：部分成功 + 留痕 + 档案状态收口）
    # ------------------------------------------------------------------
    @classmethod
    def confirm(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        items: list[ReviewConfirmItem],
        *,
        force: bool = False,
        idempotent_key: str | None = None,
    ) -> ReviewConfirmOut:
        if len(items) > CONFIRM_BATCH_LIMIT and not force:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                f"单次批量确认不得超过 {CONFIRM_BATCH_LIMIT} 条，请拆批或在确认无并发风险后加 force=true",
                {"received": len(items), "limit": CONFIRM_BATCH_LIMIT},
            )
        if len(items) > 1000:  # 硬上限：force 也不允许无限大（防一次事务写爆）
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                "单次批量确认的硬上限为 1000 条",
                {"received": len(items), "limit": 1000},
            )

        now = datetime.now()
        confirmed = corrected = skipped = 0
        conflicted: list[dict[str, str | int]] = []
        change_log_ids: list[int] = []
        touched_talents: set[int] = set()

        for item in items:
            field = talent_dao.get_field(db, principal.tenant_id, int(item.field_id))
            if field is None:
                conflicted.append({"field_id": str(item.field_id), "reason": "not_found"})
                continue
            target_status: int
            new_value: str | None = None
            if item.action == "ACCEPT_AI":
                target_status = int(ParseReviewStatus.CONFIRMED)
            elif item.action == "ACCEPT_RAW":
                target_status = int(ParseReviewStatus.CONFIRMED)
                new_value = field.raw_text  # 采纳原文值
            else:  # EDIT
                target_status = int(ParseReviewStatus.CORRECTED)
                new_value = (item.value or "").strip()

            affected = talent_dao.update_parse_field_review(
                db,
                principal.tenant_id,
                int(field.id),
                new_status=target_status,
                parsed_value=new_value,
                # 人工修正的置信度就是「人担保」（与解析流水线的人工修正口径一致）
                confidence=1.0 if target_status == int(ParseReviewStatus.CORRECTED) else None,
                reviewed_by=principal.user_id,
                reviewed_at=now,
            )
            if affected == 0:
                conflicted.append(
                    {
                        "field_id": str(item.field_id),
                        "reason": "already_handled",
                        "review_status": int(field.review_status),
                    }
                )
                continue

            before = field.parsed_value
            talent_dao.insert_change_log(
                db,
                TalentChangeLog(
                    tenant_id=principal.tenant_id,
                    talent_id=int(field.talent_id),
                    change_source=int(ChangeSource.MANUAL_REVIEW),
                    field_name=field.field_name,
                    before_value=before[:512] if before else None,
                    after_value=(new_value or before or "")[:512],
                    operator_id=principal.user_id,
                ),
            )
            db.flush()
            latest = talent_dao.list_change_logs(
                db, principal.tenant_id, talent_id=int(field.talent_id), limit=1
            )
            if latest:
                change_log_ids.append(int(latest[0].id))

            if target_status == int(ParseReviewStatus.CORRECTED):
                corrected += 1
            else:
                confirmed += 1
            touched_talents.add(int(field.talent_id))

        # 档案状态收口：没有待复核字段了 → data_status=1 有效（一次显式流转 + 留痕）
        for talent_id in sorted(touched_talents):
            per_talent = talent_dao.list_fields_by_talent(db, principal.tenant_id, talent_id)
            still_pending = any(
                int(f.review_status) == int(ParseReviewStatus.PENDING) for f in per_talent
            )
            if not still_pending:
                talent = talent_dao.get_talent(db, principal.tenant_id, talent_id)
                if talent is not None and int(talent.data_status) == int(
                    TalentDataStatus.PENDING_REVIEW
                ):
                    talent_dao.set_talent_data_status(db, talent, int(TalentDataStatus.ACTIVE))
                    talent_dao.insert_change_log(
                        db,
                        TalentChangeLog(
                            tenant_id=principal.tenant_id,
                            talent_id=talent_id,
                            change_source=int(ChangeSource.MANUAL_REVIEW),
                            field_name="data_status",
                            before_value="0",
                            after_value="1",
                            operator_id=principal.user_id,
                        ),
                    )
        db.commit()

        AuditService.record(
            AuditAction.REVIEW_CONFIRM,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT_FIELD,
            target_id=None,
            after={
                "confirmed": confirmed,
                "corrected": corrected,
                "conflicted": len(conflicted),
                "talents": sorted(touched_talents)[:50],
                "idempotent_key": idempotent_key,
            },
        )
        return ReviewConfirmOut(
            confirmed_count=confirmed,
            corrected_count=corrected,
            skipped_count=skipped,
            conflicted=conflicted,
            change_log_ids=change_log_ids,
        )

    # ------------------------------------------------------------------
    # API-47 修正单字段
    # ------------------------------------------------------------------
    @classmethod
    def correct_field(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        field_id: int,
        value: str,
    ) -> ReviewCorrectOut:
        item = ReviewConfirmItem(field_id=field_id, action="EDIT", value=value)
        out = cls.confirm(db, principal, [item])
        if out.conflicted:
            raise BizError(
                ErrorCode.CONCURRENT_CONFLICT,
                "该条目已被他人处理，请刷新后重试",
                out.conflicted[0],
            )
        field = talent_dao.get_field(db, principal.tenant_id, field_id)
        return ReviewCorrectOut(
            field_id=field_id,
            talent_id=field.talent_id if field else 0,
            review_status=int(ParseReviewStatus.CORRECTED),
            review_status_name=review_status_name_of(int(ParseReviewStatus.CORRECTED)),
            change_log_id=out.change_log_ids[0] if out.change_log_ids else None,
        )

    # ------------------------------------------------------------------
    # API-48 标记免复核
    # ------------------------------------------------------------------
    @classmethod
    def skip_field(
        cls, db: Session, principal: CurrentPrincipal, field_id: int
    ) -> ReviewCorrectOut:
        now = datetime.now()
        field = talent_dao.get_field(db, principal.tenant_id, field_id)
        if field is None:
            raise NotFoundError("复核条目不存在")
        affected = talent_dao.update_parse_field_review(
            db,
            principal.tenant_id,
            field_id,
            new_status=int(ParseReviewStatus.SKIPPED),
            reviewed_by=principal.user_id,
            reviewed_at=now,
        )
        if affected == 0:
            raise BizError(
                ErrorCode.CONCURRENT_CONFLICT,
                "该条目已被他人处理，请刷新后重试",
                {"field_id": str(field_id), "review_status": int(field.review_status)},
            )
        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=int(field.talent_id),
                change_source=int(ChangeSource.MANUAL_REVIEW),
                field_name=field.field_name,
                before_value=str(int(ParseReviewStatus.PENDING)),
                after_value=str(int(ParseReviewStatus.SKIPPED)),
                operator_id=principal.user_id,
            ),
        )
        db.commit()
        AuditService.record(
            AuditAction.REVIEW_SKIP,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT_FIELD,
            target_id=field_id,
            after={"talent_id": str(field.talent_id), "field": field.field_name},
        )
        return ReviewCorrectOut(
            field_id=field_id,
            talent_id=field.talent_id,
            review_status=int(ParseReviewStatus.SKIPPED),
            review_status_name=review_status_name_of(int(ParseReviewStatus.SKIPPED)),
            change_log_id=None,
        )

    # ------------------------------------------------------------------
    # API-49 复核看板
    # ------------------------------------------------------------------
    @classmethod
    def dashboard(cls, db: Session, principal: CurrentPrincipal) -> ReviewDashboardOut:
        counts = talent_dao.count_fields_by_status(db, principal.tenant_id, task_id=None)
        pending = counts.get(int(ParseReviewStatus.PENDING), 0)
        by_field_rows = talent_dao.list_field_completeness(db, principal.tenant_id)
        by_field: list[dict[str, str | int | float]] = [
            {"field_code": code, "total": total, "rate": (round(filled / total, 3) if total else 0.0)}
            for code, filled, total in by_field_rows
        ]
        return ReviewDashboardOut(
            pending_total=pending,
            confirmed_total=counts.get(int(ParseReviewStatus.CONFIRMED), 0),
            corrected_total=counts.get(int(ParseReviewStatus.CORRECTED), 0),
            skipped_total=counts.get(int(ParseReviewStatus.SKIPPED), 0),
            by_field=by_field,
            estimated_remaining_minutes=estimated_minutes(
                pending, seconds_per_item=settings.REVIEW_AVG_SECONDS_PER_ITEM
            ),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _can_see_pii(principal: CurrentPrincipal) -> bool:
        """AC-20/21：HR/ADM 明文（业务必需），MGR 及更窄角色脱敏。"""
        return principal.has_role("ADM", "HR")
