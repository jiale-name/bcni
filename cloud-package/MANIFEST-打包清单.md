# 打包清单 · MANIFEST

> **本包用途**：把「一期已完成的项目」整理成一份可上传到云端服务器（阿里云）的部署包。
> **生成方式**：项目根 `_make_cloud_package.py`（**纯复制**：只读源目录，不做任何删除）。
> **生成时间**：2026-10-04 14:12 ｜ **源目录**：`D:\gan\16个人项目`
> **包体积**：约 **13.97 MB / 467 个文件**（不含需在服务器端重建的 `node_modules` 与 `.venv`）

---

## 一、目录总览

```
cloud-package/
├── README-部署说明.md            ← 怎么部署到阿里云（软件 / 版本 / 配置 / 注意事项）
├── MANIFEST-打包清单.md          ← 本文件：打包了什么、排除了什么、为什么
├── 一期功能与依赖概览.md          ← 一期已完成功能清单 + 文件结构 + 依赖关系
│
├── app/                          ← ★ 真正要上传到服务器的内容（396 文件 / 6.67 MB）
│   ├── backend/                  ←   后端源码（FastAPI，端口 8002）
│   │   ├── app/                  ←     六层主代码（128 文件）
│   │   ├── alembic/              ←     迁移链（17 文件，含 versions/ 共 14 个版本）
│   │   ├── scripts/              ←     运维/自检 CLI（24 文件：seed_dev / audit_partition / acceptance_check …）
│   │   ├── tests/                ←     契约与集成测试（48 文件）
│   │   ├── alembic.ini · pyproject.toml · requirements.txt
│   │   ├── .env.example          ←     配置模板（**真实 .env 不进包**）
│   │   └── README.md             ←     后端说明（含排障手册）
│   ├── frontend/                 ←   前端（Vue3 + Vite）
│   │   ├── dist/                 ←     ★ 构建产物（89 文件 / 3.2 MB），Nginx 直接托管
│   │   ├── src/                  ←     源码（69 文件），服务器上重建时用
│   │   ├── index.html · vite.config.ts · tsconfig*.json
│   │   ├── package.json · package-lock.json
│   │   └── .env.example
│   └── deploy/                   ←   部署产物（9 文件）
│       ├── docker-compose.dev.yml    数据区编排（MySQL/Redis/MinIO/etcd/Milvus）
│       ├── nginx.conf                生产入口：托管 dist + 反代 /api → 8002
│       ├── mysql-init/01-audit-account.sh   审计专用库账号（只 SELECT+INSERT）
│       ├── partition-ensure.sh / .bat       分区补足定时任务
│       ├── archive-partitions.sh            分区归档（先转储校验再 DROP）
│       ├── .env.example
│       └── README.md                部署说明（与 §README-部署说明.md 互补）
│
├── docs/                         ← 设计与交付文档（33 文件 / 6.13 MB，**可不传服务器**）
│   ├── 需求分析（md + html）· 架构设计 · 数据库设计 · 接口设计
│   ├── 编码前评审文档（进度台账唯一事实源）· 开发计划 · 测试用例与验收脚本
│   ├── 数据流图与类图时序图 · 图运行时选型评估 · 金标准集标注规范
│   ├── N0 基线实测报告（0922 / 0923，md + json）
│   ├── 4 张链路图（png）
│   ├── superpowers/plans/       测评模块的 4 份实施计划（二期线）
│   └── 人才测评分析Agent/       测评模块设计文档 7 份（二期线）
│
├── design/                       ← 设计资产（31 文件 / 1.08 MB，可不传服务器）
│   ├── prototype/               批次 14 定稿的多页原型（p01~p26，27 个 html + css/js）
│   └── AI数字化人才平台-架构设计.html
│
└── env/                          ← 环境与依赖清单（7 文件 / 0.09 MB）
    ├── host-versions.txt                  本机开发环境版本快照（含 netstat 实测状态）
    ├── backend-requirements.txt           后端**声明**依赖（范围版本，来自项目）
    ├── backend-requirements-resolved.txt  后端**实际**依赖（pip freeze，62 个精确版本）⚠️ 两者不一致，见 §四
    ├── backend-env.example                后端环境变量模板（唯一配置清单）
    ├── frontend-package.json / frontend-package-lock.json
    └── frontend-env.example               前端环境变量模板
```

---

## 二、三段式使用建议

| 段落 | 是否必须上传服务器 | 说明 |
| --- | :-: | --- |
| `app/` | ✅ **必须** | 部署正体；其中 `app/frontend/dist` 是可直接托管的静态产物 |
| `env/` | 🟡 建议 | 配置模板与依赖版本对账，部署时照着填 |
| `docs/` + `design/` | ⬜ 可选 | 交付/答辩用；服务器上不需要，可只留在本机 |

---

