"""通用契约：统一响应 / 分页 / 雪花 ID 序列化。

严格对齐《接口设计文档》§1.1 基础约定 与 §1.2 雪花 ID 约束。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema

T = TypeVar("T")

# ============================================================================
# 🔴 雪花 ID：响应一律 string（接口 §1.2 —— 本项目最强约束）
# ============================================================================
# 主键为雪花 ID（int64），可超 JS Number.MAX_SAFE_INTEGER（9.007e15）。
# 直接以数字下发会末位静默截断，前端拿到的 ID 与真实 ID 不一致 → 请求详情必然 404。
# （同类项目已在合同要素接口踩过此坑，排查耗时 >1 天。）
#
# 行为：
#   响应 → 序列化为 str（when_used="json"，不影响 Python 内部逻辑为 int）
#   请求 → 接受 str 或 int（Pydantic lax 模式天然把 "123" 转为 123）
#   文档 → OpenAPI 声明为 type: string, format: int64（接口 §1.2 要求 Swagger 亦为 string）
SnowflakeId = Annotated[
    int,
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "format": "int64",
            "examples": ["1839204771234567890"],
        }
    ),
]

#: 分页上限（接口 §1.1：``per_page`` 上限 100，超出按 100 处理）
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 20

#: 时间序列化格式（接口 §1.1：「请求与响应统一 ``YYYY-MM-DD HH:mm:ss``，服务器本地时区」）。
#: ⚠️ 不能依赖 Pydantic 默认的 ISO-8601（``2026-09-22T11:30:00``）—— 带 ``T`` 的格式
#: 会让前端 date-picker 直接解析失败，且与请求侧约定的格式**不一致**（请求只收空格分隔）。
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 响应侧的时间字段类型：Python 内部是 ``datetime``，序列化为 ``YYYY-MM-DD HH:mm:ss``；
#: ``None`` 原样下发（接口 §1.1：未填字段一律 ``null``，不返回空字符串）。
DateTimeStr = Annotated[
    datetime,
    PlainSerializer(
        lambda v: v.strftime(DATETIME_FORMAT) if isinstance(v, datetime) else None,
        return_type=str | None,
        when_used="json",
    ),
]


class PageInfo(BaseModel):
    """分页信息。与 ``data`` **同级**，不得塞进 ``data``（接口 §1.1）。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(0, description="总条数")
    page: int = Field(1, description="当前页码，从 1 起")
    per_page: int = Field(DEFAULT_PER_PAGE, description="每页条数（上限 100）")
    pages: int = Field(0, description="总页数")


class CommonResp(BaseModel, Generic[T]):
    """统一响应体（接口 §1.1 / 类图 CL-2 的 ``CommonResp``）。

    .. code-block:: json

        { "code": 0, "message": "ok", "data": {...}, "page": null, "trace_id": "..." }

    🔴 **成功判定是 ``code == 0**``，不是 HTTP 200** —— 降级场景 HTTP 200 但 ``code`` 可为非 0
    （接口 §1.5：50301/50302 走 HTTP 200 + ``data.degraded=true``）。
    """

    model_config = ConfigDict(extra="forbid")

    code: int = 0
    message: str = "ok"
    data: T | None = None
    page: PageInfo | None = None
    trace_id: str | None = None


#: 便于阅读的别名（与 ``CommonResp`` 同一类）
ApiResponse = CommonResp


class PageQuery(BaseModel):
    """分页请求参数基类。"""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(1, ge=1, description="页码，从 1 起")
    per_page: int = Field(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE, description="每页条数，上限 100")


# ============================================================================
# 构造助手
# ============================================================================
def normalize_per_page(per_page: int | None) -> int:
    """把 ``per_page`` 收敛到 [1, 100]（接口 §1.1：超出按 100 处理）。"""
    if per_page is None:
        return DEFAULT_PER_PAGE
    return max(1, min(int(per_page), MAX_PER_PAGE))


def build_page_info(total: int, page: int, per_page: int) -> PageInfo:
    per_page = normalize_per_page(per_page)
    page = max(1, int(page))
    pages = (total + per_page - 1) // per_page if per_page else 0
    return PageInfo(total=total, page=page, per_page=per_page, pages=pages)


def ok(
    data: Any = None,
    *,
    page: PageInfo | None = None,
    message: str = "ok",
    code: int = 0,
    trace_id: str | None = None,
) -> CommonResp[Any]:
    """构造成功响应。``trace_id`` 缺省时自动从上下文取（接口 §1.4 第 8 层）。"""
    if trace_id is None:
        # 局部导入避免循环依赖（context 不依赖 schemas）
        from app.core.context import get_trace_id

        trace_id = get_trace_id()
    return CommonResp(code=code, message=message, data=data, page=page, trace_id=trace_id)


def paginated(
    items: list[Any],
    total: int,
    *,
    page: int,
    per_page: int,
    message: str = "ok",
) -> CommonResp[Any]:
    """构造分页响应：``data`` 直接放数组，分页信息放同级 ``page``。"""
    return ok(items, page=build_page_info(total, page, per_page), message=message)


def degraded(
    data: Any = None,
    *,
    reason: str,
    code: int = 50301,
    message: str = "服务已降级",
    trace_id: str | None = None,
) -> CommonResp[Any]:
    """构造降级响应（接口 §1.5 红线：**降级不抛 500**）。

    HTTP 仍为 200，``code`` 标记降级类型，``data.degraded=true``/``data.degraded_reason``
    供前端渲染降级徽标。❌ 严禁把第三方异常包装成 50000。
    """
    payload = dict(data) if isinstance(data, dict) else {"result": data}
    payload["degraded"] = True
    payload["degraded_reason"] = reason
    return ok(payload, code=code, message=message, trace_id=trace_id)
