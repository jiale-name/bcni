"""Alembic 运行环境（CU-02）。

设计要点
--------
1. **连接串的唯一来源是 ``app.config.settings``** —— 与业务代码共用同一套引擎配置，
   不会出现「迁移连 A 库、应用连 B 库」的经典事故。
2. **``target_metadata`` 来自 ``app.models.Base``** —— 而 ``app/models/__init__.py``
   显式 import 了每一张表，保证 ``Base.metadata`` 完整（否则 autogenerate 会生成
   ``DROP TABLE``）。
3. **离线模式可用**：``alembic upgrade head --sql`` 不连数据库即可产出 DDL，
   用于「无库环境下的迁移评审与 CI 校验」。
4. 🔴 **唯一事实来源是 ORM 模型**（数据库 §0）：DDL 只在 ``versions/`` 里，
   数据库设计文档的 DDL 仅作字段参考。
"""

from __future__ import annotations

import pathlib
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---- 保证 `alembic` 命令在任意 cwd 下都能 import 到 app 包 ----
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402  — 副作用导入：注册全部表

config = context.config

#: 把 settings 的连接串注入 alembic（`%` 需转义，否则 configparser 插值报错）
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: autogenerate 的比对基准
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只产出 SQL，不连库（用于 DDL 评审 / CI）。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # 迁移是短任务，无需连接池
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # 🔴 迁移里禁止出现「隐式删表」——必须显式写 op.drop_table
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
