# AI 数字化人才平台（2026 顶配版）

> 面向学校 / 单位的 **AI 驱动人才档案与能力评估平台**：把散落的简历与人事材料，变成**可检索、可复核、可追溯、可问答**的结构化人才资产。
>
> 一期聚焦 **F-3 档案与数字画像**（导入 → 解析 → 复核 → 标签 → 查重 → 检索 → 问答），并已落地 **人才测评**的任务与作答后端能力。

<p>
<img alt="backend" src="https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLAlchemy-009688">
<img alt="frontend" src="https://img.shields.io/badge/frontend-Vue3%20%2B%20TS%20%2B%20Vite-42b883">
<img alt="db" src="https://img.shields.io/badge/MySQL-8.0-4479A1">
<img alt="tests" src="https://img.shields.io/badge/pytest-1006%20passed-brightgreen">
</p>

---

## 1. 这个项目解决什么问题

传统人才库的通病是「文件堆在网盘里，信息锁在 PDF 里」。本项目把简历导入之后的全过程做成一条**有状态、可审计**的流水线：

| 阶段 | 解决的问题 | 落地形态 |
| --- | --- | --- |
| 批量导入 | 几百份简历逐个打开太慢 | 拖拽上传 → 后台异步解析 → SSE 实时进度 |
| 字段抽取 | 姓名/学历/技能散落在正文 | 文本层抽取 + 字典归一 + 逐字段置信度 |
| 分层复核 | AI 抽错没人管 | 置信度漏斗 → 字段级/文件级人工介入队列 |
| 受控标签 | 标签各人各叫法 | 受控词表 + AI 选标签（只从词表里选） |
| 查重合并 | 同一人投了三次 | 确定性打分 + 人工确认 + 可回滚合并 |
| 语义检索 | 关键词搜不到「会大模型的」 | 混合召回（关键词 + 向量）+ RRF + Rerank |
| 档案问答 | 想知道「他做过几个 To B 项目」 | RAG 问答 + 出网前脱敏，答案带引用 |
| 测评 | 能力评估靠拍脑袋 | 题库 → 试卷 → 任务 → 作答 → 评分 → 监控 |

**设计红线**：所有「确定性结论」（成本、阈值、查重分、标签校验、排序）都由确定性组件产出，大模型**只产文本**，不参与判定。

---

## 2. 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+（本机 3.13）、FastAPI、SQLAlchemy 2.0、Alembic、Pydantic v2 |
| 数据 | MySQL 8.0（`talent_platform_v2`）、Redis 7（会话/限流/幂等/队列） |
| 向量与检索 | Milvus 2.x（库 `talent_vec`）+ bge-m3 Embedding + Rerank |
| 对象存储 | MinIO（bucket `talent-files`，存简历原文） |
| 模型接入 | Ollama 本地模型（qwen 系）+ Dify 云通道，统一走**模型网关** |
| Agent | LangGraph 风格图运行时（自研，`app/agents/`）+ Checkpointer 断点续跑 |
| 前端 | Vue 3.5 + TypeScript + Vite 6 + Element Plus + Pinia + Vue Router + ECharts + Axios |
| 部署 | Docker Compose（数据区）+ Nginx（同源反代 `/api`），另备阿里云单机方案 |
| 测试 | pytest（49 个测试文件）+ 自研自检器 `acceptance_check.py` |

**规模**：40 张表 / 14 个 Alembic 版本 / 17 个路由模块（110 个端点声明）/ 前端 23 个页面。

---

## 3. 系统架构

### 3.1 六层依赖（只允许向下）

```
routers/        HTTP 声明与参数绑定，不含业务判定
   ↓
services/       业务编排与判定（唯一允许做决策的层）
   ↓
dao/            纯 CRUD，每条查询必带 tenant_id + is_deleted
   ↓
models/         SQLAlchemy ORM（库的唯一事实来源）
      core/     横切：雪花 ID / 安全 / 加密 / 脱敏 / 限流 / 成本 / 阈值 / 日志
```

`test_layering.py` 用 AST 把这条规则钉死：**反向 import 直接判失败**。改 Router 或 `core/` 之后必须复跑它。

### 3.2 请求链路

```
浏览器 → CORS → TraceId → RequestMeta → RateLimit → 路由 → Service → DAO → MySQL/Redis
                                       ↘ 全局异常处理器：任何异常收口成统一结构
```

