# 人才测评分析 Agent Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create six interactive static prototype pages for the confirmed talent assessment scope and connect them from the existing prototype index.

**Architecture:** Add one shared CSS/JS layer for the assessment pages while keeping each page independently openable from the `prototype/` directory. Each page uses representative data and client-side interactions only; no backend contract or production frontend route is changed.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, existing prototype visual tokens, PowerShell static server, browser viewport checks.

**Spec:** `docs/superpowers/specs/2026-09-28-talent-assessment-agent-design.md`

## Global Constraints

- Reuse the existing prototype palette and density: `--bg #f3f6f7`, `--primary #175d75`, `--radius 6px`.
- Keep the prototype within the confirmed scope: question bank, paper assembly, online answering, monitoring, and evidence-backed capability reports.
- Objective scoring remains deterministic in the UI copy; AI output is always marked as draft and requires human confirmation before publication.
- Every page contains visible loading, empty, forbidden, and error-state examples in a compact state gallery.
- Do not modify production Vue routes, backend schemas, or database files in this prototype pass.

---

### Task 1: Shared assessment prototype foundation

**Files:**
- Create: `prototype/assessment.css`
- Create: `prototype/assessment.js`

**Interfaces:**
- Pages load `assessment.css` and `assessment.js` with relative paths.
- `assessment.js` exposes `toggleNav()`, `switchTab()`, `toast()`, and `setActiveMode()` for page-local controls.

- [ ] **Step 1: Create shared layout and tokens**

Add the existing sidebar/topbar shell, responsive breakpoints, table/card/form primitives, state gallery, status tags, radar chart helpers, and assessment-specific colors. Keep the sidebar links pointing to existing prototype pages and the new P-21~P-26 files.

- [ ] **Step 2: Create shared interactions**

Implement no-dependency interactions: mobile nav open/close, tabs, segmented controls, toast notices, drawer open/close, and button feedback. All interactions must degrade to readable static content when JavaScript is unavailable.

- [ ] **Step 3: Verify the foundation**

Run a static server from `prototype/` and open each shared asset through HTTP. Expected: CSS and JS return 200 and contain no console-blocking syntax error.

### Task 2: Question bank and paper assembly pages

**Files:**
- Create: `prototype/p21-assessment-question.html`
- Create: `prototype/p22-assessment-paper.html`

**Interfaces:**
- P-21 links to P-22 through “用于组卷” actions.
- P-22 shows the selected question pool, fixed/random mode, validation summary, preview, and publish controls.

- [ ] **Step 1: Build P-21 question bank**

Include filters for question type, dimension, difficulty, review status, and source; a list with version and audit columns; a create/edit drawer with stem, options, answer/rubric, dimension weights, and difficulty; review actions; version history; and a disabled “用于组卷” action for draft/unapproved questions.

- [ ] **Step 2: Build P-22 paper assembly**

Include fixed/random segmented control, duration and total score, dimension quota rows, question pool filters, selected-question tray, validation panel, preview modal/section, and a publish button. Show the frozen-version and random-seed rules in the page.

- [ ] **Step 3: Add interactions**

Make P-21 status tabs filter visible rows, make P-22 fixed/random mode update the rule summary, and make “发布试卷” show a confirmation toast only when validation is green.

- [ ] **Step 4: Verify these pages**

Open both pages at desktop and 390px widths. Expected: no horizontal overflow in the main layout, filters wrap, drawer/preview remain readable, and all primary actions provide feedback.

### Task 3: Task management and online answering pages

**Files:**
- Create: `prototype/p23-assessment-task.html`
- Create: `prototype/p24-assessment-take.html`

**Interfaces:**
- P-23 links “进入作答预览” to P-24 and “查看分析” to P-25.
- P-24 uses the same paper/task sample data as P-23 and presents a candidate-safe view without answer keys.

- [ ] **Step 1: Build P-23 task management**

Include task KPIs, task status filters, task creation panel with paper, target scope, schedule, duration, attempt limit, notification mode, candidate progress table, and anomaly count. Add empty and forbidden state examples.

- [ ] **Step 2: Build P-24 online answering**

Include fixed top timer, progress indicator, question navigator, single/multiple/short-answer examples, save status, previous/next controls, submit confirmation, deadline state, reconnect state, and the monitoring notice without exposing scoring rules or answer keys.

- [ ] **Step 3: Add answering interactions**

Implement question navigation, selected-option styling, short-answer character count, simulated autosave timestamp, idle warning toggle, and submit confirmation that changes the page to a submitted state.

- [ ] **Step 4: Verify these pages**

Check desktop and mobile layouts. Expected: timer remains visible, navigator stays usable, answer controls do not shift the page, submit state is explicit, and no candidate-only page reveals standard answers.

### Task 4: Monitoring and evidence-backed report pages

**Files:**
- Create: `prototype/p25-assessment-monitor.html`
- Create: `prototype/p26-assessment-report.html`

**Interfaces:**
- P-25 links a candidate row to P-26 and exposes event details without declaring automatic cheating.
- P-26 links evidence rows to a report conclusion and shows draft/review/published states.

- [ ] **Step 1: Build P-25 monitoring/results**

Include candidate result table, score distribution summary, dimension radar chart, anomaly timeline, event severity tags, filtering by task/status/anomaly, and a human review panel. Use language “异常事件待核查”, never “系统判定作弊”.

- [ ] **Step 2: Build P-26 report/evidence**

Include report header/version/status, score and radar summary, strengths/gaps/recommendations, evidence drawer with question/answer/score/source, AI draft versus human confirmed badges, reviewer comment box, publish/export controls, and evidence-incomplete blocking state.

- [ ] **Step 3: Add report interactions**

Implement evidence selection, report tabs, review decision toggle, publish confirmation only after all evidence checks pass, and export feedback. Make the “AI 建议分” visibly separate from “最终确认分”.

- [ ] **Step 4: Verify these pages**

Open both pages at desktop and mobile widths. Expected: radar chart remains bounded, evidence panel does not occlude the report, status changes are readable, and the publish block is clear when evidence is incomplete.

### Task 5: Prototype index and final verification

**Files:**
- Modify: `prototype/index.html`

**Interfaces:**
- The index exposes six new cards P-21~P-26 and preserves all existing prototype links.

- [ ] **Step 1: Add the six prototype cards**

Add a “人才测评分析 Agent（本期新增）” group with links, scope labels, and role labels for P-21~P-26.

- [ ] **Step 2: Run static validation**

Use PowerShell to verify all six files exist, all relative CSS/JS references resolve, and each page has a title, main content landmark, and responsive viewport meta tag.

- [ ] **Step 3: Run browser visual checks**

Serve `prototype/` over HTTP and inspect P-21, P-22, P-24, P-25, and P-26 at 1440x900 and 390x844. Check no blank page, no overlapping text, no clipped buttons, and no horizontal scrollbar on mobile.

