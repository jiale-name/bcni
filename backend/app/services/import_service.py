"""简历批量导入与任务进度（CU-14，API-36~43，F-3.1 / F-3.8）。

🔴 与接口 §4.1 的三条硬约束
===========================
1. **单批 ≤100 份**（含 zip 解包后的份数）：超限 **40002** 并给拆分建议；
   恰好 100 份返回 ``warn`` 提示 —— 「提示」与「拒绝」是两个动作，混了就是 bug。
2. **单文件 ≤50MB**：超限 **41301**，``data.oversize_files`` 逐个列出
   （只报第一个会让用户改一个错一个，来回上传 N 次）。
3. **强制幂等**（``Idempotency-Key`` 请求头）：重复提交返回**首次任务** + ``40903``；
   幂等键落在 ``import_task.idempotent_key``（``uk_tenant_idem`` 库级兜底）。

🔴 解析是**异步**的（接口 §4.1：任务创建后立即返回）
====================================================
后台线程逐文件处理：每文件一个 Agent 任务（``task_type=parse``），一个文件一个
数据库事务 —— **单文件失败不阻塞整批**（F-3.1，SM-2 的 ``部分失败`` 与 ``失败`` 平级）。
进度由前端通过 API-39 SSE 或 API-38 轮询取得（计数以逐文件状态聚合为准）。

⚠️ MinIO（DS-4）本机未就绪（N0 实测）：原始文件**降级本地落盘**
（``UPLOAD_STORAGE_DIR``），降级只打 WARNING 不报错（CR-7：环境缺件不伪装成 500）。
MinIO 接入后本模块只改 ``_store_file`` 一处。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agents.registry import KEY_EXTRACTOR, KEY_NORMALIZER, KEY_PERSIST
from app.config import settings
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import agent_dao, import_dao, talent_dao
from app.exceptions import BizError, ErrorCode, NotFoundError
from app.models.enums import (
    ImportFileParseStatus,
    ImportTaskStatus,
    ParseReviewStatus,
)
from app.models.talent import ImportFile, ImportTask
from app.schemas.audit import AuditAction, AuditTarget
from app.schemas.common import normalize_per_page
from app.schemas.talent import (
    SUPPORTED_EXTENSIONS,
    FieldCompletenessOut,
    FailureReasonOut,
    ImportCreateForm,
    ImportCreateOut,
    ImportFileOut,
    ImportProgressEvent,
    ImportReportExportOut,
    ImportReportOut,
    ImportRetryOut,
    ImportTaskOut,
    parse_status_name_of,
    status_name_of,
)
from app.services.agent_service import AgentService
from app.services.audit_service import AuditService
from app.services.parse_pipeline import (
    PersistRejected,
    build_parser_state,
    make_normalizer,
    make_persist_callback,
    rule_extract,
)

logger = get_logger(__name__)

#: zip 内条目的大小上限（防 zip bomb：解包前校验中央目录声明的未压缩大小）
_ZIP_ENTRY_LIMIT = settings.upload_max_file_size_bytes

#: 失败原因码 → 展示名（接口 §4.1：新增原因码须同步前端常量表，此处是唯一事实）
FAILURE_REASON_LABELS: dict[str, str] = {
    "OCR_FAILED": "扫描件 OCR 识别失败",
    "ENCRYPTED_FILE": "文件加密无法读取",
    "FORMAT_UNSUPPORTED": "格式不支持",
    "EXTRACTOR_MISSING": "解析器未就绪（待 OCR 选型）",
    "DUPLICATE_HASH": "文件内容重复",
    "STORAGE_FAILED": "文件存储失败",
    "PARSE_INTERRUPTED": "解析被人工驳回或异常终止",
    # 批次 21 新增：不是「解析失败」，而是**待人工补录**（姓名是建档唯一必需项）
    "NAME_MISSING": "未抽取到姓名，待人工补录",
    "UNKNOWN": "未知错误",
}


class ImportService:
    """导入任务（API-36~43）。"""

    # ------------------------------------------------------------------
    # API-36 创建导入任务
    # ------------------------------------------------------------------
    @classmethod
    def create_task(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        idempotent_key: str | None,
        form: ImportCreateForm,
        uploads: list[tuple[str, bytes]],
        run_async: bool = True,
    ) -> ImportCreateOut:
        """创建导入任务（异步解析，立即返回）。

        :param uploads: ``[(文件名, 字节内容), ...]`` —— zip 由本方法解包
        :param run_async: ``False`` 时**同步**处理（测试与本地调试用），
            生产行为恒为异步（由 Router 层固定传 ``True``）。
        """
        # ---- 幂等回放（先于一切校验：重复提交不该因文件再次校验而报 41301）----
        if not idempotent_key or not idempotent_key.strip():
            raise BizError(ErrorCode.PARAM_INVALID, "缺少 Idempotency-Key 请求头（API-36 强制幂等）", {"field": "Idempotency-Key"})
        key = idempotent_key.strip()[:64]
        replay = import_dao.find_task_by_idempotent_key(db, principal.tenant_id, key)
        if replay is not None:
            raise BizError(
                ErrorCode.IDEMPOTENT_REPLAY,
                "导入任务已创建，返回首次结果",
                {"task_id": str(replay.id), "task_no": replay.task_no},
            )

        # ---- 解包 zip（解包后的总份数仍受 ≤100 限制）----
        files: list[tuple[str, bytes]] = []
        for name, content in uploads:
            if name.lower().endswith(".zip"):
                files.extend(cls._unpack_zip(name, content))
            else:
                files.append((name, content))
        if not files:
            raise BizError(ErrorCode.PARAM_INVALID, "未收到任何简历文件", {"field": "files"})

        # ---- 校验：单批 ≤100 / 单文件 ≤50MB / 扩展名 ----
        limit = settings.UPLOAD_MAX_FILES_PER_BATCH
        if len(files) > limit:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                f"单批最多 {limit} 份简历，当前 {len(files)} 份，请拆分提交",
                {"received": len(files), "limit": limit, "suggestion": "按部门或日期拆分为多个批次"},
            )
        oversize = [
            {"name": n, "size_mb": round(len(c) / 1024 / 1024, 2)}
            for n, c in files
            if len(c) > settings.upload_max_file_size_bytes
        ]
        if oversize:
            raise BizError(
                ErrorCode.UPLOAD_TOO_LARGE,
                f"存在超过 {settings.UPLOAD_MAX_FILE_SIZE_MB}MB 的文件，请压缩后重试",
                {"oversize_files": oversize},
            )
        unsupported = sorted(
            {
                n.rsplit(".", 1)[-1].lower()
                for n, _c in files
                if "." not in n or n.rsplit(".", 1)[-1].lower() not in SUPPORTED_EXTENSIONS
            }
        )
        if unsupported:
            raise BizError(
                ErrorCode.UNSUPPORTED_MEDIA,
                f"文件格式不支持：{', '.join(unsupported)}（支持 pdf/doc/docx/jpg/png/zip）",
                {"unsupported": unsupported},
            )

        # ---- 批内去重（同内容文件在本任务内唯一，uk_tenant_task_hash 兜底）----
        seen_hashes: dict[str, str] = {}
        duplicates: list[str] = []
        for name, content in files:
            digest = hashlib.sha256(content).hexdigest()
            if digest in seen_hashes:
                duplicates.append(name)
            else:
                seen_hashes[digest] = name
        if duplicates:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                "本批内存在内容重复的文件",
                {"duplicate_files": duplicates[:20]},
            )

        warn = None
        if len(files) == limit:
            warn = f"本批 {limit} 份已达单批上限，如需更多请分批提交"

        # ---- 建任务 + 文件明细 ----
        day_prefix = datetime.now().strftime("%Y%m%d")
        task_no = import_dao.next_task_no(db, principal.tenant_id, day_prefix=day_prefix)
        task = import_dao.insert_task(
            db,
            ImportTask(
                tenant_id=principal.tenant_id,
                task_no=task_no,
                source=0,
                parse_strategy=form.parse_strategy,
                auto_dedup=1 if form.auto_dedup else 0,
                auto_tag=1 if form.auto_tag else 0,
                remark=form.remark,
                total_count=len(files),
                status=int(ImportTaskStatus.PENDING),
                idempotent_key=key,
                created_by=principal.user_id,
            ),
        )
        for resume_id, (name, content) in enumerate(files, start=1):
            storage_path = cls._store_file(principal.tenant_id, task_no, resume_id, name, content)
            import_dao.insert_file(
                db,
                ImportFile(
                    tenant_id=principal.tenant_id,
                    task_id=task.id,
                    resume_id=resume_id,
                    file_name=name[:255],
                    file_ext=(name.rsplit(".", 1)[-1].lower() if "." in name else None),
                    file_size=len(content),
                    file_hash=hashlib.sha256(content).hexdigest(),
                    storage_path=storage_path,
                ),
            )
        db.commit()

        AuditService.record(
            AuditAction.IMPORT_CREATE,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.IMPORT_TASK,
            target_id=task.id,
            after={
                "task_no": task_no,
                "total_count": len(files),
                "parse_strategy": form.parse_strategy,
                "auto_dedup": form.auto_dedup,
                "auto_tag": form.auto_tag,
            },
        )

        if run_async:
            cls._spawn(task.id, principal.tenant_id, principal.user_id)
        else:
            cls.process_task(task.id, principal.tenant_id, principal.user_id)

        return ImportCreateOut(
            task_id=task.id,
            task_no=task_no,
            status=int(task.status),
            status_name=status_name_of(int(task.status)),
            total_count=len(files),
            warn=warn,
        )

    # ------------------------------------------------------------------
    # API-37 任务列表 / API-38 详情
    # ------------------------------------------------------------------
    @classmethod
    def list_tasks(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        *,
        status: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ImportTaskOut], int, int, int]:
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = import_dao.list_tasks(
            db, principal.tenant_id, status=status, limit=size, offset=(current - 1) * size
        )
        total = import_dao.count_tasks(db, principal.tenant_id, status=status)
        creator_names = cls._creator_names(db, principal.tenant_id, rows)
        return (
            [cls.to_task_out(db, principal.tenant_id, r, creator_names) for r in rows],
            total,
            current,
            size,
        )

    @classmethod
    def get_task_detail(
        cls, db: Session, principal: CurrentPrincipal, task_id: int
    ) -> ImportTaskOut:
        task = cls._get_task(db, principal, task_id)
        names = cls._creator_names(db, principal.tenant_id, [task])
        return cls.to_task_out(db, principal.tenant_id, task, names)

    # ------------------------------------------------------------------
    # API-39 SSE 进度快照
    # ------------------------------------------------------------------
    @classmethod
    def progress_snapshot(
        cls, db: Session, principal: CurrentPrincipal, task_id: int
    ) -> ImportProgressEvent:
        task = cls._get_task(db, principal, task_id)
        counts = import_dao.count_by_status(db, principal.tenant_id, task_id)
        done = counts.get(int(ImportFileParseStatus.SUCCESS), 0) + counts.get(
            int(ImportFileParseStatus.FAILED), 0
        )
        total = int(task.total_count) or import_dao.count_files(db, principal.tenant_id, task_id)
        pct = int(done * 100 / total) if total else 0
        return ImportProgressEvent(
            task_id=str(task.id),
            status=int(task.status),
            status_name=status_name_of(int(task.status)),
            done=done,
            total=total,
            progress_pct=min(100, pct),
        )

    # ------------------------------------------------------------------
    # API-40 文件明细
    # ------------------------------------------------------------------
    @classmethod
    def list_files(
        cls,
        db: Session,
        principal: CurrentPrincipal,
        task_id: int,
        *,
        parse_status: int | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[ImportFileOut], int, int, int]:
        cls._get_task(db, principal, task_id)  # 先验任务可见性（防跨租户枚举文件）
        size = normalize_per_page(per_page)
        current = max(1, int(page))
        rows = import_dao.list_files(
            db,
            principal.tenant_id,
            task_id,
            parse_status=parse_status,
            limit=size,
            offset=(current - 1) * size,
        )
        total = import_dao.count_files(db, principal.tenant_id, task_id)
        return (
            [
                ImportFileOut(
                    file_id=r.id,
                    resume_id=int(r.resume_id),
                    file_name=r.file_name,
                    file_ext=r.file_ext,
                    file_size=int(r.file_size),
                    file_hash=r.file_hash,
                    parse_status=int(r.parse_status),
                    parse_status_name=parse_status_name_of(int(r.parse_status)),
                    parse_error=r.parse_error,
                    talent_id=r.talent_id,
                    agent_task_id=r.agent_task_id,
                    ocr_used=bool(r.ocr_used),
                    created_at=r.created_at,
                )
                for r in rows
            ],
            total,
            current,
            size,
        )

    # ------------------------------------------------------------------
    # API-41 质量报告 / API-42 导出
    # ------------------------------------------------------------------
    @classmethod
    def build_report(cls, db: Session, principal: CurrentPrincipal, task_id: int) -> ImportReportOut:
        task = cls._get_task(db, principal, task_id)
        if int(task.status) not in {
            int(ImportTaskStatus.COMPLETED),
            int(ImportTaskStatus.PARTIAL_FAILED),
        }:
            raise BizError(
                ErrorCode.ILLEGAL_STATE,
                "任务尚未完成，暂无法生成质量报告",
                {
                    "status": int(task.status),
                    "status_name": status_name_of(int(task.status)),
                },
            )
        counts = import_dao.count_by_status(db, principal.tenant_id, task_id)
        success = counts.get(int(ImportFileParseStatus.SUCCESS), 0)
        fail = counts.get(int(ImportFileParseStatus.FAILED), 0)
        field_rows = talent_dao.list_field_completeness(db, principal.tenant_id, task_id=task_id)
        field_status = talent_dao.count_fields_by_status(db, principal.tenant_id, task_id=task_id)
        review_pending = field_status.get(int(ParseReviewStatus.PENDING), 0)
        field_names = talent_dao.list_talents_by_task(db, principal.tenant_id, task_id)
        completeness = [
            FieldCompletenessOut(
                field_code=code,
                field_name=code,  # 展示名由前端字典渲染；字段中文名在 parse_pipeline.FIELD_LABELS
                filled=filled,
                total=total,
                rate=round(filled / total, 3) if total else 0.0,
            )
            for code, filled, total in field_rows
        ]
        return ImportReportOut(
            task_id=task.id,
            task_no=task.task_no,
            summary={
                "total_count": int(task.total_count),
                "success_count": success,
                "fail_count": fail,
                "success_rate": round(success / int(task.total_count), 4) if int(task.total_count) else 0.0,
                "field_complete_rate": cls._complete_rate(field_rows),
                "talent_count": len(field_names),
                "review_pending_count": review_pending,
            },
            field_completeness=completeness,
            failure_reasons=[
                FailureReasonOut(
                    reason_code=code,
                    reason_name=FAILURE_REASON_LABELS.get(code, FAILURE_REASON_LABELS["UNKNOWN"]),
                    count=count,
                )
                for code, count in import_dao.list_failure_reasons(db, principal.tenant_id, task_id)
            ],
            accuracy_note="字段级准确率需由金标准集（≥200 份）离线评估得出（IDX-02），不在本报告内",
            generated_at=datetime.now(),
        )

    @classmethod
    def export_report(cls, db: Session, principal: CurrentPrincipal, task_id: int) -> ImportReportExportOut:
        report = cls.build_report(db, principal, task_id)
        lines = ["section,item,value"]
        for key, value in report.summary.items():
            lines.append(f"summary,{key},{value}")
        for row in report.field_completeness:
            lines.append(f"completeness,{row.field_code},{row.rate}")
        for row in report.failure_reasons:
            lines.append(f"failure,{row.reason_code},{row.count}")
        content = "\r\n".join(lines) + "\r\n"
        from app.services.audit_service import AuditService as _AS

        _AS.record(
            AuditAction.AUDIT_EXPORT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.IMPORT_TASK,
            target_id=task_id,
            after={"kind": "import_quality_report", "task_no": report.task_no},
        )
        return ImportReportExportOut(
            filename=f"{report.task_no}_quality_report.csv",
            content="\ufeff" + content,  # BOM：Excel 双击不乱码（与 API-29 同口径）
            row_count=len(lines) - 1,
            generated_at=report.generated_at,
        )

    # ------------------------------------------------------------------
    # API-43 失败重试
    # ------------------------------------------------------------------
    @classmethod
    def retry_failed(
        cls, db: Session, principal: CurrentPrincipal, task_id: int, *, run_async: bool = True
    ) -> ImportRetryOut:
        task = cls._get_task(db, principal, task_id)
        failed = import_dao.list_failed_files(db, principal.tenant_id, task_id)
        if not failed:
            raise BizError(ErrorCode.ILLEGAL_STATE, "没有可重试的失败文件", {"task_id": str(task_id)})
        if int(task.status) == int(ImportTaskStatus.PARSING):
            raise BizError(ErrorCode.ILLEGAL_STATE, "任务正在解析中，不能重试", {"status": int(task.status)})
        for f in failed:
            # 🔴 批次 22 修复：**「重试」必须真的重新解析**（否则重试是个假动作）
            # ==================================================================
            # 上一次解析留下的 ``agent_task`` 仍持有幂等键
            # ``parse:{task_id}:{resume_id}``；而 ``AgentService.start_task`` 命中
            # 幂等键时**直接回放首次结果、不重跑图**（见该方法 docstring 的 replayed 语义）
            # ⇒ 用户点多少次「重试」都只会把文件再标一次失败（且 ``parse_error``
            # 会被写成回放指纹 ``PARSE_FAILED``，与真实原因码不同，极具误导性）。
            #
            # ``AgentTask`` 的 docstring 已写明：**重试属「非幂等场景」，幂等键应为 NULL**。
            # 故在此把旧任务的幂等键**作废**（旧行本身保留 → 步骤回放与审计历史不丢），
            # 让本次重试新建一个 agent_task、真正重跑解析图。
            if f.agent_task_id:
                agent_dao.update_task(
                    db, principal.tenant_id, int(f.agent_task_id), idempotent_key=None
                )
            f.parse_status = int(ImportFileParseStatus.PENDING)
            f.parse_error = None
            f.talent_id = None
            f.agent_task_id = None
        import_dao.set_task_status(db, task, int(ImportTaskStatus.PENDING))
        db.commit()
        AuditService.record(
            AuditAction.IMPORT_RETRY,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.IMPORT_TASK,
            target_id=task_id,
            after={"retry_count": len(failed)},
        )
        if run_async:
            cls._spawn(task_id, principal.tenant_id, principal.user_id, only_pending=True)
        else:
            cls.process_task(task_id, principal.tenant_id, principal.user_id, only_pending=True)
        return ImportRetryOut(
            task_id=task.id,
            retry_count=len(failed) + 1,
            retryable_count=len(failed),
            message=None,
        )

    # ==================================================================
    # 解析流水线（后台 / 同步共用）
    # ==================================================================
    @classmethod
    def process_task(
        cls, task_id: int, tenant_id: int, user_id: int, *, only_pending: bool = False
    ) -> dict[str, int]:
        """逐文件解析（**每文件独立 Session + 独立事务**：单文件失败不阻塞整批）。

        线程入口与测试入口共用本方法：``process_task`` 内部**自建 Session**，
        不复用 HTTP 请求的会话（后台线程没有请求作用域）。
        """
        from app.dao.session import SessionLocal

        with SessionLocal() as db:
            task = import_dao.get_task(db, tenant_id, task_id)
            if task is None:
                logger.error("导入任务不存在 task_id=%s", task_id)
                return {"success": 0, "failed": 0}
            import_dao.set_task_status(db, task, int(ImportTaskStatus.PARSING))
            task.started_at = task.started_at or datetime.now()
            db.commit()

            pending = int(ImportFileParseStatus.PENDING)
            files = import_dao.list_files(
                db, tenant_id, task_id, parse_status=pending if only_pending else None, limit=10_000
            )
            if only_pending:
                # 🔴 批次 24：PENDING 文件重入前**必须作废旧幂等键**，否则重入是假动作
                # ==================================================================
                # 重入场景（启动自愈 / 重试）里，PENDING 文件可能仍挂着上一次解析的
                # ``agent_task_id``，而那个 agent_task 仍持有幂等键
                # ``parse:{task_id}:{resume_id}`` ⇒ ``AgentService.start_task`` 命中键
                # **回放首次结果、不重跑图**（replayed=True）⇒ 文件行停留在 PENDING，
                # 终态计数读到 0/0，任务却被判 COMPLETED —— 「已完成 / 0 / 0」以
                # 另一条路径复发（新用例
                # ``test_startup_resume_finds_zombie_parsing_task_and_closes_it`` 钉死）。
                # 与 ``retry_failed`` 同一口径：**幂等键是「同一请求的防火墙」，
                # 重入是「新请求」** —— 旧行保留（审计不丢），只作废键。
                for f in files:
                    if f.agent_task_id:
                        agent_dao.update_task(
                            db, tenant_id, int(f.agent_task_id), idempotent_key=None
                        )
                db.commit()
            success = failed = 0
            for file_row in files:
                ok = cls._process_one_file(db, tenant_id, user_id, task, file_row)
                success += 1 if ok else 0
                failed += 0 if ok else 1

            # 🔴 C 修复（批次 20）：主会话在 REPEATABLE-READ 下持有「循环前」的快照，
            # 直接 ``count_by_status(db)`` 会读到过期的 PENDING（各文件已在各自 file_db 会话提交），
            # 导致 final_fail==0 → 任务被错判为 COMPLETED（「已完成 / 0 / 3」的根因）。
            # 改用**全新会话**读取已提交的真实状态（重试场景 only_pending=True 也正确：
            # 之前已成功的文件仍记为 SUCCESS，本轮重处理的结果也已提交）。
            with SessionLocal() as count_db:
                counts = import_dao.count_by_status(count_db, tenant_id, task_id)
            final_success = counts.get(int(ImportFileParseStatus.SUCCESS), 0)
            final_fail = counts.get(int(ImportFileParseStatus.FAILED), 0)
            if final_fail == 0:
                final = ImportTaskStatus.COMPLETED
            elif final_success > 0:
                final = ImportTaskStatus.PARTIAL_FAILED
            else:
                final = ImportTaskStatus.FAILED
            task.success_count = final_success
            task.fail_count = final_fail
            task.status = int(final)
            task.finished_at = datetime.now()
            db.commit()
            logger.info(
                "导入任务完成 %s success=%s fail=%s", task.task_no, final_success, final_fail
            )
            # ---- 查重（F-3.5，CU-19 消费 auto_dedup）：解析完成后扫描精确键重复 ----
            # 🔴 fail-open：查重是旁路增强，失败**不得**阻断导入完成（导入已完成 commit）。
            if int(task.auto_dedup) == 1:
                try:
                    from app.services.dedup_service import DedupService

                    created = DedupService.scan_exact_duplicates(
                        db,
                        CurrentPrincipal(
                            user_id=user_id or 0, tenant_id=tenant_id, org_id=None, roles=("HR",)
                        ),
                    )
                    logger.info("导入任务 %s 精确键查重：新增 %s 个候选对", task.task_no, created)
                except Exception as exc:  # noqa: BLE001 — 查重失败只告警，不回滚导入
                    logger.warning("导入任务 %s 查重扫描失败（不阻断导入）：%s", task.task_no, exc)
            return {"success": final_success, "failed": final_fail}

    @classmethod
    def _process_one_file(
        cls, db: Session, tenant_id: int, user_id: int, task: ImportTask, file_row: ImportFile
    ) -> bool:
        """单文件解析：独立事务提交；异常 → ``parse_status=2``（不阻塞整批）。

        🔴 批次 21 修复（真机缺陷：2026-09-24 IMP20260924001~003「已完成 / 0 / 0」）
        =================================================================
        ``file_row`` 是 ``process_task`` **主会话**加载的实例。旧实现把它直接交给
        ``mark_file(file_db, file_row, ...)`` —— 改的是主会话的对象，
        ``file_db.commit()`` 提交的是一个**空事务**：

        1. 逐文件状态**没有**在本文件的会话里提交（与本文档「独立事务」的说法不符）；
        2. ``process_task`` 随后用全新会话读「已提交状态」→ 仍是 ``PENDING``
           ⇒ ``final_fail == 0`` ⇒ 任务被判 ``COMPLETED`` 且 ``success=fail=0``；
        3. 文件状态直到主会话 ``db.commit()``（**在计数之后**）才落库。

        于是前端出现「已完成 / 0 / 0」或「解析中 0%」，而文件明细里写着「失败」；
        ``success_count`` 在成功路径上同样是 0（成功导入也看不到成功数）。

        修法：**在本文件的会话里重新取一次行对象**（``get_file(file_db, ...)``），
        标记与入库回调全部作用在它身上 —— 这才是 docstring 一直宣称的
        「每文件独立事务」。``db`` 参数仅用于主循环传参，**不再对它写任何东西**。
        """
        import os

        from app.dao.session import SessionLocal

        # Agent 任务与解析图需要独立 Session（单文件事务边界）
        file_db = SessionLocal()
        try:
            row = import_dao.get_file(file_db, tenant_id, int(file_row.id))
            if row is None:
                logger.error("导入文件行不存在 file_id=%s", file_row.id)
                return False
            # 🔴 排障入口：先确认「文件到底在不在磁盘上」——
            #    文本层抽取的第一条 WARNING（text_source_missing）就是这里决定的
            logger.info(
                "导入文件开始 task=%s file_id=%s resume=%s file=%s ext=%s storage=%s 磁盘存在=%s",
                task.task_no,
                row.id,
                row.resume_id,
                row.file_name,
                row.file_ext,
                row.storage_path,
                bool(row.storage_path) and os.path.isfile(row.storage_path),
            )
            state = build_parser_state(
                resume_id=int(row.resume_id),
                file_name=row.file_name,
                file_path=row.storage_path or row.file_name,
                ocr_used=bool(row.file_ext in {"png", "jpg", "jpeg", "pdf"}),
            )
            state["__tenant_id__"] = tenant_id
            state[KEY_EXTRACTOR] = rule_extract
            state[KEY_NORMALIZER] = make_normalizer(file_db, tenant_id)
            state[KEY_PERSIST] = make_persist_callback(
                file_db,
                tenant_id,
                user_id,
                task_id=task.id,
                file_row=row,  # 🔴 必须是本会话的行对象（幂等锚 talent_id 也落在它上面）
                source_file=row.file_name,
            )
            # 复核判定所需字段由图内 score_confidence 节点计算（确定性 ThresholdEngine）
            agent_task, outcome, _replayed = AgentService.start_task(
                file_db,
                CurrentPrincipal(user_id=user_id or 0, tenant_id=tenant_id, org_id=None, roles=("HR",)),
                task_type="parse",
                state=state,
                biz_id=int(row.id),
                idempotent_key=f"parse:{task.id}:{row.resume_id}",
            )
            row.agent_task_id = agent_task.id
            logger.info(
                "导入文件图执行结束 task=%s file=%s agent_task=%s status=%s next=%s error=%s",
                task.task_no,
                row.file_name,
                agent_task.id,
                outcome.status,
                outcome.next_node,
                outcome.error,
            )

            if outcome.status == "failed":
                logger.error(
                    "导入文件失败 task=%s file=%s error_code=%s error=%s",
                    task.task_no,
                    row.file_name,
                    outcome.error_code,
                    outcome.error,
                )
                import_dao.mark_file(
                    file_db, row,
                    parse_status=int(ImportFileParseStatus.FAILED),
                    parse_error=(outcome.error or "PARSE_FAILED")[:512],
                )
                file_db.commit()
                return False

            if outcome.status == "waiting_human":
                # 文件级人工介入（整份无字段/姓名缺失/整体过低）→ 待办已建，文件标记「待人工」
                reason_code = cls._review_reason_code(outcome)
                logger.warning(
                    "导入文件待人工 task=%s file=%s 原因码=%s 复核原因=%s",
                    task.task_no,
                    row.file_name,
                    reason_code,
                    (outcome.state or {}).get("review", {}).get("reasons"),
                )
                import_dao.mark_file(
                    file_db, row,
                    parse_status=int(ImportFileParseStatus.FAILED),
                    parse_error=reason_code,
                )
                file_db.commit()
                return False

            logger.info(
                "导入文件成功 task=%s file=%s talent_id=%s",
                task.task_no,
                row.file_name,
                (outcome.state or {}).get("talent_id"),
            )
            file_db.commit()
            return True
        except PersistRejected as exc:
            # 入库规则驳回（无名不入库等）：这是**业务结论**，不是程序错误 ——
            # 落**可读原因码**（而不是一行 Python 异常文本）
            file_db.rollback()
            logger.warning(
                "文件解析被入库规则驳回 file=%s reason=%s", file_row.file_name, exc.reason_code
            )
            cls._mark_failed(tenant_id, int(file_row.id), exc.reason_code, session_factory=SessionLocal)
            return False
        except Exception as exc:  # noqa: BLE001 — 单文件失败必须兜住，不阻塞整批
            file_db.rollback()
            logger.error("文件解析失败 file=%s: %s", file_row.file_name, exc)
            cls._mark_failed(
                tenant_id,
                int(file_row.id),
                f"{type(exc).__name__}: {exc}",
                session_factory=SessionLocal,
            )
            return False
        finally:
            file_db.close()

    @staticmethod
    def _review_reason_code(outcome: Any) -> str:
        """文件级中断的原因码。

        ``name_missing`` → ``NAME_MISSING``（「待人工补录」，**不是**解析被驳回）；
        其余（``no_fields`` / ``overall_low``）沿用 ``PARSE_INTERRUPTED``。
        """
        reasons = set((getattr(outcome, "state", {}) or {}).get("review", {}).get("reasons") or [])
        return "NAME_MISSING" if "name_missing" in reasons else "PARSE_INTERRUPTED"

    @staticmethod
    def _mark_failed(
        tenant_id: int, file_id: int, parse_error: str, *, session_factory: Any
    ) -> None:
        """异常兜底：用**全新会话**把该文件标成失败（主会话此时已不可信/已回滚）。"""
        retry_db = session_factory()
        try:
            row = import_dao.get_file(retry_db, tenant_id, file_id)
            if row is not None:
                import_dao.mark_file(
                    retry_db, row,
                    parse_status=int(ImportFileParseStatus.FAILED),
                    parse_error=parse_error[:512],
                )
                retry_db.commit()
        finally:
            retry_db.close()

    @classmethod
    def _spawn(cls, task_id: int, tenant_id: int, user_id: int, *, only_pending: bool = False) -> None:
        import threading

        thread = threading.Thread(
            target=cls.process_task,
            args=(task_id, tenant_id, user_id),
            kwargs={"only_pending": only_pending},
            name=f"import-task-{task_id}",
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # 启动自愈（批次 24）：进程死亡留下的「解析中」僵尸任务
    # ------------------------------------------------------------------
    @classmethod
    def find_interrupted_tasks(cls, *, limit: int = 100) -> list[tuple[int, int, int]]:
        """找出被进程死亡留在「解析中(1)」的任务，返回 ``(task_id, tenant_id, user_id)``。

        🔴 与 ``_spawn`` 拆开是**为了测试不碰线程**：用例直接对返回值断言、再以
        ``process_task(only_pending=True)`` 同步验证重入收敛，不启动任何后台线程。
        """
        from app.dao.session import SessionLocal

        with SessionLocal() as db:
            stale = import_dao.list_stale_parsing_tasks(db, limit=limit)
            return [(int(t.id), int(t.tenant_id), int(t.created_by or 0)) for t in stale]

    @classmethod
    def resume_interrupted(cls, *, limit: int = 100) -> int:
        """启动自愈入口（``main.py`` lifespan 延迟调用，fail-open）。

        🔴 只重处理 **PENDING** 文件（``only_pending=True``）：进程死亡时「正在解析」
        的那一份状态仍是 PENDING（逐文件独立事务，批次 21 已保证提交语义），
        已成功/已失败的文件不动；终态计数由 ``process_task`` 用全新会话重算
        （批次 20 C 修复）—— 整条链路幂等可重入，重复调用无副作用。
        """
        pairs = cls.find_interrupted_tasks(limit=limit)
        for task_id, tenant_id, user_id in pairs:
            logger.warning(
                "启动自愈：重拉「解析中」僵尸任务 task_id=%s tenant_id=%s", task_id, tenant_id
            )
            cls._spawn(task_id, tenant_id, user_id, only_pending=True)
        return len(pairs)

    # ==================================================================
    # 内部：存储 / 解包 / 装配
    # ==================================================================
    @staticmethod
    def _store_file(tenant_id: int, task_no: str, resume_id: int, name: str, content: bytes) -> str:
        """原始文件留存。MinIO 未就绪（N0 实测）→ 本地落盘（降级打 WARNING）。

        🔴 **原始文件留存**是复核对照与重解析的前提（ADR-10）——
        「解析完就丢原始文件」会让 API-45 的原文并排展示无源可查。
        """
        base = Path(settings.UPLOAD_STORAGE_DIR) / str(tenant_id) / task_no
        safe_name = f"{resume_id:04d}_{Path(name).name}"
        path = base / safe_name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return str(path)
        except OSError as exc:
            logger.warning("文件本地留存失败（解析继续，重解析将无源可查）：%s", exc)
            return ""

    @staticmethod
    def _unpack_zip(name: str, content: bytes) -> list[tuple[str, bytes]]:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                entries = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX")]
                if any(i.file_size > _ZIP_ENTRY_LIMIT for i in entries):
                    raise BizError(
                        ErrorCode.UPLOAD_TOO_LARGE,
                        f"zip 内存在超过 {settings.UPLOAD_MAX_FILE_SIZE_MB}MB 的文件",
                        {"zip": name},
                    )
                out: list[tuple[str, bytes]] = []
                for info in entries:
                    inner = info.filename.rsplit("/", 1)[-1]
                    if "." in inner and inner.rsplit(".", 1)[-1].lower() in SUPPORTED_EXTENSIONS:
                        out.append((inner, zf.read(info)))
                return out
        except zipfile.BadZipFile as exc:
            raise BizError(ErrorCode.UNSUPPORTED_MEDIA, "zip 文件损坏无法解包", {"zip": name}) from exc

    @staticmethod
    def _get_task(db: Session, principal: CurrentPrincipal, task_id: int) -> ImportTask:
        task = import_dao.get_task(db, principal.tenant_id, task_id)
        if task is None:
            # 导入任务无行级数据域（org 只在档案上）→ 跨租户 = 不存在 = 40401（同审计/Agent 口径）
            raise NotFoundError("导入任务不存在")
        return task

    @staticmethod
    def _complete_rate(field_rows: list[tuple[str, int, int]]) -> float | None:
        if not field_rows:
            return None
        filled = sum(f for _c, f, _t in field_rows)
        total = sum(t for _c, _f, t in field_rows)
        return round(filled / total, 3) if total else None

    @staticmethod
    def _creator_names(db: Session, tenant_id: int, rows: list[ImportTask]) -> dict[int, str]:
        ids = {int(r.created_by) for r in rows if r.created_by}
        if not ids:
            return {}
        from app.dao import user_admin_dao

        return user_admin_dao.list_usernames_by_ids(db, tenant_id, sorted(ids))

    @classmethod
    def to_task_out(
        cls,
        db: Session,
        tenant_id: int,
        task: ImportTask,
        creator_names: dict[int, str] | None = None,
    ) -> ImportTaskOut:
        counts = import_dao.count_by_status(db, tenant_id, int(task.id))
        success = counts.get(int(ImportFileParseStatus.SUCCESS), 0)
        fail = counts.get(int(ImportFileParseStatus.FAILED), 0)
        done = success + fail
        total = int(task.total_count) or import_dao.count_files(db, tenant_id, int(task.id))
        field_status = talent_dao.count_fields_by_status(db, tenant_id, task_id=int(task.id))
        pending = field_status.get(int(ParseReviewStatus.PENDING), 0)
        field_rows = talent_dao.list_field_completeness(db, tenant_id, task_id=int(task.id))
        return ImportTaskOut(
            task_id=task.id,
            task_no=task.task_no,
            status=int(task.status),
            status_name=status_name_of(int(task.status)),
            parse_strategy=task.parse_strategy,
            total_count=total,
            success_count=success,
            fail_count=fail,
            dup_pair_count=0,  # CU-19 落地后接入
            review_pending_count=pending,
            review_high_impact_count=pending,  # 精确拆分见 API-44 的 meta；详情页给总量
            review_low_impact_sampled_count=0,
            field_complete_rate=cls._complete_rate(field_rows),
            progress_pct=min(100, int(done * 100 / total)) if total else 0,
            agent_task_id=None,  # 逐文件 Agent 任务；反查见 API-40 明细行的 agent_task_id
            remark=task.remark,
            created_by=task.created_by,
            created_by_name=(creator_names or {}).get(int(task.created_by)) if task.created_by else None,
            started_at=task.started_at,
            finished_at=task.finished_at,
            created_at=task.created_at,
        )
