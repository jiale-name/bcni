"""统一异常与错误码（F-1.4）。"""

from app.exceptions.exception_handlers import (
    ERROR_META,
    AccountLockedError,
    BadCredentialsError,
    BizError,
    ConflictError,
    ErrorCategory,
    ErrorCode,
    ErrorMeta,
    IllegalStateError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    UnauthenticatedError,
    build_error_response,
    register_exception_handlers,
    validation_fields,
)

__all__ = [
    "ERROR_META",
    "AccountLockedError",
    "BadCredentialsError",
    "BizError",
    "ConflictError",
    "ErrorCategory",
    "ErrorCode",
    "ErrorMeta",
    "IllegalStateError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitedError",
    "UnauthenticatedError",
    "build_error_response",
    "register_exception_handlers",
    "validation_fields",
]
