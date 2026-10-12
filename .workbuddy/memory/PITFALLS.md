# 环境坑 · 排障速查 · 工具纪律（16个人项目）

> 从 `MEMORY.md` 拆出（2026-09-22）：主文件需保持精简以便每次会话注入；
> 本文件存放**低频但高价值**的踩坑记录 —— 遇到对应现象时读它，不要凭印象重排。
> 全部结论**本机已实测**。

## 1. 环境注意

| 项 | 结论 |
| --- | --- |
| **pip** | 直连 `pypi.org` 超时 → 用 `-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host ...`。⚠️ **不要** `pip config set global.*`（污染 `%APPDATA%\pip\pip.ini`） |
| **Redis** | **5.0.14.1**（无 `HELLO`）。**必须 `REDIS_PROTOCOL=2`** —— redis-py 8.x 默认 RESP3 会先发 `HELLO 3` 而失败，现象是「Redis 活着但连不上」。见 `README §8.1` |
| **MySQL** | 8.0.27 已启动；连接串在 `backend/.env`。客户端 `/c/tools/mysql/mysql-8.0.27-winx64/bin/mysql`。⚠️ **`DATETIME` 无小数秒且对亚秒值四舍五入**（`…00.5 → …01`）→ 默认时间窗查询有 **1 秒级可见性延迟**（「刚插入就查不到」**不是丢数据**；**不要**给默认 `end` 加 1 秒） |
| **依赖现状** | 已装 httpx / openpyxl；**未装** langgraph / langchain / pymilvus（按 requirements 分批启用）→ `app/agents/` 用自研运行时，LangGraph 适配器为**可选延迟导入** |
| **pytest 输出** | 🔴 **判全绿只认 `--junitxml` 的 `failures=0 / errors=0`** —— 既不认终端汇总行、**也不认退出码**。两个钩子：① 终端钩子**隐藏汇总行**；② safe-delete 钩子拦截临时目录批量删除（打印 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`）**把退出码变非 0** 而用例全过。⚠️ `--basetemp` **不能**指向重定向输出的目录（pytest 启动会清空它） |
| **`backend/.env`** | 🔴 曾误用他项目配置，**已按 `.env.example` 重建**（备份 `.env.bak.20260922-before-rebuild`）；`.gitignore` 已补 `.env.*`。新增配置项后**三处同步**（`Settings` / `.env.example`+`.env` / 架构 §5），否则 `test_config_env` 失败 |
| **`alembic.ini`** | 🔴 **必须纯 ASCII**。Alembic 以 `encoding="locale"`（中文 Windows = GBK）读 ini，含中文会在启动前抛 `UnicodeDecodeError`。中文说明写 `alembic/README.md` |
| **硬件** | 无显存（AI 算力需租云 GPU）；本地 MySQL / Redis / Ollama / Dify / MinIO 均已部署 |
| **时区/编码** | `.env` 用 UTF-8，`.ini` 纯 ASCII，DB 时间用**本地 naive datetime**（与 MySQL `CURRENT_TIMESTAMP` 一致） |

## 2. 排障速查（已踩过的坑，别重复排查）

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| `unknown command 'HELLO'` | redis-py 8 默认 RESP3 vs Redis 5 | `REDIS_PROTOCOL=2` |
| `alembic` 报 `UnicodeDecodeError: 'gbk'` | 中文 Windows 读 ini 用 GBK | `alembic.ini` 保持纯 ASCII |
| `Access denied ... using password: NO` | MySQL root 需密码 | 填 `backend/.env` 的 `MYSQL_PASSWORD` |
| `InsecureKeyLengthWarning` | HS256 密钥 <32 字节 | 用 `DEV_JWT_SECRET_PLACEHOLDER`（75 字节），生产替换 |
| `ORJSONResponse is deprecated` | FastAPI ≥0.13x 改由 Pydantic 直接序列化 | 不要用 `ORJSONResponse` |
| 遍历 `app.routes` 找不到子路由 | FastAPI ≥0.14x 的 `include_router` 保留 `_IncludedRouter` 包装 | 路由断言读 **OpenAPI** |
| 列表返回的 ID 请求详情稳定 404 | 雪花 ID 当数字下发，JS 精度截断 | `schemas.common.SnowflakeId`（CR-5） |
| `SettingsError: parsing value for "CORS_ORIGINS"`（导入 settings 即崩） | pydantic-settings 对 `list` 字段**默认先 `json.loads`** | `Annotated[list[str], NoDecode]` + `_split_cors_origins` 校验器（回归测试 `test_config_env.py`） |
| 登录成功但业务接口**一律 40301** | ① 权限点未种子化（非 ADM 无权限码 → fail-closed）；② 非 ADM 访问 ADM 专属接口 | 多为**设计行为**。跑 `scripts/seed_dev.py`，用 ADM 账号；见 `README §8.6` |
| 根部门子树解析整体失效 | `build_dept_path` 未给根部门加**前导斜杠** → 得 `1/` 而非 `/1/` | 已修（`org_scope_service.py`），由 AC-11b 抓出 |
| 字典接口三个码分不清 | 40401=本租户不存在（含跨租户）、40301=缺权限码、40001=编码不合规 | 读法见 `README §8.11`。**字典无行级数据域**，故跨租户 = 40401 |
| 第二次软删同编码记录报 1062 | DDL 把 `is_deleted` 放进唯一键，而该列只有 0/1 → 同编码最多一条软删行 | 字典 / `ai_call_log`（不软删）不受影响；其余表支持真删除前**必须先改唯一键**（已登记） |
| `audit_log` 加分区报 **1481**（`MAXVALUE can only be used in last partition definition`） | 表尾是 `pmax`，MySQL **不允许**对含 `MAXVALUE` 的表用 `ADD PARTITION` | 改 `REORGANIZE PARTITION pmax INTO (新月份…, pmax…)`；用 `scripts/audit_partition.py --ensure-next --table all`（幂等，已泛化支持 `ai_call_log`） |
| 分区归档**删错分区**（静默偏移一个季度） | MySQL `TO_DAYS` 与 Python `date.toordinal()` **差一天**；反算得错误月份 | `covered_month` **从分区名 `pYYYYMM` 解析**。**「不报错但结果错」比崩溃危险** |
| `Annotated[SomeModel, Query()]` 变成「必填标量参数」，报 **500** 而非 40001 | FastAPI 0.141.1 真实应用里的退化行为（最小复现却通过） | 改回**显式 Query 参数** + 依赖里手工 `model_validate`，`ValidationError` 显式转 `BizError(40001)` |
| 中间件里 `raise BizError` 得到 **50000** 而非 42901 | Starlette 的 `ExceptionMiddleware` 在中间件栈**内侧** | 中间件必须**自己构造响应** |
| 限流被「遍历 ID」绕过 | 每写 `/users/1`、`/2`… 各自成桶 | 路径归一化 `normalize_rate_path`（资源 ID 段 → `{id}`） |
| 「刚插入就查不到」 | MySQL `DATETIME` 四舍五入（见 §1） | 测试侧统一 `.replace(microsecond=0)`；**不要**给默认 `end` 加 1 秒 |
| 迁移文件报 `AttributeError: 'sqlalchemy' has no attribute 'TINYINT'` | SQLAlchemy **顶层无 TINYINT**，MySQL 方言才有 | 迁移里 `from sqlalchemy.dialects import mysql` 后写 `mysql.TINYINT(1)`，别写 `sa.TINYINT`（批次 10） |
| 只追加表 ORM 查询报 `Unknown column 'xxx.updated_at'` | 该类表 DDL 无 `updated_at`，但模型误混了 `TimestampMixin`（带 `updated_at`） | 只追加表**不继承 TimestampMixin**，`created_at` 显式声明（对齐 `AuditLog` 口径）。守住「只追加三层保证①」（批次 10） |
| 复核队列 SQL 报 `You have an error in your SQL syntax near 'NULLS FIRST'` | **MySQL 8 不支持 `NULLS FIRST/LAST` 语法** | 去掉 `.nullsfirst()`；ASC 时 NULL 天然排最前，直接 `.order_by(col.asc())`（批次 10） |
| `GET /x/{id}` 之后注册 `GET /x/dashboard`，后者被路径参数捕获 → 422 | FastAPI **按注册顺序匹配**，静态段排在路径参数路由后会被 `{id}` 抢占（`"dashboard"` 转 int 失败） | 静态段路由**必须注册在路径参数路由之前**（批次 10，`review_router` 已前移） |
| OpenAPI 生成时 `PydanticUserError: TypeAdapter[...] is not fully defined` | 某 Schema 用了 `Literal` 但**漏 import**；`from __future__ import annotations` 延迟求值 → 类定义时不炸、OpenAPI 生成才炸 | 用 `Literal` 必须 `from typing import Literal`（批次 10，`schemas/tag.py`） |
| `.populate_existing()` 报 `'Select' object has no attribute` | SQLAlchemy **2.0 移除了** `Query.populate_existing()` 的链式写法 | 改 `.execution_options(populate_existing=True)`（批次 10） |
| 分页接口 `data["list"]` 报 `list indices must be integers` | 本项目的 `paginated()` 把 `data` **直接放数组**，分页信息放同级 `page` | 分页响应取 `resp["data"]`（数组），不要套 `["data"]["list"]`（批次 10） |
| 合并回滚/还原类操作报「档案不存在」40401 | `get_talent` 默认滤 `is_deleted=0`，而「还原被软删的从档」这类操作**恰恰要取软删行** | 用 `talent_dao.get_talent_any_state`（含软删、仍带 `tenant_id`、**仅限回滚场景**）。通用教训：**「还原」操作永远不能复用「只读有效」的查询**（批次 11） |
| 新增迁移后全量跑，新 L2 用例**全 skip 且不报错** | 迁移文件写了但没 `alembic upgrade head` → `env` fixture 判 `has_table(...)` 为假 → `pytest.skip` | 编码新表后**必须先 `alembic upgrade head` 再复跑**，否则 L2 从 skip 转 pass 才暴露的缺陷（漏 import / 软删过滤）被静默掩盖。**「全绿但 9 个 skip」≠「没问题」**（批次 11） |
| 🔴 `test_layering.py::test_routers_contain_no_business_logic` 报 **「Router 越层：app/routers/x.py:NN 直接导入 ORM 模型 app.models.*」** | Router 里 `from app.models.enums import X` 之类的导入（常见于要传枚举值给 Service） | **ORM 枚举不是 Router 该知道的东西**。给 Service 加**意图命名**的薄方法（如 `list_corrections` / `list_deletions`），**类型选择留在 Service**。⚠️ Router 允许的只有 `app.schemas` / `app.core` / `app.services` / `app.dao.session`（批次 13） |
| `Cannot use Query for path param 'talent_id'`（路由**构造即失败**） | FastAPI 依赖**按参数名**把路径参数并进依赖；过滤器依赖的形参名与路径参数**同名** | 依赖形参改名 `filter_talent_id` + `Query(alias="talent_id")`（**对外契约不变**）。**「复用一个过滤器依赖服务多条路由」时必然会踩**（批次 13） |
| `paginated() takes 2 positional arguments but 4 were given` | 误按 `paginated(items, total, current, size)` 位置传参 | 本项目签名是 `paginated(items, total, *, page=, per_page=)`。**同类错在批次 12 出现过**（`SearchOut` 的 `meta` 形状）→ 「统一响应壳的形状」是高发区（批次 13） |
| 快照 / 回溯类用例**假失败**报 40001「回溯早于建档时间」 | 夹具用 `CURRENT_TIMESTAMP` 造 `created_at`（= 今天），而用例要回溯到几天前 —— **逻辑上确实非法** | 夹具显式 `created_at = now - timedelta(days=90)`。**别急着改服务层**：先读错误码含义，「夹具时间语义不严谨」会伪装成「功能有 bug」（批次 13） |

## 3. 工具使用纪律（踩过坑）

| 纪律 | 为什么 |
| --- | --- |
| 🔴 **不要对同一文件并行发多个 Edit** | 两个 Edit 各基于旧内容整体写回，**后写的覆盖前一个**，且**两次都返回 Successfully updated**。**同文件改动一律串行。** |
| 🔴 **替换 / 插入时，`new_string` 必须显式包含 `old_string` 的全部文本**（尤其**标题行**） | 曾因漏回补前缀，把修订记录**上一整行吞进新行**（`…未压测等） \|：§7.1 台账…`）；**批次 14 再犯一次**：拿 `### 7.4 CU-05 越权用例集（AC-01 ~ AC-40）🔴` 当 old_string、只放新内容当 new_string → **整行标题消失**（后续 `#### 7.4.1` 成了孤儿）。**Edit 返回 Success 不可信。** 改完必查：`grep -nE "^#{2,4} " 文档.md`（**标题层级必须连续**）+ 交叉引用的小节真的存在。 |
| 🔴 **Git Bash 里别用 `python - <<'PY'` heredoc** | 首个命令失败会回退到交互式 python，在后台**无限刷报错**（曾产出 130MB 输出）。改用「写脚本文件 → 执行 → 删脚本」，脚本放**系统临时目录**（放 `backend/` 会撞 `test_no_stray_python_files_outside_known_dirs`）。 |
| 🔴 **改完文档必须逐项回读校验** | 用「`期望片段 in 文件内容`」清单式校验（Python 一行跑完），才能发现「字段被覆盖」「脚本吃掉某段文字」这类**静默损伤**。 |
| 断言要写**不变量**，别硬编码具体值 | 旧用例 `heads[0] == "0002_dict"` 每加一条迁移就假失败，而**改测试比改代码容易** → 红线被随手放宽。改为断言不变量（单头 + 文件数 == 祖先链长度）。 |
| 文档改动后**交换/移动行**可用脚本做，但要**校验字节数或行数** | 纯 Edit 做行序调整容易误伤；脚本改完必须回读比对。 |
| 🔴 **针对性复跑必须带上 `tests/test_layering.py`**（凡新增 / 改动 Router · `core` · 新模块文件） | 批次 13 新增了 2 个 Router + 1 个 `core` 模块，却**只跑了业务用例** → 「Router 直接 `import app.models.*`」这处**越层只在全量回归里现形**。**新增文件恰恰是结构守卫的管辖对象**，不跑等于**把守卫当摆设**。全量回归仍**每批必跑一次**：它是唯一能抓「跨文件契约」与「结构越界」的一层。 |

