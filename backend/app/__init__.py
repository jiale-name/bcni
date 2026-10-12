"""2026 顶配版 AI 数字化人才平台 · 后端应用包（一期：F-3 档案与数字画像）。

六层落位（架构设计 §3.3 / §5）::

    routers  ──►  services  ──►  dao  ──►  models
      │             │             │
      │             │             └─ 纯 CRUD + 强制 tenant_id
      │             └─ 业务编排与事务；经 RetrievalProvider 抽象访问 Milvus
      └─ 仅参数校验与响应组装

依赖方向单向向下，禁止反向或跨层（架构 §3.3 红线）。
"""

__version__ = "0.1.0"
