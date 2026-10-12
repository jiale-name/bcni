"""AI 中台与 Agent：TBL-23 / TBL-24 / TBL-25 / TBL-26 / TBL-27 + **TBL-29（新增）**

Revision ID: 0004_ai_agent
Revises: 0003_audit
Create Date: 2026-09-22

对应编码单元 **CU-10 ~ CU-13**（§7.2 第 2 批，N1 交付物）。

本迁移建立 6 张表
================

| 表 | 编号 | 说明 |
| --- | --- | --- |
| ``prompt_template`` | TBL-23 | Prompt 版本化（**只追加新版本**） |
| ``ai_call_log`` | TBL-24 | AI 调用日志（**只追加 + 按月分区**） |
| ``ai_model_config`` | **TBL-29** | 模型网关配置（**本批新增**，见下） |
| ``agent_task`` | TBL-25 | Agent 任务（权威状态 / Checkpointer 落点） |
| ``agent_step`` | TBL-26 | Agent 步骤（**只追加**） |
| ``agent_interrupt`` | TBL-27 | 人工介入待办 |

🔴 四处与数据库设计文档**有意不同**，均已登记进评审文档 §7.3 批次 7
=============================================================

① **新增 TBL-29 ``ai_model_config``（基线偏离：表编号 +1）**
----------------------------------------------------------
接口 §2.2 把 API-81/82 的「涉及表」写成 **TBL-23**，但 ``prompt_template``
的 ``prompt_code``/``content`` 均 NOT NULL，**装不下模型接入配置**
（provider / base_url / model_name / 主备顺序 / 超时 / 熔断 / 单价）。
硬塞会把一张表变成两种语义、且一半列永远为空。故新建本表，
编号顺延自 TBL-28。**一期表数：28 → 29。**

② **``ai_call_log`` 的 DDL 未含分区 ⇒ 本文件补上**
-----------------------------------------------
数据库 §4.6 的 DDL 写了 ``PARTITION BY RANGE``（照抄了 ``p202609``/``p202610``），
本迁移改为**按执行时刻动态生成**（同 TBL-28 的处理，理由见 ``0003_audit.py`` 的
模块 docstring）：照抄字面量会让 2027 年建库的人拿到一张「分区早已过期」的表。
**分区的唯一事实来源是本文件**（SQLAlchemy 表达不了命名分区列表）。

③ **``agent_interrupt`` 增加 ``interrupt_type`` / ``idempotent_key`` 两列**
-----------------------------------------------------------------------
- ``interrupt_type``：接口 §3 的 API-94 响应体里有这个字段，而数据库 §4.6 的 DDL
  **没有这一列** —— 缺列则无法生成该字段，也无法按类型分派处理界面。属**结构补充**。
- ``idempotent_key``：API-94 是接口 §1.6 列出的**强制幂等**接口之一，
  幂等需要落键才能跨进程生效（仅靠 Redis 不够 —— Redis 只存可丢数据）。

④ **``ai_call_log`` 增加 ``deployment`` 列（调用时落库的出网事实）**
------------------------------------------------------------------
接口 §3 备注把 ``mask_applied`` 称为「合规核查的唯一抓手」，其监控指标
``summary.mask_applied_rate`` 的分母必须是**真的出网的那些调用**。
最初按「场景是否配了 cloud 模型」推导分母（``ai_model_config.deployment``），
但那样有两个致命情形：① 云端主路失败、落到**本地备用**的那次调用会被算成
「出网但未脱敏」→ 凭空告警；② 某场景从 cloud 切回 local 之后，
**历史区间**的合规结论会随当前配置漂移 —— 合规证据被追溯性改写。
故把「本次调用实际走的是哪一路」在**调用时**落库（与 ``cost`` 同级的事实字段），
分母直接按它统计。``NULL`` 表示调用方未声明（按场景固有形态兜底，见 ``record``）。

⚠️ 复合主键（``ai_call_log``）
-----------------------------
与 ``audit_log`` 同因：分区列必须出现在每一个唯一键中，故 ``created_at`` 进主键。
这不是设计选择，是 MySQL 的硬性约束。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0004_ai_agent"
down_revision: str | None = "0003_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: MySQL 表级参数（数据库 §0 原则 7）
_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

#: 预建分区个数（当月 + 后续 n-1 个月）。与 ``0003_audit.py`` 保持一致。
INITIAL_PARTITIONS = 2

#: 兜底分区名。必须**永远存在且排在最后** —— 详见 ``0003_audit.py`` 同名常量的说明。
PMAX_PARTITION = "pmax"


# ---------------------------------------------------------------------------
# 分区助手
# ---------------------------------------------------------------------------
# ⚠️ 这两个小函数与 ``0003_audit.py`` 里的**刻意重复**，不从那边 import：
#    迁移文件必须能独立执行（历史迁移不能因为后来有人重构公共模块而失效）。
def _month_partitions(count: int = INITIAL_PARTITIONS) -> list[tuple[str, str]]:
    """生成 ``[(分区名, 上界日期), ...]``，从**执行月份**起连续 ``count`` 个月。"""
    out: list[tuple[str, str]] = []
    year, month = date.today().year, date.today().month
    for _ in range(max(1, count)):
        next_year, next_month = year + (month // 12), (month % 12) + 1
        out.append((f"p{year:04d}{month:02d}", date(next_year, next_month, 1).isoformat()))
        year, month = next_year, next_month
    return out


def _partition_clause() -> str:
    """拼出 ``PARTITION BY RANGE (TO_DAYS(created_at)) (...)`` 子句。"""
    parts = [
        f"PARTITION {name} VALUES LESS THAN (TO_DAYS('{upper}'))"
        for name, upper in _month_partitions()
    ]
    parts.append(f"PARTITION {PMAX_PARTITION} VALUES LESS THAN MAXVALUE")
    joined = ",\n  ".join(parts)
    return f"PARTITION BY RANGE (TO_DAYS(`created_at`)) (\n  {joined}\n)"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # TBL-23 prompt_template（版本化，只追加新版本）
    # ------------------------------------------------------------------
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("prompt_code", sa.String(64), nullable=False, comment="模板编码"),
        sa.Column("scene", sa.String(32), nullable=False, comment="场景"),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content", sa.Text(), nullable=False, comment="模板正文（含变量占位）"),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("0"),
            comment="状态 0 草稿 1 启用 2 停用",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="维护人"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "prompt_code", "version", "is_deleted", name="uk_tenant_code_ver"
        ),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_scene", "prompt_template", ["tenant_id", "scene", "status"])

    # ------------------------------------------------------------------
    # TBL-24 ai_call_log（只追加 + 按月分区）
    # ------------------------------------------------------------------
    op.create_table(
        "ai_call_log",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="调用时间（分区键，必须进主键）",
        ),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("trace_id", sa.String(64), nullable=True, comment="链路 ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=True, comment="调用用户"),
        sa.Column("scene", sa.String(32), nullable=True, comment="场景"),
        sa.Column(
            "deployment",
            sa.String(16),
            nullable=True,
            comment="本次调用实际部署位置 local/cloud（出网判定，见模型类注释）",
        ),
        sa.Column("provider", sa.String(32), nullable=True, comment="提供方"),
        sa.Column("model", sa.String(64), nullable=True, comment="模型"),
        sa.Column("model_version", sa.String(64), nullable=True, comment="模型版本"),
        sa.Column("prompt_id", sa.BigInteger(), nullable=True, comment="Prompt ID"),
        sa.Column("prompt_version", sa.Integer(), nullable=True, comment="Prompt 版本"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
            comment="成本核算（元）",
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="总耗时 ms"),
        sa.Column("first_token_ms", sa.Integer(), nullable=True, comment="首 token 耗时 ms"),
        sa.Column("cache_hit", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column("fallback_used", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "mask_applied",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否经脱敏出网（F-3.9 审计点）",
        ),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 失败 1 成功",
        ),
        sa.Column("error_code", sa.String(64), nullable=True, comment="错误码"),
        sa.Column("error_msg", sa.String(512), nullable=True, comment="错误信息"),
        sa.PrimaryKeyConstraint("id", "created_at"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_time", "ai_call_log", ["tenant_id", "created_at"])
    op.create_index("idx_trace", "ai_call_log", ["trace_id"])
    op.create_index("idx_tenant_scene", "ai_call_log", ["tenant_id", "scene"])
    # 🔴 唯一事实来源在本文件（见模块 docstring ②）
    op.execute("ALTER TABLE `ai_call_log` " + _partition_clause())

    # ------------------------------------------------------------------
    # TBL-29 ai_model_config（本批新增，见模块 docstring ①）
    # ------------------------------------------------------------------
    op.create_table(
        "ai_model_config",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("scene", sa.String(32), nullable=False, comment="场景码"),
        sa.Column("model_code", sa.String(64), nullable=False, comment="模型配置编码"),
        sa.Column("display_name", sa.String(128), nullable=False, comment="展示名"),
        sa.Column("provider", sa.String(32), nullable=False, comment="ollama/dify/openai/cloud"),
        sa.Column(
            "deployment",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'local'"),
            comment="local 不出网 / cloud 出网（须脱敏）",
        ),
        sa.Column("base_url", sa.String(255), nullable=False, comment="服务地址"),
        sa.Column("model_name", sa.String(128), nullable=False, comment="实际模型名"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("1"), comment="主备顺序"),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default=sa.text("60")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("breaker_threshold", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("breaker_cooldown_seconds", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("price_in_per_1k", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("price_out_per_1k", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("1"),
            comment="状态 0 停用 1 启用",
        ),
        sa.Column("remark", mysql.MEDIUMTEXT(), nullable=True, comment="备注"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "scene", "model_code", "is_deleted", name="uk_tenant_scene_model"
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "idx_tenant_scene_status", "ai_model_config", ["tenant_id", "scene", "status"]
    )

    # ------------------------------------------------------------------
    # TBL-25 agent_task（权威状态 / Checkpointer 落点）
    # ------------------------------------------------------------------
    op.create_table(
        "agent_task",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("task_type", sa.String(32), nullable=False, comment="任务类型"),
        sa.Column("biz_id", sa.BigInteger(), nullable=True, comment="业务 ID"),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("0"),
            comment="0 pending 1 running 2 waiting_human 3 success 4 failed",
        ),
        sa.Column("graph_state", mysql.LONGTEXT(), nullable=True, comment="图状态（JSON）"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_node", sa.String(64), nullable=True, comment="当前节点"),
        sa.Column("idempotent_key", sa.String(64), nullable=True, comment="幂等键"),
        sa.Column(
            "idempotent_result",
            mysql.MEDIUMTEXT(),
            nullable=True,
            comment="幂等回放响应快照（JSON）",
        ),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotent_key", name="uk_tenant_idem"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_status", "agent_task", ["tenant_id", "status"])
    op.create_index("idx_biz", "agent_task", ["biz_id"])

    # ------------------------------------------------------------------
    # TBL-26 agent_step（只追加：无 is_deleted / updated_at）
    # ------------------------------------------------------------------
    op.create_table(
        "agent_step",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("task_id", sa.BigInteger(), nullable=False, comment="Agent 任务 ID"),
        sa.Column("node_name", sa.String(64), nullable=False, comment="节点名"),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_json", mysql.MEDIUMTEXT(), nullable=True, comment="输入（JSON）"),
        sa.Column("output_json", mysql.MEDIUMTEXT(), nullable=True, comment="输出（JSON）"),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="耗时 ms"),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("0"),
            comment="状态 0 失败 1 成功",
        ),
        sa.Column("error_msg", sa.String(512), nullable=True, comment="错误信息"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_task_step", "agent_step", ["task_id", "step_index"])

    # ------------------------------------------------------------------
    # TBL-27 agent_interrupt（人工介入待办）
    # ------------------------------------------------------------------
    op.create_table(
        "agent_interrupt",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键（雪花 ID）"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("task_id", sa.BigInteger(), nullable=False, comment="Agent 任务 ID"),
        sa.Column("node_name", sa.String(64), nullable=False, comment="中断节点名"),
        sa.Column(
            "interrupt_type",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'generic'"),
            comment="待办类型（本批补充，见模块 docstring ③）",
        ),
        sa.Column("payload_json", mysql.MEDIUMTEXT(), nullable=True, comment="待办载荷（JSON）"),
        sa.Column("assignee_role", sa.String(32), nullable=True, comment="指派角色"),
        sa.Column(
            "status",
            mysql.TINYINT(),
            nullable=False,
            server_default=sa.text("0"),
            comment="0 pending 1 approved 2 rejected",
        ),
        sa.Column("decided_by", sa.BigInteger(), nullable=True, comment="处理人"),
        sa.Column("decided_at", sa.DateTime(), nullable=True, comment="处理时间"),
        sa.Column("comment", sa.String(512), nullable=True, comment="处理意见"),
        sa.Column("idempotent_key", sa.String(64), nullable=True, comment="幂等键"),
        sa.Column("is_deleted", mysql.TINYINT(1), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        **_TABLE_KW,
    )
    op.create_index("idx_tenant_status", "agent_interrupt", ["tenant_id", "status"])
    op.create_index("idx_task", "agent_interrupt", ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_task", table_name="agent_interrupt")
    op.drop_index("idx_tenant_status", table_name="agent_interrupt")
    op.drop_table("agent_interrupt")

    op.drop_index("idx_task_step", table_name="agent_step")
    op.drop_table("agent_step")

    op.drop_index("idx_biz", table_name="agent_task")
    op.drop_index("idx_tenant_status", table_name="agent_task")
    op.drop_table("agent_task")

    op.drop_index("idx_tenant_scene_status", table_name="ai_model_config")
    op.drop_table("ai_model_config")

    op.drop_index("idx_tenant_scene", table_name="ai_call_log")
    op.drop_index("idx_trace", table_name="ai_call_log")
    op.drop_index("idx_tenant_time", table_name="ai_call_log")
    op.drop_table("ai_call_log")

    op.drop_index("idx_tenant_scene", table_name="prompt_template")
    op.drop_table("prompt_template")