## 4. 图运行时的红线守卫（批次 9 · **改图 / 新增图必读**）

> 这不是环境坑，是**编码陷阱** —— 放在这里是因为它「**今天不会造成任何可观测故障**」，
> 所以最容易被人（包括未来的自己）忽略。

### 4.1 陷阱本体

一期沿用**自研图运行时**（评审文档 §7.3 批次 8/9）。自研的语义是
**「挂起即落检查点、恢复只跑后继节点」**，而 **LangGraph 会重放中断节点**
（`interrupt()` 之前的代码**执行两次**）。

⇒ **在中断节点里写副作用，今天不会炸；切换运行时那天会写两遍库 / 计费两次。**
最可能的踩坑姿势：**CU-16 复核工作台**要「落一条复核待办」——
最自然的写法就是在复核节点里写，而那正好踩在红线上。

### 4.2 三道守卫（**已经把「不要这么写」变成「构造不出来 / 会挂用例」**）

| 层 | 落地物 | 拦什么 |
| --- | --- | --- |
| **装配期** | `GraphDefinition.interrupts` / `.side_effects` + `validate()`（`__post_init__` 自动调用） | 中断节点被声明有副作用 → **构造即抛 `NodeError`**。因 `registry` 在**导入时**构图 ⇒ 坏声明**让进程起不来**（**故意的**：比恢复时写两遍库便宜得多） |
| **声明完整性** | `tests/test_graph_replay_safety.py` 用 **AST 扫真实节点源码**，断言 `interrupts` **==** 代码里真实构造 `InterruptRequest` 的节点集合 | 漏声明 —— 否则装配期校验会被**空声明**架空（校验再严，声明为空就永不触发） |
| **红线本体** | 同文件 AST 用例：中断节点在 `InterruptRequest` **之前**不得有写操作 | 就是那条红线 |

