"""CU-01 / CU-03 · 应用装配冒烟（入口、中间件、异常处理器、探针、OpenAPI）。

🔴 本文件**不依赖任何外部服务** —— 它校验的是"服务能起来、契约不破"。
   依赖 MySQL/Redis 的用例见 ``test_auth_db.py``（缺依赖时 skip）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app

# ============================================================================
# 装配
# ============================================================================
def _registered_paths(app) -> set[str]:
    """取全部已暴露路径。

    ⚠️ **不要直接遍历 ``app.routes``** —— 新版 FastAPI 的 ``include_router``
    不再把子路由摊平进 ``app.routes``（而是保留一个 ``_IncludedRouter`` 包装）。
    以 **OpenAPI 描述**为准：它既是版本无关的，也正是前端/网关真正看到的东西。
    """
    return set(app.openapi()["paths"])


def test_app_builds_and_routes_registered(app) -> None:
    paths = _registered_paths(app)
    for expected in (
        f"{settings.API_V1_PREFIX}/auth/login",
        f"{settings.API_V1_PREFIX}/auth/refresh",
        f"{settings.API_V1_PREFIX}/auth/logout",
        f"{settings.API_V1_PREFIX}/auth/me",
        f"{settings.API_V1_PREFIX}/auth/permissions",
        f"{settings.API_V1_PREFIX}/auth/password",
        "/health",
    ):
        assert expected in paths, f"路由缺失：{expected}（现有：{sorted(paths)}）"


def test_auth_methods_match_interface_spec(app) -> None:
    """方法与接口文档逐字对齐（API-01~05 POST/GET，API-06 PUT）。"""
    paths = app.openapi()["paths"]
    prefix = settings.API_V1_PREFIX
    expected_methods = {
        f"{prefix}/auth/login": "post",
        f"{prefix}/auth/refresh": "post",
        f"{prefix}/auth/logout": "post",
        f"{prefix}/auth/me": "get",
        f"{prefix}/auth/permissions": "get",
        f"{prefix}/auth/password": "put",
    }
    for path, method in expected_methods.items():
        assert method in paths[path], f"{path} 缺少 {method.upper()}（实际：{sorted(paths[path])}）"
        assert paths[path][method]["tags"], f"{path} 缺少 tag（不便在 Swagger 中分组）"


def test_openapi_generation_succeeds(app) -> None:
    """OpenAPI 能生成即代表所有 Schema / response_model 都是合法的。"""
    schema = app.openapi()
    assert schema["info"]["title"] == settings.APP_NAME
    assert "/api/v1/auth/login" in schema["paths"]


def test_openapi_ids_are_declared_as_string(app) -> None:
    """Swagger 上 ID 必须显示为 string（接口 §1.2）。"""
    schema = app.openapi()
    user_schema = schema["components"]["schemas"]["UserInfoOut"]
    assert user_schema["properties"]["id"].get("type") == "string"


def test_create_app_is_reentrant() -> None:
    """工厂可重复调用（测试并行 / 多实例场景）。"""
    assert create_app() is not create_app()


# ============================================================================
# trace_id 三处一致（F-1.4）
# ============================================================================
def test_response_carries_trace_id_and_header(client: TestClient) -> None:
    resp = client.get("/health")
    body = resp.json()
    assert resp.headers.get("X-Request-Id"), "响应头缺 X-Request-Id"
    assert body["trace_id"], "响应体缺 trace_id"
    assert resp.headers["X-Request-Id"] == body["trace_id"], "响应头与响应体 trace_id 不一致"


def test_client_supplied_request_id_is_echoed(client: TestClient) -> None:
    """跨端串联：客户端自带 ``X-Request-Id`` 时必须原样沿用（接口 §1.1）。"""
    given = "9f2a1c7e4b0d"
    resp = client.get("/health", headers={"X-Request-Id": given})
    assert resp.headers["X-Request-Id"] == given
    assert resp.json()["trace_id"] == given


def test_trace_id_is_regenerated_per_request(client: TestClient) -> None:
    first = client.get("/health").json()["trace_id"]
    second = client.get("/health").json()["trace_id"]
    assert first and second and first != second


# ============================================================================
# 探针（降级不抛 500）
# ============================================================================
def test_health_never_returns_5xx(client: TestClient) -> None:
    """依赖不可用时也必须 HTTP 200 + degraded 标记（与业务降级口径一致）。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] in (0, 50301)
    assert body["data"]["status"] in ("ok", "degraded")

    deps = body["data"]["dependencies"]
    # 基础探活位必须在；版本号是排障附加信息（可能为 None）
    assert {"mysql", "redis"} <= set(deps)
    assert {"mysql_version", "redis_version"} <= set(deps)

    if body["code"] == 50301:
        assert body["data"]["degraded"] is True
        assert body["data"]["degraded_reason"]
        # 降级时至少要有一项依赖确实不可用（不能自相矛盾）
        assert deps["mysql"] is False or deps["redis"] is False
    else:
        assert deps["mysql"] is True and deps["redis"] is True


# ============================================================================
# 统一异常（接口 §1.4 第 8 层：任何异常都不得裸抛）
# ============================================================================
def test_unknown_route_returns_unified_40401(client: TestClient) -> None:
    resp = client.get("/api/v1/definitely-not-exists")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 40401
    assert set(body) == {"code", "message", "data", "page", "trace_id"}
    # message 必须是 ERROR_META 的中文文案，不能把框架英文 detail 透出去
    assert body["message"] == "资源不存在"


def test_method_not_allowed_keeps_http_405(client: TestClient) -> None:
    """方法不符属传输层语义：HTTP 保持 405，message 走中文（接口 §1.5 无 405 码）。"""
    resp = client.get("/api/v1/auth/login")
    assert resp.status_code == 405
    assert resp.json()["code"] == 40001
    assert "Method Not Allowed" not in resp.json()["message"]


def test_missing_token_returns_40101_not_403(client: TestClient) -> None:
    """🔴 未带凭证必须是 40101（而非 FastAPI 默认的 403）—— 前端据此决定是否跳登录页。"""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


def test_invalid_token_returns_40101(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


def test_validation_error_returns_40001_with_field_list(client: TestClient) -> None:
    """入参校验失败 → 40001 + ``data.fields[]``（接口 §1.5，表单据此定位字段）。"""
    resp = client.post("/api/v1/auth/login", json={"username": "ab", "password": "123"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001
    fields = {f["field"] for f in body["data"]["fields"]}
    assert {"username", "password"} <= fields, f"未定位到非法字段：{fields}"


def test_extra_field_is_rejected_not_ignored(client: TestClient) -> None:
    """🔴 ``extra=forbid``：字段拼错必须 40001，静默忽略是事故源（接口 §1.1）。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "hr_zhang", "password": "12345678", "usernmae": "typo"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


def test_login_missing_body_returns_40001(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == 40001


# ============================================================================
# CORS（开发期前端 5173 直连 8002）
# ============================================================================
def test_cors_preflight_allows_dev_origin(client: TestClient) -> None:
    origin = settings.CORS_ORIGINS[0]
    resp = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == origin
