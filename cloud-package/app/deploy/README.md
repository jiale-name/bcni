# 部署说明（CU-24 · N1 收尾）

> 依据：架构设计 §7（部署拓扑与端口）、评审文档 §7.2 第 2 批 / §7.3 遗留 ① ② ⑥。
> 本目录只负责**数据区与应用区入口**；后端进程由使用者手动管理（架构 §7.3）。

## 0. 文件清单

| 文件 | 作用 |
| --- | --- |
| `docker-compose.dev.yml` | 开发环境数据组件：MySQL 8.0 / Redis 7 / MinIO / etcd / Milvus 2.4（**不含后端与前端**） |
| `nginx.conf` | 生产入口：托管前端 `dist` + 反代 `/api` → 后端 8002（同源，故**不需要 CORS**） |
| `.env.example` | 部署变量模板（**入库**）；`deploy/.env` 为本机实际值（**不入库**） |
| `mysql-init/01-audit-account.sh` | 建「审计专用库账号」—— 只 `SELECT` + `INSERT`，是「只追加」的**第三层**保证 |
| `partition-ensure.sh` / `.bat` | 分区补足定时任务（Linux cron / Windows 计划任务） |
| `archive-partitions.sh` | 分区归档：**先转储并校验，再 DROP** |

## 1. 前置条件

| 项 | 要求 |
| --- | --- |
| Docker | Docker Desktop（Windows）或 Docker Engine；Compose V2（`docker compose`，带空格） |
| 复用宿主机服务 | Ollama `:11434`、Dify `:80/5001`、Reranker `:9997` —— **不由 compose 管理** |
| 后端 | `backend/.env` 已按 `backend/.env.example` 填好（尤其 `MYSQL_PASSWORD`） |

## 2. 两条启动路径（**先选一条，别混着来**）

### 路径 A · 用本机已装的服务（当前这台机器的默认做法）

本机已有 MySQL 8.0.27 / Redis / Ollama / Dify，**不需要** compose：

```bash
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe scripts/seed_dev.py          # 建租户/角色/权限/字典/AI 配置
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002   # 不带 --reload
```

### 路径 B · 用 compose 起数据区

```bash
cp deploy/.env.example deploy/.env      # 填密钥，至少 4 个：MYSQL_ROOT_PASSWORD / REDIS_PASSWORD / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
docker compose -f deploy/docker-compose.dev.yml --env-file deploy/.env up -d
docker compose -f deploy/docker-compose.dev.yml ps      # 等 mysql/redis/milvus 都 healthy
```

🔴 **端口冲突（最常踩的一脚）**：架构 §7.3 的宿主端口就是 3306/6379，
而本机若已装 MySQL/Redis，`up` 会直接报 *port is already allocated*。
两种处置：① 走路径 A；② 在 `deploy/.env` 把 `MYSQL_HOST_PORT` / `REDIS_HOST_PORT`
改成 `13306` / `16379`，**并同步改 `backend/.env` 的 `MYSQL_PORT` / `REDIS_PORT`**（必须成对改）。

容器内访问宿主服务（Ollama / Dify）用 `host.docker.internal`（Docker Desktop 已提供）。

## 3. 端口对照（架构 §7.3）

| 组件 | 宿主端口 | 说明 |
| --- | --- | --- |
| 后端 uvicorn | **8002** | 本地或容器；**不带 `--reload`** |
| 前端 dev（Vite） | 5173 | proxy `/api` → `:8002` |
| 前端 prod（Nginx） | 80 | 托管 `dist` + 反代 `/api` |
| MySQL | 3306 | 可改 `MYSQL_HOST_PORT` |
| Redis | 6379 | 可改 `REDIS_HOST_PORT` |
| Milvus | 19530 / 9091 | gRPC / 指标 |
| MinIO | 9000 / 9001 | S3 API / 控制台 |
| Ollama / Dify / Reranker | 11434 / 80,5001 / 9997 | **宿主机服务**，复用 |

## 4. 前端部署（Nginx）

```bash
cd frontend && npm run build            # 产出 frontend/dist
docker run -d --name talent-web -p 80:80 \
  -v D:/gan/16个人项目/frontend/dist:/usr/share/nginx/html:ro \
  -v D:/gan/16个人项目/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  --add-host host.docker.internal:host-gateway \
  nginx:1.27-alpine
```

两个必须知道的约束：

- **`client_max_body_size 50m` 必须与后端 `UPLOAD_MAX_FILE_SIZE_MB` 一致**。
  只改后端不改这里 ⇒ 大文件在 Nginx 层被 413 拦下，**后端一条日志都没有**，
  排查时会误以为是后端校验错了。
- **`index.html` 不缓存、`/assets/*` 长缓存**。反过来的配置会导致发版后
  用户加载旧 HTML 去请求已被清掉的旧 hash 资源 → 白屏。

## 5. 🔴 分区定时任务（遗留 ①，**必做**）

`audit_log`（TBL-28）与 `ai_call_log`（TBL-24）按月 RANGE 分区，末尾有 `pmax` 兜底。
兜底让「忘了补分区」**不报错**（数据进 `pmax`），但**丧失归档能力** ——
之后无法 `DROP PARTITION` 回收，只能全表 `DELETE`。

```bash
# Linux（每月 1 日 03:00）
0 3 1 * *  /path/to/deploy/partition-ensure.sh >> /var/log/talent-partition.log 2>&1
```

