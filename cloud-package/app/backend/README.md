# AI 数字化人才平台 · 后端（一期）

> 2026 顶配版 AI 数字化人才平台 —— 后端服务。
> 文档集见仓库根 `docs/`，**编码前评审文档** `docs/AI数字化人才平台-编码前评审文档.md` 是编码门禁与进度台账。

---

## 1. 当前进度（对照评审文档 §7 台账）

| 编码单元 | 内容 | 设计状态 | 编码状态 |
| --- | --- | --- | --- |
| **CU-01** | 后端六层骨架 + 配置 + 入口 | 完成设计 | ✅ 已完成 |
| **CU-02** | ORM 基类 + 雪花 ID + Alembic 迁移链 | 完成设计 | ✅ 已完成 |
| **CU-03** | 统一响应 / 异常 / 错误码表 | 完成设计 | ✅ 已完成 |
| **CU-04** | 认证与 JWT（API-01 ~ API-06） | 完成设计 | ✅ 已完成 |
| **CU-05** | RBAC 三层权限 + 数据域裁剪（API-18 ~ API-22） | 完成设计 | ✅ 已完成 |
| **CU-06** | 组织 / 岗位 / 用户 CRUD（API-07 ~ API-17） | 完成设计 | ✅ 已完成 |
| **CU-07** | 数据字典 CRUD（API-23 ~ API-27） | 完成设计 | ✅ 已完成 |
| **CU-08** | 审计日志（只追加 + 分区，API-28 / API-29） | 完成设计 | ✅ 已完成 → **§10** |
| **CU-09** | 限流熔断 + 降级响应（42901） | 完成设计 | ✅ 已完成 → **§10.3** |
| **CU-10** | 模型网关（本地 + 云端路由，API-81 ~ API-83） | 完成设计 | ✅ 已完成 → **§11** |
| **CU-11** | Prompt 版本管理（API-84 ~ API-87） | 完成设计 | ✅ 已完成 → **§11** |
| **CU-12** | AI 调用日志与成本看板（API-88 / API-89） | 完成设计 | ✅ 已完成 → **§11** |
| **CU-13** | Agent 骨架 + Checkpointer + 人工介入（API-90 ~ API-95） | 完成设计 | ✅ 已完成 → **§11** |
| **CU-14** | 简历批量导入 + 任务进度 SSE（API-36 ~ API-43） | 完成设计 | ✅ 已完成 |
| **CU-15** | 字段抽取 + 字典归一 + 置信度（API-44） | 完成设计 | ✅ 已完成（LLM 抽取器待金标准集定稿） |
| **CU-16** | 分层复核漏斗 + 复核工作台（API-44 ~ API-49） | 完成设计 | ✅ 已完成 |
| **CU-17** | 受控标签词表 + AI 选标签闭环（API-50 ~ API-56） | 完成设计 | ✅ 已完成 |
| **CU-18** | 向量画像 + Embedding 流水线（COL-01 ~ COL-03） | 正在设计 | 🔵 非缺口部分已编码（真实向量化待 **H-19**） |
| **CU-19** | 查重与合并（含回滚，API-57 ~ API-61） | 完成设计 | ✅ 已完成 |
| **CU-20** | 语义检索（混合召回 + RRF + Rerank，API-62） | 完成设计 | ✅ 已完成 |
| **CU-21** | 档案 RAG 问答 + 脱敏回填（API-63） | 完成设计 | ✅ 已完成 |
| **CU-22** | 变更留痕 + 个人权利响应（API-67 ~ API-80） | 完成设计 | ✅ 已完成 → **评审文档 §7.3 批次 13** |
| **CU-24** | 部署（Compose / Nginx / 分区任务 / 审计账号 / 归档） | 正在设计 | 🔵 **开发环境已交付**（生产三区待 Q-3）→ **`deploy/README.md`** |
| **CU-27** | N0 前置实测与模型就绪验证（10 探针 + 报告自动生成） | 完成设计 | 🔵 进行中（首轮已跑通；3 项阻塞待环境就绪）→ **§9.1** |
| **CU-28** | 金标准集工具链（≥200 份简历 / ≥50 条查询） | 正在设计 | 🔵 进行中（工具链就绪；标注待甲方启动）→ **§9.2** |
| CU-23 · CU-25 · CU-26 | 前端 19 页 / 开发计划 / 测试用例 | 详见评审文档 | ⏳ 未开始（CU-23 **前置「原型形态定稿」未解除**） |

> ✅ **N1 应用基座（CU-01 ~ CU-09）已全部交付**（2026-09-22）。
> ✅ **AI 中台 + Agent 骨架（CU-10 ~ CU-13）已交付**（批次 7）。
> ✅ **档案链路（CU-14 ~ CU-17）已交付**（批次 10）· ✅ **查重合并（CU-19）已交付**（批次 11）。
> ✅ **检索与问答（CU-20 / CU-21）已交付**（批次 12，一期技术难度最高的一批）。
> ✅ **变更留痕 + 个人权利响应（CU-22）已交付**（批次 13，2026-09-23）—— **一期 F-3 后端功能面至此全部交付**；🔵 **CU-18 只交付非缺口部分**（抽象层 + 占位实现，真实 Embedding 待 H-19）。
> 🔵 **CU-24 只完成「开发环境」** —— 生产三区分离 compose / MySQL 主从 / HTTPS 待 Q-3 与证书来源。
> ⏳ 下一步：**第 7 批 CU-23 前端**（⚠️ 前置「原型形态定稿」未解除）；可并行的是 **CU-26 测试用例**与 **CU-28 标注启动**（N6 关键路径）。
> ✅ **「LangGraph 是否替换自研图运行时」已拍板 = 一期沿用自研**（评审文档 §7.3 批次 8/9，论证见 `docs/图运行时选型评估-自研运行时与LangGraph.md`），并已把**迁移控制项**落地为机械守卫（§11.5）。
>
> ⚠️ **本 README 的两处滞后已登记（不属本批遗漏）**：① **§5 接口速查缺 API-30 ~ API-80**（CU-14 ~ CU-22 的接口，仅覆盖 API-01~29 与 API-81~95）；② 历史章节（§8 排障 / §9~§12）按批次增量补，**CU-14 ~ CU-22 的专题说明尚未成章**。**状态事实源始终是评审文档 §7 台账与本表**，缺口见 §7。
> 结构守卫见 `tests/test_layering.py`（分层反向依赖 / 按文件落地清单 / `deploy/` 产物存在 / 限流接线真存在）。
> 🔴 **图运行时红线守卫**见 `tests/test_graph_replay_safety.py`（14 用例）：中断节点不得在 `interrupt` 之前产生副作用 + 声明必须与代码一致。

> ⚠️ 每次编码后必须更新评审文档 §7 台账的三态标注（完成设计 / 正在设计 / 未进行设计）。
> 📌 **CU-27 / CU-28 即评审文档 §7.2 的「第 0 批」**：与编码主力**并行、不占批次**，
> 但**必须先启动** —— 金标准集以**人天**计且是 N6 的硬阻塞（评审文档 §7.3 批次 5）。

---

## 2. 技术栈与关键约定

| 项 | 选型 / 约定 | 出处 |
| --- | --- | --- |
| Web 框架 | FastAPI（**同步路由**，由线程池承接 IO） | 架构 §2.2 |
| ORM | SQLAlchemy 2.0 同步 + PyMySQL | 架构 §5 |
| 迁移 | Alembic（ORM 模型是**唯一事实来源**，禁止手工改库） | 数据库 §0 |
| 校验 / 配置 | Pydantic v2 + pydantic-settings | 架构 §5 |
| 缓存 | Redis（**只存可丢数据**；权威状态一律落 MySQL） | 数据库 §1 |
| 主键 | 雪花 ID（应用生成，有符号 BIGINT） | 数据库 §0.1 |
| 响应 | `{code, message, data, page, trace_id}`，**成功判定看 `code == 0`** | 接口 §1.1 |
| ID 序列化 | 🔴 **所有 ID 对外一律字符串**（JS 精度坑） | 接口 §1.2 |
| 降级 | 第三方异常 → HTTP 200 + `code=50301/50302` + `data.degraded=true`，**严禁抛 50000** | 接口 §1.5 |

### 六层架构与依赖方向（只能向下）

```
routers  →  services  →  dao  →  models
   ↓            ↓          ↓
 只声明绑定    业务/事务    纯 CRUD + 强制租户条件
```

- **Router 不得写业务**（判定、事务一律在 Service）；
- **DAO 不得写规则**（只做持久化，且**每个查询必须带 `tenant_id` + `is_deleted`**）；
- 跨库操作（MySQL + Milvus）的补偿顺序在 Service。

---

## 3. 目录结构

