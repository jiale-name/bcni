<template>
  <div>
    <PageHeader title="字段复核工作台" :desc="desc">
      <template #actions>
        <el-button :disabled="loading" @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 队列顶层 meta 的口径说明（放在最显眼处，因为它决定「能不能相信这批数据」） -->
    <el-alert v-if="warning" type="warning" :closable="false" show-icon class="mb12">
      <template #title>非阻断提示</template>
      <template #default>{{ warning }}</template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        复核只做「逐字段处置」。批量确认单次上限 <b>{{ REVIEW_CONFIRM_MAX }}</b> 条（超出返回
        <code class="mono">40002</code>，<b>不是</b>自动截断）。人工修正（EDIT）后置信度由后端置为
        <b>1.0</b>，前端不自行改写。
      </template>
    </el-alert>

    <!-- ============ 工作量看板 ============ -->
    <div class="grid g4 mb12">
      <MetricCard label="待复核" :value="board?.pending_total ?? 0" unit=" 项"
        :tone="(board?.pending_total ?? 0) > 0 ? 'warn' : 'ok'" />
      <MetricCard label="已确认" :value="board?.confirmed_total ?? 0" unit=" 项" tone="ok" />
      <MetricCard label="已修正" :value="board?.corrected_total ?? 0" unit=" 项" tone="purple" />
      <MetricCard
        label="预计剩余工时"
        :value="board?.estimated_remaining_minutes ?? 0"
        unit=" 分钟"
        :sub="`高影响 ${meta?.high_impact_total ?? 0} · 低影响抽样 ${meta?.low_impact_sampled_total ?? 0}`"
      />
    </div>

    <!-- ============ 筛选 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <!-- ⚠️ 影响级别是**字符串枚举**（HIGH / LOW），不是数值枚举，
             故不走 `EnumSelect`（它面向 `EnumOption {value:number}`）。 -->
        <el-select v-model="impactLevel" clearable placeholder="影响级别" style="width: 150px">
          <el-option value="HIGH" label="高影响字段" />
          <el-option value="LOW" label="低影响字段" />
        </el-select>
        <el-select v-model="fieldCodes" multiple filterable clearable collapse-tags
          placeholder="字段（不选=全部）" style="min-width: 220px">
          <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
        </el-select>
        <el-input-number v-model="confidenceMax" :min="0" :max="1" :step="0.1" :precision="2"
          placeholder="置信度上限" controls-position="right" style="width: 150px" />
        <el-checkbox v-model="sampleOnly">仅抽样下发项</el-checkbox>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="onResetFilters">重置</el-button>
      </div>
    </el-card>

    <!-- ============ 队列 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />复核队列
            <span class="sub">共 {{ total }} 项</span>
          </span>
          <div class="bulk">
            <span class="muted xs">已选 {{ selected.length }} 项</span>
            <el-button
              size="small"
              :disabled="!selected.length || submitting"
              :loading="submitting"
              @click="batchConfirm('ACCEPT_AI')"
            >
              批量采纳 AI 值
            </el-button>
            <el-button
              size="small"
              :disabled="!selected.length || submitting"
              :loading="submitting"
              @click="batchConfirm('ACCEPT_RAW')"
            >
              批量采纳原文值
            </el-button>
            <el-button
              size="small"
              :disabled="!selected.length || submitting"
              @click="batchConfirm('SKIP')"
            >
              批量跳过
            </el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="conflictNote" type="warning" :closable="false" show-icon class="mb12">
        <template #title>并发冲突</template>
        <template #default>
          {{ conflictNote }} —— 冲突项已从选中集中剔除并高亮（<b>整批不会失败</b>）。
        </template>
      </el-alert>

      <StatePanel
        :status="status === 'ready' && rows.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有待复核字段"
        empty-hint="当前筛选条件下，复核队列为空（可能已被处理完）"
        @retry="loadAll"
      >
        <el-table
          ref="tableRef"
          :data="rows"
          v-loading="loading"
          stripe
          row-key="field_id"
          :row-class-name="rowClass"
          @selection-change="onSelectionChange"
        >
          <el-table-column type="selection" width="44" :selectable="() => true" />

          <el-table-column label="档案" min-width="130">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openTalent(row.talent_id)">
                {{ row.talent_name }}
              </el-link>
              <div class="muted xs mono">{{ shortId(row.talent_id) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="字段" width="120">
            <template #default="{ row }">{{ row.field_name || row.field_code }}</template>
          </el-table-column>

          <el-table-column label="原文值" min-width="180">
            <template #default="{ row }">
              <span class="val">{{ dash(row.raw_value) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="AI 解析值" min-width="180">
            <template #default="{ row }">
              <span class="val ai">{{ dash(row.parsed_value) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="置信度" width="100" align="right">
            <template #default="{ row }">
              <!-- 🔴 confidence=null 表示「不来自模型」（确定性规则），不是 0 -->
              <span v-if="row.confidence === null" class="muted xs">规则命中</span>
              <span v-else class="num" :class="confTone(row.confidence)">
                {{ (row.confidence * 100).toFixed(0) }}%
              </span>
            </template>
          </el-table-column>

          <el-table-column label="影响" width="90">
            <template #default="{ row }">
              <StatusTag
                :text="row.impact_level_name || row.impact_level"
                :tone="row.impact_level === 'HIGH' ? 'danger' : 'info'"
              />
            </template>
          </el-table-column>

          <el-table-column label="来源" min-width="140">
            <template #default="{ row }">
              <div class="ellipsis xs" :title="row.source_file || ''">
                {{ dash(row.source_file) }}
              </div>
              <div class="muted xs ellipsis" :title="row.source_locator || ''">
                {{ dash(row.source_locator) }}
              </div>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <div class="ops">
                <a @click="act(row, 'ACCEPT_AI')">采纳 AI</a>
                <a @click="act(row, 'ACCEPT_RAW')">采纳原文</a>
                <a @click="openCorrect(row)">修正</a>
                <a @click="act(row, 'SKIP')">跳过</a>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            background
            layout="total, sizes, prev, pager, next, jumper"
            :total="total"
            :current-page="page"
            :page-size="perPage"
            :page-sizes="[20, 50, 100]"
            @current-change="onPageChange"
            @size-change="onSizeChange"
          />
        </div>

        <TraceFooter :trace-id="traceId" extra="批量确认单次上限 200 条" />
      </StatePanel>
    </el-card>

    <!-- ============ 修正对话框 ============ -->
    <el-dialog v-model="correctVisible" title="修正字段值" width="520px">
      <div v-if="correctRow" class="correct-form">
        <div class="row">
          <span class="k">档案</span>
          <span class="v">{{ correctRow.talent_name }}</span>
        </div>
        <div class="row">
          <span class="k">字段</span>
          <span class="v">{{ correctRow.field_name || correctRow.field_code }}</span>
        </div>
        <div class="row">
          <span class="k">原文值</span>
          <span class="v muted">{{ dash(correctRow.raw_value) }}</span>
        </div>
        <div class="row">
          <span class="k">AI 值</span>
          <span class="v muted">{{ dash(correctRow.parsed_value) }}</span>
        </div>
        <div class="row">
          <span class="k">修正为</span>
          <el-input v-model="correctValue" placeholder="输入最终值" />
        </div>
        <div class="row">
          <span class="k">修正理由</span>
          <el-input v-model="correctReason" placeholder="可选，便于后续追溯" />
        </div>
        <div class="muted xs">
          提交后该字段置信度由后端置为 <b>1.0</b>，并写入变更留痕。
        </div>
      </div>
      <template #footer>
        <el-button @click="correctVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCorrect">提交修正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-10 · 字段复核工作台（`/talent/review`，F-3.3 / F-3.3.1，**API-44 ~ API-49**）
 *
 * 🔴 本页承载五条最容易写错的契约（`api/review.ts` 已钉死）：
 *   1. **队列响应有顶层 `meta`**（与 `page` 同级，**不在 `data` 里**）——
 *      把它当 `data.meta` 读会拿到 `undefined`，工作台就会「看不到低影响字段是抽样的」。
 *      因此本页**不用 `usePagedTable`**（它只回 `page`），改为手写分页 + 单独接 `meta`。
 *   2. **`meta.warning` 是非阻断提示** —— 必须显示，但**不拦截**任何操作。
 *   3. **批量确认上限 200 条**，超出 `40002`（**不是**截断）—— 前端先在本地拦一道。
 *   4. **并发冲突 `40902` 带 `conflicted[]`** —— 把冲突项**从选中集剔除并高亮**，
 *      **不整批回滚**（其余项其实已经生效了）。
 *   5. **`confidence=null` ≠ `0`** —— `null` 表示该字段不来自模型（确定性规则）。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/http'
import {
  REVIEW_CONFIRM_MAX,
  reviewApi,
  type ReviewConfirmAction,
  type ReviewDashboard,
  type ReviewQueueItem,
  type ReviewQueueMeta,
} from '@/api/review'
import { dash } from '@/api/types'
import type { LoadStatus } from '@/composables/useAsyncData'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const router = useRouter()
const notify = useNotify()

const desc =
  '逐字段处置复核队列（接受 AI 值 / 接受原文值 / 手工修正 / 跳过）。批量操作单次上限 200 条。'

// ---------------- 看板（API-49） ----------------
const board = ref<ReviewDashboard | null>(null)

// ---------------- 队列 ----------------
const loading = ref(false)
const status = ref<LoadStatus>('idle')
const rows = ref<ReviewQueueItem[]>([])
/** 🔴 顶层 `meta`（与 `page` 同级）—— 不是 `data.meta`。 */
const meta = ref<ReviewQueueMeta | null>(null)
const warning = ref<string | null>(null)
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

// ---------------- 筛选 ----------------
const impactLevel = ref<'HIGH' | 'LOW' | null>(null)
const fieldCodes = ref<string[]>([])
const confidenceMax = ref<number | null>(null)
const sampleOnly = ref(false)

/** 字段候选：从队列结果里动态归纳（后端无「字段枚举」接口，故按实际出现项给选项）。 */
const fieldOptions = ref<{ value: string; label: string }[]>([])

// ---------------- 选择与提交 ----------------
const selected = ref<ReviewQueueItem[]>([])
const submitting = ref(false)
const conflictNote = ref<string | null>(null)
/** 被他人抢先处理的 field_id —— 用于行高亮。 */
const conflictedIds = ref<Set<string>>(new Set())

const tableRef = ref<unknown>()

function shortId(id: string): string {
  return id.length > 10 ? `…${id.slice(-10)}` : id
}

function confTone(c: number): string {
  if (c >= 0.9) return 'ok'
  if (c >= 0.7) return 'mid'
  return 'low'
}

function rowClass({ row }: { row: ReviewQueueItem }): string {
  return conflictedIds.value.has(row.field_id) ? 'row-conflict' : ''
}

function onSelectionChange(sel: ReviewQueueItem[]): void {
  selected.value = sel
}

async function loadBoard(): Promise<void> {
  try {
    const res = await reviewApi.dashboard()
    board.value = res.data
  } catch {
    // 看板失败不影响队列主路径（可能无 review:dashboard 权限）
    board.value = null
  }
}

async function loadQueue(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const res = await reviewApi.queue({
      page: page.value,
      per_page: perPage.value,
      impact_level: impactLevel.value ?? undefined,
      confidence_max: confidenceMax.value ?? undefined,
      field_codes: fieldCodes.value.length ? fieldCodes.value.join(',') : undefined,
      sample_only: sampleOnly.value || undefined,
    })

    rows.value = res.data ?? []
    // 🔴 `meta` 与 `page` 同级（在 `data` 之外），由 `http.parseBody` 归一进 `res.extra`
    //    —— 直接写 `res.meta` 编译期不存在、运行期恒 `undefined`（页面永远拿不到 warning）。
    meta.value = res.extra ?? null
    warning.value = res.extra?.warning ?? null
    total.value = res.page?.total ?? rows.value.length
    if (res.page?.page) page.value = res.page.page
    if (res.page?.per_page) perPage.value = res.page.per_page
    traceId.value = res.traceId
    status.value = 'ready'

    // 归纳字段候选项（首次加载时）
    if (fieldOptions.value.length === 0) {
      const seen = new Map<string, string>()
      for (const r of rows.value) seen.set(r.field_code, r.field_name || r.field_code)
      fieldOptions.value = [...seen.entries()].map(([value, label]) => ({ value, label }))
    }
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    rows.value = []
    meta.value = null
    total.value = 0
    errCode.value = err.code
    errMsg.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadBoard(), loadQueue()])
}

function reload(): void {
  page.value = 1
  void loadQueue()
}

function onResetFilters(): void {
  impactLevel.value = null
  fieldCodes.value = []
  confidenceMax.value = null
  sampleOnly.value = false
  reload()
}

function onPageChange(p: number): void {
  page.value = p
  void loadQueue()
}

function onSizeChange(s: number): void {
  perPage.value = s
  page.value = 1
  void loadQueue()
}

function openTalent(id: string): void {
  void router.push({ name: 'talent-detail', params: { id } })
}

// ---------------- 单条处置 ----------------
async function act(row: ReviewQueueItem, action: 'ACCEPT_AI' | 'ACCEPT_RAW' | 'SKIP'): Promise<void> {
  await submit([{ field_id: row.field_id, action }])
}

async function batchConfirm(action: 'ACCEPT_AI' | 'ACCEPT_RAW' | 'SKIP'): Promise<void> {
  if (!selected.value.length) return
  // 🔴 前端先拦一道上限（后端也会拦并返回 40002，但本地拦能省一次往返）
  if (selected.value.length > REVIEW_CONFIRM_MAX) {
    notify.warn(
      `单次最多确认 ${REVIEW_CONFIRM_MAX} 条，当前已选 ${selected.value.length} 条，请分批提交。`,
    )
    return
  }
  const label =
    action === 'ACCEPT_AI' ? '采纳 AI 值' : action === 'ACCEPT_RAW' ? '采纳原文值' : '跳过'
  const okToGo = await notify.confirm(`将对选中的 ${selected.value.length} 个字段执行「${label}」，是否继续？`)
  if (!okToGo) return

  await submit(selected.value.map((r) => ({ field_id: r.field_id, action })))
}

/**
 * 提交确认（单条与批量共用）。
 *
 * 🔴 `40902`（并发冲突）是**局部**失败：后端在 `conflicted[]` 里列出被别人抢先的字段，
 *    其余字段**已经生效**。因此这里：
 *      - 不整批报错；
 *      - 把冲突项从选中集剔除、标记高亮，并刷新列表；
 *      - 提示「已被他人处理」而非「操作失败」。
 */
async function submit(actions: ReviewConfirmAction[]): Promise<void> {
  submitting.value = true
  conflictNote.value = null
  conflictedIds.value = new Set()
  try {
    const res = await reviewApi.confirm(actions)
    const out = res.data
    const msg = [
      out?.confirmed_count ? `确认 ${out.confirmed_count}` : '',
      out?.corrected_count ? `修正 ${out.corrected_count}` : '',
      out?.skipped_count ? `跳过 ${out.skipped_count}` : '',
    ]
      .filter(Boolean)
      .join(' · ')
    notify.ok(msg || '已提交')
    void clearSelection()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40902) {
      const conflicted = (e.data as { conflicted?: { field_id?: string }[] } | null)?.conflicted ?? []
      const ids = conflicted
        .map((c) => c.field_id)
        .filter((x): x is string => typeof x === 'string')
      conflictedIds.value = new Set(ids)
      selected.value = selected.value.filter((r) => !conflictedIds.value.has(r.field_id))
      conflictNote.value = `${ids.length} 个字段已被他人抢先处理`
      void clearSelection()
    } else if (e instanceof ApiError && e.code === 40002) {
      notify.warn(`提交条数超出上限（单次最多 ${REVIEW_CONFIRM_MAX} 条），请分批提交。`)
    } else {
      notify.fail(e, '提交失败')
    }
  } finally {
    submitting.value = false
    await loadQueue()
  }
}

async function clearSelection(): Promise<void> {
  const t = tableRef.value as { clearSelection?: () => void } | null
  t?.clearSelection?.()
  selected.value = []
}

// ---------------- 修正（API-47） ----------------
const correctVisible = ref(false)
const correctRow = ref<ReviewQueueItem | null>(null)
const correctValue = ref('')
const correctReason = ref('')

function openCorrect(row: ReviewQueueItem): void {
  correctRow.value = row
  // 预填：优先 AI 解析值，其次原文值（人工在此基础上改）
  correctValue.value = row.parsed_value ?? row.raw_value ?? ''
  correctReason.value = ''
  correctVisible.value = true
}

async function submitCorrect(): Promise<void> {
  const row = correctRow.value
  if (!row) return
  if (!correctValue.value.trim()) {
    ElMessage({ type: 'warning', message: '修正值不能为空' })
    return
  }
  submitting.value = true
  try {
    await reviewApi.correct(row.field_id, correctValue.value.trim(), correctReason.value.trim() || undefined)
    notify.ok('已修正，置信度由后端置为 1.0，并已写入变更留痕')
    correctVisible.value = false
    await loadQueue()
  } catch (e) {
    notify.fail(e, '修正失败')
  } finally {
    submitting.value = false
  }
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
.card-head .sub {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--txt-3);
}
.bulk {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
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
.val {
  font-size: 12.5px;
  word-break: break-all;
}
.val.ai {
  color: var(--primary);
}
.num.ok {
  color: var(--ok);
}
.num.mid {
  color: var(--warn);
}
.num.low {
  color: var(--danger);
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.correct-form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.correct-form .row {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 10px;
  align-items: center;
}
.correct-form .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.correct-form .v {
  font-size: 12.5px;
}
.mb12 {
  margin-bottom: 12px;
}
:deep(.row-conflict) {
  background: var(--danger-weak) !important;
}
:deep(.row-conflict td) {
  background: var(--danger-weak) !important;
}
</style>
