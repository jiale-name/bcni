"""业务服务层（架构 §3.3）。

🔴 唯一允许「编排 + 事务 + 跨存储」的一层：

- 事务边界在这里（DAO 不 commit，Router 不 commit）；
- 跨库补偿顺序在这里（如「合并先写 MySQL、删除先删向量」）；
- 确定性结论必须来自确定性组件（ThresholdEngine 等），**LLM 只产文本**。

已落地单元：``AuthService``（CU-04）。
"""

from app.services.auth_service import AuthService

__all__ = ["AuthService"]
