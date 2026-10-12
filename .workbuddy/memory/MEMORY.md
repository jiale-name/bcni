# 项目长期记忆 · 16个人项目（2026 顶配版 AI 数字化人才平台）

> ⚠️ **有注入体积上限，只放索引 + 铁律 + 纪律**，细节外置：
> 🔴 `OPEN-ITEMS.md`（**待拍板 + 未闭环，排期/汇报前必读**）· `DECISIONS.md`（**改代码前先看**；§10 前端 · §11 测试 · §12 档案 · §14 导入链 · §15 端到端）· `PITFALLS.md`（§9~§12 批次 20~23 坑）· `YYYY-MM-DD.md`（每日）
> 🔴 **进度唯一事实源 = `docs\AI数字化人才平台-编码前评审文档.md`**（§7.1 台账 / §7.3 批次 / §10），与本文件冲突以它为准。

## 1. 位置 · 端口 · 命令

| 项 | 值 |
| --- | --- |
| 项目根 | `D:\gan\16个人项目` |
| 设计文档 | `docs\`：需求 v1.3 / 架构 / 数据库 / 接口 / 图 / **编码前评审** / 开发计划 / 测试用例与验收 |
| 数据库 | **`talent_platform_v2`**；MySQL 8.0.27 / root / 见 `backend/.env` |
| 部署产物 | 仓库根 `deploy\`（不在 `backend/` 下） |
| 后端 / 前端 | `backend\`（.venv）**8002**；`frontend\` 5173（proxy `/api`→8002，axios `/api/v1`） |
| 原型 | `prototype\` 批次 14 定稿**选 A 多页**；页面清单事实源 = 架构 §4.2（前端 23 页） |
| 自检器 | `backend\scripts\acceptance_check.py`（S-01~S-09） |

**后端启动**（`backend/` 下，**不带 `--reload`**）：`.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002`（日志 `logs/uvicorn.YYYYMMDD.log`）

**干净全量回归两前提（批次 23 实测，缺一即大面积假错）**：① **先停在跑后端**（否则 1213 死锁 + 1062 撞号）；② `SNOWFLAKE_WORKER_ID` **合法 0~31 且 ≠1**（99 越界 ⇒ 252 setup error）。示例：`SNOWFLAKE_WORKER_ID=2 ./.venv/Scripts/python.exe -m pytest --junitxml=.junit_batchN.xml -q`；跑完**把后端启回来**。

## 2. 进度

- **批次 0~17 全部完成**；设计「⛔ 未设计」清零（✅26/🟡3/⛔0）。
- **批次 20**：解析图补 `extract_text`（PDF 文字层等，空则降级 OCR）。
- **批次 21（1003）**：假事务（跨会话传 ORM 行对象 ⇒ 空事务）· 汉字空格折叠 + 姓名三级来源（0.85/0.70/0.65）· `NAME_MISSING` 文件级人工介入 · 批次 20「C 修复」系误判（用例 monkeypatch 了被测函数）。
- **批次 22（1004）**：「重试」被旧幂等键短路 ⇒ 重试时作废旧键；真机四段全通。脚本：`_probe_parse_real / _e2e_import_check / _probe_talent_rows / _probe_import_task`。
- **批次 23（1006，09-24 收尾）**：① 反转 `GET /talents` 默认口径 —— `_resolve_data_status` 返回 tuple，**默认 ADM/HR=(0 待复核,1 有效)**、其余 (1,)；DAO 序列→`IN`；与检索/问答既有 `in_((0,1))` 对齐；前端占位「状态（默认：有效+待复核）」；T-46b 收紧为元组相等 + 新增 T-46c/T-46d。② 干净全量（停后端 + WORKER_ID=2）**junit 1006/0/0/0**（543.6s，`.junit_batch23.xml`）；`WORKER_ID=99` 系自伤（越界），文档三处 🔁 留痕更正（评审 v1.21/v1.22/§7.3 · 测试 §2.2.1）。③ 后端 AI 代启 **PID 9788**（含批次 23 代码）；真机验收：默认 **7 = 6 待复核 + 1 有效**，与成功文件数 7 对账 ⇒ 报障闭环。探针 `scripts/_probe_talent_list_scope.py`（只读）。④ 🔴 新发现：IMP…006（刘佳华…pdf）`NAME_MISSING` 失败、无档案、**前端无补录入口** ⇒ 简历永远不可见（OPEN-ITEMS 高优先，待拍板）。

- **批次 24（进度条 + SSE，09-24）**：① 单一根因 —— Vite 代理目标 `localhost:8002`（Node 17+ verbatim 常解析成 `::1`，uvicorn 只绑 IPv4）⇒ SSE 握手必败 + 列表旧快照；修 = 写死 `127.0.0.1:8002`。② ImportView SSE 事件回写列表行进度。③ **启动自愈**：lifespan 延迟 3s fail-open → `find_interrupted_tasks` / `resume_interrupted` 重拉「解析中」僵尸任务（DAO `list_stale_parsing_tasks` 是 CR-9 唯一显式例外）。④ **`process_task(only_pending=True)` 重入必须先作废旧幂等键**（回放陷阱随新入口复发，「已完成 0/0」差点复发；新用例反向验证揪出）。⚠️ 本机 shell 有 `http_proxy`，**curl 探针必带 `--noproxy '*'`**；dev server 只绑 `[::1]:5173`。针对性 34/0/0/0；全量预测 1007（待复核）。

**测试计数只认 `--junitxml` 的 failures/errors/skipped=0**（终端摘要不可信）。
⚠️ 改动 Router/`core`/新模块后复跑**必带 `test_layering.py`**；新增测试文件**登记两处**：`test_layering.MODULE_FILES` + 测试文档 §2.1（S-08 对账）。

## 3. 铁律（改代码前必读）

1. 六层依赖只向下：`routers→services(agents)→dao→models`，`core` 横切；业务判定在 Service；DAO 纯 CRUD 且**每查询必带 `tenant_id`+`is_deleted`**。
2. 雪花 ID 一律序列化为字符串（`SnowflakeId`）。
3. 成功 = `code==0`；降级 = 200 + `50301/50302` + `degraded=true`；严禁包成 50000。
4. 权限只从 JWT 读；前端传参只能收窄。
5. 越权与不存在统一 **40301**（有行级域表）；无行级域表跨租户 = 40401。
6. Redis 只存可丢数据；权威在 MySQL；抖动 fail-open。
7. 库唯一事实来源 = ORM + Alembic；禁手工改库。
8. 配置只经 `app/config.py`，同步三处（Settings / `.env.example`+本机 `.env` / 架构 §5）；`.env.example` 密钥留空是正确设计。
9. 密码明文永不入日志（`safe_payload`）。
10. 确定性结论来自确定性组件（CostCalculator/ThresholdEngine/TagDictValidator/DupScorer/RRF）；LLM 只产文本。
11. 不可逆动作幂等：已归档再归档 → 返回成功不 raise；AST 断言钉死。
12. 越权与不存在同码 40301 且**先带行级条件查**（防 ID 探测）；只有 AST 结构断言能守。
13. 码制冲突在契约层做唯一转换点（`education_code_to_int` 等），两侧不改。
14. 会话与对象归属一致：跨会话传行对象 = 空事务；**每层用自己的会话重读行**。
15. 领域失败抛 `PersistRejected(reason_code)` → 文件级人工介入；不把 Python 异常文本透给用户。

## 4. 技术选型

→ `DECISIONS.md` §1~§15（§7 图运行时 · §8 检索/查重/问答 · §10 前端 · §11 测试 · §12 档案 · §14 导入链 · §15 端到端）。

## 5. 待拍板 / 未闭环（详见 `OPEN-ITEMS.md`）

- 🔴 **Milvus collection 命名/语义**（代码 vs 四份文档，语义映射也不同）—— **CU-18 灌数前必须定**（现 0 collection）。
- 🔴 **NAME_MISSING 补录入口缺失**（批次 23 新发现）—— 该类简历永远不可见。
- 🟡 「姓名来自文件名」兜底是否可接受（现 0.65 + 强制进复核）。
- 其余：OCR/LLM 抽取 · G-01~03 脱敏缺口 · 测试专用库 · Q-2/Q-3/金标准（甲方侧）。

## 6. 工程纪律（跨批次，均实际违反过）

1. 🔴 **永远红的检查项 = 没有检查项**：交付使判据失效的功能时**同批反转判据** + 有非平凡挡住对象 + 三处留痕。⚠️ 不得为全绿放松判据（该删就删项+写明原因）。
2. 🔴 **人机边界当一等产出**：先判哪条线能立刻做完；其余写成「须谁做什么解锁」清单；不用占位/假数据凑绿灯。
3. 🔴 **纪律 C**：收到纠正性反馈第一件事**只读核实现状**；修真正错处；回填并 🔁 留痕（保留原文，不省略「原来记错了」）。
4. 🔴 **纪律 D**：核实用的脚本自己先被核实（`\s*` 吞换行等）；**核实工具的输出必须能被第二眼看穿**。
5. 🔴 回归用例**绝不 monkeypatch 被测函数**；走真实路径。
6. 🔴 既有用例「靠缺陷才绿」也是缺陷：修夹具 + 收紧断言。
7. 🔴 进程/端口只认 `netstat -ano`（PowerShell 会假阴性）；pytest teardown 不删文件（安全钩子 SystemExit(1)）。
8. 🔴 工具报「成功」≠落盘：同文件多处编辑**串行** + grep 验收；`.env` 值前缀编辑带行尾换行（防拼接双份）。
9. 🔴 重试/重跑类动作先问「会否命中幂等键被短路」；幂等键是「同一请求的防火墙」非「业务缓存」。
10. 🔴 **功能正确 ≠ 用户能用上**：验收按用户完整链路走到底；默认口径不一致的文案比 bug 更隐蔽。
11. 🔴 会话摘要对截图/现象的转述不可信，**下结论前回看原图**（006 实为失败 0/1，摘要称成功）。
