# 开发协作规范 · Git 分支策略与提交规范

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 编写日期 | 2026-10-12 |
| 适用范围 | `D:\gan\16个人项目`（2026 顶配版 AI 数字化人才平台）全仓库 |
| 关联文档 | `AI数字化人才平台-编码前评审文档.md`（§7.1 台账 / §7.2 批次 / §7.3 批次记录）、`AI数字化人才平台-开发计划.md`（WBS）、`docs/人才画像智能体agent/需求分析文档.md` |
| 目标读者 | 全部参与编码、测试、文档与部署的成员 |

---

## 1. 目的

本仓库需要同时支撑三件事，分支与提交规范必须**同时**满足：

1. **敏捷迭代**：多模块（基座 / 档案画像 / 测评 / 画像智能体…）并行推进，互不阻塞。
2. **可回溯**：每个编码批次（§7.2 批次）与编码单元（CU-nn）都能在 Git 历史中精确定位。
3. **可交付**：任何时候 `main` 都代表"已验证可交付基线"，可随时打包上线。

因此采用 **Git Flow 轻量版**（保留 `main` / `develop` / `feature` / `release` / `hotfix` 五类分支），并把**提交规范与项目既有编号体系（CU-nn / 批次 / API-nn / TBL-nn）显式绑定**。

---

## 2. 分支模型

```text
main ─────●────────────────●──────────────●─────────────►  可交付基线（受保护）
          │                ▲              ▲
          │                │ release/0.2  │ hotfix/xxx
          ▼                │              │
develop ──●────●────●──────●──────────────┴─────────────►  集成分支（受保护）
               ▲    ▲
               │    │
     feature/… ─┘    └─ feature/…                         功能分支（短生命周期）
```

| 分支 | 用途 | 从哪切 | 合入哪 | 生命周期 | 保护 |
| --- | --- | --- | --- | --- | :-: |
| `main` | 生产 / 交付基线，每次合入即打 tag | — | — | 永久 | 🔒 受保护 |
| `develop` | 敏捷集成主干，日常集结点 | `main` | — | 永久 | 🔒 受保护 |
| `feature/*` | 单个功能 / 单个 CU 的开发 | `develop` | `develop` | 短（≤ 1 个批次） | — |
| `release/*` | 发布候选与验收加固（只修不新增功能） | `develop` | `main` + `develop` | 中 | — |
| `hotfix/*` | 生产紧急修复 | `main` | `main` + `develop` | 短 | — |
| `docs/*` | 纯文档变更（§7.3 留痕、设计文档修订） | `develop` | `develop` | 短 | — |

### 2.1 命名规范

```text
feature/<编号>-<英文短描述>     例：feature/cu-29-profile-aggregate
feature/<模块>-<英文短描述>     例：feature/assessment-attempt-freeze
release/<语义版本>              例：release/0.2.0
hotfix/<问题号>-<英文短描述>    例：hotfix/1003-fake-transaction
docs/<主题>                     例：docs/profile-requirement
```

硬性规则：

1. **分支名一律 ASCII**（英文小写 + 短横线）。中文分支名在 Windows / Gitee / CI 上易出现乱码与推送失败。
2. **一个 feature 分支只做一件事**：对应一个 CU 或一个紧密耦合的模块闭环，不跨批次堆积（承接约束性文档"每批只实现一个闭环"）。
3. 分支名中的编号优先用 **CU-nn**；无 CU 时用模块名。

---

## 3. 提交规范（Conventional Commits）

### 3.1 格式

```text
<type>(<scope>): <subject>

<body>

<footer>
```

### 3.2 type（必选，取小写）

| type | 含义 | 本项目示例 |
| --- | --- | --- |
| `feat` | 新增功能 | 新增画像维度配置接口 |
| `fix` | 修复缺陷 | 修复幂等键被短路导致的重试失效 |
| `refactor` | 重构（不改外部行为） | 拆出画像描述节点 |
| `perf` | 性能优化 | 画像列表查询加索引 |
| `docs` | 仅文档 | 回填 §7.3 批次记录 |
| `test` | 仅测试 | 补画像聚合重复一致性用例 |
| `chore` | 构建 / 依赖 / 脚手架 | 初始化仓库、升级依赖 |
| `build` | 构建系统 / 打包 | 调整 vite 构建配置 |
| `ci` | CI 配置 | 增加全量回归流水线 |
| `revert` | 回滚提交 | 回滚某次提交 |
| `style` | 格式（无逻辑变更） | 统一缩进 |