```
backend/
├── alembic/                      # 迁移链（env.py 复用 app.config 的连接串）
│   └── versions/0001_init_basement.py   # TBL-01 ~ TBL-08（基座 8 张表）
├── app/
│   ├── config.py                 # 唯一配置入口（禁止业务代码读 os.environ）
│   ├── main.py                   # 应用工厂 + lifespan + /health
│   ├── core/                     # 横切关注点
│   │   ├── context.py            #   trace_id / user_id / ip / ua（contextvars）
│   │   ├── logging.py            #   结构化日志 + 敏感字段脱敏
│   │   ├── middleware.py         #   TraceIdMiddleware（纯 ASGI）+ RequestMetaMiddleware（ip/ua）
│   │   │                         #   + RateLimitMiddleware（CU-09，42901 + 响应头 + fail-open）
│   │   ├── security.py           #   bcrypt / JWT / 依赖（get_current_principal / require_perm）
│   │   ├── snowflake.py          #   雪花 ID（含时钟回拨保护）
│   │   ├── crypto.py             #   AES-256-GCM（PII）+ 联系方式加盐哈希（CONTACT_HASH_SALT）
│   │   ├── tenant.py             #   CurrentPrincipal / DataScopeFilter（行级过滤唯一来源）
│   │   ├── ratelimit.py          #   登录锁定 + HTTP 限流（fail-open）
│   │   ├── model_gateway.py      # 🔴 CU-10 模型网关（priority 链 + 熔断 + 降级 + **永不抛异常**）
│   │   ├── cost.py               # 🔴 CU-10 成本核算（Decimal + ROUND_HALF_UP）
│   │   └── threshold.py          # 🔴 CU-13 确定性阈值引擎（LLM 不参与数值结论 —— 铁律 10）
│   ├── exceptions/               # ErrorCode 全量 + 统一异常处理器 + build_error_response（中间件共用）
│   ├── models/                   # ORM（数据结构唯一事实来源）：base / enums / tenant / user / dict
│   │                             #   / audit（CU-08，复合主键 + 无软删列）
│   │                             #   / ai（CU-10/11/12：ai_model_config=TBL-29、prompt_template、
│   │                             #        ai_call_log 含 deployment 列 + 月份分区）
│   │                             #   / agent（CU-13：agent_task / agent_step / agent_interrupt）
│   ├── schemas/                  # Pydantic 契约：common / auth / rbac / org / user_admin / dict
│   │                             #   / audit / ai（API-81~89）/ agent（API-90~95）
│   ├── dao/                      # session / redis / user_dao / token_dao / rbac_dao / org_dao
│   │                             #   / user_admin_dao / dict_dao / audit_dao（🔴 只增查，无改删）
│   │                             #   / ai_dao（含 aggregate_outbound_mask，deployment 必填 keyword-only）
│   │                             #   / agent_dao（含 get_interrupt(*, refresh) —— 防陈旧回显）
│   ├── services/                 # auth / rbac / org_scope / org / user_admin / dict / audit
│   │                             #   / ai（模型配置）/ prompt（版本与回滚）/ ai_log（看板与脱敏率）
│   │                             #   / agent（启动 / 恢复 / 重试 / 幂等 / 步骤落库）
│   ├── agents/                   # CU-13 Agent 运行时（自研；LangGraph 适配器为**可选延迟导入**）
│   │   ├── graph.py              #   图结构 / 节点 / 边 / 中断 / checkpointer 协议
│   │   ├── checkpointer.py       #   图状态读写（权威状态落 MySQL）
│   │   ├── parse_graph.py        #   简历解析图（6 步 → 中断 → persist_talent / manual_reject）
│   │   └── registry.py           #   图注册表（按 task_type 取图）
│   └── routers/                  # auth（API-01~06）/ org（07~12）/ user（13~17）
│                                 #   / rbac（18~22）/ dict（23~27）/ audit（28~29，🔴 无写端点）
│                                 #   / ai（81~89）/ agent（90~95，🔴 **无「创建任务」端点**）
├── alembic/versions/             # 0001_basement（基座 8 表）→ 0002_dict（TBL-09/10）
│                                 #   → 0003_audit（TBL-28 审计 + RANGE 分区）
│                                 #   → 0004_ai_agent（TBL-23~27 + TBL-29 + ai_call_log 分区）
├── scripts/                      # 工具层（🔴 只允许 scripts → app 单向依赖，禁止被 app 反向导入）
│   ├── _console.py               # Windows UTF-8 控制台（各 CLI 共用）
│   ├── seed_dev.py               # 开发种子：租户/7 预置角色/权限点/17 字典类型/AI 模型配置/Prompt/首个管理员（幂等）
│   ├── n0_probe.py               # CU-27 CLI：N0 前置实测（--list / --only / --out / --embedding-rounds）
│   ├── n0/                       # CU-27：result（四态模型）/ targets（指标基线）/ probes（10 探针）/ report
│   ├── goldset.py                # CU-28 CLI：build / validate / status / seal
│   ├── goldset/                  # CU-28：schema（字段口径）/ build（模板）/ validate（18 条质检）/ seal（脱敏）
│   └── audit_partition.py        # CU-08/CU-24 CLI：分区维护（--list / --ensure-next / --drop-before / --table all）
├── tests/                        # 契约测试（无外部依赖可跑）+ 集成测试（自动 skip）
│   ├── test_access_control.py    # 🔴 CU-05 越权用例集 AC-01 ~ AC-40（评审文档 §7.4）
│   ├── test_org_user_crud.py     # CU-06 组织 / 岗位 / 用户（含 AC-34 端点级 40401 补）
│   ├── test_dict_crud.py         # CU-07 数据字典（含种子↔代码枚举一致性、权限矩阵静态钉死）
│   ├── test_n0_probe.py          # CU-27 注册表 / 状态语义 / 退出码矩阵 / 报告结构 / **不泄露凭据**
│   ├── test_goldset.py           # CU-28 字段口径 / 模板 / 18 条质检规则 / 脱敏
│   ├── test_contact_hash.py      # 联系方式比对哈希：归一化 / 加盐 / 无盐即报错
│   ├── test_audit.py             # 🔴 CU-08 只追加三层结构 / 脱敏 / CSV 注入 / 42901 / 分区月份算术（61 例）
│   ├── test_ai_gateway.py        # 🔴 CU-10 网关：键集完整 / 主备切换 / 熔断 / 重试 / 成本 Decimal（39 例）
│   ├── test_prompt_versions.py   # 🔴 CU-11 Prompt：只追加 / 族内唯一启用 / 回滚=追加新版本（54 例）
│   ├── test_ai_log.py            # 🔴 CU-12 日志与看板：脱敏率分母 / 成本标记 / 租户隔离含 summary（51 例）
│   ├── test_agent_flow.py        # 🔴 CU-13 Agent 端点：三态并发 40903/40902 / 重试起点 / 审计（40 例）
│   ├── test_agent_graph.py       # 🔴 CU-13 图运行时：节点 / 中断 / 恢复 / checkpointer（43 例）
│   ├── test_layering.py          # 🔴 结构守卫：分层反向依赖 / 按文件落地清单 / deploy 产物 / 限流接线（19 例）
│   └── ...                       # 其余为 CU-01~04 的契约/集成测试
├── requirements.txt
├── .env.example
└── alembic.ini
```

> 📁 **不在 `backend/` 内的目录**（架构 §7 目录结构）：
> - `<仓库根>/deploy/` —— **部署产物**（compose / Nginx / 分区定时任务 / 审计专用库账号 / 归档脚本），
>   由 `tests/test_layering.py::PROJECT_FILES` 断言存在。使用说明见 **`deploy/README.md`** 与本文 **§12**；
> - `<仓库根>/data/goldset/` —— 金标准集标注文件（🔴 含个人信息，**禁止提交到任何代码仓库**）；
> - `<仓库根>/docs/` —— 自动生成的 `N0-基线实测报告-YYYY-MM-DD.md` / `.json`（跑一次刷一次）。

---

## 4. 本地启动

### 4.1 准备环境

```bash
cd backend

# ① 创建虚拟环境（Python ≥ 3.11）
python -m venv .venv

# ② 安装依赖（国内建议走镜像）
.venv/Scripts/python.exe -m pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

# ③ 复制环境变量模板并按需修改
cp .env.example .env
# 🔴 必须填的两项：
#    JWT_SECRET_KEY  签名密钥（生成：python -c "import secrets; print(secrets.token_urlsafe(48))"）
#    AES_KEY         PII 加密密钥，64 位 hex（生成：python -c "import secrets; print(secrets.token_hex(32))"）
#                    ⚠️ CU-06 起用户 CRUD 会读它；**没配就写手机号/邮箱会直接报错**（这是刻意的，见 §6 红线）
#    本地 MySQL 无密码则保持 MYSQL_PASSWORD 为空
```

### 4.2 准备数据库

```sql
-- MySQL 需先手工建库（Alembic 只建表，不建库）
CREATE DATABASE talent_platform_v2
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> 🔴 **库名为何不是设计文档 DS-1 写的 `talent_platform`**
>
> 本机 `talent_platform` **已被一套 2026-08-30 的旧 schema 连同真实数据占用**
> （11 张表：`sys_user` / `talent_profile` / `assessment_*` / `match_record` / `job_position` …，
> 主键为 `AUTO_INCREMENT`、无 `tenant_id`），其中旧 `sys_user` 与本项目迁移要建的
> `sys_user` **同名不同构** —— 直接建表必然冲突。
>
> 处置：**非破坏性** —— 本项目改用 `talent_platform_v2`，旧库一行不动。
> 库里唯一的事实来源是 `.env` 的 `MYSQL_DATABASE`（`app/config.py` 的默认值也已同步改为安全值，
> 避免有人漏配 `.env` 后顺手 migrate 到旧库上）。
> 若确认旧库可弃，把 `MYSQL_DATABASE` 改回 `talent_platform` 并重建库即可回归 DS-1，**代码无需改动**。

```bash
# 建表（首次）
.venv/Scripts/python.exe -m alembic upgrade head

# 查看当前版本
.venv/Scripts/python.exe -m alembic current

# 【推荐】不连库即可产出 DDL，用于评审/CI 校验
.venv/Scripts/python.exe -m alembic upgrade head --sql
```

### 4.3 启动服务

```bash
# 🔴 不带 --reload：便于观察代码是否真正生效（进程启停由人工管理）
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

| 地址 | 说明 |
| --- | --- |
| http://127.0.0.1:8002/docs | Swagger UI |
| http://127.0.0.1:8002/redoc | ReDoc |
| http://127.0.0.1:8002/openapi.json | OpenAPI 描述 |
| http://127.0.0.1:8002/health | 健康检查（依赖不可用返回 200 + `code=50301`） |

> ⚠️ **启动阶段不连接数据库**（`create_engine` 是惰性的）。库没起也能拉起服务看 OpenAPI，
> 便于前端联调 —— 但登录等接口会明确报 40102/50001，不会静默成功。

### 4.4 跑测试

```bash
.venv/Scripts/python.exe -m pytest -v
```

- **契约类测试**（雪花 ID 序列化、统一响应、JWT、异常码、越权用例 AC-01~40 的 L1 部分）：**任何环境都执行**；
- **集成类测试**（登录链路、RBAC 端到端）：需要可用的 MySQL，缺依赖时自动 `skip`（不是 fail）。

**当前规模**：**421 用例**（0 失败 / 0 错误 / 2 skip —— 2 个 skip 是 AC-20/21 字段级脱敏，待 CU-14/15）。

🔴 **本机判「是否全绿」的正确姿势（踩过两次坑）**：

| 别信 | 为什么 |
| --- | --- |
| ❌ 终端汇总行（`N passed, M skipped`） | 本机终端钩子会**把它隐藏**（不在 stdout / 重定向文件里） |
| ❌ 进程退出码 | pytest 收尾清理临时目录会触发本机安全钩子（`[safe-delete] SAFE_DELETE_BULK_CONFIRM_REQUIRED`），**退出码变非 0 但用例全过** |

✅ **只认 junit 的计数**：

```bash
.venv/Scripts/python.exe -m pytest tests/ --junitxml=/tmp/result.xml -q
.venv/Scripts/python.exe -c "
import xml.etree.ElementTree as ET
r = ET.parse(r'/tmp/result.xml').getroot(); s = r if r.tag=='testsuite' else r[0]
print('tests=%s failures=%s errors=%s skipped=%s' % (s.get('tests'), s.get('failures'), s.get('errors'), s.get('skipped')))
"
```

⚠️ `--basetemp` **不能**指向重定向输出的目录 —— pytest 启动时会清空 basetemp，把输出文件一起删掉。

### 4.5 初始化种子数据（首次必做）

🔴 **不做这一步就无法登录** —— 库里没有任何租户/账号，而「新建用户」接口本身又需要先登录，
这是一个死锁。故首个租户与管理员只能由脚本提供。

```bash
# 自动生成管理员密码并打印（推荐）
.venv/Scripts/python.exe scripts/seed_dev.py

# 指定账号密码（联调 / 自动化用）
.venv/Scripts/python.exe scripts/seed_dev.py --username admin --password 'Admin@12345'

# 已存在时重设密码（其余数据不动）
.venv/Scripts/python.exe scripts/seed_dev.py --reset-password --password 'NewPass@123'
```

脚本会写入：**1 个租户 + 7 个预置角色（ADM/EXEC/HR/MGR/EMP/AIOPS/AUDIT）+ 92 个权限点 + 1 个根部门 + 17 个字典类型（含 59 个字典项）+ 1 个管理员（绑定 ADM）**。

- **幂等**：可重复执行，已存在的对象一律复用（不会重置手工改过的数据）；
- **前置**：需先完成 §4.2 的 `alembic upgrade head`，否则报「表不存在」；
- ⚠️ 生产环境**不要**用本脚本，应通过正式的用户导入流程建号。

---

## 5. 接口速查（API-01 ~ API-95）

