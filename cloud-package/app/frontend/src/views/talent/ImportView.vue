<template>
  <div>
    <PageHeader
      title="批量导入与任务进度"
      desc="支持 PDF / Word / 文本 / 图片（图片走 OCR 占位，会转人工介入）。单批 ≤100 份、单文件 ≤50MB。"
    >
      <template #actions>
        <el-button :loading="tasks.loading.value" @click="tasks.load()">
          <el-icon><Refresh /></el-icon>刷新任务
        </el-button>
      </template>
    </PageHeader>

    <div class="grid g-1-2">
      <!-- ============ 上传区 ============ -->
      <div>
        <el-card shadow="never" class="mb12">
          <template #header><span class="hd"><i class="bar" />创建导入任务</span></template>

          <el-upload
            ref="uploadRef"
            drag
            multiple
            :auto-upload="false"
            :file-list="fileList"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :limit="IMPORT_LIMITS.MAX_FILES"
            accept=".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg"
          >
            <el-icon class="up-ico"><UploadFilled /></el-icon>
            <b>拖拽简历到此处，或点击选择</b>
            <p>单批最多 {{ IMPORT_LIMITS.MAX_FILES }} 份 · 单文件 ≤ {{ IMPORT_LIMITS.MAX_FILE_SIZE_MB }}MB</p>
          </el-upload>

          <el-alert
            v-if="precheckMsg"
            :type="precheckOk ? 'success' : 'warning'"
            :closable="false"
            show-icon
            class="mt12"
          >
            {{ precheckMsg }}
          </el-alert>

          <div class="opts mt12">
            <el-checkbox v-model="form.auto_dedup">自动查重</el-checkbox>
            <el-checkbox v-model="form.auto_tag">自动打标</el-checkbox>
            <el-select v-model="form.parse_strategy" style="width: 168px">
              <el-option label="自动策略（推荐）" value="auto" />
              <el-option label="强制 OCR" value="ocr_force" />
              <el-option label="仅文本层" value="text_only" />
            </el-select>
          </div>
          <el-input
            v-model="form.remark"
            class="mt8"
            placeholder="备注（可选，如「2026 秋招第二批」）"
            maxlength="128"
            show-word-limit
          />

          <el-button
            class="mt12"
            type="primary"
            :loading="submitting"
            :disabled="!fileList.length"
            @click="submit"
          >
            开始导入（{{ fileList.length }} 份）
          </el-button>
          <div class="muted xs mt8">
            🔴 本接口**强制幂等**：重复点击不会产生第二个任务（重放返回首次结果，按成功处理）。
          </div>
        </el-card>

        <!-- ============ 质量报告 ============ -->
        <el-card v-if="currentTaskId" shadow="never">
          <template #header>
            <div class="head">
              <span class="hd"><i class="bar" />数据质量报告</span>
              <el-button size="small" :loading="report.loading.value" @click="loadReport">加载</el-button>
            </div>
          </template>
          <StatePanel
            :status="report.status.value"
            :loading="report.loading.value"
            :code="report.error.value?.code ?? null"
            :message="report.error.value?.message ?? null"
            :trace-id="report.traceId.value"
            :rows="4"
            empty-title="报告尚未生成"
            empty-hint="任务解析完成后可查看质量报告"
            @retry="loadReport"
          >
            <template v-if="report.data.value">
              <el-alert type="info" :closable="false" show-icon class="mb12">
                {{ report.data.value.accuracy_note }}
              </el-alert>
              <div class="grid g2">
                <MetricCard
                  v-for="f in report.data.value.field_completeness.slice(0, 4)"
                  :key="f.field_code"
                  :label="`${f.field_name}完整率`"
                  :value="fmtRate(f.rate)"
                  :sub="`${f.filled} / ${f.total}`"
                />
              </div>
              <el-divider />
              <div class="hd xs"><i class="bar" />失败原因分布</div>
              <el-table :data="report.data.value.failure_reasons" size="small" class="mt8" empty-text="无失败文件">
                <el-table-column prop="reason_name" label="原因" min-width="180" />
                <el-table-column prop="reason_code" label="码" width="140" class-name="mono" />
                <el-table-column prop="count" label="数量" width="90" align="right" />
              </el-table>
              <el-button class="mt12" size="small" @click="exportReport">导出报告（CSV）</el-button>
            </template>
          </StatePanel>
        </el-card>
      </div>

      <!-- ============ 任务列表 + 进度 + 文件 ============ -->
      <div>
        <el-card shadow="never" class="mb12">
          <template #header>
            <div class="head">
              <span class="hd"><i class="bar" />导入任务
                <span class="sub">共 {{ tasks.total.value }} 个</span>
              </span>
            </div>
          </template>

          <StatePanel
            :status="tasks.status.value === 'ready' && tasks.isEmpty.value ? 'empty' : tasks.status.value"
            :loading="tasks.loading.value"
            :code="tasks.error.value?.code ?? null"
            :message="tasks.error.value?.message ?? null"
            :trace-id="tasks.traceId.value"
            :rows="4"
            empty-title="还没有导入任务"
            empty-hint="上传简历文件后，任务会出现在这里"
            @retry="tasks.load()"
          >
            <el-table
              :data="tasks.rows.value"
              size="small"
              highlight-current-row
              :current-row-key="currentTaskId ?? undefined"
              row-key="task_id"
              @current-change="onTaskSelect"
            >
              <el-table-column label="任务号" min-width="160">
                <template #default="{ row }">
                  <span class="mono">{{ row.task_no }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <StatusTag :text="row.status_name" :tone="taskTone(row.status)" />
                </template>
              </el-table-column>
              <el-table-column label="进度" width="150">
                <template #default="{ row }">
                  <el-progress
                    :percentage="row.progress_pct"
                    :status="row.status === 4 ? 'exception' : row.status === 2 ? 'success' : undefined"
                    :stroke-width="10"
                  />
                </template>
              </el-table-column>
              <el-table-column label="成功/失败" width="110" align="right">
                <template #default="{ row }">
                  <span class="ok">{{ row.success_count }}</span> /
                  <span class="danger">{{ row.fail_count }}</span>
                </template>
              </el-table-column>
              <el-table-column label="待复核" width="90" align="right">
                <template #default="{ row }">{{ row.review_pending_count }}</template>
              </el-table-column>
              <el-table-column label="创建时间" width="140">
                <template #default="{ row }">
                  <span class="muted nowrap">{{ fmtDateTime(row.created_at) }}</span>
                </template>
              </el-table-column>
            </el-table>

            <div class="pager">
              <el-pagination
                background
                layout="total, prev, pager, next"
                :total="tasks.total.value"
                :current-page="tasks.page.value"
                :page-size="tasks.perPage.value"
                @current-change="tasks.onPageChange"
              />
            </div>
          </StatePanel>
        </el-card>

        <!-- ============ 实时进度（SSE） ============ -->
        <el-card v-if="currentTaskId" shadow="never" class="mb12">
          <template #header>
            <div class="head">
              <span class="hd"><i class="bar" />实时进度（SSE）
                <span class="sub">任务 {{ currentTaskId }}</span>
              </span>
              <div>
                <el-button size="small" :disabled="sseRunning" @click="startSse">开始监听</el-button>
                <el-button size="small" :disabled="!sseRunning" @click="stopSse">停止</el-button>
              </div>
            </div>
          </template>

          <el-alert v-if="sseError" type="error" :closable="false" show-icon class="mb12">
            {{ sseError }}
          </el-alert>

          <div v-if="sseEvent" class="progress-box">
            <div class="row">
              <StatusTag
                :text="sseEvent.status_name"
                :tone="sseEvent.status === 2 ? 'success' : sseEvent.status === 4 ? 'danger' : 'primary'"
              />
              <span class="mono">{{ sseEvent.done }} / {{ sseEvent.total }}</span>
              <span class="grow" />
              <b>{{ sseEvent.progress_pct }}%</b>
            </div>
            <el-progress
              class="mt8"
              :percentage="sseEvent.progress_pct"
              :status="sseEvent.status === 4 ? 'exception' : sseEvent.status === 2 ? 'success' : undefined"
              :stroke-width="12"
            />
          </div>
          <el-empty v-else description="点击「开始监听」建立 SSE 连接" :image-size="60" />

          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="mt12"
            title="SSE 握手期即校验权限：任务不存在 / 越权会在建立连接时直接返回错误，不会藏在事件流里（CR-8 口径）。"
          />

          <el-button class="mt12" size="small" :loading="retrying" @click="retryFailed">
            <el-icon><RefreshRight /></el-icon>重试失败文件
          </el-button>
        </el-card>

        <!-- ============ 文件明细 ============ -->
        <el-card v-if="currentTaskId" shadow="never">
          <template #header>
            <div class="head">
              <span class="hd"><i class="bar" />文件明细
                <span class="sub">共 {{ files.total.value }} 份</span>
              </span>
              <EnumSelect v-model="fileStatus" :options="IMPORT_FILE_PARSE_STATUS" placeholder="全部" />
            </div>
          </template>

          <StatePanel
            :status="files.status.value === 'ready' && files.isEmpty.value ? 'empty' : files.status.value"
            :loading="files.loading.value"
            :code="files.error.value?.code ?? null"
            :trace-id="files.traceId.value"
            :rows="4"
            empty-title="暂无文件记录"
            @retry="files.load()"
          >
            <el-table :data="files.rows.value" size="small">
              <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip />
              <el-table-column label="大小" width="90">
                <template #default="{ row }">{{ fmtBytes(row.file_size) }}</template>
              </el-table-column>
              <el-table-column label="解析" width="100">
                <template #default="{ row }">
                  <StatusTag
                    :text="row.parse_status_name"
                    :tone="row.parse_status === 1 ? 'success' : row.parse_status === 2 ? 'danger' : 'plain'"
                  />
                </template>
              </el-table-column>
              <el-table-column label="OCR" width="70">
                <template #default="{ row }">
                  <el-tag v-if="row.ocr_used" size="small" type="warning">是</el-tag>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="失败原因" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">
                  <span class="muted">{{ reasonLabel(row.parse_error) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="90">
                <template #default="{ row }">
                  <div class="ops">
                    <a v-if="row.talent_id" @click="openTalent(row.talent_id)">查看档案</a>
                    <span v-else class="muted">—</span>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <div class="pager">
              <el-pagination
                background
                layout="total, prev, pager, next"
                :total="files.total.value"
                :current-page="files.page.value"
                :page-size="files.perPage.value"
                @current-change="files.onPageChange"
              />
            </div>
          </StatePanel>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * P-9 · 批量导入与任务进度（`/talent/import`，F-3.1 / F-3.8，API-36 ~ API-43）
 *
 * 🔴 七条口径：
 *   1. **上传限制以前端为「前置提示」、后端为「唯一防线」** —— 前端 `precheck` 只
 *      降低无效请求；`41301`（超限）/ `41501`（格式）必须能正常渲染，不能被前端拦掉就以为不会有。
 *   2. **API-36 强制幂等**：`Idempotency-Key` 由 `http.upload` 注入（一次提交一个键），
 *      重放返回 `40903` + 首次结果，前端**按成功处理**（不弹错）。
 *   3. **SSE 用 `fetch` 流**（`EventSource` 无法带 `Authorization`）—— 见 `api/sse.ts`。
 *   4. **SSE 握手期校验权限**：任务不存在 / 越权在**建连时**返回，不在事件流里。
 *   5. **终态（2/3/4）服务端主动关流** —— 前端不需要心跳或超时兜底逻辑。
 *   6. **`dup_pair_count` 在 CU-19 落地前恒为 0**（契约先占位，防前端字段缺列）；
 *      当前后端已支持，但字段可能为 0 表示「本批未产生重复对」而非「未实现」。
 *   7. **质量报告含 `accuracy_note`** —— 金标准集未定稿前，报告里的**完整率不是验收值**，
 *      必须把口径说明原样展示给用户。
 */
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type UploadFile, type UploadInstance, type UploadRawFile, type UploadUserFile } from 'element-plus'

import { ApiError } from '@/api/http'
import { IMPORT_FAILURE_REASON, IMPORT_FILE_PARSE_STATUS } from '@/api/enums'
import {
  IMPORT_LIMITS,
  importApi,
  type ImportFileItem,
  type ImportProgressEvent,
  type ImportReport,
  type ImportTask,
} from '@/api/importer'
import { openSse } from '@/api/sse'
import { dash } from '@/api/types'
import EnumSelect from '@/components/EnumSelect.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useNotify } from '@/composables/useNotify'
import { usePagedTable } from '@/composables/usePagedTable'
import { fmtBytes, fmtDateTime, fmtRate, saveTextFile } from '@/utils/format'

const router = useRouter()
const notify = useNotify()

// ---------------- 上传 ----------------
const uploadRef = ref<UploadInstance>()
const fileList = ref<UploadUserFile[]>([])
const form = ref({
  auto_dedup: true,
  auto_tag: true,
  parse_strategy: 'auto' as 'auto' | 'ocr_force' | 'text_only',
  remark: '',
})
const submitting = ref(false)
const precheckMsg = ref('')
const precheckOk = ref(true)

function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i === -1 ? '' : name.slice(i).toLowerCase()
}

/** 前端**前置**校验（降低无效请求；不是唯一防线）。 */
function precheck(): boolean {
  const okExt = new Set<string>(IMPORT_LIMITS.ALLOWED_EXT as readonly string[])
  const badExt = fileList.value.filter((f) => !okExt.has(extOf(f.name)))
  const tooBig = fileList.value.filter((f) => (f.size ?? 0) > IMPORT_LIMITS.MAX_FILE_SIZE_MB * 1024 * 1024)
  const names = fileList.value.map((f) => f.name)
  const dup = names.filter((n, i) => names.indexOf(n) !== i)

  if (badExt.length) {
    precheckOk.value = false
    precheckMsg.value = `格式不支持：${badExt.map((f) => f.name).join('、')}（支持 ${IMPORT_LIMITS.ALLOWED_EXT.join(' / ')}）`
    return false
  }
  if (tooBig.length) {
    precheckOk.value = false
    precheckMsg.value = `超出单文件上限：${tooBig.map((f) => f.name).join('、')}`
    return false
  }
  precheckOk.value = true
  precheckMsg.value = dup.length
    ? `检测到 ${dup.length} 个同名文件（后端会做批内去重，可继续）`
    : `已选择 ${fileList.value.length} 份文件，可以开始导入`
  return true
}

function onFileChange(_file: UploadFile, list: UploadUserFile[]): void {
  fileList.value = list
  precheck()
}
function onFileRemove(_file: UploadFile, list: UploadUserFile[]): void {
  fileList.value = list
  precheck()
}

async function submit(): Promise<void> {
  if (!fileList.value.length) return
  if (!precheck()) return
  // 🔴 类型谓词必须写 `UploadRawFile`（= `File & { uid: number }`），**不能写 `File`**：
  //    `UploadUserFile.raw` 的静态类型是 `UploadRawFile | undefined`，
  //    谓词类型必须是参数类型的**子类型**，写 `File` 会报 TS2677（`uid` 缺失）。
  //    而 `UploadRawFile` 本身 extends `File`，故过滤后可直接喂给 `files: File[]`。
  const files = fileList.value.map((f) => f.raw).filter((f): f is UploadRawFile => !!f)
  submitting.value = true
  try {
    const res = await importApi.create({
      files,
      auto_dedup: form.value.auto_dedup,
      auto_tag: form.value.auto_tag,
      parse_strategy: form.value.parse_strategy,
      remark: form.value.remark || undefined,
    })
    const p = res.data
    if (res.code === 40903) {
      // 幂等回放：首次结果，按成功处理
      notify.info(`该提交已处理过，返回首次任务 ${p?.task_no ?? ''}`)
    } else {
      notify.ok(`任务已创建：${p?.task_no ?? ''}（共 ${p?.total_count ?? 0} 份）`)
    }
    if (p?.warn) notify.warn(p.warn)
    uploadRef.value?.clearFiles()
    fileList.value = []
    precheckMsg.value = ''
    await tasks.load()
    if (p?.task_id) {
      currentTaskId.value = String(p.task_id)
      startSse()
    }
  } catch (e) {
    if (e instanceof ApiError && e.code === 41301) notify.warn('文件超出上限，请拆批后重试')
    else if (e instanceof ApiError && e.code === 41501) notify.warn('存在不支持的文件格式')
    else notify.fail(e, '创建导入任务失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 任务列表 ----------------
const currentTaskId = ref<string | null>(null)

const tasks = usePagedTable<ImportTask, Record<string, unknown>>(
  (params) => importApi.list(params as { status?: number; page?: number; per_page?: number }),
  {},
  { perPage: 20 },
)

/**
 * 失败原因：原因码 → 中文展示名。
 * ⚠️ 未登记的原因码**原样显示** —— 后端异常兜底路径写的 `ValueError: xxx` 原文是唯一线索，
 * 用「未知错误」覆盖它反而让人查不下去（唯一事实见 `@/api/enums` 的常量表注释）。
 */
function reasonLabel(code: string | null | undefined): string {
  if (!code) return dash(code)
  return IMPORT_FAILURE_REASON[code] ?? code
}

function taskTone(status: number): 'success' | 'warning' | 'danger' | 'primary' | 'plain' {
  if (status === 2) return 'success'
  if (status === 3) return 'warning'
  if (status === 4) return 'danger'
  if (status === 1) return 'primary'
  return 'plain'
}

function onTaskSelect(row: ImportTask | null): void {
  if (!row) return
  // 🔴 保持字符串：task_id 是雪花 ID
  currentTaskId.value = String(row.task_id)
  stopSse()
  sseEvent.value = null
  sseError.value = ''
  files.search()
}

// ---------------- 文件明细 ----------------
const fileStatus = ref<number | null>(null)
const files = usePagedTable<ImportFileItem, Record<string, unknown>>(
  (params) => {
    const p = { ...params } as Record<string, unknown>
    if (fileStatus.value !== null) p.parse_status = fileStatus.value
    return importApi.files(currentTaskId.value as string, p as { parse_status?: number; page?: number; per_page?: number })
  },
  {},
  { perPage: 50, immediate: false },
)

function openTalent(talentId: string): void {
  void router.push({ name: 'talent-detail', params: { id: talentId } })
}

// ---------------- SSE 进度 ----------------
const sseEvent = ref<ImportProgressEvent | null>(null)
const sseError = ref('')
const sseRunning = ref(false)
let controller: AbortController | null = null

function startSse(): void {
  if (!currentTaskId.value || sseRunning.value) return
  sseError.value = ''
  sseRunning.value = true
  // 🔴 **连接代际守卫**：每次连接持有自己的 controller，`isStale()` 判断它是否已被
  //    新一轮（或 `stopSse()`）取代。没有它的话，上一轮 `abort()` 后**异步**到达的
  //    `onClose` / `onError` 会回过头改写**新连接**的状态 ——
  //    表现为「明明在监听，按钮却显示未监听」，或「刚清空的错误又被写回来」。
  const ctrl = new AbortController()
  controller = ctrl
  const isStale = (): boolean => controller !== ctrl
  void openSse(
    importApi.progressPath(currentTaskId.value),
    {
      onEvent: (evt) => {
        if (isStale()) return
        try {
          const payload = JSON.parse(evt.data) as ImportProgressEvent
          if (evt.event === 'error') {
            sseError.value = (payload as unknown as { message?: string }).message || '进度读取失败'
            return
          }
          sseEvent.value = payload
          // 🔴 行内进度同步（批次 24）：解析中就让任务列表的进度条实时走 ——
          //    此前只有 done（终态）才 `tasks.load()`，列表行会冻结在创建瞬间的
          //    「解析中 0%」直到终态（用户报障「进度条不出现」）。done/total 是
          //    文件级聚合，success/fail 计数仍以终态整表刷新为准，这里只改
          //    展示字段（status / status_name / progress_pct）。
          const row = tasks.rows.value.find((r) => String(r.task_id) === payload.task_id)
          if (row) {
            row.status = payload.status
            row.status_name = payload.status_name
            row.progress_pct = payload.progress_pct
          }
          if (evt.event === 'done') {
            sseRunning.value = false
            void tasks.load()
            void files.load()
          }
        } catch {
          sseError.value = '进度事件解析失败'
        }
      },
      onClose: () => {
        if (isStale()) return
        sseRunning.value = false
      },
      onError: (err) => {
        if (isStale()) return
        sseError.value = err.message
        sseRunning.value = false
      },
    },
    { signal: ctrl.signal },
  )
}

function stopSse(): void {
  controller?.abort()
  controller = null
  sseRunning.value = false
}

onBeforeUnmount(() => stopSse())

// ---------------- 质量报告 ----------------
const report = useAsyncData<ImportReport>(() => importApi.report(currentTaskId.value as string), {
  immediate: false,
})
function loadReport(): void {
  if (!currentTaskId.value) return
  void report.run()
}

async function exportReport(): Promise<void> {
  if (!currentTaskId.value) return
  try {
    const res = await importApi.exportReport(currentTaskId.value)
    if (!res.data) {
      notify.warn('导出未返回内容')
      return
    }
    saveTextFile(res.data.content, res.data.filename)
    notify.ok(`已导出 ${res.data.row_count} 行`)
  } catch (e) {
    notify.fail(e, '导出报告失败')
  }
}

// ---------------- 重试失败文件 ----------------
const retrying = ref(false)
async function retryFailed(): Promise<void> {
  if (!currentTaskId.value) return
  retrying.value = true
  try {
    const res = await importApi.retry(currentTaskId.value)
    const p = res.data
    if (p) {
      notify.ok(`已重试 ${p.retry_count} 个文件（可重试 ${p.retryable_count} 个）`)
      if (p.message) notify.info(p.message)
    }
    await tasks.load()
    await files.load()
  } catch (e) {
    notify.fail(e, '重试失败')
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.head .hd {
  font-size: 13.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.head .bar {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: var(--primary);
}
.head .sub {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--txt-3);
}
.up-ico {
  font-size: 30px;
  color: var(--primary);
  opacity: 0.75;
}
.opts {
  display: flex;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
}
.progress-box {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px;
  background: var(--panel);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
.ops a {
  cursor: pointer;
  color: var(--primary);
  font-size: 12.5px;
}
.ok {
  color: var(--ok);
  font-weight: 600;
}
.danger {
  color: var(--danger);
  font-weight: 600;
}
.mono {
  font-family: var(--mono);
  font-size: 12px;
}
</style>
