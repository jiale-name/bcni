"""pytest 公共夹具（CU-26 测试骨架的前置）。

🔴 **测试必须能在「没有任何外部依赖」的机器上跑起来**：
本项目的 CI / 同事本机未必有 MySQL + Redis。因此：

- 不依赖 DS 的用例（契约、雪花 ID、JWT、异常码）**永远执行**；
- 依赖 DS 的用例统一用 ``@requires_db`` / ``@requires_redis`` 标注，缺依赖时 **skip** 而非 fail。

这样「契约红线」（如 ID 必须序列化为字符串）在任何环境下都被强制校验。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app


# ============================================================================
# 应用 / 客户端
# ============================================================================
@pytest.fixture(scope="session")
def app():
    """应用实例（session 级：避免反复构建中间件栈）。

    ⚠️ 本夹具把 ``RATE_LIMIT_PER_MINUTE`` 抬高到 10 万，**不是**为了让限流「不生效」，
    而是因为限流的主体在**未登录**时退化为 **IP**（``ip:testclient``）——
    整轮测试的所有登录请求会共用同一个桶。真实默认值（300/min）下，
    只要一轮跑得快（本套件登录次数已接近数百），就可能偶发 **429**，
    那是**测试的假阳性**，而不是被测行为的问题（会掩盖真正的失败，排查成本极高）。

    限流本身有**专门用例**覆盖（见 ``tests/test_audit.py`` 的 CU-09 小节）：
    那里直接对判定结果打桩，既能稳定触发 42901、又不受默认阈值影响 ——
    比「靠真实阈值凑够请求数」既快又稳。
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "RATE_LIMIT_PER_MINUTE", 100_000)
    try:
        yield create_app()
    finally:
        monkey.undo()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """HTTP 客户端。**with** 形式确保 lifespan 生效（日志与连接池释放被覆盖测试到）。"""
    with TestClient(app) as c:
        yield c


# ============================================================================
# 外部依赖探测（惰性，带短超时，避免拖慢收集阶段）
# ============================================================================
def _probe_mysql() -> bool:
    """用一个 2s 超时的独立引擎探测 MySQL，**不复用业务引擎**（避免污染连接池）。"""
    from sqlalchemy import create_engine

    try:
        probe = create_engine(
            settings.database_url,
            connect_args={"connect_timeout": 2},
            poolclass=None,
        )
        try:
            with probe.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        finally:
            probe.dispose()
    except Exception:  # noqa: BLE001 — 探测，任何异常都算不可用
        return False


def _probe_redis() -> bool:
    from app.dao.redis_client import ping

    return ping()


_DB_OK: bool | None = None
_REDIS_OK: bool | None = None


def db_available() -> bool:
    global _DB_OK  # noqa: PLW0603 — 进程内缓存探测结果
    if _DB_OK is None:
        _DB_OK = _probe_mysql()
    return _DB_OK


def redis_available() -> bool:
    global _REDIS_OK  # noqa: PLW0603
    if _REDIS_OK is None:
        _REDIS_OK = _probe_redis()
    return _REDIS_OK


requires_db = pytest.mark.skipif(not db_available(), reason="需要可用的 MySQL（DS-1）")
requires_redis = pytest.mark.skipif(not redis_available(), reason="需要可用的 Redis（DS-3）")


@pytest.fixture
def db():
    """数据库会话（仅被 ``@requires_db`` 的用例使用）。"""
    from app.dao.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
