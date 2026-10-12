<template>
  <div>
    <PageHeader title="我的画像 / 个人权利" :desc="desc">
      <template #actions>
        <el-button :disabled="loading" @click="loadProfile">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 四条口径前置 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        <b>可更正字段白名单由后端下发</b>（<code class="mono">correction_field_codes</code>）——
        页面不会自建白名单（排除派生字段与唯一键的规则在后端）。
        提交更正时后端用 <code class="mono">current_value</code> 做<b>乐观锁</b>：
        若库中最新值已变 → <code class="mono">40902</code>，需刷新后重新提交。
      </template>
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        一期数据包仅支持 <code class="mono">JSON</code> / <code class="mono">CSV</code>，
        <b>不支持 PDF</b>（不谎称）。导出申请<b>提交即完成</b>（无审批环节），
        因此导出类请求的处理时长恒为 0。
      </template>
    </el-alert>

    <el-tabs v-model="tab">
      <!-- ==================== 我的档案 ==================== -->
      <el-tab-pane label="我的档案" name="profile">
        <DegradedBadge :degraded="degraded" :reason="degradedReason" />

        <StatePanel
          :status="status"
          :loading="loading"
          :code="errCode"
          :message="errMsg"
          :trace-id="traceId"
          :rows="6"
          empty-title="没有你的档案"
          empty-hint="可能尚未建档，或你的账号未与人才档案关联"
          @retry="loadProfile"
        >
          <template v-if="profile">
            <!-- 概要 -->
            <el-card shadow="never" class="mb12">
              <div class="summary">
                <div class="head">
                  <span class="avatar">{{ (profile.name || '?').slice(0, 1) }}</span>
                  <div>
                    <div class="name">
                      {{ dash(profile.name) }}
                      <StatusTag :text="profile.data_status_name" tone="plain" />
                      <span class="muted xs">v{{ profile.version_no ?? '—' }}</span>
                    </div>
                    <div class="sub muted sm">
                      {{ dash(profile.school) }} · {{ dash(profile.major) }} ·
                      {{ dash(profile.education_level) }} ·
                      {{ profile.work_years === null ? '—' : `${profile.work_years} 年` }}
                    </div>
                  </div>
                </div>
                <div class="contact">
                  <div><span class="k">手机</span>{{ dash(profile.phone) }}</div>
                  <div><span class="k">邮箱</span>{{ dash(profile.email) }}</div>
                  <div>
                    <span class="k">身份证</span>
                    <!-- 🔴 只下发脱敏形态；明文不经接口 -->
                    <span class="masked" title="服务端只下发脱敏形态">
                      {{ profile.id_card_masked ? `🔒 ${profile.id_card_masked}` : '—' }}
                    </span>
                  </div>
                </div>
              </div>

              <div class="tags-row">
                <span class="muted xs">标签：</span>
                <el-tag v-for="t in profile.tags" :key="t.tag_code" size="small" type="info" effect="light">
                  {{ t.tag_name || t.tag_code }}
                </el-tag>
                <span v-if="!profile.tags.length" class="muted xs">—</span>
              </div>

              <div v-if="profile.pending_corrections?.length" class="mt12">
                <el-alert type="warning" :closable="false" show-icon>
                  <template #title>
                    你有 {{ profile.pending_corrections.length }} 条更正申请正在处理中
                  </template>
                  <template #default>
                    处理期间可对<b>其它字段</b>继续提交，同一字段请勿重复提交。
                  </template>
                </el-alert>
              </div>
            </el-card>

            <!-- 字段明细（可更正字段带「申请更正」入口） -->
            <el-card shadow="never">
              <template #header>
                <div class="card-head">
                  <span class="hd">
                    <i class="bar" />字段明细
                    <span class="sub">{{ profile.fields.length }} 个字段</span>
                  </span>
                  <span class="muted xs">
                    「可更正」由后端白名单判定；不可更正字段悬停可见原因
                  </span>
                </div>
              </template>

              <el-table :data="profile.fields" stripe row-key="field_code">
                <el-table-column label="字段" min-width="150">
                  <template #default="{ row }">
                    {{ row.field_label || row.field_code }}
                    <div class="muted xs mono">{{ row.field_code }}</div>
                  </template>
                </el-table-column>

                <el-table-column label="当前值" min-width="200">
                  <template #default="{ row }">
                    <span class="val">{{ dash(row.value) }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="来源" width="110">
                  <template #default="{ row }">
                    <span class="xs muted">{{ row.source_name || '—' }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="置信度" width="100" align="right">
                  <template #default="{ row }">
                    <span v-if="row.confidence === null" class="muted xs">规则命中</span>
                    <span v-else class="num xs">{{ (row.confidence * 100).toFixed(0) }}%</span>
                  </template>
                </el-table-column>

                <el-table-column label="复核状态" width="110">
                  <template #default="{ row }">
                    <StatusTag :text="row.review_status_name" tone="plain" />
                  </template>
                </el-table-column>

                <el-table-column label="来源文件" min-width="150">
                  <template #default="{ row }">
                    <span class="muted xs ellipsis" :title="row.source_file || ''">
                      {{ dash(row.source_file) }}
                    </span>
                  </template>
                </el-table-column>

                <el-table-column label="操作" width="130" fixed="right">
                  <template #default="{ row }">
                    <el-tooltip
                      v-if="!canCorrect(row.field_code)"
                      :content="excludeReason(row.field_code)"
                      placement="left"
                    >
                      <span class="muted xs">不可更正</span>
                    </el-tooltip>
                    <a v-else @click="openCorrection(row)">申请更正</a>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </template>
        </StatePanel>
      </el-tab-pane>

      <!-- ==================== 我的申请 ==================== -->
      <el-tab-pane label="我的申请与数据包" name="requests">
        <el-card shadow="never" class="mb12">
          <template #header>
            <div class="card-head">
              <span class="hd"><i class="bar" />发起权利请求</span>
              <span class="muted xs">
                对外只显示三态：待处理 / 已通过 / 已驳回（内部「处理中」由后端映射，不外露）
              </span>
            </div>
          </template>

          <div class="req-actions">
            <el-button v-perm="'privacy:delete'" @click="openDeletion">
              <el-icon><Delete /></el-icon>申请删除我的档案
            </el-button>
            <el-button v-perm="'privacy:export'" @click="openExport">
              <el-icon><Download /></el-icon>申请导出个人数据包
            </el-button>
            <span class="muted xs">
              删除为不可逆动作（审批后软删 + 向量清理）；导出提交即完成，可直接下载。
            </span>
          </div>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span class="hd"><i class="bar" />我的请求记录</span>
              <el-button size="small" :disabled="reqLoading" @click="loadMyRequests">刷新</el-button>
            </div>
          </template>

          <el-alert type="info" :closable="false" show-icon class="mb12">
            <template #title>
              若当前角色没有 <code class="mono">privacy:list</code>，这里只会显示「读取不到」而不是空白 ——
              避免让人误以为「我提交的申请丢了」。
            </template>
          </el-alert>

          <el-table :data="myRequests" v-loading="reqLoading" stripe row-key="id">
            <el-table-column label="请求号" width="170">
              <template #default="{ row }">
                <span class="mono xs">{{ row.request_no }}</span>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="90">
              <template #default="{ row }">
                <StatusTag :text="row.request_type_name" tone="primary" />
              </template>
            </el-table-column>
            <el-table-column label="字段" width="130">
              <template #default="{ row }">
                {{ row.field_label || row.field_code || '—' }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="reqStatusTone(row.status)" />
              </template>
            </el-table-column>
            <el-table-column label="提交时间" width="150">
              <template #default="{ row }">
                <span class="muted xs">{{ fmtDateTime(row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="处理结果 / 驳回理由" min-width="200">
              <template #default="{ row }">
                <span v-if="row.reject_reason" class="danger sm">{{ row.reject_reason }}</span>
                <span v-else-if="row.processed_at" class="muted xs">
                  已于 {{ fmtDateTime(row.processed_at) }} 处理
                </span>
                <span v-else class="muted xs">
                  处理时限 {{ row.sla_hours }} 小时
                  <el-tag v-if="row.overdue" type="danger" size="small" effect="light">已逾期</el-tag>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <a
                  v-if="row.request_type === 2 && row.status === 1"
                  @click="downloadPackage(row.id)"
                >
                  下载数据包
                </a>
                <span v-else class="muted xs">—</span>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination
              background
              layout="total, prev, pager, next"
              :total="reqTotal"
              :current-page="reqPage"
              :page-size="reqPerPage"
              @current-change="onReqPageChange"
            />
          </div>
          <TraceFooter :trace-id="reqTraceId" extra="下载数据包会留痕（download_count 自增）" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- ============ 申请更正 ============ -->
    <el-dialog v-model="correctionVisible" title="申请更正字段值" width="560px">
      <div v-if="correctField" class="form">
        <div class="row">
          <span class="k">字段</span>
          <span>{{ correctField.field_label || correctField.field_code }}</span>
        </div>
        <div class="row">
          <span class="k">当前值</span>
          <span class="muted">{{ dash(correctField.value) }}</span>
          <span class="muted xs">（作为乐观锁基准）</span>
        </div>
        <div class="row">
          <span class="k">期望值</span>
          <el-input v-model="correctForm.expected_value" placeholder="你希望改成什么" />
        </div>
        <div class="row">
          <span class="k">更正理由</span>
          <el-input
            v-model="correctForm.reason"
            type="textarea"
            :rows="3"
            placeholder="说明依据（如「证书上的日期是 2021-06」）"
          />
        </div>
        <div class="muted xs">
          无实际差异的更正会被后端拒绝（<code class="mono">current_value === expected_value</code>），
          这里也先在本地拦一道。
        </div>
      </div>
      <template #footer>
        <el-button @click="correctionVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCorrection">提交申请</el-button>
      </template>
    </el-dialog>

    <!-- ============ 申请删除 ============ -->
    <el-dialog v-model="deletionVisible" title="申请删除我的档案" width="560px">
      <el-alert type="error" :closable="false" show-icon class="mb12">
        <template #title>这是不可逆操作</template>
        <template #default>
          审批通过后：档案将被<b>软删</b>，并<b>清理向量</b>（顺序为先删向量、后软删 MySQL）——
          与常规「先软删后清理」刻意相反，用于保证「已删除即搜不到」。
        </template>
      </el-alert>
      <el-input
        v-model="deletionReason"
        type="textarea"
        :rows="3"
        placeholder="申请理由（必填）"
      />
      <template #footer>
        <el-button @click="deletionVisible = false">取消</el-button>
        <el-button type="danger" :loading="submitting" @click="submitDeletion">提交删除申请</el-button>
      </template>
    </el-dialog>

    <!-- ============ 申请导出 ============ -->
    <el-dialog v-model="exportVisible" title="申请导出个人数据包" width="480px">
      <el-alert type="info" :closable="false" show-icon class="mb12">
        <template #title>提交即完成</template>
        <template #default>
          导出<b>没有审批环节</b>：提交后状态直接为「已通过」，可立即下载。
          一期仅支持 JSON / CSV，<b>不支持 PDF</b>。
        </template>
      </el-alert>
      <div class="form">
        <div class="row">
          <span class="k">格式</span>
          <el-radio-group v-model="exportFormat">
            <el-radio v-for="f in EXPORT_FORMATS" :key="f.value" :value="f.value">
              {{ f.label }}
            </el-radio>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <el-button @click="exportVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitExport">提交并下载</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-17 · 我的画像 / 个人权利（`/my/profile`，F-3.11，**API-71 ~ API-80**）
 *
 * 🔴 本页承载八条契约（`api/privacy.ts` 已钉死）：
 *   1. **可更正字段白名单由后端下发**（`correction_field_codes`）—— 前端**不得自建**。
 *   2. **无实际差异的更正不该产生请求** —— `current_value === expected_value` 本地先拦。
 *   3. **`current_value` 是乐观锁** —— 与库中最新值不一致 → `40902`（防覆盖他人刚修好的值）。
 *   4. **驳回必须给理由**（在审批侧）。
 *   5. **SM-3 内部态不外露**：界面只有 `0 待处理 / 1 已通过 / 2 已驳回`。
 *   6. **API-77 是跨库五步编排** —— 前端只呈现 `talent_soft_deleted` / `vector_cleaned` /
 *      `vector_degraded` 三个结果。
 *   7. **API-78 提交即 `status=1`** —— 无审批，处理时长恒 0。
 *   8. **API-79 下载留痕**；**PDF 明确不支持**。
 */
import { onMounted, ref } from 'vue'

import { ApiError } from '@/api/http'
import {
  EXPORT_FORMATS,
  privacyApi,
  type PrivacyRequest,
  type SelfField,
  type SelfProfile,
} from '@/api/privacy'
import { dash } from '@/api/types'
import { fmtDateTime, saveTextFile } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import DegradedBadge from '@/components/DegradedBadge.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc =
  '查看本人档案（含字段来源与置信度），并行使更正 / 删除 / 导出等个人数据权利。'

const tab = ref('profile')

// ---------------- 本人档案（API-71） ----------------
const loading = ref(false)
const status = ref<LoadStatus>('idle')
const profile = ref<SelfProfile | null>(null)
const degraded = ref(false)
const degradedReason = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)
const traceId = ref<string | null>(null)

async function loadProfile(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const res = await privacyApi.myProfile()
    profile.value = res.data
    degraded.value = res.degraded
    degradedReason.value = res.degradedReason
    traceId.value = res.traceId
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    profile.value = null
    errCode.value = err.code
    errMsg.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

/** 🔴 白名单来自后端；前端不做任何「派生字段 / 唯一键」推断。 */
function canCorrect(fieldCode: string): boolean {
  const list = profile.value?.correction_field_codes
  if (!list) return false
  return list.includes(fieldCode)
}

function excludeReason(fieldCode: string): string {
  const m = profile.value?.correction_excluded_reason ?? {}
  return m[fieldCode] || '该字段由系统派生或属于唯一键，不接受更正申请。'
}

// ---------------- 更正（API-72） ----------------
const correctionVisible = ref(false)
const correctField = ref<SelfField | null>(null)
const correctForm = ref({ expected_value: '', reason: '' })
const submitting = ref(false)

function openCorrection(f: SelfField): void {
  correctField.value = f
  correctForm.value = { expected_value: '', reason: '' }
  correctionVisible.value = true
}

async function submitCorrection(): Promise<void> {
  const p = profile.value
  const f = correctField.value
  if (!p || !f) return

  const expected = correctForm.value.expected_value.trim()
  if (!expected) {
    notify.warn('期望值不能为空')
    return
  }
  // 🔴 口径 ②：无实际差异的更正不该产生请求（后端也会拒，这里先拦）
  if (expected === (f.value ?? '')) {
    notify.warn('期望值与当前值相同，未产生实际差异，无需提交更正申请。')
    return
  }
  if (!correctForm.value.reason.trim()) {
    notify.warn('请填写更正理由')
    return
  }
  // 🔴 口径：同一字段已有在处理中的申请 → 不重复提交
  if (f.review_field_id && p.pending_corrections?.includes(f.review_field_id)) {
    notify.warn('该字段已有更正申请正在处理中，请勿重复提交。')
    return
  }

  submitting.value = true
  try {
    const res = await privacyApi.submitCorrection({
      talent_id: p.talent_id,
      field_code: f.field_code,
      // 🔴 current_value 是乐观锁基准：用页面上「看到的那一版」
      current_value: f.value ?? '',
      expected_value: expected,
      reason: correctForm.value.reason.trim(),
    })
    notify.ok(
      `已提交（${res.data?.request_no ?? ''}），处理时限 ${res.data?.sla_hours ?? '—'} 小时。` +
        (res.data?.review_queue ? `已进入复核队列。` : ''),
    )
    correctionVisible.value = false
    await loadProfile()
    await loadMyRequests()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40902) {
      notify.warn('提交失败：该字段的值在你提交前已发生变化（乐观锁冲突）。已为你刷新，请基于最新值重新提交。')
      correctionVisible.value = false
      await loadProfile()
    } else {
      notify.fail(e, '提交更正申请失败')
    }
  } finally {
    submitting.value = false
  }
}

// ---------------- 删除（API-75 / API-77） ----------------
const deletionVisible = ref(false)
const deletionReason = ref('')

function openDeletion(): void {
  deletionReason.value = ''
  deletionVisible.value = true
}

async function submitDeletion(): Promise<void> {
  const p = profile.value
  if (!p) return
  if (!deletionReason.value.trim()) {
    notify.warn('请填写申请理由')
    return
  }
  submitting.value = true
  try {
    const res = await privacyApi.submitDeletion({
      talent_id: p.talent_id,
      reason: deletionReason.value.trim(),
    })
    notify.ok(
      `删除申请已提交（${res.data?.request_no ?? ''}）。审批人提示：${res.data?.approver_hint ?? '—'}`,
    )
    deletionVisible.value = false
    await loadMyRequests()
  } catch (e) {
    notify.fail(e, '提交删除申请失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 导出（API-78 / API-79） ----------------
const exportVisible = ref(false)
const exportFormat = ref<'json' | 'csv'>('json')

function openExport(): void {
  exportFormat.value = 'json'
  exportVisible.value = true
}

async function submitExport(): Promise<void> {
  const p = profile.value
  if (!p) return
  submitting.value = true
  try {
    const res = await privacyApi.submitExport({
      talent_id: p.talent_id,
      format: exportFormat.value,
    })
    const rid = res.data?.request_id
    notify.ok(`导出申请已提交并完成（${res.data?.request_no ?? ''}），正在下载…`)
    exportVisible.value = false
    if (rid) await downloadPackage(rid)
    await loadMyRequests()
  } catch (e) {
    notify.fail(e, '导出失败')
  } finally {
    submitting.value = false
  }
}

async function downloadPackage(requestId: string): Promise<void> {
  try {
    const res = await privacyApi.downloadExport(requestId)
    const payload = res.data
    if (!payload) {
      notify.warn('下载内容为空')
      return
    }
    saveTextFile(
      payload.content,
      payload.filename,
      payload.format === 'json' ? 'application/json;charset=utf-8' : 'text/csv;charset=utf-8',
    )
    notify.ok(`已下载（第 ${payload.download_count} 次下载，本次下载已留痕）`)
    await loadMyRequests()
  } catch (e) {
    notify.fail(e, '下载失败')
  }
}

// ---------------- 我的请求（API-73 / 76 / 80） ----------------
const myRequests = ref<PrivacyRequest[]>([])
const reqLoading = ref(false)
const reqTotal = ref(0)
const reqPage = ref(1)
const reqPerPage = ref(20)
const reqTraceId = ref<string | null>(null)

function reqStatusTone(code: number): 'success' | 'danger' | 'warning' {
  if (code === 1) return 'success'
  if (code === 2) return 'danger'
  return 'warning'
}

async function loadMyRequests(): Promise<void> {
  reqLoading.value = true
  try {
    // 走统一台账（API-80）。无 `privacy:list` 时后端 40301 → 显示「读取不到」而非空白。
    const res = await privacyApi.ledger({ page: reqPage.value, per_page: reqPerPage.value })
    myRequests.value = res.data?.items ?? []
    reqTotal.value = res.data?.page?.total ?? res.page?.total ?? myRequests.value.length
    reqTraceId.value = res.traceId
  } catch (e) {
    myRequests.value = []
    reqTotal.value = 0
    if (e instanceof ApiError && e.isForbidden) {
      notify.info('当前账号没有「权利请求列表」权限，无法查看请求记录（这不代表你的申请没有被受理）。')
    }
  } finally {
    reqLoading.value = false
  }
}

function onReqPageChange(p: number): void {
  reqPage.value = p
  void loadMyRequests()
}

onMounted(async () => {
  await loadProfile()
  await loadMyRequests()
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
.card-head .sub {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--txt-3);
}
.summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}
.summary .head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.summary .avatar {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  font-weight: 600;
}
.summary .name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}
.summary .sub {
  margin-top: 4px;
}
.summary .contact {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12.5px;
}
.summary .contact .k {
  color: var(--txt-3);
  display: inline-block;
  width: 52px;
}
.summary .masked {
  color: var(--txt-3);
}
.tags-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 12px;
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}
.req-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.ops a,
td a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover,
td a:hover {
  text-decoration: underline;
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
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.danger {
  color: var(--danger);
}
.mt12 {
  margin-top: 12px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