| 接口 | 方法 | 路径 | 权限 | 幂等 |
| --- | :-: | --- | --- | :-: |
| API-01 登录 | POST | `/api/v1/auth/login` | 公开 | 否 |
| API-02 刷新 Token | POST | `/api/v1/auth/refresh` | 持 refresh_token | 否 |
| API-03 登出 | POST | `/api/v1/auth/logout` | 已登录 | **是** |
| API-04 当前用户 | GET | `/api/v1/auth/me` | 已登录 | 否 |
| API-05 权限快照 | GET | `/api/v1/auth/permissions` | 已登录 | 否 |
| API-06 修改本人密码 | PUT | `/api/v1/auth/password` | 已登录 | 否 |
| API-07 组织架构树 | GET | `/api/v1/orgs/tree` | `org:view` | 否 |
| API-08 新建部门 | POST | `/api/v1/orgs` | `org:create` | 可选 |
| API-09 修改部门（含移动） | PUT | `/api/v1/orgs/{id}` | `org:update` | 否 |
| API-10 删除部门 | DELETE | `/api/v1/orgs/{id}` | `org:delete` | 否 |
| API-11 岗位列表 | GET | `/api/v1/positions` | `position:view` | 否 |
| API-12 新建岗位 | POST | `/api/v1/positions` | `position:create` | 可选 |
| API-13 用户列表（分页） | GET | `/api/v1/users` | `user:view` | 否 |
| API-14 新建用户 | POST | `/api/v1/users` | `user:create` | 可选 |
| API-15 修改用户 | PUT | `/api/v1/users/{id}` | `user:update` | 否 |
| API-16 分配角色 | PUT | `/api/v1/users/{id}/roles` | `user:role` | 否 |
| API-17 批量导入用户 | POST | `/api/v1/users/import` | `user:import` | 可选 |
| API-18 角色列表 | GET | `/api/v1/roles` | `rbac:role:list` | 否 |
| API-19 新建角色 | POST | `/api/v1/roles` | `rbac:role:create` | 可选 |
| API-20 修改角色 | PUT | `/api/v1/roles/{id}` | `rbac:role:update` | 否 |
| API-21 权限点树 | GET | `/api/v1/permissions/tree` | `rbac:perm:tree` | 否 |
| API-22 绑定权限点 | PUT | `/api/v1/roles/{id}/permissions` | `rbac:role:bind` | 否 |
| API-23 字典类型列表 | GET | `/api/v1/dicts` | `dict:type:list` | 否 |
| API-24 字典项列表 | GET | `/api/v1/dicts/{type_code}/items` | **已登录**（无权限码） | 否 |
| API-25 新增字典项 | POST | `/api/v1/dicts/{type_code}/items` | `dict:create` | 可选 |
| API-26 修改字典项 | PUT | `/api/v1/dict-items/{id}` | `dict:update` | 否 |
| API-27 停用字典项 | DELETE | `/api/v1/dict-items/{id}` | `dict:update` | **是** |
| API-28 审计日志查询（分页） | GET | `/api/v1/audit-logs` | `audit:view` | 否 |
| API-29 审计日志导出（CSV） | POST | `/api/v1/audit-logs/export` | `audit:export` | **是** |

> 🔴 **API-28/29 没有写接口** —— 审计表**只追加**：不存在「新增/修改/删除审计」的端点，
> DAO 也没有 update/delete 函数（由 `tests/test_audit.py` 静态断言钉死）。详见 **§10**。

> **两种列表形状**（接口 §1.1 / §2.2）：
> - **分页列表**（仅 API-13）→ `data` 是**数组**，分页信息在**同级** `page = {total, page, per_page, pages}`；
> - **非分页列表**（API-07/11/18/21/23/24）→ `data = {total, items}`；字典沿用后者
>   （类型量级 10¹、项 10²，分页只增加调用复杂度而无收益）。
>
> `per_page > 100` 时**按 100 处理**并在 `page.per_page` 回显实际值（**不是**报错）。

**字典接口的三个「反直觉」约定**（都对齐接口 §4 矩阵，不是随手写的）：

| 约定 | 为什么 |
| --- | --- |
| API-23 用 `dict:type:list`，**不复用** `dict:view` | `dict:view` 被广授给 HR/MGR/EMP/EXEC（供下拉与菜单），拿它把关会让这 4 个角色也能列类型 → 与矩阵「仅 ADM」不符 |
| API-24 **不挂权限码**（仅要求登录） | 接口 §2.2 给 API-24 标注的权限就是「已登录」，矩阵也是 7 角色全 ✓；挂 `dict:view` 会把 AIOPS/AUDIT 拒掉 |
| API-27 复用 `dict:update` | 矩阵里**没有** `dict:disable` 这个动作，且「停用」就是改 `status` 字段（接口标题自己写着「不物理删」）；凭空造权限点会留下「有权限点、无对应动作」的悬空项 |

```bash
# 登录
curl -s -X POST http://127.0.0.1:8002/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@123456","tenant_code":"demo"}'

# 带 Token 访问
curl -s http://127.0.0.1:8002/api/v1/auth/me -H "Authorization: Bearer <access_token>"

# 组织树 / 用户分页
curl -s http://127.0.0.1:8002/api/v1/orgs/tree -H "Authorization: Bearer <t>"
curl -s 'http://127.0.0.1:8002/api/v1/users?page=1&per_page=20' -H "Authorization: Bearer <t>"
```

### 5.1 AI 中台与 Agent（API-81 ~ API-95，CU-10 ~ CU-13）

| 接口 | 方法 | 路径 | 权限 | 幂等 |
| --- | :-: | --- | --- | :-: |
| API-81 模型配置列表 | GET | `/api/v1/ai/models` | `ai:model:list`（AIOPS） | 否 |
| API-82 修改模型配置与主备路由 | PUT | `/api/v1/ai/models/{model_id}` | `ai:model:update`（AIOPS） | 否 |
| API-83 模型健康与熔断状态 | GET | `/api/v1/ai/models/health` | ADM / AIOPS | 否 |
| API-84 Prompt 模板列表（含版本） | GET | `/api/v1/ai/prompts` | `ai:prompt:list`（AIOPS） | 否 |
| API-85 新建 Prompt 模板 | POST | `/api/v1/ai/prompts` | `ai:prompt:create`（AIOPS） | 可选 |
| API-86 修改 Prompt（**生成新版本**） | PUT | `/api/v1/ai/prompts/{prompt_id}` | `ai:prompt:update`（AIOPS） | 否 |
| API-87 回滚到指定版本（**追加新版本**） | POST | `/api/v1/ai/prompts/{prompt_id}/rollback` | `ai:prompt:update`（AIOPS） | 否 |
| API-88 AI 调用日志（分页） | GET | `/api/v1/ai/call-logs` | `ai:log:view`（ADM / AIOPS / AUDIT） | 否 |
| API-89 Token 成本看板 | GET | `/api/v1/ai/cost-dashboard` | `ai:cost:view`（ADM / AIOPS / EXEC） | 否 |
| API-90 Agent 任务列表 | GET | `/api/v1/agent/tasks` | `agent:task:list`（ADM / HR / AIOPS） | 否 |
| API-91 任务详情 + 图状态 | GET | `/api/v1/agent/tasks/{task_id}` | `agent:task:view` | 否 |
| API-92 步骤回放 | GET | `/api/v1/agent/tasks/{task_id}/steps` | `agent:task:view` | 否 |
| API-93 人工介入待办列表 | GET | `/api/v1/agent/interrupts` | `agent:interrupt:list`（ADM / HR / AIOPS） | 否 |
| ⭐API-94 处理人工待办（+ 恢复执行） | POST | `/api/v1/agent/interrupts/{interrupt_id}/resolve` | `agent:interrupt:resolve` | **是** |
| API-95 重试失败任务 | POST | `/api/v1/agent/tasks/{task_id}/retry` | `agent:task:retry`（**HR 没有**，ADM / AIOPS） | 否 |

> 🔴 **没有「创建 Agent 任务」的接口** —— 任务由业务动作（如导入解析）在服务端启动。
> 暴露一个「随便起个任务」的端点等于给外部一个**无需业务前提的执行入口**。
>
> 🔴 **API-94 的三态必须分清**（前端动作完全相反）：首次提交 `code=0` · **同幂等键重放 → 40903**
> （返回**首次结果**，当作成功继续）· **别人已处理 → 40902**（`data.handled_by_name` / `handled_at`，
> 要「刷新后重试」）。**幂等判定先于状态判定** —— 顺序颠倒会把「你的通过已生效」误报成「别人已处理」。
> 幂等键走请求头 `Idempotency-Key`；**空串 / 全空白视为「未提供」**（否则所有未填的请求会共用同一个键）。
>
> 🔴 **API-95 重试从「当前节点」继续**（已成功的步骤不重跑）；`approved` / `waiting_human` 状态的任务重试 → **42201**。
>
> ⚠️ 权限**最小化**：能看日志（`ai:log:view`）≠ 能看成本（`ai:cost:view`）；
> HR 能读能处理人工待办，但**不能重试失败任务**（改图状态属运维动作）。详见 **§11**。

---

## 6. 红线清单（改代码前先读）

| 编号 | 红线 | 落地位置 |
| --- | --- | --- |
| CR-1~4 | 六层依赖只能向下；Router 无业务、DAO 无规则 | 分层目录结构 |
| **CR-5** | 🔴 **所有 ID 响应序列化为字符串** | `schemas/common.py::SnowflakeId`，测试 `test_contract_snowflake_id.py` |
| CR-11 | 列表侧 MySQL 条件与检索侧 Milvus 表达式**由同一 principal 推导** | `core/tenant.py::DataScopeFilter` |
| — | 权限数据**只从 JWT 读**，前端传参只能收窄 | `core/security.py` + `core/tenant.py` |
| — | 越权与不存在**统一 40301**（不返回 40401） | `exceptions` |
| — | **权限 fail-closed**：权限读不到就拒绝（40301），**不得降级为放行、也不得变成 50000** | `services/auth_service.py::get_perm_codes` + `core/security.py::require_perm`（用例 AC-08/AC-37） |
| — | **Token 吊销 fail-open**：Redis 抖动不得升级为全站 401（与上一条**刻意相反**） | `dao/token_dao.py`（用例 AC-38） |
| — | **部门子树未解析时锁死为不可见**（宁可查不到，不可越权） | `core/tenant.py::mysql_condition` + `services/org_scope_service.py`（用例 AC-11） |
| — | 🔴 **移动部门必须级联重算「自身 + 全部子孙」的 `dept_path`/`dept_level`**，且用 `parent_id` 走 BFS 重算（不拿旧路径做字符串替换） | `services/org_service.py::_move_dept` |
| — | 🔴 **PII 无密钥时拒绝写入，绝不静默存明文**（`AES_KEY` 缺失 → 报错，不是降级） | `core/crypto.py::encrypt_sensitive` |
| — | 手机号/邮箱**任何角色都只拿脱敏值**（`138****8888`）；明文身份字段归档案模块按 §1.7 裁剪 | `core/crypto.py::mask_*` + `schemas/user_admin.py` |
| — | 🔴 **自锁防护**：不能改自己的角色、不能禁用自己、不能动掉最后一名可用超管（42201） | `services/user_admin_service.py` + `dao/user_admin_dao.py::count_enabled_admins` |
| — | 删除部门前校验引用（子部门 / 任何未软删账号）→ 40904 | `services/org_service.py::delete_dept` |
| — | 🔴 **DAO 的每个查询都必须带 `tenant_id`**（租户隔离第一位），且 `is_deleted` 默认过滤 | `dao/*.py`（`audit_dao._apply_filters` 把 `tenant_id` 放在首位） |
| — | 🔴 **审计只追加**：模型无软删列、DAO 无 update/delete、路由无写端点 | `models/audit.py` + `dao/audit_dao.py::__all__` + `routers/audit_router.py`（用例见 `test_audit.py`） |
| — | 🔴 **审计写入独立事务 + fail-open 永不抛异常**（业务回滚不得带走审计；审计失败不得把业务变 500） | `services/audit_service.py::record`；调用点**一律在业务 commit 之后** |
| — | 🔴 **密码 / Token 永不入审计**（写入侧键名黑名单递归脱敏） | `services/audit_service.py::_sanitize` |
| — | 🔴 **中间件不得 `raise` 业务异常**：必须用 `build_error_response` 自己构造响应 | `core/middleware.py::RateLimitMiddleware` + `exceptions/exception_handlers.py` |
| — | 🔴 **中间件顺序：CORS → TraceId → RequestMeta → RateLimit**（改顺序会让 429 丢 `trace_id` / 丢 CORS 头） | `app/main.py`，用例 `test_layering.py::test_rate_limit_module_is_wired_into_request_chain` |
| — | 导出/查询超上限**直接报 `40002`，绝不截断**（「导出一半」看不出不完整，比失败更危险） | `services/audit_service.py::export` |
| — | 🔴 **模型网关永不抛异常**：一律返回结构化结果（`ok` / `degraded` / `error_code` / `retryable`）；第三方异常**严禁**包装成 50000 | `core/model_gateway.py::call` |
| — | 🔴 **网关返回值恒为一套完整键集**（差异用「哪些键非空」表达）—— 曾因按 kind 返回部分键导致**成功的 embed 调用全 `KeyError`** | `core/model_gateway.py::_EMPTY_RESULT` / `_result()`，用例 `test_ai_gateway.py` |
| — | 🔴 **脱敏率分母 = `ai_call_log.deployment`（调用时刻的事实）**，**不由 `ai_model_config` 当前配置推导** | `models/ai.py::AiCallLog.deployment` + `dao/ai_dao.py::aggregate_outbound_mask`（`deployment` 必填 keyword-only） |
| — | 🔴 **成本核算只能用 `Decimal`**（float 累加漂移会让「看板合计」≠「逐条之和」） | `core/cost.py::CostCalculator` |
| — | 🔴 **Prompt 回滚 = 追加新版本**，绝不改历史行（历史是审计证据） | `services/prompt_service.py::rollback` |
| — | 🔴 **API-94 幂等判定先于状态判定**（同键 → 40903 返回首次结果；他人已处理 → 40902） | `services/agent_service.py::resolve_interrupt` + `agent_task.idempotent_result` |
| — | 🔴 **`agent_step.step_index` 是「任务内全局序号」**（跨 run 单调递增）—— 回放时间线依赖它 | `services/agent_service.py::_step_callback`（按已有步数偏移） |
| — | 🔴 **不提供「创建 Agent 任务」端点** —— 等于给外部一个无需业务前提的执行入口 | `routers/agent_router.py`（由用例登记 6 个端点、断言无 create） |
| — | 状态流转走 Core UPDATE 后必须 `refresh`（`expire_on_commit=False` + Identity Map 会**回显内存旧对象**） | `dao/agent_dao.py::get_interrupt(*, refresh=True)` |
| — | 🔴 **中断节点内、`interrupt` 之前不得有副作用** —— 自研下只执行一次，**LangGraph 会重放该节点 ⇒ 执行两次**；构造期即拦 | `agents/graph.py::GraphDefinition.validate` + `tests/test_graph_replay_safety.py`（AST 扫真实源码） |
| — | 🔴 **新增/修改图必须同步 `interrupts` / `side_effects` 声明**，且二者**无交集** | `agents/parse_graph.py::build_parse_graph`；用例断言「声明 == 代码里真实请求中断的节点」 |
| — | 降级**不抛 500**（HTTP 200 + `code=50301`） | `schemas/common.py::degraded` |
| — | 密码明文**永不入日志** | `core/logging.py::safe_payload` |
| — | Redis **只存可丢数据**，权威状态落 MySQL | `dao/redis_client.py` |
| — | 改库只能「改 ORM → 生成迁移 → Review → 发布」 | `alembic/` |