外加 **契约 6 可测化**：Service 层只许 import 运行时的 **12 个公开符号**（白名单），
且不得 import 图定义模块 —— 因为「切换时要改的面」正好等于「Service 直接依赖的那些符号」。

### 4.3 两条设计教训（写代码时同样适用）

1. **扫描器必须带正反对照** —— 文件内含「故意写违规代码**必须被抓到**」的用例。
   ⚠️ **「永远返回空列表的检查」比没有检查更危险**：它让人以为检查过了。
2. **误报会让守卫被删掉** —— 排除内存操作（`state.update(...)`）与 `logger.*`；
   提供 `# replay-safe: <理由>` 逃生舱，但**必须写可被复核的理由**（「因为赶时间」不是理由）。

### 4.4 逃生舱与操作要点

```python
GraphDefinition(
    name="...", entry="...", nodes={...},
    interrupts=frozenset({"复核节点"}),              # 会挂起等人的节点
    side_effects=frozenset({"入库节点", "抽取节点"}),  # 会产生外部影响的节点
)
# ⚠️ 两个集合必须无交集，否则构造即抛 NodeError
```

- 需要「落待办 / 发通知 / 计费」时，写在**中断之后的节点**里，或幂等 + 行内 `# replay-safe: <理由>`。
- `side_effects` 同时是**将来启用重放 / 时间旅行时的幂等审计清单**（`describe()` 会把两个标记下发给前端）。
- ⚠️ **改 `app/agents/parse_graph.py` 或新增任何图之后，必跑**
  `pytest tests/test_graph_replay_safety.py` —— 它是「自研 ↔ LangGraph 双运行时语义」之间**唯一**的静态防线。
- 纪律成文于**评审文档 §8.5「图与节点编码铁律」**。

### 4.5 守卫有效性怎么证明（**别只信「用例全绿」**）

往 `field_review` 函数体插一行 `record_change(...)` → **2 条用例失败并精确报出
「第 9 行 + 处置办法」**；撤销后复跑全绿。
**「只写不验」的守卫与没有守卫在结果上等价**，所以这一步不是可选项。

## 5. 前端（CU-23 · 批次 14）—— **前端批次没有 pytest，判绿换三层口径**

前端判绿 = ① `npx vue-tsc --noEmit` **0 error** ② `npx vite build` 通过 ③ **产物核对**（`ls dist/assets | grep -E "View-"` = **页面数 × 2**）+ 页面数**三口径互证**（`find src/views -name "*.vue"` / 路由 `name:` / `path:`）。

**前端的错不报错**，所以要按「不报错的错」找：