### 3.3 scope（必选，标明影响域）

`backend` · `frontend` · `db`（迁移/模型）· `agent` · `assessment` · `profile` · `docs` · `deploy` · `repo` · `deps`

### 3.4 subject（必选）

- 使用**简体中文**，动宾结构，一句话说清"做了什么"。
- 不超过 50 字，**句末不加句号**。
- 不写"修改bug""优化代码"这类无信息量的描述。

### 3.5 body / footer

- **body**：说明"为什么"（取舍、踩坑、影响面），而非复述代码。
- **footer**：关联项目编号，供台账对账。约定关键字：

```text
CU: CU-29
批次: 批次 25（画像智能体第一批）
Refs: TBL-47, API-160~165
Closes: #12
BREAKING CHANGE: <说明>       # 破坏性变更必须写，并与 ! 标记（如 feat(db)!:）
```

### 3.6 示例

```text
feat(db): 新增画像维度与配置表（TBL-47 / TBL-48）

- 维度支持等级规则与默认权重；配置按 version 冻结快照
- 配置发布后不可原地修改，变更须创建新版本

CU: CU-29
批次: 批次 25
Refs: TBL-47, TBL-48
```

```text
fix(profile): 修复测评报告重复投递导致画像重复出账

幂等键改为 talent_id:profile:v{config_version}:{source_version}，
重算入口先作废旧键再执行，避免回放短路后被当成已处理。

CU: CU-30
```

```text
docs(评审): 回填 §7.3 批次 25 记录

CU: CU-29
```

---

## 4. 提交粒度与纪律

1. **小步提交**：一个可运行、可测试的最小单元一次提交；不把"半天工作量"堆成一次。
2. **每次提交保持可运行**：不留半成品入口；无法独立运行的中间态不进 `develop`。
3. **绝不提交机密**：`.env` / 密钥 / 证书 / `data/uploads` 内简历原文**永不入库**（`.gitignore` 已兜底，但不得靠它兜底）。
4. **文档与代码同批提交**：改了契约就同批改文档（本项目"文档漂移"是历史高发缺陷）。
5. **提交信息即台账线索**：`CU:` 行必须真实，供 `git log --grep` 反查。

---

## 5. 工作流

### 5.1 日常功能开发

```bash
git switch develop
git pull --ff-only
git switch -c feature/cu-29-profile-aggregate

# … 小步提交 …
git add -A && git commit -m "feat(profile): 新增画像维度配置接口"

# 同步最新基线后集成
git switch develop && git pull --ff-only
git switch feature/cu-29-profile-aggregate && git rebase develop

# 推送并发起 Pull Request（合入 develop）
git push -u origin feature/cu-29-profile-aggregate
```

### 5.2 发布

```bash
git switch -c release/0.2.0 develop
# 只修缺陷与验收加固，不新增功能
git commit -m "chore(release): 0.2.0 发布前加固"
git switch main && git merge --no-ff release/0.2.0
git tag -a v0.2.0 -m "0.2.0 画像智能体第一批"
git switch develop && git merge --no-ff release/0.2.0
```

### 5.3 紧急修复

```bash
git switch -c hotfix/1003-fake-transaction main
# 修复 + 补回归用例
git switch main && git merge --no-ff hotfix/1003-fake-transaction
git tag -a v0.2.1 -m "0.2.1 紧急修复"
git switch develop && git merge --no-ff hotfix/1003-fake-transaction
```

### 5.4 合并方式

| 场景 | 方式 | 理由 |
| --- | --- | --- |
| `feature/*` → `develop` | **Squash merge**（压缩为 1 个提交） | 保持集成分支线性、易回溯 |
| `release/*` → `main` | `--no-ff` | 保留发布边界，便于回滚整批 |
| `hotfix/*` → `main` | `--no-ff` | 同上 |

