<template>
  <div>
    <PageHeader title="AI 调用日志与成本看板" :desc="desc">
      <template #actions>
        <el-button :disabled="loading" @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 脱敏率分母口径：最容易误用的一条 -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        脱敏率的分母是<b>调用时刻的事实列 <code class="mono">deployment</code></b>，
        不是模型配置的<b>当前值</b> —— 改一次配置不会让历史合规统计集体失真。
        因此本页所有脱敏统计<b>只按日志行自身</b>计算，不会用「当前配置」反推。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        金额（<code class="mono">cost_yuan</code>）是后端 <code class="mono">Decimal</code> 的<b>字符串</b>序列化。
        本页<b>直接渲染字符串</b>；确需换算时使用「分」为单位的整数运算（见 <code class="mono">ai.ts</code> 的
        <code class="mono">yuanToCents</code>），绝不做浮点累加。
      </template>
    </el-alert>

    <!-- ============ 概览指标 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input v-model="sceneFilter" placeholder="场景 code" clearable style="width: 160px" />
        <el-input v-model="modelFilter" placeholder="模型 code" clearable style="width: 160px" />
        <el-date-picker
          v-model="range"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 350px"
        />
        <el-button type="primary" @click="loadAll">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </el-card>

    <div class="grid g4 mb12">
      <MetricCard label="调用次数" :value="summary?.calls ?? 0" unit=" 次" />
      <MetricCard
        label="累计成本"
        :value="summary?.cost_total_yuan ?? '0.00'"
        unit=" 元"
        :tone="summary?.cost_alert ? 'warn' : 'default'"
        :sub="summary?.cost_alert ? '已触发成本告警' : '未触发告警'"
      />
      <MetricCard
        label="平均延迟"
        :value="summary?.avg_latency_ms ?? 0"
        unit=" ms"
        :sub="`失败 ${summary?.failed ?? 0} 次`"
        :tone="(summary?.failed ?? 0) > 0 ? 'warn' : 'default'"
      />
      <MetricCard
        label="脱敏覆盖率"
        :value="fmtRate(summary?.mask_applied_rate)"
        :tone="summary?.mask_alert ? 'danger' : 'ok'"
        :sub="`缓存命中率 ${fmtRate(summary?.cache_hit_rate)} · 云端调用 ${summary?.cloud_calls ?? 0} 次`"
        :hint="true"
      />
    </div>

    <!-- ============ 成本看板（API-89） ============ -->
    <div class="grid g2 mb12">
      <el-card shadow="never">
        <template #header>
          <div class="card-head"><span class="hd"><i class="bar" />按场景成本</span></div>
        </template>
        <StatePanel
          :status="dashStatus === 'ready' && !(board?.by_scene?.length) ? 'empty' : dashStatus"
          :loading="dashLoading"
          :code="dashErrCode"
          :message="dashErrMsg"
          :rows="5"
          empty-title="暂无按场景数据"
          empty-hint="当前时间窗口内没有调用记录"
          @retry="loadDashboard"
        >
          <EChart v-if="board?.by_scene?.length" :option="sceneChart" height="260px" />
        </StatePanel>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <div class="card-head"><span class="hd"><i class="bar" />按模型成本</span></div>
        </template>
        <StatePanel
          :status="dashStatus === 'ready' && !(board?.by_model?.length) ? 'empty' : dashStatus"
          :loading="dashLoading"
          :code="dashErrCode"
          :message="dashErrMsg"
          :rows="5"
          empty-title="暂无按模型数据"
          empty-hint="当前时间窗口内没有调用记录"
          @retry="loadDashboard"
        >
          <EChart v-if="board?.by_model?.length" :option="modelChart" height="260px" />
        </StatePanel>
      </el-card>
    </div>

    <el-card v-if="board?.by_time?.length" shadow="never" class="mb12">
      <template #header>
        <div class="card-head">
          <span class="hd"><i class="bar" />成本趋势</span>
          <span class="muted xs">金额为各时间桶的成本合计（字符串原值汇总由后端完成）</span>
        </div>
      </template>
      <EChart :option="timeChart" height="240px" />
    </el-card>

    <!-- ============ 调用日志（API-88） ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />调用日志
            <span class="sub">共 {{ total }} 条</span>
          </span>
          <div class="filters">
            <el-select v-model="statusFilter" clearable placeholder="状态" size="small" style="width: 110px">
              <el-option v-for="o in AI_CALL_STATUS" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <el-select v-model="cacheFilter" clearable placeholder="缓存" size="small" style="width: 110px">
              <el-option :value="true" label="命中" />
              <el-option :value="false" label="未命中" />
            </el-select>
            <el-select v-model="maskFilter" clearable placeholder="脱敏" size="small" style="width: 110px">
              <el-option :value="true" label="已脱敏" />
              <el-option :value="false" label="未脱敏" />
            </el-select>
          </div>
        </div>
      </template>

      <StatePanel
        :status="status === 'ready' && rows.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有调用记录"
        empty-hint="当前筛选条件下没有 AI 调用日志"
        @retry="loadLogs"
      >
        <el-table :data="rows" v-loading="loading" stripe row-key="id">
          <el-table-column label="时间" width="150">
            <template #default="{ row }">
              <span class="muted nowrap xs">{{ fmtDateTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="场景" width="130">
            <template #default="{ row }">
              {{ row.scene_name || row.scene || '—' }}
              <div v-if="row.scene_stage" class="muted xs">{{ row.scene_stage }}</div>
            </template>
          </el-table-column>

          <el-table-column label="模型 / 提供方" width="170">
            <template #default="{ row }">
              <span class="mono xs">{{ row.model || '—' }}</span>
              <div class="muted xs">{{ row.provider || '—' }}</div>
            </template>
          </el-table-column>

          <el-table-column label="Prompt" width="150">
            <template #default="{ row }">
              <span class="xs mono">{{ row.prompt_code || '—' }}</span>
              <div v-if="row.prompt_version !== null" class="muted xs">v{{ row.prompt_version }}</div>
            </template>
          </el-table-column>

          <el-table-column label="Tokens" width="130" align="right">
            <template #default="{ row }">
              <span class="num xs">入 {{ fmtNumber(row.tokens_in) }}</span>
              <div class="num xs muted">出 {{ fmtNumber(row.tokens_out) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="成本（元）" width="110" align="right">
            <template #default="{ row }">
              <!-- 🔴 字符串直接渲染 -->
              <span class="num">{{ row.cost_yuan }}</span>
            </template>
          </el-table-column>

          <el-table-column label="延迟" width="110" align="right">
            <template #default="{ row }">
              <span class="num xs">{{ row.latency_ms ?? '—' }}<small v-if="row.latency_ms !== null">ms</small></span>
              <div v-if="row.first_token_ms !== null" class="muted xs">
                首字 {{ row.first_token_ms }}ms
              </div>
            </template>
          </el-table-column>

          <el-table-column label="标记" width="150">
            <template #default="{ row }">
              <div class="marks">
                <el-tag v-if="row.cache_hit" size="small" type="info" effect="light">缓存</el-tag>
                <el-tag v-if="row.fallback_used" size="small" type="warning" effect="light">降级</el-tag>
                <el-tag v-if="row.mask_applied" size="small" type="success" effect="light">脱敏</el-tag>
                <span v-if="!row.cache_hit && !row.fallback_used && !row.mask_applied" class="muted xs">—</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <StatusTag :text="row.status_name" :tone="row.status === 1 ? 'success' : 'danger'" />
            </template>
          </el-table-column>

          <el-table-column label="trace / 错误" min-width="180">
            <template #default="{ row }">
              <div v-if="row.error_code" class="danger xs">
                [{{ row.error_code }}] {{ row.error_msg || '' }}
              </div>
              <span v-if="row.trace_id" class="mono xs muted">{{ shortTrace(row.trace_id) }}</span>
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

        <TraceFooter
          :trace-id="traceId"
          extra="summary 与 page 同级（不在 data 内）"
        />
      </StatePanel>
    </el-card>
  </div>
</template>

<script setup lang="ts">
/**
 * P-18c · AI 调用日志与成本看板（`/ai/call-log`，F-9.5，**API-88 / API-89**）
 *
 * 🔴 三条契约（`api/ai.ts` 已钉死）：
 *   ① **API-88 的响应在分页壳上追加顶层 `summary`**（与 `page` **同级**，**不在 `data` 里**）——
 *      所以本页**不用 `usePagedTable`**（它只回 `page`），改为手写分页 + 单独接 `summary`。
 *   ② **脱敏率分母是「调用时刻的 `deployment`」**，不是模型配置当前值 ——
 *      故统计**只按日志行自身**，页面明说这一点。
 *   ③ **`cost_yuan` 是字符串**（`Decimal`）—— 直接渲染，不做浮点累加。
 */
import { computed, onMounted, ref } from 'vue'

import {
  aiApi,
  type AiCallLog,
  type AiCallSummary,
  type CostDashboard,
} from '@/api/ai'
import { AI_CALL_STATUS } from '@/api/enums'
import { ApiError } from '@/api/http'
import { fmtDateTime, fmtNumber, fmtRate } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import EChart from '@/components/EChart.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'

const desc =
  'AI 调用明细与成本看板。脱敏率按日志行自身统计（分母为调用时刻的 deployment），金额按字符串处理。'

const sceneFilter = ref('')
const modelFilter = ref('')
const range = ref<[string, string] | null>(null)

const statusFilter = ref<number | null>(null)
const cacheFilter = ref<boolean | null>(null)
const maskFilter = ref<boolean | null>(null)

function shortTrace(t: string): string {
  return t.length > 16 ? `${t.slice(0, 8)}…${t.slice(-6)}` : t
}

// ---------------- 日志（API-88） ----------------
const loading = ref(false)
const status = ref<LoadStatus>('idle')
const rows = ref<AiCallLog[]>([])
/** 🔴 顶层 `summary`（与 `page` 同级，不在 `data` 内）。 */
const summary = ref<AiCallSummary | null>(null)
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

async function loadLogs(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const [start, end] = range.value ?? [undefined, undefined]
    const res = await aiApi.callLogs({
      page: page.value,
      per_page: perPage.value,
      scene: sceneFilter.value.trim() || undefined,
      model: modelFilter.value.trim() || undefined,
      status: statusFilter.value ?? undefined,
      cache_hit: cacheFilter.value ?? undefined,
      mask_applied: maskFilter.value ?? undefined,
      start_time: start,
      end_time: end,
    })
    rows.value = res.data ?? []
    // 🔴 与 `page` 同级读取，**不是** `res.data.summary`。
    //    顶层聚合字段由 `http.parseBody` 归一进 `res.extra`（见 `api/types.ts` 的 `ApiResult<T, E>`）。
    summary.value = res.extra ?? null
    total.value = res.page?.total ?? rows.value.length
    if (res.page?.per_page) perPage.value = res.page.per_page
    traceId.value = res.traceId
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    rows.value = []
    summary.value = null
    total.value = 0
    errCode.value = err.code
    errMsg.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number): void {
  page.value = p
  void loadLogs()
}
function onSizeChange(s: number): void {
  perPage.value = s
  page.value = 1
  void loadLogs()
}

// ---------------- 成本看板（API-89） ----------------
const board = ref<CostDashboard | null>(null)
const dashLoading = ref(false)
const dashStatus = ref<LoadStatus>('idle')
const dashErrCode = ref<number | null>(null)
const dashErrMsg = ref<string | null>(null)

async function loadDashboard(): Promise<void> {
  dashLoading.value = true
  dashStatus.value = 'loading'
  dashErrCode.value = null
  dashErrMsg.value = null
  try {
    const [start, end] = range.value ?? [undefined, undefined]
    const res = await aiApi.costDashboard({
      scene: sceneFilter.value.trim() || undefined,
      model: modelFilter.value.trim() || undefined,
      start_time: start,
      end_time: end,
    })
    board.value = res.data
    dashStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    board.value = null
    dashErrCode.value = err.code
    dashErrMsg.value = err.displayMessage
    dashStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    dashLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  page.value = 1
  await Promise.allSettled([loadLogs(), loadDashboard()])
}

function onReset(): void {
  sceneFilter.value = ''
  modelFilter.value = ''
  range.value = null
  statusFilter.value = null
  cacheFilter.value = null
  maskFilter.value = null
  void loadAll()
}

// ---------------- 图表配置 ----------------
/** 主题色（与 tokens.css 对齐；图表内不硬编码品牌色以外的语义色）。 */
const C = {
  primary: '#175d75',
  purple: '#6b4ea8',
  ok: '#2c8a5f',
  warn: '#c8862a',
}

const sceneChart = computed(() => {
  const items = board.value?.by_scene ?? []
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '45%'],
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        label: { fontSize: 11, formatter: '{b}\n{c} 次' },
        data: items.map((b, i) => ({
          name: b.label || b.key,
          value: b.calls,
          itemStyle: {
            color: [C.primary, C.purple, C.ok, C.warn, '#4a8fa8', '#8f7ab8'][i % 6],
          },
        })),
      },
    ],
  }
})

const modelChart = computed(() => {
  const items = board.value?.by_model ?? []
  return {
    grid: { left: 90, right: 24, top: 16, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: {
      type: 'category',
      data: items.map((b) => b.label || b.key),
      axisLabel: { fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: C.primary, borderRadius: [0, 3, 3, 0] },
        data: items.map((b) => b.calls),
      },
    ],
  }
})

const timeChart = computed(() => {
  const items = board.value?.by_time ?? []
  return {
    grid: { left: 50, right: 24, top: 20, bottom: 28 },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    xAxis: {
      type: 'category',
      data: items.map((b) => b.label || b.key),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '调用次数',
        type: 'line',
        smooth: true,
        symbolSize: 5,
        itemStyle: { color: C.primary },
        areaStyle: { opacity: 0.12 },
        data: items.map((b) => b.calls),
      },
    ],
  }
})

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
.toolbar,
.filters {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.marks {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.nowrap {
  white-space: nowrap;
}
.danger {
  color: var(--danger);
}
.sm {
  font-size: 12.5px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