---

## 7. 已知遗留（已登记，不属本批遗漏）

| 项 | 归属 | 说明 |
| --- | --- | --- |
| ~~登录成功/失败写审计日志（TBL-28）~~ | ~~CU-08~~ | ✅ **已完成**（批次 6）：`AuthService._audit` 已改调 `AuditService.record`，`TODO(CU-08)` 已移除 |
| ~~HTTP 限流中间件接入~~ | ~~CU-09~~ | ✅ **已完成**（批次 6）：`RateLimitMiddleware` 已接线；由 `test_rate_limit_module_is_wired_into_request_chain` 钉死「不是假完成」 |
| 字段级脱敏（身份证/手机/邮箱） | **CU-14/15** | 落在**序列化层**（接口 §1.7）；越权用例 AC-20/21 已就位待启用 |
| 档案类资源的行级断言（真实表） | **CU-14** | 本批行级条件用 `SysUser` 编译验证；档案表落地后补真实表断言 |
| 端点级「读他人档案 → 40301」 | **CU-14** | 条件级断言已完成（AC-12/13） |
| 「降级后仍带检索 expr」端到端断言 | **CU-20** | 结构级断言已完成（AC-32） |
| ~~**节点级检查点**（方案 A 的增强项）~~ | ~~CU-14 前置~~ | ✅ **已完成**（批次 10）：`run_graph` 增 `on_checkpoint(state, next_node)` 回调（**中断前 / 达终态 / 解析后继**三处触发，失败步骤不触发），服务层 `_checkpoint_callback` 把 `state + current_node` 刷进 `agent_task` 对接 API-95 重试语义；`tests/test_node_checkpoint.py` 9 用例钉死。⚠️ 与「迁移控制项」是两件事：批次 9 交付的是**控制项**（保证不写错），本行是**增强项**（崩溃后少重跑） |
| 档案类资源的字段级脱敏（身份证等） | **CU-14/15** | 用户 CRUD 侧已完成（手机/邮箱一律脱敏）；档案侧待序列化层 |
| 用户/组织的删除**接口**（API 矩阵中无 `DELETE /users`） | **CU-07+** | 现状：`soft_delete_user` 已备好但**不开放接口** —— 矩阵未定义即不实现 |
| ~~角色**删除**接口~~ | ~~CU-06~~ | ❌ **决定不开放**：矩阵中无 `DELETE /roles`；删角色会造成「用户权限真空」与审计断链。`rbac_dao.soft_delete_role` 保留备查 |
| ~~API 调用审计落库（角色的增改、组织的增删改、用户的增改与角色分配、**字典项的增改停用**）~~ | ~~CU-08~~ | ✅ **已完成**（批次 6）：`rbac_service` / `org_service` / `user_admin_service` / `dict_service` 共 10+ 处调用点已落库（角色与权限绑定记 `granted`/`revoked` 差集） |
| ~~**API-26 的「改前值/改后值留痕」落库**~~ | ~~CU-08~~ | ✅ **已完成**（批次 6）：⚠️ 上游把留痕指向 **TBL-22 `talent_change_log`**（接口 §2.2），但该表 `talent_id NOT NULL`、语义是「**人才**档案变更」—— 字典项落不进去。已确认改走 **TBL-28 审计日志**并落库（带 `before`/`after` 快照） |
| ~~**审计分区的「补足 / 归档」需定时任务**~~ | ~~CU-24~~ | ✅ **已完成**（批次 7）：落地为 `deploy/partition-ensure.{sh,bat}`（cron / `schtasks` 注册命令见 `deploy/README §5`），并当期实跑 `--ensure-next --months 3 --table all`（`ai_call_log` 新增 `p202611`）。归档改为「**先转储再校验，最后才 DROP**」：`deploy/archive-partitions.sh`（见 §10.2） |
| ~~**库级「仅 INSERT/SELECT」授权未做**~~ | ~~CU-24~~ | ✅ **已完成**（批次 7）：`deploy/mysql-init/01-audit-account.sh` + `Settings.AUDIT_DB_USER`（留空即 fail-open 退回主账号）。⚠️ 本机走 `deploy/README` 的「路径 A」（复用本机 MySQL，未起 compose），故 `AUDIT_DB_USER` 暂为空 ⇒ **第三层保证当前未生效**，已在 `backend/.env` 就地注明 |
| **归档的「恢复流程」未实测** | **CU-24** | 🔴 归档是 TSV（`mysql --batch --raw`），恢复需先建对应月份分区再 `LOAD DATA LOCAL INFILE`（NULL 以 `\N` 保留）。**「有文件」不等于「能恢复」** —— 须在真实归档发生前做一次演练 |
| **生产部署形态未做**（三区分离 compose / MySQL 主从 / HTTPS 证书） | **CU-24** | **开发环境已交付**；生产形态取决于 **Q-3 国产化清单**与域名/证书来源（`deploy/README §10` 已逐项登记，不静默带过） |
| ~~**LangGraph 是否替换自研图运行时**~~ | ~~CU-14 起（N2）~~ | ✅ **已决策**（批次 8）：一期**保留自研** + 补节点级检查点 + 把切换绑定**三触发条件**（决策时点 = CU-21 开工前）。依据：自研 1 195 行 / 83 用例、F-A0 七项逐条覆盖、CU-14~17 **四类能力核查全为「不需要」**；切换代价 = **+24 包（含 `httpx2`，会让 `TestClient` 切实现 ⇒ 654 用例 HTTP 层需整体复核）+ 4 个编译扩展**（信创挂 Q-3），且社区 MySQL saver **自建表绕过 Alembic + 无 `tenant_id`** ⇒ 切换须**自写适配器**。论证见 `docs/图运行时选型评估-自研运行时与LangGraph.md`；已补 §6.5 组件选型偏离申报 |
| **README §5 接口速查缺 API-30 ~ API-80** | **文档** | 🔵 **未补**：现仅覆盖 API-01~29（CU-01~CU-07）与 API-81~95（CU-10~CU-13），**CU-14 ~ CU-22 的 51 条接口未成表**。**接口事实源 = 接口设计文档 + `openapi.json`**（`/docs` 可交互），本表只是速查；补表属文档整理，不影响功能 |
| **两个 Agent 状态枚举的 `*_name` 是英文** | **CU-23** | `pending` / `approved` / `rejected` 走的是**字典种子**，代码 LABELS 若只改中文会让「代码渲染名」与「字典里的名字」打架。**建议与上一行 `status_name` 用词一并处理**（同一类问题：两种事实来源） |
| 网关阈值 / 单价表仍为默认值 | **CU-23** | 熔断阈值、重试次数、超时（`AI_GATEWAY_DEFAULT_TIMEOUT`）与出网单价（`ai_model_config.price_*`）待 N0 结论与选型定稿；**当前值不可当作容量或成本承诺** |
| 成本看板「大区间聚合」性能未压测 | **CU-24** | 三维度之和 = 总额已由用例钉死；接近 90 天上限时的聚合耗时未实测 |
| **限流阈值仍为默认值** | **CU-23** | 中间件与响应格式已闭环，但**数值**待 N0 压测标定；当前值仅保证「能拦住异常流量」，**不可当作容量承诺** |
| 审计查询未做**内容级**检索索引 | **待需求** | `keyword` 目前对 `target_id` / `trace_id` 做模糊匹配；若要按 `before`/`after` 内容检索，需另建索引或外部索引（结构变更） |
| 审计的**租户内可见范围**未定义行级数据域 | **待需求** | 本批按「审计**无**行级数据域」实现（同租户内有 `audit:view` 即可见全租户记录）。若要求「部门管理员只看本部门操作」，需给 `audit_log` 加 `org_id` 并接入 `DataScopeFilter`（结构变更） |
| `AuditAction` 词表与业务调用点靠**人工同步** | **本期不做** | 新增业务动作须同时加词表常量与调用点。测试能兜住「词表内部一致性」，但**无法**保证「每个业务动作都调了审计」；彻底解决需 AOP / 事件总线 |
| 字典**类型**的增删改接口 | **待矩阵补充** | 接口矩阵中类型**只有 GET**（API-23），故类型由种子初始化、运行期只读；**矩阵未定义即不实现**（同 CU-06 不给岗位加改/删接口） |
| `org_dept` / `position` 的 `status_name` 用词 | **CU-23** | 现用 `SysUserStatus` 取标签 → 停用状态显示成「**禁用**」，与 DDL 注释「停用」不一致。字典已改用新增的 `EnabledStatus`（停用/启用）；⚠️ **批次 6 曾计划随 CU-08 一并改，实际未做**（避免为一个文案改动扩大批次范围）→ 改 `_to_node` / `_to_position_out` 两处即可，**建议随 CU-23 前端文案定稿时一并处理** |
| 字典唯一键的**潜在缺陷**（软删场景） | **留痕备查** | `uk_tenant_type_code` / `uk_tenant_type_item` 带 `is_deleted`，而 MySQL 下该列只有 0/1 → **同编码最多只能有一条软删行**（第二次软删会撞 1062）。本期安全：字典项**只停用、从不软删**（`is_deleted` 恒为 0），该唯一键退化为 `(tenant_id, type_code[, item_code])`。**若将来支持真删除，必须先改唯一键**（纳入 `deleted_at` 或删除批次号） |
| 用户角色变更对 **ADM 短路**的即时性 | **CU-05/06 已登记** | 见下方「残留风险 R-1」 |
| 前端联调 / 部署编排 | **CU-23 / CU-24** | 未开始 |
| **CU-27 的 3 项阻塞探针**（Milvus / Reranker / OCR） | **CU-27（属环境）** | 代码已就绪，卡在**环境**：① Milvus 未装 `pymilvus`；② Reranker（:9997）返回 **502**；③ 无 OCR 后端。依赖/服务装好后 `python scripts/n0_probe.py` 复跑即闭环，报告自动刷新 |
| `pymilvus` 未加入 `requirements.txt` | **CU-18（N4）** | 它是 N4 的正式依赖（`pip install pymilvus`）；本批未提前引入，以免污染 N1 的轻净环境 |
| MinIO 未部署（`127.0.0.1:9000` 连接被拒） | **CU-14（N2）** | N2 导入链路（F-3.1 落原始文件，ADR-10）前必须就绪 |
| Dify 未配 API Key / 云通道未配置 | **非阻塞** | 均不挡 N1 编码；云通道是否启用取决于 N0 吞吐结论与甲方出网边界协议（ADR-4） |
| **金标准集标注未开始** | **CU-28（须甲方）** | 工具链已就绪（模板 200 份 + 50 条已生成，质检/脱敏可跑），但**标注须甲方 HR / 业务专家执行**（D-11，8~11 人天）；且 IDX-02 的「**分母口径**」待甲方确认 → **N6 关键路径项**（见 §9.2） |
| 金标准集「期望人才」需 `resume_id → talent_id` 映射 | **CU-14** | 标注阶段档案尚未入库（没有 `talent_id`），故查询集用 `resume_id`；**CU-14 的导入明细必须保留该映射**，否则 N6 无法把 Milvus 返回结果与查询集对齐，**IDX-01 根本算不出来** |
| 联系方式哈希的盐口径（`CONTACT_HASH_SALT`） | **CU-14/15（N2）** | 手机号仅 10^11 空间，裸 SHA256 秒级可穷举 → 必须加盐。**N2 落库必须复用 `core.crypto.hash_contact`**，否则金标准集与系统侧算出不同哈希，IDX-02 的联系方式字段**全判错且极难定位** |
| ~~部门子树解析（`data_scope=2` 用）~~ | ~~CU-05~~ | ✅ **已完成**（`org_scope_service.py`，登录时回填 `dept_ids`） |
| ~~权限快照的精确版本号~~ | ~~CU-05~~ | ✅ **已完成**（Redis 两级计数 `sess:perm_ver:*`） |
| ~~AC-34 端点级 40401 断言~~ | ~~CU-06~~ | ✅ **已完成**（`test_ac_34_endpoint_not_found_is_40401`） |
| ~~`bump_user_perm_version` 无调用方~~ | ~~CU-06~~ | ✅ **已完成**（API-16 分配角色时递增用户级版本号） |

