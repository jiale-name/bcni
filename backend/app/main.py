"""FastAPI 应用入口（CU-01）。

启动链（顺序不可调换，对应架构 §5 与接口 §1.4）
------------------------------------------------

1. ``setup_logging()`` —— 让后续所有日志都带 ``trace_id``；
2. ``RequestMetaMiddleware``（最外层，纯 ASGI）—— 采集客户端 IP / UA（审计留痕用）；
3. ``TraceIdMiddleware``（纯 ASGI）—— 建立 trace_id 并回写 ``X-Request-Id``；
4. ``RateLimitMiddleware``（纯 ASGI）—— 接口限流（F-1.6，超限 42901）；
5. ``CORSMiddleware`` —— 开发期前端 5173 直连后端 8002 需要；
6. ``register_exception_handlers()`` —— 任何异常兜成统一结构（第 8 层红线）；
7. ``api_router`` 挂到 ``/api/v1``；
8. lifespan 关闭时释放 Redis 连接池与 MySQL 引擎。

启动命令（注意**不带** ``--reload``，便于观察代码是否真正生效）::

    .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RateLimitMiddleware, RequestMetaMiddleware, TraceIdMiddleware
from app.dao.redis_client import close_redis, ping as redis_ping, server_version
from app.dao.session import audit_engine, engine
from app.exceptions import register_exception_handlers
from app.routers import api_router
from app.schemas.common import CommonResp, degraded, ok

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动只做日志与探测，关闭释放连接池。

    🔴 **启动阶段不连接数据库** —— ``create_engine`` 是惰性的，如此可保证
    「库没起也能拉起服务看到 OpenAPI」，便于前端联调与容器编排。
    """
    setup_logging()
    logger.info(
        "%s v%s 启动中 env=%s port=%s",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.APP_ENV,
        settings.APP_PORT,
    )
    _schedule_import_resume()
    yield
    close_redis()
    engine.dispose()
    # 审计引擎可能是**另一个**引擎（专用库账号，CU-24），要一并释放；
    # 未配置时它与 ``engine`` 是同一个对象，重复 dispose 无害（幂等）。
    if audit_engine is not engine:
        audit_engine.dispose()
    logger.info("应用已关闭，连接池已释放")


def _schedule_import_resume() -> None:
    """启动自愈调度（批次 24）：延迟 3 秒重拉「解析中」僵尸导入任务。

    🔴 三条约束缺一不可：
    1. **延迟 + 后台线程** —— 不得占用启动窗口，更不能在 lifespan 里同步连库
       （本文件「启动阶段不连接数据库」的既定设计）；
    2. **fail-open** —— DB 未就绪 / 查询失败只告警，绝不影响服务可用性；
    3. **测试进程守卫** —— pytest 下（``PYTEST_CURRENT_TEST`` 存在）**不启动**：
       测试夹具会在 teardown 删行，后台线程此时重拉任务会造成跨用例竞争。
    """
    import os
    import threading

    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    def _resume() -> None:
        try:
            from app.services.import_service import ImportService

            resumed = ImportService.resume_interrupted()
            if resumed:
                logger.warning("启动自愈：重拉了 %s 个「解析中」僵尸导入任务", resumed)
        except Exception as exc:  # noqa: BLE001 —— 自愈失败不影响服务可用性
            logger.warning("启动自愈未执行（DB 未就绪或查询失败，不阻断启动）：%s", exc)

    timer = threading.Timer(3.0, _resume)
    timer.daemon = True
    timer.start()


def create_app() -> FastAPI:
    """应用工厂（测试可多次调用，互不污染）。"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "AI 数字化人才平台后端 API（一期）。\n\n"
            "- 统一响应：`{code, message, data, page, trace_id}`，**成功判定看 `code == 0`**；\n"
            "- 所有 ID 均为**雪花 ID 字符串**（避免 JS 精度丢失）；\n"
            "- 降级场景返回 HTTP 200 + `data.degraded=true`，**不抛 500**。"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ---- 中间件（后 add 的在外层）----
    #
    # 🔴 顺序不是随手排的，两点约束决定了它：
    #   ① ``RateLimitMiddleware`` 要**早于**路由执行（§1.4 第 1 层），
    #      但它生成的 429 响应里必须有 ``trace_id`` —— 所以 TraceId 必须在它**外侧**；
    #   ② 这个 429 是浏览器要读的，必须带 CORS 头 —— 所以 CORS 必须在**最外层**
    #      （若 CORS 在内层，限流的应答不经过它，前端只会看到一个被浏览器拦下的神秘失败）。
    #
    # 最终执行顺序（外 → 内）：
    #   CORS → TraceId → RequestMeta → RateLimit → 路由 → services → dao
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestMetaMiddleware)
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
    )

    # ---- 全局异常（第 8 层红线）----
    register_exception_handlers(app)

    # ---- 业务路由 ----
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # ---- 运维探针 ----
    @app.get("/health", response_model=CommonResp[dict], tags=["Z · 运维"], summary="健康检查")
    def health() -> CommonResp[dict]:
        """存活/就绪探针。

        🔴 **依赖不可用不返回 5xx** —— 而是 HTTP 200 + ``code=50301`` + ``data.degraded=true``，
        与业务降级口径一致，便于编排侧按 ``degraded`` 判定而非按状态码。

        ``dependencies`` 里附带版本号：连不上时能一眼看出是「服务没起」还是
        「驱动与版本不兼容」（如 redis-py 8 的 RESP3 协商在旧版 Redis 上失败）。
        """
        mysql_ok = _check_mysql()
        redis_ok = redis_ping()
        payload = {
            "status": "ok" if (mysql_ok and redis_ok) else "degraded",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
            "dependencies": {
                "mysql": mysql_ok,
                "redis": redis_ok,
                "mysql_version": _mysql_version() if mysql_ok else None,
                "redis_version": server_version() if redis_ok else None,
            },
        }
        if mysql_ok and redis_ok:
            return ok(payload)
        reason = "数据库或缓存不可用：" + ", ".join(
            name for name, healthy in (("mysql", mysql_ok), ("redis", redis_ok)) if not healthy
        )
        return degraded(payload, reason=reason, message="依赖不可用，服务降级运行")

    return app


def _check_mysql() -> bool:
    """轻量连接探活（短超时内返回，不抛异常）。"""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception as exc:  # noqa: BLE001 — 探针函数，任何异常均视为不可用
        logger.warning("MySQL 不可用：%s", exc)
        return False


def _mysql_version() -> str | None:
    """尽力探测 MySQL 版本（纯诊断用途，失败返回 ``None``）。"""
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT VERSION()").first()
        return str(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


app = create_app()


if __name__ == "__main__":
    # 直接 ``python -m app.main`` 也能起服务，参数与文档给出的 uvicorn 命令保持一致。
    #
    # 🔴 **固定 ``reload=False``** —— 用户需要「所见即所跑」：改完代码必须手动重启进程，
    # 才能确认新代码是否真的生效（否则热重载会把「没保存/没生效」的问题掩盖掉）。
    #
    # ``host`` 取 ``0.0.0.0``：容器与局域网真机联调时前端要能直连；本地开发同理无害。
    import uvicorn  # 局部导入：仅在作为脚本运行时才需要，避免测试导入 app 时加载 uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=False,
    )
