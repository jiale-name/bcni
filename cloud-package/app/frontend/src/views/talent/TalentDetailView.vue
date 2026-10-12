<template>
  <div>
    <PageHeader :title="title" :desc="desc">
      <template #actions>
        <el-button @click="router.back()">返回</el-button>
        <el-button v-if="canEdit" v-perm="'talent:update'" @click="openEdit">编辑档案</el-button>
        <el-button
          v-if="canEdit"
          v-perm="'talent:archive'"
          type="danger"
          plain
          :loading="archiving"
          @click="onArchive"
        >
          归档
        </el-button>
      </template>
    </PageHeader>

    <el-alert v-if="!TALENT_MASTER_API_READY" type="warning" :closable="false" show-icon class="mb12">
      <template #title>档案详情接口未就绪（API-31 / API-34）</template>
      <template #default>{{ TALENT_MASTER_API_NOTICE }}</template>
    </el-alert>

    <StatePanel
      :status="detail.status.value"
      :code="detail.error.value?.code ?? null"
      :message="detail.error.value?.message ?? null"
      :trace-id="detail.traceId.value"
      :rows="8"
      not-found-title="未找到该档案"
      not-found-hint="该档案可能已被删除，或不在您的数据范围内（按接口口径，越权与不存在一律 40301）"
      @retry="detail.run()"
      @back="router.back()"
    >
      <template v-if="d">
        <!-- ============ 基本信息 ============ -->
        <el-card shadow="never" class="mb12">
          <template #header>
            <div class="head">
              <span class="hd"><i class="bar" />基础信息</span>
              <div class="head-right">
                <StatusTag
                  :text="d.data_status_name"
                  :tone="d.data_status === 1 ? 'success' : d.data_status === 0 ? 'warning' : 'plain'"
                />
                <span class="muted xs">版本 v{{ d.version_no }}</span>
                <el-tag v-if="!d.sensitive_visible" size="small" type="warning">
                  🔒 敏感字段按权限脱敏
                </el-tag>
              </div>
            </div>
          </template>

          <el-descriptions :column="3" border size="default">
            <el-descriptions-item label="姓名">{{ dash(d.name) }}</el-descriptions-item>
            <el-descriptions-item label="性别">{{ dash(d.gender_name) }}</el-descriptions-item>
            <el-descriptions-item label="出生年月">{{ dash(d.birth_month) }}</el-descriptions-item>
            <el-descriptions-item label="部门">{{ dash(d.org_name) }}</el-descriptions-item>
            <el-descriptions-item label="学历">{{ dash(d.education_level_name) }}</el-descriptions-item>
            <el-descriptions-item label="工作年限">
              {{ d.work_years === null ? '—' : `${d.work_years} 年` }}
            </el-descriptions-item>
            <el-descriptions-item label="毕业院校">{{ dash(d.school) }}</el-descriptions-item>
            <el-descriptions-item label="专业">{{ dash(d.major) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ fmtDateTime(d.updated_at) }}</el-descriptions-item>
            <el-descriptions-item label="身份证">
              <!-- 🔴 后端下发的已是脱敏串；前端**不做脱敏**（CL-4 只在服务端） -->
              <span class="mono" :class="{ masked: looksMasked(d.id_card) }">{{ dash(d.id_card) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="手机号">
              <span class="mono" :class="{ masked: looksMasked(d.phone) }">{{ dash(d.phone) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="邮箱">
              <span class="mono" :class="{ masked: looksMasked(d.email) }">{{ dash(d.email) }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <div class="grid g-2-1">
          <div>
            <!-- ============ 履历明细 ============ -->
            <el-card shadow="never" class="mb12">
              <template #header><span class="hd"><i class="bar" />履历明细</span></template>
              <el-tabs v-model="expTab">
                <el-tab-pane label="工作经历" name="work">
                  <el-table :data="d.work_exps" size="small" empty-text="暂无工作经历">
                    <el-table-column prop="company" label="公司" min-width="150" show-overflow-tooltip />
                    <el-table-column prop="dept_name" label="部门" min-width="120" show-overflow-tooltip />
                    <el-table-column prop="title" label="职位" min-width="130" show-overflow-tooltip />
                    <el-table-column label="起止" width="200">
                      <template #default="{ row }">
                        <span class="muted nowrap">
                          {{ fmtDate(row.start_date) }} ~ {{ fmtDate(row.end_date) }}
                        </span>
                      </template>
                    </el-table-column>
                    <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
                    <el-table-column label="状态" width="90">
                      <template #default="{ row }">{{ row.is_current ? '在职' : '已离职' }}</template>
                    </el-table-column>
                  </el-table>
                </el-tab-pane>
                <el-tab-pane label="项目经历" name="project">
                  <el-table :data="d.project_exps" size="small" empty-text="暂无项目经历">
                    <el-table-column prop="project_name" label="项目" min-width="150" show-overflow-tooltip />
                    <el-table-column prop="role" label="角色" width="130" />
                    <el-table-column label="起止" width="200">
                      <template #default="{ row }">
                        <span class="muted nowrap">
                          {{ fmtDate(row.start_date) }} ~ {{ fmtDate(row.end_date) }}
                        </span>
                      </template>
                    </el-table-column>
                    <el-table-column label="技术栈" min-width="160" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.tech_stack.join('、') || '—' }}</template>
                    </el-table-column>
                    <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
                  </el-table>
                </el-tab-pane>
                <el-tab-pane label="技能" name="skill">
                  <el-table :data="d.skills" size="small" empty-text="暂无技能">
                    <el-table-column prop="skill_name" label="技能" min-width="180" />
                    <el-table-column prop="proficiency_name" label="熟练度" width="130" />
                  </el-table>
                </el-tab-pane>
                <el-tab-pane label="证书" name="cert">
                  <el-table :data="d.certs" size="small" empty-text="暂无证书">
                    <el-table-column prop="cert_name" label="证书" min-width="220" />
                    <el-table-column label="取得日期" width="140">
                      <template #default="{ row }">{{ fmtDate(row.issue_date) }}</template>
                    </el-table-column>
                  </el-table>
                </el-tab-pane>
              </el-tabs>
            </el-card>

            <!-- ============ 变更时间线（API-68） ============ -->
            <el-card shadow="never">
              <template #header>
                <div class="head">
                  <span class="hd"><i class="bar" />变更时间线</span>
                  <router-link to="/talent/change-log" class="xs">
                    完整变更历史 / 时间点快照回溯 →
                  </router-link>
                </div>
              </template>
              <div v-loading="chain.loading.value">
                <el-empty
                  v-if="!chain.loading.value && !chain.rows.value.length"
                  description="暂无变更记录"
                  :image-size="60"
                />
                <el-timeline v-else>
                  <el-timeline-item
                    v-for="c in chain.rows.value"
                    :key="c.id"
                    :timestamp="fmtDateTime(c.created_at)"
                    placement="top"
                  >
                    <div class="tl">
                      <StatusTag :text="c.change_source_name" tone="info" />
                      <b>{{ c.field_label || c.field_name || '（整档）' }}</b>
                      <span class="muted xs">
                        {{ dash(c.before_value) }} → <span class="after">{{ dash(c.after_value) }}</span>
                      </span>
                      <span class="muted xs">by {{ dash(c.operator_name) }}</span>
                    </div>
                  </el-timeline-item>
                </el-timeline>
                <router-link
                  v-if="chain.total.value > chain.rows.value.length"
                  to="/talent/change-log"
                  class="xs"
                >
                  仅显示最近 {{ chain.rows.value.length }} 条（共 {{ chain.total.value }} 条）→
                </router-link>
              </div>
            </el-card>
          </div>

          <!-- ============ 画像（API-34） ============ -->
          <div>
            <el-card shadow="never" class="mb12">
              <template #header>
                <div class="head">
                  <span class="hd"><i class="bar" />数字画像</span>
                  <el-tag v-if="profile.degraded.value" size="small" type="warning">降级</el-tag>
                </div>
              </template>
              <div v-loading="profile.loading.value">
                <div v-if="profile.status.value === 'forbidden'" class="muted sm">
                  无权限查看该档案画像（40301）
                </div>
                <div v-else-if="!profile.data.value" class="muted sm">
                  暂无画像数据（接口未就绪或未生成向量）
                </div>
                <template v-else>
                  <div ref="radarEl" class="radar" />
                  <el-divider />
                  <div class="hd xs"><i class="bar" />标签（{{ profile.data.value.tags.length }}）</div>
                  <div class="tags">
                    <el-tag
                      v-for="t in profile.data.value.tags"
                      :key="t.tag_code"
                      size="small"
                      :type="t.source === 1 ? 'success' : 'info'"
                      class="tag-gap"
                    >
                      {{ t.tag_name }}
                      <span class="muted xs">{{ t.source === 1 ? 'AI' : '人工' }}</span>
                    </el-tag>
                    <span v-if="!profile.data.value.tags.length" class="muted sm">暂无标签</span>
                  </div>
                  <el-divider />
                  <div class="hd xs"><i class="bar" />向量摘要</div>
                  <p class="sm muted" style="margin-top: 6px; line-height: 1.7">
                    {{ profile.data.value.vector_summary || '暂无向量（真实向量化待 H-19 闭环）' }}
                  </p>
                </template>
              </div>
            </el-card>

            <!-- 受控标签（API-55 / API-56） -->
            <el-card shadow="never">
              <template #header>
                <div class="head">
                  <span class="hd"><i class="bar" />档案标签</span>
                  <el-button v-if="auth.hasPerm('tag:assign')" size="small" v-perm="'tag:assign'" @click="openTagEdit">
                    管理标签
                  </el-button>
                </div>
              </template>
              <div v-loading="tags.loading.value">
                <div class="tags">
                  <el-tag
                    v-for="t in tags.data.value?.tags ?? []"
                    :key="t.tag_code"
                    size="small"
                    class="tag-gap"
                  >
                    {{ t.tag_name }}
                  </el-tag>
                  <span v-if="!(tags.data.value?.tags ?? []).length" class="muted sm">暂无标签</span>
                </div>
              </div>
            </el-card>
          </div>
        </div>

        <TraceFooter :trace-id="detail.traceId.value" />
      </template>
    </StatePanel>

    <!-- ============ 编辑档案（API-32；后端写变更留痕） ============ -->
    <el-dialog v-model="editVisible" title="编辑档案" width="560px" @closed="resetEdit">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="保存后由后端写入变更留痕（TBL-22），可在「变更历史」中回溯。"
        class="mb12"
      />
      <el-form ref="editRef" :model="editForm" label-width="96px">
        <el-form-item label="姓名"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="性别">
          <EnumSelect v-model="editForm.gender" :options="GENDER" :clearable="false" width="180px" />
        </el-form-item>
        <el-form-item label="出生年月"><el-input v-model="editForm.birth_month" placeholder="YYYY-MM" /></el-form-item>
        <el-form-item label="学历">
          <EnumSelect v-model="editForm.education_level" :options="EDUCATION_LEVEL" width="180px" />
        </el-form-item>
        <el-form-item label="毕业院校"><el-input v-model="editForm.school" /></el-form-item>
        <el-form-item label="专业"><el-input v-model="editForm.major" /></el-form-item>
        <el-form-item label="工作年限">
          <el-input-number v-model="editForm.work_years" :min="0" :max="60" />
        </el-form-item>
        <el-form-item label="手机号"><el-input v-model="editForm.phone" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="editForm.email" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 标签全量替换（API-56） ============ -->
    <el-dialog v-model="tagVisible" title="管理档案标签" width="520px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="本操作是「全量替换」：提交的集合就是最终标签集合，不是增量追加。"
        class="mb12"
      />
      <el-select
        v-model="tagCodes"
        multiple
        filterable
        clearable
        placeholder="只能选择启用状态的标签"
        style="width: 100%"
      >
        <el-option v-for="t in tagOptions" :key="t.tag_code" :label="t.tag_name" :value="t.tag_code" />
      </el-select>
      <template #footer>
        <el-button @click="tagVisible = false">取消</el-button>
        <el-button type="primary" :loading="tagSaving" @click="submitTags">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-8 · 人才档案详情（`/talent/detail/:id`，F-3.2 / F-3.4 / F-3.10）
 * 聚合 **API-31**（详情）/ **API-34**（画像）/ **API-55**（标签）/ **API-68**（变更链）
 *
 * 🔴 六条口径：
 *   1. **`id` 全程字符串** —— 取自路由 `params.id`（本身就是 `string`），
 *      直接透传给 API。**不 `Number()`、不 `parseInt`**（接口 §1.2）。
 *   2. **不区分「不存在」与「无权限」**：两者都返回 `40301`；仅「本人未关联档案」等
 *      本域内确实不存在才 `40401`。因此页面把 `40301` 渲染为「无权限」、
 *      `40401` 渲染为「未找到」，**不能合并**。
 *   3. **脱敏在服务端**：身份证 / 手机 / 邮箱由序列化层裁剪，前端只加 🔒 标识，
 *      **绝不自己写脱敏规则**（CL-4：脱敏实现全项目只有一份）。
 *   4. **编辑即留痕**：API-32 由后端写 TBL-22，前端**不需要**也不应该自己记一条日志。
 *   5. **归档是不可逆动作** → 二次确认 + 强制幂等（`Idempotency-Key`）。
 *   6. **画像与标签是增强**：任一失败**不得**让整页失败（各自独立渲染状态）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import * as echarts from 'echarts'

import { EDUCATION_LEVEL, GENDER } from '@/api/enums'
import { changeApi } from '@/api/change'
import { tagApi } from '@/api/tag'
import {
  TALENT_MASTER_API_NOTICE,
  TALENT_MASTER_API_READY,
  talentApi,
  type TalentDetail,
  type TalentUpdateRequest,
} from '@/api/talent'
import { dash } from '@/api/types'
import EnumSelect from '@/components/EnumSelect.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useNotify } from '@/composables/useNotify'
import { usePagedTable } from '@/composables/usePagedTable'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'
import { fmtDate, fmtDateTime, looksMasked } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const app = useAppStore()
const notify = useNotify()

/** 🔴 路由参数天然是字符串 —— 直接作为 `SnowflakeId` 使用 */
const talentId = computed(() => String(route.params.id ?? ''))

const title = computed(() => (detail.data.value?.name ? `档案详情 · ${detail.data.value.name}` : '人才档案详情'))
const desc = '字段级裁剪由序列化层执行：敏感字段按数据域下发脱敏值，前端只做展示与标识。'
const canEdit = computed(() => auth.hasRole(['ADM', 'HR']))
const expTab = ref('work')

// ---------------- API-31 详情 ----------------
const detail = useAsyncData<TalentDetail>(() => talentApi.detail(talentId.value, true))
const d = computed(() => detail.data.value)

// ---------------- API-68 变更链（最近 20 条） ----------------
const chain = usePagedTable(
  (params) => changeApi.chain(talentId.value, { ...params, per_page: 20 }),
  {} as Record<string, unknown>,
  { perPage: 20 },
)

// ---------------- API-34 画像 ----------------
const profile = useAsyncData(() => talentApi.profile(talentId.value), { immediate: false })
const radarEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

function renderRadar(): void {
  const data = profile.data.value
  if (!radarEl.value || !data?.dimensions?.length) return
  if (!chart) chart = echarts.init(radarEl.value)
  chart.setOption({
    tooltip: {},
    radar: {
      indicator: data.dimensions.map((x) => ({ name: x.name, max: 100 })),
      radius: '62%',
      splitLine: { lineStyle: { color: '#d9e3e6' } },
      axisName: { color: '#52636a', fontSize: 11 },
    },
    series: [
      {
        type: 'radar',
        areaStyle: { opacity: 0.22 },
        lineStyle: { color: '#175d75' },
        itemStyle: { color: '#175d75' },
        data: [{ value: data.dimensions.map((x) => x.score), name: '画像维度' }],
      },
    ],
  })
}

watch(() => profile.data.value, () => void nextTick(renderRadar))
// 侧栏折叠会改变内容区宽度 → 重新计算雷达图尺寸（否则会拉伸变形）
watch(() => app.sidebarCollapsed, () => nextTick(() => chart?.resize()))

// ---------------- API-55 标签 ----------------
const tags = useAsyncData(() => tagApi.talentTags(talentId.value), { immediate: false })
const tagOptions = ref<{ tag_code: string; tag_name: string }[]>([])

onMounted(() => {
  void profile.run()
  void tags.run()
  void (async () => {
    try {
      const res = await tagApi.flat({ status: 1, per_page: 100 })
      tagOptions.value = (res.data ?? []).map((t) => ({ tag_code: t.tag_code, tag_name: t.tag_name }))
    } catch {
      /* 标签下拉拉不到不影响主体 */
    }
  })()
})

// ---------------- API-32 编辑 ----------------
const editVisible = ref(false)
const saving = ref(false)
const editRef = ref()
const editForm = ref<TalentUpdateRequest & { gender?: number }>({})

function openEdit(): void {
  const cur = d.value
  if (!cur) return
  editForm.value = {
    name: cur.name,
    gender: cur.gender ?? undefined,
    birth_month: cur.birth_month ?? undefined,
    education_level: cur.education_level ?? undefined,
    school: cur.school ?? undefined,
    major: cur.major ?? undefined,
    work_years: cur.work_years ?? undefined,
    phone: cur.phone ?? undefined,
    email: cur.email ?? undefined,
  }
  editVisible.value = true
}

function resetEdit(): void {
  editForm.value = {}
}

/** 只提交**发生变化的字段** —— 后端据此写留痕，提交未变字段会污染变更历史。 */
function diffPayload(): TalentUpdateRequest {
  const cur = d.value
  const next = editForm.value
  const out: TalentUpdateRequest = {}
  if (!cur) return out
  const keys: (keyof TalentUpdateRequest)[] = [
    'name', 'gender', 'birth_month', 'education_level', 'school', 'major', 'work_years', 'phone', 'email',
  ]
  for (const k of keys) {
    const nv = next[k] as unknown
    const ov = (cur as unknown as Record<string, unknown>)[k as string]
    if (nv !== undefined && nv !== null && nv !== '' && nv !== ov) {
      ;(out as unknown as Record<string, unknown>)[k as string] = nv
    }
  }
  return out
}

async function submitEdit(): Promise<void> {
  const payload = diffPayload()
  if (Object.keys(payload).length === 0) {
    notify.info('没有检测到任何修改')
    return
  }
  saving.value = true
  try {
    await talentApi.update(talentId.value, payload)
    notify.ok('已保存，变更已留痕')
    editVisible.value = false
    await detail.run()
    await chain.load()
  } catch (e) {
    notify.fail(e, '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------------- API-33 归档 ----------------
const archiving = ref(false)
async function onArchive(): Promise<void> {
  const yes = await notify.confirm(
    `归档后该档案将从常规列表移除，并同步清理向量索引。确定归档「${d.value?.name ?? ''}」？`,
    '归档档案',
  )
  if (!yes) return
  archiving.value = true
  try {
    const res = await talentApi.archive(talentId.value)
    const p = res.data
    if (p?.vector_cleaned) notify.ok('已归档，向量索引已同步清理')
    else notify.warn('已归档；向量索引未清理（可能未生成向量）')
    await detail.run()
  } catch (e) {
    notify.fail(e, '归档失败')
  } finally {
    archiving.value = false
  }
}

// ---------------- API-56 标签全量替换 ----------------
const tagVisible = ref(false)
const tagSaving = ref(false)
const tagCodes = ref<string[]>([])

function openTagEdit(): void {
  tagCodes.value = (tags.data.value?.tags ?? []).map((t) => t.tag_code)
  tagVisible.value = true
}

async function submitTags(): Promise<void> {
  tagSaving.value = true
  try {
    await tagApi.assign(talentId.value, tagCodes.value)
    notify.ok('标签已更新（全量替换）')
    tagVisible.value = false
    await tags.run()
    await detail.run()
  } catch (e) {
    notify.fail(e, '保存标签失败')
  } finally {
    tagSaving.value = false
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
.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.masked {
  color: var(--txt-3);
}
.radar {
  width: 100%;
  height: 220px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.tag-gap {
  margin-right: 4px;
}
.tl {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
}
.tl .after {
  color: var(--ok);
  font-weight: 600;
}
</style>
