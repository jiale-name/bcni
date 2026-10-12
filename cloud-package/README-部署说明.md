# 云端部署说明（阿里云）

> 对象：本项目**一期**（FastAPI 后端 + Vue3 前端 + MySQL/Redis）
> 依据：`docs/AI数字化人才平台-架构设计.md §7 部署拓扑` · `deploy/README.md` · `backend/.env.example`
> 编写日期：2026-10-04 ｜ 本机基线：Windows 11 · Python 3.13.14 · MySQL 8.0.27 · Redis 7 · Node 22.22.2

---

## 0. 结论速览（先看这张表）

| 问题 | 结论 |
| --- | --- |
| 免费服务器能跑什么？ | ✅ **Nginx(80) + FastAPI(8002) + MySQL 8.0 + Redis 7** —— 这是一期的**最小可用形态**，全部页面与接口可用 |
| 免费服务器跑不动什么？ | ❌ **Milvus + etcd + MinIO + Ollama + Dify**（内存 ×2~×4 打不住，详见 §1.2）。缺失时**不是报错，而是自动降级**（设计内行为） |
| 典型免费规格 | 阿里云免费试用/学生机常见 **2 核 2G 或 2 核 4G / 40~60G ESSD / 3~4M 带宽**（**以实际领取的为准**，本方案按 2 核 2G 下限设计） |
| 推荐系统 | **Ubuntu 22.04 / 24.04 LTS** 或 Alibaba Cloud Linux 3（**不要**选 Windows Server：本机 `.venv` 与脚本均不可复用） |
| 部署方式 | **方案 A：宿主直装**（推荐，内存占用最低）。方案 B 的 Docker Compose **不能原样使用**（见 §3.2 警告） |
| 一键判断能否跑通 | 部署完执行 §7 冒烟清单，`/health` 的 `dependencies.mysql/redis` 均为 `true` 即成功 |

---

## 1. 目标机资源评估（**部署前必看**）

### 1.1 一期各组件的内存需求（实测量级）

| 进程 | 常驻内存 | 2 核 2G 上可行性 |
| --- | --- | --- |
| Nginx | ~15 MB | ✅ |
| uvicorn（单 worker） | ~180~260 MB | ✅ |
| MySQL 8.0（调优后） | ~400~600 MB | ✅ 需调 `innodb_buffer_pool_size=256M` 并关 `performance_schema` |
| Redis 7 | ~30~60 MB | ✅ |
| **小计（最小可用）** | **约 0.7~0.9 GB** | ✅ 有充足余量 |
| Milvus standalone + etcd | ~2~4 GB | ❌ 放不下（且需 MinIO 作为对象后端） |
| MinIO | ~150~300 MB | 🟡 勉强，但配合 Milvus 才划算；单用可装 |
| Ollama + qwen2.5:3b | ~3~5 GB（模型加载后） | ❌ 放不下；且 2 核 CPU 推理会卡到不可用 |
| Dify（Docker 全家桶） | ~4 GB+ | ❌ 放不下 |

### 1.2 降级行为说明（**关键：缺组件不等于坏系统**）

一期对每个外部组件都实现了 **fail-open 降级**，由 `deploy/.env.example` 与 `backend/.env.example` 的注释明确约定：

| 缺失组件 | 用户可见表现 | 是否影响主流程 |
| --- | --- | --- |
| Milvus（含 `pymilvus` 未装） | 语义检索回落**关键词检索**，响应 `code=50302` + `data.degraded=true`，前端挂「降级」徽标 | ❌ 不影响登录/档案/导入/复核/查重/留痕/权限 |
| Ollama / Reranker | 字段抽取走**确定性规则组件**；AI 文本类能力降级 | ❌ 同上 |
| MinIO | 上传原件回落落盘 `UPLOAD_STORAGE_DIR=data/uploads` | ❌ 不影响 |

