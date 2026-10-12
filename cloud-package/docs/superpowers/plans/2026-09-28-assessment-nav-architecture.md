# 人才测评导航与 Agent 架构文档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 P-21~P-26 与既有 P-1~P-19 的导航和视觉基线，并产出可供后续开发使用的人才测评分析 Agent 架构设计 Markdown 文档。

**Architecture:** 继续使用静态 HTML 原型，不引入前端框架或后端改动。六个测评页面保留各自页面内容，但共享原有原型的侧栏信息架构、设计令牌和角色标识；架构文档复用现有 Agent 基座、权限模型、测评规格中的 TBL/API 编号。

**Tech Stack:** HTML5、CSS3、原生 JavaScript、Markdown、PowerShell 静态 HTTP 服务。

**Spec:** `docs/superpowers/specs/2026-09-28-talent-assessment-agent-design.md`

## Global Constraints

- 保留 P-1~P-19 的页面链接和原有菜单顺序。
- P-21~P-26 仅为静态原型，不修改生产 Vue、FastAPI、数据库和接口实现。
- 测评页使用原有原型的蓝色设计令牌，不继续维护第二套青绿色导航主题。
- 监控文案只能表达“异常事件待核查”，不能表达系统自动判定作弊。
- 客观题评分保持确定性；主观题 AI 结果只能作为建议，发布前必须人工确认。
- 架构文档中的 TBL-29~41、API-96~131 与现有测评规格保持一致。

---

### Task 1: 统一测评页面导航

**Files:**
- Modify: `prototype/assessment.css`
- Modify: `prototype/p21-assessment-question.html`
- Modify: `prototype/p22-assessment-paper.html`
- Modify: `prototype/p23-assessment-task.html`
- Modify: `prototype/p24-assessment-take.html`
- Modify: `prototype/p25-assessment-monitor.html`
- Modify: `prototype/p26-assessment-report.html`

**Interfaces:**
- 每个测评页面继续加载 `assessment.css` 和 `assessment.js`。
- 每个页面继续使用相对路径链接；原有 P-1~P-19 页面入口必须可达。
- 侧栏新增测评入口，但不删除原有档案、AI 中台和系统管理入口。

- [x] **Step 1: 替换六个页面的侧栏菜单结构**

统一使用以下分组和顺序：工作台首页、人才档案、人才测评 Agent、AI 中台、系统管理。人才档案分组保留人才档案列表、语义人才检索、档案 RAG 问答、批量导入、字段复核工作台、重复档案合并、标签字典、我的画像；测评分组放置题库管理、组卷工作台、测评任务、结果与监控、能力报告；AI 中台保留模型与 Prompt、调用日志与成本、Agent 任务与待办；系统管理保留组织架构、用户管理、角色与权限、数据字典与配置、审计日志。锁定入口沿用原型中的 `ADM`、`HR`、`AIOPS`、`AUDIT` 角色文字。

- [x] **Step 2: 对齐导航视觉令牌**

将 `assessment.css` 的导航相关颜色、侧栏宽度、品牌标识、活动态和锁定标识对齐 `prototype/_src/02_app.css` 的蓝色令牌；保留测评页面主体既有卡片和报告内容，只修改共享导航基础样式。

- [x] **Step 3: 静态检查导航链接**

用 PowerShell 解析六个页面的 `href`，逐一确认目标文件存在；检查每个页面只有一个活动导航项，当前页活动项与页面标题一致。

### Task 2: 编写人才测评分析 Agent 架构设计文档

**Files:**
- Create: `docs/人才测评分析Agent-架构设计.md`
- Reference: `docs/superpowers/specs/2026-09-28-talent-assessment-agent-design.md`
- Reference: `docs/AI数字化人才平台-架构设计.md`

**Interfaces:**
- 文档面向产品、后端、前端、数据和运维评审人员。
- 文档必须使用现有 `agent_task`、`agent_step`、`agent_interrupt` 作为 Agent 运行基座。
- 文档中的模块、表、接口和状态名称可直接作为后续实现约定。

- [x] **Step 1: 写明范围、角色和系统边界**

明确题库、组卷、任务、在线作答、监控、评分、报告和证据引用属于本期范围；明确 ADM、HR、MGR、EMP、AIOPS 的数据范围与操作边界；明确不包含摄像头监考、自动作弊判定和无人化发布。

- [x] **Step 2: 写明分层架构和数据流**

使用 Mermaid 描述 Web/API 层、测评领域服务、Agent 编排层、评分与证据层、数据存储层、通知与审计层之间的依赖；给出“创建任务→作答→评分→人工复核→发布→画像快照”的主链路。

- [x] **Step 3: 写明 Agent 图、状态和人工介入**

固定 `validate_attempt → score_objective → aggregate_dimensions → prepare_subjective_evidence → ai_review_subjective → build_report_draft → review_gate → publish_report → sync_talent_profile` 节点；说明重试、幂等、断点恢复、`interrupt` 和待办回放规则。

- [x] **Step 4: 写明数据模型、接口和安全约束**

收录 TBL-29~41、API-96~131、评分来源、报告状态、证据完整性、监控事件和权限裁剪规则；明确所有租户/组织范围由服务端决定，历史快照只追加不覆盖。

- [x] **Step 5: 写明可靠性、部署、观测和分期验收**

覆盖消息/任务重试、超时、失败补偿、审计关联、指标、日志、告警、灰度和 8 个开发阶段；每个阶段给出可验证的交付物和关键验收指标。

### Task 3: 验证原型和架构文档

**Files:**
- Verify: `prototype/assessment.css`
- Verify: `prototype/assessment.js`
- Verify: `prototype/p21-assessment-question.html` through `prototype/p26-assessment-report.html`
- Verify: `docs/人才测评分析Agent-架构设计.md`

**Interfaces:**
- 静态服务地址：`http://127.0.0.1:8765/`。
- 验证覆盖桌面默认视口和窄视口，重点检查导航、报告页雷达图、表格和弹窗。

- [x] **Step 1: 校验文档和页面资源**

确认六个页面、共享 CSS/JS、入口页返回 HTTP 200；确认六个页面包含 title、viewport 和 `main-content`。

- [x] **Step 2: 浏览器检查导航和主要交互**

逐页打开六个页面，检查完整侧栏、当前活动项、移动端导航展开、题目抽屉、固定/随机组卷切换、在线作答选择、报告发布确认弹窗。

- [x] **Step 3: 检查窄视口布局**

确认主体无页面级横向滚动条，评分明细表只在自身容器内滚动，导航和报告内容不遮挡。

- [x] **Step 4: 自审架构文档**

检索并清理 `TBD`、`TODO`、未定义接口、空 Mermaid 节点和与测评规格冲突的表号/状态；确认文档中的范围与页面实现一致。