| 坑 | 现象（**看起来正常**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴 **`parseBody` 未透传响应顶层 `meta`/`summary`** | 复核队列**永远不显示** `meta.warning`；AI 调用日志 `summary` 恒空。**接口 200、数据也在，页面就是拿不到** | `ApiResult` 没有承载顶层聚合字段的槽位，调用点用 `as Promise<…>` **强行断言掩盖**（编译期被拒 / 运行期恒 `undefined`）。处置：`ApiResult<T, E>` 增 `extra` + `parseBody` 归一 `body.meta ?? body.summary ?? null` + `http` 六方法补泛型 + **删掉掩盖问题的断言** |
| 🔴 **模板 `:disabled="loading"` 引用了不存在的变量** | **按钮永不失效**（可重复提交） | Vue 对未定义标识符解析为 `undefined`，`:disabled="undefined"` **等价于不禁用**。批次 14 在 3 个视图各踩一次 ⇒ 改用真实加载态变量名 |
| 🔴 **给状态机赋了不存在的取值** | 空态 / 加载态显示错乱 | `SearchView` 给 `LoadStatus` 赋 `'empty'` —— **`empty` 是 `StatePanel` 的展示态，不是加载态**。改 `'idle'` |
| **`tsconfig` 工程引用自相矛盾** | 类型检查**根本跑不起来**，看着像「代码问题很多」 | `references` + `composite: true` 与 `noEmit: true` 冲突（TS6305/TS6310），且 `include` 同时含 `vite.config.ts`。**先修工具链再数错误** |
| **`RouteMeta` 索引签名键类型写窄** | 23 条路由**全部**报 TS2322 | vue-router 要求 `Record<PropertyKey, unknown>`，写 `Record<string, unknown>` **缺 `symbol`**。改交叉类型 `Record<PropertyKey, unknown> & { … }` |
| **EP 上传的文件类型谓词写 `File`** | TS2677（`uid` 缺失）+ 过滤结果赋不回 `files: File[]` | `UploadUserFile.raw` 是 `UploadRawFile \| undefined`；谓词写 `f is UploadRawFile`（它 extends `File`，下游仍可当 `File[]`） |
| **`EnumSelect` 承载字符串枚举**（`HIGH`/`LOW`） | 编译报错 / 选项错乱 | `EnumOption.value` 是 `number`；字符串枚举改用原生 `el-select` + `el-option` |
| **合并类操作只做了「合并」** | 界面看着完整，**但没有回滚入口**（后端有 `rollback` 端点） | 交付前按「后端每个写端点是否都有前端入口」对一遍（含**反向操作**，如合并 ↔ 回滚） |
| 🔴 **接口不存在时** | 必然 404，容易被读成「前端有 bug」 | 按契约把前端写完，并导出 `XXX_API_READY = false` + 提示文案，页面渲染「接口未就绪」提示条 |

**判据工具**：拿 `app.openapi()` 的 `paths` 与台账各 CU 认领的 API 编号区间**做差集** —— 批次 14 就是这样发现 **API-30~35 / 64~66 共 9 条无人认领**（B-8 / B-9）。

---

## 6. 测试与验收（CU-26 · 批次 15）

| 坑 | 现象（**看起来正常**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴 **行为断言测不出「先召回后过滤」** | 结果**完全正确**、行为用例全绿 —— 但 Milvus 是**先全量召回再在应用层过滤**（越权数据**已经出过库**） | 这类实现「行为等价、越权面不同」，**只能靠 AST 结构断言**：断言 `_vector_recall` / `_keyword_recall` 签名含 `allowed_org_ids`、且 `provider.search(...)` **显式传 `tenant_id=` / `org_ids=`**（`T-20` / `T-20b`） |
| 🔴 **缺口只写在文档里 = 会腐烂** | 文档写着「已登记缺口」，半年后没人知道闭环没有 | **把缺口写成会失败的用例**：正向固化（断言「当前确实漏检」）+ docstring 写「闭环后请反转本断言」+ **反向对照**（为修缺口而写松校验时，反向对照立刻失败） |
| **「零漏检」式验收措辞不可验证** | 需求写「零漏检」，实现永远达不到，验收时争论不休 | 无法穷举「所有姓名变体」⇒ **是措辞问题**。改为「**已登记变体形态漏检率 = 0**」或明确声明不纳入的形态（与 Q-2 同类） |
| **`ast.literal_eval` 只认 `Assign`** | 解析 Alembic 迁移时**假失败**「未声明 revision」 | autogenerate 产出的是 **`AnnAssign`**（`revision: str = "0001_basement"`）。**两种形态都要支持**（`acceptance_check.py` 首版即踩） |
| **正则锚定不严会「多数一个」** | 数路由条目/关键字时多数出几条 | `routes.ts` 的 `meta` 里也有 `name:` ⇒ 正则**必须锚定行首缩进**：`^\s+name: '` |
| **`pytest` 全绿 ≠「用例没被删」** | 删掉一批用例，回归照样全绿 | 唯一防线是**资产对账**：`acceptance_check.py` **S-08** 拿磁盘 `tests/test_*.py` 与《测试用例与验收脚本》§2.1 清单做**双向差集**（两个方向都要查，单向各漏一半） |
| **junit 的「行数」比磁盘文件数少 1** | 误以为缺文件 | `test_contact_hash.py` 内含两个类（`TestHashContact` / `TestNormalize`），junit 按 **classname** 分行 ⇒ **行数 37 = 文件数 38** |
| 🔴 **新增测试文件必须同步登记两处** | 新用例写完、全绿，但**结构守卫与文档都没它** | ① `tests/test_layering.py` 的 `MODULE_FILES`（按文件落地清单）；② 《测试用例与验收脚本》§2.1 —— **S-08 会对账，不登记即 FAIL** |

---

## 7. 批次 16（CU-29）新增的坑