> ⇒ **免费机部署的正确预期**是「档案管理平台」可用，而不是「AI 检索问答全功能」。要全功能需 4 核 8G 起，且 AI 侧建议云 GPU / 外部 API（合规通道默认关闭，需在 `.env` 显式开启出网）。

---

## 2. 必须安装的软件与版本

| 软件 | 版本要求 | 本项目本机实测 | 服务器安装方式（Ubuntu 22.04/24.04） |
| :-: | --- | --- | --- |
| Python | **3.11 ~ 3.13**（3.13 已验证） | 3.13.14（venv） | `apt install -y python3-venv python3-dev`（Ubuntu 24.04 自带 3.12） |
| pip | 随 Python | — | `python3 -m ensurepip --upgrade` |
| MySQL | **8.0.x**（需支持 RANGE 分区，5.7 亦可但未验证） | 8.0.27 | `apt install -y mysql-server` |
| Redis | **5.0+**（代码固定用 RESP2 协议，兼容性最好） | 7.x | `apt install -y redis-server` |
| Nginx | 1.18+ | 未装（本机用 Vite 开发） | `apt install -y nginx` |
| Node.js | **≥ 20**（仅**重建前端**时需要；直接上传 `dist` 则不需要） | 22.22.2 | `apt install -y nodejs npm` 或 nodesource |
| Git | 任意 | 2.55.0 | `apt install -y git` |
| Docker + Compose | 可选（方案 B 才需要） | 29.8.0 / v5.5.1 | 官方脚本；**2 核 2G 不建议** |
| 系统时区 | **必须 Asia/Shanghai** | — | `timedatectl set-timezone Asia/Shanghai` |

> 🔴 **Python 3.14 不要用**：本机 `D:\miniconda3` 是 3.14.6，但项目 venv 用的是 3.13.14。部分依赖（`pymilvus` 系）对新版本解释器尚未提供轮子，服务器上请用系统自带 3.12 或 3.13。

---

## 3. 部署拓扑

### 3.1 方案 A · 宿主直装（**免费服务器推荐**）

```
公网 ──► 阿里云安全组(22/80/443) ──► Nginx :80
                                      ├─ /            → /opt/talent/frontend/dist（静态 SPA）
                                      ├─ /api/        → 127.0.0.1:8002（uvicorn，systemd 守护）
                                      └─ /health      → 127.0.0.1:8002/health
                                                          │
                              MySQL :3306 ◄───────────────┤（仅监听 127.0.0.1）
                              Redis :6379 ◄───────────────┘（仅监听 127.0.0.1，带密码）
```

### 3.2 方案 B · Docker Compose（⚠️ **不能原样使用**）

`app/deploy/docker-compose.dev.yml` 会拉起 **mysql + redis + minio + etcd + milvus** 五个服务；
其中 `etcd + milvus` 在 **2~4G 内存的免费机上会直接 OOM / 反复重启**。

要用 compose，**必须先裁剪成最小集**（自行保存为 `docker-compose.cloud.yml` 后使用）：

```yaml
# 最小集：仅 mysql + redis（其余服务删除或注释）
services:
  mysql:
    image: mysql:8.0
    command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci", "--default-time-zone=+08:00", "--innodb-buffer-pool-size=256M", "--performance-schema=OFF"]
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?必须设置}
      MYSQL_DATABASE: ${MYSQL_DATABASE:-talent_platform_v2}
    ports: ["127.0.0.1:3306:3306"]        # 🔴 只绑本机，不要 0.0.0.0
    volumes: ["mysql-data:/var/lib/mysql", "./mysql-init:/docker-entrypoint-initdb.d:ro"]
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${REDIS_PASSWORD:?必须设置}"]
    ports: ["127.0.0.1:6379:6379"]        # 🔴 同上
    volumes: ["redis-data:/data"]
volumes: { mysql-data: {}, redis-data: {} }
```

