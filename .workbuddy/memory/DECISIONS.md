# 已定型技术选型 · 16个人项目（2026 顶配版 AI 数字化人才平台）

> 由 `MEMORY.md` 拆出（该文件有注入体积上限，故按主题外置）。
> 读者：改代码前先看这里，**这些是已拍板的结论，不要重新设计**。
> 相关：`MEMORY.md`（铁律与进度）· `PITFALLS.md`（环境坑与排障）。

## 1. 驱动 / 迁移 / 配置

- **统一同步驱动**：SQLAlchemy 同步 Session + 同步 Redis，路由用 `def` 交线程池；仅 SSE / 流式接口用 `async def` + `run_in_threadpool`。
- Alembic 连接串唯一来源 = `app.config.settings`（`alembic.ini` 的 `sqlalchemy.url` 故意留空）；支持 `alembic upgrade head --sql` 离线出 DDL。
- 新增配置项必须**同步三处**：`Settings` / `.env.example`（+ 本机 `.env`）/ 架构 §5；`tests/test_config_env.py` 会守。

## 2. 认证 / 权限 / 数据域

- **Token 吊销 = 双重机制**：单 Token 否定名单 `sess:revoked:<jti>` + 用户级 **Token 版本号** `sess:tok_ver:<tid>:<uid>`。⚠️ **不要改回「比较 `iat` 与时间戳」**（秒级精度，同秒误杀新 Token）。
- **多角色数据域合并 = 取最宽**（数值最小 = 范围最大），依据 RBAC 并集语义；只需改 `AuthService._resolve_data_scope`。
- **权限推导的两侧一致性**：列表侧 SQL 条件与检索侧 Milvus 表达式必须由**同一个 `CurrentPrincipal`** 推导（`app/core/tenant.py` 的设计理由）；**严禁「先召回全量再应用层过滤」**（CR-11）。

## 3. 数据字典 / 状态枚举

- 三条不变式：① `item_code` 创建后不可改（业务表引用它）；② 字典项**只停用、从不软删**；③ 停用「有启用中子项」的父项 → **40904**。API-26 与 API-27 走同一条校验。
- **两种 0/1 状态用词不可混**：`EnabledStatus`（0 **停用**/1 启用）用于配置类；`SysUserStatus`（0 **禁用**/1 启用）只用于账号。⚠️ 组织/岗位 `status_name` 仍用后者 → 显示「禁用」，**改归 CU-23**。

## 4. 审计 / AI 日志（「只追加」）

- **「只追加」= 三层结构保证**：① 模型无 `is_deleted`/`updated_at`；② DAO `__all__` 只导出 insert/list/count；③ **库级账号无 UPDATE/DELETE**（`deploy/mysql-init/01-audit-account.sh`；`AUDIT_DB_USER` 留空即 fail-open 退回主账号，第三层不生效）。
- `audit_log` / `ai_call_log` 的**复合主键 `(id, created_at)`** 是分区约束的必然结果（分区列必须进每个唯一键）。
- **审计写入 = 独立 Session + fail-open 永不抛**，调用点**一律在业务 `commit` 之后**（共用 Session 会让业务回滚带走审计，而最该留痕的恰是失败事件）；脱敏在**写入侧**。
- **AI 调用日志与 Agent 三表则复用业务 Session**（与审计刻意不同）。
- **脱敏率分母 = `ai_call_log.deployment`（调用时刻的事实）**，不由 `ai_model_config` 当前配置推导（否则改一次配置会让既往合规统计集体失真）；DAO 层该列为**必填 keyword-only 无默认值**。

## 5. 模型网关 / Prompt / 并发

- **模型网关**：按 `scene` 的 priority 链 + Redis 熔断 + fail-open + **永不抛异常**；`call()` **返回值恒为一套完整键集**（差异用「哪些键非空」表达）—— 曾因按 kind 返回部分键导致**成功的 embed 调用全 `KeyError`**。
- **Prompt 回滚 = 追加新版本**（历史是审计证据，就地改写等于抹掉「当时用的是什么」）。`prompt_code` 加格式闸门（转大写而非拒绝小写，与字典 `item_code` 口径**有意不同**）。
- **API-94 三态并发**：首次 0 / 同幂等键 **40903**（返回首次结果）/ 他人已处理 **40902**；**幂等判定先于状态判定**（两者前端动作相反）。

## 6. 中间件 / 结构守卫 / 测试

- **中间件顺序：CORS → TraceId → RequestMeta → RateLimit → 路由**；中间件**不得 `raise`**，必须用 `exceptions.build_error_response` 自己构造响应。
- **结构守卫 = `backend/tests/test_layering.py`**：反向依赖 / Router 不碰 ORM / `MODULE_FILES`（= 按文件落地验收表，**新增模块必须补一行**）/ `PROJECT_FILES`（`deploy/` 根产物）/ 限流接线。→ **改结构或新增模块后必须跑它**。
- **pytest 分层**：契约类测试必须**无外部依赖可跑**；依赖 DS 的用 `@requires_db` / `@requires_redis`，缺依赖时 **skip 而非 fail**。
- 测试计数**只认 `--junitxml` 的 `failures=0 / errors=0 / skipped=0`**；终端摘要与退出码在本机均不可信。

## 7. Agent 图运行时

