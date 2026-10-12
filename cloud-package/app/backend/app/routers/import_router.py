"""简历导入路由（CU-14，API-36~43，模块 G「导入」，F-3.1 / F-3.8）。

权限矩阵（接口 §4）：API-36~43 全部 **ADM / HR**（``import:*`` 权限点，
种子已授予 HR；ADM 走超管短路）。

🔴 API-39 是本模块唯一的 ``async def`` 端点（统一同步驱动的既定例外）：
SSE 长连接不能占住线程池线程，轮询查询经 ``run_in_threadpool`` 下沉。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_current_principal, require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.exceptions import ERROR_META, BizError, ErrorCode, validation_fields
from app.schemas.common import CommonResp, ok, paginated
from app.schemas.talent import ImportCreateForm, ImportCreateOut, ImportTaskOut
from app.services.import_service import ImportService

logger = get_logger(__name__)

router = APIRouter(tags=["G · 简历导入 (F-3.1/F-3.8)"])

DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]


def _param_invalid(fields: list[dict[str, str]]) -> BizError:
    return BizError(
        ErrorCode.PARAM_INVALID, ERROR_META[ErrorCode.PARAM_INVALID].message, {"fields": fields}
    )


# ============================================================================
# API-36 创建导入任务（multipart，强制幂等）
# ============================================================================
@router.post("/talent/import", response_model=CommonResp[ImportCreateOut])
async def create_import_task(
    db: DbDep,
    principal: PrincipalDep,
    files: Annotated[list[UploadFile], File(description="简历文件（≤100 份，zip 自动解包）")],
    auto_dedup: Annotated[bool, Form()] = True,
    auto_tag: Annotated[bool, Form()] = True,
    parse_strategy: Annotated[str, Form()] = "auto",
    remark: Annotated[str | None, Form(max_length=200)] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CommonResp[ImportCreateOut]:
    """创建导入任务（解析异步执行，立即返回；进度走 API-39 SSE / API-38 轮询）。"""
    try:
        form = ImportCreateForm(
            auto_dedup=auto_dedup,
            auto_tag=auto_tag,
            parse_strategy=parse_strategy,  # type: ignore[arg-type]
            remark=remark,
        )
    except ValidationError as exc:
        raise _param_invalid(validation_fields(exc.errors())) from exc

    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        uploads.append((upload.filename or "unnamed", content))

    # 🔴 校验/建任务/提交是同步 DB 操作（六层既定同步驱动）——经 run_in_threadpool 下沉，
    #    不让 multipart 处理把事件循环占住（async 只用于读取上传字节流）。
    from starlette.concurrency import run_in_threadpool

    out = await run_in_threadpool(
        ImportService.create_task,
        db,
        principal,
        idempotent_key=idempotency_key,
        form=form,
        uploads=uploads,
    )
    return ok(out)


# ============================================================================
# API-37 导入任务列表
# ============================================================================
@router.get("/talent/import", response_model=CommonResp[list[ImportTaskOut]])
def list_import_tasks(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:view"))],
    status: Annotated[int | None, Query(ge=0, le=4)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommonResp[list[ImportTaskOut]]:
    rows, total, current, size = ImportService.list_tasks(
        db, principal, status=status, page=page, per_page=per_page
    )
    return paginated(rows, total, page=current, per_page=size)


# ============================================================================
# API-38 任务详情
# ============================================================================
@router.get("/talent/import/{task_id}", response_model=CommonResp[ImportTaskOut])
def get_import_task(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:view"))],
) -> CommonResp[ImportTaskOut]:
    return ok(ImportService.get_task_detail(db, principal, task_id))


# ============================================================================
# API-39 SSE 实时进度流
# ============================================================================
@router.get("/talent/import/{task_id}/progress")
async def stream_import_progress(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:view"))],
    interval: Annotated[float, Query(ge=0.5, le=10)] = 1.0,
) -> StreamingResponse:
    """SSE 进度流：``event: progress``（进行中）/ ``done``（终态）/ ``error``（任务不存在）。

    🔗 事件负载与 ``ImportProgressEvent`` 一致；终态后服务端主动关闭流
    （前端无需额外心跳逻辑）。
    """
    from starlette.concurrency import run_in_threadpool

    # 校验（任务存在性）放流外：404 必须在握手期返回，不能藏在事件里
    ImportService.get_task_detail(db, principal, task_id)

    async def _gen():
        from app.dao.session import SessionLocal

        yield ": connected\n\n"
        while True:
            session = SessionLocal()
            try:
                snapshot = await run_in_threadpool(
                    ImportService.progress_snapshot, session, principal, task_id
                )
            except Exception:  # noqa: BLE001 — 读取失败发 error 事件并退出（不给前端挂死流）
                yield "event: error\ndata: {\"message\": \"进度读取失败\"}\n\n"
                return
            finally:
                session.close()
            payload = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
            terminal = int(snapshot.status) in {2, 3, 4}
            event = "done" if terminal else "progress"
            yield f"event: {event}\ndata: {payload}\n\n"
            if terminal:
                return
            await asyncio.sleep(interval)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# API-40 文件明细
# ============================================================================
@router.get("/talent/import/{task_id}/files")
def list_import_files(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:view"))],
    parse_status: Annotated[int | None, Query(ge=0, le=2)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CommonResp:
    from app.schemas.talent import ImportFileOut

    rows, total, current, size = ImportService.list_files(
        db, principal, task_id, parse_status=parse_status, page=page, per_page=per_page
    )
    return paginated(rows, total, page=current, per_page=size)


# ============================================================================
# API-41 数据质量报告
# ============================================================================
@router.get("/talent/import/{task_id}/report")
def get_import_report(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:report"))],
) -> CommonResp:
    from app.schemas.talent import ImportReportOut

    return ok(ImportService.build_report(db, principal, task_id))


# ============================================================================
# API-42 导出质量报告
# ============================================================================
@router.get("/talent/import/{task_id}/report/export")
def export_import_report(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:report"))],
) -> CommonResp:
    from app.schemas.talent import ImportReportExportOut

    return ok(ImportService.export_report(db, principal, task_id))


# ============================================================================
# API-43 失败文件批量重试
# ============================================================================
@router.post("/talent/import/{task_id}/retry")
def retry_import_task(
    task_id: Annotated[int, Path()],
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("import:retry"))],
) -> CommonResp:
    from app.schemas.talent import ImportRetryOut

    return ok(ImportService.retry_failed(db, principal, task_id))
