<template>
  <div>
    <PageHeader title="模型配置与路由" :desc="desc">
      <template #actions>
        <el-badge :value="unhealthyCount" :hidden="unhealthyCount === 0" type="danger">
          <el-button :loading="healthLoading" @click="loadHealth">
            <el-icon><Connection /></el-icon>健康检查
          </el-button>
        </el-badge>
      </template>
    </PageHeader>

    <!-- 🔴 主通道异常是**必须显著提示**的状态（口径 ⑤） -->
    <el-alert
      v-if="health && !health.all_primary_healthy"
      type="error"
      :closable="false"
      show-icon
      class="mb12"
    >
      <template #title>主通道存在不可用项</template>
      <template #default>
        检查时间 {{ fmtDateTime(health.checked_at) }}：共 {{ health.total }} 个目标，
        健康 {{ health.healthy }}、不可用 {{ health.unavailable }}、已熔断 {{ health.blocked }}。
        主备路由会尝试自动切换，但请尽快排查（熔断后端
        {{ health.breaker_backend_ok ? '正常' : '异常' }}）。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        <b>单价为 0 表示本地部署</b>（不走云端计费）。金额以<b>字符串</b>（`Decimal`）下发与展示，
        不做浮点累加。脱敏率的分母是<b>调用时刻的 <code class="mono">deployment</code></b>，
        不是本页配置的当前值 —— 改配置不会让历史合规统计失真。
      </template>
    </el-alert>

    <!-- ============ 健康概览 ============ -->
    <div v-if="health" class="grid g4 mb12">
      <MetricCard label="目标总数" :value="health.total" unit=" 个" />
      <MetricCard label="健康" :value="health.healthy" unit=" 个" tone="ok" />
      <MetricCard
        label="不可用"
        :value="health.unavailable"
        unit=" 个"
        :tone="health.unavailable > 0 ? 'danger' : 'default'"
      />
      <MetricCard
        label="已熔断拦截"
        :value="health.blocked"
        unit=" 个"
        :tone="health.blocked > 0 ? 'warn' : 'default'"
        sub="熔断中的项会按冷却时间自动恢复"
      />
    </div>

    <!-- ============ 模型列表 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />模型与主备路由
            <span class="sub">{{ total }} 个配置</span>
          </span>
          <div class="filters">
            <el-input
              v-model="sceneFilter"
              placeholder="场景 code"
              clearable
              size="small"
              style="width: 150px"
            />
            <el-select v-model="statusFilter" clearable placeholder="状态" size="small" style="width: 120px">
              <el-option :value="1" label="启用" />
              <el-option :value="0" label="停用" />
            </el-select>
            <el-button size="small" type="primary" @click="reload">查询</el-button>
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
        empty-title="没有模型配置"
        empty-hint="当前筛选条件下没有模型配置"
        @retry="load"
      >
        <el-table :data="rows" v-loading="loading" stripe row-key="id">
          <el-table-column label="场景" width="150">
            <template #default="{ row }">
              {{ row.scene_name || row.scene }}
              <div class="muted xs mono">{{ row.scene }}</div>
            </template>
          </el-table-column>

          <el-table-column label="模型" min-width="180">
            <template #default="{ row }">
              <b>{{ row.display_name }}</b>
              <div class="muted xs mono">{{ row.model_code }}</div>
            </template>
          </el-table-column>

          <el-table-column label="提供方 / 部署" width="170">
            <template #default="{ row }">
              {{ row.provider_name || row.provider }}
              <div>
                <!-- 🔴 deployment 是脱敏率统计的事实列，必须显式展示 -->
                <StatusTag
                  :text="row.deployment_name || row.deployment"
                  :tone="row.deployment === 'local' ? 'primary' : 'purple'"
                />
              </div>
            </template>
          </el-table-column>

          <el-table-column label="优先级" width="90" align="right">
            <template #default="{ row }">
              <span class="num">{{ row.priority }}</span>
              <div class="muted xs">越小越优先</div>
            </template>
          </el-table-column>

          <el-table-column label="单价（元/1K）" width="150" align="right">
            <template #default="{ row }">
              <!-- 🔴 字符串直接渲染；本地部署（0）明确标注 -->
              <div class="num">
                入 {{ row.price_in_per_1k }} / 出 {{ row.price_out_per_1k }}
              </div>
              <div v-if="isLocal(row)" class="muted xs">本地部署 · 不计费</div>
            </template>
          </el-table-column>

          <el-table-column label="熔断 / 重试" width="140">
            <template #default="{ row }">
              <span class="xs muted">
                阈值 {{ row.breaker_threshold }} 次 · 冷却 {{ row.breaker_cooldown_seconds }}s
              </span>
              <div class="xs muted">超时 {{ row.timeout_seconds }}s · 重试 {{ row.max_retries }}</div>
            </template>
          </el-table-column>

          <el-table-column label="实时健康" width="190">
            <template #default="{ row }">
              <div v-if="targetOf(row)" class="health-cell">
                <StatusTag
                  :text="healthText(targetOf(row)!)"
                  :tone="healthTone(targetOf(row)!)"
                />
                <span v-if="targetOf(row)?.latency_ms !== null" class="muted xs num">
                  {{ targetOf(row)?.latency_ms }}ms
                </span>
                <div v-if="targetOf(row)?.blocked" class="danger xs">
                  已熔断拦截，冷却剩余 {{ targetOf(row)?.cooldown_remaining_seconds }}s
                </div>
                <div v-else-if="targetOf(row)?.error" class="danger xs ellipsis" :title="targetOf(row)?.error || ''">
                  {{ targetOf(row)?.error }}
                </div>
              </div>
              <span v-else class="muted xs">未检查</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <StatusTag :text="row.status_name" :tone="row.status === 1 ? 'success' : 'info'" />
            </template>
          </el-table-column>

          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <a @click="openEdit(row)">配置</a>
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
        <TraceFooter :trace-id="traceId" />
      </StatePanel>
    </el-card>

    <!-- ============ 配置编辑 ============ -->
    <el-dialog v-model="editVisible" title="修改模型配置" width="640px">
      <div v-if="editing" class="form">
        <div class="row">
          <span class="k">模型编码</span>
          <span class="mono">{{ editing.model_code }}</span>
          <span class="muted xs">（主键，不可修改）</span>
        </div>
        <div class="row">
          <span class="k">显示名</span>
          <el-input v-model="form.display_name" />
        </div>
        <div class="row">
          <span class="k">提供方</span>
          <el-input v-model="form.provider" />
        </div>
        <div class="row">
          <span class="k">部署方式</span>
          <el-select v-model="form.deployment" style="width: 100%">
            <el-option value="local" label="local（本地部署，不计费）" />
            <el-option value="cloud" label="cloud（云端，按 token 计费）" />
          </el-select>
        </div>
        <div class="row">
          <span class="k">Base URL</span>
          <el-input v-model="form.base_url" />
        </div>
        <div class="row">
          <span class="k">模型名</span>
          <el-input v-model="form.model_name" />
        </div>
        <div class="row">
          <span class="k">优先级</span>
          <el-input-number v-model="form.priority" :min="1" :max="999" />
        </div>
        <div class="row">
          <span class="k">超时 / 重试</span>
          <div class="inline">
            <el-input-number v-model="form.timeout_seconds" :min="1" :max="600" />
            <span class="muted xs">秒</span>
            <el-input-number v-model="form.max_retries" :min="0" :max="10" />
            <span class="muted xs">次</span>
          </div>
        </div>
        <div class="row">
          <span class="k">熔断阈值 / 冷却</span>
          <div class="inline">
            <el-input-number v-model="form.breaker_threshold" :min="1" :max="100" />
            <span class="muted xs">次</span>
            <el-input-number v-model="form.breaker_cooldown_seconds" :min="1" :max="3600" />
            <span class="muted xs">秒</span>
          </div>
        </div>
        <div class="row">
          <span class="k">单价（元/1K）</span>
          <div class="inline">
            <!-- 🔴 用字符串输入，避免浮点：后端字段就是 string -->
            <el-input v-model="form.price_in_per_1k" placeholder="输入" style="width: 110px" />
            <span class="muted xs">入</span>
            <el-input v-model="form.price_out_per_1k" placeholder="输出" style="width: 110px" />
            <span class="muted xs">出</span>
          </div>
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="form.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
        <div class="row">
          <span class="k">备注</span>
          <el-input v-model="form.remark" />
        </div>
      </div>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-18a · 模型配置与路由（`/ai/model`，F-9.1，**API-81 ~ API-83**）
 *
 * 🔴 五条契约（`api/ai.ts` 已钉死）：
 *   ① **`deployment` 是调用时刻的事实列** —— 脱敏率分母依据，必须展示。
 *   ② **单价为 0 表示本地部署**（不走云端计费）。
 *   ③ **`cost_yuan` / 单价是字符串**（`Decimal` 序列化）——
 *      表单里也用**字符串输入**，不 `parseFloat` 相乘。
 *   ④ **熔断 `blocked=true` 是「已被拦截」**，配 `cooldown_remaining_seconds`；
 *      `all_primary_healthy=false` 必须**显著提示**。
 *   ⑤ **改配置不影响历史合规统计** —— 页面明说，避免误用「当前配置」重算历史脱敏率。
 */
