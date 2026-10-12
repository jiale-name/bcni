# Alembic 迁移链说明（CU-02）

> ⚠️ **`backend/alembic.ini` 必须保持纯 ASCII，不得写入中文字符。**
>
> 原因（已实测踩坑）：Alembic 用 `configparser` 以 `encoding="locale"` 读取 ini 文件。
> 在中文 Windows 上 locale 编码是 **GBK**，ini 中只要出现一个中文字符，Alembic 就会在
> 真正开始工作**之前**抛出：
>
> ```
> UnicodeDecodeError: 'gbk' codec can't decode byte 0x89 in position 105: illegal multibyte sequence
> ```
>
> 表现为「`alembic` 命令完全不可用」。所以：**ini 写英文，中文说明写在本文件**。

---

## 1. 目录职责

| 文件 | 作用 |
| --- | --- |
| `alembic.ini` | Alembic 配置。**`sqlalchemy.url` 故意留空** —— 连接串只有一个来源：`app/config.py` |
| `env.py` | 运行环境：把 `settings.database_url` 注入 Alembic，并把 `app.models.Base.metadata` 作为比对基准 |
| `script.py.mako` | 新迁移文件的模板（已加 `from __future__ import annotations`，风格与项目一致） |
| `versions/` | 迁移脚本（**唯一的事实来源**；数据库设计文档的 DDL 仅作字段参考） |

## 2. 三条铁律

1. 🔴 **ORM 模型是数据库结构的唯一事实来源。**
   变更流程固定为：**改 ORM 模型 → 生成迁移 → Code Review → 发布**。
   **禁止手工改库**，也禁止「先改库再补迁移」——否则环境之间必然漂移。
2. 🔴 **`app/models/__init__.py` 必须显式 import 每一张表的模型。**
   否则该表不会注册进 `Base.metadata`，autogenerate 会认为它「不存在」并生成
   `op.drop_table(...)` —— 这是最危险的 autogenerate 事故。
3. 🔴 **新增配置项不得写进 `alembic.ini`。** 连接串与运行参数一律经 `app.config.settings`，
   保证「迁移连的库 == 应用连的库」。

## 3. 常用命令

在 `backend/` 目录下执行：

```bash
# 生成迁移（需连库比对；只想看差异可先跑 --sql）
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "add talent_base"

# 应用 / 回退
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade -1

# 查看当前版本 / 历史
.venv/Scripts/python.exe -m alembic current
.venv/Scripts/python.exe -m alembic history --verbose

# 🔴 离线生成 DDL：不连数据库即可产出完整 SQL
#    用途：① 无库环境下评审迁移；② CI 校验迁移是否可编译；③ 交 DBA 审阅
.venv/Scripts/python.exe -m alembic upgrade head --sql > ddl.sql
```

> **离线模式的实现**：`env.py` 的 `run_migrations_offline()` 分支使用
> `context.configure(url=..., literal_binds=True)`，因此不需要建立连接。

## 4. 迁移书写规范

| 规范 | 说明 |
| --- | --- |
| 主键 | 雪花 ID：`sa.BigInteger, primary_key=True, autoincrement=False`，**绝不用 `AUTO_INCREMENT`** |
| 租户列 | `tenant_id` 必须存在，且是**所有联合索引的最左列** |
| 唯一约束 | **必须带 `is_deleted`**，否则软删后无法重建同名记录 |
| 软删 | `is_deleted` + `deleted_at`；业务表一律继承 `SoftDeleteMixin` |
| 时间列 | `server_default=sa.text("CURRENT_TIMESTAMP")`；`updated_at` 带 `ON UPDATE CURRENT_TIMESTAMP` |
| 表参数 | 一律 `mysql_engine="InnoDB"`、`mysql_charset="utf8mb4"`、`mysql_collate="utf8mb4_unicode_ci"` |
| 长字符串索引 | `VARCHAR(512)` 等需用前缀索引，如 `mysql_length={"dept_path": 191}` |
| 外键 | 一期**不建物理外键**（跨库 Milvus 无法建 FK，且与软删语义冲突），按「逻辑外键 + 应用层校验」 |
| 追加型日志表 | 审计 / AI 调用日志 / 变更留痕**只追加**：无 `is_deleted` / `updated_at`（`AppendOnlyMixin`） |
| `downgrade()` | 必须写，且**先删依赖方再删被依赖方**；破坏性迁移在 docstring 中显式警告 |

## 5. 当前迁移链

| revision | 内容 | 覆盖表 |
| --- | --- | --- |
| `0001_basement` | 一期基座（架构 §4.1「基座·权限」） | TBL-01 `tenant` / TBL-02 `sys_user` / TBL-03 `sys_role` / TBL-04 `sys_permission` / TBL-05 `sys_user_role` / TBL-06 `sys_role_permission` / TBL-07 `org_dept` / TBL-08 `position` |

> TBL-09 ~ TBL-28 随各自编码单元（CU-07 / CU-11 / CU-12 / CU-13 / CU-14 / CU-17 / CU-22 …）
> 以**追加迁移**的方式落地，❌ 不修改已发布的 `0001_basement`。
