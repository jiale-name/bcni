<template>
  <div>
    <PageHeader title="Agent 任务与人工待办" :desc="desc">
      <template #actions>
        <el-button :disabled="taskLoading || intLoading" @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 幂等 vs 状态冲突的顺序——这条决定了「该继续还是该刷新」 -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        处理人工待办时，后端<b>先判幂等（<code class="mono">40903</code>）再判状态（<code class="mono">40902</code>）</b>。
        因此两种情况的前端动作<b>完全相反</b>：
        <code class="mono">40903</code> = 你自己刚才那次已生效 → <b>继续</b>；
        <code class="mono">40902</code> = 别人已抢先处理 → <b>刷新后重试</b>。
      </template>
    </el-alert>

    <el-tabs v-model="tab">
      <!-- ==================== Agent 任务 ==================== -->
      <el-tab-pane label="Agent 任务" name="tasks">
        <el-card shadow="never" class="mb12">
          <div class="toolbar">
            <el-input v-model="taskTypeFilter" placeholder="任务类型 code" clearable style="width: 170px" />
            <el-select v-model="taskStatusFilter" clearable placeholder="状态不限" style="width: 170px">
              <el-option
                v-for="o in AGENT_TASK_STATUS"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
            <el-input v-model="bizIdFilter" placeholder="业务对象 ID" clearable style="width: 180px" />
            <el-button type="primary" @click="loadTasks">查询</el-button>
          </div>
          <div class="muted xs mt8">
            ⚠️ 后端两个 Agent 状态枚举的 <code class="mono">*_name</code> 目前是<b>英文</b>（已登记遗留），
            本页在它为英文时用<b>前端中文兜底</b>显示；后端一旦中文化会自动优先采用后端值。
          </div>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span class="hd">
                <i class="bar" />任务列表
                <span class="sub">共 {{ taskTotal }} 个</span>
              </span>
            </div>
          </template>

          <StatePanel
            :status="taskStatus === 'ready' && tasks.length === 0 ? 'empty' : taskStatus"
            :loading="taskLoading"
            :code="taskErrCode"
            :message="taskErrMsg"
            :trace-id="taskTraceId"
            :rows="6"
            empty-title="没有 Agent 任务"
            empty-hint="当前筛选条件下没有任务记录"
            @retry="loadTasks"
          >
            <el-table :data="tasks" v-loading="taskLoading" stripe row-key="id">
              <el-table-column label="任务 ID" width="150">
                <template #default="{ row }">
                  <el-link type="primary" :underline="false" @click="openDetail(row.id)">
                    <span class="mono xs">{{ shortId(row.id) }}</span>
                  </el-link>
                </template>
              </el-table-column>

              <el-table-column label="类型" width="150">
                <template #default="{ row }">
                  {{ row.task_type_name || row.task_type }}
                  <div class="muted xs mono">{{ row.task_type }}</div>
                </template>
              </el-table-column>

              <el-table-column label="业务对象" width="150">
                <template #default="{ row }">
                  <span v-if="row.biz_id" class="mono xs">{{ shortId(row.biz_id) }}</span>
                  <span v-else class="muted xs">—</span>
                </template>
              </el-table-column>

              <el-table-column label="状态" width="150">
                <template #default="{ row }">
                  <StatusTag
                    :text="statusText(row.status, row.status_name, AGENT_TASK_STATUS)"
                    :tone="taskTone(row.status)"
                  />
                </template>
              </el-table-column>

              <el-table-column label="当前节点" min-width="150">
                <template #default="{ row }">
                  <span class="mono xs">{{ dash(row.current_node) }}</span>
                </template>
              </el-table-column>

              <el-table-column label="重试" width="80" align="right">
                <template #default="{ row }">
                  <span :class="{ danger: row.retry_count > 0 }">{{ row.retry_count }}</span>
                </template>
              </el-table-column>

              <el-table-column label="更新时间" width="150">
                <template #default="{ row }">
                  <span class="muted xs">{{ fmtDateTime(row.updated_at) }}</span>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <div class="ops">
                    <a @click="openDetail(row.id)">详情 / 回放</a>
                    <a v-if="row.status === 4" @click="onRetry(row)">重试</a>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <div class="pager">
              <el-pagination
                background
                layout="total, sizes, prev, pager, next, jumper"
                :total="taskTotal"
                :current-page="taskPage"
                :page-size="taskPerPage"
                :page-sizes="[20, 50, 100]"
                @current-change="onTaskPageChange"
                @size-change="onTaskSizeChange"
              />
            </div>
            <TraceFooter :trace-id="taskTraceId" />
          </StatePanel>
        </el-card>
      </el-tab-pane>

      <!-- ==================== 人工待办 ==================== -->
      <el-tab-pane label="人工介入待办" name="interrupts">
        <el-card shadow="never" class="mb12">
          <div class="toolbar">
            <el-select v-model="intStatusFilter" clearable placeholder="状态不限" style="width: 150px">
              <el-option
                v-for="o in AGENT_INTERRUPT_STATUS"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
            <el-input v-model="intTypeFilter" placeholder="待办类型 code" clearable style="width: 180px" />
            <el-input v-model="intRoleFilter" placeholder="指派角色" clearable style="width: 150px" />
            <el-button type="primary" @click="loadInterrupts">查询</el-button>
          </div>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span class="hd">
                <i class="bar warn" />待办列表
                <span class="sub">共 {{ intTotal }} 项</span>
              </span>
              <span class="muted xs">同一待办重复提交返回首次结果（40903）；不同人并发提交后者 40902</span>
            </div>
          </template>

          <StatePanel
            :status="intStatus === 'ready' && interrupts.length === 0 ? 'empty' : intStatus"
            :loading="intLoading"
            :code="intErrCode"
            :message="intErrMsg"
            :trace-id="intTraceId"
            :rows="6"
            empty-title="没有人工待办"
            empty-hint="当前筛选条件下没有需要人工介入的事项"
            @retry="loadInterrupts"
          >
            <div class="todo-list">
              <div v-for="it in interrupts" :key="it.id" class="todo">
                <div class="t-head">
                  <StatusTag
                    :text="it.interrupt_type_name || it.interrupt_type"
                    tone="primary"
                  />
                  <span class="muted xs mono">{{ it.node_name }}</span>
                  <StatusTag
                    :text="statusText(it.status, it.status_name, AGENT_INTERRUPT_STATUS)"
                    :tone="interruptTone(it.status)"
                  />
                  <span v-if="it.assignee_role" class="muted xs">指派：{{ it.assignee_role }}</span>
                  <span class="muted xs">
                    任务 <span class="mono">{{ shortId(it.task_id) }}</span>
                  </span>
                  <span class="muted xs push-right">{{ fmtDateTime(it.created_at) }}</span>
                </div>

                <div class="t-body">
                  <JsonViewer
                    :value="it.payload"
                    label="待办负载"
                    :collapsible="true"
                    :default-open="false"
                  />
                </div>

                <!-- 已处理：展示处理人与意见 -->
                <div v-if="it.status !== 0" class="t-decided">
                  <span class="muted xs">
                    由 {{ it.decided_by_name || '—' }} 于 {{ fmtDateTime(it.decided_at) }} 处理
                  </span>
                  <span v-if="it.comment" class="xs">｜意见：{{ it.comment }}</span>
                </div>

                <!-- 待处理：给出决策入口 -->
                <div v-else class="t-actions">
                  <el-button
                    v-perm="'agent:interrupt:resolve'"
                    size="small"
                    type="primary"
                    :loading="resolving === it.id"
                    @click="openResolve(it, 'approved')"
                  >
                    通过
                  </el-button>
                  <el-button
                    v-perm="'agent:interrupt:resolve'"
                    size="small"
                    type="danger"
                    plain
                    :loading="resolving === it.id"
                    @click="openResolve(it, 'rejected')"
                  >
                    驳回
                  </el-button>
                  <el-button size="small" text @click="openTaskFromInterrupt(it.task_id)">
                    查看任务
                  </el-button>
                </div>
              </div>
            </div>

            <div class="pager">
              <el-pagination
                background
                layout="total, sizes, prev, pager, next, jumper"
                :total="intTotal"
                :current-page="intPage"
                :page-size="intPerPage"
                :page-sizes="[20, 50, 100]"
                @current-change="onIntPageChange"
                @size-change="onIntSizeChange"
              />
            </div>
            <TraceFooter :trace-id="intTraceId" />
          </StatePanel>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- ============ 任务详情 + 步骤回放 ============ -->
    <el-drawer v-model="detailVisible" title="任务详情与步骤回放" size="60%">
      <div v-if="detail" class="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务 ID">
            <span class="mono xs">{{ detail.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="类型">
            {{ detail.task_type_name || detail.task_type }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <StatusTag
              :text="statusText(detail.status, detail.status_name, AGENT_TASK_STATUS)"
              :tone="taskTone(detail.status)"
            />
          </el-descriptions-item>
          <el-descriptions-item label="当前节点">
            <span class="mono xs">{{ dash(detail.current_node) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="业务对象">
            <span class="mono xs">{{ detail.biz_id || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="重试次数">{{ detail.retry_count }}</el-descriptions-item>
          <el-descriptions-item label="幂等键">
            <span class="mono xs">{{ detail.idempotent_key || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="步骤数">{{ detail.step_count }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ fmtDateTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ fmtDateTime(detail.updated_at) }}</el-descriptions-item>
        </el-descriptions>

        <!-- 🔴 graph_state 是内部图状态，仅 AIOPS/ADM 排查用 → 折叠，不作为业务字段 -->
        <div class="mt12">
          <JsonViewer
            :value="detail.graph_state"
            label="图状态快照 graph_state（内部状态，仅排查用）"
            :default-open="false"
          />
        </div>

        <div class="mt12">
          <div class="card-head mb8">
            <span class="hd"><i class="bar" />步骤回放</span>
            <span class="muted xs">
              按 <code class="mono">step_index</code>（<b>全任务递增序号</b>）排序，即真实执行顺序
            </span>
          </div>

          <StatePanel
            :status="stepStatus === 'ready' && steps.length === 0 ? 'empty' : stepStatus"
            :loading="stepLoading"
            :code="stepErrCode"
            :message="stepErrMsg"
            :rows="4"
            empty-title="没有步骤记录"
            empty-hint="该任务尚未执行到任何节点"
            @retry="loadSteps"
          >
            <el-timeline>
              <el-timeline-item
                v-for="s in sortedSteps"
                :key="s.id"
                :timestamp="`#${s.step_index} · ${fmtDateTime(s.created_at)}`"
                placement="top"
                :type="s.status === 3 ? 'success' : s.status === 4 ? 'danger' : 'primary'"
              >
                <div class="step">
                  <div class="s-head">
                    <b class="mono">{{ s.node_name }}</b>
                    <StatusTag
                      :text="statusText(s.status, s.status_name, AGENT_TASK_STATUS)"
                      :tone="s.status === 3 ? 'success' : s.status === 4 ? 'danger' : 'primary'"
                    />
                    <span v-if="s.latency_ms !== null" class="muted xs num">{{ s.latency_ms }}ms</span>
                  </div>
                  <div v-if="s.error_msg" class="danger xs mb6">{{ s.error_msg }}</div>
                  <div class="s-io">
                    <JsonViewer :value="s.input" label="输入" :default-open="false" />
                    <JsonViewer :value="s.output" label="输出" :default-open="false" />
                  </div>
                </div>
              </el-timeline-item>
            </el-timeline>
          </StatePanel>

          <div v-if="detail.status === 4" class="mt12">
            <el-button type="warning" :loading="retrying" @click="onRetryById(detail.id)">
              <el-icon><RefreshRight /></el-icon>重试该任务
            </el-button>
            <span class="muted xs" style="margin-left: 10px">
              重试需 <code class="mono">agent:task:retry</code> 权限（ADM / AIOPS）
            </span>
          </div>
        </div>
      </div>
      <div v-else class="muted sm">加载中…</div>
    </el-drawer>

    <!-- ============ 处理人工待办 ============ -->
    <el-dialog v-model="resolveVisible" title="处理人工待办" width="560px">
      <div v-if="resolvingItem" class="form">
        <div class="row">
          <span class="k">待办类型</span>
          <span>{{ resolvingItem.interrupt_type_name || resolvingItem.interrupt_type }}</span>
        </div>
        <div class="row">
          <span class="k">节点</span>
          <span class="mono xs">{{ resolvingItem.node_name }}</span>
        </div>
        <div class="row">
          <span class="k">决策</span>
          <StatusTag
            :text="decision === 'approved' ? '通过' : '驳回'"
            :tone="decision === 'approved' ? 'success' : 'danger'"
          />
        </div>
        <div class="row">
          <span class="k">意见</span>
          <el-input
            v-model="comment"
            type="textarea"
            :rows="3"
            placeholder="处理意见（可附在待办记录中）"
          />
        </div>
        <el-alert type="info" :closable="false" show-icon>
          <template #title>
            提交后任务将<b>恢复执行</b>；同一待办重复提交返回首次结果（<code class="mono">40903</code>，按成功处理），
            若已被他人处理则返回 <code class="mono">40902</code>（需刷新列表）。
          </template>
        </el-alert>
      </div>
      <template #footer>
        <el-button @click="resolveVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitResolve">确认提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-19 · Agent 任务与人工介入待办（`/ai/agent-task`，F-A0.1 ~ F-A0.5，**API-90 ~ API-95**）
 *
 * 🔴 五条契约（`api/agent.ts` 已钉死）：
 *   ① **幂等判定先于状态判定** —— API-94 顺序是「先查 `Idempotency-Key`（`40903`）
 *      再判状态（`40902`）」。两者前端动作**完全相反**：
 *      `40903` → 按成功，提示「已生效」；`40902` → 提示「已被 {handled_by_name} 处理」并**刷新列表**。
 *   ② **`step_index` 是「全任务递增序号」**（跨 run 连续）—— 按它排序即真实执行顺序。
 *   ③ **`next_node` 首次为 `null`、回放才有值** —— 未恢复执行时不据它推断「下一步是空」。
 *   ④ **同一 `interrupt_id` 重复提交返回首次结果（`40903`）**；
 *      **不同人并发提交** → 后者 `40902`（回带 `handled_by_name` / `handled_at`）。
 *   ⑤ **`graph_state` 是内部图状态快照**（仅 AIOPS/ADM 排查用）—— 折叠展示，不作为业务字段。
 */
import { computed, onMounted, ref } from 'vue'

import {
  agentApi,
  type AgentInterrupt,
  type AgentStep,
  type AgentTask,
  type AgentTaskDetail,
} from '@/api/agent'
import { AGENT_INTERRUPT_STATUS, AGENT_TASK_STATUS, statusText } from '@/api/enums'
import { ApiError } from '@/api/http'
import { dash } from '@/api/types'
import { fmtDateTime } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import JsonViewer from '@/components/JsonViewer.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc =
  'Agent 任务的执行状态与步骤回放，以及需要人工决策的中断待办。'

const tab = ref('tasks')

function shortId(id: string | null): string {
  if (!id) return '—'
  return id.length > 12 ? `…${id.slice(-10)}` : id
}

function taskTone(code: number): 'success' | 'danger' | 'warning' | 'primary' | 'plain' {
  if (code === 3) return 'success'
  if (code === 4) return 'danger'
  if (code === 2) return 'warning'
  if (code === 1) return 'primary'
  return 'plain'
}

function interruptTone(code: number): 'success' | 'danger' | 'warning' {
  if (code === 1) return 'success'
  if (code === 2) return 'danger'
  return 'warning'
}

// ---------------- 任务列表（API-90） ----------------
const taskLoading = ref(false)
const taskStatus = ref<LoadStatus>('idle')
const tasks = ref<AgentTask[]>([])
const taskTotal = ref(0)
const taskPage = ref(1)
const taskPerPage = ref(20)
const taskTraceId = ref<string | null>(null)
const taskErrCode = ref<number | null>(null)
const taskErrMsg = ref<string | null>(null)

const taskTypeFilter = ref('')
const taskStatusFilter = ref<number | null>(null)
const bizIdFilter = ref('')

async function loadTasks(): Promise<void> {
  taskLoading.value = true
  taskStatus.value = 'loading'
  taskErrCode.value = null
  taskErrMsg.value = null
  try {
    const res = await agentApi.tasks({
      page: taskPage.value,
      per_page: taskPerPage.value,
      task_type: taskTypeFilter.value.trim() || undefined,
      status: taskStatusFilter.value ?? undefined,
      biz_id: bizIdFilter.value.trim() || undefined,
    })
    tasks.value = res.data ?? []
    taskTotal.value = res.page?.total ?? tasks.value.length
    if (res.page?.per_page) taskPerPage.value = res.page.per_page
    taskTraceId.value = res.traceId
    taskStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    tasks.value = []
    taskTotal.value = 0
    taskErrCode.value = err.code
    taskErrMsg.value = err.displayMessage
    taskTraceId.value = err.traceId
    taskStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    taskLoading.value = false
  }
}

function onTaskPageChange(p: number): void {
  taskPage.value = p
  void loadTasks()
}
function onTaskSizeChange(s: number): void {
  taskPerPage.value = s
  taskPage.value = 1
  void loadTasks()
}

// ---------------- 待办列表（API-93） ----------------
const intLoading = ref(false)
const intStatus = ref<LoadStatus>('idle')
const interrupts = ref<AgentInterrupt[]>([])
const intTotal = ref(0)
const intPage = ref(1)
const intPerPage = ref(20)
const intTraceId = ref<string | null>(null)
const intErrCode = ref<number | null>(null)
const intErrMsg = ref<string | null>(null)

const intStatusFilter = ref<number | null>(null)
const intTypeFilter = ref('')
const intRoleFilter = ref('')

async function loadInterrupts(): Promise<void> {
  intLoading.value = true
  intStatus.value = 'loading'
  intErrCode.value = null
  intErrMsg.value = null
  try {
    const res = await agentApi.interrupts({
      page: intPage.value,
      per_page: intPerPage.value,
      status: intStatusFilter.value ?? undefined,
      interrupt_type: intTypeFilter.value.trim() || undefined,
      assignee_role: intRoleFilter.value.trim() || undefined,
    })
    interrupts.value = res.data ?? []
    intTotal.value = res.page?.total ?? interrupts.value.length
    if (res.page?.per_page) intPerPage.value = res.page.per_page
    intTraceId.value = res.traceId
    intStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    interrupts.value = []
    intTotal.value = 0
    intErrCode.value = err.code
    intErrMsg.value = err.displayMessage
    intTraceId.value = err.traceId
    intStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    intLoading.value = false
  }
}

function onIntPageChange(p: number): void {
  intPage.value = p
  void loadInterrupts()
}
function onIntSizeChange(s: number): void {
  intPerPage.value = s
  intPage.value = 1
  void loadInterrupts()
}

function openTaskFromInterrupt(taskId: string): void {
  tab.value = 'tasks'
  void openDetail(taskId)
}

// ---------------- 任务详情 + 步骤回放（API-91 / API-92） ----------------
const detailVisible = ref(false)
const detail = ref<AgentTaskDetail | null>(null)
const steps = ref<AgentStep[]>([])
const stepLoading = ref(false)
const stepStatus = ref<LoadStatus>('idle')
const stepErrCode = ref<number | null>(null)
const stepErrMsg = ref<string | null>(null)

/** 🔴 按 `step_index`（全任务递增序号）排序 —— 不排序会出现「入库排在 OCR 前面」。 */
const sortedSteps = computed(() => [...steps.value].sort((a, b) => a.step_index - b.step_index))

async function openDetail(taskId: string): Promise<void> {
  detailVisible.value = true
  detail.value = null
  steps.value = []
  stepStatus.value = 'idle'
  try {
    const res = await agentApi.taskDetail(taskId)
    detail.value = res.data
  } catch (e) {
    detailVisible.value = false
    notify.fail(e, '读取任务详情失败')
    return
  }
  await loadSteps(taskId)
}

async function loadSteps(taskIdArg?: string): Promise<void> {
  const taskId = taskIdArg ?? detail.value?.id
  if (!taskId) return
  stepLoading.value = true
  stepStatus.value = 'loading'
  stepErrCode.value = null
  stepErrMsg.value = null
  try {
    const res = await agentApi.steps(taskId, { page: 1, per_page: 100 })
    steps.value = res.data ?? []
    stepStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    steps.value = []
    stepErrCode.value = err.code
    stepErrMsg.value = err.displayMessage
    stepStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    stepLoading.value = false
  }
}

// ---------------- 重试（API-95） ----------------
const retrying = ref(false)

async function onRetry(row: AgentTask): Promise<void> {
  const okToGo = await notify.confirm(`重试任务 ${shortId(row.id)}（当前重试 ${row.retry_count} 次）？`)
  if (!okToGo) return
  await doRetry(row.id)
}

async function onRetryById(taskId: string): Promise<void> {
  const okToGo = await notify.confirm('重试该失败任务？')
  if (!okToGo) return
  await doRetry(taskId)
}

async function doRetry(taskId: string): Promise<void> {
  retrying.value = true
  try {
    const res = await agentApi.retryTask(taskId)
    const out = res.data
    notify.ok(
      `已提交重试（第 ${out?.retry_count ?? '—'} 次）` +
        (out?.message ? `｜${out.message}` : '') +
        (out?.resumed ? '｜任务已恢复执行' : ''),
    )
    await loadTasks()
    if (detail.value?.id === taskId) await openDetail(taskId)
  } catch (e) {
    notify.fail(e, '重试失败')
  } finally {
    retrying.value = false
  }
}

// ---------------- 处理待办（API-94） ----------------
const resolveVisible = ref(false)
const resolvingItem = ref<AgentInterrupt | null>(null)
const resolving = ref<string | null>(null)
const decision = ref<'approved' | 'rejected'>('approved')
const comment = ref('')
const submitting = ref(false)

function openResolve(it: AgentInterrupt, d: 'approved' | 'rejected'): void {
  resolvingItem.value = it
  decision.value = d
  comment.value = ''
  resolveVisible.value = true
}

async function submitResolve(): Promise<void> {
  const it = resolvingItem.value
  if (!it) return
  submitting.value = true
  resolving.value = it.id
  try {
    const res = await agentApi.resolveInterrupt(it.id, {
      decision: decision.value,
      comment: comment.value.trim() || null,
    })
    const out = res.data
    notify.ok(
      `${decision.value === 'approved' ? '已通过' : '已驳回'}；` +
        (out?.resumed ? '任务已恢复执行' : '任务未恢复') +
        // 🔴 口径 ③：`next_node` 首次为 null，不要据此推断「下一步是空」
        (out?.next_node ? `，下一节点：${out.next_node}` : ''),
    )
    resolveVisible.value = false
    await loadInterrupts()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40903) {
      // 🔴 幂等回放 = **你自己刚才那次已生效** → 按成功继续
      notify.ok('该待办已由你此前的提交生效（幂等回放 40903），无需重复操作。')
      resolveVisible.value = false
      await loadInterrupts()
    } else if (e instanceof ApiError && e.code === 40902) {
      // 🔴 被别人抢先处理 → 提示处理人 + **刷新列表**
      const d = e.data as { handled_by_name?: string; handled_at?: string } | null
      notify.warn(
        `该待办已被 ${d?.handled_by_name || '他人'} 于 ${fmtDateTime(d?.handled_at)} 处理，已为你刷新列表，请查看最新状态。`,
      )
      resolveVisible.value = false
      await Promise.allSettled([loadInterrupts(), loadTasks()])
    } else {
      notify.fail(e, '处理失败')
    }
  } finally {
    submitting.value = false
    resolving.value = null
  }
}

async function loadAll(): Promise<void> {
  await Promise.allSettled([loadTasks(), loadInterrupts()])
}

onMounted(loadAll)
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
.card-head .sub {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--txt-3);
}
.toolbar {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.ops {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
}
.ops a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover {
  text-decoration: underline;
}
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.todo {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 11px 13px;
  background: var(--panel);
}
.t-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}
.push-right {
  margin-left: auto;
}
.t-body {
  margin-bottom: 8px;
}
.t-decided {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px dashed var(--line);
  padding-top: 8px;
}
.t-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  border-top: 1px dashed var(--line);
  padding-top: 9px;
}
.detail {
  padding-bottom: 20px;
}
.step {
  padding-bottom: 4px;
}
.s-head {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.s-io {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
@media (max-width: 900px) {
  .s-io {
    grid-template-columns: 1fr;
  }
}
.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form .row {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 10px;
  align-items: center;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.danger {
  color: var(--danger);
}
.mt8 {
  margin-top: 8px;
}
.mt12 {
  margin-top: 12px;
}
.mb6 {
  margin-bottom: 6px;
}
.mb8 {
  margin-bottom: 8px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