- **禁止**对 `main` / `develop` 执行 `push --force`。
- `feature` 分支 rebase 前必须先 `pull --ff-only` 同步 `develop`。

---

## 6. 与编码批次 / 编号体系的绑定

本项目以《编码前评审文档》为准绳，Git 与之对齐如下，形成"**台账 ↔ 分支 ↔ 提交 ↔ 标签**"的四向可追溯链：

| 项目概念 | Git 载体 | 约定 |
| --- | --- | --- |
| 编码批次（§7.2 第 N 批） | `release/x.y.z` 的合并边界 | 每批验收通过后，由 `develop` 切 `release` 并打 tag |
| 编码单元 CU-nn | 一个 `feature/cu-nn-*` 分支 | 分支合入即台账标"编码已完成" |
| 缺陷修复 | `fix(...)` 提交或 `hotfix/*` | 提交 body 写明现象、根因、验证方式 |
| 设计文档修订 | `docs/*` 分支 | 每次修订更新修订记录并 🔁 留痕 |
| 阶段 N0~N7 | tag 前缀或发布说明 | 在 release note 中标注所处阶段 |

推荐 tag 策略（语义化版本）：

```text
v0.1.0   一期基座 + 档案画像模块交付
v0.2.0   人才测评分析 Agent 上线
v0.3.0   人才画像智能体上线（本智能体）
v0.3.x   画像智能体缺陷修复
```

---

## 7. 评审（Pull Request）纪律

1. **目标分支**：`feature` / `docs` → `develop`；`release` / `hotfix` → `main`。
2. **PR 必备内容**：
   - 关联的 `CU-nn` / 批次；
   - 变更范围与影响面（是否动 Router / `core` / 新模块）；
   - 验证方式（**测试计数只认 `--junitxml` 的 failures/errors/skipped = 0**）；
   - 是否新增/改动迁移，如何回滚。
3. **合入前必做**：
   - 分层守卫必跑：改动 Router / `core` / 新增模块文件时，针对性复跑**必带** `tests/test_layering.py`；
   - 新增测试文件必须同步登记 `test_layering.MODULE_FILES` 与测试文档 §2.1（S-08 对账）；
   - 需要时执行干净全量回归（**先停在跑后端** + `SNOWFLAKE_WORKER_ID` 合法且 ≠1）。
4. **建议开启分支保护**（Gitee / GitHub）：`main` / `develop` 禁止直接推送、禁止 force push、合并需 PR。

---

## 8. 安全与常见问题处置

| 现象 | 处置 |
| --- | --- |
| 误把 `.env` 提交 | **立即轮换其中的口令 / 密钥**（历史重写不能挽回已泄露的凭据），再 `git rm --cached .env` 并确认 `.gitignore` 生效 |
| 行尾（LF/CRLF）大面积抖动 | 以 `.gitattributes` 为准；执行 `git add --renormalize .` 后单独提交一次 |
| 误提交隐私数据（简历原文） | 立即从版本库移除并评估合规影响（承接 F-3.11 个人权利响应纪律） |
| 大文件误入（>50MB） | 用 `git rm --cached` 移除并补 `.gitignore`；已推送的历史用 `git filter-repo` 清理 |
| 忘了建分支，直接在 `develop` 上改了 | 若未提交：`git stash` → 切分支 → `git stash pop`；若已提交：`git switch -c feature/xxx && git switch develop && git reset --hard origin/develop` |

---

## 9. 命令速查

```bash
git switch develop && git pull --ff-only            # 同步集成主干
git switch -c feature/cu-29-profile-aggregate       # 开功能分支
git status --short                                   # 看改动
git diff --cached                                    # 看暂存内容（提交前自检）
git log --oneline --graph --all -20                  # 看分支拓扑
git log --grep="CU-29" --oneline                     # 按编码单元反查提交
git log --grep="TBL-47" --oneline                    # 按编号反查提交
git check-ignore -v backend/.env                     # 验证忽略规则命中
git ls-files | grep -c ""                            # 统计入库文件数
```