import { computed, onMounted, ref } from 'vue'

import { aiApi, type ModelConfig, type ModelConfigUpdateRequest, type ModelHealth } from '@/api/ai'
import { ApiError } from '@/api/http'
import { fmtDateTime } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc = '模型场景绑定、主备路由优先级、熔断与重试参数。价格与成本一律按字符串处理。'

const loading = ref(false)
const submitting = ref(false)
const status = ref<LoadStatus>('idle')
const rows = ref<ModelConfig[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

const sceneFilter = ref('')
const statusFilter = ref<number | null>(null)

// ---------------- 健康（API-83） ----------------
const health = ref<ModelHealth | null>(null)
const healthLoading = ref(false)

const unhealthyCount = computed(() => {
  const h = health.value
  if (!h) return 0
  return h.unavailable + h.blocked
})

function targetOf(row: ModelConfig) {
  return health.value?.items.find((t) => t.model_code === row.model_code && t.scene === row.scene)
}

function healthText(t: { healthy: boolean; blocked: boolean }): string {
  if (t.blocked) return '已熔断'
  return t.healthy ? '健康' : '不可用'
}

function healthTone(t: { healthy: boolean; blocked: boolean }): 'success' | 'danger' | 'warning' {
  if (t.blocked) return 'warning'
  return t.healthy ? 'success' : 'danger'
}

async function loadHealth(): Promise<void> {
  healthLoading.value = true
  try {
    const res = await aiApi.modelHealth(
      sceneFilter.value.trim() ? { scene: sceneFilter.value.trim() } : {},
    )
    health.value = res.data
  } catch (e) {
    health.value = null
    notify.fail(e, '健康检查失败')
  } finally {
    healthLoading.value = false
  }
}

// ---------------- 列表（API-81） ----------------
function isLocal(row: ModelConfig): boolean {
  return row.deployment === 'local' || row.price_in_per_1k === '0' || row.price_in_per_1k === '0.00'
}

async function load(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const res = await aiApi.models({
      scene: sceneFilter.value.trim() || undefined,
      status: statusFilter.value ?? undefined,
      page: page.value,
      per_page: perPage.value,
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
  void loadHealth()
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

// ---------------- 编辑（API-82） ----------------
const editVisible = ref(false)
const editing = ref<ModelConfig | null>(null)
const form = ref<ModelConfigUpdateRequest>({})

function openEdit(row: ModelConfig): void {
  editing.value = row
  form.value = {
    display_name: row.display_name,
    provider: row.provider,
    deployment: row.deployment,
    base_url: row.base_url,
    model_name: row.model_name,
    priority: row.priority,
    timeout_seconds: row.timeout_seconds,
    max_retries: row.max_retries,
    breaker_threshold: row.breaker_threshold,
    breaker_cooldown_seconds: row.breaker_cooldown_seconds,
    // 🔴 单价保持字符串原值，不转 number
    price_in_per_1k: row.price_in_per_1k,
    price_out_per_1k: row.price_out_per_1k,
    status: row.status,
    remark: row.remark ?? undefined,
  }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  const row = editing.value
  if (!row) return
  submitting.value = true
  try {
    await aiApi.updateModel(row.id, form.value)
    notify.ok('已保存。注意：修改配置不会回溯影响历史调用日志的脱敏率统计。')
    editVisible.value = false
    await load()
    await loadHealth()
  } catch (e) {
    notify.fail(e, '保存失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await Promise.allSettled([load(), loadHealth()])
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
.filters {
  display: flex;
  gap: 8px;
  align-items: center;
}
.health-cell {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.form .row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 10px;
  align-items: center;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.form .inline {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
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
.mb12 {
  margin-bottom: 12px;
}
</style>
