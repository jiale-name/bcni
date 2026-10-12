"""数据访问层（架构 §5）。

- :mod:`session`        DS-1 MySQL 引擎 / 会话 / ``Base``
- :mod:`redis_client`   DS-3 Redis 客户端与键规范
- :mod:`user_dao`       用户 / 角色 / 权限 / 租户 / 组织（TBL-01~08）
- :mod:`token_dao`      Token 吊销与会话版本（``sess:revoked:*`` / ``sess:tok_ver:*``）

🔴 DAO 红线（架构 §3.3）：

1. **只做纯 CRUD**，不含业务判定（判定在 Service）；
2. **强制租户条件** —— 每个查询都带 ``tenant_id`` 且过滤 ``is_deleted``；
3. **不控制事务** —— 事务边界在 Service；
4. 不拼接 SQL，全走 ORM。

注：``from app.dao import user_dao`` 这类写法依赖 Python 的子模块导入机制，
**无需在本文件显式 import**（否则易与 ``core`` 层形成环）。
"""
