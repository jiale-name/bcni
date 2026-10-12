"""DS-1 MySQL 引擎与会话（架构 §5：``dao/session.py`` = 引擎 / 会话 / 基类）。

约定
----
- ``pool_pre_ping=True``：取连接前探活，避免 MySQL 空闲断连导致的偶发 50001。
- ``expire_on_commit=False``：提交后仍可读对象属性，避免 Service 层再查一次。
- ``Base`` 从 :mod:`app.models.base` 转出，**models 与 dao 共用同一个 Base**，
  保证 ``Base.metadata`` 能覆盖全部表（Alembic autogenerate 依赖此点）。
- 🔴 ``get_db`` 只负责会话生命周期，**租户条件注入不在此处**（见 :mod:`app.core.tenant`）。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base, MYSQL_TABLE_KWARGS  # noqa: F401 — Base 供 alembic/env.py 复用

__all__ = [
    "AuditSessionLocal",
    "Base",
    "SessionLocal",
    "audit_engine",
    "engine",
    "get_db",
    "session_scope",
]

#: 同步引擎（create_engine 是惰性的，**不会**在 import 时连接数据库 —— 便于无库启动与测试）
engine = create_engine(
    settings.database_url,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    # 连接失败要**快速暴露**（默认 10s 会让 /health 探针与启动自检变得很慢）
    connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

#: 🔴 **审计写入专用引擎 / 会话**（CU-24，「只追加」的第三层保证）。
#:
#: 与 :data:`engine` 的**唯一区别**是账号：该账号在库级只有 ``SELECT`` + ``INSERT``
#: （``UPDATE``/``DELETE`` 被 MySQL 拒绝）。于是「改审计 / 删审计」不再依赖
#: 「应用代码不去改」这条**约定**，而是被数据库强制 —— 直接连库写 SQL 也改不动。
#:
#: ⚠️ 未配置 ``AUDIT_DB_USER`` 时**复用主引擎**（功能不受影响，只是少了这层硬保证）。
#: 之所以不做成「没配就报错」：审计是 fail-open 的旁路记录，
#: 让它因一个配置缺失而整体停摆，是把「合规增强」变成「可用性风险」。
audit_engine = (
    create_engine(
        settings.audit_database_url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        # 审计写入是**低频小事务**（每条业务动作一条），池子不需要和主库一样大
        pool_size=max(2, settings.DB_POOL_SIZE // 4),
        max_overflow=5,
        pool_recycle=settings.DB_POOL_RECYCLE,
        connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
        future=True,
    )
    if settings.AUDIT_DB_USER
    else engine
)

AuditSessionLocal = sessionmaker(
    bind=audit_engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
) if settings.AUDIT_DB_USER else SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：提供一个请求级 Session。

    事务边界在 **Service 层**（架构 §3.3：DAO 只做持久化，不控制事务）。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class session_scope:
    """脚本 / 后台任务用的事务上下文（无请求上下文时的替代方案）。

    .. code-block:: python

        with session_scope() as db:
            db.add(obj)
    """

    def __enter__(self) -> Session:
        self._db = SessionLocal()
        return self._db

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        try:
            if exc_type is None:
                self._db.commit()
            else:
                self._db.rollback()
        finally:
            self._db.close()