## 三、排除清单（**已核对，均为有意排除**）

| 排除项 | 体积 | 为什么排除 | 服务器上如何补齐 |
| --- | --- | --- | --- |
| `backend/.venv/` | 271 MB | Windows 二进制，**跨平台不可用**（Linux 用不了 `Scripts/*.exe`） | 在服务器上 `python -m venv .venv` + `pip install -r requirements.txt` |
| `frontend/node_modules/` | 218 MB | 含大量平台相关二进制（esbuild/rollup），且 `npm ci` 可完全重建 | `npm ci`（优先）或 `npm install` |
| `backend/__pycache__/`、`*.pyc` | — | 字节码缓存，无意义且含本机路径 | 自动生成 |
| `backend/data/uploads/` | 8.9 MB / 275 文件 | 🔴 **含真实简历原件（个人信息）**，不得随包流转 | 由导入功能在服务器上重新产生 |
| `backend/.env` | 11 KB | 🔴 **含真实密钥**（DB 密码 / JWT 密钥 / AES 密钥 / 盐） | 复制 `.env.example` 后重填，见 README §五 |
| `backend/.env.bak.*` | — | 同上（历史密钥备份） | — |
| `backend/logs/`、`tmp/`、`.pytest_*/`、`*.junit*.xml`、`*.log` | 约 18 MB | 运行期与测试产物，非交付物 | 运行后自动生成 |
| `deploy/.env`（若存在） | — | 部署密钥 | 复制 `deploy/.env.example` 后重填 |
| 根 `data/goldset/` | — | 🔴 **含个人信息的人工标注数据**（设计文档明确禁止入库/出包） | 仅留在本机 |
| 根目录 `*.docx` / `_*.py` / `assets/` | 约 0.4 MB | 需求书原件与文档生成脚本，非运行依赖 | 无需 |
| `.idea/`、`.pytest-junit*.xml`（根层） | — | 编辑器与测试报告 | — |

> **安全自检结果**（打包后实测）：真实 `.env` = 0 · `uploads` 命中 = 0 · `node_modules` = 0 · `.venv` = 0 · `*.pyc` = 0 · junit 报告 = 0。
> `goldset` 命中 8 项，**经逐条核对全部是工具代码**（`scripts/goldset/*.py` 与 `tests/test_goldset.py`），**不含任何标注数据**。

---

## 四、⚠️ 依赖不一致（打包时发现，部署前必须拍板）

`backend/requirements.txt`（声明）与本机 `.venv`（实际 62 个包）**不一致**，差额如下：

| 包 | 在 requirements.txt | 在本机 venv | 代码是否真的 import | 结论 |
| --- | :-: | :-: | --- | --- |
| `pymilvus` | ❌ 无 | ✅ 3.0.2 | ✅ **有**（`app/retrieval/milvus_provider.py:51`，**延迟导入**） | 装了才走向量检索；不装则**自动降级**为关键词检索（代码本就 fail-open，不报错） |
| `pandas` / `numpy` | ❌ 无 | ✅ 3.0.6 / 2.5.3 | ❌ 全仓无 import | 是 `pymilvus` 的传递依赖，非项目直接依赖 |
| `grpcio` / `protobuf` | ❌ 无 | ✅ 1.84.0 / 7.36.2 | ❌ | 同上（gRPC 通道） |
| `pycryptodome` | ❌ 无 | ✅ 3.23.0 | ❌ | `minio` SDK 的依赖 |
| `argon2-cffi` | ❌ 无 | ✅ 25.1.0 | ❌ | 传递依赖（非项目直接使用） |
| `langchain` 系 | ❌ 无 | ❌ **也没有** | ❌ | 一期**沿用自研图运行时**（已拍板），符合预期 |

**部署含义（二选一）**：

- **选择 A · 最小部署（推荐免费服务器）**：只装 `requirements.txt` ⇒ 不装 `pymilvus` ⇒ 检索功能**自动降级为关键词模式**（返回 `code=50302` + `degraded=true`），其余功能全部正常。适合 2 核 2G 的免费机。
- **选择 B · 完整能力**：额外 `pip install pymilvus==3.0.2`（会连带 pandas / numpy / grpcio，约 +250 MB）⇒ 需要 Milvus 服务，且**内存要求显著上升**（免费机基本放不下，见 README §一）。

---

## 五、重新生成 / 增量更新

```bash
cd D:/gan/16个人项目
"C:/Users/Windows/.workbuddy/binaries/python/versions/3.13.12/python.exe" _make_cloud_package.py
```

- 脚本**幂等**：重复运行只覆盖同名文件，不删除任何东西；
- 脚本**零删除**：全程只用 `shutil.copy*`，不会碰源目录；
- 新增了源码目录 / 文档后要同步更新脚本里的**白名单**（`main()` 里的 `copy_dir` / `copy_files` 调用）。
