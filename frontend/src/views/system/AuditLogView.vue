<template>
  <div>
    <PageHeader title="审计日志" :desc="desc">
      <template #actions>
        <el-button v-perm="'audit:export'" :loading="exporting" @click="openExport">
          <el-icon><Download /></el-icon>导出 CSV
        </el-button>
        <el-button :disabled="table.loading.value" @click="table.load()">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置（导出是最容易被误解的一环） -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        导出<b>超过上限会报 <code class="mono">40002</code>，不是静默截断</b> ——
        <code class="mono">max_rows</code> 是<b>防爆上限</b>，不是「最多给我这么多条」的分页参数。
        遇到 40002 请<b>缩小时间窗</b>后重试。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        导出返回的是 <code class="mono">CommonResp</code> 内嵌的 <b><code class="mono">content</code>
        字符串（CSV）</b>，<b>不是二进制流</b>。CSV 已由后端做<b>公式注入防护</b>
        （<code class="mono">= / + / - / @</code> 开头会被转义）——本页<b>不再二次加工</b>内容，
        否则会把转义前缀去掉、把防护绕回来。
      </template>
    </el-alert>

    <!-- ============ 筛选 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input
          v-model="keywordInput"
          placeholder="关键字（目标名 / trace_id 等）"
          clearable
          style="width: 220px"
          @keyup.enter="reload"
        />
        <el-input v-model="actionInput" placeholder="动作 code，如 LOGIN" clearable style="width: 180px" />
        <el-input v-model="targetTypeInput" placeholder="目标类型，如 TALENT" clearable style="width: 190px" />
        <el-input v-model="targetIdInput" placeholder="目标 ID（雪花 ID 字符串）" clearable style="width: 210px" />
        <el-date-picker
          v-model="range"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 360px"
        />
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </el-card>

    <!-- ============ 列表 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />操作留痕
            <span class="sub">共 {{ table.total.value }} 条</span>
          </span>
          <span class="muted xs">审计日志为「只追加」，任何角色都不能修改或删除</span>
        </div>
      </template>

      <StatePanel
        :status="table.status.value === 'ready' && table.isEmpty.value ? 'empty' : table.status.value"
        :loading="table.loading.value"
        :code="table.error.value?.code ?? null"
        :message="table.error.value?.message ?? null"
        :trace-id="table.traceId.value"
        :rows="6"
        empty-title="没有审计记录"
        empty-hint="当前筛选条件下没有操作留痕（试试放宽时间窗口）"
        @retry="table.load()"
      >
        <template #empty-actions>
          <el-button @click="onReset">重置筛选</el-button>
        </template>

        <el-table :data="table.rows.value" v-loading="table.loading.value" stripe row-key="id">
          <el-table-column label="时间" width="150">
            <template #default="{ row }">
              <span class="muted nowrap xs">{{ fmtDateTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作人" width="140">
            <template #default="{ row }">
              <span>{{ row.username || '系统' }}</span>
              <div v-if="row.user_id" class="muted xs mono">…{{ tailId(row.user_id) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="动作" width="180">
            <template #default="{ row }">
              <StatusTag :text="row.action_name || row.action" tone="primary" />
              <div class="muted xs mono">{{ row.action }}</div>
            </template>
          </el-table-column>

          <el-table-column label="目标" min-width="190">
            <template #default="{ row }">
              <div class="ellipsis" :title="row.target_name || ''">
                {{ dash(row.target_name) }}
              </div>
              <div class="muted xs">
                <span class="mono">{{ dash(row.target_type) }}</span>
                <span v-if="row.target_id" class="mono"> · {{ shortId(row.target_id) }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="前后值" min-width="260">
            <template #default="{ row }">
              <div v-if="row.before !== null || row.after !== null" class="diff-cell">
                <JsonViewer :value="row.before" label="变更前" :default-open="false" />
                <JsonViewer :value="row.after" label="变更后" :default-open="false" />
              </div>
              <span v-else class="muted xs">—</span>
            </template>
          </el-table-column>

          <el-table-column label="来源 IP" width="140">
            <template #default="{ row }">
              <span class="mono xs">{{ dash(row.ip) }}</span>
              <div v-if="row.ua" class="muted xs ellipsis" :title="row.ua">{{ uaShort(row.ua) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="trace_id" width="170">
            <template #default="{ row }">
              <span v-if="row.trace_id" class="mono xs" :title="row.trace_id">
                {{ shortId(row.trace_id) }}
              </span>
              <span v-else class="muted xs">—</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            background
            layout="total, sizes, prev, pager, next, jumper"
            :total="table.total.value"
            :current-page="table.page.value"
            :page-size="table.perPage.value"
            :page-sizes="[20, 50, 100]"
            @current-change="table.onPageChange"
            @size-change="table.onSizeChange"
          />
        </div>

        <TraceFooter :trace-id="table.traceId.value" extra="导出行为自身留痕" />
      </StatePanel>
    </el-card>

    <!-- ============ 导出 ============ -->
    <el-dialog v-model="exportVisible" title="导出审计日志（CSV）" width="560px">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>本次导出将使用与列表<b>完全相同的筛选条件</b></template>
        <template #default>导出行为<b>自身会留痕</b>（谁在何时导出了什么范围的日志）。</template>
      </el-alert>

      <div class="form">
        <div class="row">
          <span class="k">当前筛选</span>
          <div class="filter-summary">
            <span v-if="keywordInput" class="chip">关键字：{{ keywordInput }}</span>
            <span v-if="actionInput" class="chip">动作：{{ actionInput }}</span>
            <span v-if="targetTypeInput" class="chip">目标类型：{{ targetTypeInput }}</span>
            <span v-if="targetIdInput" class="chip">目标 ID：{{ targetIdInput }}</span>
            <span v-if="range" class="chip">时间：{{ range[0] }} ~ {{ range[1] }}</span>
            <span v-if="!hasFilter" class="muted xs">无筛选（导出全量范围内数据，可能超上限）</span>
          </div>
        </div>

        <div class="row">
          <span class="k">导出上限</span>
          <div class="inline">
            <el-input-number v-model="maxRows" :min="1" :max="100000" :step="1000" />
            <span class="muted xs">条（超出会返回 40002，不会截断）</span>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="exportVisible = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="doExport">开始导出</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-6b · 审计日志（`/system/audit-log`，F-1.5，**API-28 / API-29**）
 *
 * 🔴 三条契约（`api/audit.ts` 已钉死）：
 *   ① **导出超上限报 `40002` 而非静默截断** —— 界面要把「请缩小时间窗」明确提示，
 *      **不得**把 `max_rows` 当成分页参数。
 *   ② **导出返回 `CommonResp` 内嵌 `content` 字符串（CSV）**，**不是二进制流**。
 *   ③ **CSV 已由后端做公式注入防护** —— 前端**不得二次加工**内容。
 *
 * 另：时间参数是**扁平** `start_time` / `end_time`（CU-08 起的既有口径），
 * 不是 `filters.xxx` 那种带点键名。
 */
import { computed, ref } from 'vue'

import { auditApi, type AuditExportRequest, type AuditLogQuery } from '@/api/audit'
import { ApiError } from '@/api/http'
import { dash } from '@/api/types'
import { fmtDateTime, saveTextFile } from '@/utils/format'
import JsonViewer from '@/components/JsonViewer.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'
import { usePagedTable } from '@/composables/usePagedTable'

const notify = useNotify()

const desc = '全量操作留痕（只追加）。导出超上限会明确报错，不会静默截断。'

// ---------------- 筛选 ----------------
const keywordInput = ref('')
const actionInput = ref('')
const targetTypeInput = ref('')
const targetIdInput = ref('')
const range = ref<[string, string] | null>(null)

function buildQuery(): AuditLogQuery {
  const q: AuditLogQuery = {}
  if (keywordInput.value.trim()) q.keyword = keywordInput.value.trim()
  if (actionInput.value.trim()) q.action = actionInput.value.trim()
  if (targetTypeInput.value.trim()) q.target_type = targetTypeInput.value.trim()
  if (targetIdInput.value.trim()) q.target_id = targetIdInput.value.trim()
  if (range.value) {
    q.start_time = range.value[0]
    q.end_time = range.value[1]
  }
  return q
}

const table = usePagedTable(
  (params) => auditApi.list(params as unknown as AuditLogQuery),
  buildQuery() as unknown as Record<string, unknown>,
  { perPage: 20 },
)

/** 先删旧键再合并（防脏筛选条件）。 */
function applyQuery(): void {
  const next = buildQuery() as unknown as Record<string, unknown>
  const target = table.query as unknown as Record<string, unknown>
  for (const key of Object.keys(target)) {
    if (!(key in next)) delete target[key]
  }
  Object.assign(target, next)
  table.search()
}

function reload(): void {
  applyQuery()
}

function onReset(): void {
  keywordInput.value = ''
  actionInput.value = ''
  targetTypeInput.value = ''
  targetIdInput.value = ''
  range.value = null
  applyQuery()
}

// ---------------- 展示辅助 ----------------
function shortId(id: string | null): string {
  if (!id) return '—'
  return id.length > 14 ? `${id.slice(0, 6)}…${id.slice(-6)}` : id
}

function tailId(id: string): string {
  return id.length > 8 ? id.slice(-8) : id
}

/** UA 只做粗粒度识别，便于快速扫读（完整 UA 在 title 里）。 */
function uaShort(ua: string): string {
  const s = ua.toLowerCase()
  if (s.includes('edg')) return 'Edge'
  if (s.includes('chrome')) return 'Chrome'
  if (s.includes('firefox')) return 'Firefox'
  if (s.includes('safari')) return 'Safari'
  if (s.includes('curl')) return 'curl'
  if (s.includes('python')) return 'Python'
  return ua.slice(0, 18)
}

const hasFilter = computed(
  () =>
    !!keywordInput.value.trim() ||
    !!actionInput.value.trim() ||
    !!targetTypeInput.value.trim() ||
    !!targetIdInput.value.trim() ||
    !!range.value,
)

// ---------------- 导出（API-29） ----------------
const exportVisible = ref(false)
const exporting = ref(false)
const maxRows = ref(5000)

function openExport(): void {
  exportVisible.value = true
}

async function doExport(): Promise<void> {
  exporting.value = true
  try {
    const payload: AuditExportRequest = {
      ...buildQuery(),
      format: 'csv',
      max_rows: maxRows.value,
    }
    const res = await auditApi.export(payload)
    const out = res.data
    if (!out?.content) {
      notify.warn('导出未返回内容')
      return
    }
    // 🔴 原样落盘：后端已完成公式注入防护，前端不再加工
    saveTextFile(out.content, out.filename)
    notify.ok(`已导出 ${out.row_count} 条（本次导出已留痕）`)
    exportVisible.value = false
  } catch (e) {
    if (e instanceof ApiError && e.code === 40002) {
      // 🔴 明确告知「要做什么」，而不是笼统的「参数错误」
      notify.warn(
        `导出条数超过上限（${maxRows.value} 条）：请<b>缩小时间窗口</b>或收紧筛选条件后分批导出（后端不会自动截断）。`,
      )
    } else if (e instanceof ApiError && e.isForbidden) {
      notify.warn('当前角色没有导出审计日志的权限（audit:export）。')
    } else {
      notify.fail(e, '导出失败')
    }
  } finally {
    exporting.value = false
  }
}

// 🔴 `usePagedTable` 默认已在 `onMounted` 时加载，本页无需再手动触发一次。
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
.diff-cell {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.nowrap {
  white-space: nowrap;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form .row {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 10px;
  align-items: start;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
  padding-top: 5px;
}
.form .inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-summary {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.filter-summary .chip {
  font-size: 11.5px;
  background: var(--primary-weak);
  color: var(--primary);
  border-radius: 3px;
  padding: 1px 7px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