| 坑 | 现象（**看起来像什么**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴🔴 **本机 HTTP 代理会把「服务未监听」伪造成 502** | N0 探针报 Reranker「**502 异常**」——看起来像**服务起了但报错**，于是去查服务日志/代码 | 根因是**本机代理**（`HTTP_PROXY=http://127.0.0.1:60458`）：走代理的 `curl` 得 **502（代理伪造）**，`curl --noproxy '*'` 得**连接失败（真相：9997 无监听）**。**判据：先 `netstat` 看端口有没有监听。** 处置：探针改用 `httpx.Client(timeout=..., trust_env=False)`（**与 `model_gateway._get_client` 既有做法同源**），并把「**全部连接失败**」归为 `BLOCKED`（待就绪）而**不是 `FAILED`（异常）** —— 🔴 **把环境问题伪装成代码缺陷，会把返工引向错误方向** |
| 🔴 **「无害的说明文案」也能把 PII 带进响应体** | 自检接口出参扫出明文手机号，而代码里**没有任何字段回显原文** | 响应体的 `note` 文案里写了「举例：编号 `91380013800012` 会被误判」，其**子串 `13800138000` 恰好命中生产正则**。处置：① 说明文案**禁止出现形如手机号/身份证的数字示例**；② 断言升级为**「响应体自身必须过生产出网扫描」**的自洽不变量（**全量扫描优于逐字段白名单**，本条就是实证） |
| **`TestClient.get()` 不接受 `json=`** | 5 条「未登录返回 40101」用例**全部 `TypeError` 失败**，看起来像端点有问题 | 失败信息指向**测试**而不是产品。**构造 kwargs 按方法区分**：POST 才传 `json={}`；GET/DELETE 不传 body（否则「40001 参数错误」还会被误读成「认证失败」）。**教训：验证「端点挂了」这件事，必须传对参数，否则失败指向错误的方向** |
| **不可逆动作不幂等 ⇒ 用户反复点击** | 「已归档 → 再归档」抛 42201，**看起来是很合理的防御** | 前端对不可逆动作会带 `Idempotency-Key` 重试，**重试得到「失败」会让用户以为没成功而反复点击，而事实早已完成**。⇒ 已终态**返回成功**，并用 AST 断言「已终态分支内无 Raise」钉死 |
| **工具清单检索的「默认条数上限」会静默截断** | 按文件清单读代码时，**漏看了既有文件**（一度以为要新建 DAO，实际 `talent_dao.py` 早已存在） | `Glob` 等检索默认只返 **100** 条且**无任何提示**。⇒ **清单类检索必须与 `ls` / 显式目录列举交叉核对**；「按记忆/按一次检索结果」下结论前先验完整性 |
| **`pymilvus` 的传递依赖会悄悄改变依赖面** | `pip install pymilvus` 后，`pandas` + `numpy` 一并进来了，而 `requirements.txt` 注释写着「**不引入 pandas**」 | 装包时看**传递依赖**；本机已登记该冲突（**影响 Q-3 信创评审与镜像体积**）。⚠️ 「注释说不引、实际引了」本身就是**文档与事实脱钩**的小样本 |
| 🔴🔴 **探针与生产「不同源」⇒ 报出生产根本不会遇到的错误** | 装了 `pymilvus 3.0.2` 后，Milvus 探针报 `ConnectionNotExistException: should create connection first` —— **看起来像 Milvus 坏了**（而服务是好的：19530 在听、2.6.1） | 探针原先用 **ORM 式 `connections.connect` + `utility.*`**，而生产（`app/retrieval/milvus_provider.MilvusProvider._connect`）用 **`MilvusClient`**（`pymilvus` 3.x 推荐 API）。⇒ **探针必须复用生产同源的调用方式**；否则「假失败」会把返工引向错误的地方（比不测更坏） |
| 🔴🔴 **「服务好、库没建」会表现为「静默降级」而不是「报错」** | 什么都连得上，但向量检索**永远走关键词** —— 日志只有一条 warning，HTTP 200 + `code=50301`，**看起来像「Milvus 没装」于是长期误判** | `settings.MILVUS_DATABASE='talent_vec'` 而服务端只有 `default`；生产 `_connect()` 把「库不存在」（`code=800`）当连接失败**捕获 → 返回 None → 降级**。⇒ **探针必须显式检查「配置的库是否存在」**：⚠️ 检查时**故意不传 `db_name`**（先以服务器视角 `list_databases()`），否则库不存在会直接抛异常，连「服务是死是活」都答不上来。修法二选一：`create_database('talent_vec')`（推荐）或改配置指向 `default`（**别与别的项目混库**）。<br>🔁 **批次 17 更新：本条的**事实前提**已消除**（用户已建库 `talent_vec`，备选修法「改指 `default`」作废），**但「配置级不一致 ⇒ 静默降级」的机制与「探针必须显式查库」的做法仍然成立、不可撤** —— 换一种配置不匹配（`MILVUS_HOST` 写错 / collection 名与文档不符 / 桶名不符）仍会以同样方式静默降级 |
| **「环境未就绪」的两种伪装** | ① 端口无监听被报成 **502 异常**（本机 HTTP 代理伪造）；② 库/桶不存在被报成 **服务不可用** | 判据顺序：**先 `netstat` 看端口** → 再看「服务是否可枚举资源（库/桶/模型）」→ 最后才怀疑代码。探针状态用 **`BLOCKED`（待就绪）≠ `FAILED`（异常）** 区分 —— **把环境问题伪装成代码缺陷，返工方向必错** |

---

## 8. 批次 17（配置收敛核实 + N0 复跑修正）新增的坑