- **运行时 = 自研**（`app/agents/`：graph.py 纯运行时 / checkpointer.py / parse_graph.py / registry.py），83+ 用例钉死；**F-A0 七项能力逐项已覆盖**。
- 三处「有意不同」：① 挂起后**只跑后继节点**（人工结论存 `state["__human__"][节点名]`），LangGraph **重放中断节点**；② 检查点 **run 级** vs 每 super-step；③ `state` 是普通 dict（可 JSON 落 LONGTEXT）vs 需 `TypedDict` + reducer。
- **节点级检查点已补**：`run_graph` 增 `on_checkpoint(state, next_node)` 回调（中断前 / 达终态 / 解析后继三处触发，**失败步骤不触发**），服务层刷 `agent_task.graph_state + current_node` 对接 API-95 重试语义。
- **LangGraph 选型 = 方案 A**（一期保留自研 + 补节点级检查点 + 补 §6.5 申报）；**切换绑定三触发条件**：① 甲方/验收要求技术栈含 LangGraph；② 出现需并行/流式/子图的图；③ 多智能体协同。
- 🔴 **配套红线**：**副作用不得放在 `interrupt()` 之前**，或必须幂等 —— 自研「挂起后只跑后继节点」，**LangGraph 会重放中断节点**（同一段代码跑两遍）。已落地为机械守卫：`interrupts` / `side_effects` 声明 + **装配期互斥校验** + `tests/test_graph_replay_safety.py`（AST 扫真实源码，含正反对照）。
- ⚠️ 若切换**别用**社区包 `langgraph-checkpoint-mysql`：`setup()` **自建表绕过 Alembic**（撞铁律 7）、检查点表**无 `tenant_id`**（撞租户隔离）。正解 = **自写 `BaseCheckpointSaver` 适配器**。
- ⚠️ 切换代价 = +24 包（含 `httpx2`，Starlette `TestClient` 换实现 → 全量 HTTP 层用例需整体复核）+ **4 个编译扩展** → 信创可用性**挂 Q-3**。

## 8. 检索 / 查重 / 问答（CU-18 ~ CU-21）

- **检索抽象（CU-18）**：业务只依赖 `RetrievalProvider` 协议（`ingest/search/delete/health`）；一期实现 `MilvusRetrievalProvider`（延迟导入 pymilvus）+ `keyword_provider`（MySQL LIKE 降级）；**Service 绝不直接调 Milvus SDK**，数据自有可全量重建（`scripts/rebuild_index.py`）。Milvus 三 collection 同构 schema，`chunk_text` **脱敏后**、检索必带权限前置 `expr`（CR-11）。
- **查重合并（CU-19）**：① **跨库写顺序**：合并「先 MySQL 事务 → 再删向量，向量删除失败整体回滚」（`delete` 返回 `-1` 哨兵触发 `db.rollback()`）；与删除（SQ-5 先删向量）**顺序刻意相反**。② **`snapshot` 是回滚唯一依据**：缺快照 → **42203**，绝不做「尽力还原」。③ 从档软删 `data_status=2`（API-61 唯一可逆的删除类迁移）。④ 相似度**全确定性**（`DupScorer` + `ThresholdEngine.is_dup_candidate`，精确键豁免阈值、空 hash 不算命中）。⚠️ **回滚取「软删从档」必须用 `talent_dao.get_talent_any_state`**（`get_talent` 默认滤 `is_deleted`，会误报「档案不存在」）。`talent_dup_pair` 约定 `talent_id_a < talent_id_b` 防重 + `uk_tenant_pair` 唯一键。
- **混合召回（CU-20）**：**RRF 做成纯函数、只用排名不用分数**（`score(d) = Σ 1/(k + rank_r(d))`，`k=60`，读 `RAG_RRF_K`）—— 跨模态分数不可比，只有排名可比；同分按首次出现序稳定排序。`HybridRetriever` 不做 IO。
- **恒兜底信号**：H-19 未闭环 ⇒ 向量分可能整体缺失，故用**确定性词法分（字符 bigram Jaccard，`lexical_score`）**作**恒存在**的兜底信号，使 `RAG_NO_ANSWER_THRESHOLD` 在任何降级路径下都可复算（铁律 10）。
- **降级口径**：`auto` 模式 Milvus 不可用 → HTTP 200 + `code=50301` + `degraded=true`；**显式 `keyword` 模式不标降级**（用户要的就是词法）。**Rerank 是增强不是依赖**：精排失败**保留融合序，且不算降级**。
- **CL-4 脱敏四条约定**（`app/core/masking.py`）：① 先脱敏再出网/入库（正则兜底扫出的 PII **也必须登记进占位符表**，否则回填不回来）；② **出网前必过 `verify_outbound` 二次扫描**（手机/邮箱/身份证三类正则 + `known_names` 反向找人名），**有残留即拒答**；③ **回填只在服务端**，`PlaceholderTable` **纯内存不落库**（落库等于把「明文→占位符」映射变成新资产）；④ **任一步失败即拒答**（占位符解析不出 → `rehydrate` 返回 `None` → `MASK_REQUIRED`）。
- **无依据兜底**：收进 `ThresholdEngine.is_answerable`（不新写阈值比较），返回 `AskDecision(answerable, best_score, reasons)`；**判定不过直接拒答、不调模型**。
- **引用约束**：提示词强制 `[n]` 编号 + `_postcheck_citations` **确定性串扫剔除越界编号**；**剔除后为空即拒答** —— **`citation_count=0` 的 `done` 按缺陷处理，不下发裸回答**。
- **SSE 口径**：问答**全链在握手期用 `run_in_threadpool` 跑完**再开流 —— **40301 不能藏在事件流里**（一旦推事件，HTTP 状态已固定 200，越权会降级成「业务事件」，撞 CR-8）。`delta` 目前是**整包拆片**（网关非流式），token 级流式待云端通道。