> 无论哪种方案：**`deploy/nginx.conf` 的 upstream 必须改**。原文件写的是 `host.docker.internal:8002`
> （为「Nginx 跑在 Docker 里」设计）；若 Nginx 是**宿主机直装**，要改成：
> ```nginx
> upstream talent_backend { server 127.0.0.1:8002; keepalive 32; }
> ```

---

## 4. 分步部署（方案 A，逐条可复制）

### 4.1 上传与解包

```bash
# 本地（Windows Git Bash）打包上传。注意：上传的是 cloud-package/app 下的三块
cd D:/gan/16个人项目/cloud-package
tar czf talent-app.tgz app/backend app/frontend/dist app/deploy
scp talent-app.tgz root@<公网IP>:/tmp/

# 服务器
mkdir -p /opt/talent && cd /opt/talent
tar xzf /tmp/talent-app.tgz --strip-components=1
# 得到 /opt/talent/{backend,frontend/dist,deploy}
```

### 4.2 系统初始化

```bash
timedatectl set-timezone Asia/Shanghai
# 🔴 2G 内存必须加 swap，否则 pip 编译 / 构建时会 OOM
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
apt update && apt install -y python3-venv python3-dev mysql-server redis-server nginx
```

### 4.3 MySQL：建库 + 审计专用账号

```bash
# 🔴 Alembic 只建表、不建库，必须手工建库（字符集/排序规则与 compose 注释一致）
mysql -uroot <<'SQL'
CREATE DATABASE IF NOT EXISTS talent_platform_v2
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '<强密码>';
FLUSH PRIVILEGES;
SQL

# 建「审计专用库账号」——只 SELECT + INSERT，是「审计只追加」的第三层保证
bash /opt/talent/deploy/mysql-init/01-audit-account.sh
```

MySQL 调优（`/etc/mysql/mysql.conf.d/mysqld.cnf`）：

```ini
[mysqld]
bind-address            = 127.0.0.1        # 🔴 绝不对公网监听
character-set-server    = utf8mb4
collation-server        = utf8mb4_unicode_ci
default-time-zone       = '+08:00'         # 🔴 必须东八区：分区表按 created_at 分月，UTC 会错月
innodb_buffer_pool_size = 256M             # 2G 机型不要用默认大值
performance_schema      = OFF
```

### 4.4 Redis

```bash
sed -i 's/^# requirepass .*/requirepass <强密码>/' /etc/redis/redis.conf
sed -i 's/^bind .*/bind 127.0.0.1 -::1/' /etc/redis/redis.conf
systemctl restart redis-server
```

### 4.5 后端

```bash
cd /opt/talent/backend
python3 -m venv .venv                       # ⚠️ Windows 的 .venv 不可复用，必须重建
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt   # 见 §5「选择 A/B」
# 选择 B（要向量检索）再加：.venv/bin/pip install pymilvus==3.0.2

cp .env.example .env && chmod 600 .env      # 然后按 §5 填值
.venv/bin/python -m alembic upgrade head    # 建表（14 个迁移）
.venv/bin/python scripts/seed_dev.py        # 种子：租户/角色/权限/字典/AI 配置/首个管理员
```

systemd 守护（`/etc/systemd/system/talent-api.service`）：

```ini
[Unit]
Description=Talent Platform API
After=network.target mysql.service redis-server.service

[Service]
Type=simple
WorkingDirectory=/opt/talent/backend
ExecStart=/opt/talent/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
Restart=always
RestartSec=5
User=root
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now talent-api
curl -s http://127.0.0.1:8002/health
```

> 🔴 绑 `127.0.0.1` 而非 `0.0.0.0`：后端只经 Nginx 对外，**不要**把 8002 暴露到安全组。

### 4.6 前端与 Nginx

