"""数据字典（TBL-09 ``dict_type`` / TBL-10 ``dict_item``，F-1.7，接口 API-23 ~ API-27）。

字典的定位
==========
**扁平枚举码表** —— 与「受控标签词表」（TBL-18 ``talent_tag_dict``）**不是一回事**：

| | 数据字典（本模块） | 受控标签词表（CU-17） |
| --- | --- | --- |
| 语义 | **封闭枚举**（学历/性别/证件类型…） | **开放标签**（可增长、可由 AI 建议） |
| 写入方 | 仅 ADM 人工维护（API-25~27） | 人工 + AI 建议 → 审核（F-3.4.1） |
| 业务引用 | 业务表存**编码**（如 ``talent_base.education_level``） | 通过关联表打标（TBL-19） |
| 数量级 | 10¹（类型）/ 10²（项） | 10² ~ 10³ |

🔴 两个不变式（实现与测试都依赖它们）
------------------------------------
1. **字典项只「停用」，从不物理删、也不软删**（API-27 的语义）。
   业务表里存的是 ``item_code``，把行删掉会让**历史数据的枚举值变成孤儿**
   （``education_level='master'`` 查不到显示名）。停用只是让下拉框不再提供它。

2. **``item_code`` 创建后不可修改**（API-26 在服务层拒绝改它）。
   它是被业务数据引用的**值**，改了等于静默篡改历史语义 ——
   比「删一行」更隐蔽：数据还在，但含义漂移了。

⚠️ 顺带说明上游 DDL 中的一个**潜在缺陷**（本期不触发，但需留痕）
--------------------------------------------------------------
数据库设计 §4.2 的唯一键是 ``(tenant_id, type_code, is_deleted)``。
这类「唯一键带 ``is_deleted``」的写法在 MySQL 下有**已知坑**：
``is_deleted`` 是 0/1 两值，同一编码**最多只能存在一条软删行** ——
第二次软删会与第一次撞唯一键（``Duplicate entry``）。

本项目之所以安全，是因为上面的不变式 1「**字典项从不软删**」：
``is_deleted`` 恒为 0，于是该唯一键**退化为** ``(tenant_id, type_code, item_code)``，
恰好就是我们要的语义。**若将来有人给字典加上「真删除」，
必须先改这个唯一键（例如把 ``deleted_at`` 或删除批次号纳入唯一键）**，
否则会在第二次删除时炸出 1062。已登记进评审文档 §7.3 批次 4 遗留。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import MYSQL_TABLE_KWARGS, Base, IdTenantCommonMixin


class DictType(IdTenantCommonMixin, Base):
    """TBL-09 字典类型（扁平枚举码表，区别于受控标签词表）。

    ⚠️ **本表没有 CRUD 接口**：接口矩阵只给了 ``GET /dicts``（API-23），
    新增/修改/停用**类型**的动作在矩阵中不存在。因此类型由种子脚本初始化
    （见 ``scripts/seed_dev.py``），运行期只读 —— 与 CU-06 对「岗位没有改/删接口」
    的处理同源：**矩阵未定义即不实现**，不凭空造接口。
    """

    __tablename__ = "dict_type"
    __table_args__ = (
        # ⚠️ 唯一索引必须带 is_deleted（数据库 §0 原则 4）。本表的 is_deleted 恒为 0，
        #    故等价于「租户内类型编码唯一」（详见模块 docstring 的缺陷说明）。
        UniqueConstraint("tenant_id", "type_code", "is_deleted", name="uk_tenant_type_code"),
        MYSQL_TABLE_KWARGS,
    )

    type_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="类型编码，如 education_level"
    )
    type_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="类型名")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<DictType id={self.id} code={self.type_code!r} status={self.status}>"


class DictItem(IdTenantCommonMixin, Base):
    """TBL-10 字典项（枚举值 + 显示名）。

    ``parent_code`` 支持多级（如「省 → 市」），用**编码**而非 ID 串联 ——
    因为「项的编码」才是业务数据引用的东西，且让种子数据的书写与校对更直观。
    多级只用于展示（下拉联动），**不参与权限或行级过滤**。
    """

    __tablename__ = "dict_item"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "type_code", "item_code", "is_deleted", name="uk_tenant_type_item"
        ),
        # 「查某个类型的全部项」是唯一的读路径（下拉框），故按 (tenant_id, type_code) 建索引；
        # 排序由 sort_no 完成，不额外建索引（10² 量级，回表代价可忽略）。
        Index("idx_tenant_type", "tenant_id", "type_code"),
        MYSQL_TABLE_KWARGS,
    )

    type_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="字典类型编码")
    item_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="枚举值（业务数据引用它，创建后不可修改）"
    )
    item_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="显示名")
    sort_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序"
    )
    parent_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="父项编码（支持多级）"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=1, server_default=text("1"), comment="状态 0 停用 1 启用"
    )

    def __repr__(self) -> str:
        return f"<DictItem {self.type_code}.{self.item_code} status={self.status}>"