### 残留风险 R-1（**已知且有界**，务必知悉）

**现象**：角色变更对 **`ADM` 短路**要等 Token 刷新才生效；对**普通权限码**则**立即生效**。

**原因**：`require_perm` 的 ADM 判定读的是 **JWT 里的 `rls`**（签发时固定），
而普通权限码走的是**数据库快照**（`assign_roles` 会失效快照 → 下次请求即回查新权限）。

**影响范围**：把某人的 `ADM` 摘掉后，他手上**已签发的** Token 最长还能当 `ADM` 用
`ACCESS_TOKEN_EXPIRE_SECONDS`（默认 2 小时）。**但「最后一名超管」守卫不受影响** ——
它查的是数据库（`count_enabled_admins`），与 Token 无关。

**为什么不改**：把 ADM 判定也压到数据库，等于给**每一次管理员请求**多加一次查库
（ADM 是最频繁的角色），而收益只是一个有界窗口。当前采取的是
「**缩短 Token 有效期 + 前端在 `perm_version` 变化时静默 refresh**」的组合。
若安全评审要求强一致，改造点只在 `core/security.py::require_perm` 一处。

---

## 8. 排障手册（本机已实测踩过的坑）

### 8.1 「Redis 进程活着，应用却连不上」

**现象**：日志出现 `unknown command `HELLO`, with args beginning with: `3``，`/health` 里 `redis=false`。

**根因**：`redis-py 8.x` 的默认协议是 **RESP3**，连接时先发 `HELLO 3` 协商；
而 `HELLO` 是 **Redis 6.0** 才引入的命令。本机 Redis 为 **5.0.14.1**（Windows 版普遍是旧版），
因此每次建连都失败 —— 看起来像是「服务没起」，实为**驱动与版本不兼容**。

**处置**：本项目已固定 `REDIS_PROTOCOL=2`（RESP2），本项目不需要 RESP3 的推送能力。
若确要用 RESP3，请先确认服务端 ≥ 6.0（推荐 7.x）。

```bash
# 确认服务端版本（RESP2 下）
.venv/Scripts/python.exe -c "import redis; print(redis.Redis(host='127.0.0.1',port=6379,protocol=2).info('server').get('redis_version'))"
```

### 8.2 `alembic` 命令直接报 UnicodeDecodeError

**现象**：`UnicodeDecodeError: 'gbk' codec can't decode byte ...`，Alembic 完全无法启动。

**根因**：Alembic 用 `configparser` 以 `encoding="locale"` 读 `alembic.ini`，
中文 Windows 的 locale 是 **GBK**，ini 里只要有中文字符就会炸。

**处置**：🔴 **`alembic.ini` 保持纯 ASCII**，中文说明写 `alembic/README.md`。

### 8.3 MySQL 连接被拒（1045）

**现象**：`Access denied for user 'root'@'localhost' (using password: NO)`，`/health` 里 `mysql=false`。

**处置**：`cp .env.example .env` 并填 `MYSQL_PASSWORD`。注意 `.env` 已被 `.gitignore` 排除，
🔴 **绝不要提交**。若使用非 root 账号，请先授权：

```sql
CREATE DATABASE talent_platform DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'talent'@'localhost' IDENTIFIED BY '<pwd>';
GRANT ALL PRIVILEGES ON talent_platform_v2.* TO 'talent'@'localhost';
FLUSH PRIVILEGES;
```

> 库名是 `talent_platform_v2`（不是 DS-1 写的 `talent_platform`），原因见 §4.2。

### 8.4 pip 安装超时

国内直连 `pypi.org` 经常超时（实测 5 分钟仍未完成）。改用镜像：

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```

> ⚠️ 请用**命令级** `-i` 参数，不要执行 `pip config set global.index-url ...`
> —— 那会写入用户级 `%APPDATA%\pip\pip.ini`，污染本机所有 Python 项目。

### 8.5 测试大量 skip

契约类测试（雪花 ID 序列化 / 统一响应 / JWT / 错误码表 / 越权用例 AC-01~40 的 L1 部分）**必须全绿**；
`test_auth_db.py`（登录链路）、`test_token_dao.py`（Redis 版本号）与
`test_access_control.py::test_l2_rbac_endpoints_end_to_end`（RBAC 端到端）在依赖不可用时
会明确 skip 并给出原因，**这是设计行为，不是缺陷**。看到 skip 请先按 §8.1 / §8.3 把依赖拉起。

### 8.6 登录成功，但业务接口一律 40301

**现象**：API-01 能拿到 Token，随后 `/api/v1/roles` 等接口全返回 `code=40301`。

**根因排序**（按概率）：

1. **权限点未种子化** —— 非 ADM 角色没有任何权限码，`require_perm` 按 **fail-closed** 拒绝。
   🔴 **这是设计行为**：权限矩阵里这些接口对非 ADM 本来就是 `❌`。
   若你现在用的是非 ADM 账号，请换 ADM 账号，或先执行 §4.5 的种子脚本。
2. **用的是非 ADM 账号访问 ADM 专属接口** —— 同上是**预期**结果（AC-01~07 正是这么断言的）。
3. **角色↔权限绑定被改过** —— 用 API-22 重新绑定；`.env` 的 Redis 若可用，
   绑定接口会自动失效该角色下所有用户的权限快照，无需手动清缓存。

**自检命令**：

```bash
# 看自己实际拿到了哪些权限码（ADM 会返回全部启用权限点）
curl -s http://127.0.0.1:8002/api/v1/auth/permissions -H "Authorization: Bearer <access_token>"
```

### 8.7 建号/改号时报 50000「未配置敏感字段加密密钥（AES_KEY）」

**现象**：`POST /api/v1/users` 带 `phone` 或 `email` → `code=50000`，日志里是
`服务端未正确配置敏感字段加密密钥（AES_KEY），无法保存手机号`。

**根因**：`.env` 的 `AES_KEY` 为空或格式不对（要求 **64 位 hex = 32 字节**）。

**为什么不自动降级成明文**：PII 一旦以明文落库，后续任何补丁都无法回收已泄露的数据。
所以这里是**刻意的 fail-loud**：宁可不让你建号，也不让明文进库。

```bash
# 生成并填入 .env
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_hex(32))"
```

> 注：**不带手机号/邮箱**的建号请求**不受影响** —— `encrypt_sensitive(None)` 不读密钥，
> 所以漏配密钥只影响「真的要写 PII」的请求，不会让整个用户管理瘫痪。

### 8.8 应用启动正常，但 `/health` 里 `mysql=false` 且报「表不存在」

**现象**：日志 `Table 'talent_platform_v2.sys_user' doesn't exist`。

**根因**：只建了库、没跑迁移（Alembic 负责建**表**，不负责建**库**）。

```bash
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic current      # 应显示 0001_basement
.venv/Scripts/python.exe scripts/seed_dev.py --username admin --password 'Admin@123456'
```

### 8.9 `data_scope=2 本部门` 的用户「永远查不到人」

**现象**：部门经理（MGR）调 `/api/v1/users` 或带 `org_id` 过滤的接口，一律 `40301 请求的数据范围超出行级权限`。

**根因（CU-06 实测踩到并已修）**：`principal.dept_ids` 在**请求期是空的** ——
JWT 里只有 `oid`/`dsc`，部门子树是**派生数据**，必须由服务层按需回填
（`OrgScopeService.resolve_dept_ids`）。若直接把空的 `dept_ids` 拿去跑
`assert_scope_narrowing`，任何 `org_id` 都会被判「超出范围」。

**排查**：确认调用方**显式解析并传入**了自身子树：

```python
DataScopeFilter.assert_scope_narrowing(principal, [org_id], dept_ids=own_dept_ids)
```

另一个常见原因是**部门树 ``dept_path`` 没维护好**（为空或格式不对）——
它由 API-08/09 自动维护，若有人手工改库就会失真。
用 `GET /api/v1/orgs/tree` 看每个节点的 `dept_path` 是否为 `/<id>/`、`/1/2/` 这种形态。

### 8.10 测试里「明明改了，断言却读到旧值」

**现象**：接口返回已是新值，测试用同一个 Session 再查却是旧值。

**两个坑叠在一起**（缺一不可解）：

1. **MySQL 默认 REPEATABLE READ** —— 同一事务内后续 SELECT 复用第一次读的快照。
   接口侧是另一个会话，它刚提交的修改在旧事务里看不见 ⇒ 需要先 `session.commit()` 结束事务；
2. **本项目 `expire_on_commit=False`**（见 `app/dao/session.py`，为让 Service 提交后还能读对象属性）
   ⇒ identity map 里已加载的属性**不会**被后续 SELECT 覆盖 ⇒ 需要 `session.expire_all()`。

