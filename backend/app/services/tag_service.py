"""受控标签词表与打标闭环（CU-17，API-50~56，F-3.4 / F-3.4.1）。

🔴 三条不变式（SQ-8 / 接口 §4.4，全部由测试钉死）
================================================
1. **AI 不得自由造词**：API-53 的建议落 ``status=0 待审核``（``source=1``），
   只有 API-54 审核通过（``status=1``）后才可被 ``talent_tag_rel`` 引用；
2. **打标只能选启用项**：API-56 / AI 打标的每个 ``tag_code`` 必须存在于
   ``talent_tag_dict`` 且 ``status=1``，否则 **40001**（不是 40401 ——
   「引用了不存在的词表项」是**请求写错了**，不是资源找不到）；
3. **审核通过 ≠ 历史回填**：标签启用后历史档案**不自动回填**
   （回填须 HR 显式触发重解析，``change_source=3``）—— 避免静默改写历史。

⚠️ 词表变更的留痕走**审计日志**（TBL-28），不走 TBL-22：``talent_change_log.talent_id``
是 NOT NULL，词表变更没有档案可挂（上游不一致，处置同 CU-07 API-26 的先例）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.tagdict import TagDictValidator
from app.core.tenant import CurrentPrincipal
from app.dao import tag_dao, talent_dao
from app.exceptions import BizError, ErrorCode, NotFoundError, PermissionDeniedError
from app.models.enums import TagDictStatus, TagSource
from app.models.talent import TalentTagDict
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.schemas.tag import (
    TagCreateRequest,
    TagDictOut,
    TagReviewOut,
    TagSuggestOut,
    TagTreeNode,
    TagUpdateRequest,
    TalentTagOut,
    TalentTagsOut,
    tag_source_name_of,
    tag_status_name_of,
)
from app.services.audit_service import AuditService


class TagService:
    """标签词表与人才打标（API-50~56）。"""

    # ------------------------------------------------------------------
    # API-50 词表（三级树 + 平铺列表）
    # ------------------------------------------------------------------
    @classmethod
    def tree(
        cls, db: Session, principal: CurrentPrincipal, *, include_pending: bool = False
    ) -> list[TagTreeNode]:
        statuses = None if include_pending else int(TagDictStatus.ENABLED)
        rows = tag_dao.list_tags(
            db, principal.tenant_id, status=statuses, limit=10_000
        )
        outs = [cls.to_tag_out(r) for r in rows]
        # 组树：l1 → l2 → l3（三级分类；缺失层级用「未分类」兜底，树不断枝）
        UNCATEGORIZED = "未分类"
        root: dict[str, dict[str, list[TagDictOut]]] = {}
        for out in outs:
            l1 = out.category_l1 or UNCATEGORIZED
            l2 = out.category_l2 or UNCATEGORIZED
            l3 = out.category_l3 or UNCATEGORIZED
            root.setdefault(l1, {}).setdefault(l2, {}).setdefault(l3, []).append(out)
        return [
            TagTreeNode(
                category=l1,
                level=1,
                children=[
                    TagTreeNode(
                        category=l2,
                        level=2,
                        children=[
                            TagTreeNode(category=l3, level=3, tags=l3_tags)
                            for l3, l3_tags in sorted(l2_children.items())
                        ],
                    )
                    for l2, l2_children in sorted(l2_map.items())
                ],
            )
            for l1, l2_map in sorted(root.items())
        ]

    @classmethod
    def list_flat(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        status: int | None = None,
        keyword: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[TagDictOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = tag_dao.list_tags(
            db,
            principal.tenant_id,
            status=status,
            keyword=keyword,
            limit=size,
            offset=(current - 1) * size,
        )
        total = tag_dao.count_tags(db, principal.tenant_id, status=status, keyword=keyword)
        return [cls.to_tag_out(r) for r in rows], total, current, size

    # ------------------------------------------------------------------
    # API-51 新增（管理员直接启用）
    # ------------------------------------------------------------------
    @classmethod
    def create_tag(
        cls, db: Session, principal: CurrentPrincipal, payload: TagCreateRequest
    ) -> TagDictOut:
        tag_code = (payload.tag_code or "").strip() or TagDictValidator.derive_code(payload.tag_name)
        if not TagDictValidator.is_valid_code(tag_code):
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"标签编码 {tag_code!r} 不合法（大写字母开头，仅大写字母/数字/下划线）",
                {"field": "tag_code"},
            )
        existing = tag_dao.get_tag_by_code(db, principal.tenant_id, tag_code)
        if existing is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"标签码 {tag_code} 已存在",
                {"existing_tag_id": str(existing.id)},
            )
        name_dup = tag_dao.get_tag_by_name(db, principal.tenant_id, payload.tag_name.strip())
        if name_dup is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"同名标签「{payload.tag_name}」已存在（{name_dup.tag_code}）",
                {"existing_tag_id": str(name_dup.id)},
            )
        tag = tag_dao.insert_tag(
            db,
            TalentTagDict(
                tenant_id=principal.tenant_id,
                tag_code=tag_code,
                tag_name=payload.tag_name.strip(),
                category_l1=payload.category_l1,
                category_l2=payload.category_l2,
                category_l3=payload.category_l3,
                synonyms=payload.synonyms,
                status=int(TagDictStatus.ENABLED),  # 人工创建直接启用（接口 §4.4）
                source=int(TagSource.MANUAL),
                approved_by=principal.user_id,
                approved_at=datetime.now(),
            ),
        )
        db.commit()
        AuditService.record(
            AuditAction.TAG_CREATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TAG,
            target_id=tag.id,
            after={"tag_code": tag_code, "tag_name": tag.tag_name, "status": int(TagDictStatus.ENABLED)},
        )
        return cls.to_tag_out(tag)

    # ------------------------------------------------------------------
    # API-52 修改（含启用/停用；tag_code 不可改）
    # ------------------------------------------------------------------
    @classmethod
    def update_tag(
        cls, db: Session, principal: CurrentPrincipal, tag_id: int, payload: TagUpdateRequest
    ) -> TagDictOut:
        tag = tag_dao.get_tag(db, principal.tenant_id, tag_id)
        if tag is None:
            raise NotFoundError("标签不存在")
        before = {"name": tag.tag_name, "status": int(tag.status), "synonyms": list(tag.synonyms or [])}
        fields: dict[str, Any] = {}
        if payload.tag_name is not None:
            name_dup = tag_dao.get_tag_by_name(db, principal.tenant_id, payload.tag_name.strip())
            if name_dup is not None and int(name_dup.id) != int(tag.id):
                raise BizError(
                    ErrorCode.UNIQUE_CONFLICT,
                    f"同名标签「{payload.tag_name}」已存在（{name_dup.tag_code}）",
                    {"existing_tag_id": str(name_dup.id)},
                )
            fields["tag_name"] = payload.tag_name.strip()
        for attr in ("category_l1", "category_l2", "category_l3"):
            value = getattr(payload, attr)
            if value is not None:
                fields[attr] = value
        if payload.synonyms is not None:
            fields["synonyms"] = payload.synonyms
        if payload.action == "enable":
            fields["status"] = int(TagDictStatus.ENABLED)
        elif payload.action == "disable":
            # 停用「有人引用」的标签不拦截 —— 已有引用照常展示，只是不能再**新**打（写侧校验兜住）
            fields["status"] = int(TagDictStatus.DISABLED)
        if fields:
            tag_dao.update_tag_fields(db, tag, fields)
        db.commit()
        AuditService.record(
            AuditAction.TAG_UPDATE if payload.action != "disable" else AuditAction.TAG_DISABLE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TAG,
            target_id=tag_id,
            before=before,
            after={"name": tag.tag_name, "status": int(tag.status), "synonyms": list(tag.synonyms or [])},
        )
        return cls.to_tag_out(tag)

    # ------------------------------------------------------------------
    # API-53 提交 AI 建议标签
    # ------------------------------------------------------------------
    @classmethod
    def submit_suggestion(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        payload: Any,
        *,
        suggested_by: int | None = None,
        from_ai: bool = False,
    ) -> TagSuggestOut:
        """AI/人工提交建议（**不得直接入库为启用项**）。

        :param suggested_by: AI 触发时为系统账号或 ``None``
        :param from_ai: ``True`` → ``source=1 AI``
        """
        tag_name = payload.tag_name.strip()
        # 已有启用项（或同义词命中）→ 没必要再建议：40901，data 带现有项
        entries = tag_dao.list_enabled_tag_entries(db, principal.tenant_id)
        normalized = TagDictValidator.normalize_to_dict(tag_name, entries)
        if normalized is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"「{tag_name}」已存在于词表（{normalized}），无需建议",
                {"existing_tag_id": str(normalized)},
            )
        dup = tag_dao.find_tag_suggestion(db, principal.tenant_id, tag_name, payload.category_code)
        if dup is not None:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                "该建议已在待审核队列中",
                {"existing_suggestion_id": str(dup.id)},
            )
        tag = tag_dao.insert_tag(
            db,
            TalentTagDict(
                tenant_id=principal.tenant_id,
                tag_code=f"SUGGEST_{tag_dao.count_tags(db, principal.tenant_id) + 1:06d}",
                tag_name=tag_name,
                category_l3=payload.category_code,
                synonyms=payload.synonyms,
                status=int(TagDictStatus.PENDING_AUDIT),
                source=int(TagSource.AI) if from_ai else int(TagSource.MANUAL),
                suggested_by=suggested_by,
            ),
        )
        db.commit()
        AuditService.record(
            AuditAction.TAG_SUGGEST,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TAG,
            target_id=tag.id,
            after={
                "tag_name": tag_name,
                "category_code": payload.category_code,
                "from_ai": from_ai,
                "reason": getattr(payload, "reason", None),
            },
        )
        return TagSuggestOut(
            suggestion_id=tag.id,
            status=int(tag.status),
            status_name=tag_status_name_of(int(tag.status)),
            tag_code=None,
        )

    # ------------------------------------------------------------------
    # API-54 审核建议
    # ------------------------------------------------------------------
    @classmethod
    def review_suggestion(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        suggestion_id: int,
        *,
        decision: str,
        tag_code: str | None,
        reject_reason: str | None,
    ) -> TagReviewOut:
        tag = tag_dao.get_tag(db, principal.tenant_id, suggestion_id)
        if tag is None or int(tag.status) != int(TagDictStatus.PENDING_AUDIT):
            raise NotFoundError("待审核建议不存在（或已处理）")
        now = datetime.now()
        if decision == "approve":
            code = (tag_code or "").strip() or TagDictValidator.derive_code(tag.tag_name)
            if not TagDictValidator.is_valid_code(code):
                raise BizError(
                    ErrorCode.PARAM_INVALID,
                    f"标签编码 {code!r} 不合法（大写字母开头，仅大写字母/数字/下划线）",
                    {"field": "tag_code"},
                )
            clash = tag_dao.get_tag_by_code(db, principal.tenant_id, code)
            if clash is not None:
                raise BizError(
                    ErrorCode.UNIQUE_CONFLICT,
                    f"标签码 {code} 已存在",
                    {"existing_tag_id": str(clash.id)},
                )
            tag_dao.update_tag_fields(
                db,
                tag,
                {
                    "tag_code": code,
                    "status": int(TagDictStatus.ENABLED),
                    "approved_by": principal.user_id,
                    "approved_at": now,
                },
            )
            db.commit()
            AuditService.record(
                AuditAction.TAG_REVIEW,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                target_type=AuditTarget.TAG,
                target_id=suggestion_id,
                before={"status": int(TagDictStatus.PENDING_AUDIT)},
                after={"status": int(TagDictStatus.ENABLED), "tag_code": code},
            )
            return TagReviewOut(
                suggestion_id=tag.id,
                status=int(tag.status),
                status_name=tag_status_name_of(int(tag.status)),
                tag_code=code,
                activated_at=now,
            )
        # reject
        if not (reject_reason or "").strip():
            raise BizError(
                ErrorCode.PARAM_INVALID, "驳回时 reject_reason 必填", {"field": "reject_reason"}
            )
        tag_dao.update_tag_fields(db, tag, {"status": int(TagDictStatus.DISABLED)})
        db.commit()
        AuditService.record(
            AuditAction.TAG_REVIEW,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TAG,
            target_id=suggestion_id,
            before={"status": int(TagDictStatus.PENDING_AUDIT)},
            after={"status": int(TagDictStatus.DISABLED), "reason": reject_reason},
        )
        return TagReviewOut(
            suggestion_id=tag.id,
            status=int(tag.status),
            status_name=tag_status_name_of(int(tag.status)),
            tag_code=None,
            activated_at=None,
        )

    # ------------------------------------------------------------------
    # API-55 人才标签（读）
    # ------------------------------------------------------------------
    @classmethod
    def talent_tags(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int
    ) -> TalentTagsOut:
        talent = talent_dao.get_talent(db, principal.tenant_id, talent_id)
        if talent is None:
            raise NotFoundError("档案不存在")
        # 档案是**有行级数据域**的表：域外访问统一 40301（CR-8，防资源探测）
        if not cls._can_access_talent(principal, talent):
            raise PermissionDeniedError("无权限访问该档案")
        rels = tag_dao.list_talent_tags(db, principal.tenant_id, talent_id)
        entries = tag_dao.list_enabled_tag_entries(db, principal.tenant_id)
        # 停用标签的历史引用照常返回（数据还在），词表名查不到时用编码兜底
        all_rows = tag_dao.list_tags(db, principal.tenant_id, limit=10_000)
        name_map = {str(r.tag_code): r.tag_name for r in all_rows}
        return TalentTagsOut(
            talent_id=talent.id,
            tags=[
                TalentTagOut(
                    tag_code=str(r.tag_code),
                    tag_name=name_map.get(str(r.tag_code), str(r.tag_code)),
                    confidence=float(r.confidence) if r.confidence is not None else None,
                    source=int(r.source),
                    source_name=tag_source_name_of(int(r.source)),
                    category_l1=entries.get(str(r.tag_code), {}).get("category_l1"),  # type: ignore[union-attr]
                    category_l2=entries.get(str(r.tag_code), {}).get("category_l2"),  # type: ignore[union-attr]
                    category_l3=entries.get(str(r.tag_code), {}).get("category_l3"),  # type: ignore[union-attr]
                )
                for r in rels
            ],
        )

    # ------------------------------------------------------------------
    # API-56 人工调整标签（写，全量替换；只能选启用项）
    # ------------------------------------------------------------------
    @classmethod
    def assign_talent_tags(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int, tag_codes: list[str]
    ) -> TalentTagsOut:
        talent = talent_dao.get_talent(db, principal.tenant_id, talent_id)
        if talent is None:
            raise NotFoundError("档案不存在")
        # ① 每个编码必须是**启用**项（受控词表红线；数据在写侧拦，不是展示侧过滤）
        entries = tag_dao.list_enabled_tag_entries(db, principal.tenant_id)
        invalid = [c for c in tag_codes if c not in entries]
        if invalid:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"标签 {'、'.join(invalid[:5])} 不在启用词表中（只能选受控词表启用项）",
                {"invalid_tags": invalid},
            )
        # ② 全量替换（同角色-权限绑定语义；唯一键 + 软删旧行保证不重复）
        before_rels = tag_dao.list_talent_tags(db, principal.tenant_id, talent_id)
        before = sorted(str(r.tag_code) for r in before_rels)
        added, removed = tag_dao.replace_talent_tags(
            db,
            principal.tenant_id,
            talent_id,
            [{"tag_code": c, "confidence": None, "source": int(TagSource.MANUAL)} for c in tag_codes],
        )
        # ③ 变更留痕（TBL-22）：标签是档案数据的一部分（F-3.10 覆盖标签口径）
        from app.models.enums import ChangeSource
        from app.models.talent import TalentChangeLog

        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=talent_id,
                change_source=int(ChangeSource.MANUAL_REVIEW),
                field_name="tags",
                before_value=",".join(before)[:512] or None,
                after_value=",".join(sorted(tag_codes))[:512] or None,
                operator_id=principal.user_id,
            ),
        )
        db.commit()
        AuditService.record(
            AuditAction.TAG_ASSIGN,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            target_id=talent_id,
            before={"tags": before},
            after={"tags": sorted(tag_codes), "added": added, "removed": removed},
        )
        return cls.talent_tags(db, principal, talent_id)

    # ------------------------------------------------------------------
    @staticmethod
    def _can_access_talent(principal: CurrentPrincipal, talent: Any) -> bool:
        """档案访问判定（API-55 矩阵：ADM/HR/MGR 全域或子树；EMP 仅本人）。

        ⚠️ 简化口径（批次 10 登记）：MGR 的子树收窄依赖 ``dept_ids`` 解析，
        本批按「``data_scope=1``（ADM/HR）/``2``（MGR，子树由前端收窄 +
        列表侧 ``DataScopeFilter`` 承担）/``3``（EMP 本人）」三分。
        单条接口的完整子树校验随 CU-22（变更留痕 + 权利响应）统一补。
        """
        if talent is None:
            return True  # 不存在时交给 40401 分支，不做二次判定
        from app.models.enums import RoleDataScope

        scope = int(principal.data_scope)
        if scope == int(RoleDataScope.ALL):
            return True
        if scope == int(RoleDataScope.OWN_DEPT):
            return True  # 列表侧已收窄；单条访问的子树校验随 CU-22 统一补
        if scope == int(RoleDataScope.SELF):
            return talent.user_id is not None and int(talent.user_id) == int(principal.user_id)
        return False

    @staticmethod
    def to_tag_out(tag: TalentTagDict) -> TagDictOut:
        return TagDictOut(
            tag_id=tag.id,
            tag_code=str(tag.tag_code),
            tag_name=str(tag.tag_name),
            category_l1=tag.category_l1,
            category_l2=tag.category_l2,
            category_l3=tag.category_l3,
            synonyms=list(tag.synonyms or []),
            status=int(tag.status),
            status_name=tag_status_name_of(int(tag.status)),
            source=int(tag.source),
            source_name=tag_source_name_of(int(tag.source)),
            approved_by=tag.approved_by,
            approved_at=tag.approved_at,
            created_at=tag.created_at,
        )