## 9. 变更留痕 / 个人权利（CU-22 · 批次 13）

- 🔴 **新增 TBL-30 `privacy_request`**（TBL 编号 29 → 30，库内 MySQL 表 26 → 27）：**上游缺口** —— 接口 §2.2 把 API-73/76/80 指向 `talent_change_log`，而该表 `talent_id NOT NULL`、**无状态/申请人/处理人/时限/驳回理由**任何一列，**装不下 SM-3 状态机**。是「新增」不是「偏离 DDL」（数据库设计文档里根本没这张表）。
- **变更留痕四视图**：API-67 列表 / API-68 时间线链路 / API-69 时间点快照 / API-70 字段历史。**快照回溯 = 确定性重放**（取 `at` 时刻最后一条留痕 id，沿日志链把 `TRACKABLE_FIELDS` 逐字段回放；未追踪字段按声明处理、未知字段码报出不崩、脏历史值降级 `None`）—— **不让 LLM 参与**（它是证据，必须可复算）。查看快照写审计 `CHANGE_SNAPSHOT_VIEW`。
- **SM-3 状态机**：对外只有 `0 待处理 / 1 已通过 / 2 已驳回`；**内部态 `3 处理中` 映射为「待处理」下发**（暴露内部工作流节奏无意义，且请求可能被释放回队列）。
- 🔴 **SQ-5 写顺序 = 先删向量、后软删 MySQL**（与 SQ-4 合并「先 MySQL、后删向量」**刻意相反**）：删除**没有还原依据** ⇒「向量还在、档案已删」是**数据残留（合规）**，「档案还在、向量已删」只是**检索降级（可用性）**。两段代码各自带注释指向对侧。
- 🔴 **新增 `RetrievalProvider.delete_all`**（`provider.py` / `milvus_provider.py` / `keyword_provider.py`）：**逐 collection 删、不短路**，失败记 `-1`，降级返回 `{}`。**原 `delete` 只作用于 `DEFAULT_COLLECTION`**，用它支撑删除权会**静默漏删另外两个集合**。
- **并发与幂等**：**CAS 防并发双审**（`WHERE status=0`，影响 0 行 → **40902**，回带 `handled_by_name`/`handled_at`）；**幂等回放先查键（40903）→ 再判状态（40902）**（与批次 7 Agent 结论一致）。API-77 审批删除**强制幂等**。
- 🔴 **处理时限取「请求时刻的快照」**：`privacy_request.sla_hours` 创建时从 `PRIVACY_SLA_HOURS`（默认 72）落值，逾期判定用它 —— 否则**改一次配置会让所有历史请求的「是否逾期」集体翻转**（逾期率是合规指标，须按当时承诺考核）。同批次 7「脱敏率分母用调用时刻事实列」。
- **可更正字段是白名单**：排除**派生字段**（`age`/`phone_hash`）与**唯一键字段**（`employee_no`）；手机号更正**同事务重算 `phone_enc` + `phone_hash`**；**留痕只落脱敏串**（`138****5678`），**留痕表永不出现明文 PII**。
- **复核队列「确认」不新建 `talent_parse_field` 行**，只**关闭**已有待复核行 —— 复核「确认」只改 `talent_parse_field`、**不写 `talent_base`**，而**更正的唯一生效路径是 API-74**；新建字段行会造成「**假生效**」。
- **`privacy:correct` 与「处理」共码 ⇒ 服务层必须补 `_require_manager`（ADM/HR）** —— 否则**任何 EMP 都能审批自己的申请**。非管理者的可见范围强制收窄为本人（`_scope_kwargs`），越权 40301，**只有「本人未关联档案」才 40401**。
- **API-78「提交即完成」（`status=1`）**：接口集**没有「审批导出」**但 API-79 依赖终态 ⇒ 否则数据包永远下载不了；「处理时长恒 0 拉低跨类型均值」用 `summary.by_type` 分类型化解。导出内容 = `data.content`（同 CU-08 API-29），`_change_summary` **不含前值/后值**（留痕可能含他人操作明细，最小必要）。
- **审计新增 8 动作 + 2 目标**：`CHANGE_SNAPSHOT_VIEW` + 7 个 `PRIVACY_*`；`AuditTarget` 增 `PRIVACY_REQUEST` / `CHANGE_LOG`。
- **本批不调模型** ⇒ **不受 Q-2 / H-19 约束**，故可整体交付（不像 CU-18 / CU-20 只交付非缺口部分）。

## 10. 前端（CU-23 · 批次 14 · **改前端前必读**）

**工程位置**：仓库根 `frontend/`（与 `backend/` 平级）。栈：Vue 3.5 + TS 5.6 + Vite 6 + Element Plus 2.9 + Pinia + Vue Router 4 + ECharts 5 + Axios。
`npm run build` = **`vue-tsc --noEmit && vite build`**（类型检查与构建绑在同一脚本里，**故意如此** —— 防止有人绕过类型检查直接构建）。

