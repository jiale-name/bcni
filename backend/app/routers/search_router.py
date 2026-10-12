"""语义检索与 RAG 问答路由（CU-20 / CU-21，API-62 / API-63）。

权限矩阵（接口 §4）：
| 接口 | 权限码 | 说明 |
| --- | --- | --- |
| API-62 语义检索 | ``talent:search`` | ADM/HR/MGR（本部门，行级由数据域裁剪） |
| API-63 RAG 问答 | ``talent:ask`` | ADM/HR/MGR/EMP（本人；EMP 由 ``data_scope=3`` 行级保证） |

🔴 API-63 是本模块唯一的 ``async def`` 端点（统一同步驱动的既定例外，与 API-39 同口径）：
SSE 长连接不能占住线程池线程；问答全链经 ``run_in_threadpool`` 下沉，
**且在握手期执行完** —— 越权（40301）必须在流开始前返回，不能藏在事件里。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import require_perm
from app.core.tenant import CurrentPrincipal
from app.dao.session import get_db
from app.schemas.common import CommonResp, ok
from app.schemas.search import AskRequest, AskResultOut, SearchOut, SearchRequest
from app.services.ask_service import AskService
from app.services.search_service import SearchService

router = APIRouter(tags=["K · 检索与问答 (F-3.6/F-3.7)"])

DbDep = Annotated[Session, Depends(get_db)]


# ============================================================================
# API-62 语义人才检索（非流式）
# ============================================================================
@router.post("/talent/search", response_model=CommonResp[SearchOut])
def search_talents(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("talent:search"))],
    payload: SearchRequest,
) -> CommonResp[SearchOut]:
    """语义人才检索（权限前置过滤 + 命中理由）。

    🔴 权限前置在 Service 内完成：``meta.permission_filter_applied`` 恒为 ``True``；
    Milvus 不可用自动降级关键词检索（HTTP 200 + ``meta.degraded=true``）。
    """
    return ok(SearchService.search(db, principal, payload))


# ============================================================================
# API-63 档案 RAG 问答（SSE / 整包）
# ============================================================================
@router.post("/talent/ask", response_model=None)
async def ask_talents(
    db: DbDep,
    principal: Annotated[CurrentPrincipal, Depends(require_perm("talent:ask"))],
    payload: AskRequest,
) -> StreamingResponse | CommonResp[AskResultOut]:
    """档案 RAG 问答：``stream=true`` 返回 SSE（meta/delta/citation/refusal/done/error）；
    ``stream=false`` 返回整包（与各事件合并后的对象一致，便于对接与自动化测试）。"""
    from starlette.concurrency import run_in_threadpool

    if not payload.stream:
        result = await run_in_threadpool(AskService.ask, db, principal, payload)
        return ok(result)

    # 🔴 问答全链在握手期同步执行完（含权限裁剪）：40301 必须在流开始前返回
    result = await run_in_threadpool(AskService.ask, db, principal, payload)

    async def _gen():
        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield ": connected\n\n"
        yield _sse(
            "meta",
            {
                "session_id": result.session_id,
                "request_id": result.request_id,
                "model": result.model,
                "mask_applied": result.mask_applied,
                "degraded": result.degraded,
                "scope_effective": result.scope_effective.model_dump(mode="json"),
            },
        )
        if result.refusal is not None:
            yield _sse("refusal", result.refusal.model_dump(mode="json"))
        elif result.answer is not None:
            # 整包拆片（网关 generate 为非流式；token 级流式见批次 12 遗留）
            text = result.answer
            step = 48
            for i in range(0, len(text), step):
                yield _sse("delta", {"text": text[i : i + step]})
            for citation in result.citations:
                yield _sse("citation", citation.model_dump(mode="json"))
        if result.done.finish_reason == "error":
            yield _sse(
                "error",
                {"code": 50301, "message": result.degraded_reason or "AI 服务降级"},
            )
        yield _sse("done", result.done.model_dump(mode="json"))

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
