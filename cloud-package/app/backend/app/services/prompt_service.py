"""Prompt 版本管理（CU-11，API-84 ~ API-87，F-9.2）。

🔴 核心不变式：**版本正文只追加，永不改写历史**
==========================================
``(tenant_id, prompt_code)`` 是一个**模板族**，``version`` 族内单调递增。
「修改」与「回滚」**都不是 UPDATE**，而是插入 ``version = max + 1`` 的新行。

理由（这条理由必须写下来，否则迟早有人为了「省一张表」改成原地更新）：

1. ``ai_call_log.prompt_version`` 记录的是**当时**用的版本号。
   若正文可被原地改写，这个版本号就指向一份**已经变了的**正文 ——
   线上出现「同一个输入昨天答得对、今天答错了」时，**无法复现**，
   版本号记了等于白记；
2. F-9.2 要求 Prompt 可审计。可审计的前提是「历史不可改」，
   而不是「历史改过但记了日志」——后者要读两份记录才能还原真相。

🔴 第二条不变式：**同一模板族内最多一个「启用」版本**
==============================================
``status=1``（启用）在族内只能有 1 条 —— 否则「取当前生效版本」这件事
就有歧义（``ORDER BY version DESC LIMIT 1`` 会取到最新的，
但「最新」与「启用」是两件事：可以存在一个刚写好但还没启用的草稿版本号更大）。

故本模块每一次把某版本置为启用时，都**同一事务内**把同族其他版本置为停用
（:func:`app.dao.ai_dao.set_prompt_status` 的批量 UPDATE）。这两步必须原子：
中间态（两个启用版本）一旦落库，且随后发生异常，就是一个**永久性的歧义状态**。

🔴 第三条不变式：**「改了但内容一样」必须拒绝**
==========================================
版本历史是「哪个 Prompt 产生了哪个输出」的追溯依据。允许「一字未改也生成新版本」
会让版本号迅速膨胀（试跑时反复提交），而版本号一旦失去信息量，
追溯就退化成「翻几十个内容相同的版本看哪个是真改过的」。
故内容与源版本一致时直接 **42201**（状态机非法流转语义：这次操作没有意义）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import ai_dao
from app.exceptions import BizError, ErrorCode, IllegalStateError, NotFoundError
from app.models.ai import PromptTemplate
from app.models.enums import PromptStatus
from app.schemas.ai import (
    PromptCreateRequest,
    PromptOut,
    PromptRollbackRequest,
    PromptScene,
    PromptUpdateRequest,
)
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.services.audit_service import AuditService

logger = get_logger(__name__)

#: 正文长度上限（TEXT 列理论上 64KB，但一份 Prompt 超过这个量级
#: 基本都是「把示例数据也拼进去了」；限制它同时防住「把整库塞进 Prompt」）
MAX_CONTENT_CHARS = 20000


class PromptService:
    """Prompt 模板的查询 / 新建 / 改版 / 回滚（API-84 ~ API-87）。"""

    # ------------------------------------------------------------------
    # API-84 列表
    # ------------------------------------------------------------------
    @classmethod
    def list_prompts(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        scene: str | None = None,
        prompt_code: str | None = None,
        status: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[PromptOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = ai_dao.list_prompts(
            db,
            principal.tenant_id,
            scene=scene,
            prompt_code=prompt_code,
            status=status,
            limit=size,
            offset=(current - 1) * size,
        )
        total = ai_dao.count_prompts(
            db, principal.tenant_id, scene=scene, prompt_code=prompt_code, status=status
        )
        return [cls.to_out(row) for row in rows], total, current, size

    # ------------------------------------------------------------------
    # API-85 新建
    # ------------------------------------------------------------------
    @classmethod
    def create_prompt(
        cls, db: Session, principal: CurrentPrincipal, payload: PromptCreateRequest
    ) -> PromptOut:
        """新建模板族（``version`` 固定从 1 开始）。

        :raises BizError: ``prompt_code`` 已存在 → **40901**。
            🔴 这里**不**「为了友好」自动变成「给已有族加一个版本」——
            调用方以为自己在建新模板、实际改了另一个模板的启用版本，
            是那种「界面上什么异常都没有、但线上 Prompt 已经换了」的事故。
        """
        content = cls._validate_content(payload.content)
        existing = ai_dao.list_prompt_versions(db, principal.tenant_id, payload.prompt_code)
        if existing:
            raise BizError(
                ErrorCode.UNIQUE_CONFLICT,
                f"Prompt 编码 {payload.prompt_code!r} 已存在（最新版本 v{existing[-1].version}）",
                {
                    "prompt_code": payload.prompt_code,
                    "latest_version": int(existing[-1].version),
                    "next_id": str(existing[-1].id),
                },
            )

        row = PromptTemplate(
            tenant_id=principal.tenant_id,
            prompt_code=payload.prompt_code,
            scene=payload.scene,
            version=1,
            content=content,
            status=int(payload.status),
            created_by=principal.user_id,
        )
        ai_dao.insert_prompt(db, row)
        # 新建族只有这一个版本，故无需停用兄弟版本（但保持调用形式一致，防将来加逻辑漏掉）
        if int(payload.status) == int(PromptStatus.ENABLED):
            ai_dao.set_prompt_status(
                db, principal.tenant_id, payload.prompt_code, int(PromptStatus.DISABLED),
                except_id=int(row.id),
            )
        db.commit()

        AuditService.record(
            AuditAction.AI_PROMPT_CREATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PROMPT,
            target_id=row.id,
            after=cls._snapshot(row, remark=payload.remark),
        )
        return cls.to_out(row)

    # ------------------------------------------------------------------
    # API-86 修改（生成新版本）
    # ------------------------------------------------------------------
    @classmethod
    def update_prompt(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        prompt_id: int,
        payload: PromptUpdateRequest,
    ) -> PromptOut:
        """以 ``prompt_id`` 指向的版本为**源版本**，生成 ``version = max + 1`` 的新版本。

        :param prompt_id: **源版本**的 ID（不是 "prompt_code"）——
            与接口 §2.2 的路径 ``/ai/prompts/{id}`` 一致。
        :raises NotFoundError: 源版本不存在 / 已软删 → **40401**
        :raises IllegalStateError: 新正文与源版本完全一致 → **42201**
        """
        source = ai_dao.get_prompt(db, principal.tenant_id, prompt_id)
        if source is None:
            raise NotFoundError("Prompt 版本不存在")

        content = cls._validate_content(payload.content)
        if content == source.content:
            raise IllegalStateError(
                f"内容与源版本 v{source.version} 完全一致，无需生成新版本",
                {"source_version": int(source.version), "prompt_code": source.prompt_code},
            )

        # 新版本状态：显式传了用传入值，否则**继承源版本**
        # （改一个已启用的 Prompt，期望显然也是启用 —— 若默认落回草稿，
        #  改完线上行为不变，而调用方以为已经生效）
        new_status = int(payload.status) if payload.status is not None else int(source.status)
        row = PromptTemplate(
            tenant_id=principal.tenant_id,
            prompt_code=source.prompt_code,
            scene=source.scene,
            version=ai_dao.get_prompt_max_version(db, principal.tenant_id, source.prompt_code) + 1,
            content=content,
            status=new_status,
            created_by=principal.user_id,
        )
        ai_dao.insert_prompt(db, row)
        cls._enforce_single_enabled(db, principal.tenant_id, source.prompt_code, row)
        db.commit()

        AuditService.record(
            AuditAction.AI_PROMPT_UPDATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PROMPT,
            target_id=row.id,
            before=cls._snapshot(source, remark=None),
            after=cls._snapshot(row, remark=payload.remark),
        )
        return cls.to_out(row)

    # ------------------------------------------------------------------
    # API-87 回滚
    # ------------------------------------------------------------------
    @classmethod
    def rollback_prompt(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        prompt_id: int,
        payload: PromptRollbackRequest,
    ) -> PromptOut:
        """回滚到 ``target_version`` —— **以该版本的正文追加一个新版本并置为启用**。

        🔴 为什么不是「把旧版本重新置为启用」（这是最自然但错误的做法）：

        1. 版本号会**倒退**，「版本号越大越新」这条性质失效 ——
           而 ``ai_call_log.prompt_version`` 的追溯、前端的版本下拉、排序全都依赖它；
        2. 「v3 在 2026-09 启用过、又在 2026-11 重新启用」这件事在库里**看不出来**
           （只有 ``updated_at`` 变了，而它是状态字段的更新时间，不是业务事件时间）；
        3. 一旦后来有人误操作把 v3 又停用，无法区分「哪次停用是哪次启用之后的」。

        追加新版本则天然留下完整时间线：``v5 = v3 的正文``，
        ``before/after`` 快照里带上「回滚自 v3」。

        :param prompt_id: 发起回滚的版本 ID（仅用于定位模板族）
        :raises NotFoundError: 发起版本 / 目标版本不存在 → **40401**
        :raises IllegalStateError: 目标版本正文与当前最新版本一致 → **42201**（无意义操作）
        """
        source = ai_dao.get_prompt(db, principal.tenant_id, prompt_id)
        if source is None:
            raise NotFoundError("Prompt 版本不存在")

        versions = ai_dao.list_prompt_versions(db, principal.tenant_id, source.prompt_code)
        by_version = {int(v.version): v for v in versions}
        target = by_version.get(int(payload.target_version))
        if target is None:
            raise NotFoundError(
                f"Prompt {source.prompt_code!r} 不存在版本 v{payload.target_version}",
                {
                    "prompt_code": source.prompt_code,
                    "available_versions": sorted(by_version),
                },
            )

        latest = versions[-1] if versions else source
        if target.content == latest.content:
            raise IllegalStateError(
                f"目标版本 v{target.version} 与最新版本 v{latest.version} 内容一致，无需回滚",
                {"target_version": int(target.version), "latest_version": int(latest.version)},
            )

        row = PromptTemplate(
            tenant_id=principal.tenant_id,
            prompt_code=source.prompt_code,
            scene=target.scene,
            version=int(latest.version) + 1,
            content=target.content,
            # 回滚的语义就是「让它重新生效」，故状态固定为启用
            # （不做成可选项：一个「回滚了但仍未生效」的结果对调用方毫无意义）
            status=int(PromptStatus.ENABLED),
            created_by=principal.user_id,
        )
        ai_dao.insert_prompt(db, row)
        cls._enforce_single_enabled(db, principal.tenant_id, source.prompt_code, row)
        db.commit()

        AuditService.record(
            AuditAction.AI_PROMPT_ROLLBACK,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.PROMPT,
            target_id=row.id,
            before=cls._snapshot(latest, remark=None),
            after={
                **cls._snapshot(row, remark=payload.remark),
                "rollback_from_version": int(target.version),
                "replaced_version": int(latest.version),
            },
        )
        return cls.to_out(row)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _enforce_single_enabled(
        db: Session, tenant_id: int, prompt_code: str, new_row: PromptTemplate
    ) -> None:
        """保持「族内最多一个启用版本」不变式。

        ⚠️ 只在 ``new_row`` 是启用态时才停用兄弟版本；
        若新版本是草稿/停用，则**不动**任何既有版本 ——
        「保存一个草稿」不应该顺手把线上正在用的版本关掉。
        """
        if int(new_row.status) != int(PromptStatus.ENABLED):
            return
        ai_dao.set_prompt_status(
            db, tenant_id, prompt_code, int(PromptStatus.DISABLED), except_id=int(new_row.id)
        )

    @staticmethod
    def _validate_content(content: str) -> str:
        """正文校验：去首尾空白 + 长度上限。"""
        text = (content or "").strip()
        if not text:
            raise BizError(
                ErrorCode.PARAM_INVALID, "模板正文不能为空", {"fields": [{"field": "content", "message": "不能为空"}]}
            )
        if len(text) > MAX_CONTENT_CHARS:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"模板正文超长（{len(text)} > {MAX_CONTENT_CHARS} 字符）",
                {
                    "fields": [
                        {"field": "content", "message": f"不得超过 {MAX_CONTENT_CHARS} 字符"}
                    ],
                    "max_chars": MAX_CONTENT_CHARS,
                    "actual_chars": len(text),
                },
            )
        return text

    @staticmethod
    def _snapshot(row: PromptTemplate, *, remark: str | None) -> dict[str, Any]:
        """审计快照。

        ⚠️ **正文截断到 500 字符**：审计表是 ``MEDIUMTEXT`` 装得下，
        但审计列表要渲染、要导出 CSV；把整篇 Prompt 塞进每条审计记录，
        会让「查一次审计」变成下载几 MB 文本。需要全文时按 ``target_id``
        回查 ``prompt_template``（历史版本永不删，所以**一定查得到**——
        这正是「只追加」带来的好处）。
        """
        content = str(row.content or "")
        return {
            "prompt_code": row.prompt_code,
            "scene": row.scene,
            "scene_name": PromptScene.LABELS.get(row.scene),
            "version": int(row.version),
            "status": int(row.status),
            "status_name": PromptStatus.name_of(int(row.status)),
            "content_chars": len(content),
            "content_excerpt": content if len(content) <= 500 else content[:500] + "…",
            "remark": remark,
        }

    @classmethod
    def to_out(cls, row: PromptTemplate) -> PromptOut:
        return PromptOut(
            id=row.id,
            prompt_code=row.prompt_code,
            scene=row.scene,
            scene_name=PromptScene.LABELS.get(row.scene),
            version=int(row.version),
            content=row.content,
            status=int(row.status),
            status_name=PromptStatus.name_of(int(row.status)),
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