### 3.3 目录总览

```
16个人项目/
├── backend/                  ★ 后端服务（端口 8002）—— 详见 backend/README.md
│   ├── app/
│   │   ├── routers/          17 个路由模块，api_router 是唯一聚合点
│   │   ├── services/         25 个业务服务
│   │   ├── dao/              15 个 DAO + session / redis 客户端
│   │   ├── models/           40 张表的 ORM 定义
│   │   ├── schemas/          Pydantic 出入参
│   │   ├── core/             雪花 ID、加密、脱敏、限流、成本、阈值、模型网关
│   │   ├── agents/           图运行时、简历解析图、Checkpointer、注册表
│   │   ├── retrieval/        混合召回、关键词/向量 Provider、画像
│   │   └── workers/          测评作答与评分 outbox 后台线程
│   ├── alembic/versions/     14 个迁移版本（0001 → 20260930_1400）
│   ├── scripts/              seed_dev / acceptance_check / 各类只读探针
│   └── tests/                49 个测试文件
├── frontend/                 ★ 前端（端口 5173）—— 23 个页面
│   └── src/{api,views,router,store,components,layouts,composables,directives,styles}
├── prototype/                批次 14 定稿的 19+6 页 HTML 高保真原型
├── deploy/                   ★ 部署资产（Compose / Nginx / 分区任务 / 归档）—— 见 deploy/README.md
├── cloud-package/            阿里云单机部署包（含打包清单与云端说明）
├── docs/                     ★ 设计文档全集（需求 / 架构 / 数据库 / 接口 / 评审 / 测试）
├── data/goldset/             金标准集（抽取准确率的评测基准）
└── _*.py                     一次性生成脚本（原型 HTML、架构图、云包）
```

> 🎯 **进度唯一事实源**：`docs/AI数字化人才平台-编码前评审文档.md`（§7.1 台账 / §7.3 批次记录）。
> 本 README 只做导航与上手，与文档冲突时**以评审文档为准**。

---

## 4. 快速开始

### 4.0 前置条件

| 项 | 要求 |
| --- | --- |
| Python | 3.11+（本机 3.13.12） |
| Node.js | 20+（本机 22.22.2） |
| MySQL | 8.0（本机 8.0.27），库名 `talent_platform_v2` |
| Redis | 5+（本机 7） |
| 可选 | MinIO / Milvus / Ollama / Dify —— **缺失不报错，走降级**（HTTP 200 + `50301`） |

### 4.1 后端（路径 A：用本机已装服务，**推荐**）

```bash
cd backend

# 1) 依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt
#    首次无 venv：python -m venv .venv

# 2) 配置
cp .env.example .env          # 填 MYSQL_PASSWORD / REDIS_PASSWORD / 模型地址等

# 3) 建表
.venv/Scripts/python.exe -m alembic upgrade head

# 4) 种子数据（租户 + 7 个角色 + 权限点 + 字典 + 首个管理员）
.venv/Scripts/python.exe scripts/seed_dev.py --username admin --password 'Admin@12345'
#    不传 --password 则自动生成强密码并打印到终端（更安全）

# 5) 启动 —— 🔴 不带 --reload，改代码必须手动重启才能确认生效
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

启动后：
- 接口文档 <http://127.0.0.1:8002/docs>
- 健康检查 <http://127.0.0.1:8002/health>（看 `code == 0` 与 `data.dependencies`）

### 4.2 后端（路径 B：用 Compose 起数据区）

> ⚠️ 与路径 A **二选一，别混着来**。本机已装 MySQL/Redis 时，`up` 会直接报端口占用。

```bash
cp deploy/.env.example deploy/.env    # 至少填 4 个密钥
docker compose -f deploy/docker-compose.dev.yml --env-file deploy/.env up -d
docker compose -f deploy/docker-compose.dev.yml ps     # 等 healthy
```

### 4.3 前端

```bash
cd frontend
npm install
npm run dev          # 5173，strictPort
```

Vite 把 `/api` 代理到 `http://127.0.0.1:8002`（axios baseURL = `/api/v1`）。
🔴 **代理目标必须写 `127.0.0.1` 而不是 `localhost`**：Node 17+ 会把 `localhost` 解析成 `::1`，而 uvicorn 只绑 IPv4，表现为「SSE 面板空白 / 接口时好时坏」。

