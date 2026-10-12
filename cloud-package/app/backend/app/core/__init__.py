"""横切关注点（架构 §8）。

- :mod:`context`     请求上下文（trace_id 等）—— 依赖 contextvars，异步安全
- :mod:`logging`     结构化日志 + trace_id 串联
- :mod:`snowflake`   雪花 ID 生成（数据库 §0.1）
- :mod:`security`    JWT / 密码 / 权限依赖
- :mod:`tenant`      数据域（tenant_id / org_id / data_scope）
- :mod:`ratelimit`   限流（F-1.6）
- :mod:`middleware`  请求处理链中间件（接口 §1.4）
"""