- 🔴 **成功判定是 `code === 0`，不是 HTTP 200**。降级恰恰返回 200（`code=50301/50302` + `degraded=true`）。判定收敛在 `api/http.ts` 的 `parseBody` **一处**，业务层不得再各写一套。
- 🔴 **`CommonResp` 的 `page` / `meta` / `summary` 与 `data` 同级**。故 `ApiResult<T, E>` 用第二个泛型槽 `extra` 承载**响应顶层聚合字段**（复核队列 `meta` / AI 调用日志 `summary`）：塞进 `data` 会与分页壳打架，塞进全局类型会失去逐接口约束。**`extra` 为空时是显式 `null`**（不是 `undefined`）。
- 🔴 **雪花 ID 一律字符串**（同 CR-5）：全工程 **零处 `Number()` / `parseInt()`**。**详情页 404 的头号成因就是列表页把 ID 转成了 number**（末位静默截断、不报错）。列表 → 详情的 ID 直传是接口 §7 联调清单的「必跑」项。
- **降级只弹徽标不弹错误框**（`DegradedBadge`）；**显式 `keyword` 检索不标降级**（用户要的就是词法，标降级是误报 —— 与后端批次 12 口径刻意一致）。
- **幂等回放 `40903` 按成功处理**（三个强制幂等接口：**API-36 导入 / API-79 数据包下载 / API-94 中断处置**），UI 给「已处理过」的成功提示并**回填首次结果**。
- **`40903`（自己刚做过）与 `40902`（他人已处理）必须写成两个显式分支** —— 两者的**前端动作完全相反**（继续 vs 刷新重试），**不得合并成「冲突类错误统一 toast」**。涉及 `AgentTaskView` / `ReviewView`。
- **`40301` 与 `40401` 在 UI 上必须不同形**：40301 渲染「无权限」并**保留页面骨架**（不暴露资源是否存在），40401 才渲染「不存在」。两者**不允许**退化成同一个空白页。
- **图层 = `meta.roles`（路由级）+ `v-perm`（按钮级），两层都只是体验层**，真裁决是后端 40301 ⇒ **每个页面都必须能独立渲染无权限态**（不许「菜单藏了就不做 403 分支」）。
- 🔴 **SSE 用 `fetch` 流 + `AbortController`，不用 `EventSource`**（`EventSource` **无法带 `Authorization` 头**）。`openSse` 在**握手期**非 2xx 直接抛错 —— **40301 必须建连时暴露，不能藏在事件流里**。事件名：`meta`/`delta`/`citation`/`refusal`/`done`/`error`；**`delta` 载荷是 `{text}`**；**`citation_count = 0` 的 `done` 不渲染裸回答**。
- **复核队列 / AI 调用日志不用通用 `usePagedTable`** —— 它们的顶层 `meta` / `summary` 超出分页壳形状，硬套会让字段**静默读不到**。改为手写分页 + `res.extra`。
- **`AgentTaskView` 的 `step_index` 按全任务升序渲染，不按 run** —— 否则「相对序号」缺陷（批次 7）会在界面复活（表现为「入库排在 OCR 前」）。
- **Prompt 回滚 = 追加新版本，UI 文案叫「回滚为启用」**（不叫「恢复到该版本」）；版本号**继续增长**，旧版本仍可查。
- **`ConfigView` 的 `already_disabled = true` 按幂等成功提示** —— 重复执行同一意图不是错误。
- **`AuditLogView` 导出超上限（`40002`）提示「缩小时间窗」；CSV 原样落盘，前端不二次加工**（不截断/不重排/不补表头 —— 审计导出的全部价值就在「与记录等价」）。
- **`UserView` 初始密码只走内存 + 弹窗** —— 不写 `localStorage`/`sessionStorage`，**也不放进路由 query**（进 URL 会被浏览器历史与访问日志留存）。
- **`ChangeLogView`（P-20，前端新增）**：`untracked_fields` 单列展示（不假装全字段可回溯）、`history_truncated` 可识别（不静默截断）。
- **ECharts 收进唯一入口 `components/EChart.vue`** —— 主题色从 CSS 变量读（不硬编码），`ResizeObserver` + 卸载 `dispose`。

**页面清单的事实来源是架构 §4.2，不是原型**：原型 `p01~p19` 19 页 ≠ §4.2 的 19 行。
原型 P-16 把「模型配置 + Prompt 管理」合成一页（前端拆 `/ai/model` + `/ai/prompt`），且**原型无「档案变更历史」页**（前端补 **P-20**）。
⇒ 前端实际 **21 条业务路由 + `/403`·`/404` = 23 个页面文件**；原型 `_src/` 的单文件 SPA 分片产物**已确认废弃**。