```bat
REM Windows（管理员 cmd 执行一次，注册计划任务）
schtasks /Create /TN "TalentPartitionEnsure" /SC MONTHLY /D 1 /ST 03:00 ^
  /TR "D:\gan\16个人项目\deploy\partition-ensure.bat" /RL HIGHEST /F
```

手工查看 / 补足：

```bash
cd backend
.venv/Scripts/python.exe -m scripts.audit_partition --list --table all
.venv/Scripts/python.exe -m scripts.audit_partition --ensure-next --months 3 --table all
```

> ⚠️ `--table` 默认只有 `audit_log`（保持 CU-08 的行为）。
> **生产环境请一律带 `--table all`** —— 漏掉 `ai_call_log` 的后果更严重：
> 它的写入量级远高于审计（每次模型调用一条），膨胀快一个数量级。

## 6. 🔴 审计专用库账号（遗留 ②，**「只追加」的第三层**）

「审计只追加」三层：① 模型无 `is_deleted`/`updated_at` 列；② DAO 只导出 insert/list/count；
③ **库级账号没有 `UPDATE`/`DELETE`**。①② 挡的是应用代码，挡不住直接连库写 SQL。

```bash
# 首次 up 时自动执行；数据卷已存在时手工执行：
docker exec -i talent-mysql bash -s < deploy/mysql-init/01-audit-account.sh
```

本机（非容器）等价 SQL 见 `mysql-init/01-audit-account.sh` 内的 `GRANT` 两条。
建号后**必须**在 `backend/.env` 里设置：

```
AUDIT_DB_USER=audit_writer
AUDIT_DB_PASSWORD=<与 deploy/.env 一致>
```

`AUDIT_DB_USER` 留空 ⇒ 后端**退回主账号**（功能正常，但第三层保证不生效）。
这是有意的 fail-open：审计是旁路记录，不该因一个配置缺失让业务停摆。

> 该账号**不能**做分区维护（`ALTER` 未授权）；分区补足/回收由运维用主账号执行，
> 于是「改分区结构」这个动作必然留下运维痕迹 —— 这正是想要的。

## 7. 归档：**先转储，再删除**（遗留 ⑥）

```bash
./deploy/archive-partitions.sh 12 --dry-run   # 先只导出 + 校验，不删
./deploy/archive-partitions.sh 12             # 确认无误后再删
```

流程被强制成三段：**导出全部 → 校验全部（gzip 完整 + 行数 > 0）→ 才 DROP**。
任何一个分区导出失败或校验不过，脚本**中止且一行都不删** ——
「边导边删」在单条失败时会留下无法回补的空洞。

⚠️ **恢复不是理所当然**：归档是 TSV（`mysql --batch --raw`），恢复需先建好对应月份分区，
再用 `LOAD DATA LOCAL INFILE`（NULL 以 `\N` 保留）。真要用到归档那天，请按当时的
合规要求做一次**恢复演练**，别把「有文件」当成「能恢复」。

## 8. 备份与迁移（命名卷）

compose 用**命名卷**而非 bind mount（Windows 下 bind mount 需把宿主目录加入文件共享，
未加时容器**能起但写不进去**，报错滞后且难定位；见 compose 内注释）：

```bash
# 导出
docker run --rm -v talent-platform-dev_mysql-data:/d -v "${PWD}":/b alpine \
  tar czf /b/mysql-backup.tgz -C /d .
# 恢复（先停容器）
docker run --rm -v talent-platform-dev_mysql-data:/d -v "${PWD}":/b alpine \
  sh -c "rm -rf /d/* && tar xzf /b/mysql-backup.tgz -C /d"
```

数据库层备份（逻辑备份，推荐与上面配合）：
`mysqldump --single-transaction --routines --triggers talent_platform_v2 > dump.sql`

## 9. 冒烟检查清单

```bash
curl -s http://127.0.0.1:8002/health            # 期望 HTTP 200 + code=0；依赖不可用时 code=50301 + data.degraded=true
curl -s http://127.0.0.1:8002/openapi.json | head -c 200
cd backend && .venv/Scripts/python.exe -m scripts.audit_partition --list --table all
```

- [ ] `/health` 的 `dependencies.mysql` / `redis` 均为 `true`
- [ ] `/docs` 能看到 API-81~89（AI 中台）与 API-90~95（Agent）
- [ ] `--list --table all` 两张表都有 **本月 + 后续 ≥2 个月**的分区
- [ ] 用列表接口返回的 ID 请求一次详情（**CR-5 雪花 ID 冒烟**，唯一能当场暴露精度丢失的检查）

## 10. 未完成 / 待决策（**明确登记，不静默带过**）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| **Q-3 国产化清单** | ⏳ 待用户拍板 | 生产环境（甲方）的镜像/中间件选型依赖它，故**三区分离的生产 compose 本批不做** |
| 生产区部署（应用区/数据区/AI 算力区分离） | 未做 | 架构 §7.2 描述的是生产拓扑；需 Q-3 明确后方可定镜像与信创适配清单 |
| MySQL 主从 / 读写分离 | 未做 | 架构 §7.2 生产要求，一期开发环境不需要 |
| 归档的**恢复流程**未实测 | 未做 | 见 §7 的说明；需在真实归档发生前做一次演练 |
| HTTPS / 证书 | 未做 | 生产必须，需域名与证书来源（待甲方提供） |

`deploy/.env` 与 `deploy/archive/` 一律不入库（见 `deploy/.gitignore`）。
