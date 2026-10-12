"""CU-03 · 统一响应契约 / 分页 / 降级语义（接口 §1.1 / §1.5）。"""

from __future__ import annotations

import json

from app.exceptions import ErrorCode
from app.schemas.common import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    CommonResp,
    build_page_info,
    degraded,
    normalize_per_page,
    ok,
    paginated,
)


def test_ok_envelope_has_all_five_fields() -> None:
    """统一响应五字段：``code / message / data / page / trace_id``（接口 §1.1）。"""
    payload = json.loads(ok({"a": 1}).model_dump_json())
    assert set(payload) == {"code", "message", "data", "page", "trace_id"}
    assert payload["code"] == 0
    assert payload["message"] == "ok"
    assert payload["data"] == {"a": 1}
    assert payload["page"] is None


def test_envelope_forbids_extra_fields() -> None:
    """结构污染必须报错（防止有人往统一响应里塞自定义字段）。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CommonResp(code=0, unexpected="x")  # type: ignore[call-arg]


# ============================================================================
# 分页
# ============================================================================
def test_per_page_is_capped_at_100() -> None:
    """接口 §1.1：``per_page`` 上限 100，超出按 100 处理并回显实际值。"""
    assert normalize_per_page(999) == MAX_PER_PAGE
    assert normalize_per_page(0) == 1
    assert normalize_per_page(None) == DEFAULT_PER_PAGE
    assert normalize_per_page(37) == 37


def test_page_info_pages_calculation() -> None:
    assert build_page_info(382, 1, 20).pages == 20
    assert build_page_info(0, 1, 20).pages == 0
    assert build_page_info(1, 1, 20).pages == 1
    assert build_page_info(101, 1, 100).pages == 2


def test_paginated_puts_items_in_data_and_meta_in_page() -> None:
    """🔴 ``data`` 直接放数组，分页信息放**同级 ``page``**（不得整包塞进 data）。"""
    body = json.loads(paginated([{"id": "1"}], 382, page=2, per_page=20).model_dump_json())
    assert body["data"] == [{"id": "1"}]
    assert body["page"] == {"total": 382, "page": 2, "per_page": 20, "pages": 20}


def test_over_limit_per_page_is_reflected_in_response() -> None:
    body = json.loads(paginated([], 5, page=1, per_page=500).model_dump_json())
    assert body["page"]["per_page"] == MAX_PER_PAGE


# ============================================================================
# 降级（接口 §1.5 红线：降级不抛 500）
# ============================================================================
def test_degraded_is_http_200_with_code_50301_and_flag() -> None:
    body = json.loads(degraded({"result": []}, reason="向量服务不可用").model_dump_json())
    assert body["code"] == int(ErrorCode.AI_DEGRADED) == 50301
    assert body["data"]["degraded"] is True
    assert body["data"]["degraded_reason"] == "向量服务不可用"


def test_degraded_never_uses_internal_error_code() -> None:
    """严禁把第三方异常包装成 50000（否则前端无法区分「业务失败」与「服务降级」）。"""
    body = json.loads(degraded(None, reason="x").model_dump_json())
    assert body["code"] != int(ErrorCode.INTERNAL_ERROR)


def test_vector_degraded_uses_50302() -> None:
    body = json.loads(
        degraded({"result": []}, reason="向量库超时", code=int(ErrorCode.VECTOR_DEGRADED)).model_dump_json()
    )
    assert body["code"] == 50302
    assert body["data"]["degraded"] is True


# ============================================================================
# 错误码表完整性（接口 §1.5 全量）
# ============================================================================
def test_error_code_table_matches_interface_spec() -> None:
    from app.exceptions import ERROR_META

    expected = {
        40001: 400,
        40002: 400,
        40101: 401,
        40102: 401,
        40103: 401,
        40301: 403,
        40401: 404,
        40901: 409,
        40902: 409,
        40903: 409,
        40904: 409,
        41301: 413,
        41501: 415,
        42201: 422,
        42203: 422,  # 缺少合并快照，无法回滚（SQ-4 决策 2 · 数据流图/类图时序图已用）
        42901: 429,
        50000: 500,
        50001: 500,
        50002: 500,
        50301: 200,  # 🔴 降级走 HTTP 200
        50302: 200,  # 🔴 降级走 HTTP 200
        50401: 504,
        50402: 504,
    }
    actual = {int(code): meta.http_status for code, meta in ERROR_META.items()}
    assert actual == expected, f"错误码表与接口 §1.5 不一致：{actual}"
