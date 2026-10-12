<template>
  <div>
    <PageHeader :title="`你好，${auth.displayName}`" :desc="desc">
      <template #actions>
        <el-button v-if="auth.hasPerm('import:view')" @click="router.push('/talent/import')">
          <el-icon><UploadFilled /></el-icon>批量导入
        </el-button>
        <el-button type="primary" @click="router.push('/talent/search')">
          <el-icon><Aim /></el-icon>语义检索
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 数据域常驻提示：本页所有卡片都受它约束，不写清楚会让人误以为「数据不全」 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        当前数据域：<b>{{ auth.scopeLabel }}</b>
        <span class="muted">
          （{{ roleText }}）。卡片数值均按此范围聚合；范围内无数据时显示 0，而不是「加载失败」。
        </span>
      </template>
    </el-alert>

    <!-- ============ 指标卡（每张卡独立加载，互不阻断） ============ -->
    <div class="grid g4 mb12">
      <MetricCard
        label="档案总数"
        :value="dash.talentTotal"
        unit=" 人"
        :sub="dash.talentSub"
        tone="default"
        :hint="true"
      />
      <MetricCard
        label="待复核字段"
        :value="dash.reviewPending"
        unit=" 项"
        :sub="dash.reviewSub"
        :tone="dash.reviewPending > 0 ? 'warn' : 'ok'"
      />
      <MetricCard
        label="待处理重复对"
        :value="dash.dedupPending"
        unit=" 组"
        :sub="dash.dedupSub"
        :tone="dash.dedupPending > 0 ? 'warn' : 'ok'"
      />
      <MetricCard
        label="人工介入待办"
        :value="dash.interruptPending"
        unit=" 项"
        :sub="dash.interruptSub"
        :tone="dash.interruptPending > 0 ? 'danger' : 'ok'"
      />
    </div>

    <!-- ============ 后端未就绪时的显式声明（kill-switch，默认关闭） ============ -->
    <!-- ⚠️ CU-29 已交付 API-30~35 / 64~66，故 `TALENT_MASTER_API_READY = true`、本块不渲染。
         保留它是为了让「端点被误删」这件事有一个**一行开关**的兜底：翻回 `false` 即可
         让三个页面同时显示「后端未就绪」，而不是让用户直面 404。 -->
    <el-alert
      v-if="!TALENT_MASTER_API_READY"
      type="warning"
      :closable="false"
      show-icon
      class="mb12"
    >
      <template #title>部分卡片依赖「档案主档」接口（API-30 ~ API-35），后端未就绪</template>
      <template #default>
        {{ TALENT_MASTER_API_NOTICE }}
      </template>
    </el-alert>

    <div class="grid g2">
      <!-- ============ 左：AI 成本与调用 ============ -->
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span class="hd"><i class="bar" />AI 调用与成本（近 7 天）</span>
            <el-link type="primary" :underline="false" @click="router.push('/ai/call-log')">
              查看明细 →
            </el-link>
          </div>
        </template>

        <DegradedBadge :degraded="cost.degraded" :reason="cost.reason" />

        <StatePanel
          :status="cost.status === 'ready' ? 'ready' : cost.status"
          :loading="cost.status === 'loading'"
          :code="cost.code"
          :message="cost.message"
          :trace-id="cost.traceId"
          :rows="4"
          empty-title="暂无调用记录"
          empty-hint="近 7 天内没有 AI 调用，或当前数据域内不可见"
          @retry="loadCost"
        >
          <div class="grid g3 mini">
            <div class="mini-box">
              <div class="k">调用次数</div>
              <div class="v num">{{ fmtNumber(cost.data?.total.calls ?? 0) }}</div>
            </div>
            <div class="mini-box">
              <div class="k">累计成本（元）</div>
              <!-- 🔴 cost_yuan 是字符串（Decimal 序列化）；直接渲染，不做浮点运算 -->
              <div class="v num">{{ cost.data?.total.cost_total_yuan ?? '0.00' }}</div>
            </div>
            <div class="mini-box">
              <div class="k">平均延迟</div>
              <div class="v num">{{ cost.data?.total.avg_latency_ms ?? 0 }}<small>ms</small></div>
            </div>
          </div>

          <div class="alert-row">
            <el-tag v-if="cost.data?.total.cost_alert" type="warning" size="small" effect="light">
              成本告警
            </el-tag>
            <el-tag v-if="cost.data?.total.mask_alert" type="danger" size="small" effect="light">
              脱敏率告警
            </el-tag>
            <span class="muted xs">
              脱敏率 {{ fmtRate(cost.data?.total.mask_applied_rate) }}（分母为调用时刻的
              <code class="mono">deployment</code>，非当前配置）
            </span>
          </div>

          <div v-if="cost.data?.by_scene?.length" class="scene-list">
            <div v-for="b in cost.data.by_scene.slice(0, 5)" :key="b.key" class="scene-row">
              <span class="ellipsis" :title="b.key">{{ b.label || b.key }}</span>
              <span class="num">{{ b.calls }} 次</span>
              <span class="num">{{ b.cost_yuan }} 元</span>
            </div>
          </div>
        </StatePanel>
      </el-card>

      <!-- ============ 右：人工待办 ============ -->
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span class="hd"><i class="bar warn" />人工介入待办</span>
            <el-link type="primary" :underline="false" @click="router.push('/ai/agent-task')">
              全部待办 →
            </el-link>
          </div>
        </template>

        <StatePanel
          :status="todo.status"
          :loading="todo.status === 'loading'"
          :code="todo.code"
          :message="todo.message"
          :trace-id="todo.traceId"
          :rows="4"
          empty-title="没有待处理的人工待办"
          empty-hint="所有 Agent 中断都已处理完毕"
          @retry="loadTodo"
        >
          <div v-if="todo.items.length === 0" class="muted sm">没有待处理的人工待办。</div>
          <div v-else class="todo-list">
            <div
              v-for="it in todo.items"
              :key="it.id"
              class="todo-item"
              @click="openTask(it.task_id)"
            >
              <div class="line1">
                <StatusTag :text="interruptType(it)" tone="primary" />
                <span class="muted xs">{{ it.node_name }}</span>
              </div>
              <div class="line2 ellipsis" :title="payloadText(it)">
                {{ payloadText(it) }}
              </div>
              <div class="line3">
                <span class="muted xs">指派：{{ it.assignee_role || '—' }}</span>
                <span class="muted xs">{{ fmtRelative(it.created_at) }}</span>
              </div>
            </div>
          </div>
        </StatePanel>
      </el-card>
    </div>

    <div class="grid g2 mt12">
      <!-- ============ 最近导入任务 ============ -->
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span class="hd"><i class="bar" />最近导入任务</span>
            <el-link type="primary" :underline="false" @click="router.push('/talent/import')">
              进入导入 →
            </el-link>
          </div>
        </template>

        <StatePanel
          :status="imports.status"
          :loading="imports.status === 'loading'"
          :code="imports.code"
          :message="imports.message"
          :trace-id="imports.traceId"
          :rows="4"
          empty-title="还没有导入任务"
          empty-hint="从「批量导入」上传简历即可创建任务"
          @retry="loadImports"
        >
          <el-table :data="imports.items" size="small" :show-header="true">
            <el-table-column label="任务号" min-width="150">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openImport(row.task_id)">
                  <span class="mono xs">{{ row.task_no }}</span>
                </el-link>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="importTone(row.status)" />
              </template>
            </el-table-column>
            <el-table-column label="进度" width="150">
              <template #default="{ row }">
                <el-progress :percentage="row.progress_pct" :stroke-width="6" :show-text="false" />
                <span class="muted xs">{{ row.success_count }}/{{ row.total_count }}</span>
              </template>
            </el-table-column>
            <el-table-column label="失败" width="70" align="right">
              <template #default="{ row }">
                <span :class="{ danger: row.fail_count > 0 }">{{ row.fail_count }}</span>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="130">
              <template #default="{ row }">
                <span class="muted nowrap xs">{{ fmtDateTime(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </StatePanel>
      </el-card>

      <!-- ============ 最近审计日志 ============ -->
      <el-card v-if="auth.hasPerm('audit:view')" shadow="never">
        <template #header>
          <div class="card-head">
            <span class="hd"><i class="bar" />最近操作留痕</span>
            <el-link type="primary" :underline="false" @click="router.push('/system/audit-log')">
              审计日志 →
            </el-link>
          </div>
        </template>

        <StatePanel
          :status="audit.status"
          :loading="audit.status === 'loading'"
          :code="audit.code"
          :message="audit.message"
          :trace-id="audit.traceId"
          :rows="4"
          empty-title="暂无操作记录"
          empty-hint="当前数据域内没有可查看的审计记录"
          @retry="loadAudit"
        >
          <div v-for="a in audit.items" :key="a.id" class="audit-row">
            <div class="line1">
              <StatusTag :text="a.action_name || a.action" tone="plain" />
              <span class="muted xs ellipsis" :title="a.target_name || ''">
                {{ a.target_name || a.target_type || '—' }}
              </span>
            </div>
            <div class="line3">
              <span class="muted xs">{{ a.username || '系统' }}</span>
              <span class="muted xs">{{ fmtDateTime(a.created_at) }}</span>
            </div>
          </div>
        </StatePanel>
      </el-card>

      <!-- 无审计权限时的占位（避免右侧空掉） -->
      <el-card v-else shadow="never">
        <template #header>
          <div class="card-head"><span class="hd"><i class="bar" />快捷入口</span></div>
        </template>
        <div class="quick">
          <el-button
            v-for="q in quickLinks"
            :key="q.path"
            plain
            style="justify-content: flex-start"
            @click="router.push(q.path)"
          >
            {{ q.label }}
          </el-button>
        </div>
        <div class="muted xs mt8">
          审计日志需要 <code class="mono">audit:view</code> 权限（ADM / AUDIT 角色）。
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * P-2 · 工作台首页（`/dashboard`）
 *
 * 🔴 四条设计口径：
 *   1. **每张卡片独立加载**（各自 `useAsyncData` 语义），一张卡失败**不拖垮整页** ——
 *      首页是「第一印象页」，任何一处接口 500 都不该让人看到满屏错误。
 *   2. **按数据域聚合，不做前端二次过滤** —— MGR 只看本部门、EMP 可能什么都看不到；
 *      此时显示 0 而不是「加载失败」（`40401` 走空态）。
 *   3. **金额按字符串渲染**（`cost_yuan` 是 `Decimal` 序列化）—— 直接显示，不做浮点累加。
 *   4. **降级不弹错**：`50301/50302` 由 `DegradedBadge` 承载，卡片数值照常渲染。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { agentApi, type AgentInterrupt } from '@/api/agent'
import { aiApi, type CostDashboard } from '@/api/ai'
import { auditApi, type AuditLog } from '@/api/audit'
import { ApiError } from '@/api/http'
import { importApi, type ImportTask } from '@/api/importer'
import { reviewApi, type ReviewDashboard } from '@/api/review'
import { dedupApi } from '@/api/dedup'
import {
  talentApi,
  TALENT_MASTER_API_NOTICE,
  TALENT_MASTER_API_READY,
} from '@/api/talent'
import { fmtDateTime, fmtNumber, fmtRate, fmtRelative } from '@/utils/format'
import DegradedBadge from '@/components/DegradedBadge.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const auth = useAuthStore()

const desc = '一眼看清「待处理的事」与「正在花的钱」。所有数值按当前数据域聚合。'

const roleText = computed(() =>
  auth.isAdmin ? 'ADM 管理员，全量可见' : auth.roles.join(' / ') || '—',
)

// ---------------- 通用「单卡状态」容器 ----------------
interface CardState<T> {
  status: 'idle' | 'loading' | 'ready' | 'forbidden' | 'notfound' | 'error'
  data: T | null
  code: number | null
  message: string | null
  traceId: string | null
  degraded: boolean
  reason: string | null
}

function newCard<T>(): CardState<T> {
  return {
    status: 'idle',
    data: null,
    code: null,
    message: null,
    traceId: null,
    degraded: false,
    reason: null,
  }
}

/** 把一次请求的结果写进卡片状态（成败都不抛，保证其它卡继续跑）。 */
async function fill<T>(card: CardState<T>, fn: () => Promise<unknown>): Promise<void> {
  card.status = 'loading'
  try {
    const res = (await fn()) as {
      data: T
      degraded?: boolean
      degradedReason?: string | null
      traceId?: string | null
    }
    card.data = res.data
    card.degraded = res.degraded ?? false
    card.reason = res.degradedReason ?? null
    card.traceId = res.traceId ?? null
    card.status = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    card.code = err.code
    card.message = err.displayMessage
    card.traceId = err.traceId
    card.status = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  }
}

// ---------------- 顶部四张指标卡 ----------------
const reviewState = ref<CardState<ReviewDashboard>>(newCard<ReviewDashboard>())
const dedupState = ref<{ count: number | null; sub: string }>({ count: null, sub: '加载中…' })
const interruptState = ref<{ count: number | null; sub: string }>({ count: null, sub: '加载中…' })
const importState = ref<{ total: number | null; sub: string }>({ total: null, sub: '加载中…' })
/**
 * 档案总数（CU-29 点亮）。
 *
 * 🔴 只用「取 1 条拿 `page.total`」的**计数查询**，不拉列表：
 * `per_page=1` 让数据库走 `COUNT`-友好路径，不把整页档案搬进内存。
 * 权限由**后端数据域**决定（MGR 只见本部门、EMP 走 `SELF`），前端**不做二次过滤** ——
 * 与 §1.5 铁律 4「前端传参只能收窄」一致：这里连收窄参数都不传。
 */
const talentState = ref<{ total: number | null; sub: string }>({ total: null, sub: '加载中…' })

const dash = computed(() => ({
  // 🔴 读取中 / 无权限时用「—」占位而不是 0（0 会被当成「真的一个人都没有」）
  talentTotal: talentState.value.total ?? '—',
  talentSub: talentState.value.sub,
  reviewPending: reviewState.value.data?.pending_total ?? 0,
  reviewSub: reviewState.value.data
    ? `已确认 ${reviewState.value.data.confirmed_total} · 已修正 ${reviewState.value.data.corrected_total}`
    : '加载中…',
  dedupPending: dedupState.value.count ?? 0,
  dedupSub: dedupState.value.sub,
  interruptPending: interruptState.value.count ?? 0,
  interruptSub: interruptState.value.sub,
}))

async function loadReview(): Promise<void> {
  await fill(reviewState.value, () => reviewApi.dashboard())
}

async function loadDedupCount(): Promise<void> {
  try {
    const res = await dedupApi.list({ status: 0, page: 1, per_page: 1 })
    dedupState.value = { count: res.page?.total ?? 0, sub: '待处理候选对' }
  } catch (e) {
    if (e instanceof ApiError && (e.isForbidden || e.isNotFound)) {
      dedupState.value = { count: 0, sub: '当前角色不可见' }
    } else {
      dedupState.value = { count: 0, sub: '读取失败' }
    }
  }
}

async function loadInterruptCount(): Promise<void> {
  try {
    const res = await agentApi.interrupts({ status: 0, page: 1, per_page: 1 })
    interruptState.value = { count: res.page?.total ?? 0, sub: '等待人工决策' }
  } catch (e) {
    if (e instanceof ApiError && (e.isForbidden || e.isNotFound)) {
      interruptState.value = { count: 0, sub: '当前角色不可见' }
    } else {
      interruptState.value = { count: 0, sub: '读取失败' }
    }
  }
}

async function loadImportCount(): Promise<void> {
  try {
    const res = await importApi.list({ page: 1, per_page: 1 })
    importState.value = { total: res.page?.total ?? 0, sub: '个导入任务' }
  } catch {
    importState.value = { total: 0, sub: '读取失败' }
  }
}

/**
 * 档案总数（API-30）。
 *
 * 与上面几张卡同一个三种结局的写法，但**多一个分支要考虑**：
 * - `40301`：越权（EM 的 `SELF` 范围、`STATS_ONLY` 角色）→ 显示 0 + 「当前角色不可见」，
 *   ❌ 不显示「读取失败」（那是把**权限设计**误报成**系统故障**）；
 * - 其他异常：`50301/50302` 降级时后端仍返回 200 + `data=[]`，故这里只可能是真故障；
 * - ⚠️ `total` 缺失（后端不返回 `page`）时用 `null` 而不是 0 —— 让卡片显示「—」，
 *   与「确实是 0 个人」区分开。
 */
async function loadTalentCount(): Promise<void> {
  try {
    const res = await talentApi.list({ page: 1, per_page: 1 })
    const total = res.page?.total
    talentState.value =
      total === undefined
        ? { total: null, sub: '未返回总数' }
        : { total, sub: '按当前数据域' }
  } catch (e) {
    if (e instanceof ApiError && (e.isForbidden || e.isNotFound)) {
      talentState.value = { total: 0, sub: '当前角色不可见' }
    } else {
      talentState.value = { total: null, sub: '读取失败' }
    }
  }
}

// ---------------- 成本卡 ----------------
const cost = ref<CardState<CostDashboard>>(newCard<CostDashboard>())
async function loadCost(): Promise<void> {
  await fill(cost.value, () => aiApi.costDashboard({}))
}

// ---------------- 待办卡 ----------------
const todo = ref<{
  status: CardState<AgentInterrupt[]>['status']
  items: AgentInterrupt[]
  code: number | null
  message: string | null
  traceId: string | null
}>({ status: 'idle', items: [], code: null, message: null, traceId: null })

async function loadTodo(): Promise<void> {
  todo.value.status = 'loading'
  try {
    const res = await agentApi.interrupts({ status: 0, page: 1, per_page: 5 })
    todo.value.items = res.data ?? []
    todo.value.traceId = res.traceId
    todo.value.status = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    todo.value.items = []
    todo.value.code = err.code
    todo.value.message = err.displayMessage
    todo.value.traceId = err.traceId
    todo.value.status = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  }
}

function interruptType(it: AgentInterrupt): string {
  return it.interrupt_type_name || it.interrupt_type
}

function payloadText(it: AgentInterrupt): string {
  const p = it.payload
  if (!p) return '（无负载说明）'
  const desc = (p as Record<string, unknown>).description
  if (typeof desc === 'string' && desc) return desc
  try {
    return JSON.stringify(p)
  } catch {
    return '（负载无法序列化）'
  }
}

function openTask(taskId: string): void {
  void router.push({ name: 'ai-agent-task', query: { task_id: taskId } })
}

// ---------------- 导入任务卡 ----------------
const imports = ref<{
  status: CardState<ImportTask[]>['status']
  items: ImportTask[]
  code: number | null
  message: string | null
  traceId: string | null
}>({ status: 'idle', items: [], code: null, message: null, traceId: null })

async function loadImports(): Promise<void> {
  imports.value.status = 'loading'
  try {
    const res = await importApi.list({ page: 1, per_page: 5 })
    imports.value.items = res.data ?? []
    imports.value.traceId = res.traceId
    imports.value.status = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    imports.value.items = []
    imports.value.code = err.code
    imports.value.message = err.displayMessage
    imports.value.traceId = err.traceId
    imports.value.status = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  }
}

/** 导入任务状态色调：`2 已完成` 绿、`3 部分失败` 黄、`4 失败` 红、进行中主色。 */
function importTone(code: number): 'success' | 'warning' | 'danger' | 'primary' | 'plain' {
  if (code === 2) return 'success'
  if (code === 3) return 'warning'
  if (code === 4) return 'danger'
  if (code === 0 || code === 1) return 'primary'
  return 'plain'
}

function openImport(taskId: string): void {
  void router.push({ name: 'talent-import', query: { task_id: taskId } })
}

// ---------------- 审计卡 ----------------
const audit = ref<{
  status: CardState<AuditLog[]>['status']
  items: AuditLog[]
  code: number | null
  message: string | null
  traceId: string | null
}>({ status: 'idle', items: [], code: null, message: null, traceId: null })

async function loadAudit(): Promise<void> {
  audit.value.status = 'loading'
  try {
    const res = await auditApi.list({ page: 1, per_page: 6 })
    audit.value.items = res.data ?? []
    audit.value.traceId = res.traceId
    audit.value.status = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    audit.value.items = []
    audit.value.code = err.code
    audit.value.message = err.displayMessage
    audit.value.traceId = err.traceId
    audit.value.status = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  }
}

const quickLinks = computed(() => {
  const list = [
    { path: '/talent/list', label: '人才档案列表' },
    { path: '/talent/search', label: '语义人才检索' },
    { path: '/talent/ask', label: '档案 RAG 问答' },
    { path: '/talent/change-log', label: '档案变更历史' },
  ]
  return list.filter((l) => {
    if (l.path === '/talent/list' || l.path === '/talent/search') return auth.hasRole(['ADM', 'HR', 'MGR'])
    if (l.path === '/talent/change-log') return auth.hasRole(['ADM', 'HR', 'AUDIT'])
    return true
  })
})

/**
 * 🔴 并行发起、逐卡兜底：用 `Promise.allSettled` 而非 `Promise.all` ——
 *    后者会在任一失败时短路，导致其余卡片永远停在「加载中」。
 */
onMounted(async () => {
  await Promise.allSettled([
    loadReview(),
    loadDedupCount(),
    loadInterruptCount(),
    loadImportCount(),
    loadTalentCount(),
    loadCost(),
    loadTodo(),
    loadImports(),
    auth.hasPerm('audit:view') ? loadAudit() : Promise.resolve(),
  ])
})
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.card-head .hd {
  font-size: 13.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-head .bar {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: var(--primary);
}
.card-head .bar.warn {
  background: var(--warn);
}
.mini {
  margin-bottom: 10px;
}
.mini-box {
  background: var(--panel-2, #fafbfd);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 9px 11px;
}
.mini-box .k {
  font-size: 11.5px;
  color: var(--txt-3);
}
.mini-box .v {
  font-size: 18px;
  font-weight: 600;
  margin-top: 3px;
}
.mini-box .v small {
  font-size: 11px;
  color: var(--txt-3);
  margin-left: 2px;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.scene-list {
  border-top: 1px dashed var(--line);
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.scene-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 14px;
  font-size: 12.5px;
  align-items: center;
}
.todo-list,
.audit-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.todo-item {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 9px 11px;
  cursor: pointer;
  transition: border-color 0.15s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.todo-item:hover {
  border-color: var(--primary);
}
.audit-row {
  padding: 7px 0;
  border-bottom: 1px dashed var(--line);
}
.audit-row:last-child {
  border-bottom: none;
}
.line1 {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.line2 {
  font-size: 12.5px;
  color: var(--txt-2);
}
.line3 {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.quick {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.quick :deep(.el-button) {
  width: 100%;
  margin-left: 0;
}
.nowrap {
  white-space: nowrap;
}
.danger {
  color: var(--danger);
}
.mb8 {
  margin-bottom: 8px;
}
.mt8 {
  margin-top: 8px;
}
.mt12 {
  margin-top: 12px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