| 坑 | 现象（**看起来像什么**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴🔴 **密钥类配置「填了」≠「填对」** | Dify 探针报 `401 UNAUTHORIZED` —— 看起来像**鉴权 / 网关 / 端口**问题（`DIFY_BASE_URL` 少了端口？应用没发布？），于是去查端口、查容器、查路由，**方向全错** | 真因是**手抄错字**：`.env` 写 `…ZdiXQxmqjy9`，服务端实际为 `…ZdixQXmqjv9` —— **第 21/23 位大小写颠倒 + 第 27 位 `v→y`**（共 3 处）。⇒ **密钥类配置要「与服务端权威记录比对」，不要靠反复重试**：本批直读 Dify 库（`docker exec docker-db_postgres-1 psql -U postgres -d dify`）的 `apps` / `api_tokens` 取权威值，**先实测 200、再回写** |
| 🔴 **同一个 `401` 有两种含义，必须读 body** | 只看状态码会得出「Dify 鉴权坏了」这种**无法行动**的结论 | `401 {"message":"Access token is invalid"}`（**token 错 → 去改值**）**vs** `401 {"message":"Authorization header must be provided and start with 'Bearer'"}`（**没带头 → 去补头**）。返工方向**完全相反** ⇒ **鉴权类失败一律打印响应体** |
| 🔴 **「已配置但 401」必须与「未配置」分开报** | 批次 16 记「⛔ Dify key 未配置」，补上 key 后变成「❌ 401」—— 容易被读成「**越改越坏**」 | 两者返工方向相反（**去配** vs **查值**）。`⛔ → ❌ → ✅` **比停在 ⛔ 有价值**：它把被掩盖的问题暴露了出来。⇒ 探针保持**四态**（`OK` / `DEGRADED` / `BLOCKED` / `FAILED`），**不要把「填了个错值」并进「未配置」** |
| 🔴 **只读核实脚本自己的正则会把换行吃进值里** | `.env.example` 解析结果显示 `MINIO_ACCESS_KEY = MINIO_SECRET_KEY=` —— **看起来像「密钥被写串行了」**，几乎要动手去「修」一个**不存在的 Bug** | `^(KEY)\s*=\s*(.*)$` + `re.M`：`=` 后的 **`\s*` 能匹配 `\n`** ⇒ 空值行把**下一行的键名**当成值。⇒ **按行解析**（`splitlines()` + `partition('=')`），**不要用 `\s*` 跨行**。**同类**：批次 8 的 `ast.walk` 误判延迟导入 · 批次 16 的本机代理伪造 502。**凡用来「核实」的代码，它自己必须先被核实；其输出必须能被第二眼看穿** |
| **`.env.example` 的密钥项本来就该留空占位** | 看到模板里 `MINIO_ACCESS_KEY=` 空着，容易「顺手补全」 | `.env` 在 `.gitignore` 中、`!.env.example` 是**唯一例外** ⇒ **把真密钥写进模板 = 把密钥推进版本库**。**空占位是设计，不是遗漏** |
| **本机 `DIFY_BASE_URL=http://127.0.0.1` 是「无端口」的** | 看到 80 端口被 nginx 占着，容易判断「Dify 没配端口 / 指向错了」 | **80 上的 `docker-nginx-1` 就是 Dify compose 的入口**（同批 `docker-api-1` / `docker-web-1` / `docker-worker-1` / `docker-plugin_daemon-1` …）。⇒ **先 `docker ps` 按 compose 项目名分组看容器，再判断端口归谁** —— 本机 **33 个容器、多项目并存**，`netstat` 只给 PID，**看不出归属** |
| 🔴 **「环境快照」被写成断言 ⇒ 环境一变用例就红** | 全量回归 **1 个用例失败**（`assert True is False`），而**本轮只改了 `.env` 的一个 key** —— 看起来像**代码回归**，排查方向会跑到产品代码上去 | `tests/test_dedup_merge.py` 断言 `vector_reindexed is False`，其前提**写在文件 docstring 里**：「向量（Milvus）**在本环境**不可用」。用户建好 `talent_vec` 后 `get_retrieval_provider()` 返回 Milvus 实现 ⇒ `_reindex` 返回 `True`。⇒ **处置与 `S-09` 同源**：**把隐含的环境依赖改成显式、可复现的注入**（用例内 `monkeypatch` 把 provider 换成 `KeywordRetrievalProvider`，确定性走降级分支），**不是**把断言放松成恒真。⚠️ **排查提示：先问「这个断言依赖环境吗」，再怀疑代码；且「本轮没碰过那块代码」不等于「不是我造成的」** |
| 🔴 **「报告成功」但实际没做：`vector_reindexed=True` 是假信号** | 接口回执说「主档已重索引」，而 Milvus 里 `row_count = 0`、**该档案实际检索不到** | `MilvusProvider.ingest` 在 `vector is None` 时**提前 return、跳过入库**（只打一条 warning），**不设 `last_error`**；而 `_reindex` 用 `provider.last_error is None` 当成功判据。⇒ 🔴 **「有没有报错」≠「有没有真的做」**：成功判据必须落在**结果**上（是否真的落库），不能落在**异常缺席**上。**同族**：批次 16「库不存在 ⇒ 静默降级」、批次 11「`vector=None` 占位」 |
| 🔴 **「不存在就创建」的语义被用在「清理」路径上** | 归档 / 删除本应**清理**派生索引，结果**在 Milvus 里新建了 collection**（顺带把「清理失败」变成「清理成功」） | `_get_collection()` 一律「不存在则 `_create_collection`」，而 `delete_all()` 逐 `COLLECTIONS` 调它 ⇒ **清理一个不存在的 collection 会先创建再删**。**实测**：本机 `talent_vec` collection 数 **0 → 3**，**由跑一次全量回归造成**。⇒ ① 清理路径应 **no-op**；② 附带暴露 **L2 用例会写用户真实 Milvus（不隔离）** —— 此前因「库不存在」而从未显现 |
| 🔴 **测试会写真实外部资源，而「资源不存在」会把它掩盖起来** | 环境里没有那个库 / 桶时，所有副作用**静默不发生** ⇒ 测试「看起来干净」；一旦有人把资源建起来，副作用立刻显现，**还会悄悄改掉既有断言的结论** | ⇒ **资源就绪之后必须重跑全量，而不是只复跑「相关」用例** —— 本轮若只复跑 N0 探针与配置用例（它们才「相关」），就会**完全错过**这条。**同源**：批次 13「针对性复跑必须带 `test_layering.py`」—— 都是「**该跑的没跑，红点只在全量里现形**」 |

---

## 9. 批次 20（「导入全部解析失败」定位）新增的坑

| 坑 | 现象（**看起来像什么**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴🔴 **「操作失败」的错误码，其实指「正在等人工」** | `import_file.parse_error = PARSE_INTERRUPTED`，界面显示「全部解析失败」—— 看起来像**解析崩溃/异常终止** | 该码的触发点是 `outcome.status == "waiting_human"`（`parse_graph` 的 `field_review` 中断点）。⇒ **一个错误码承担了两种相反含义**（「等人复核」vs「异常终止」），且展示文案是后者。**排查提示**：看到「失败」先追**错误码的字面**而不是它的中文名；**处置**：转人工应有**独立状态**，与真失败分开 |
| 🔴🔴 **`text` 恒为空：解析图缺「文本获取」这一环** | 所有格式**都**抽不到字段 → 全部转人工，看起来像「抽取器不行 / 模型不可用」 | ① PDF/图片走 `ocr()` 占位（`KEY_OCR` 未注入 → `text=""`）；② 🔴 **docx/txt 更彻底 —— 非 OCR 文件从 `detect_format` 直跳 `extract_fields`，图里没有任何取文本节点，且 `build_parser_state()` 从不写 `text`**。⇒ **不是「OCR 没装」而是「文本源缺失」**，与格式无关。**判据**：`agent_task.graph_state` 里的 `"text": ""` 是决定性证据（比任何日志都直接） |
| 🔴 **同一会话里「先读后写再读」会读到过期快照（REPEATABLE-READ）** | `import_task.success_count/fail_count` 恒 `0/0`、终态恒 `COMPLETED`，而 `import_file` 里**全是 FAILED** —— 看起来像「前端没刷新」或「统计 SQL 写错」 | `process_task` 主循环用**同一个 `db` 会话**，文件状态却由**独立会话 `file_db`** 提交；`list_files` 与 `count_by_status` **共享同一事务且中间无 `commit`** ⇒ 沿用 `list_files` 时的读视图（文件仍 PENDING）。MySQL 默认 **REPEATABLE-READ**（实测，应用未覆盖）。⇒ **修法二选一**：`count_by_status` 前 `db.commit()`/`expire_all()`，**或直接采用循环里已算好的 `success`/`failed`**。🔴 后者才是本条的教训 —— **「算对了却不用、改用一次可能过期的 DB 读取」** |
| 🔴 **同一行 UI 的两个数来自不同数据源 ⇒ 矛盾即指纹** | 「状态=已完成」配「成功/失败=0/3」—— 一眼看上去像**前端拼接 bug** | `to_task_out()` 里 `status=int(task.status)` 读**存储列**（陈旧 → 已完成），而 `success_count/fail_count` 走 `count_by_status()` **实时统计**（真实 → 0/3）。⇒ **「同一界面元素内自相矛盾」是定位数据一致性 bug 的高价值信号**：先查这两个数**分别来自哪个查询**，往往直接落到根因，不必从前端翻起 |