```bash
mkdir -p /var/www/talent
cp -r /opt/talent/frontend/dist/* /var/www/talent/

cp /opt/talent/deploy/nginx.conf /etc/nginx/conf.d/talent.conf
# 🔴 两处必须改（否则 502）：
#   ① upstream → server 127.0.0.1:8002;
#   ② root     → /var/www/talent;
sed -i 's#host.docker.internal:8002#127.0.0.1:8002#' /etc/nginx/conf.d/talent.conf
sed -i 's#/usr/share/nginx/html#/var/www/talent#'          /etc/nginx/conf.d/talent.conf
nginx -t && systemctl reload nginx
```

**若要用 SSE（导入进度 / RAG 流式），必须额外关掉 Nginx 缓冲**（原 `nginx.conf` 里这段是注释状态）：

```nginx
location /api/ {
    proxy_pass http://talent_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;              # ← 关键：否则前端会「转圈很久后一次性吐出全部」
    chunked_transfer_encoding on;
    proxy_read_timeout 3600s;
}
```

### 4.7 安全组 / 防火墙

| 端口 | 是否对公网开放 | 说明 |
| :-: | :-: | --- |
| 22 | ✅ 建议限制来源 IP | SSH |
| 80 / 443 | ✅ 开放 | 站点入口 |
| 8002 | ❌ **不开放** | 后端只被 Nginx 反代 |
| 3306 / 6379 | ❌ **绝对不开放** | 数据库/缓存；绑 127.0.0.1 且安全组不放行 |
| 9000/19530 等 | ❌ | 未部署 |

---

## 5. 配置清单（**照此表逐项填**）

### 5.1 `backend/.env`（复制自 `.env.example`）

| 变量 | 本机值 | 云端应填 | 说明 |
| --- | --- | --- | --- |
| `APP_ENV` | `dev` | **`prod`** | 影响行为分支 |
| `DEBUG` | `true` | **`false`** | |
| `MYSQL_HOST/PORT` | `127.0.0.1:3306` | 同（若用容器则改为容器名/host） | |
| `MYSQL_PASSWORD` | 空 | **强密码** | |
| `MYSQL_DATABASE` | `talent_platform_v2` | 不变 | ⚠️ 不要用旧库名 `talent_platform`（结构冲突，见 .env.example 注释） |
| `REDIS_PASSWORD` | 空 | **强密码** | 与 `redis.conf` 一致 |
| `JWT_SECRET_KEY` | 占位符 | 🔴 **必须换**：`python3 -c "import secrets;print(secrets.token_urlsafe(48))"` | |
| `AES_KEY` | 空 | 🔴 **必须生成**（64 位 hex）：`python3 -c "import secrets;print(secrets.token_hex(32))"` | 不填则写手机号/邮箱直接报错（刻意 fail-loud） |
| `CONTACT_HASH_SALT` | 空 | 🔴 **必须生成**：`python3 -c "import secrets;print(secrets.token_hex(16))"` | ⚠️ **一经使用不可更换**（换盐后已入库哈希全失效）；且 ≠ `AES_KEY` |
| `AUDIT_DB_USER/PASSWORD` | 空 | `audit_writer` + 强密码 | 留空＝退回主账号（功能正常，第三层保证不生效） |
| `SNOWFLAKE_WORKER_ID` | `1` | `1`（单实例） | ⚠️ 多实例必须互不相同，否则撞号；合法 0~31 |
| `CORS_ORIGINS` | `http://localhost:5173` | **留空或填正式域名** | 🔴 同源部署**不需要 CORS**，放开等于给所有站点带 Cookie 读写权限 |
| `UPLOAD_MAX_FILE_SIZE_MB` | `50` | `50` | 🔴 必须与 Nginx `client_max_body_size` **一致** |
| `UPLOAD_STORAGE_DIR` | `data/uploads` | 不变（相对路径） | 确保该目录可写且有磁盘余量 |
| `MINIO_*` | 空 | 不部署则留空 | 留空 ⇒ 原回落盘 |
| `MILVUS_*` / `OLLAMA_*` / `RERANKER_*` | 本机地址 | **不部署则保留默认**（不可达即降级） | 不要删掉这些键（配置项守卫会检查键集） |
| `CLOUD_LLM_*` | 空 | 默认留空 | 🔴 **留空即禁用出网**（合规默认）；开启需配套脱敏审批 |