打开 <http://127.0.0.1:5173>，用 4.1 第 4 步的账号登录。

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 开发服务器 |
| `npm run typecheck` | `vue-tsc --noEmit` 类型检查 |
| `npm run build` | 类型检查 + 生产构建到 `dist/` |
| `npm run preview` | 预览构建产物 |

---

## 5. 核心工程约定（改代码前必读）

| # | 约定 | 原因 |
| --- | --- | --- |
| 1 | 成功判定看 **`code == 0`**，不看 HTTP 状态码 | 统一响应体 `{code, message, data, page, trace_id}` |
| 2 | 所有 ID 序列化为**字符串**（雪花 ID） | JS `Number` 超过 2^53 会丢精度 |
| 3 | 依赖不可用 = **200 + `50301/50302` + `degraded=true`** | 降级是设计内行为，**严禁包成 50000** |
| 4 | 权限只从 **JWT** 读，前端传参只能收窄 | 防越权 |
| 5 | 越权与不存在统一 **40301**（有行级域表的资源），且**先带行级条件查** | 防 ID 探测；只有 AST 结构断言能守住 |
| 6 | DAO 每条查询必带 `tenant_id` + `is_deleted` | 多租户与软删是硬约束 |
| 7 | Redis 只存**可丢**数据，权威永远在 MySQL，抖动 fail-open | 缓存故障不能变成业务故障 |
| 8 | 库结构只由 **ORM + Alembic** 决定，禁止手工改库 | 迁移链是可复现的唯一路径 |
| 9 | 配置只经 `app/config.py`，三处同步（Settings / `.env.example` / 架构 §5） | 避免「本机跑得好，部署就炸」 |
| 10 | 密码明文**永不入日志**（走 `safe_payload`） | 审计日志本身不能成为泄密渠道 |
| 11 | 不可逆动作必须**幂等**（已归档再归档 → 返回成功不 raise） | 重试不能造成二次伤害 |
| 12 | 领域失败抛 `PersistRejected(reason_code)`，不把堆栈透给用户 | 失败要能落到人工介入队列 |
| 13 | 跨会话不传 ORM 行对象，**每层用自己的会话重读行** | 传过去的是空事务 |

完整清单见 `docs/AI数字化人才平台-编码前评审文档.md` 与 `.workbuddy/memory/DECISIONS.md`。

---

## 6. 测试与验收

### 6.1 跑测试（两条硬前提，缺一即大面积假错）

```bash
cd backend

# ① 先停掉在跑的后端进程（否则 1213 死锁 + 1062 撞号）
# ② 雪花 worker id 必须合法 0~31 且 ≠ 1
SNOWFLAKE_WORKER_ID=2 ./.venv/Scripts/python.exe -m pytest --junitxml=.junit_batchN.xml -q
```

跑完记得把后端启回来。

### 6.2 计数口径

> **只认 `--junitxml` 里的 `failures / errors / skipped` 三项为 0**，终端摘要不可信。
> 最近一次干净全量：**1006 passed / 0 / 0**（批次 23，543.6s）。

### 6.3 自检器

```bash
cd backend
.venv/Scripts/python.exe scripts/acceptance_check.py     # S-01 ~ S-09
```

### 6.4 用例纪律

- 回归用例**绝不 monkeypatch 被测函数**（会造出「永远绿」的假绿）。
- 既有用例「靠缺陷才绿」也是缺陷，要修夹具并收紧断言。
- 新增测试文件要**登记两处**：`tests/test_layering.py` 的 `MODULE_FILES` + 测试文档 §2.1。
- 测试夹具**只清 DB 行，不删盘上文件**（本机有安全删除钩子，teardown 删文件会误伤下一条用例）。

---

## 7. 文档索引