## 10. 批次 21（导入解析三修）新增的坑

| 坑 | 现象（**看起来像什么**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴🔴 **跨会话传 ORM 行对象 ⇒ `commit()` 提交「空事务」** | `file_db.commit()` 全程无报错，但文件行状态**一直不变**；任务行却显示「已完成 / 0 / 0」 | `process_task` 用**主会话** `get_file(...)` 拿到行对象，传给 `_process_one_file`，后者 `mark_file(file_db, file_row, …)` —— **改的是主会话的实例**，`file_db` 里根本没加载过它 ⇒ 提交空事务。**判据（5 秒验证）**：`file_db.commit()` 之后**换一个全新会话**重读该行，状态仍是旧值 ⇒ 就是它。**纪律**：**每一层用自己的会话重新读行**，不跨会话传对象 |
| 🔴 **批次 20 的「C 修复」是误判：真问题不是「读得旧」而是「根本没提交」** | 当时诊断为「REPEATABLE-READ 过期快照」，改成新会话计数后**看起来修好了** | 方向**不够**：计数读不到失败，是因为**失败状态压根没落库**。⇒ **教训：修完仍无法解释「为什么会读到旧值」时，说明根因还没找到** —— 别把「症状消失」当「根因坐实」 |
| 🔴🔴 **回归用例把「出问题的那个函数」monkeypatch 成正确替身 ⇒ 恒绿** | `test_process_task_status_reflects_real_file_outcomes` 全绿，而线上就是坏的 | 它替换掉 `_process_one_file`（**缺陷本体**）为「逐文件独立会话提交」的正确版 ⇒ 测的是替身不是产品。**纪律**：回归**只 monkeypatch 外部依赖**（模型 / Redis / 时钟），**绝不替换被测函数**；新用例走 `create_task(run_async=False)` 真实路径 |
| 🔴 **既有用例「靠缺陷才绿」** | `test_talent_import` 一直绿，账目修好后**立刻变红**（断言 `retry in (0, 40001, 40002)`） | 夹具是「**纯文本字节 + `.doc` 后缀**」⇒ **必然解析失败**；但在账目缺陷下任务仍被判 `COMPLETED` ⇒「拿失败任务出质量报告」也能过。**处置**：改**夹具**（真 docx）+ **收紧断言**（`retry == 42201`），**不放松判据** |
| 🔴 **`\s+` 会把换行一起吞掉**（第二次踩） | 汉字间空白折叠后，整块抬头被拼成**一行** `付智超性别 ：男 …`，首行姓名启发式**全废** | `\s` 含 `\n` ⇒ 只折**行内**空白必须写 `[ \t\u3000]+`。**发现方式**：三份 PDF 的姓名来源**全部退化成 `filename`**（异常集中出现 = 信号）。⇒ 同批次 17 `.env` 被 `\s*` 吞换行：**在「按行解析」的场景里用 `\s` 一律是 bug** |
| 🔴 **折叠类清洗的副作用会「造出」错误值，且截断上限会掩盖它** | `姓名：张三 电话 138…` → 折叠成 `姓名：张三电话 138…` ⇒ 抽出「**张三电话**」**能写进档案** | 两个叠加原因：① 折叠把字段边界抹掉；② `_NAME_RE` 的 `{2,4}` 上限**恰好把标签词「电」切掉**，使边界检测失效。**处置**：**宽捕获 `{2,8}` + 按标签词截断 + 形状校验**。⇒ **教训：任何「归一化/清洗」都必须配一条「边界不得被抹掉」的用例** |
| 🔴 **本机安全删除钩子会污染 pytest** | 整轮多出 3 个 error，其中一条在**无关**的 `test_layering` 上，报 "previous item was not torn down properly" | teardown 里删文件 ⇒ 钩子（`…cli\vendor\shim\sitecustomize.py`，**本轮累计**删除量 > 50）`raise SystemExit(1)`；`SystemExit` 出现在 teardown ⇒ **连带把下一条用例的 setup 弄坏**。**处置**：夹具**只清 DB 行、不碰盘上文件**（`rmtree`→逐个 `unlink` 无效，因为统计的是**累计**量） |
| 🔴 **`Get-NetTCPConnection -State Listen` 假阴性** | **无输出 + 退出码 0** ⇒ 一度据此判定「后端已停」并准备给用户错误的建议 | `netstat -ano \| grep 8002` 明确显示 `LISTENING PID 15028`。⇒ **判断「进程在跑 / 端口被占」一律以 `netstat -ano` 为准** |
| 🔴 **「用户说改了」不等于「改了」，「代码在磁盘上」也不等于「进程加载了」** | 已实测 `agent_task.graph_state` 里 `text_len=5018`（**新代码**）而同一批另两份 `text_len=0`（旧代码）⇒ 证明用户已重启过 | ⇒ 排障时**用「哪些行是新代码产物、哪些是旧代码产物」反推进程状态**，比问「你重启了吗」可靠。**旧代码产物**：无 `extract_text` 步、`text_len=0` |

## 11. 批次 22（端到端联调）新增的坑