```python
session.commit()      # 结束事务 → 下次查询拿新快照
session.expire_all()  # 丢弃已加载属性 → SELECT 真正覆盖
```

> 只做第一条会读到 identity map 的旧值，只做第二条会读到旧事务快照 —— 两个一起才有效。

### 8.11 字典接口报了 40401，是「没权限」还是「不存在」？

**现象**：调 `/api/v1/dicts/{type_code}/items` 拿到 404「资源不存在」，但不确定是**权限**问题还是**字典类型**问题。

**读法**（三个码互斥，一眼可分）：

| 现象 | 码 | 含义 | 处置 |
| --- | :-: | --- | --- |
| `404` + 40401 | `NOT_FOUND` | 该 `type_code` **在本租户不存在**（含「是别的租户的」） | 查库：`SELECT * FROM dict_type WHERE tenant_id=? AND type_code=?` |
| `403` + 40301 | `NO_PERMISSION` | 资源可能有，但**缺权限码** | 该接口是仅 ADM：API-23 需 `dict:type:list`，25/26/27 需 `dict:create` / `dict:update`；API-24 只要登录 |
| `400` + 40001 | `PARAM_INVALID` | 编码**格式**不合规（如 `BAD-CODE`） | 只允许小写字母/数字/下划线，且不以 `_` 开头 |

🔴 **为什么字典的「跨租户」是 40401 而不是 40301** —— 这与 CU-05 定的
「超数据域一律 40301」**不冲突**：字典是**租户级公共配置**，没有 `org_id`，
接口 §1.7 的行级裁剪（`data_scope`）对它**不适用**。因此「别的租户的类型」
在本租户看来就是「本域内确实不存在」→ 40401。
把这条口径记反，前端会把「字典不存在」误报成「你没权限」，排查方向直接被带偏。

> 字典项也一样：拿别的租户的 `item_id` 调 PUT/DELETE → 40401。

### 8.12 Reranker 端口「有响应」，但 `/rerank` 全报 502 / 连接被重置

**现象**：`curl http://127.0.0.1:9997/` 能看到响应（或至少不是 connection refused），
但 `n0_probe.py` 的 `reranker` 探针三种端点形状全部失败：

```
TEI /rerank(texts)        → ReadError: [WinError 10054] 远程主机强迫关闭了一个现有的连接
Infinity /rerank(documents) → 502 Bad Gateway
Xinference /v1/rerank     → 502 Bad Gateway
```

**根因**：**502 说明该端口上有网关 / 反向代理在监听，但后端的模型服务没起来**
（常见于：模型仍在加载、加载失败、内存/显存不足被杀）。
这时**探活会「有响应」**，很容易被误判成「服务是好的、是客户端写错了」——
本项目特意把三种端点形状都试一遍并打印各自的错误，就是为了把这两种情况分开。

**处置**：

| 现象 | 含义 | 动作 |
| --- | --- | --- |
| 502 Bad Gateway | 网关在、后端未就绪 | 看模型服务日志（是否 OOM / 加载失败）；确认模型名与 `RERANKER_MODEL` 一致 |
| 连接被重置（10054） | 协议不匹配（往 HTTP 端口发 HTTPS）或只监听 IPv6 | 用 `curl -v` 确认协议；确认 `RERANKER_BASE_URL` 的 scheme |
| Connection refused | 端口没有服务 | 启动服务，或修正 `RERANKER_BASE_URL` |
| 全部失败但服务确实在跑 | 部署形态是第四种 | 在 `scripts/n0/probes.py::probe_reranker` 的 `attempts` 里补一种端点形状并回归测试 |

> 探针只覆盖「精排服务可用性 + 单次延迟」；**精排质量**要在 N5 用金标准集评估。

### 8.13 报「CONTACT_HASH_SALT 未配置」

**现象**：调用涉及联系方式哈希的代码（或 `goldset.py seal`）时抛 `ContactHashSaltError`。

**为什么不是「有默认值就行」**：`phone_hash` / `id_card_hash` 用于**精确去重**，
看似随便一个哈希都能用；但手机号只有 10^11 个可能值，**裸 SHA256 在 GPU 上秒级可穷举**
—— 那等于「存了哈希却等于明文」。盐的作用就是让穷举失效，因此**没有盐就必须报错，
不能静默退化成不加盐的弱哈希**（弱哈希一旦落库，补一个盐也无法回收已泄露的数据）。

**处置**：

```bash
# 生成一个随机盐，写入 backend/.env 的 CONTACT_HASH_SALT
python -c "import secrets; print(secrets.token_hex(16))"
```

🔴 三条纪律：

1. **必须与 `AES_KEY` 取不同的值** —— 一个用于不可逆比对、一个用于可逆解密，混用会让「泄露一个」等于「弱化另一个」；
2. **一经使用不可更换** —— 换盐会让**已入库的全部哈希失效**（需配套数据重刷方案）；
3. **盐是密钥，不是数据** —— 随部署配置下发（如 `.env` / K8s Secret），❌ 不随标注集、日志、报告外发。

> 配套：`core/crypto.hash_contact` 会把 `+86 138-1234-8888` 与 `13812348888`
> 归一到同一个哈希（`normalize_contact`），避免同一人因书写差异被判成两个人。

### 8.14 补审计分区时报 1481 `MAXVALUE can only be used in last partition definition`

**现象**：想给 `audit_log` 加下个月的分区，手写 `ALTER TABLE ... ADD PARTITION (PARTITION p202612 ...)`
时报错 1481。

**根因**：该表最后一个是 **`pmax`**（`VALUES LESS THAN MAXVALUE`）。MySQL **不允许**对含 `MAXVALUE`
分区的表用 `ADD PARTITION` —— 新分区会排在 `pmax` **之后**，而 `MAXVALUE` 必须是最后一个。

**处置**：改用 `REORGANIZE PARTITION pmax INTO (...)`：把 `pmax` 拆成「新月份分区 + 新的 `pmax`」。

```bash
cd backend
# 别手写 SQL，用脚本（已按 REORGANIZE 实现，且幂等）
.venv/Scripts/python.exe -m scripts.audit_partition --ensure-next --months 3
```

```sql
-- 脚本内部等价于这一条（新月份动态生成，不要照抄月份）
ALTER TABLE audit_log REORGANIZE PARTITION pmax INTO (
  PARTITION p202611 VALUES LESS THAN (TO_DAYS('2026-12-01')),
  PARTITION pmax    VALUES LESS THAN MAXVALUE
);
```

### 8.15 审计里查不到某条变更 / 429 一直不触发

**先分清是「没记」还是「记了但查不到」**：

| 现象 | 大概率原因 | 怎么确认 |
| --- | --- | --- |
| 某业务动作**完全没记录** | 该 Service 的 `_audit` 调用点没接上，或 `AuditAction` 里没有对应词表常量 | 搜该 Service 的 `_audit(`；看 `schemas/audit.py::AuditAction.ALL` |
| 记了，但**筛选查不到** | `AuditFilterRequest` 的时间区间**上限 90 天**；或 `action` 传了不在词表里的值 | 去掉筛选条件重查；`action` 用 `AuditAction` 的常量值（不是中文标签） |
| 明明是别的租户的记录，**查不到** | **设计如此**：审计**没有行级数据域**，跨租户 = **不存在**（40401），不是 40301 | 见 §8.11 的三态读法 |
| **审计写入失败但业务成功**了 | **设计如此**（fail-open）。日志里会有 ERROR | `grep -i "audit" <日志>`；这是**刻意**的：审计写失败不该把已成功的业务打成 500 |
| 变更**没有 before/after 快照** | 该调用点没传快照（如登录类动作本就无快照） | 对比 `dict_service` / `rbac_service` 的快照写法 |

```bash
# 别在 Python 里猜，直接看库（分区与最近记录）
.venv/Scripts/python.exe -m scripts.audit_partition --list
```

**429 一直不触发**：

1. **测试环境**：`tests/conftest.py` 把 `RATE_LIMIT_PER_MINUTE` **抬高到 100000**
   （未登录时限流按 IP 分桶，而所有用例共用同一 IP，默认 300 会造成**偶发 429 假阳性**）。
   要手测 429，得在**真实启动的服务**上打，而不是在 pytest 里。
2. **真实环境**：把 `RATE_LIMIT_PER_MINUTE` 调小再试；或检查路径是否被归一化到了同一个桶
   （`/api/v1/users/1` 与 `/users/2` 应命中**同一个** `{id}` 桶 —— 这是**有意**的，
   否则遍历 ID 就能绕过限流）。
3. **完全没限流**：先确认中间件挂上了 —— `tests/test_layering.py::test_rate_limit_module_is_wired_into_request_chain`
   会替你判断（防的正是「模块写好了但没挂上」）。

---

## 9. N0 前置实测与金标准集（CU-27 / CU-28）

> 这两项即评审文档 **§7.2「第 0 批」**：**最高风险且耗时最长**（金标准集以人天计），
> 与编码主力**并行、不占批次**，但**必须先启动**，否则 N6 硬阻塞。
> 详细记录见评审文档 **§7.3 批次 5**。

### 9.1 N0 前置实测与模型就绪验证（CU-27）

**做什么**：把需求 §12.1 N0 的「验证现有组件可用性 + 模型就绪 + 基线压测」变成
**一条命令 + 一份自动生成的报告**，而不是靠人手逐个 `curl`。

```bash
cd backend

# 全量探针（10 项）→ 打印摘要 + 写报告到 docs/
.venv/Scripts/python.exe scripts/n0_probe.py

# 只看有哪些探针（不触任何 I/O，可在没环境的机器上跑）
.venv/Scripts/python.exe scripts/n0_probe.py --list

# 排障时只跑某几项（避免等 Embedding 采样）
.venv/Scripts/python.exe scripts/n0_probe.py --only mysql,redis,ollama

# 加大 Embedding 采样次数以取更稳的均值
.venv/Scripts/python.exe scripts/n0_probe.py --only embedding --embedding-rounds 16
```

**产出**：`docs/N0-基线实测报告-YYYY-MM-DD.md` + 同名 `.json`（**重跑即刷新**）。

**探针四态**（读报告时先看这个）：

| 状态 | 含义 | 该怎么办 |
| :-: | --- | --- |
| ✅ `ok` | 可用且达到基线 | — |
| 🟡 `degraded` | 可用但**低于基线** | 按需求 §6.1：**N0 就要提降级或追加算力**，不得拖到 N6 |
| ⛔ `blocked` | **前置未就绪**（未装 / 未部署 / 未配密钥） | 按报告「处置清单」执行，**不是 bug** |
| ❌ `failed` | 服务在、但**行为异常**（连得上却报错） | 看日志，属真问题 |

**退出码**：`0` = 可继续推进；`2` = 存在 `failed` 或**阻塞项**不可用。
⚠️ 非阻塞项的 `blocked`（如未配云通道）**不会**让退出码变 2 —— 那不是缺陷，
正是 N0 要如实记录的现状。

**首轮实测结论（2026-09-22）**：

| 探针 | 结果 |
| :-: | --- |
| MySQL | ✅ 8.0.27 · `talent_platform_v2` · 11 张表 · `utf8mb4_unicode_ci` |
| Redis | ✅ 5.0.14.1 · PING 11.8ms · **3,752 ops/s**（单连接串行 SET+GET 400 次 / 0.107s） |
| Ollama | ✅ 连通 · 在册 4 个模型 · `qwen2.5:3b` / `qwen3:8b` 齐备 |
| bge-m3 | ✅ **维度 1024（与 `EMBEDDING_DIM` 一致）· 单条 647ms / 1.54 次每秒** |
| Milvus | ⛔ 未验证（未装 `pymilvus`） |
| Reranker | ❌ 不可用（:9997 返回 502，见 **§8.12**） |
| OCR | ⛔ 无 OCR 后端（仅 `openpyxl`，图片/扫描件简历无解析能力） |
| MinIO | ❌ 未启动（:9000 连接被拒） |
| Dify / 云通道 | ⛔ 未配 API Key / 未配置（均不阻塞 N1） |