**上游缺口（不阻塞前端，但阻塞「F-3 后端功能面全部交付」的表述）**：
- **API-30 ~ API-35**（档案列表/详情/编辑/归档/数字画像/导出）与 **API-64 ~ API-66**（会话历史/清空会话/**脱敏自检**）共 **9 条无单元认领**，实测 `openapi.json` 不存在。
- **处置模式**：前端**按接口文档既定契约实现**（后端补齐即点亮），`api/talent.ts` 导出 `TALENT_MASTER_API_READY = false` + `TALENT_MASTER_API_NOTICE`，页面渲染「接口未就绪」提示条 —— **让 404 被读成「后端未就绪」而不是「前端有 bug」**。
- 另一条：**角色权限只有写侧 `PUT /roles/{id}/permissions`，无读端点** ⇒ `RoleView` **结构上无法预填勾选**，页面用红色 alert 明示「空白≠无权限」并给出「先全选、再取消」的操作法。

---

## 11. 测试资产 / 验收口径 / 开发计划（CU-25 · CU-26 · 批次 15 · **改测试前必读**）

**开发计划（CU-25）**
- 🔴 **三套「批」编号不得混用**：`§7.2 计划批次`（第 0~9 批）／`§7.3 记录批次`（含非编码轮次，已到 **15**）／`WBS-x.y`（《开发计划》§2）。对照表在《开发计划》**§0.1**。
- 🔴 **《开发计划》不登记进度状态** —— WBS 表只写「见 §7.1 CU-nn 行」+ 计划窗口/责任人/DoD。**状态唯一事实来源始终是评审文档 §7.1**（防两处数字打架）。
- **排期基线 = 需求 §15.2 的 320~470 人天**；⚠️ **本机 AI 辅助的 14 批次集中在 2 个工作日内完成，不可折算成对外工期承诺**（该文档 §1.1 显式声明「实作速度 ≠ 投标承诺」）。

**测试资产（CU-26）**
- **判绿唯一口径 = `--junitxml` 的 `failures=0 / errors=0 / skipped=0`**（终端摘要被钩子隐藏、退出码被安全钩子改成 `1`，两者都不可信 —— **`echo $?` 得到 1 不代表失败**）。
- **分层**：后端 L1 契约层（纯函数，不连库）/ L2 集成层（真连 MySQL·Redis）/ L3 端到端（`TestClient` 走 HTTP）。⚠️ **分层按「测试函数用了什么 fixture」判，不是给文件贴标签**（同一文件两层并存是正常的）。
- 🔴 **不设「覆盖率百分比」指标** —— 覆盖率对「越权 / 脱敏 / 降级 / 跨库一致性」加权为 0（把 `verify_outbound` 空实现掉，覆盖率反而更高）。改用**结构性覆盖**：需求 §12.3 的 **29 条验收项逐条指名承载用例**，**没被指名的才算漏洞**。
- 🔴 **「先召回后过滤」只能靠结构断言抓**（`test_search_scope.py` 的 `T-20` / `T-20b`：AST 断言 `allowed_org_ids` 入参与 `provider.search(tenant_id=, org_ids=)`）—— **行为断言永远测不出来**。
- 🔴 **缺口必须写成会失败的用例，而不是文档里的一句话**：正向固化（断言「当前确实漏检」）+ docstring 写「闭环后请反转本断言」+ **反向对照**（防有人为修缺口把校验写松）。

**验收自检器（`backend/scripts/acceptance_check.py`）**
- **S-01~S-09**：junit 全绿 / 结构守卫 / 前端产物 / 页面 chunk = 页面数×2 / 路由三口径 / `openapi` 路径数 / 迁移单头 / **测试文件 ↔ 文档清单双向对账**（**S-08，本自检器存在的核心理由**）/ 未认领接口差异集（S-09）。
- 用法：`python -m scripts.acceptance_check`（静态）· `--with-tests`（追加 S-01/S-02）· `--layering-only`（只跑守卫）。**判读看 `[FAIL]` 行，不看退出码。**

**已登记缺口（`G-01~G-03`，固化在 `tests/test_masking_samples.py`）**
- 🔴 **`mask_text` 是精确字符串替换，人名无法用正则穷举**（`core/masking.py` 的**自觉取舍**）⇒ **姓名空格变体（`张 伟` / `张　伟`）/ 拼音·中英混排·昵称（`Zhang Wei` / `小张`）/ 手机书写变体（`+8613800138000` / `138-0013-8000`）全部漏检**。
- ⇒ 需求 §12.3 的「**零漏检**」**在当前架构下不可达** —— **这是措辞问题，不是实现问题**（与 Q-2 同类）。若走 **NER**：它是新依赖（**必与 Q-3 信创清单一并评估**）且**自身仍有漏检率**（换法不换标）。
- **手机书写变体的修法 = 「先归一化再扫描」，且归一化只作用于判定、不改写原文** —— 🔴 **不是把正则写松**（写松会误伤 `编号91380013800012` 这类长数字串，`T-40b` 就是钉这个的反向对照）。

---

## 12. 人才档案主线（CU-29 · 批次 16 · **改档案接口前必读**）

**接口面**
- **9 条接口 / 6 组路径**：`GET /talents`（API-30 列表）· `GET|PUT|DELETE /talents/{id}`（API-31 详情 / 32 编辑 / 33 归档）· `GET /talents/{id}/profile`（API-34 画像）· `POST /talents/export`（API-35 导出）· `GET|DELETE /talent/ask/sessions/{id}`（API-64 / 65）· `POST /talent/mask/verify`（API-66）。
- 🔴 **路由注册顺序**：`/talents` → `/talents/export` → `/talents/{talent_id}` → `/talents/{talent_id}/profile` —— 否则 `/export` 会被 `{talent_id}` 吃掉。

**码制与字段口径**
- 🔴 **`education_level` 唯一转换点**：上游接口写 int、字典种子写**两位数字码** `"01"~"05"`（高中及以下/大专/本科/硕士/博士）。**不在契约层选边**，用 `education_code_to_int` / `education_int_to_code`（**非数字输入返回 `None`，不抛异常**）。
- 🔴 **`sensitive_visible=true` 时仍只下发脱敏值**（**取安全侧**）—— 上游语义是「有权限者可见明文」，但**明文出网**是 CL-4 红线。若甲方要求明文，须**先解决明文出网的合规问题**，不是改个字段。
- 🔴 **`work_exps` / `project_exps` / `skills` / `certs` 恒为 `[]`**（TBL-12~15 未建模）：`with_detail=false` **什么都不说**（参数决定不返回）；`with_detail=true` 才回 `detail_note`。**把「后端没做」与「参数不返回」分开。**
- **排序白名单** `updated_at` / `created_at` / `work_years`，默认 `-updated_at`，**非法回退默认**（不报错）；筛选上限 `org_ids ≤ 50` / `tag_codes ≤ 10`（超出**截断**）；导出 ≤ 5000 行且**只支持 `csv`**（`xlsx` → 40001，**不静默降级**；CSV 带 **UTF-8 BOM**，列不含 PII）。

**权限与数据域**
- 🔴 **API-31 / 34 刻意不声明权限点** —— 可见性**全交数据域**（`DataScopeFilter`）：EMP 的「仅本人」是 `data_scope=SELF` 的天然结果，**额外挂权限点反而会让 EMP 拿到 40301 而不是「看到自己那一条」**。
- 🔴 **越权与不存在统一 40301**：`_locate` **先带行级条件查**，落空后只记内部日志 + 统一 `PermissionDeniedError`。**AST 断言：`_locate` 内只允许一种 Raise**（`T-49`）。
- **API-64/65 用 `search:ask`**（⚠️ 见下「已知缺陷」）。

**不可逆动作**
- 🔴 **归档幂等**：已归档再归档 → **返回成功**（`data_status=ARCHIVED` 分支**不得 raise**，`T-58` AST 断言）。顺序 = **先清向量 → 再软删 MySQL**（**与 API-77 删除权刻意相反**）。
- **编辑**：`data_status=MERGED` → `IllegalStateError`；手机号变更**必须同时更新 `phone_enc` + `phone_hash`**（缺 `CONTACT_HASH_SALT` 报 `INTERNAL_ERROR`）；变更写 `TalentChangeLog`（加密字段记**脱敏串**）+ 审计。**列表/详情这类读动作刻意不写审计**（审计表长期留存、多人可读，记读=用海量低价值行淹没高价值行）。

**脱敏自检（API-66）**
- 🔴 **复用生产规则 `verify_outbound`，绝不另写一套自检规则** —— 否则会得到「自检通过、生产漏检」这种**最坏结论**（比不做自检更危险：它给出虚假的安全感）。
- **出参不回显原文**；审计**只记 `{sample_count, leak_count}`**（把 PII 写进审计=换个地方存明文）；`< 20` 条样例时回 `meets_minimum=false` + 「**不构成验收证据**」。
- 🔴 **`T-52` 的自洽不变量**：「查泄漏」接口的**响应体自身必须能过生产出网扫描**。⚠️ **说明文案里禁止出现任何形如手机号/身份证的数字示例** —— 实测：一句「举例说明会被误判」的注释（含 `91380013800012`）其子串**恰好命中生产正则**，把明文手机号写回了响应体。

**自检器维护（本批新增纪律）**
- 🔴 **交付「把某个判据判成假」的功能时，必须同一批次反转该判据**（本批：`S-09` 由「B-8/B-9 的 6 组路径**应不存在**」反转为「**必须持续存在**」的契约回归守卫）。**永远红的检查项会被习惯性忽略 ⇒ 自检器从资产变负债。** 三件套：同批反转 + 反转后仍有非平凡的挡住对象 + 三处留痕。
- ⚠️ **反向红线：不得为了让自检全绿而放松判据** —— 判据真该删时，**删项 + 写明为什么删**，而不是改成永真的空断言（那是把红点藏起来）。

**已知缺陷（本批登记未修）**
- 🔴 **`search_router` 声明 `require_perm("talent:ask")`，而权限点词表里只有 `search:ask`** ⇒ **除 ADM 外 HR/MGR 调 API-63 一律 40301**（ADM 因 `ROLE_GRANTS["ADM"] = ["*"]` **短路命中，故自测时不易发现**）。属 **CU-21 返工**，需独立批次 + 回归。
- 🟠 **`pymilvus 3.0.2` 连带引入 `pandas 3.0.6` + `numpy 2.5.3`** —— 与 `requirements.txt` 注释「不引入 pandas」冲突；须决定「改注释」还是「换安装方式」。

---

## 13. 批次 17（配置收敛核实 + 台账回填）—— 非功能轮次

**配置管理**
- ✅ **Milvus 库名 / MinIO 桶名与密钥一律经 `.env`**（`MILVUS_DATABASE=talent_vec` · `MINIO_BUCKET=talent-files` · `MINIO_ACCESS_KEY` · `MINIO_SECRET_KEY` · `DIFY_API_KEY` · `MILVUS_HOST/PORT` · `MINIO_ENDPOINT/SECURE` · `DIFY_BASE_URL`）—— **`app/` 下 0 处硬编码**（`talent_vec` / `talent-files` 只出现在 `config.py` 的默认值上）。🔴 **这是既有事实（用户本轮提出要求时已经满足），不是本轮新做的** —— 不要把它记成「本轮补齐了配置项」。
- 🔴 **`.env.example` 的密钥项保持空占位**（`.env` 在 `.gitignore` 中、`!.env.example` 是唯一例外）⇒ **不要「顺手补全」**，那等于把密钥推进版本库。
- 🔴 **密钥类配置的核对方式 = 与服务端权威记录比对，而不是反复重试**（本批 Dify：`.env` 值与 Dify 库 `api_tokens` 差 **3 个字符** ⇒ 恒 401；**先实测 200 再回写**）。
- 🔴 **探针状态保持四态**：`OK` / `DEGRADED` / `BLOCKED`（待就绪）/ `FAILED`（异常）。**「已配置但被拒（401）」属 `FAILED`，不得并进 `BLOCKED`** —— 两者返工方向**相反**（**查值** vs **去配**）。
- ✅ **N0 复跑终态 `✅6 / 🟡1 / ⛔3 / ❌0`**（mysql / redis / minio / dify / ollama / embedding **就绪**；milvus **降级**（库在、0 collection）；reranker / ocr / cloud_channel **待就绪**）—— **阻塞项只剩 Reranker / OCR**。

**Milvus collection 命名（🔴 待拍板，灌数前必须定）**
- **代码** `app/retrieval/milvus_provider.py`：`COLLECTIONS = ("talent_skill", "talent_experience", "talent_quality")`（注释自称 COL-01 技能 / COL-02 经验 / COL-03 素质）。
- **四份设计文档口径一致**：`resume_vec` / `skill_vec` / `project_vec`（COL-01 简历全文 / COL-02 技能 / COL-03 项目）。⚠️ **不止命名不同，语义映射也不同**；`talent_skill` 还与 **MySQL 表 TBL-14 同名**。
- **三案**：**A 以文档为准**（改代码常量 + 用例，与 4 份文档一致、保住 F-3.4/3.6/3.7 追踪关系）· **B 以代码为准**（改 4 份文档 + 2 份 HTML，**牵动 §7 追踪矩阵与容量估算**）· **C 维持现状 + 显式登记偏离**。
- ⚠️ **当前 Milvus 内 0 collection ⇒ 改名的迁移代价为零**；灌完百万级向量再改就要**重跑向量化** ⇒ **先定名、再灌数**。

**台账回填纪律**
- 🔴 **快照类结论（探针结果 / 状态分布 / 计数）必须带时间戳；修订时「保留原文 + 加 🔁 标记」，不覆盖** —— 批次 16 的 `⛔5` 记的是「**用户建库之前**」，**它当时是对的**；直接覆盖会丢掉「曾经错过一次、错在哪」这一信息。
- 🔴 **纪律 C**：收到「**纠正性反馈**」（截图 / 日志 / 一句「我已经按你说的做了」）时，**第一件事是「只读核实现状」，而不是「照原计划继续做」**。三步：**只读核实（不改任何东西）→ 修真正错的那一处 → 逐处回填并 🔁 留痕**。⚠️ 反向红线：**不得为了让结论好看而省略「原来记错了」**。

## 14. 批次 21（导入解析三修：账目 / 文本层折叠 / 姓名来源）—— **改导入链前必读**

**会话与事务（账目面 · 铁律 14）**
- 🔴 **ORM 行对象不得跨会话传递**：`process_task`（主会话）`get_file` → `_process_one_file`（文件会话）`mark_file` ⇒ **`file_db.commit()` 提交空事务**，状态从未落库；随后计数读到的仍是 `PENDING` ⇒ 任务错判 `COMPLETED` + `success=fail=0`（而文件行显示失败）。🔴 **正确做法：在文件会话里重新取一次行对象**（`import_dao.get_file(file_db, tenant_id, file_id)`），标记与入库回调**全部作用在它身上**。⇒ **「每文件独立事务」不是一句注释，它要求对象也属于那个会话**。
- ⚠️ **批次 20 的「C 修复」是误判**：诊断成「REPEATABLE-READ 到期快照」，实为「根本没提交」。新会话计数**方向对但不够**（它让失败计数能读到，却仍依赖文件状态真的落库）。🔁 保留本条：**症状消失 ≠ 根因坐实**。
- 🔴 **逐文件失败必须逐文件落库并逐文件兜底**：异常分支新增 `_mark_failed(...)`（**用全新会话**标失败），确保「进程内抛异常」也不会留下 `PENDING` 幽灵行。

**文本层与姓名抽取（业务面）**
- **文本源**：`extract_text` 节点 —— txt 直读 / docx **零依赖** zipfile 抽 `<w:t>` / PDF 懒导入 `pypdf` 读文字层；**空文字层 → 降级 OCR**（扫描件兜底）。`pypdf>=4.0.0` 已入 `requirements.txt` 与项目 venv。
- 🔴 **汉字间空白折叠只折「行内」**：`re.sub(r"(?<=[\u4e00-\u9fa5])[ \t\u3000]+(?=[\u4e00-\u9fa5])", "", text)` —— **绝不用 `\s+`**（会吞换行，毁掉首行启发式）。典型输入：`付 智 超` / `学 历 ：本科`（PDF 文字层常见）。
- **姓名三级来源（本批定型）**：`姓名：`**标签 0.85** > 正文**首行 0.70** > **文件名片段 0.65**。⚠️ **后两级必然低于阈值 ⇒ 进字段级复核队列**（**先建档 + 有人担保**，不静默建档、也不整份失败）。🟡 「文件名是否可信」待用户确认（可单点关闭：删 1 函数 + 1 处调用）。
- 🔴 **姓名抽取必须防「折叠副作用造错名」**：`姓名：张三 电话 138…` 折叠后会连出「张三电话」⇒ **`_NAME_RE` 用宽捕获 `{2,8}` + 按 `_LABEL_WORDS` 截断 + 汉字形状校验**（窄上限 `{2,4}` 会把标签词切断，使边界检测失效）。
- **`_SCHOOL_RE` 去掉「学历」替代项**（否则 `学历：本科` → `school=本科`）；`_NAME_STOPWORDS` / `_NAME_STOP_SUFFIXES` 拒「个人简历」「求职简历」等。
- 🟡 **未闭环（登记）**：「待人工补录」与「解析失败」在**文件行**上同为 `parse_status=2`，仅 `parse_error` 可辨 —— 若要做成可筛选项，须新增枚举 + 迁移 + 接口 + 前端（独立批次）。

**失败原因口径**
- 🔴 **领域失败用领域异常表达**：入库拒绝抛 `PersistRejected(reason_code)`（本批 `NAME_MISSING`），**由上层映射为文件级人工介入**，**不把 Python 异常文本透给用户**。`FAILURE_REASON_LABELS`（后端）与 `IMPORT_FAILURE_REASON`（前端 `src/api/enums.ts`）**一一对应**，未命中**原样显示**（不吞码）。
- **阈值层**：`ThresholdEngine.needs_manual_review` 新增**规则 0** —— 无 `name` ⇒ `reasons=["name_missing"]`；`parse_graph.FILE_LEVEL_REVIEW_REASONS` **加项**（批次 10 曾钉死「只有 `no_fields` / `overall_low`」，**已同批反转判据并留痕**）。

**测试口径（本批新增，具通用性）**
- 🔴 **回归用例不得 monkeypatch 被测函数**；**新用例走真实路径**（`create_task(run_async=False)`），断言**任务行与文件行一致**（`success_count`/`fail_count`/终态/`parse_status`/`talent_id`）。
- 🔴 **夹具不得「靠缺陷才绿」**：本批把 `test_talent_import` 的假字节夹具换成**真 docx**，并把 `retry` 断言由 `in (0,40001,40002)` **收紧**为 `42201`。⇒ **收紧断言是修复的一部分，不是「改测试迁就代码」**。
- ✅ **全量 1003 / 0 失败 / 0 错误 / 0 skip**（基线 994 + 9，完全对账）；自检器 **PASS 7 / FAIL 0 / SKIP 2**；前端 `vue-tsc` 0 error + `vite build` 通过。

## 15. 批次 22（端到端联调收口：「假重试」+ 档案可见性口径）—— **改导入 / 重试 / 列表默认口径前必读**

**幂等键的适用边界（本批最重要的结论）**
- 🔴 **幂等键是「同一请求的防火墙」，不是「同一业务的缓存」** —— 把它留在「试图重来」的路径上，它就从保护变成**拦路石**：`_process_one_file` 以 `parse:{task_id}:{resume_id}` 建 Agent 任务，`retry_failed` 只清 `import_file.agent_task_id`（**清了文件行的指针，没清 Agent 行的键**）⇒ `start_task` 命中幂等键 **回放首次结果、不重跑图** ⇒ 用户点多少次「重试」都没变化，且 `parse_error` 被写成回放指纹 **`PARSE_FAILED`**（真实原因码丢失，排障被带偏）。
- ✅ **口径**：**凡「重试 / 重跑 / 重新生成」类动作，动手前先问一句「它会不会命中幂等键被短路」**；本次处置 = `retry_failed` 内把旧 Agent 任务的 `idempotent_key` **置 `NULL`**（旧行**保留** → 步骤回放与审计历史不丢）。依据是 `AgentTask` 模型 docstring 自己写明的：**「幂等键允许为 `NULL`（非幂等场景，如人工在界面上点「重试」）」** —— 设计意图本就如此，是实现漏了这一步。
- 🔴 **为什么批次 21 的用例测不出来**：它只断言「重试后账目与文件行一致」——**回放同样满足**。⇒ 新增用例必须断言「**新增了一个 agent_task**」（真跑过）+ 「旧键已作废」+ 「`parse_error` 是**真实原因码**」三条，回放路径**一条都过不了**。

**档案可见性口径（产品语义，非缺陷）**
- `GET /talents` **未传 `filters.data_status` 时只返回 `1 有效`**（API-30 明文）；而**导入落库的档案是 `0 待复核`**（`data_status=int(TalentDataStatus.PENDING_REVIEW)`，人工担保后才转有效）。⇒ **导入成功后默认档案列表必然看不到它**。
- 🔴 **前端文案必须与后端默认口径一致**：档案页状态下拉原先占位写「**全部状态**」⇒ 用户导入后看到空列表，直接判成「没入库/解析失败」。已改「**状态（默认仅有效）**」。
- **完整闭环（四段，缺一段用户都会觉得「坏了」）**：`导入/重试 → 解析入库（0 待复核）→ 复核工作台确认 → 1 有效 → 出现在默认列表`。🔴 **验收时必须走完四段**，只验「文件 SUCCESS + `talent_id` 非空」会漏掉后两段。

**端到端联调的可重复资产（`backend/scripts/`，只读/幂等）**
- `_probe_parse_real.py` —— **重启前**离线干跑盘上真实简历（读文字层 + 跑 `rule_extract`），回答「新代码对真实文件到底能不能抽出字段」。
- `_e2e_import_check.py` —— **前端同款 HTTP 链路**（登录 → 任务列表 → 重试 → 轮询终态 → 文件明细 → 档案列表 → 复核队列；`--confirm-one` 再走完「确认 → 转有效 → 进默认列表」）。⚠️ `httpx.Client(trust_env=False)`（**绕开本机代理**）。
- `_probe_talent_rows.py` —— **用应用自己的会话直读库**并 `print(tuple(row))`。🔴 **不要用 `mysql … | grep`**：输出含二进制字节时 `grep` 会判 "Binary file matches" 并**吞掉结果行**，本批因此一度误判「档案 0 行」。

**进程版本取证（不改代码、5 秒判定「跑的是哪版」）**
- 🔴 **源码 mtime vs `__pycache__/*.pyc` mtime**：源码新于 pyc ⇒ 进程**必然**加载的是旧模块（uvicorn 不带 `--reload` 时不重读源码）。本批实测源码 `09-24 09:45` / pyc 全为 `09-23` ⇒ 判定「进程未加载修复」，避免了「改了没生效 → 又改一遍」的返工。
- 交叉证据：错误文案的新旧（旧文案 `ValueError: missing_name…` vs 新原因码 `NAME_MISSING`）、`agent_step` 里有没有 `extract_text` 步。


