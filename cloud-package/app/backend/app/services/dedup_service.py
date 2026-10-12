"""查重与合并（CU-19，API-57~61，F-3.5 / SQ-4 / SM-1）。

🔴 四条贯穿全文件的决策（SQ-4 时序图，全部由测试钉死）
======================================================

1. **先 MySQL 事务，再向量删除；向量删除失败 → 整体回滚**（CR-18 / SQ-4 决策 1）。
   合并不是不可逆操作（有 ``snapshot`` 可还原），故 MySQL 侧可先落地；但向量删除
   **先于 commit**：若从档向量删不掉（Milvus 异常），``db.rollback()`` 让 MySQL 侧
   也一并还原，保证「从档软删」与「从档向量删除」**要么都发生、要么都不发生**。
2. **``snapshot`` 是回滚唯一依据**（SQ-4 决策 2）。❌ 没有快照就返回 **42203**
   （:class:`~app.exceptions.MISSING_SNAPSHOT`），**绝不做「尽力还原」** ——
   「尽力还原」在档案合并这种高不可逆操作里，等于「凭猜测改写 PII 归属」。
3. **从档软删（``data_status=2``）而非物理删**（SQ-4 决策 3）：保留可追溯，
   且 API-61 是「已合并 → 有效」这一**唯一可逆的删除类迁移**（SM-1）。
4. **主档必须重索引**（SQ-4 决策 4）：合并改变了画像内容，``vector_reindexed`` 回传。

🔴 相似度判定是**确定性**的（铁律 10）：精确键命中豁免阈值、否则用向量相似度比
``ThresholdEngine.is_dup_candidate``。LLM 不参与「是不是重复」的判定。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.dup import MERGEABLE_FIELDS, DupScorer
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import dup_pair_dao, tag_dao, talent_dao
from app.exceptions import BizError, ErrorCode, NotFoundError, PermissionDeniedError
from app.models.dup_pair import TalentDupPair
from app.models.enums import ChangeSource, DupPairStatus, TalentDataStatus
from app.models.talent import (
    TalentBase,
    TalentChangeLog,
    TalentProjectExp,
    TalentTagRel,
    TalentWorkExp,
)
from app.retrieval import MilvusRetrievalProvider, get_retrieval_provider
from app.retrieval.profile import build_profile_text
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.schemas.dedup import (
    DedupMergeOut,
    DedupRollbackOut,
    DupCandidateOut,
    DupCompareOut,
    DupIgnoreOut,
    TalentBriefOut,
    pair_status_name_of,
)
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class DedupService:
    """查重与合并（API-57~61）。"""

    # ------------------------------------------------------------------
    # 精确键扫描（候选生成：被导入 auto_dedup / 手工触发调用）
    # ------------------------------------------------------------------
    @classmethod
    def scan_exact_duplicates(cls, db: Session, principal: CurrentPrincipal) -> int:
        """扫描「有效」档案的精确键（phone/id_card 哈希）重复，落 ``talent_dup_pair``。

        🔴 精确键命中 = 同一联系方式 / 同一证件，是「同一个人」的最强信号；
        相似度恒记 **1.0**、``match_fields`` 记命中的哈希列。已存在的对（任意状态）
        **不重复落**（``uk_tenant_pair`` + 先查后插双保险）。

        :return: 本次**新增**的候选对数量
        """
        rows = dup_pair_dao.list_dedup_candidates(db, principal.tenant_id)
        by_phone: dict[str, list[int]] = {}
        by_idcard: dict[str, list[int]] = {}
        for tid, phone, idcard in rows:
            if phone:
                by_phone.setdefault(phone, []).append(tid)
            if idcard:
                by_idcard.setdefault(idcard, []).append(tid)

        # 收集待落对：``(a, b) -> 命中的字段列表``（phone 与 id_card 两个来源合并去重）
        pairs: dict[tuple[int, int], list[str]] = {}

        def _collect(bucket: list[int], match_field: str) -> None:
            if len(bucket) < 2:
                return
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    a, b = sorted((bucket[i], bucket[j]))
                    pairs.setdefault((a, b), [])
                    if match_field not in pairs[(a, b)]:
                        pairs[(a, b)].append(match_field)

        for bucket in by_phone.values():
            _collect(bucket, "phone_hash")
        for bucket in by_idcard.values():
            _collect(bucket, "id_card_hash")

        created = 0
        for (a, b), fields in pairs.items():
            existing = dup_pair_dao.get_pair_by_ids(db, principal.tenant_id, a, b)
            if existing is not None:
                continue  # 已存在（任意状态）不重复落
            dup_pair_dao.insert_pair(
                db,
                TalentDupPair(
                    tenant_id=principal.tenant_id,
                    talent_id_a=a,
                    talent_id_b=b,
                    similarity=1.0,
                    match_fields=sorted(set(fields)),
                    status=int(DupPairStatus.PENDING),
                ),
            )
            created += 1
        if created:
            db.commit()
        return created

    # ------------------------------------------------------------------
    # API-57 候选对列表
    # ------------------------------------------------------------------
    @classmethod
    def list_candidates(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        status: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[DupCandidateOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = dup_pair_dao.list_pairs(
            db, principal.tenant_id, status=status, limit=size, offset=(current - 1) * size
        )
        total = dup_pair_dao.count_pairs(db, principal.tenant_id, status=status)
        items = [cls._to_candidate(db, principal, p) for p in rows]
        return items, total, current, size

    # ------------------------------------------------------------------
    # API-58 并排对比
    # ------------------------------------------------------------------
    @classmethod
    def compare(cls, db: Session, principal: CurrentPrincipal, pair_id: int) -> DupCompareOut:
        pair = cls._get_pair(db, principal, pair_id)
        a = talent_dao.get_talent(db, principal.tenant_id, int(pair.talent_id_a))
        b = talent_dao.get_talent(db, principal.tenant_id, int(pair.talent_id_b))
        if a is None or b is None:
            raise NotFoundError("档案不存在")
        if not cls._can_access_talent(principal, a) or not cls._can_access_talent(principal, b):
            raise PermissionDeniedError("无权限访问该档案")
        return DupCompareOut(
            pair_id=pair.id,
            talent_a=cls._to_brief(db, principal.tenant_id, a),
            talent_b=cls._to_brief(db, principal.tenant_id, b),
            similarity=float(pair.similarity) if pair.similarity is not None else None,
            match_fields=list(pair.match_fields or []),
            status=int(pair.status),
            status_name=pair_status_name_of(int(pair.status)),
        )

    # ------------------------------------------------------------------
    # API-59 合并（核心）
    # ------------------------------------------------------------------
    @classmethod
    def merge(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        pair_id: int,
        *,
        master_id: int,
        field_strategy: dict[str, str],
        merge_tags: bool = True,
        merge_detail: bool = True,
    ) -> DedupMergeOut:
        pair = cls._get_pair(db, principal, pair_id)
        if int(pair.status) != int(DupPairStatus.PENDING):
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                "该查重对已处理（已合并或已忽略）",
                {"pair_id": str(pair.id), "status": int(pair.status)},
            )
        a, b = int(pair.talent_id_a), int(pair.talent_id_b)
        master_id = int(master_id)
        if master_id not in (a, b):
            raise BizError(
                ErrorCode.PARAM_INVALID, "master_id 必须是该查重对中的一方", {"field": "master_id"}
            )
        slave_id = b if master_id == a else a
        master = talent_dao.get_talent(db, principal.tenant_id, master_id)
        slave = talent_dao.get_talent(db, principal.tenant_id, slave_id)
        if master is None or slave is None:
            raise NotFoundError("档案不存在")
        if not cls._can_access_talent(principal, master) or not cls._can_access_talent(principal, slave):
            raise PermissionDeniedError("无权限访问该档案")
        if int(master.data_status) != int(TalentDataStatus.ACTIVE) or int(
            slave.data_status
        ) != int(TalentDataStatus.ACTIVE):
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                "仅『有效』状态的档案可被合并",
                {
                    "master_status": int(master.data_status),
                    "slave_status": int(slave.data_status),
                },
            )
        invalid = [k for k in field_strategy if k not in MERGEABLE_FIELDS]
        if invalid:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"field_strategy 含非法字段：{', '.join(invalid)}（可合并字段：{', '.join(MERGEABLE_FIELDS)}）",
                {"invalid_fields": invalid},
            )

        now = datetime.now()
        # ---- 回滚快照（SQ-4 决策 2：回滚唯一依据）----
        master_before = {f: cls._snap(getattr(master, f)) for f in MERGEABLE_FIELDS}
        slave_before_status = int(slave.data_status)
        master_tags_before = sorted(
            str(r.tag_code) for r in tag_dao.list_talent_tags(db, principal.tenant_id, master_id)
        )
        slave_tags_before = sorted(
            str(r.tag_code) for r in tag_dao.list_talent_tags(db, principal.tenant_id, slave_id)
        )
        master_work_before = talent_dao.list_work_exps(db, principal.tenant_id, master_id)
        slave_work_before = talent_dao.list_work_exps(db, principal.tenant_id, slave_id)
        master_project_before = talent_dao.list_project_exps(db, principal.tenant_id, master_id)
        slave_project_before = talent_dao.list_project_exps(db, principal.tenant_id, slave_id)
        master_work_snapshot = [cls._work_exp_snapshot(row) for row in master_work_before]
        slave_work_snapshot = [cls._work_exp_snapshot(row) for row in slave_work_before]
        master_project_snapshot = [cls._project_exp_snapshot(row) for row in master_project_before]
        slave_project_snapshot = [cls._project_exp_snapshot(row) for row in slave_project_before]

        # ---- ① 字段合并（field_strategy / 非空优先）----
        for f in MERGEABLE_FIELDS:
            new_val = DupScorer.resolve_field(
                getattr(master, f), getattr(slave, f), field_strategy.get(f)
            )
            setattr(master, f, new_val)

        # ---- ② 标签合并（merge_tags）----
        if merge_tags:
            cls._merge_tags(db, principal.tenant_id, master_id, slave_id, now)

        # ---- ③ 履历明细合并（同一事务，快照先于迁移）----
        if merge_detail:
            cls._merge_work_exps(
                db, principal.tenant_id, master_id, slave_id, now
            )
            cls._merge_project_exps(
                db, principal.tenant_id, master_id, slave_id, now
            )

        # 主档字段、标签或履历变化都推进一次版本；明细内容放快照，不写入普通留痕列。
        talent_dao.update_talent_fields(db, master, {})

        # ---- ④ 从档软删（data_status=2，保留可追溯）----
        talent_dao.set_talent_data_status(db, slave, int(TalentDataStatus.MERGED))
        slave.is_deleted = 1
        slave.deleted_at = now
        slave.merged_into_id = master_id
        db.flush()

        # ---- ④ 变更留痕（TBL-22，change_source=2 合并）----
        snapshot_payload = {
            "master_id": master_id,
            "slave_id": slave_id,
            "master_before": master_before,
            "slave_before_status": slave_before_status,
            "master_tags_before": master_tags_before,
            "slave_tags_before": slave_tags_before,
            "master_before_version": int(master.version_no) - 1,
            "slave_before_version": int(slave.version_no) - 1,
            "detail_merged": bool(merge_detail),
            "master_work_exps_before": master_work_snapshot,
            "slave_work_exps_before": slave_work_snapshot,
            "master_project_exps_before": master_project_snapshot,
            "slave_project_exps_before": slave_project_snapshot,
        }
        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=master_id,
                change_source=int(ChangeSource.MERGE),
                field_name=None,  # 整体合并，无单字段
                before_value=None,
                after_value=None,
                snapshot_ref=str(pair.id),  # 快照引用 → pair.snapshot
                operator_id=principal.user_id,
            ),
        )
        db.flush()

        # ---- ⑤ pair 落合并态 ----
        dup_pair_dao.update_pair_fields(
            db,
            pair,
            {
                "status": int(DupPairStatus.MERGED),
                "master_id": master_id,
                "snapshot": snapshot_payload,
                "merged_by": principal.user_id,
                "merged_at": now,
            },
        )
        db.flush()

        # ---- ⑥ 向量删除（先于 commit：失败整体回滚，CR-18）----
        provider = get_retrieval_provider()
        is_milvus = isinstance(provider, MilvusRetrievalProvider)
        if is_milvus:
            deleted = provider.delete(db, talent_id=slave_id, tenant_id=principal.tenant_id)
            if deleted < 0:
                db.rollback()
                raise BizError(
                    ErrorCode.VECTOR_DEGRADED,
                    "从档向量删除失败，本次合并已整体回滚（MySQL 未变更）",
                    {"slave_id": str(slave_id)},
                )

        db.commit()

        # ---- ⑦ 主档重索引（commit 后，尽力而为；派生索引可全量重建）----
        vector_reindexed = False
        if is_milvus:
            vector_reindexed = cls._reindex(db, provider, master, principal.tenant_id)

        AuditService.record(
            AuditAction.DEDUP_MERGE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            target_id=master_id,
            before={"slave_id": str(slave_id)},
            after={
                "master_id": str(master_id),
                "slave_id": str(slave_id),
                "pair_id": str(pair.id),
                "field_strategy": field_strategy,
                "merge_tags": merge_tags,
                "merge_detail": merge_detail,
                "vector_reindexed": vector_reindexed,
            },
        )
        return DedupMergeOut(
            pair_id=pair.id,
            master_id=master_id,
            slave_id=slave_id,
            vector_reindexed=vector_reindexed,
            merged_at=now,
        )

    # ------------------------------------------------------------------
    # API-60 忽略
    # ------------------------------------------------------------------
    @classmethod
    def ignore(cls, db: Session, principal: CurrentPrincipal, pair_id: int) -> DupIgnoreOut:
        pair = cls._get_pair(db, principal, pair_id)
        if int(pair.status) != int(DupPairStatus.PENDING):
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                "该查重对已处理（已合并或已忽略）",
                {"pair_id": str(pair.id), "status": int(pair.status)},
            )
        dup_pair_dao.update_pair_fields(db, pair, {"status": int(DupPairStatus.IGNORED)})
        db.commit()
        AuditService.record(
            AuditAction.DEDUP_IGNORE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            target_id=pair.talent_id_a,
            after={"pair_id": str(pair.id), "talent_b": str(pair.talent_id_b)},
        )
        return DupIgnoreOut(
            pair_id=pair.id,
            status=int(pair.status),
            status_name=pair_status_name_of(int(pair.status)),
        )

    # ------------------------------------------------------------------
    # API-61 回滚
    # ------------------------------------------------------------------
    @classmethod
    def rollback(cls, db: Session, principal: CurrentPrincipal, merge_id: int) -> DedupRollbackOut:
        pair = cls._get_pair(db, principal, merge_id)
        if int(pair.status) != int(DupPairStatus.MERGED):
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                "该记录不是已合并状态，无法回滚",
                {"pair_id": str(pair.id), "status": int(pair.status)},
            )
        snapshot = pair.snapshot
        if not snapshot:
            # SQ-4 决策 2：无快照 → 42203，绝不做「尽力还原」
            raise BizError(
                ErrorCode.MISSING_SNAPSHOT,
                "缺少合并快照，无法回滚",
                {"pair_id": str(pair.id)},
            )
        master_id = int(snapshot["master_id"])
        slave_id = int(snapshot["slave_id"])
        # 🔴 回滚必须取「含软删」的档案：从档在合并时被软删（is_deleted=1），
        # 常规 get_talent 会因软删过滤返回 None → 误报「档案不存在」。
        master = talent_dao.get_talent_any_state(db, principal.tenant_id, master_id)
        slave = talent_dao.get_talent_any_state(db, principal.tenant_id, slave_id)
        if master is None or slave is None:
            raise NotFoundError("档案不存在")

        now = datetime.now()
        # ① 主档字段还原
        for f in MERGEABLE_FIELDS:
            if f in snapshot["master_before"]:
                setattr(master, f, cls._restore(f, snapshot["master_before"][f]))
        master.version_no = int(snapshot.get("master_before_version", master.version_no)) + 1
        # ② 标签还原（全量替换回合并前）
        tag_dao.replace_talent_tags(
            db,
            principal.tenant_id,
            master_id,
            [{"tag_code": c, "confidence": None, "source": 0} for c in snapshot["master_tags_before"]],
        )
        if snapshot.get("detail_merged"):
            cls._restore_detail_snapshot(
                db,
                principal.tenant_id,
                TalentWorkExp,
                snapshot.get("master_work_exps_before", [])
                + snapshot.get("slave_work_exps_before", []),
            )
            cls._restore_detail_snapshot(
                db,
                principal.tenant_id,
                TalentProjectExp,
                snapshot.get("master_project_exps_before", [])
                + snapshot.get("slave_project_exps_before", []),
            )
        tag_dao.replace_talent_tags(
            db,
            principal.tenant_id,
            slave_id,
            [{"tag_code": c, "confidence": None, "source": 0} for c in snapshot["slave_tags_before"]],
        )
        # ③ 从档还原（唯一可逆的「删除类」迁移）
        slave.data_status = int(snapshot["slave_before_status"])
        slave.version_no = int(snapshot.get("slave_before_version", slave.version_no)) + 1
        slave.is_deleted = 0
        slave.deleted_at = None
        slave.merged_into_id = None
        # ④ pair 回到待处理
        dup_pair_dao.update_pair_fields(
            db,
            pair,
            {
                "status": int(DupPairStatus.PENDING),
                "master_id": None,
                "snapshot": None,
                "merged_by": None,
                "merged_at": None,
            },
        )
        # ⑤ 留痕（回滚也是一次变更，F-3.10）
        talent_dao.insert_change_log(
            db,
            TalentChangeLog(
                tenant_id=principal.tenant_id,
                talent_id=slave_id,
                change_source=int(ChangeSource.MERGE),
                field_name="data_status",
                before_value=str(int(TalentDataStatus.MERGED)),
                after_value=str(snapshot["slave_before_status"]),
                operator_id=principal.user_id,
            ),
        )
        db.commit()

        # ⑥ 向量：从档重索引 + 主档重索引（尽力而为）
        provider = get_retrieval_provider()
        if isinstance(provider, MilvusRetrievalProvider):
            cls._reindex(db, provider, master, principal.tenant_id)
            cls._reindex(db, provider, slave, principal.tenant_id)

        AuditService.record(
            AuditAction.DEDUP_ROLLBACK,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            target_id=master_id,
            after={"merge_id": str(merge_id), "restored_slave_id": str(slave_id)},
        )
        return DedupRollbackOut(
            merge_id=merge_id,
            master_id=master_id,
            slave_id=slave_id,
            rolled_back_at=now,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _get_pair(db: Session, principal: CurrentPrincipal, pair_id: int) -> Any:
        pair = dup_pair_dao.get_pair(db, principal.tenant_id, pair_id)
        if pair is None:
            # 查重对无独立行级数据域（挂两张档案，越权在下方逐档判定）→ 跨租户 = 不存在
            raise NotFoundError("查重对不存在")
        return pair

    @staticmethod
    def _can_access_talent(principal: CurrentPrincipal, talent: TalentBase) -> bool:
        """档案访问判定（同 tag_service 的简化口径，批次 10 登记）。"""
        from app.models.enums import RoleDataScope

        scope = int(principal.data_scope)
        if scope == int(RoleDataScope.ALL):
            return True
        if scope == int(RoleDataScope.OWN_DEPT):
            return True  # 列表侧已收窄；单条子树校验随 CU-22 统一补
        if scope == int(RoleDataScope.SELF):
            return talent.user_id is not None and int(talent.user_id) == int(principal.user_id)
        return False

    @staticmethod
    def _snap(value: Any) -> Any:
        """快照值转 JSON 安全形态（date → ISO 字符串）。"""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.isoformat()
        return value

    @staticmethod
    def _restore(field: str, value: Any) -> Any:
        """快照值还原为模型列类型。"""
        if value is None:
            return None
        if field == "birth_date" and isinstance(value, str):
            return date.fromisoformat(value)
        if field in ("gender", "work_years"):
            return int(value)
        return value

    @staticmethod
    def _detail_identity(row: Any, *, project: bool) -> tuple[str, ...]:
        """Build a normalized identity key without using description or tech stack."""
        def norm(value: Any) -> str:
            return "" if value is None else " ".join(str(value).strip().split()).casefold()

        def date_key(value: Any) -> str:
            return value.isoformat() if hasattr(value, "isoformat") else norm(value)

        if project:
            return (norm(row.project_name), date_key(row.start_date), date_key(row.end_date))
        return (
            norm(row.company),
            norm(row.position_name),
            date_key(row.start_date),
            date_key(row.end_date),
        )

    @staticmethod
    def _copy_work_exp(target: TalentWorkExp, source: TalentWorkExp) -> None:
        for field in (
            "company",
            "dept_name",
            "position_name",
            "start_date",
            "end_date",
            "is_current",
            "description",
            "source",
        ):
            setattr(target, field, getattr(source, field))

    @staticmethod
    def _copy_project_exp(target: TalentProjectExp, source: TalentProjectExp) -> None:
        for field in (
            "project_name",
            "role_name",
            "start_date",
            "end_date",
            "description",
            "tech_stack",
            "source",
        ):
            value = getattr(source, field)
            setattr(target, field, list(value) if field == "tech_stack" and value else value)

    @classmethod
    def _merge_work_exps(
        cls, db: Session, tenant_id: int, master_id: int, slave_id: int, now: datetime
    ) -> None:
        master_rows = talent_dao.list_work_exps(db, tenant_id, master_id)
        slave_rows = talent_dao.list_work_exps(db, tenant_id, slave_id)
        by_identity: dict[tuple[str, ...], TalentWorkExp] = {
            cls._detail_identity(row, project=False): row for row in master_rows
        }
        for slave_row in slave_rows:
            key = cls._detail_identity(slave_row, project=False)
            winner = by_identity.get(key)
            if winner is not None:
                # source=1 is人工，source=0 is AI；同来源时主档已有条目优先。
                if int(slave_row.source or 0) > int(winner.source or 0):
                    cls._copy_work_exp(winner, slave_row)
                slave_row.is_deleted = 1
                slave_row.deleted_at = now
            else:
                slave_row.talent_id = int(master_id)
                slave_row.deleted_at = None
                by_identity[key] = slave_row
                master_rows.append(slave_row)
        cls._resequence(master_rows)
        db.flush()

    @classmethod
    def _merge_project_exps(
        cls, db: Session, tenant_id: int, master_id: int, slave_id: int, now: datetime
    ) -> None:
        master_rows = talent_dao.list_project_exps(db, tenant_id, master_id)
        slave_rows = talent_dao.list_project_exps(db, tenant_id, slave_id)
        by_identity: dict[tuple[str, ...], TalentProjectExp] = {
            cls._detail_identity(row, project=True): row for row in master_rows
        }
        for slave_row in slave_rows:
            key = cls._detail_identity(slave_row, project=True)
            winner = by_identity.get(key)
            if winner is not None:
                if int(slave_row.source or 0) > int(winner.source or 0):
                    cls._copy_project_exp(winner, slave_row)
                slave_row.is_deleted = 1
                slave_row.deleted_at = now
            else:
                slave_row.talent_id = int(master_id)
                slave_row.deleted_at = None
                by_identity[key] = slave_row
                master_rows.append(slave_row)
        cls._resequence(master_rows)
        db.flush()

    @staticmethod
    def _resequence(rows: list[Any]) -> None:
        rows.sort(
            key=lambda row: (
                row.start_date or date.min,
                row.end_date or date.min,
                int(row.id),
            ),
            reverse=True,
        )
        for index, row in enumerate(rows, start=1):
            row.seq_no = index

    @classmethod
    def _work_exp_snapshot(cls, row: TalentWorkExp) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "talent_id": str(row.talent_id),
            "seq_no": int(row.seq_no),
            "company": row.company,
            "dept_name": row.dept_name,
            "position_name": row.position_name,
            "start_date": cls._snap(row.start_date),
            "end_date": cls._snap(row.end_date),
            "is_current": int(row.is_current),
            "description": row.description,
            "source": int(row.source),
            "is_deleted": int(row.is_deleted),
            "deleted_at": cls._snap(row.deleted_at),
        }

    @classmethod
    def _project_exp_snapshot(cls, row: TalentProjectExp) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "talent_id": str(row.talent_id),
            "seq_no": int(row.seq_no),
            "project_name": row.project_name,
            "role_name": row.role_name,
            "start_date": cls._snap(row.start_date),
            "end_date": cls._snap(row.end_date),
            "description": row.description,
            "tech_stack": list(row.tech_stack or []),
            "source": int(row.source),
            "is_deleted": int(row.is_deleted),
            "deleted_at": cls._snap(row.deleted_at),
        }

    @staticmethod
    def _restore_detail_snapshot(
        db: Session, tenant_id: int, model: type[Any], rows: list[dict[str, Any]]
    ) -> None:
        for payload in rows:
            row = db.get(model, int(payload["id"]))
            if row is None or int(row.tenant_id) != int(tenant_id):
                raise NotFoundError("合并明细快照对应的行不存在")
            for field, value in payload.items():
                if field in {"id", "deleted_at"}:
                    continue
                if field in {"start_date", "end_date"} and isinstance(value, str):
                    value = date.fromisoformat(value)
                setattr(row, field, value)
            deleted_at = payload.get("deleted_at")
            row.deleted_at = datetime.fromisoformat(deleted_at) if deleted_at else None
        db.flush()

    @staticmethod
    def _merge_tags(db: Session, tenant_id: int, master_id: int, slave_id: int, now: datetime) -> None:
        """把从档标签并入主档（去重），并软删从档标签（已「搬走」）。"""
        master_tags = {
            str(r.tag_code) for r in tag_dao.list_talent_tags(db, tenant_id, master_id)
        }
        slave_tags = tag_dao.list_talent_tags(db, tenant_id, slave_id)
        for rel in slave_tags:
            code = str(rel.tag_code)
            if code not in master_tags:
                tag_dao.insert_tag_rel(
                    db,
                    TalentTagRel(
                        tenant_id=tenant_id,
                        talent_id=master_id,
                        tag_code=code,
                        confidence=rel.confidence,
                        source=int(rel.source),
                    ),
                )
                master_tags.add(code)
            rel.is_deleted = 1
            rel.deleted_at = now
        db.flush()

    @staticmethod
    def _reindex(db: Session, provider: Any, talent: TalentBase, tenant_id: int) -> bool:
        """主档重索引（尽力而为）。返回是否真正重索引成功。"""
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
                vector=None,  # 🔴 真实向量待 H-19 吞吐标定后接入（CU-18 缺口）
            )
            return provider.last_error is None
        except Exception:  # noqa: BLE001 — 派生索引，尽力而为
            return False

    @classmethod
    def _to_candidate(cls, db: Session, principal: CurrentPrincipal, pair: Any) -> DupCandidateOut:
        a = talent_dao.get_talent(db, principal.tenant_id, int(pair.talent_id_a))
        b = talent_dao.get_talent(db, principal.tenant_id, int(pair.talent_id_b))
        return DupCandidateOut(
            pair_id=pair.id,
            talent_a=cls._to_brief(db, principal.tenant_id, a),
            talent_b=cls._to_brief(db, principal.tenant_id, b),
            similarity=float(pair.similarity) if pair.similarity is not None else None,
            match_fields=list(pair.match_fields or []),
            status=int(pair.status),
            status_name=pair_status_name_of(int(pair.status)),
        )

    @staticmethod
    def _to_brief(db: Session, tenant_id: int, talent: TalentBase | None) -> TalentBriefOut:
        if talent is None:
            return TalentBriefOut(talent_id=0, name="（已不存在）")
        from app.core.crypto import mask_encrypted_email, mask_encrypted_phone

        tags = sorted(
            str(r.tag_code) for r in tag_dao.list_talent_tags(db, tenant_id, int(talent.id))
        )
        return TalentBriefOut(
            talent_id=talent.id,
            name=talent.name,
            school=talent.school,
            major=talent.major,
            education_level=talent.education_level,
            work_years=int(talent.work_years) if talent.work_years is not None else None,
            phone_masked=mask_encrypted_phone(talent.phone_enc),
            email_masked=mask_encrypted_email(talent.email_enc),
            tags=tags,
        )
