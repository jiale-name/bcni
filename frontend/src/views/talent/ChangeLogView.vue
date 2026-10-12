<template>
  <div>
    <PageHeader title="档案变更历史" :desc="desc">
      <template #actions>
        <el-button @click="snapshotVisible = true">
          <el-icon><Clock /></el-icon>时间点回溯
        </el-button>
        <el-button :disabled="loading" @click="reload">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        快照是<b>确定性重放</b>（由变更日志链回放得出），<b>不是备份</b>。
        因此：未追踪字段按声明处理（不代表「当时为空」）、脏历史值降级为
        <code class="mono">null</code>（不抛错）。回溯时间早于建档时间返回
        <code class="mono">40001</code>（非法参数），不是 <code class="mono">40401</code>。
      </template>
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        <b>查看快照本身是敏感动作</b> —— 后端会写入审计
        <code class="mono">CHANGE_SNAPSHOT_VIEW</code>（谁在何时看了谁的历史）。
        本页不需要额外留痕，但你应当知道<b>本次查看已被记录</b>。
      </template>
    </el-alert>

    <!-- ============ 筛选 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input
          v-model="talentIdInput"
          placeholder="档案 ID（精确，雪花 ID 字符串）"
          clearable
          style="width: 230px"
        />

        <el-select v-model="fieldName" clearable filterable placeholder="字段（英文 code）" style="width: 190px">
          <el-option v-for="f in FIELD_OPTIONS" :key="f.value" :label="f.label" :value="f.value" />
        </el-select>

        <EnumSelect v-model="changeSource" :options="CHANGE_SOURCE" placeholder="变更来源" />

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
      <div class="muted xs mt8">
        ⚠️ 字段名请传<b>英文 code</b>（如 <code class="mono">work_years</code>），
        未知字段名返回 <code class="mono">40001</code>；时间窗口超限、或
        <code class="mono">start &gt; end</code> 同样返回 <code class="mono">40001</code>（不是空结果）。
      </div>
    </el-card>

    <!-- ============ 变更流水 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />变更流水
            <span class="sub">共 {{ total }} 条</span>
          </span>
          <span class="muted xs">按时间倒序；字段同时下发英文 code 与中文标签</span>
        </div>
      </template>

      <StatePanel
        :status="status === 'ready' && rows.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有变更记录"
        empty-hint="当前条件下没有变更留痕（试试放宽时间窗口或清空筛选）"
        @retry="load"
      >
        <el-table :data="rows" v-loading="loading" stripe row-key="id">
          <el-table-column label="时间" width="150">
            <template #default="{ row }">
              <span class="muted nowrap sm">{{ fmtDateTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="档案" min-width="150">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openTalent(row.talent_id)">
                {{ row.talent_name || shortId(row.talent_id) }}
              </el-link>
            </template>
          </el-table-column>

          <el-table-column label="字段" width="130">
            <template #default="{ row }">
              {{ row.field_label || row.field_name || '—' }}
              <div v-if="row.field_name" class="muted xs mono">{{ row.field_name }}</div>
            </template>
          </el-table-column>

          <el-table-column label="变更前 → 变更后" min-width="300">
            <template #default="{ row }">
              <div class="diff">
                <span class="before">{{ dash(row.before_value) }}</span>
                <el-icon class="arrow"><Right /></el-icon>
                <span class="after">{{ dash(row.after_value) }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="来源" width="110">
            <template #default="{ row }">
              <StatusTag :text="row.change_source_name" :tone="sourceTone(row.change_source)" />
            </template>
          </el-table-column>

          <el-table-column label="操作人" width="120">
            <template #default="{ row }">
              <span class="sm">{{ dash(row.operator_name) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="快照引用" width="150">
            <template #default="{ row }">
              <span v-if="row.snapshot_ref" class="mono xs" :title="row.snapshot_ref">
                {{ shortId(row.snapshot_ref) }}
              </span>
              <span v-else class="muted xs">—</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="ops">
                <a @click="openFieldHistory(row)">字段全链</a>
                <a @click="openSnapshotFor(row.talent_id)">回溯此处</a>
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
        <TraceFooter :trace-id="traceId" extra="查看快照会写审计 CHANGE_SNAPSHOT_VIEW" />
      </StatePanel>
    </el-card>

    <!-- ============ 时间点回溯（API-69） ============ -->
    <el-dialog v-model="snapshotVisible" title="时间点回溯（只读）" width="760px" top="6vh">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>本次查看已留痕</template>
        <template #default>
          后端将写入审计 <code class="mono">CHANGE_SNAPSHOT_VIEW</code>（记录查看人与时间）。
          回溯时间<b>必须 ≥ 建档时间</b>，否则返回 <code class="mono">40001</code>。
        </template>
      </el-alert>

      <div class="snap-form">
        <el-input v-model="snapTalentId" placeholder="档案 ID（雪花 ID 字符串）" style="width: 230px" />
        <el-date-picker
          v-model="snapAt"
          type="datetime"
          placeholder="回溯到哪个时间点"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 210px"
        />
        <el-button type="primary" :loading="snapLoading" @click="runSnapshot">回溯</el-button>
      </div>

      <el-alert v-if="snapError" type="error" :closable="false" show-icon class="mt12">
        <template #title>{{ snapErrorTitle }}</template>
        <template #default>{{ snapError }}</template>
      </el-alert>

      <template v-if="snap">
        <!-- 历史过长提示（**可识别**，不是静默截断） -->
        <el-alert
          v-if="snap.history_truncated"
          type="warning"
          :closable="false"
          show-icon
          class="mt12"
        >
          <template #title>历史过长，仅回溯最近一段</template>
          <template #default>
            本次回溯触达扫描上限（<code class="mono">SNAPSHOT_LOG_LIMIT</code> = 5000 条日志）。
            结果<b>按上限截断且可识别</b>，不代表更早之前没有变更。
          </template>
        </el-alert>

        <div class="snap-meta mt12">
          <span>
            快照来源日志：
            <code class="mono xs">{{ snap.snapshot_from_change_log_id || '—' }}</code>
          </span>
          <span>
            生效区间：{{ fmtDateTime(snap.effective_from) }} ~ {{ fmtDateTime(snap.effective_to) }}
          </span>
          <span>此后变更 {{ snap.changes_after }} 条</span>
        </div>

        <table class="snap-table mt12">
          <tbody>
            <tr>
              <td class="k">姓名</td>
              <td>{{ dash(snap.talent.name) }}</td>
              <td class="k">性别</td>
              <td>{{ dash(snap.talent.gender) }}</td>
            </tr>
            <tr>
              <td class="k">出生日期</td>
              <td>{{ dash(snap.talent.birth_date) }}</td>
              <td class="k">学历</td>
              <td>{{ dash(snap.talent.education_level) }}</td>
            </tr>
            <tr>
              <td class="k">学校</td>
              <td>{{ dash(snap.talent.school) }}</td>
              <td class="k">专业</td>
              <td>{{ dash(snap.talent.major) }}</td>
            </tr>
            <tr>
              <td class="k">工作年限</td>
              <td>{{ snap.talent.work_years === null ? '—' : `${snap.talent.work_years} 年` }}</td>
              <td class="k">数据状态</td>
              <td>
                <StatusTag :text="snap.talent.data_status_name" tone="plain" />
                版本 v{{ snap.talent.version_no ?? '—' }}
              </td>
            </tr>
          </tbody>
        </table>

        <div class="mt12">
          <div class="xs muted mb6">标签（{{ snap.tags.length }}）</div>
          <div class="tag-wrap">
            <el-tag v-for="t in snap.tags" :key="t.tag_code" size="small" type="info" effect="light">
              {{ t.tag_name || t.tag_code }}
            </el-tag>
            <span v-if="!snap.tags.length" class="muted xs">—</span>
          </div>
        </div>

        <!-- 未追踪字段：必须显式告知，避免把 null 误读为「当时为空」 -->
        <div v-if="snap.untracked_fields?.length" class="mt12">
          <div class="xs muted mb6">
            未追踪字段（{{ snap.untracked_fields.length }}）—— 这些字段<b>不在变更追踪范围内</b>，
            上表若为空<b>不代表当时为空</b>
          </div>
          <div class="tag-wrap">
            <el-tag
              v-for="f in snap.untracked_fields"
              :key="f"
              size="small"
              type="warning"
              effect="plain"
            >
              {{ f }}
            </el-tag>
          </div>
        </div>

        <JsonViewer class="mt12" :value="snap" label="快照原始响应（排查用）" />
      </template>
    </el-dialog>

    <!-- ============ 字段完整变更链（API-70） ============ -->
    <el-dialog v-model="fieldVisible" title="单字段完整变更链" width="720px">
      <div v-if="fieldHist" class="fh">
        <div class="fh-head">
          <b>{{ fieldHist.talent_name || fieldHist.talent_id }}</b>
          <span class="muted">·</span>
          <span>{{ fieldHist.field_label || fieldHist.field_code }}</span>
          <span class="muted xs mono">{{ fieldHist.field_code }}</span>
        </div>

        <div class="fh-current">
          当前值：<b>{{ dash(fieldHist.current_value) }}</b>
          <span class="muted xs">（共 {{ fieldHist.total }} 次变更）</span>
        </div>

        <el-alert
          v-if="fieldHist.total >= 1000"
          type="warning"
          :closable="false"
          show-icon
          class="mb12"
        >
          <template #title>已达字段历史扫描上限</template>
          <template #default>
            字段历史上限 <code class="mono">FIELD_HISTORY_LIMIT</code> = 1000 条，
            超出部分<b>按上限截断且可识别</b>。
          </template>
        </el-alert>

        <el-timeline class="mt12">
          <el-timeline-item
            v-for="c in fieldHist.items"
            :key="c.id"
            :timestamp="fmtDateTime(c.created_at)"
            placement="top"
            :type="sourceTimelineType(c.change_source)"
          >
            <div class="tl-body">
              <span class="before">{{ dash(c.before_value) }}</span>
              <el-icon class="arrow"><Right /></el-icon>
              <span class="after">{{ dash(c.after_value) }}</span>
              <StatusTag :text="c.change_source_name" :tone="sourceTone(c.change_source)" />
              <span class="muted xs">{{ c.operator_name || '系统' }}</span>
            </div>
          </el-timeline-item>
        </el-timeline>
        <div v-if="!fieldHist.items.length" class="muted sm">该字段没有变更记录。</div>
      </div>
      <div v-else class="muted sm">加载中…</div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-20 · 档案变更历史（`/talent/change-log`，F-3.10，**API-67 ~ API-70**）
 *
 * 📌 本页是 CU-23 **新增的第 20 页**：原型 `p01~p19` 没有独立「变更历史」页
 *    （原型 P-8 把时间线内嵌在详情里），但架构 §4.2 第 16 行列了独立路由，
 *    且后端 API-67 ~ API-70 已成体系，故本批补出。
 *
 * 🔴 五条契约（`api/change.ts` 已钉死）：
 *   1. **快照是「确定性重放」不是备份** —— 未追踪字段按声明处理（`untracked_fields`）、
 *      脏历史值降级为 `null`（不抛错）。故页面**必须能渲染 `null`**，
 *      并显示「未追踪」字段清单。
 *   2. **`at` 早于建档时间 → `40001`**（非法参数），**不是** `40401`。
 *   3. **看快照本身敏感**：后端写审计 `CHANGE_SNAPSHOT_VIEW` —— UI 须提示「已留痕」。
 *   4. **`history_truncated=true` 触达扫描上限** —— **按上限截断且可识别**，
 *      **不是静默截断**；页面必须显示「历史过长，仅回溯最近 N 条」。
 *   5. **窗口超限 → `40001`**；**`start > end` → `40001`**（不是空结果）。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { changeApi, type ChangeLogItem, type FieldHistoryOut, type SnapshotOut } from '@/api/change'
import { CHANGE_SOURCE } from '@/api/enums'
import { ApiError } from '@/api/http'
import { dash } from '@/api/types'
import { fmtDateTime } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import EnumSelect from '@/components/EnumSelect.vue'
import JsonViewer from '@/components/JsonViewer.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const router = useRouter()
const notify = useNotify()

const desc =
  '档案变更留痕（谁 / 何时 / 改了哪个字段 / 前值后值），并支持时间点回溯与单字段完整链路。'

/** 常用字段（英文 code）—— 后端只认 code，未知 code → 40001。 */
const FIELD_OPTIONS = [
  { value: 'name', label: '姓名 name' },
  { value: 'gender', label: '性别 gender' },
  { value: 'birth_date', label: '出生日期 birth_date' },
  { value: 'education_level', label: '学历 education_level' },
  { value: 'school', label: '学校 school' },
  { value: 'major', label: '专业 major' },
  { value: 'work_years', label: '工作年限 work_years' },
  { value: 'phone', label: '手机 phone' },
  { value: 'email', label: '邮箱 email' },
  { value: 'data_status', label: '数据状态 data_status' },
]

// ---------------- 列表 ----------------
const loading = ref(false)
const status = ref<LoadStatus>('idle')
const rows = ref<ChangeLogItem[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

const talentIdInput = ref('')
const fieldName = ref<string | null>(null)
const changeSource = ref<number | null>(null)
const range = ref<[string, string] | null>(null)

function shortId(id: string | null): string {
  if (!id) return '—'
  return id.length > 12 ? `…${id.slice(-10)}` : id
}

function sourceTone(code: number): 'primary' | 'success' | 'purple' | 'plain' {
  if (code === 0) return 'primary'
  if (code === 1) return 'success'
  if (code === 2) return 'purple'
  return 'plain'
}

function sourceTimelineType(code: number): 'primary' | 'success' | 'warning' | 'info' {
  if (code === 0) return 'primary'
  if (code === 1) return 'success'
  if (code === 2) return 'warning'
  return 'info'
}

async function load(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const [start, end] = range.value ?? [undefined, undefined]
    const res = await changeApi.list({
      page: page.value,
      per_page: perPage.value,
      talent_id: talentIdInput.value.trim() || undefined,
      field_name: fieldName.value ?? undefined,
      change_source: changeSource.value ?? undefined,
      start_time: start,
      end_time: end,
    })
    rows.value = res.data ?? []
    total.value = res.page?.total ?? rows.value.length
    if (res.page?.per_page) perPage.value = res.page.per_page
    traceId.value = res.traceId
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    rows.value = []
    total.value = 0
    errCode.value = err.code
    errMsg.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

function reload(): void {
  page.value = 1
  void load()
}
function onReset(): void {
  talentIdInput.value = ''
  fieldName.value = null
  changeSource.value = null
  range.value = null
  reload()
}
function onPageChange(p: number): void {
  page.value = p
  void load()
}
function onSizeChange(s: number): void {
  perPage.value = s
  page.value = 1
  void load()
}

function openTalent(id: string): void {
  void router.push({ name: 'talent-detail', params: { id } })
}

// ---------------- 时间点回溯（API-69） ----------------
const snapshotVisible = ref(false)
const snapTalentId = ref('')
const snapAt = ref<string | null>(null)
const snapLoading = ref(false)
const snap = ref<SnapshotOut | null>(null)
const snapError = ref<string | null>(null)
const snapErrorTitle = ref('回溯失败')

function openSnapshotFor(talentId: string): void {
  snapTalentId.value = talentId
  snap.value = null
  snapError.value = null
  snapshotVisible.value = true
}

async function runSnapshot(): Promise<void> {
  const id = snapTalentId.value.trim()
  if (!/^\d+$/.test(id)) {
    notify.warn('档案 ID 必须是纯数字字符串（雪花 ID）')
    return
  }
  if (!snapAt.value) {
    notify.warn('请选择回溯时间点')
    return
  }
  snapLoading.value = true
  snap.value = null
  snapError.value = null
  try {
    const res = await changeApi.snapshot(id, snapAt.value)
    snap.value = res.data
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    // 🔴 40001 = 非法参数（早于建档 / 窗口超限），必须与「找不到」区分
    if (err.code === 40001) {
      snapErrorTitle.value = '回溯时间不合法'
      snapError.value =
        '所选时间早于该档案的建档时间，或时间窗口超出限制。回溯是「非法参数」而不是「找不到记录」，请调整时间后重试。'
    } else if (err.isNotFound) {
      snapErrorTitle.value = '档案不存在'
      snapError.value = '该档案不存在或已被软删（40401）。'
    } else if (err.isForbidden) {
      snapErrorTitle.value = '无访问权限'
      snapError.value = '当前角色无权查看该档案的历史快照。'
    } else {
      snapErrorTitle.value = '回溯失败'
      snapError.value = err.displayMessage
    }
  } finally {
    snapLoading.value = false
  }
}

// ---------------- 字段完整链（API-70） ----------------
const fieldVisible = ref(false)
const fieldHist = ref<FieldHistoryOut | null>(null)

async function openFieldHistory(row: ChangeLogItem): Promise<void> {
  if (!row.field_name) {
    notify.warn('该变更记录没有字段名（如归档 / 合并等整体动作），无法查看字段链路。')
    return
  }
  fieldVisible.value = true
  fieldHist.value = null
  try {
    const res = await changeApi.fieldHistory(row.talent_id, row.field_name)
    fieldHist.value = res.data
  } catch (e) {
    fieldVisible.value = false
    notify.fail(e, '读取字段变更链失败')
  }
}

onMounted(load)
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
.diff,
.tl-body {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12.5px;
}
.before {
  color: var(--txt-3);
  text-decoration: line-through;
  word-break: break-all;
}
.after {
  color: var(--primary);
  font-weight: 500;
  word-break: break-all;
}
.arrow {
  color: var(--txt-3);
  font-size: 12px;
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
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.snap-form {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.snap-meta {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--txt-2);
}
.snap-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.snap-table td {
  border: 1px solid var(--line);
  padding: 7px 10px;
}
.snap-table td.k {
  width: 100px;
  color: var(--txt-3);
  background: var(--panel-2, #fafbfd);
  font-size: 11.5px;
}
.tag-wrap {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.fh-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.fh-current {
  font-size: 12.5px;
  color: var(--txt-2);
}
.nowrap {
  white-space: nowrap;
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
.mb12 {
  margin-bottom: 12px;
}
</style>