**🔴 关键结论 —— bge-m3 吞吐推算**：实测 **1.54 次/秒 ≈ 92 次/分钟**。按 3.8 万份规模：

| 分块单元数/份（CL-3 待定） | 总单元 | 单线程耗时 |
| :-: | :-: | :-: |
| 10 | 38 万 | ≈ 68.6 小时（2.9 天） |
| 24 | 91.2 万 | ≈ 164.6 小时（6.9 天） |
| 50 | 190 万 | ≈ **343 小时（14.3 天）** |

→ 直接回应风险 **R-17「N4 工期偏乐观」**：**必须并行化**，否则单线程导入会压垮 N4；
若要压缩窗口，须按 **D-4 / D-19** 评估 Embedding 上云（配合 D-15 脱敏）或追加算力。

**口径局限（读数字前必看）**：Redis 吞吐是**单连接串行**、Reranker 是**单次调用**、
云通道是 **TCP 建连 RTT**（不含 TLS/HTTP/排队）；Embedding 单条耗时**含请求开销**且本机
**无显存（CPU 推理）**。这些**只用于建立基线量级，不能替代 IDX-04/05/11 的并发压测**。

### 9.2 金标准集（CU-28）

**做什么**：提供**标注工具 + 字段格式模板 + 质检脚本**（需求 §12.5 界定的乙方交付边界）。

```bash
cd backend

# 1) 生成模板（默认 200 份简历 + 50 条查询，图片件占 20%）
.venv/Scripts/python.exe scripts/goldset.py build

# 2) 标注中随时质检（只读，不改标注文件）；--report 额外写 CHECK_REPORT.md
.venv/Scripts/python.exe scripts/goldset.py validate --report

# 3) 只看进度
.venv/Scripts/python.exe scripts/goldset.py status

# 4) 交付前脱敏：明文联系方式 → 盐化哈希（产出 sealed/）
.venv/Scripts/python.exe scripts/goldset.py seal
```

**产出**：

| 路径 | 内容 |
| --- | --- |
| `<仓库根>/data/goldset/resume_annotations.csv` | 简历标注集 **200 行 × 21 列**（8 管理列 + **13 字段列**） |
| `<仓库根>/data/goldset/query_set.csv` | 查询-人才集 **50 条 × 10 列** |
| `<仓库根>/data/goldset/README.md` | 给标注人的一页纸 |
| `<仓库根>/data/goldset/sealed/` | 脱敏后的**可交付版本**（明文列已替换为 `phone_hash` / `email_hash`） |

**作业指导书**：`docs/金标准集标注规范.md`（发甲方专家用，含逐字段口径、缺失怎么标、
三种准确率口径、18 条质检规则、FAQ）。

🔴 **三条红线**：

1. **本标注集含个人信息** —— 禁止提交到任何代码仓库 / 云盘 / 外部工具（`data/` 不在 `backend/` 内，但仍须自觉）；
2. **标注必须由甲方专家执行**（D-11，乙方无业务判断力）—— 乙方只提供工具、模板与质检脚本；
3. **交付乙方前必须 `seal`** —— 联系方式不比对明文，比对盐化哈希。

**工作量**（需求 §12.5）：简历标注集 6~8 人天 + 查询集 2~3 人天 = **8~11 人天高技能人工**
→ 这是 **N6 的关键路径**，越早启动越好。

**两个待甲方确认的口径**（已在评审文档 §7.3 批次 5 登记）：

- **IDX-02 的「分母」** —— 需求 §5 原文只说「字段级准确率 ≥95%」未给分母定义（M-3）。
  本批给出推荐口径（主口径 = 正确字段数 / **真值存在**的格子数；另设辅助口径、**编造率单列**、
  **图片件单列**），**需甲方确认后**才能开始标注；
- **`resume_id → talent_id` 映射** —— 标注阶段档案未入库，故查询集用 `resume_id`；
  **CU-14 的导入明细必须保留该映射**，否则 N6 无法把 Milvus 结果与查询集期望对齐。

---

## 10. 审计日志与限流（CU-08 / CU-09）

### 10.1 审计日志：「只追加」是**结构保证**，不是约定

`audit_log`（TBL-28）用**三层**保证「记录只增不改不删」。任何一层被削弱，都可能
在赶工期的某个下午被人「顺手改一下」：

| 层 | 做法 | 在哪看 |
| :-: | --- | --- |
| ① 模型 | `AuditLog` **没有** `is_deleted` / `updated_at` 列 —— 软删能力在结构上就不存在 | `app/models/audit.py` |
| ② DAO | 只导出 `insert_log` / `list_logs` / `count_logs`，`__all__` 钉死 —— **没有** update/delete 函数可调 | `app/dao/audit_dao.py` |
| ③ 数据库 | 应用账号只授 `INSERT` / `SELECT`（**待 CU-24 拆账号**，见「已知遗留」） | — |

**为什么审计写入用独立事务、且永不抛异常（fail-open）**：

- 若与业务共用 Session，**业务失败回滚时审计记录会跟着消失** —— 而最该留痕的恰恰是失败事件
  （登录失败、越权被拒）。故 `AuditService.record` 开**独立 Session**；
- 若审计写失败就抛异常，会把**已经成功的业务请求变成 500**。故只记 ERROR、**永不抛**；
- 调用点一律放在业务 `commit` **之后** —— 反过来会留下「审计说有、业务实际已回滚」的**假记录**。

**已落审计的动作**（受控词表见 `schemas/audit.py` 的 `AuditAction`）：

登录 · 登出 · 改密 · 用户增改 / 角色分配 / 批量导入 · 角色增改 / 权限绑定 · 部门增改删 · 岗位新建 · 字典项增改停用

**写入侧递归脱敏**：`password` / `token` / `jwt` / `authorization` / `apikey` / `aes` / `encrypt` /
`sign_key` 等键名一律替换；超长与过深内容截断。用户快照**不含 PII 明文**（只记 `has_phone` / `has_email`）。
→ 🔴 密码与 Token **永远不进审计表**。

```bash
# API-28 查询（分页 + 筛选；时间区间上限 90 天）
curl -s 'http://127.0.0.1:8002/api/v1/audit-logs?page=1&per_page=20&action=user.update' \
  -H "Authorization: Bearer <t>"

# API-29 导出（返回 CSV 文本，仍走统一响应壳：data.filename / data.content）
curl -s -X POST http://127.0.0.1:8002/api/v1/audit-logs/export \
  -H "Authorization: Bearer <t>" -H 'Content-Type: application/json' \
  -d '{"start_time":"2026-09-01 00:00:00","end_time":"2026-09-22 23:59:59","max_rows":10000}'
```

> ⚠️ **导出为什么返回文本而不是文件流**：返回 `StreamingResponse` 就把统一响应的
> `code` / `trace_id` 丢了（前端拿到裸文件、无法区分成功与降级）。故 CSV 内容放在 `data.content`。
> ⚠️ **超行数上限直接报 `40002`，不截断** —— 「导出了一半」在界面上**看不出不完整**，
> 会被当成全量数据用来决策，比直接失败危险得多。

### 10.2 分区维护（🔴 运维必做，别等跨月才发现）

`audit_log` 按 `TO_DAYS(created_at)` **RANGE 分区**（迁移 `0003_audit` 已建执行月 + 1 个月 + `pmax`）。

```bash
cd backend
# 看现有分区与覆盖到的月份
.venv/Scripts/python.exe -m scripts.audit_partition --list

# 补足未来 3 个月（幂等，可重复跑；建议配成「每月 1 日」的计划任务）
.venv/Scripts/python.exe -m scripts.audit_partition --ensure-next --months 3

# 归档：预览「删掉 13 个月前的分区」（不带 --yes 只打印不执行）
.venv/Scripts/python.exe -m scripts.audit_partition --drop-before 13
.venv/Scripts/python.exe -m scripts.audit_partition --drop-before 13 --yes
```

🔴 **两个已踩过的坑（写进脚本里了，改脚本时别改回去）**：

1. **补分区必须用 `REORGANIZE PARTITION pmax INTO (...)`，不能用 `ADD PARTITION`** ——
   MySQL 不允许对含 `MAXVALUE` 分区的表 `ADD PARTITION`，会报
   **1481 `MAXVALUE can only be used in last partition definition`**。
2. **覆盖月份从分区名 `pYYYYMM` 解析，绝不从 `TO_DAYS` 反算** —— MySQL 的 `TO_DAYS`
   以「公元 0 年」起算，与 Python `date.toordinal()` **差一天**；反算会静默偏移一个季度
   → **归档时删错分区**。这类「不报错但结果是错的」缺陷比崩溃危险得多。

> 另：`audit_log` 的**复合主键是 `(id, created_at)`**，不是设计洁癖 ——
> MySQL 要求**分区列必须出现在每个唯一键里**，`id` 单主键时 `PARTITION BY RANGE` 直接建不了表。
> 副作用是 `created_at` 不可 UPDATE，这在审计表上是**优点**。

### 10.3 限流与降级（CU-09）

`RateLimitMiddleware` 已接线，超限返回 **42901** 并带 `X-RateLimit-Limit` / `X-RateLimit-Remaining` /
`Retry-After`。阈值经 `settings.RATE_LIMIT_PER_MINUTE` 配置（**当前为默认值，待 N0 压测标定**）。

| 设计点 | 为什么这么做 |
| --- | --- |
| **中间件自己构造响应**（`build_error_response`），不 `raise` | Starlette 的 `ExceptionMiddleware` 在中间件栈**内侧**，从中间件 `raise` 会被兜成 **50000** 而不是 42901 |
| 顺序 **CORS → TraceId → RequestMeta → RateLimit → 路由** | TraceId 在外侧，429 才有 `trace_id` 可排查；CORS 在最外层，429 才带 CORS 头（否则浏览器把失败拦掉，**真实原因被掩盖**） |
| 路径归一化：资源 ID 段替换为 `{id}` | 否则 `/users/1`、`/2`… 各自成桶，**遍历 ID 即可无限绕过** |
| 已鉴权按**验签后**的 `uid` 分桶，未登录退化为 IP | 直接信 payload 里的 `sub` 的话，伪造 `sub` 即**换桶绕过** |
| `/health`、`/docs` 等豁免 | 否则「限流把监控打挂」，探活失败会被读成服务不可用 |
| Redis 不可用时 **fail-open + 告警** | 限流组件自身**绝不能**把业务打成 500 |

**自测**（本机已实测）：

```bash
# 连打 5 次触发 42901（默认阈值下）
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w '%{http_code} ' http://127.0.0.1:8002/api/v1/audit-logs
done; echo
```

### 10.4 结构守卫（「按文件分层」是可执行断言）

`tests/test_layering.py`（**19 用例**）把分层纪律变成测试，**违反时失败并说明原因**：

| 用例 | 防的是什么 |
| --- | --- |
| `test_no_upward_layer_imports` | 反向依赖（下层 import 上层）—— 违反时功能正常、测试全绿，直到改一处权限逻辑要改 5 个地方 |
| `test_only_allowed_core_to_service_exception` | 唯一白名单（`core/security.py → services.auth_service`）必须是**函数体内**延迟导入，否则循环导入 |
| `test_app_never_imports_scripts` | `scripts → app` 单向；反向会把「开发机才装的包」变成**生产启动硬依赖** |
| `test_routers_contain_no_business_logic` | Router 里出现 `select(DictItem)` 这类 ORM 用法 |
| `test_every_module_has_its_layer_files` | 代码写了但**放错地方/改名**—— MODULE_FILES 清单即「按文件落地」的验收表 |
| `test_project_level_artifacts_exist` | **`deploy/` 只写在文档里、目录是空的**；或重构时把 `deploy/` 挪进 `backend/`（所有部署命令 / cron 路径一起失效，而本地开发毫无感觉） |
| `test_rate_limit_module_is_wired_into_request_chain` | **CU-09 假完成**：模块写好、单测全绿，但中间件**根本没挂到应用上** |

