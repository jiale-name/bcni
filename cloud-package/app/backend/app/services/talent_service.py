"""人才档案主线服务（CU-29，API-30~35，F-3.2 / F-3.4 / F-3.10 / F-3.11）。

本模块承担四件事
================
1. **列表与详情的行级权限**：全部经由 :class:`~app.core.tenant.DataScopeFilter`
   推导 —— 这是 CR-11 的立意（「列表侧 MySQL 条件」与「检索侧 Milvus 表达式」
   必须同源），所以档案列表**不自己拼权限条件**。
2. **字段级裁剪**：PII 一律只下发脱敏形态（口径见
   :mod:`app.schemas.talent_master` 的模块 docstring ②）。
3. **变更留痕**：编辑走「先 diff → 再写 ``TalentChangeLog`` → 最后改主档」，
   与 F-3.10「任何变更都是一次版本推进」一致（``version_no`` 自增在 DAO 里）。
4. **导出留痕**：数据出库类动作必须可追（与 API-29 导出自身写审计同源）。

🔴 越权与不存在**统一 40301**（接口 API-31 备注）
================================================
:meth:`TalentService._locate` 先带行级条件查；落空时**只在内部日志**里区分
「不存在」与「越权」，对外永远同一个 40301。理由：状态码是探测资源存在性的信道，
一旦区分（403 vs 404），攻击者就能拿一个字典把全库 ID 扫一遍 ——
「哪些 ID 是真的」本身就是敏感信息。
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import (
    contact_hash_salt_configured,
    encrypt_sensitive,
    hash_contact,
    mask_encrypted_email,
    mask_encrypted_id_card,
    mask_encrypted_phone,
)
from app.core.logging import get_logger
from app.core.privacy import log_field_value
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.dao import dict_dao, org_dao, tag_dao, talent_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError, PermissionDeniedError
from app.models.enums import ChangeSource, Gender, RoleDataScope, TalentDataStatus
from app.models.talent import TalentBase, TalentChangeLog
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.talent_master import (
    DETAIL_NOTE_NO_DETAIL_TABLE,
    MAX_TOP_TAGS,
    ProfileDimensionOut,
    ProfileTagOut,
    TalentArchiveOut,
    TalentDetailOut,
    TalentExportOut,
    TalentExportRequest,
    TalentListItemOut,
    TalentListQuery,
    TalentProfileOut,
    TalentUpdateRequest,
    TopTagOut,
    education_code_to_int,
    education_int_to_code,
    month_str,
    parse_sort,
)
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService

logger = get_logger(__name__)

#: 导出 CSV 的列（**刻意不含 PII**：导出是数据出库，联系方式出库须走
#: 单独的合规审批 —— 见 :meth:`TalentService.export` 的说明）
_EXPORT_HEADERS: tuple[str, ...] = (
    "档案ID",
    "姓名",
    "性别",
    "学历",
    "院校",
    "专业",
    "工作年限",
    "归属部门",
    "档案状态",
    "版本号",
    "更新时间",
)


class TalentService:
    """API-30 ~ API-35。"""

    # ==================================================================
    # API-30 档案列表
    # ==================================================================
    @classmethod
    def list_talents(
        cls, db: Session, principal: CurrentPrincipal, query: TalentListQuery
    ) -> tuple[list[TalentListItemOut], int]:
        tenant_id = int(principal.tenant_id)
        dept_ids = cls._resolve_dept_ids(db, principal)

        # ---- ① 前端传参只能收窄（超出即 40301，不静默忽略）----
        narrowed = DataScopeFilter.assert_scope_narrowing(
            principal, query.filters.org_ids, dept_ids=dept_ids
        )
        org_ids = narrowed

        # ---- ② 状态口径（批次 23 反转：默认 ADM/HR =「待复核+有效」，其余仅「有效」；
        #      显式指定非 1 仍仅 ADM/HR）----
        data_status = cls._resolve_data_status(principal, query.filters.data_status)

        # ---- ③ 权限条件（唯一来源：DataScopeFilter）----
        scope = DataScopeFilter.mysql_condition(
            TalentBase, principal, dept_ids=dept_ids, owner_column="user_id"
        )
        sort_field, direction = parse_sort(query.sort)

        # 🔴 条件**只构造一次**，list 与 count 共用 —— 两处各写一遍早晚分叉，
        #    表现为 total 与实际行数对不上（「翻到最后一页是空的」）。
        conditions: dict[str, Any] = {
            "keyword": query.keyword,
            "org_ids": org_ids,
            "education_code": query.filters.education_code,
            "work_years_min": query.filters.work_years_min,
            "work_years_max": query.filters.work_years_max,
            "tag_codes": query.filters.tag_codes,
            "data_status": data_status,
        }
        offset = (int(query.page) - 1) * int(query.per_page)
        rows = talent_dao.list_talents(
            db,
            tenant_id,
            scope_clause=scope,
            sort_field=sort_field,
            sort_desc=(direction == "desc"),
            limit=int(query.per_page),
            offset=offset,
            **conditions,
        )
        total = talent_dao.count_talents(db, tenant_id, scope_clause=scope, **conditions)

        # ---- ④ 批量装配（避免 N+1）----
        org_names = cls._org_names(db, tenant_id, {int(r.org_id) for r in rows if r.org_id})
        edu_names = cls._education_names(db, tenant_id)
        rels = tag_dao.list_rels_by_talent_ids(db, tenant_id, [int(r.id) for r in rows])
        all_codes = sorted({str(rel.tag_code) for lst in rels.values() for rel in lst})
        tag_names = tag_dao.names_by_codes(db, tenant_id, all_codes)

        items = [
            cls._to_list_item(row, org_names=org_names, edu_names=edu_names, rels=rels, tag_names=tag_names)
            for row in rows
        ]
        return items, total

    # ==================================================================
    # API-31 档案详情
    # ==================================================================
    @classmethod
    def detail(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int, *, with_detail: bool = True
    ) -> TalentDetailOut:
        talent = cls._locate(db, principal, talent_id)
        return cls._to_detail(db, principal, talent, with_detail=with_detail)

    # ==================================================================
    # API-32 编辑档案（写变更留痕）
    # ==================================================================
    @classmethod
    def update(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int, req: TalentUpdateRequest
    ) -> TalentDetailOut:
        talent = cls._locate(db, principal, talent_id)

        # 已合并的档案是「从档」，它的一切字段都由合并结果决定；
        # 此时编辑会与合并快照（API-61 回滚依据）冲突 —— 校验恢复一致性时对不上。
        if int(talent.data_status) == int(TalentDataStatus.MERGED):
            raise IllegalStateError("已合并的档案不可编辑（如需修改，请先用 API-61 回滚合并）")

        payload = req.provided()
        if not payload:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "没有需要更新的字段",
                {"fields": [{"field": "body", "message": "至少提交一个非 null 字段"}]},
            )

        # ---- 手机号：加密 + 比对哈希必须同时更新 ----
        # 🔴 只改其一 → 档案里存着新号、去重却按旧号比，是最难发现的一类脏数据。
        if "phone" in payload and not contact_hash_salt_configured():
            raise BizError(
                ErrorCode.INTERNAL_ERROR,
                "服务未配置联系方式比对盐（CONTACT_HASH_SALT），无法更新手机号",
                {"fields": [{"field": "phone", "message": "服务端缺少 CONTACT_HASH_SALT"}]},
            )

        changes: list[tuple[str, str | None, str | None]] = []
        for field, value in payload.items():
            before_value, after_value = cls._apply_field(talent, field, value)
            if before_value != after_value:
                changes.append((field, before_value, after_value))

        if not changes:
            # 提交了字段但值都没变：不是错误，但要说清楚（避免前端以为「保存失败」）
            logger.info("档案编辑无实际变化 talent_id=%s user=%s", talent_id, principal.user_id)
            return cls._to_detail(db, principal, talent, with_detail=True)

        # ---- 留痕（TBL-22，只追加）----
        now = datetime.now()
        for field, before_value, after_value in changes:
            talent_dao.insert_change_log(
                db,
                TalentChangeLog(
                    tenant_id=int(principal.tenant_id),
                    talent_id=int(talent.id),
                    change_source=int(ChangeSource.MANUAL_REVIEW),
                    field_name=field,
                    before_value=cls._clip(before_value),
                    after_value=cls._clip(after_value),
                    operator_id=int(principal.user_id),
                ),
            )
        talent_dao.update_talent_fields(db, talent, {})

        AuditService.record(
            AuditAction.TALENT_EDIT,
            tenant_id=int(principal.tenant_id),
            user_id=int(principal.user_id),
            target_type=AuditTarget.TALENT,
            target_id=int(talent.id),
            after={"changed_fields": [c[0] for c in changes], "changed_at": now.isoformat()},
        )
        db.commit()
        return cls._to_detail(db, principal, talent, with_detail=True)

    # ==================================================================
    # API-33 归档（软删 + 向量同步清理）
    # ==================================================================
    @classmethod
    def archive(
        cls, db: Session, principal: CurrentPrincipal, talent_id: int
    ) -> TalentArchiveOut:
        talent = cls._locate(db, principal, talent_id)
        if int(talent.data_status) == int(TalentDataStatus.ARCHIVED):
            # 🔴 **幂等重放**：重复归档返回「已经是归档态」的成功结果，而不是报错。
            #    前端对不可逆动作带 Idempotency-Key 重试（网络抖动、用户连点）时，
            #    若这里抛 42201，重试会得到「失败」—— 而档案其实**早就归档好了**，
            #    用户会以为没成功而反复点击（对一个不可逆动作是最糟的交互）。
            return TalentArchiveOut(
                talent_id=int(talent.id),
                archived=True,
                vector_cleaned=False,
                vector_note="该档案此前已归档 —— 本次为幂等重放，未重复执行向量清理与状态流转",
                data_status=int(talent.data_status),
                data_status_name=TalentDataStatus.name_of(int(talent.data_status)),
                version_no=int(talent.version_no),
            )

        # ---- ① 先清向量（派生索引）：失败不回滚归档 ----
        # 顺序与「删除权」（API-77）**刻意相反**：那里必须「先向量后 MySQL」，
        # 因为删除权是合规动作，「向量里还留着」等于没删干净；
        # 而归档是业务动作，MySQL 才是权威 —— 向量清不掉只影响检索能召回，
        # 由重建脚本补跑即可，不该因此让「档案归档不了」变成常态故障。
        vector_cleaned = False
        vector_note: str | None = None
        try:
            from app.retrieval import get_retrieval_provider

            provider = get_retrieval_provider()
            result = provider.delete_all(
                db, talent_id=int(talent.id), tenant_id=int(principal.tenant_id)
            )
            failed = [name for name, count in result.items() if int(count) < 0]
            if failed:
                vector_note = f"以下集合清理失败，请运维补跑重建：{', '.join(failed)}"
            else:
                vector_cleaned = True
                if not result:
                    vector_note = "当前为关键词降级实现（无独立向量索引），无需清理"
        except Exception as exc:  # noqa: BLE001 — 向量是派生索引，清理失败不阻断归档
            vector_note = f"向量清理异常（已忽略）：{type(exc).__name__}: {exc}"
            logger.warning("归档时向量清理失败 talent_id=%s：%s", talent_id, exc)

        # ---- ② MySQL 权威状态（软删 + data_status=3）----
        talent.is_deleted = 1
        talent.deleted_at = datetime.now()
        talent_dao.soft_delete_work_exps(
            db, int(principal.tenant_id), int(talent.id), deleted_at=talent.deleted_at
        )
        talent_dao.soft_delete_project_exps(
            db, int(principal.tenant_id), int(talent.id), deleted_at=talent.deleted_at
        )
        talent.data_status = int(TalentDataStatus.ARCHIVED)
        talent_dao.update_talent_fields(db, talent, {})

        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=int(principal.tenant_id),
                talent_id=int(talent.id),
                change_source=int(ChangeSource.MANUAL_REVIEW),
                field_name="data_status",
                before_value=str(int(TalentDataStatus.ACTIVE)),
                after_value=str(int(TalentDataStatus.ARCHIVED)),
                operator_id=int(principal.user_id),
            ),
        )

        AuditService.record(
            AuditAction.TALENT_ARCHIVE,
            tenant_id=int(principal.tenant_id),
            user_id=int(principal.user_id),
            target_type=AuditTarget.TALENT,
            target_id=int(talent.id),
            after={"vector_cleaned": vector_cleaned, "vector_note": vector_note},
        )
        db.commit()

        return TalentArchiveOut(
            talent_id=int(talent.id),
            archived=True,
            vector_cleaned=vector_cleaned,
            vector_note=vector_note,
            data_status=int(talent.data_status),
            data_status_name=TalentDataStatus.name_of(int(talent.data_status)),
            version_no=int(talent.version_no),
        )

    # ==================================================================
    # API-34 数字画像
    # ==================================================================
    @classmethod
    def profile(cls, db: Session, principal: CurrentPrincipal, talent_id: int) -> TalentProfileOut:
        talent = cls._locate(db, principal, talent_id)
        tenant_id = int(principal.tenant_id)

        rels = tag_dao.list_talent_tags(db, tenant_id, int(talent.id))
        codes = [str(r.tag_code) for r in rels]
        names = tag_dao.names_by_codes(db, tenant_id, codes)
        tags = [
            ProfileTagOut(
                tag_code=str(r.tag_code),
                tag_name=names.get(str(r.tag_code)),
                source=int(r.source) if r.source is not None else None,
                source_name=cls._tag_source_name(r.source),
            )
            for r in rels
        ]

        vector_summary, vector_note = cls._vector_summary(db, principal, talent)
        org_names = cls._org_names(
            db, tenant_id, {int(talent.org_id)} if talent.org_id is not None else set()
        )

        return TalentProfileOut(
            talent_id=int(talent.id),
            name=str(talent.name),
            org_id=int(talent.org_id) if talent.org_id is not None else None,
            org_name=org_names.get(int(talent.org_id)) if talent.org_id is not None else None,
            vector_summary=vector_summary,
            vector_note=vector_note,
            dimensions=cls._dimensions(talent, len(tags)),
            tags=tags,
            version_no=int(talent.version_no),
            updated_at=talent.updated_at,
        )

    # ==================================================================
    # API-35 导出
    # ==================================================================
    @classmethod
    def export(
        cls, db: Session, principal: CurrentPrincipal, req: TalentExportRequest
    ) -> TalentExportOut:
        if req.format != "csv":
            # ❌ 不静默降级成 CSV：那会让调用方以为拿到了 xlsx（一个「成功但格式不对」
            #    的响应，比一个明确的 40001 危险得多）。
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "本期仅支持 CSV 导出（xlsx 需要二进制流，与统一响应壳的文本 content 不兼容）",
                {"fields": [{"field": "format", "message": "可选值：csv"}]},
            )

        tenant_id = int(principal.tenant_id)
        dept_ids = cls._resolve_dept_ids(db, principal)
        narrowed = DataScopeFilter.assert_scope_narrowing(
            principal, req.filters.org_ids, dept_ids=dept_ids
        )
        data_status = cls._resolve_data_status(principal, req.filters.data_status)
        scope = DataScopeFilter.mysql_condition(
            TalentBase, principal, dept_ids=dept_ids, owner_column="user_id"
        )
        sort_field, direction = parse_sort(req.sort)

        # 多取一行用于判断「是否截断」——否则无法区分「刚好 1000 行」与「被截了」
        max_rows = int(req.max_rows)
        rows = talent_dao.list_talents(
            db,
            tenant_id,
            scope_clause=scope,
            sort_field=sort_field,
            sort_desc=(direction == "desc"),
            limit=max_rows + 1,
            offset=0,
            keyword=req.keyword,
            org_ids=narrowed,
            education_code=req.filters.education_code,
            work_years_min=req.filters.work_years_min,
            work_years_max=req.filters.work_years_max,
            tag_codes=req.filters.tag_codes,
            data_status=data_status,
        )
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]

        org_names = cls._org_names(db, tenant_id, {int(r.org_id) for r in rows if r.org_id})
        edu_names = cls._education_names(db, tenant_id)

        buffer = io.StringIO()
        # utf-8-sig 的前导 BOM：不加的话 Excel 打开中文 CSV 会乱码
        # （Excel 在无 BOM 时按本地代码页解码）。这是导出场景的必备细节。
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_HEADERS)
        for row in rows:
            writer.writerow(
                [
                    int(row.id),
                    row.name,
                    Gender.name_of(int(row.gender)) if row.gender is not None else "",
                    edu_names.get(str(row.education_level or ""), ""),
                    row.school or "",
                    row.major or "",
                    row.work_years if row.work_years is not None else "",
                    org_names.get(int(row.org_id), "") if row.org_id is not None else "",
                    TalentDataStatus.name_of(int(row.data_status)) or "",
                    int(row.version_no),
                    row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
                ]
            )

        generated_at = datetime.now()
        filename = f"talents_{generated_at.strftime('%Y%m%d_%H%M%S')}.csv"

        # ---- 留痕：数据出库必须可追（after 只记规模与筛选，不记导出内容）----
        AuditService.record(
            AuditAction.TALENT_EXPORT,
            tenant_id=tenant_id,
            user_id=int(principal.user_id),
            target_type=AuditTarget.TALENT,
            after={
                "row_count": len(rows),
                "truncated": truncated,
                "keyword": req.keyword,
                "data_status": data_status,
                "org_ids": list(narrowed) if narrowed else None,
            },
        )
        db.commit()

        return TalentExportOut(
            filename=filename,
            format="csv",
            row_count=len(rows),
            content="\ufeff" + buffer.getvalue(),
            truncated=truncated,
            generated_at=generated_at,
        )

    # ==================================================================
    # 权限与定位
    # ==================================================================
    @staticmethod
    def _resolve_dept_ids(db: Session, principal: CurrentPrincipal) -> tuple[int, ...]:
        """``data_scope=2`` 时解析部门子树（含自身）；其余范围为空元组。"""
        if int(principal.data_scope) == int(RoleDataScope.OWN_DEPT) and not principal.dept_ids:
            if principal.org_id is not None:
                return OrgScopeService.resolve_dept_ids(
                    db, int(principal.tenant_id), int(principal.org_id)
                )
        return tuple(principal.dept_ids)

    @staticmethod
    def _resolve_data_status(
        principal: CurrentPrincipal, requested: int | None
    ) -> tuple[int, ...]:
        """档案状态的口径（接口 API-30 备注）—— 🔴 批次 23 **反转**默认口径。

        - **显式指定** → 单元素元组；非 ``1`` 的取值仍**仅 ADM/HR**（保护性规则不变）；
        - **未指定（默认）** →
          **ADM / HR：``(0, 1)``＝「待复核 + 有效」**；其余角色：``(1,)`` 仅「有效」。

        为什么反转（批次 23，用户报「导入很多简历成功了，前端只显示一个人」）：
        导入落库的档案是 ``0 待复核``（人工担保后才转有效），而旧默认只查 ``1`` ⇒
        **导入成功后档案页默认一条都看不到**，会被直接判成「没入库」。
        旧口径的保护意图是「MGR 不应把未核对的数据当事实用于决策」——
        它的落点应当是**限制角色**，而不是**连管理员也把在办数据藏起来**；
        故 ADM/HR 的默认放开到「待复核 + 有效」，其余角色维持仅「有效」。
        🔁 旧行为（「未传 → 对所有人仅 1 有效」）留痕于批次 23 的台账与 T-46b。
        """
        if requested is None:
            if principal.has_role("ADM", "HR"):
                return (int(TalentDataStatus.PENDING_REVIEW), int(TalentDataStatus.ACTIVE))
            return (int(TalentDataStatus.ACTIVE),)
        value = int(requested)
        if value != int(TalentDataStatus.ACTIVE) and not principal.has_role("ADM", "HR"):
            raise PermissionDeniedError("仅管理员 / HR 可查询非有效状态的档案")
        return (value,)

    @classmethod
    def _locate(cls, db: Session, principal: CurrentPrincipal, talent_id: int) -> TalentBase:
        """按 ID 取**可见**档案；不可见 / 不存在统一 40301（接口 API-31 备注）。

        两步查询是刻意的：
        ① 第一次**带行级条件**（这是安全路径，也是唯一对外的结论）；
        ② 落空后**只在内部日志**里区分「不存在」与「越权」——
           对外仍是同一个 40301，因为状态码本身就是探测资源存在性的信道。

        ③ ADM/HR（且范围 = 全部）例外：接口原文「已软删档案对**非** ADM/HR
           返回 40301」，反过来说 ADM/HR 能看到归档件。**只在 ``ALL`` 范围**
           放开 —— 若 HR 被配成「本部门」范围，它也应当只看本部门（含归档件），
           所以先判范围、再取含软删的行，避免「靠角色名绕过行级条件」。
        """
        dept_ids = cls._resolve_dept_ids(db, principal)
        scope = DataScopeFilter.mysql_condition(
            TalentBase, principal, dept_ids=dept_ids, owner_column="user_id"
        )
        stmt = select(TalentBase).where(
            TalentBase.tenant_id == int(principal.tenant_id),
            TalentBase.id == int(talent_id),
            TalentBase.is_deleted == 0,
            scope,
        ).execution_options(populate_existing=True)
        row = db.execute(stmt).scalar_one_or_none()
        if row is not None:
            return row

        # ADM/HR + 全部范围 → 允许看归档件（含软删）
        if int(principal.data_scope) == int(RoleDataScope.ALL) and principal.has_role("ADM", "HR"):
            archived = talent_dao.get_talent_any_state(db, int(principal.tenant_id), int(talent_id))
            if archived is not None:
                return archived

        exists = talent_dao.get_talent(db, int(principal.tenant_id), int(talent_id))
        if exists is None:
            logger.warning(
                "档案不存在或已删除 talent_id=%s tenant=%s user=%s",
                talent_id,
                principal.tenant_id,
                principal.user_id,
            )
        else:
            logger.warning(
                "档案越权访问被拒 talent_id=%s tenant=%s user=%s scope=%s dept_ids=%s",
                talent_id,
                principal.tenant_id,
                principal.user_id,
                principal.data_scope,
                dept_ids,
            )
        raise PermissionDeniedError("无权访问该档案")

    # ==================================================================
    # 字段装配
    # ==================================================================
    @staticmethod
    def _apply_field(talent: TalentBase, field: str, value: Any) -> tuple[str | None, str | None]:
        """把一个字段写入 ORM 行，返回 ``(变更前可展示值, 变更后可展示值)``。

        🔴 留痕里的值一律走 :func:`~app.core.privacy.log_field_value`：
        加密字段（手机/邮箱）在留痕里是**脱敏串**，效果是「留痕能证明变了、
        不暴露变成什么」（F-3.11 要求「更正可追溯」，不要求「更正内容全员可见」）。
        """
        if field == "phone":
            before = mask_encrypted_phone(talent.phone_enc)
            talent.phone_enc = encrypt_sensitive(value)
            talent.phone_hash = hash_contact(value, "phone")
            return log_field_value("phone", before), log_field_value("phone", value)
        if field == "email":
            before = mask_encrypted_email(talent.email_enc)
            talent.email_enc = encrypt_sensitive(value)
            return log_field_value("email", before), log_field_value("email", value)
        if field == "birth_month":
            before = month_str(talent.birth_date)
            talent.birth_date = TalentService._parse_birth_month(str(value))
            return before, month_str(talent.birth_date)
        if field == "education_level":
            before = str(talent.education_level) if talent.education_level else None
            talent.education_level = education_int_to_code(int(value))
            return before, talent.education_level
        if field == "org_id":
            before = str(int(talent.org_id)) if talent.org_id is not None else None
            talent.org_id = int(value)
            return before, str(int(value))

        before_raw = getattr(talent, field, None)
        before = str(before_raw) if before_raw is not None else None
        setattr(talent, field, value)
        return before, str(value)

    @staticmethod
    def _parse_birth_month(text: str) -> date:
        """``"1993-06"`` / ``"1993-06-01"`` → ``date``（**只到月**，日缺省为 1）。"""
        raw = text.strip()
        for fmt in ("%Y-%m", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "birth_month 格式应为 YYYY-MM 或 YYYY-MM-DD",
            {"fields": [{"field": "birth_month", "message": "示例：1993-06"}]},
        )

    @staticmethod
    def _clip(value: str | None, limit: int = 500) -> str | None:
        """留痕列是 ``String(512)``，超长截断（宁可留痕不完整也不让写库失败）。"""
        if value is None:
            return None
        text = str(value)
        return text[:limit] if len(text) > limit else text

    @classmethod
    def _to_list_item(
        cls,
        row: TalentBase,
        *,
        org_names: dict[int, str],
        edu_names: dict[str, str],
        rels: dict[int, list[Any]],
        tag_names: dict[str, str],
    ) -> TalentListItemOut:
        code = str(row.education_level) if row.education_level else None
        top_tags = [
            TopTagOut(tag_code=str(rel.tag_code), tag_name=tag_names.get(str(rel.tag_code)))
            for rel in rels.get(int(row.id), [])[:MAX_TOP_TAGS]
        ]
        return TalentListItemOut(
            id=int(row.id),
            name=str(row.name),
            gender=int(row.gender) if row.gender is not None else None,
            gender_name=Gender.name_of(int(row.gender)) if row.gender is not None else None,
            org_id=int(row.org_id) if row.org_id is not None else None,
            org_name=org_names.get(int(row.org_id)) if row.org_id is not None else None,
            education_level=education_code_to_int(code),
            education_level_name=edu_names.get(code or ""),
            work_years=int(row.work_years) if row.work_years is not None else None,
            top_tags=top_tags,
            data_status=int(row.data_status),
            data_status_name=TalentDataStatus.name_of(int(row.data_status)),
            updated_at=row.updated_at,
        )

    @classmethod
    def _to_detail(
        cls, db: Session, principal: CurrentPrincipal, talent: TalentBase, *, with_detail: bool
    ) -> TalentDetailOut:
        tenant_id = int(principal.tenant_id)
        code = str(talent.education_level) if talent.education_level else None
        edu_names = cls._education_names(db, tenant_id)
        org_names = cls._org_names(
            db, tenant_id, {int(talent.org_id)} if talent.org_id is not None else set()
        )
        work_exps: list[Any] = []
        project_exps: list[Any] = []
        if with_detail:
            work_exps = [
                {
                    "id": row.id,
                    "company": str(row.company),
                    "dept_name": row.dept_name,
                    "title": row.position_name,
                    "start_date": cls._date_text(row.start_date),
                    "end_date": cls._date_text(row.end_date),
                    "is_current": bool(row.is_current),
                    "description": row.description,
                }
                for row in talent_dao.list_work_exps(db, tenant_id, int(talent.id))
            ]
            project_exps = [
                {
                    "id": row.id,
                    "project_name": str(row.project_name),
                    "role": row.role_name,
                    "start_date": cls._date_text(row.start_date),
                    "end_date": cls._date_text(row.end_date),
                    "description": row.description,
                    "tech_stack": [str(item) for item in (row.tech_stack or [])],
                }
                for row in talent_dao.list_project_exps(db, tenant_id, int(talent.id))
            ]

        return TalentDetailOut(
            id=int(talent.id),
            name=str(talent.name),
            gender=int(talent.gender) if talent.gender is not None else None,
            gender_name=Gender.name_of(int(talent.gender)) if talent.gender is not None else None,
            birth_month=month_str(talent.birth_date),
            education_level=education_code_to_int(code),
            education_level_name=edu_names.get(code or ""),
            school=talent.school,
            major=talent.major,
            work_years=int(talent.work_years) if talent.work_years is not None else None,
            # 🔴 PII 恒为脱敏形态（口径见 schemas/talent_master 模块 docstring ②）
            id_card=mask_encrypted_id_card(talent.id_card_enc),
            phone=mask_encrypted_phone(talent.phone_enc),
            email=mask_encrypted_email(talent.email_enc),
            sensitive_visible=principal.has_role("ADM", "HR"),
            org_id=int(talent.org_id) if talent.org_id is not None else None,
            org_name=org_names.get(int(talent.org_id)) if talent.org_id is not None else None,
            data_status=int(talent.data_status),
            data_status_name=TalentDataStatus.name_of(int(talent.data_status)),
            version_no=int(talent.version_no),
            work_exps=work_exps,
            project_exps=project_exps,
            # 技能/证书仍未建模；with_detail=False 时不说明，避免把参数语义
            # 与数据缺口混在一起。
            detail_note=DETAIL_NOTE_NO_DETAIL_TABLE if with_detail else None,
            updated_at=talent.updated_at,
        )

    @staticmethod
    def _date_text(value: Any) -> str | None:
        """把 DATE 或兼容的字符串值统一成 API-31 的 ISO 日期。"""
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    # ==================================================================
    # 画像辅助
    # ==================================================================
    @staticmethod
    def _vector_summary(
        db: Session, principal: CurrentPrincipal, talent: TalentBase
    ) -> tuple[str | None, str | None]:
        """向量可用性摘要（**诚实反映**，不用文字充数，见 TalentProfileOut 的说明）。"""
        if not talent.profile_vec_id:
            return (
                None,
                "该档案尚未做真实向量化（profile_vec_id 为空）。"
                "归 CU-18：H-19 的 Embedding 吞吐标定闭环后由 scripts/rebuild_index.py 写入。",
            )
        try:
            from app.retrieval import get_retrieval_provider

            provider = get_retrieval_provider()
            if not provider.health():
                return None, "检索提供方当前不可用（向量检索处于关键词降级），无法读取向量摘要。"
            return (
                f"已建立画像向量索引（主键 {talent.profile_vec_id}）",
                None,
            )
        except Exception as exc:  # noqa: BLE001 — 画像不应因检索侧异常而打不开
            logger.info("读取向量摘要失败 talent_id=%s：%s", talent.id, exc)
            return None, f"向量摘要读取失败（{type(exc).__name__}），已按「无向量」展示。"

    @staticmethod
    def _dimensions(talent: TalentBase, tag_count: int) -> list[ProfileDimensionOut]:
        """画像维度得分 —— **由确定性字段折算**，不是 LLM 生成的。

        口径（可复算、可解释）：
        - 经验厚度 = ``min(work_years, 15) / 15``（15 年以上封顶）；
        - 学历水平 = 字典码数值 / 5（``01``~``05``）；
        - 标签覆盖 = ``min(标签数, 5) / 5``；
        - 技能多样性暂缺（TBL-14 未建模），故**不出现该维度** ——
          ❌ 不用 0 分占位：0 分会被读成「这个人没有技能」，而事实是「没这项数据」。
        """
        dims: list[ProfileDimensionOut] = []
        if talent.work_years is not None:
            dims.append(
                ProfileDimensionOut(
                    code="experience",
                    name="经验厚度",
                    score=round(min(int(talent.work_years), 15) / 15, 4),
                )
            )
        level = education_code_to_int(str(talent.education_level) if talent.education_level else None)
        if level is not None:
            dims.append(
                ProfileDimensionOut(
                    code="education", name="学历水平", score=round(min(level, 5) / 5, 4)
                )
            )
        dims.append(
            ProfileDimensionOut(code="tags", name="标签覆盖", score=round(min(tag_count, 5) / 5, 4))
        )
        return dims

    @staticmethod
    def _tag_source_name(source: Any) -> str | None:
        from app.models.enums import TagSource

        return TagSource.name_of(int(source)) if source is not None else None

    # ==================================================================
    # 字典 / 组织名称
    # ==================================================================
    @staticmethod
    def _education_names(db: Session, tenant_id: int) -> dict[str, str]:
        """``{"04": "硕士"}``（**含停用项**）。

        为什么取全部状态而不只取启用：档案上可能挂着**后来被停用**的学历档
        （政策调整会停用旧档位）。若只查启用项，那些档案的学历名会显示为空 ——
        看起来像数据坏了，而其实只是档位停用了。
        """
        out: dict[str, str] = {}
        for item in dict_dao.list_items(db, tenant_id, "education_level"):
            out[str(item.item_code)] = str(item.item_name)
        return out

    @staticmethod
    def _org_names(db: Session, tenant_id: int, org_ids: set[int]) -> dict[int, str]:
        if not org_ids:
            return {}
        return {
            int(dept.id): str(dept.dept_name)
            for dept in org_dao.list_depts(db, tenant_id)
            if int(dept.id) in org_ids
        }


__all__ = ["TalentService"]