### 5.2 `deploy/.env`（仅方案 B 用）与前端

| 项 | 说明 |
| --- | --- |
| `MYSQL_ROOT_PASSWORD` | 必须与 `backend/.env` 的 `MYSQL_PASSWORD` **一致**，否则「容器里的库」与「后端连的库」是两个世界 |
| `REDIS_PASSWORD` / `MINIO_*` | 同上，成对一致 |
| 前端 `VITE_API_BASE_URL` | **保持 `/api/v1`**（相对路径）；改成 `http://ip:8002/api/v1` 会立刻引入跨域与 Cookie 策略变更 |
| 前端重建 | `npm ci && npm run build`（Node ≥20）。当前 `dist` 已构建好，**可直接用，无需重建** |

---

## 6. 阿里云环境特有注意事项

| # | 事项 | 为什么 / 怎么做 |
| :-: | --- | --- |
| 1 | **内存是唯一的硬约束** | 2G 机型务必加 2G swap、MySQL 降 buffer pool、uvicorn **单 worker**（`--workers` 不要加） |
| 2 | **时区必须 +08:00** | `audit_log` / `ai_call_log` 按月 RANGE 分区；UTC 会让记录掉进错误月份分区 |
| 3 | **字符集 utf8mb4 / utf8mb4_unicode_ci** | 库、表、连接三处一致，否则中文与 emoji 出错 |
| 4 | **数据库端口绝不对外** | 3306/6379 绑 `127.0.0.1` + 安全组不放行；公网暴露的 MySQL 通常几小时内被爆破 |
| 5 | **上传上限三处对齐** | Nginx `client_max_body_size 50m` ⟷ 后端 `UPLOAD_MAX_FILE_SIZE_MB=50`。只改一处 ⇒ 大文件在 Nginx 层被 413 拦下，**后端一条日志都没有** |
| 6 | **SSE 必须 `proxy_buffering off`** | 否则导入进度条与 RAG 流式会「转圈很久后一次性吐出」（看起来像后端没做流式） |
| 7 | **`index.html` 不缓存、`/assets/*` 长缓存** | 反过来会导致发版后白屏（旧 HTML 引用已被清掉的旧 hash 资源）—— 原配置已正确，别改 |
| 8 | **Alembic 不建库** | 必须先手工 `CREATE DATABASE` |
| 9 | **`.env` 权限 600 + 不进镜像** | 密钥只经环境变量注入 |
| 10 | **带宽 3~4M 够用但别浪费** | 打开 Nginx gzip（`gzip on; gzip_types text/css application/javascript application/json;`），首屏 JS 约 3.2 MB → 压缩后 <1 MB |
| 11 | **HTTPS** | 阿里云可申请免费 DV 证书；443 监听 + 把 80 跳转 443。SSE 与 Cookie 都建议在 HTTPS 下 |
| 12 | **进程守护用 systemd** | 不要 `nohup &`：免费机重启/关机后无人拉起 |
| 13 | **免费试用到期会释放实例** | 数据随之丢失 ⇒ 提前 `mysqldump` + 阿里云快照，或尽快续费/迁移 |
| 14 | **不要用 Windows Server 镜像** | 本机 `.venv`、`.sh` 脚本、`deploy/partition-ensure.bat` 的 Linux 对应物都按 Linux 设计 |
| 15 | **分区定时任务要配** | `deploy/partition-ensure.sh` 挂 cron（每月 1 日 03:00，**带 `--table all`**），否则分区耗尽后数据全进 `pmax`，丧失归档能力 |
| 16 | **首次登录后立即改默认管理员密码** | `seed_dev.py` 会打印初始账号密码 |

---

## 7. 冒烟验收（部署完成即跑）