| 想知道什么 | 看哪里 |
| --- | --- |
| 需求范围与业务规则 | `docs/AI数字化人才平台-需求分析文档.md` |
| 架构、六层、部署拓扑 | `docs/AI数字化人才平台-架构设计.md` |
| 表结构、字段口径 | `docs/AI数字化人才平台-数据库设计.md` |
| 接口契约、错误码、权限矩阵 | `docs/AI数字化人才平台-接口设计.md` |
| 数据流图 / 类图 / 时序图 | `docs/AI数字化人才平台-数据流图与类图时序图.md` |
| **进度台账与批次记录** | `docs/AI数字化人才平台-编码前评审文档.md` ⭐ |
| 测试用例与验收脚本 | `docs/AI数字化人才平台-测试用例与验收脚本.md` |
| 开发计划与排期 | `docs/AI数字化人才平台-开发计划.md` |
| 分支策略与提交规范 | `docs/开发协作规范-Git分支策略与提交规范.md` |
| 后端模块细节 | `backend/README.md` |
| 部署（数据区 / Nginx / 分区） | `deploy/README.md` |
| 上阿里云 | `cloud-package/README-部署说明.md` |
| 测评模块设计 | `docs/测评分析Agent/`（7 份文档） |
| 人才画像智能体 | `docs/人才画像agent/需求分析文档.md` |

---

## 8. 分支与提交

| 分支 | 用途 |
| --- | --- |
| `main` | 交付基线，**受保护**，只接受合并 |
| `develop` | 集成主干，日常开发提交到这里 |

提交遵循 **Conventional Commits + 强制 `scope`**，footer 用编号回溯台账：

```
feat(talent): 新增按技能聚合的画像接口

Refs: TBL-13, API-31
批次: 25
CU: CU-18
```

`git log --grep` 能直接按 `CU-` / `API-` / `TBL-` 编号反查改动，这是台账与代码对齐的手段。

---

## 9. 当前状态与已知限制

### 9.1 已完成

- 设计项「⛔ 未设计」已清零；编码单元 **CU-01~CU-27** 中除 **CU-18（真实向量化灌数）** 与 **CU-24（生产三区）** 外均已交付。
- 最近一次干净全量回归：**1006 passed / 0 failed / 0 error / 0 skipped**（批次 23）。
- 后端：认证/RBAC/组织用户/字典/审计/AI 中台/Agent/简历导入/复核/标签/查重/检索/问答/隐私权利/测评任务，全部接口可用。
- 前端：23 个页面（1 登录 + 1 工作台 + 19 业务 + 2 状态页），路由级 + 按钮级双重权限。
- 部署：开发数据区 Compose、Nginx 同源反代、分区补足与归档脚本、阿里云单机包。

### 9.2 已知缺口（**不是 bug，是还没到那一步**）

| 缺口 | 影响 |
| --- | --- |
| Milvus **0 collection**（`talent_vec` 库已建，未灌数） | 语义检索走**关键词降级**；真实向量召回待 collection 命名拍板后复跑 |
| OCR 与 LLM 抽取器未接入 | 扫描件 / 无文字层简历抽不出字段，落人工介入 |
| `NAME_MISSING` 文件无补录入口 | 姓名抽不出且文件名兜底失败的文件，档案**不可见**（短期靠重命名重传） |
| 前端**测评页面**未接入 | 后端 API-119~135 可用，页面仅有原型 `prototype/p21~p26` |
| 前端行为层自动化未做 | 类型检查与构建**抓不到运行期字段丢失** |
| 生产三区部署 / HTTPS / 主从 | 仅开发环境就绪，待信创清单确认 |

> 完整待办与「须谁做什么」见 `.workbuddy/memory/OPEN-ITEMS.md`。

---

## 10. 安全与合规

- **`.env` / 上传的简历原文永不入库**（`.gitignore` 已挡；`.env.example` 密钥留空是正确设计）。
- 敏感字段（手机号、身份证、邮箱）落库前 AES 加密 + 哈希列供精确匹配。
- 出网前做**脱敏**，失败即不出网（`core/masking.py` + `privacy_service`）。
- 审计日志**只追加**，无任何 HTTP 写/删入口；数据库侧另设只有 `SELECT + INSERT` 的专用账号。
- 个人权利请求（查询/更正/删除/导出）走 `privacy_request` 流程留痕。

---

## 11. 一分钟自检（接手 / 评审时最先跑）

```bash
# 1) 服务活着吗
curl --noproxy '*' http://127.0.0.1:8002/health

# 2) 库表齐吗
cd backend && .venv/Scripts/python.exe -m alembic current

# 3) 分层没破吗
SNOWFLAKE_WORKER_ID=2 ./.venv/Scripts/python.exe -m pytest tests/test_layering.py -q
```

三项都过，再谈「哪些功能能用」。

---

<p align="center"><sub>一期 · F-3 档案与数字画像 ｜ 进度以 <code>docs/AI数字化人才平台-编码前评审文档.md</code> 为准</sub></p>