> 🔴 **新增模块时必须同步补 `MODULE_FILES` 一行**（`deploy/` 产物补 `PROJECT_FILES`）——
> 清单漏了就等于该模块不受结构守卫保护。

**第二道守卫**：`tests/test_graph_replay_safety.py`（**14 用例**）守「图与节点」的红线 ——
装配期互斥校验（`GraphDefinition.validate`）＋ **AST 扫真实节点源码**（中断节点不得在 `interrupt` 之前写库）
＋ Service 层依赖面白名单（只允许 12 个公开符号）。
⚠️ 它内含**正反对照**用例（「故意写违规代码必须被抓到」）——
**一个永远返回空的检查比没有检查更危险**，因为它让人以为检查过了；
守卫「只写不验」与「没有守卫」在结果上等价。

---

## 11. AI 中台与 Agent（CU-10 ~ CU-13）

### 11.1 模型网关：**永不抛异常**，按场景走 priority 链

`core/model_gateway.py` 是**所有 AI 能力的唯一出口**。约定：

| 约定 | 为什么 |
| --- | --- |
| 按 `scene` 取 `ai_model_config` 的 **priority 链**，逐路尝试直到成功 | 「主备切换」不是配置里写死的一条链，而是**按优先级排的候选集** |
| 熔断状态放 **Redis**，按「模型 + 场景」隔离；**Redis 不可用 → fail-open + 告警** | 熔断是为了**少打无用的请求**，不该成为新的单点（铁律 6） |
| **返回值恒为一套完整键集**（`content` / `vectors` / `scores` / `usage` / `model` …），差异用「哪些键非空」表达 | 曾因「按 kind 各返回各的键」导致**成功的 embed 调用全 `KeyError: 'content'`** —— HTTP 200、上游正常、**所有 AI 调用却全线失败**（见评审文档 §7.3 批次 7 缺陷 ①） |
| **永不抛异常**，一律返回结构化结果（含 `degraded` / `error_code` / `retryable`） | 降级不抛 500（CR-7）；调用方只需判断 `ok`，不必包 try |
| 超时值**夹紧**（上下界） | 过大：上游卡死时占住线程池；过小：必然超时 → 两种都会让「主备切换」形同虚设 |
| 4xx **不重试**、5xx **重试**、空正文**判失败** | 重试一个「参数错」只会浪费时间；**误重试的代价远大于误不重试** |

成本核算（`core/cost.py`）用 **`Decimal` + `ROUND_HALF_UP`**，float 入参先经 `str()` 再量化 ——
金额累加不能用 float（`0.1+0.2` 漂移会让「看板合计」与「逐条之和」对不上）。

### 11.2 AI 调用日志与合规分母

🔴 **脱敏率的分母是「调用时刻的事实」`ai_call_log.deployment`（`local` / `cloud`），
不由 `ai_model_config` 的当前配置推导。**

若按「今天的模型配置」反推历史日志，**改一次配置会让既往合规统计集体失真** ——
昨天确实出网的调用会显示成「本地调用」，脱敏率虚高。因此：

- `deployment` 在有模型调用**那一刻**落库；
- `ai_dao.aggregate_outbound_mask` 的 `deployment` 是**必填 keyword-only 无默认值**
  （防止有人「顺手」给个默认值把统计悄悄算错）；
- 分母只有 `deployment='cloud'` 的调用；`mask_applied_rate < AI_MASK_APPLIED_RATE_FLOOR`（默认 1.0）
  → `summary.mask_alert=true`；成本超 `AI_COST_ALERT_YUAN` → `cost_alert=true`（**只呈报不告警**）。

⚠️ 接口 §3 明确「本接口只呈报不告警」：真正的告警动作（发通知 / 写告警表）属运维侧。

### 11.3 Prompt：回滚 = **追加新版本**，不是改历史行

| 不变式 | 说明 |
| --- | --- |
| 正文**只追加** | 改 `content` 不改原行，而是新增 `version+1`（同一 `prompt_code` 的族内） |
| 族内**唯一启用** | 同一 `prompt_code` 只能有一个 `status=1`，否则「当前用哪版」没有唯一答案 |
| **同内容改版 → 42201** | 内容一字未改就发新版本，只会制造一堆无法区分的版本号 |
| **回滚 = 追加新版本** | 回滚到 `v1` 后生成 `v3`（正文取自 `v1`），状态序列 `[1,2,3] → [2,2,1]`；历史版本是**审计证据**，就地改写等于抹掉「当时用的是什么」 |

`prompt_code` 只**转大写、不拒绝小写**（`PROMPT_CODE_PATTERN` 只拦中文等非法字符）——
与字典 `item_code` 的严格口径**有意不同**：字典编码是**业务引用值**（改了会让历史枚举值静默变孤儿），
Prompt 编码是**开发期标识**。

### 11.4 Agent：幂等、中断、步骤回放

- 图定义在 `agents/parse_graph.py`：`detect_format → ocr → extract_fields → normalize_fields →
  score_confidence → field_review`（**中断点**）→ approve 后 `persist_talent` / reject 后 `manual_reject`。
- **幂等判定先于状态判定**（API-94）；`agent_task.idempotent_result` 存**首次结果**，重放时原样返回。
- **`rejected` 不静默丢弃**：走 `manual_reject` 终态，任务置 `success`（「被人工驳回」是**正常结束**，
  不是系统故障 —— 置 `failed` 会触发重试与告警，把业务判断变成运维事件）。
- **重试从当前节点继续**（`current_node`），已成功步骤不重跑；`approved` / `waiting_human` 重试 → 42201。
- **`agent_step.step_index` 是「任务内全局序号」**（跨 run 单调递增），API-92 的回放时间线依赖它 ——
  早期实现用的是「本次 run 内从 1 起」的**相对序号**，结果「入库」排到了「OCR」前面（批次 7 缺陷 ③）。
- 三张 Agent 表**都不加 `org_id`**：Agent 任务是**系统执行轨迹**而非业务档案，可见性由「谁能看这个任务类型」决定。

### 11.5 图运行时的迁移控制项（🔴 **新增图 / 改图必读**）

一期已拍板**沿用自研运行时**（评审文档 §7.3 批次 8）—— 理由**不是** LangGraph 不好，
而是它相对自研多出来的能力（super-step 检查点 / 状态历史·时间旅行 / `Send` 并行分支 / 子图 / stream）
在 **CU-14 ~ CU-17 的逐单元核查中全部「无实际需求」**，
而自研在**租户隔离、单事务原子、与十条铁律的契合度**上明显更强。
代价要自己扛的一件事是：

> 🔴 **LangGraph 会重放中断节点**（`interrupt()` 之前的代码会执行**两次**），
> 而自研是「挂起即落检查点、恢复只跑后继节点」。
> **所以在中断节点里写副作用，今天不会炸，切换那天会写两遍 / 计费两次。**

故有 6 条**迁移控制项**（完整表见 `agents/graph.py` 模块 docstring 与评审文档 §8.5），
其中最关键的两条已**可执行**（不是注释）：

| 红线 | 谁在守（机械守卫） |
| --- | --- |
| 中断节点内、`InterruptRequest` 之前**不得有写操作** | `GraphDefinition.validate()` **构造期抛错** + `tests/test_graph_replay_safety.py` 的 AST 用例 |
| 新增中断节点**必须**同步 `interrupts` 声明 | 同一用例：断言「声明 == 代码里真实构造 `InterruptRequest` 的节点」；漏声明即失败 |

新增一张图的模板：

```python
GraphDefinition(
    name="...", entry="...", nodes={...},
    # 会挂起等人的节点
    interrupts=frozenset({"复核节点"}),
    # 会产生外部影响的节点（写库 / 调模型 / 调 OCR / 发通知 / 投递队列）
    side_effects=frozenset({"入库节点", "抽取节点"}),
)
# ⚠️ 两个集合必须无交集，否则构造即抛 NodeError
#    （registry 在导入时构图 ⇒ 坏声明会让进程起不来 —— 这是故意的：
#      比「恢复时把库写两遍」便宜得多）
```

- 需要「落复核待办 / 发通知 / 计费」时，写在**中断之后的节点**里；
  或让它幂等，并在**同一行**写 `# replay-safe: <理由>` 豁免（必须写可被复核的理由）。
- `side_effects` 同时是**将来启用重放 / 时间旅行时的幂等审计清单**；
  `describe()` 会把 `interrupt` / `side_effect` 两个标记下发给前端（画中断点与副作用角标）。
- ⚠️ **改动 `parse_graph.py` 或新增任何图之后，必须跑**
  `pytest tests/test_graph_replay_safety.py` —— 它是「自研 ↔ LangGraph 双运行时语义」
  之间**唯一**的一道静态防线。

### 11.6 本批新增的 7 个配置项（三处同步，缺一即 `test_config_env` 失败）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `AI_GATEWAY_DEFAULT_TIMEOUT` | 60 | 网关兜底超时（秒）；单模型可在 `ai_model_config.timeout_seconds` 覆盖 |
| `AI_COST_ALERT_YUAN` | 100 | 区间成本超过即 `cost_alert=true`（**只标记不告警**） |
| `AI_MASK_APPLIED_RATE_FLOOR` | 1.0 | 🔴 **不要调低**：低于 1.0 即意味着**存在未脱敏出网的调用**（合规红线） |
| `AGENT_MAX_STEPS` | 50 | 图单次运行最大步数（死循环兜底）；图内显式 `max_steps` 优先 |
| `AGENT_STEP_RETENTION_MONTHS` | 6 | `agent_step` 保留月数；⚠️ **仅用于清理提示**，清理是运维动作（自动删执行轨迹不可逆） |
| `AUDIT_DB_USER` / `AUDIT_DB_PASSWORD` | 空 | 审计专用库账号（只 `SELECT`+`INSERT`）；**留空 = fail-open 退回主账号** |

> ⚠️ 本机 MySQL 的 `DATETIME` **无小数秒且对亚秒值四舍五入**（`…00.5 → …01`）→
> 默认时间窗（最近 7 天，`end=now()`）存在 **1 秒级可见性延迟**：刚发生的调用可能查不到。
> **这不是丢数据**；也**不要**给默认 `end` 加 1 秒（会把「恰好 90 天」的精确口径变成模糊口径）。

---

## 12. 部署（CU-24）

**部署产物全部在仓库根 `deploy/`，不在 `backend/` 下**（架构 §7 的目录结构）。完整说明见 **`deploy/README.md`**，
这里只列三条最容易踩的：

| 事项 | 要点 |
| --- | --- |
| **端口冲突** | `deploy/docker-compose.dev.yml` 的宿主端口就是 3306/6379，本机若已装 MySQL/Redis 会直接报 *port is already allocated* → ① 走「路径 A」（复用本机服务，不用 compose）；② 改 `MYSQL_HOST_PORT`/`REDIS_HOST_PORT` **并同步改 `backend/.env` 的 `MYSQL_PORT`/`REDIS_PORT`**（必须成对） |
| **Nginx 上传上限** | `client_max_body_size` 必须与后端 `UPLOAD_MAX_FILE_SIZE_MB` **一致** —— 只改后端不改 Nginx ⇒ 大文件在 Nginx 层被 413 拦下、**后端一条日志都没有** |
| **前端缓存策略** | `index.html` **不缓存**、`/assets/*` **长缓存**。反过来会导致发版后用户加载旧 HTML 去请求已被清掉的旧 hash 资源 → **白屏** |



