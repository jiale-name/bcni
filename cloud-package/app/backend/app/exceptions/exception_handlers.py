"""统一异常与错误码（F-1.4）。

对齐《接口设计文档》§1.5 错误码表：**码段规划 ``4xxxx`` 业务 / ``5xxxx`` 系统与第三方**。

🔴 **本条是接口层最重要的红线之一**：任何异常都不得裸抛，一律由本模块兜成统一结构
``{code, message, data, page, trace_id}``（接口 §1.4 第 8 层）。

🔴 **降级不抛 500**：下游 AI / 检索异常返回 **HTTP 200 + ``code=0`` + ``data.degraded=true``**，
由 :func:`app.schemas.common.degraded` 构造。**严禁**包装成 50000 —— 否则前端无法区分
「业务失败」与「服务降级」，也无法统计降级率（F-1.6）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_trace_id
from app.core.logging import get_logger

logger = get_logger(__name__)

# 为什么不用 ORJSONResponse：
#   FastAPI ≥ 0.13x 起，带 response_model / 返回类型的接口会**直接经 Pydantic 序列化为
#   JSON 字节**（比自定义 Response 类更快），并已弃用 ORJSONResponse（会打印
#   FastAPIDeprecationWarning）。异常处理器构造的是**裸 dict**（无 response_model），
#   用 starlette 的 JSONResponse 即可 —— 它内部走 ``json.dumps(ensure_ascii=False)``，
#   中文不会被转义。


# ============================================================================
# 错误码
# ============================================================================
class ErrorCode(IntEnum):
    """错误码枚举（接口 §1.5 全量）。"""

    OK = 0

    # ---------- 4xxxx 业务 ----------
    PARAM_INVALID = 40001  # 参数校验失败
    PARAM_OVER_LIMIT = 40002  # 参数超出业务上限
    UNAUTHENTICATED = 40101  # 未登录 / Token 过期
    BAD_CREDENTIALS = 40102  # 账号或密码错误
    ACCOUNT_LOCKED = 40103  # 账号被锁定
    NO_PERMISSION = 40301  # 无权限（含跨租户/跨部门越权）
    NOT_FOUND = 40401  # 资源不存在（含已软删）
    UNIQUE_CONFLICT = 40901  # 唯一约束冲突
    CONCURRENT_CONFLICT = 40902  # 并发状态冲突
    IDEMPOTENT_REPLAY = 40903  # 幂等键冲突（返回首次结果）
    PRECONDITION_UNMET = 40904  # 依赖前置未满足
    UPLOAD_TOO_LARGE = 41301  # 上传超限
    UNSUPPORTED_MEDIA = 41501  # 文件格式不支持
    ILLEGAL_STATE = 42201  # 状态机非法流转
    MISSING_SNAPSHOT = 42203  # 缺少合并快照，无法回滚（SQ-4 决策 2）

    # ---------- 5xxxx 系统 ----------
    RATE_LIMITED = 42901  # 触发限流
    INTERNAL_ERROR = 50000  # 服务内部错误
    DATABASE_ERROR = 50001  # 数据库错误
    STORAGE_ERROR = 50002  # 对象存储异常

    # ---------- 5xxxx 第三方（降级走 HTTP 200）----------
    AI_DEGRADED = 50301  # AI/检索服务降级
    VECTOR_DEGRADED = 50302  # 向量检索降级为关键词模式
    MODEL_TIMEOUT = 50401  # 模型调用超时
    OCR_TIMEOUT = 50402  # OCR 服务超时


class ErrorCategory(str):
    BUSINESS = "业务"
    SYSTEM = "系统"
    THIRD_PARTY = "第三方"


@dataclass(frozen=True)
class ErrorMeta:
    http_status: int
    message: str
    category: str


ERROR_META: dict[int, ErrorMeta] = {
    ErrorCode.PARAM_INVALID: ErrorMeta(400, "参数校验失败", ErrorCategory.BUSINESS),
    ErrorCode.PARAM_OVER_LIMIT: ErrorMeta(400, "参数超出业务上限", ErrorCategory.BUSINESS),
    ErrorCode.UNAUTHENTICATED: ErrorMeta(401, "未登录或登录已过期", ErrorCategory.BUSINESS),
    ErrorCode.BAD_CREDENTIALS: ErrorMeta(401, "账号或密码错误", ErrorCategory.BUSINESS),
    ErrorCode.ACCOUNT_LOCKED: ErrorMeta(401, "账号已锁定", ErrorCategory.BUSINESS),
    ErrorCode.NO_PERMISSION: ErrorMeta(403, "无权限", ErrorCategory.BUSINESS),
    ErrorCode.NOT_FOUND: ErrorMeta(404, "资源不存在", ErrorCategory.BUSINESS),
    ErrorCode.UNIQUE_CONFLICT: ErrorMeta(409, "唯一约束冲突", ErrorCategory.BUSINESS),
    ErrorCode.CONCURRENT_CONFLICT: ErrorMeta(409, "该记录已被他人处理", ErrorCategory.BUSINESS),
    ErrorCode.IDEMPOTENT_REPLAY: ErrorMeta(409, "重复请求，已返回首次结果", ErrorCategory.BUSINESS),
    ErrorCode.PRECONDITION_UNMET: ErrorMeta(409, "依赖前置未满足", ErrorCategory.BUSINESS),
    ErrorCode.UPLOAD_TOO_LARGE: ErrorMeta(413, "上传文件超限", ErrorCategory.BUSINESS),
    ErrorCode.UNSUPPORTED_MEDIA: ErrorMeta(415, "文件格式不支持", ErrorCategory.BUSINESS),
    ErrorCode.ILLEGAL_STATE: ErrorMeta(422, "状态机非法流转", ErrorCategory.BUSINESS),
    ErrorCode.MISSING_SNAPSHOT: ErrorMeta(422, "缺少合并快照，无法回滚", ErrorCategory.BUSINESS),
    ErrorCode.RATE_LIMITED: ErrorMeta(429, "请求过于频繁", ErrorCategory.SYSTEM),
    ErrorCode.INTERNAL_ERROR: ErrorMeta(500, "服务内部错误", ErrorCategory.SYSTEM),
    ErrorCode.DATABASE_ERROR: ErrorMeta(500, "数据库错误", ErrorCategory.SYSTEM),
    ErrorCode.STORAGE_ERROR: ErrorMeta(500, "对象存储异常", ErrorCategory.SYSTEM),
    # 降级类走 HTTP 200（接口 §1.5 红线）
    ErrorCode.AI_DEGRADED: ErrorMeta(200, "AI 服务已降级", ErrorCategory.THIRD_PARTY),
    ErrorCode.VECTOR_DEGRADED: ErrorMeta(200, "向量检索已降级为关键词模式", ErrorCategory.THIRD_PARTY),
    ErrorCode.MODEL_TIMEOUT: ErrorMeta(504, "模型调用超时", ErrorCategory.THIRD_PARTY),
    ErrorCode.OCR_TIMEOUT: ErrorMeta(504, "OCR 服务超时", ErrorCategory.THIRD_PARTY),
}


# ============================================================================
# 业务异常
# ============================================================================
class BizError(Exception):
    """业务异常基类。业务代码只抛这个（或"由它派生"的），不要抛裸 ``Exception``。"""

    def __init__(
        self,
        code: ErrorCode | int,
        message: str | None = None,
        data: Any = None,
        *,
        http_status: int | None = None,
    ) -> None:
        self.code = int(code)
        meta = ERROR_META.get(self.code)
        self.http_status = http_status if http_status is not None else (meta.http_status if meta else 500)
        self.message = message or (meta.message if meta else "业务异常")
        self.data = data
        super().__init__(self.message)


class UnauthenticatedError(BizError):
    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.UNAUTHENTICATED, message, data)


class BadCredentialsError(BizError):
    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.BAD_CREDENTIALS, message, data)


class AccountLockedError(BizError):
    def __init__(self, locked_seconds: int) -> None:
        minutes = max(1, round(locked_seconds / 60))
        super().__init__(
            ErrorCode.ACCOUNT_LOCKED,
            f"账号已锁定，请 {minutes} 分钟后重试",
            {"locked_seconds": int(locked_seconds)},
        )


class PermissionDeniedError(BizError):
    """越权。**同时用于"资源不存在"** —— 接口 §1.7 红线：不区分「不存在」与「无权限」。"""

    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.NO_PERMISSION, message, data)


class NotFoundError(BizError):
    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, data)


class ConflictError(BizError):
    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.CONCURRENT_CONFLICT, message, data)


class RateLimitedError(BizError):
    def __init__(self, retry_after: int, limit: int | None = None) -> None:
        data: dict[str, Any] = {"retry_after": int(retry_after)}
        if limit is not None:
            data["limit"] = int(limit)
        super().__init__(ErrorCode.RATE_LIMITED, None, data)


class IllegalStateError(BizError):
    def __init__(self, message: str | None = None, data: Any = None) -> None:
        super().__init__(ErrorCode.ILLEGAL_STATE, message, data)


# ============================================================================
# 响应构造
# ============================================================================
def validation_fields(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """把 Pydantic / FastAPI 的校验错误列表转成契约里的 ``data.fields[]``（接口 §1.5）。

    **唯一事实来源**：请求体校验（``RequestValidationError``）与
    「服务层手工 ``model_validate`` 失败后转 BizError」两条路径都用它，
    保证两处的 ``fields[]`` 形状完全一致 —— 否则前端表单定位逻辑会在
    两类错误之间表现不一致（一类能高亮字段，另一类只能弹个 toast）。
    """
    out: list[dict[str, str]] = []
    for err in errors:
        # ``loc`` 里会带上来源前缀（``body`` / ``query`` / ``path``），
        # 但契约里的 ``field`` 只要字段名本身（前端拿它做 ``form.fields[field]`` 定位）
        parts = [
            str(part)
            for part in (err.get("loc") or ())
            if part not in ("body", "query", "path", "header", "cookie")
        ]
        out.append(
            {
                "field": ".".join(parts) or "_",
                "message": str(err.get("msg", "参数不合法")),
                "type": str(err.get("type", "")),
            }
        )
    return out


def _build_body(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "page": None,
        "trace_id": get_trace_id(),
    }


def _json_response(
    status_code: int,
    code: int,
    message: str,
    data: Any = None,
    headers: dict[str, str] | None = None,
) -> _JSONResponse:
    return _JSONResponse(
        status_code=status_code,
        content=_build_body(code, message, data),
        headers=headers,
    )


def build_error_response(
    code: ErrorCode | int,
    *,
    data: Any = None,
    message: str | None = None,
    headers: dict[str, str] | None = None,
) -> _JSONResponse:
    """在**不能抛异常**的位置构造统一错误响应（供 ASGI 中间件使用）。

    为什么需要它（而不是让中间件自己拼 JSON）
    -----------------------------------------
    Starlette 的异常处理中间件位于**用户中间件之内**：从用户中间件的
    ``__call__`` 里 ``raise BizError`` 不会被本模块的处理器接住，
    而是冒泡到最外层的 ``ServerErrorMiddleware`` 变成 500。
    （限流发生在路由之前，必须在中间件里直接应答。）

    于是中间件需要「自己造响应」，但绝不能**自己拼格式** —— 那样
    ``code``/``page``/``trace_id`` 五件套迟早与 :func:`_json_response` 漂移。
    本函数即是这一处的**唯一格式来源**。
    """
    meta = ERROR_META.get(int(code))
    http_status = meta.http_status if meta else 500
    return _json_response(
        http_status,
        int(code),
        message or (meta.message if meta else "业务异常"),
        data,
        headers,
    )


# ============================================================================
# 处理器注册
# ============================================================================
def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。应用启动时调用一次。"""

    @app.exception_handler(BizError)
    async def _handle_biz_error(request: Request, exc: BizError) -> _JSONResponse:
        meta = ERROR_META.get(exc.code)
        category = meta.category if meta else ErrorCategory.BUSINESS
        logger.warning(
            "业务异常 [%s] %s %s -> code=%s message=%s",
            category,
            request.method,
            request.url.path,
            exc.code,
            exc.message,
        )
        return _json_response(exc.http_status, exc.code, exc.message, exc.data)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> _JSONResponse:
        """入参校验失败 → 40001，``data.fields[]`` 供表单定位（接口 §1.5）。"""
        fields = validation_fields(exc.errors())
        logger.warning("参数校验失败 %s %s fields=%s", request.method, request.url.path, fields)
        return _json_response(
            ERROR_META[ErrorCode.PARAM_INVALID].http_status,  # 400（接口 §1.5）
            int(ErrorCode.PARAM_INVALID),
            ERROR_META[ErrorCode.PARAM_INVALID].message,
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> _JSONResponse:
        """Starlette/FastAPI 原生 HTTPException（含 404 路由未命中、401/403 依赖抛出）。

        🔴 两条口径：
        1. **越权与不存在统一按 40301 处理**（接口 §1.7）。这里对 404 做特例保留，
           因为"路由不存在"与"资源不存在"语义不同 —— 后者由业务层抛 ``NotFoundError``；
        2. **业务可见的 ``message`` 一律走 ERROR_META 中文文案**，不把 Starlette 的
           英文 ``detail``（如 "Not Found" / "Method Not Allowed"）透给前端。
           ⚠️ 接口 §1.5 的码表里没有 405，属传输层语义，故按最接近的 40001 归类，
           但**保留 HTTP 405 状态码**以免掩盖真实的调用方式错误。
        """
        mapping = {
            401: ErrorCode.UNAUTHENTICATED,
            403: ErrorCode.NO_PERMISSION,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.PARAM_INVALID,
            429: ErrorCode.RATE_LIMITED,
        }
        code = mapping.get(exc.status_code)
        if code is None:
            # 不在码表内的状态：原样透出 detail（多为框架/中间件抛出，用于排障）
            code = ErrorCode.INTERNAL_ERROR
            message = str(exc.detail) if isinstance(exc.detail, str) else ERROR_META[code].message
        else:
            message = ERROR_META[code].message  # 统一中文文案（不泄漏框架英文 detail）

        headers = dict(exc.headers or {})
        logger.warning(
            "HTTP 异常 %s %s -> status=%s code=%s detail=%s",
            request.method,
            request.url.path,
            exc.status_code,
            int(code),
            exc.detail,
        )
        return _json_response(
            exc.status_code if exc.status_code < 500 else 500,
            int(code),
            message,
            None,
            headers,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> _JSONResponse:
        """兜底：任何未捕获异常都不得裸抛 500（接口 §1.4 第 8 层）。"""
        logger.exception(
            "未捕获异常 %s %s: %s", request.method, request.url.path, exc
        )
        meta = ERROR_META[ErrorCode.INTERNAL_ERROR]
        return _json_response(
            meta.http_status,
            int(ErrorCode.INTERNAL_ERROR),
            meta.message,
            None,
        )