| 坑 | 现象（**看起来像什么**） | 根因 / 处置 |
| --- | --- | --- |
| 🔴🔴 **「重试」按钮完全无效：幂等键把「重跑」短路成「回放」** | 用户反复点重试，文件永远是失败 —— 看起来像**解析器还是坏的** | `retry_failed` 只清 `import_file.agent_task_id`，**旧 `agent_task` 行的 `idempotent_key=parse:{task_id}:{resume_id}` 仍在** ⇒ `AgentService.start_task` 命中即 `replayed=True` **回放首次结果、不重跑图**；且 `parse_error` 被写成**回放指纹 `PARSE_FAILED`**（真实原因码被抹掉，误导排查方向）。**判据**：重试后 `agent_task` 行数**没变**（仍是 1）。**处置**：重试时把旧键置 `NULL`（模型 docstring 本就规定「重试属非幂等场景」）。⇒ **凡「重试/重跑/重新生成」类动作，先问「会不会命中幂等键」** |
| 🔴 **「用例断言账目一致」不代表「重试真的重跑」** | 新用例全绿，线上重试仍然无效 | 回放路径**同样满足**「任务行与文件行一致」⇒ 断言必须钉在「**新增了一个 agent_task**」这种**只有真跑才会出现的**事实上 |
| 🔴🔴 **`mysql … \| grep` 会把结果当二进制吞掉（本批差点误判出「入库丢失」缺陷）** | `mysql -e "..." \| grep -v Warning` 输出「只看到表头、没有数据行」，再配合 `COUNT(*)` 结果 ⇒ 得出「档案 0 行」的**错误结论**，差点去查一个不存在的写库缺陷 | 输出里含二进制字节时 `grep` 打印 **`Binary file (standard input) matches`** 并**丢弃大部分行**（本机 mysql 输出含非 UTF-8 字节即可触发）。**处置**：改用**应用自己的会话**（`SessionLocal` + `sa.text`）读库并 `print(tuple(row))` —— 结论立刻变成「4 行俱在」。⚠️ 同族：批次 17 `\s*` 跨行吞换行 · 批次 16 本机代理伪造 502 · 批次 8 `ast.walk` 误判 |
| 🔴 **功能全对、说法全错：前端默认口径与后端不一致** | 导入成功（任务页显示成功 1）但**档案列表是空的** ⇒ 看起来像「没入库」 | 后端 `GET /talents` **未传 `data_status` 时只返回 `1 有效`**（API-30 明文），而导入落库是 `0 待复核`；前端下拉占位却写「**全部状态**」。⇒ **这类缺陷不产生任何日志/异常，只有走完整条链路才会现形**。**处置**：文案改为「状态（默认仅有效）」 |
| 🔴 **「文件 SUCCESS + `talent_id` 非空」≠「用户能用上」** | 以为已经修好，收工时用户仍会说「看不到我的简历」 | 完整链路是**四段**：导入/重试 → 解析入库（`0 待复核`）→ **复核确认** → `1 有效` → 出现在默认列表。⇒ **验收必须走完四段**（`_e2e_import_check.py --confirm-one`） |
| 🔴 **验证进程加载了哪版代码：源码 mtime vs `.pyc` mtime** | 反复怀疑「改的代码没生效」 | uvicorn **不带 `--reload`** 时不重读源码 ⇒ **源码新于 pyc = 进程加载的是旧模块**（本批：源码 `09-24 09:45` / pyc `09-23`）。交叉证据：错误文案新旧 · `agent_step` 有无 `extract_text` 步 |


## 12. 批次 23（口径反转收尾）新增的坑

| 坑 | 教训 / 正确做法 |
| --- | --- |
| 🔴 **`SNOWFLAKE_WORKER_ID=99` 越界 ⇒ 全量回归 252 个 setup error + 2 failure** | 合法范围 **0~31**（`app/core/snowflake.py:72` 单例构造即 `raise ValueError: worker_id 必须落在 0~31`）。想「错开撞号」时写了 99，结果**自伤**：错误堆里全是 `ValueError`，看似大面积故障实为一个越界值。**正确做法：0~31 内且 ≠1（实测 2）**。 |
| 🔴 **「错开 WORKER_ID」只治撞号（1062），不治死锁（1213）** | 死锁出现在 teardown 批量 DELETE，与在跑后端互斥锁冲突 —— **只有停掉在跑后端才能消**。干净全量回归的两个前提：**停后端 + WORKER_ID 合法错开**，缺一不可（缺①→50 error；缺②→252 error）。跑完**记得把后端启回来**（用户前端依赖 8002）。 |
| 🔴 **pytest -q 的终端摘要可能被吞（safe-delete 钩子/二进制字节），结论只认 junit XML** | 本次 `tail` 只见进度条与 `[safe-delete]` JSON，不见 summary 行 —— 用 ElementTree 解析 `.junit_batch23.xml` 的 `tests/failures/errors/skipped` 才是权威。与既有「`mysql \| grep` 吞行」同类：**输出层会骗人，取结构化记录**。 |
| 🔴 **会话摘要对截图的转述不可信，必须回看原图** | 摘要称「006 成功 1/0」，实际截图列是「成功/失败」，**006 是红色失败 0/1**。在截图上做结论前回读原图核对列含义，别拿二手转述当证据。 |

## 13. 批次 24（进度条 + SSE 修复）新增的坑

| 坑 | 教训 / 正确做法 |
| --- | --- |
| 🔴 **Vite 代理目标写 `localhost` ⇒ 时好时坏的断链（SSE 空白 / 接口间歇失败）** | Node 17+ `dns.lookup` verbatim 按操作系统顺序解析 `localhost`，Windows 常 `::1` 排前，而 uvicorn 只绑 IPv4 `0.0.0.0` ⇒ 代理连 `::1:8002` 被拒（10061）。**正确做法：代理/上游一律写死 `127.0.0.1`**。同一根因会同时表现为「SSE 无输出」+「页面数据是旧快照」—— 别当两个 bug 查。 |
| 🔴 **本机 shell 的 `http_proxy=127.0.0.1:50138` 会劫持 curl 探针** | 打 5173 报「upstream connect failed … 10061」其实是**代理**的上游失败，不是 Vite 的 —— 一度误判「修复没生效」。**curl 探针一律 `--noproxy '*'`**（与后端探针 `trust_env=False` 同一条纪律）。 |
| 🔴 **Vite dev server 默认只绑 `[::1]:5173`** | 直打 `127.0.0.1:5173` 连接拒绝（curl EXIT=7）—— 探针要按 `netstat` 显示的**真实绑定地址**打（本次是 `http://[::1]:5173`）。 |
| 🔴 **给「不可能状态」写回归用例 = 把断言钉在假世界上** | 批次 24 用例曾把僵尸任务仿真成 `talent_id` 非空但 `parse_status=PENDING` —— 实际两者由 `start_task` 的 commit **原子提交**，生产不可达；回调的幂等锚（`talent_id` 已设 ⇒ 直接返回旧 ID）让它「看起来像新缺陷」。仿真状态前先推演**真实故障时序**里该状态是否可达。 |
| 🔴 **幂等键回放陷阱会随新入口复发** | 批次 22 修了 `retry_failed`，批次 24 的**启动自愈**又踩一次（PENDING 文件重入被旧键短路成回放 ⇒ 「已完成 0/0」复发）。已把键作废下沉到 `process_task(only_pending=True)`（所有重入的必经点）。**新增任何「重跑」入口前先问：它的文件还挂着旧幂等键吗？** |
