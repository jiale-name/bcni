"""档案 RAG 问答（CU-21，API-63，F-3.7/F-3.9 / DF-3 P7.1~P7.9 / CL-4）。

🔴 本模块承载接口 API-63 的**五条红线**（缺一不可）：

| # | 红线 | 本模块落点 |
| :-: | --- | --- |
| 1 | 权限裁剪 | :meth:`_clip_scope` —— 先裁 ``scope`` 再检索；全部越权 **40301 不进流** |
| 2 | 脱敏出网 | :mod:`app.core.masking`（先脱敏）+ :func:`masking.verify_outbound`（二次扫描，残留即拒） |
| 3 | 本地回填 | :func:`masking.rehydrate`（本次请求内存的占位符表，不经外部服务） |
| 4 | 引用溯源 | :meth:`_postcheck_citations` —— 引用编号必须可定位到检索片段（确定性后置校验） |
| 5 | 无答案兜底 | :meth:`ThresholdEngine.is_answerable` —— 阈值之下**不调用大模型**（``tokens.in=0`` 可验证） |

铁律 10 的落点：**阈值判定、权限裁剪、二次扫描、引用校验全部是确定性组件**；
LLM（网关 ``scene=talent_ask``）只负责把「已脱敏片段 + 问题」变成文本 ——
它既不决定「能不能答」，也不决定「引用了什么」。

⚠️ 流式说明：网关 ``generate`` 目前为非流式（``stream=False``），SSE 的 ``delta``
是**整包拆片**下发（语义等价、事件结构一致）；token 级流式需网关上游支持 SSE，
登记为批次 12 遗留（Q-2 关闭后随云端通道一并接入）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cost import CostCalculator
from app.core.logging import get_logger
from app.core.masking import (
    PlaceholderTable,
    mask_text,
    rehydrate,
    verify_outbound,
)
from app.core.model_gateway import ModelGateway
from app.core.tenant import CurrentPrincipal, DataScopeFilter
from app.core.threshold import ThresholdEngine
from app.dao import talent_dao
from app.models.enums import AiCallStatus
from app.models.talent import TalentBase
from app.retrieval.hybrid import lexical_score
from app.schemas.ai import AiScene, AiDeployment
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.search import (
    AskCitationOut,
    AskDoneOut,
    AskRefusalOut,
    AskRequest,
    AskResultOut,
    ScopeEffectiveOut,
)
from app.schemas.talent_master import (
    AskSessionClearOut,
    AskSessionOut,
    AskTurnOut,
)
from app.services.ai_log_service import AiLogService
from app.services.ai_service import build_targets
from app.services.audit_service import AuditService
from app.services.org_scope_service import OrgScopeService
from app.services.parse_pipeline import FIELD_LABELS

logger = get_logger(__name__)

#: 引用标记（Prompt 强制要求模型用 ``[n]`` 标注依据）
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

#: 🔴 两个上限**必须分开**（CU-29 修正）：
#:
#: - ``_SESSION_HISTORY_LIMIT``：喂给 Prompt 的**窗口**（最近几轮）。多轮指代消解
#:   只需要最近几轮，全量历史既烧 token 又容易让模型跑题。
#: - ``_SESSION_STORE_LIMIT``：Redis 里**保留**的轮数。接口 API-64 明确要求
#:   「会话历史回看 **≥10 轮**」—— 若沿用 4 轮，接口承诺直接落空。
#:
#: ⚠️ 早前两者共用一个常量（4），这正是「API-64 无法满足契约（只能看 4 轮）」
#:    的根因。拆开之后：存储保留 12 轮（满足 ≥10），Prompt 仍只用最近 4 轮
#:    （token 与跑题控制不变）—— 两个目标都不牺牲。
_SESSION_HISTORY_LIMIT = 4
_SESSION_STORE_LIMIT = 12

#: 单档案最大片段数（约束 Prompt 规模；50 份批量 × 12 片段已是上限形态）
_MAX_CHUNKS_PER_TALENT = 12


class AskService:
    """API-63 档案 RAG 问答（整包；SSE 由 Router 拆事件）。"""

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    @classmethod
    def ask(cls, db: Session, principal: CurrentPrincipal, req: AskRequest) -> AskResultOut:
        session_id = (req.session_id or "").strip() or f"s-{uuid.uuid4().hex[:20]}"
        request_id = uuid.uuid4().hex[:8]

        # ---- P7.2 权限裁剪（确定性；全部越权 40301 不进流）----
        talents, scope_effective = cls._clip_scope(db, principal, req)
        allowed_ids = [int(t.id) for t in talents]
        names = [str(t.name) for t in talents]

        # ---- P7.1 会话加载与问题改写（Redis fail-open；改写失败用原问题）----
        history = cls._load_history(principal.tenant_id, session_id)
        question = cls._rewrite_question(db, principal.tenant_id, req.question, history)

        # ---- P7.3 混合召回（向量可用则叠加，词法分恒兜底）----
        table = PlaceholderTable()
        chunks = cls._assemble_chunks(db, principal.tenant_id, talents, table)
        scored = cls._score_chunks(db, principal, req, chunks)
        top = scored[: int(req.top_k)]
        best_score = top[0]["score"] if top else 0.0

        # ---- P7.5 无答案兜底（确定性；**不调用大模型**）----
        gate = ThresholdEngine.is_answerable(best_score=best_score)
        if not gate.answerable:
            result = AskResultOut(
                session_id=session_id,
                request_id=request_id,
                model=None,
                mask_applied=True,
                scope_effective=scope_effective,
                refusal=AskRefusalOut(
                    reason="LOW_SIMILARITY",
                    message="档案中未找到与该问题相关的依据，无法作答。",
                ),
                done=AskDoneOut(finish_reason="refused", fallback=True),
            )
            cls._record_refusal(db, principal, req, result, question)
            return result

        # ---- P7.6 出网前二次扫描（残留明文身份 → 拒答，绝不降级为明文出网）----
        fragments = [str(c["masked_text"]) for c in top]
        guard = verify_outbound("\n".join(fragments), known_names=names)
        if not guard.passed:
            logger.warning(
                "出网拦截：残留敏感信息 %s tid=%s user=%s", guard.residual, principal.tenant_id, principal.user_id
            )
            result = AskResultOut(
                session_id=session_id,
                request_id=request_id,
                model=None,
                mask_applied=True,
                scope_effective=scope_effective,
                refusal=AskRefusalOut(
                    reason="NER_RESIDUAL",
                    message="该内容涉及敏感信息，无法生成回答。",
                ),
                done=AskDoneOut(finish_reason="refused"),
            )
            cls._record_refusal(db, principal, req, result, question, blocked=True)
            return result

        # ---- P7.7 Prompt 组装（含强制逐条引用要求）→ 网关 ----
        prompt = cls._build_prompt(req.question, top)
        targets = build_targets(db, principal.tenant_id, AiScene.TALENT_ASK)
        gateway = ModelGateway(principal.tenant_id)
        try:
            call = gateway.call(targets, prompt=prompt) if targets else None
        finally:
            gateway.close()

        if call is None or not call.ok:
            # 网关降级（永不抛 500）：整包置 degraded，Router 以 error 事件收尾
            result = AskResultOut(
                session_id=session_id,
                request_id=request_id,
                model=None,
                mask_applied=True,
                degraded=True,
                degraded_reason=(call.degraded_reason if call is not None else "无可用模型配置"),
                scope_effective=scope_effective,
                done=AskDoneOut(finish_reason="error"),
            )
            AiLogService.record(
                tenant_id=principal.tenant_id,
                scene=AiScene.TALENT_ASK,
                deployment=AiDeployment.LOCAL,
                mask_applied=True,
                status=int(AiCallStatus.FAILED),
                error_code=str(call.error_code) if call is not None else "50301",
                error_msg=(call.error_msg if call is not None else "无可用模型配置"),
                latency_ms=(call.latency_ms if call is not None else 0),
                user_id=principal.user_id,
                db=db,
            )
            return result

        assert call.target is not None  # ok=True 时网关必回填命中路
        raw_answer = str(call.content or "")

        # ---- P7.8 占位符本地回填（失败 → MASK_REQUIRED，绝不明文出网/下发占位符）----
        answer = rehydrate(raw_answer, table)
        if answer is None:
            result = AskResultOut(
                session_id=session_id,
                request_id=request_id,
                model=call.target.model_name,
                mask_applied=True,
                scope_effective=scope_effective,
                refusal=AskRefusalOut(
                    reason="MASK_REQUIRED",
                    message="回答内容存在无法回填的占位符，已拒绝下发。",
                ),
                done=AskDoneOut(
                    finish_reason="refused",
                    tokens_in=int(call.tokens_in),
                    tokens_out=int(call.tokens_out),
                ),
            )
            cls._log_call(db, principal, call, mask_applied=True, status=int(AiCallStatus.SUCCESS))
            cls._record_refusal(db, principal, req, result, question)
            return result

        # ---- P7.9 引用溯源后置校验（确定性：编号必须可定位到送模型片段）----
        citations = cls._postcheck_citations(answer, top, table)
        if not citations:
            # 红线 4：citation_count=0 的 done 视为缺陷 → 按无依据拒答处理（登记决策见评审文档）
            result = AskResultOut(
                session_id=session_id,
                request_id=request_id,
                model=call.target.model_name,
                mask_applied=True,
                scope_effective=scope_effective,
                refusal=AskRefusalOut(
                    reason="LOW_SIMILARITY",
                    message="回答未提供可定位的档案依据，已拒绝下发。",
                ),
                done=AskDoneOut(
                    finish_reason="refused",
                    tokens_in=int(call.tokens_in),
                    tokens_out=int(call.tokens_out),
                ),
            )
            cls._log_call(db, principal, call, mask_applied=True, status=int(AiCallStatus.SUCCESS))
            cls._record_refusal(db, principal, req, result, question)
            return result

        cost = CostCalculator.compute(
            tokens_in=int(call.tokens_in),
            tokens_out=int(call.tokens_out),
            price_in_per_1k=call.target.price_in_per_1k,
            price_out_per_1k=call.target.price_out_per_1k,
        )
        result = AskResultOut(
            session_id=session_id,
            request_id=request_id,
            model=call.target.model_name,
            mask_applied=True,
            scope_effective=scope_effective,
            answer=answer,
            citations=citations,
            done=AskDoneOut(
                finish_reason="stop",
                tokens_in=int(call.tokens_in),
                tokens_out=int(call.tokens_out),
                cost_yuan=f"{cost:.4f}",
                citation_count=len(citations),
                clipped=scope_effective.clipped,
            ),
        )
        cls._log_call(db, principal, call, mask_applied=True, status=int(AiCallStatus.SUCCESS))
        cls._save_history(principal.tenant_id, session_id, req.question, answer)
        AuditService.record(
            AuditAction.TALENT_ASK,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            after={
                "question": req.question,
                "session_id": session_id,
                "model": call.target.model_name,
                "cloud": call.cloud_used,
                "citation_count": len(citations),
                "scope_effective": scope_effective.model_dump(mode="json"),
            },
        )
        return result

    # ==================================================================
    # P7.2 权限裁剪
    # ==================================================================
    @staticmethod
    def _clip_scope(
        db: Session, principal: CurrentPrincipal, req: AskRequest
    ) -> tuple[list[TalentBase], ScopeEffectiveOut]:
        """先裁 ``scope`` 再检索：域外 ID 记入 ``denied``；**全部越权 → 40301 不进流**。"""
        from app.exceptions import PermissionDeniedError

        DataScopeFilter.require_detail_access(principal)
        dept_ids = AskService._resolve_dept_ids(db, principal)
        requested = [int(i) for i in req.scope.talent_ids]
        scope_clause = DataScopeFilter.mysql_condition(
            TalentBase, principal, dept_ids=dept_ids, owner_column="user_id"
        )
        stmt = (
            select(TalentBase)
            .where(
                TalentBase.tenant_id == principal.tenant_id,
                TalentBase.id.in_(requested),
                TalentBase.is_deleted == 0,
                TalentBase.data_status.in_((0, 1)),
                scope_clause,
            )
            .order_by(TalentBase.id)
        )
        allowed_rows = list(db.execute(stmt).scalars().all())
        allowed_set = {int(t.id) for t in allowed_rows}
        denied = [tid for tid in requested if tid not in allowed_set]

        if not allowed_rows:
            raise PermissionDeniedError(
                "提问范围包含无权访问的档案",
                {"denied_talent_ids": [str(i) for i in denied or requested]},
            )
        scope_effective = ScopeEffectiveOut(
            requested=len(requested),
            allowed=len(allowed_rows),
            clipped=bool(denied),
            denied_talent_ids=[int(i) for i in denied],
        )
        return allowed_rows, scope_effective

    @staticmethod
    def _resolve_dept_ids(db: Session, principal: CurrentPrincipal) -> tuple[int, ...]:
        if int(principal.data_scope) == 2 and not principal.dept_ids and principal.org_id is not None:
            return OrgScopeService.resolve_dept_ids(db, principal.tenant_id, principal.org_id)
        return tuple(principal.dept_ids)

    # ==================================================================
    # P7.1 会话与改写（Redis 只存可丢数据，fail-open）
    # ==================================================================
    @staticmethod
    def _session_key(tenant_id: int, session_id: str) -> str:
        from app.dao.redis_client import build_key

        return build_key("agent_mem:ask:", int(tenant_id), session_id)

    @classmethod
    def _load_full_history(cls, tenant_id: int, session_id: str) -> list[dict[str, str]]:
        """读**全部保留轮次**（不被 Prompt 窗口截断）。

        API-64 要求回看 ≥10 轮，而喂 Prompt 只需要最近 4 轮 —— 两者用途不同，
        故「读全量」与「取窗口」必须分成两步（见 ``_SESSION_STORE_LIMIT`` 注释）。
        """
        try:
            from app.dao.redis_client import get_redis

            import json

            raw = get_redis().get(cls._session_key(tenant_id, session_id))
            data = json.loads(raw) if raw else []
            return list(data) if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 — 会话历史是可丢数据，丢了就当单轮
            logger.info("问答会话历史读取失败（按无历史处理）：%s", exc)
            return []

    @classmethod
    def _load_history(cls, tenant_id: int, session_id: str) -> list[dict[str, str]]:
        """取**喂 Prompt 的最近 N 轮**（不是全部，见 :meth:`_load_full_history`）。"""
        return cls._load_full_history(tenant_id, session_id)[-_SESSION_HISTORY_LIMIT:]

    @classmethod
    def _save_history(cls, tenant_id: int, session_id: str, question: str, answer: str) -> None:
        try:
            from app.dao.redis_client import get_redis

            import json

            key = cls._session_key(tenant_id, session_id)
            client = get_redis()
            raw = client.get(key)
            history = json.loads(raw) if raw else []
            if not isinstance(history, list):
                history = []
            history.append({"q": question[:500], "a": answer[:500]})
            # 🔴 存到 ``_SESSION_STORE_LIMIT``（供 API-64 回看 ≥10 轮），
            #    而**不是** Prompt 窗口 —— 写入时就截到 4 轮的话，
            #    历史根本到不了 API-64（这正是修正前的实际行为）。
            client.set(key, json.dumps(history[-_SESSION_STORE_LIMIT:], ensure_ascii=False), ex=3600)
        except Exception as exc:  # noqa: BLE001
            logger.info("问答会话历史写入失败（忽略）：%s", exc)

    @classmethod
    def _session_ttl(cls, tenant_id: int, session_id: str) -> int | None:
        """剩余存活秒数（Redis 降级时返回 ``None``，不抛）。"""
        try:
            from app.dao.redis_client import get_redis

            ttl = get_redis().ttl(cls._session_key(tenant_id, session_id))
            value = int(ttl) if ttl is not None else -1
            return value if value > 0 else None
        except Exception as exc:  # noqa: BLE001
            logger.info("读取会话 TTL 失败：%s", exc)
            return None

    # ==================================================================
    # API-64 会话历史回看（CU-29）
    # ==================================================================
    @classmethod
    def session_history(cls, principal: CurrentPrincipal, session_id: str) -> AskSessionOut:
        """回看某会话的全部保留轮次（接口要求 ≥10 轮，见 ``_SESSION_STORE_LIMIT``）。

        🔴 三件事：
        1. **租户隔离**是天然的：key 由 ``_session_key(tenant_id, session_id)`` 构造，
           换租户就是另一个 key，取不到别人的会话；
        2. **留痕**：``question`` 是**问题原文**（业务数据），接口 §2.2 明确
           「仅 ADM/HR 经 API-64 可见」—— 故回看行为必须写审计；
        3. **只读**：本方法不刷新 TTL、不改动会话内容（回看不应延长数据寿命）。

        ⚠️ **已登记缺口**：Redis 里的会话目前**不记录 owner_user_id**
        （value 只有 ``[{q,a}, ...]``）。同租户内若拿到 session_id 即可读到该会话。
        缓解是 session_id 为 20 位随机 hex（不可枚举）+ 权限点 + 审计三道。
        根治需在 value 里落 ``owner_user_id``（改动 CU-21 的写入结构，属下一个批次）。
        """
        tenant_id = int(principal.tenant_id)
        sid = (session_id or "").strip()
        turns = cls._load_full_history(tenant_id, sid)

        AuditService.record(
            AuditAction.ASK_SESSION_VIEW,
            tenant_id=tenant_id,
            user_id=int(principal.user_id),
            target_type=AuditTarget.SESSION,
            after={"session_id": sid, "turn_count": len(turns)},
        )

        return AskSessionOut(
            session_id=sid,
            turn_count=len(turns),
            max_turns=_SESSION_STORE_LIMIT,
            ttl_seconds=cls._session_ttl(tenant_id, sid),
            items=[
                AskTurnOut(question=str(t.get("q") or ""), answer=str(t.get("a") or ""))
                for t in turns
            ],
        )

    # ==================================================================
    # API-65 清空会话（CU-29）
    # ==================================================================
    @classmethod
    def clear_session(cls, principal: CurrentPrincipal, session_id: str) -> AskSessionClearOut:
        """清空会话（破坏性动作：抹掉对话证据，故必须留痕）。

        ⚠️ Redis 是**可丢数据**（铁律 6），所以「清空失败」不返 500 ——
        而是 ``cleared=false`` + 说明。理由：Redis 抖动时若返 500，
        用户以为「清空失败、对话还在」，而实际它可能已经自己过期了；
        明确告知「存储不可用、未执行清空」才是可行动的结论。
        """
        tenant_id = int(principal.tenant_id)
        sid = (session_id or "").strip()
        removed = 0
        cleared = False
        message: str | None = None
        try:
            from app.dao.redis_client import get_redis

            import json

            client = get_redis()
            key = cls._session_key(tenant_id, sid)
            raw = client.get(key)
            if raw:
                data = json.loads(raw)
                removed = len(data) if isinstance(data, list) else 0
            cleared = int(client.delete(key) or 0) > 0
            if not cleared:
                message = "该会话不存在或已过期（无需清空）"
        except Exception as exc:  # noqa: BLE001 — 可丢数据，降级不抛
            logger.info("清空会话失败（Redis 降级）：%s", exc)
            message = "会话存储当前不可用，未执行清空；请稍后重试"

        AuditService.record(
            AuditAction.ASK_SESSION_CLEAR,
            tenant_id=tenant_id,
            user_id=int(principal.user_id),
            target_type=AuditTarget.SESSION,
            after={"session_id": sid, "removed_turns": removed, "cleared": cleared},
        )

        return AskSessionClearOut(
            session_id=sid, cleared=cleared, removed_turns=removed, message=message
        )

    @classmethod
    def _rewrite_question(
        cls, db: Session, tenant_id: int, question: str, history: list[dict[str, str]]
    ) -> str:
        """多轮指代消解（本地小模型 ``scene=query_rewrite``）；失败/无历史 → 原问题。"""
        if not history:
            return question
        context = "\n".join(f"问：{h.get('q', '')}\n答：{h.get('a', '')[:200]}" for h in history)
        prompt = (
            "以下是与当前问题相关的对话历史。请把「当前问题」改写为一个不依赖上下文、"
            "可独立理解的完整问题（把「他/她/它/这个人」等指代还原为具体对象）。\n"
            f"{context}\n当前问题：{question}\n只输出改写后的问题本身。"
        )
        targets = build_targets(db, tenant_id, AiScene.QUERY_REWRITE)
        if not targets:
            return question
        gateway = ModelGateway(tenant_id)
        try:
            call = gateway.call(targets, prompt=prompt)
        finally:
            gateway.close()
        rewritten = (call.content or "").strip() if call is not None and call.ok else ""
        # 改写结果明显异常（空/过长）时退回原问题 —— 改写是优化，不是依赖
        if 2 <= len(rewritten) <= 1000:
            return rewritten
        return question

    # ==================================================================
    # P7.3 召回与打分
    # ==================================================================
    @classmethod
    def _assemble_chunks(
        cls, db: Session, tenant_id: int, talents: list[TalentBase], table: PlaceholderTable
    ) -> list[dict[str, Any]]:
        """从权威数据（MySQL）组装**脱敏后**的问答片段（chunk_pk 与 Milvus 同构）。

        🔴 与 :func:`app.retrieval.profile.build_profile_text` 同源的脱敏口径：
        姓名→``{{CANDIDATE}}``，手机/邮箱/证件由解密值（可得时）或兜底正则替换。
        """
        chunks: list[dict[str, Any]] = []
        for talent in talents:
            tid = int(talent.id)
            name = str(talent.name)
            phone = _decrypt(talent.phone_enc)
            email = _decrypt(talent.email_enc)
            # 登记真值 → 占位符（回填时用同一张表还原）
            table.put("candidate", name)
            if phone:
                table.put("phone", phone)
            if email:
                table.put("email", email)

            profile_bits = [f"姓名：{name}"]
            if talent.school:
                profile_bits.append(f"院校：{talent.school}")
            if talent.major:
                profile_bits.append(f"专业：{talent.major}")
            if talent.education_level:
                profile_bits.append(f"学历：{talent.education_level}")
            if talent.work_years is not None:
                profile_bits.append(f"工作年限：{talent.work_years} 年")
            profile_text = "；".join(profile_bits)
            chunks.append(
                {
                    "talent_id": tid,
                    "field_code": "profile",
                    "field_name": "基本画像",
                    "text": profile_text,
                    "masked_text": mask_text(profile_text, table, name=name, phone=phone, email=email),
                    "chunk_pk": f"{tid}:profile:0",
                }
            )

            for i, field in enumerate(talent_dao.list_fields_by_talent(db, tenant_id, tid)[:_MAX_CHUNKS_PER_TALENT]):
                if not field.parsed_value:
                    continue
                label = FIELD_LABELS.get(str(field.field_name), str(field.field_name))
                text = f"{label}：{field.parsed_value}"
                chunks.append(
                    {
                        "talent_id": tid,
                        "field_code": str(field.field_name),
                        "field_name": label,
                        "text": text,
                        "masked_text": mask_text(text, table, name=name, phone=phone, email=email),
                        "chunk_pk": f"{tid}:{field.field_name}:{i}",
                    }
                )
        return chunks

    @classmethod
    def _score_chunks(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        req: AskRequest,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """片段打分：**词法分恒兜底（确定性）**；向量分可用时取两者较高。

        🔴 铁律 10：兜底与阈值判定都吃这个分数，所以它必须可复算 ——
        即使向量链路（H-19）就绪，词法分仍是「为什么召回这段」的第二解释口径。
        """
        out: list[dict[str, Any]] = []
        for chunk in chunks:
            score = lexical_score(req.question, str(chunk["text"]))
            chunk["score"] = score
            out.append(chunk)

        # 向量叠加：embedding 可用 + 检索实现为向量路由时，按 talent_id 匹配提升片段分
        try:
            from app.retrieval import MilvusRetrievalProvider, get_retrieval_provider

            provider = get_retrieval_provider()
            if isinstance(provider, MilvusRetrievalProvider):
                query_vec = AskService._embed_query(db, principal.tenant_id, req.question)
                if query_vec is not None:
                    dept_ids = AskService._resolve_dept_ids(db, principal)
                    org_ids = list(dept_ids) or None
                    hits = provider.search(
                        db,
                        tenant_id=principal.tenant_id,
                        org_ids=org_ids,
                        top_k=settings_recall_top(),
                        vector=query_vec,
                    )
                    by_talent: dict[int, float] = {}
                    for hit in hits:
                        by_talent[int(hit.talent_id)] = max(
                            by_talent.get(int(hit.talent_id), 0.0),
                            min(1.0, max(0.0, float(hit.similarity))),
                        )
                    for chunk in out:
                        vec = by_talent.get(int(chunk["talent_id"]))
                        if vec is not None:
                            chunk["score"] = round(max(float(chunk["score"]), vec), 4)
        except Exception as exc:  # noqa: BLE001 — 向量叠加是增强，失败退词法
            logger.info("问答向量叠加失败（退词法分）：%s", exc)

        out.sort(key=lambda c: (-float(c["score"]), int(c["talent_id"]), str(c["chunk_pk"])))
        return out

    @staticmethod
    def _embed_query(db: Session, tenant_id: int, query: str) -> list[float] | None:
        targets = build_targets(db, tenant_id, AiScene.EMBEDDING)
        if not targets:
            return None
        gateway = ModelGateway(tenant_id)
        try:
            call = gateway.call(targets, texts=[query])
            if call.ok and call.vectors:
                return call.vectors[0]
            return None
        finally:
            gateway.close()

    # ==================================================================
    # P7.7 / P7.9
    # ==================================================================
    @staticmethod
    def _build_prompt(question: str, top: list[dict[str, Any]]) -> str:
        lines = [
            "你是人才档案问答助手。只能依据下方编号片段回答问题；"
            "每一条结论后必须标注依据片段编号，如 [1]、[2]。"
            "片段中没有依据的内容一律不得编造；若依据不足以回答，直接说明「依据不足」。",
            "",
        ]
        for i, chunk in enumerate(top, start=1):
            lines.append(f"[{i}] 人才#{chunk['talent_id']} {chunk['masked_text']}")
        lines += ["", f"问题：{question}", "回答（含 [n] 引用）："]
        return "\n".join(lines)

    @staticmethod
    def _postcheck_citations(
        answer: str, top: list[dict[str, Any]], table: PlaceholderTable
    ) -> list[AskCitationOut]:
        """抽取并校验引用：编号必须在送模型片段范围内（确定性；防「幻觉引用」）。"""
        indices = sorted({int(m) for m in _CITATION_RE.findall(answer or "")})
        out: list[AskCitationOut] = []
        for idx in indices:
            if not (1 <= idx <= len(top)):
                continue
            chunk = top[idx - 1]
            snippet = rehydrate(str(chunk["masked_text"]), table) or str(chunk["text"])
            out.append(
                AskCitationOut(
                    index=idx,
                    talent_id=int(chunk["talent_id"]),
                    field_code=str(chunk["field_code"]),
                    field_name=str(chunk["field_name"]),
                    snippet=snippet,
                    chunk_pk=str(chunk["chunk_pk"]),
                )
            )
        return out

    # ==================================================================
    # 日志与审计
    # ==================================================================
    @staticmethod
    def _log_call(
        db: Session,
        principal: CurrentPrincipal,
        call: Any,
        *,
        mask_applied: bool,
        status: int,
    ) -> None:
        AiLogService.record(
            tenant_id=principal.tenant_id,
            scene=AiScene.TALENT_ASK,
            provider=(call.target.provider if call.target else None),
            model=(call.target.model_name if call.target else None),
            deployment=(AiDeployment.CLOUD if (call.target and call.target.is_cloud) else AiDeployment.LOCAL),
            tokens_in=int(call.tokens_in),
            tokens_out=int(call.tokens_out),
            price_in_per_1k=(call.target.price_in_per_1k if call.target else None),
            price_out_per_1k=(call.target.price_out_per_1k if call.target else None),
            latency_ms=int(call.latency_ms),
            fallback_used=bool(call.fallback_used),
            mask_applied=mask_applied,
            status=status,
            user_id=principal.user_id,
            db=db,
        )

    @classmethod
    def _record_refusal(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        req: AskRequest,
        result: AskResultOut,
        question: str,
        *,
        blocked: bool = False,
    ) -> None:
        """拒答也要留痕（审计 + 会话历史），但**不写 ai_call_log**（没有真实模型调用）。"""
        AuditService.record(
            AuditAction.TALENT_ASK,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.TALENT,
            after={
                "question": question,
                "session_id": result.session_id,
                "refusal": (result.refusal.reason if result.refusal else None),
                "blocked": blocked,
                "scope_effective": result.scope_effective.model_dump(mode="json"),
            },
        )
        if result.done.finish_reason == "refused" and not blocked:
            cls._save_history(
                principal.tenant_id, result.session_id, req.question, "（依据不足，未作答）"
            )


# ============================================================================
# 工具
# ============================================================================
def _decrypt(blob: bytes | None) -> str | None:
    """解密敏感字段（AES 未配置 / 解密失败 → ``None``，调用方退化为正则兜底脱敏）。"""
    if blob is None:
        return None
    try:
        from app.core.crypto import decrypt_sensitive

        return decrypt_sensitive(blob)
    except Exception:  # noqa: BLE001 — 解密失败不得阻断问答（正则兜底仍会拦截明文形态）
        return None


def settings_recall_top() -> int:
    """问答向量召回条数（复用 ``RAG_RECALL_TOP_K``）。"""
    from app.config import settings

    return int(settings.RAG_RECALL_TOP_K)