```bash
curl -s http://127.0.0.1:8002/health                     # 期望 code=0；依赖异常时 50301 + degraded=true
curl -s http://127.0.0.1:8002/openapi.json | head -c 200 # 期望看到 paths
curl -s http://<公网IP>/nginx-health                     # Nginx 存活
curl -s http://<公网IP>/                                 # 应返回前端 index.html
```

- [ ] `/health` 中 `dependencies.mysql` 与 `dependencies.redis` **均为 `true`**
- [ ] 浏览器打开 `http://<公网IP>/` 能看到登录页，用 `seed_dev.py` 打印的管理员账号登录成功
- [ ] **列表 → 点进详情**（CR-5 雪花 ID 精度冒烟：这是唯一能当场暴露 JS 精度丢失的检查）
- [ ] 导入 1 份简历，观察导入进度（验证 SSE 与 Nginx 缓冲配置）
- [ ] `python -m scripts.audit_partition --list --table all`（在服务器 `backend/` 下用 `.venv/bin/python`）看到本月 + 后续 ≥2 个月分区

---

## 8. 常见故障对照表

| 现象 | 最可能的根因 | 处置 |
| --- | --- | --- |
| 打开站点是 Nginx 欢迎页 | `default` 站点未被覆盖 / `talent.conf` 未生效 | 删 `/etc/nginx/sites-enabled/default`；`nginx -t && systemctl reload nginx` |
| 前端能开，接口全部 502 | upstream 仍指向 `host.docker.internal:8002` | 改成 `127.0.0.1:8002` |
| 前端能开，接口 404 | `/api` 反代前缀或 `root` 路径写错 | 检查 `location /api/` 与 `proxy_pass`（**结尾不带斜杠**才保留 `/api` 前缀） |
| 刷新子路由白屏/404 | SPA 回退或缓存策略被改 | 确认 `try_files $uri $uri/ /index.html;` 且 `index.html` 为 `no-store` |
| 后端起不来，日志 `Access denied for user` | MySQL 密码/用户名不匹配 | 对齐 `backend/.env` 与 MySQL 实际账号 |
| 后端起不来，`unknown command 'HELLO'` | Redis 太旧而 redis-py 用了 RESP3 | 保持 `REDIS_PROTOCOL=2`（本项目默认已是） |
| `/health` 返回 `degraded=true` | MySQL 或 Redis 连不上 | 看 `logs/uvicorn.*.log`；确认服务已起、密码对、端口对 |
| 写手机号/邮箱直接报错 | `AES_KEY` / `CONTACT_HASH_SALT` 未填 | 按 §5.1 生成并填入（这是刻意的 fail-loud） |
| 导入进度条一直转圈，最后一次性出结果 | Nginx 缓冲未关 | `proxy_buffering off`（见 §4.6） |
| 上传大文件报 413（英文页） | Nginx `client_max_body_size` < 后端上限 | 两处都调到 50m 并 reload |
| 构建/安装中途被 Killed | 内存不足触发 OOM Killer | 加 swap；用 `pip install --no-cache-dir`，避免并发编译 |
| `pymilvus` 装不上 / Milvus 连不上 | 免费机未部署 Milvus | **这是预期行为**：检索降级为关键词模式，不影响其他功能 |

---

## 9. 明确未纳入（不静默带过）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 生产三区分离拓扑（应用区/数据区/AI 算力区） | ❌ 未做 | 架构 §7.2 描述的是生产拓扑，**待 Q-3 国产化清单**回来才能定镜像与信创适配 |
| MySQL 主从 / 读写分离 | ❌ 未做 | 一期开发环境不需要 |
| HTTPS 证书自动续期 | ❌ 未做 | 需域名与证书来源 |
| 分区**恢复**演练 | ❌ 未做 | 归档是 TSV，恢复需先建分区再 `LOAD DATA LOCAL INFILE`；「有文件」≠「能恢复」 |
| 向量检索 / RAG 全功能 | 🔵 受限 | 需 Milvus + Ollama，免费机不满足；见 §1.2 |
