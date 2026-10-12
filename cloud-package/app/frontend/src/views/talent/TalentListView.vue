<template>
  <div>
    <PageHeader title="人才档案列表" :desc="desc">
      <template #actions>
        <el-button v-if="canExport" v-perm="'talent:export'" :loading="exporting" @click="onExport">
          <el-icon><Download /></el-icon>导出
        </el-button>
        <el-button v-if="auth.hasRole(['ADM', 'HR'])" type="primary" @click="router.push('/talent/import')">
          <el-icon><UploadFilled /></el-icon>批量导入
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 结构化筛选 ≠ 语义检索：把接口语义前置到界面（原型 P-7 的核心提示） -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        本页是「结构化筛选」，不是语义检索。自然语言找人才请用
        <router-link to="/talent/search">语义人才检索</router-link>
        （F-3.6，支持「找擅长数字化转型、3 年以上 AI 项目经验的骨干」）。
      </template>
    </el-alert>

    <!-- 🔴 后端未就绪声明（不让 404 被误读成前端 bug） -->
    <el-alert v-if="!TALENT_MASTER_API_READY" type="warning" :closable="false" show-icon class="mb12">
      <template #title>档案主档接口未就绪</template>
      <template #default>
        {{ TALENT_MASTER_API_NOTICE }}
      </template>
    </el-alert>

    <DegradedBadge :degraded="table.degraded.value" :reason="table.degradedReason.value" />

    <!-- ============ 筛选栏 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input
          v-model="keywordInput"
          placeholder="姓名（模糊）"
          clearable
          style="width: 180px"
          @keyup.enter="onSearch"
        />

        <!-- ⚠️ MGR 无 org:view 权限 → 不展示部门下拉（否则下拉恒为空，像功能坏了） -->
        <el-tree-select
          v-if="auth.hasPerm('org:view')"
          v-model="orgIds"
          :data="orgTree"
          :props="{ label: 'dept_name', value: 'id', children: 'children' }"
          node-key="id"
          multiple
          show-checkbox
          check-strictly
          collapse-tags
          collapse-tags-tooltip
          :render-after-expand="false"
          placeholder="全部部门"
          style="width: 220px"
          clearable
        />
        <span v-else class="muted xs" title="行级范围由服务端按数据域自动注入">
          部门范围：服务端按数据域自动裁剪
        </span>

        <EnumSelect v-model="educationLevel" :options="EDUCATION_LEVEL" placeholder="学历不限" />

        <el-select v-model="workYearsRange" placeholder="工作年限不限" clearable style="width: 150px">
          <el-option label="1 年以内" value="0-1" />
          <el-option label="1-3 年" value="1-3" />
          <el-option label="3-5 年" value="3-5" />
          <el-option label="5-10 年" value="5-10" />
          <el-option label="10 年以上" value="10-60" />
        </el-select>

        <!-- 🔴 占位文案必须与后端口径一致（批次 23）：未选 = **ADM/HR 看「待复核+有效」**，
             其余角色仅「有效」。写成「全部状态」或「默认仅有效」都会让
             「刚导入的档案（0 待复核）」的可见性与实际不符，被误读成「导入没入库」。 -->
        <EnumSelect
          v-model="dataStatus"
          :options="TALENT_DATA_STATUS"
          placeholder="状态（默认：有效+待复核）"
        />

        <el-button type="primary" @click="onSearch">
          <el-icon><Search /></el-icon>查询
        </el-button>
        <el-button @click="onReset">重置</el-button>
      </div>

      <div v-if="selectedTagCodes.length" class="tag-row">
        <span class="muted xs">已选标签：</span>
        <el-tag
          v-for="code in selectedTagCodes"
          :key="code"
          closable
          size="small"
          @close="removeTag(code)"
        >
          {{ tagLabel(code) }}
        </el-tag>
        <router-link to="/talent/tag-dict" class="xs">标签只能取自受控词表 →</router-link>
      </div>

      <div class="mb8" style="margin-top: 8px">
        <el-select
          v-model="selectedTagCodes"
          multiple
          filterable
          clearable
          placeholder="按标签筛选（只能取启用项）"
          style="width: 100%; max-width: 620px"
        >
          <el-option
            v-for="t in tagOptions"
            :key="t.tag_code"
            :label="t.tag_name"
            :value="t.tag_code"
          />
        </el-select>
      </div>
    </el-card>

    <!-- ============ 列表 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd"><i class="bar" />人才档案
            <span class="sub">共 {{ table.total.value }} 条命中</span>
          </span>
          <span class="muted xs">
            数据范围：{{ auth.scopeLabel }}（{{ roleSummary }}）
          </span>
        </div>
      </template>

      <StatePanel
        :status="table.status.value === 'ready' && table.isEmpty.value ? 'empty' : table.status.value"
        :loading="table.loading.value"
        :code="table.error.value?.code ?? null"
        :message="table.error.value?.message ?? null"
        :trace-id="table.traceId.value"
        empty-title="没有符合条件的人才档案"
        empty-hint="试试放宽筛选条件，或使用语义检索「找擅长数字化转型的骨干」"
        :rows="6"
        @retry="table.load()"
      >
        <template #empty-actions>
          <el-button @click="onReset">重置筛选</el-button>
          <el-button type="primary" @click="router.push('/talent/search')">去语义检索</el-button>
        </template>

        <el-table :data="table.rows.value" v-loading="table.loading.value" stripe size="default">
          <el-table-column label="姓名" min-width="150">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openDetail(row.id)">
                <span class="ellipsis" :title="row.name">{{ row.name }}</span>
              </el-link>
            </template>
          </el-table-column>
          <el-table-column label="性别" width="70">
            <template #default="{ row }">{{ dash(row.gender_name) }}</template>
          </el-table-column>
          <el-table-column label="部门" min-width="150">
            <template #default="{ row }">
              <span class="ellipsis" :title="row.org_name || ''">{{ dash(row.org_name) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="学历" width="90">
            <template #default="{ row }">{{ dash(row.education_level_name) }}</template>
          </el-table-column>
          <el-table-column label="工作年限" width="100" align="right">
            <template #default="{ row }">
              {{ row.work_years === null || row.work_years === undefined ? '—' : `${row.work_years} 年` }}
            </template>
          </el-table-column>
          <el-table-column label="标签" min-width="200">
            <template #default="{ row }">
              <template v-if="row.top_tags?.length">
                <el-tag v-for="t in row.top_tags" :key="t.tag_code" size="small" type="info" class="tag-gap">
                  {{ t.tag_name }}
                </el-tag>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <StatusTag :text="row.data_status_name" :tone="dataStatusTone(row.data_status)" />
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="140">
            <template #default="{ row }">
              <span class="muted nowrap">{{ fmtDateTime(row.updated_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <div class="ops">
                <a @click="openDetail(row.id)">详情</a>
                <a @click="askAbout(row.id)">问答</a>
              </div>
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

        <TraceFooter :trace-id="table.traceId.value" extra="per_page 上限 100（超出按 100 处理）" />
      </StatePanel>
    </el-card>
  </div>
</template>

<script setup lang="ts">
/**
 * P-7 · 人才档案列表（`/talent/list`，F-3.2，**API-30**）
 *
 * 🔴 本页承载四条最容易写错的契约：
 *   1. **雪花 ID 一律按字符串流转**（接口 §1.2）—— `openDetail(row.id)` 直接把
 *      **字符串**塞进路由参数；本页**任何地方都不出现 `Number(id)` / `parseInt`**。
 *      用列表返回的 ID 请求详情，是「唯一能当场暴露精度丢失」的检查（原型 P-7 必跑项）。
 *   2. **`filters.org_ids` 只能收窄**（接口 §4）—— 传超出范围的 ID → `40301`
 *      （**不静默忽略**），所以前端不做「本地过滤后悄悄丢弃」。
 *   3. **`data_status` 默认口径（批次 23）：ADM/HR = `0 待复核 + 1 有效`**（否则导入成功
 *      但落库为「待复核」的档案在默认列表里一条都看不到）；查 `2 已合并` / `4 已删除` 仅 ADM/HR 可用。
 *   4. **MGR 没有 `org:view` 权限**（种子 `ROLE_GRANTS` 里 MGR = `talent:list/talent:detail/search/tag:view/dict:view`）
 *      —— 因此部门下拉对 MGR **不渲染**，改为文字说明。
 *      渲染一个恒为空的下拉，会被误读成「部门数据丢了」。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { EDUCATION_LEVEL, TALENT_DATA_STATUS } from '@/api/enums'
import { ApiError } from '@/api/http'
import { orgApi, type OrgNode } from '@/api/org'
import { tagApi } from '@/api/tag'
import {
  TALENT_MASTER_API_NOTICE,
  TALENT_MASTER_API_READY,
  talentApi,
  type TalentListQuery,
} from '@/api/talent'
import { dash } from '@/api/types'
import DegradedBadge from '@/components/DegradedBadge.vue'
import EnumSelect from '@/components/EnumSelect.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'
import { usePagedTable } from '@/composables/usePagedTable'
import { useAuthStore } from '@/store/auth'
import { fmtDateTime, saveTextFile } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()
const notify = useNotify()

const desc =
  '结构化条件检索（姓名 / 部门 / 学历 / 年限 / 标签 / 状态）。ID 全程按字符串处理，不接受数字形态。'

// ---------------- 筛选条件 ----------------
const keywordInput = ref('')
const orgIds = ref<string[]>([])
const educationLevel = ref<number | null>(null)
const workYearsRange = ref<string | null>(null)
const dataStatus = ref<number | null>(null)
const selectedTagCodes = ref<string[]>([])

const orgTree = ref<OrgNode[]>([])
const tagOptions = ref<{ tag_code: string; tag_name: string }[]>([])
const tagLabelMap = ref<Record<string, string>>({})

const canExport = computed(() => auth.hasPerm('talent:export'))

const roleSummary = computed(() =>
  auth.isAdmin
    ? 'ADM · data_scope=1'
    : `data_scope=${auth.user?.data_scope ?? '—'}${auth.user?.data_scope_name ? ` · ${auth.user.data_scope_name}` : ''}`,
)

/** 把「年限区间」拆成后端要求的 min / max 两个平面参数。 */
function yearsBounds(): { min?: number; max?: number } {
  const v = workYearsRange.value
  if (!v) return {}
  const [a, b] = v.split('-').map((x) => Number(x))
  return { min: Number.isFinite(a) ? a : undefined, max: Number.isFinite(b) ? b : undefined }
}

/**
 * 组装查询参数。
 * ⚠️ 后端用**扁平的带点键名**（`filters.xxx`），不是嵌套对象 —— 见 `api/talent.ts` 的 `TalentListQuery`。
 */
function buildQuery(): TalentListQuery {
  const y = yearsBounds()
  const q: TalentListQuery = {
    keyword: keywordInput.value.trim() || undefined,
    sort: '-updated_at',
  }
  if (orgIds.value.length) q['filters.org_ids'] = orgIds.value.join(',')
  if (educationLevel.value) q['filters.education_level'] = educationLevel.value
  if (y.min !== undefined) q['filters.work_years_min'] = y.min
  if (y.max !== undefined) q['filters.work_years_max'] = y.max
  if (selectedTagCodes.value.length) q['filters.tag_codes'] = selectedTagCodes.value.join(',')
  if (dataStatus.value !== null) q['filters.data_status'] = dataStatus.value
  return q
}

const table = usePagedTable(
  (params) => talentApi.list({ ...(params as unknown as TalentListQuery) }),
  buildQuery() as unknown as Record<string, unknown>,
  { perPage: 20 },
)

/**
 * 把当前筛选条件应用到查询对象。
 *
 * 🔴 必须**先删掉新条件下不再存在的键**，再合并 —— 只 `Object.assign` 会留下上一轮的
 *    `filters.tag_codes` 等键，表现为「取消勾选后结果没变」（静默的脏条件）。
 *    这是筛选器类页面最常见的隐性缺陷。
 */
function applyQuery(): void {
  const next = buildQuery() as unknown as Record<string, unknown>
  const target = table.query as unknown as Record<string, unknown>
  for (const key of Object.keys(target)) {
    if (!(key in next)) delete target[key]
  }
  Object.assign(target, next)
  table.search()
}

function onSearch(): void {
  applyQuery()
}

function onReset(): void {
  keywordInput.value = ''
  orgIds.value = []
  educationLevel.value = null
  workYearsRange.value = null
  dataStatus.value = null
  selectedTagCodes.value = []
  applyQuery()
}

function removeTag(code: string): void {
  selectedTagCodes.value = selectedTagCodes.value.filter((c) => c !== code)
  applyQuery()
}

function tagLabel(code: string): string {
  return tagLabelMap.value[code] || code
}

/** 状态色调：仅 `1 有效` 绿、`0 待复核` 黄、`4 已删除` 红、其余中性。 */
function dataStatusTone(code: number): 'success' | 'warning' | 'danger' | 'plain' {
  if (code === 1) return 'success'
  if (code === 0) return 'warning'
  if (code === 4) return 'danger'
  return 'plain'
}

function openDetail(id: string): void {
  // 🔴 id 已经是字符串（`SnowflakeId`）；直接透传，**绝不** `Number(id)`
  void router.push({ name: 'talent-detail', params: { id } })
}

function askAbout(id: string): void {
  void router.push({ name: 'talent-ask', query: { talent_id: id } })
}

// ---------------- 导出（API-35，导出行为自身留痕） ----------------
const exporting = ref(false)
async function onExport(): Promise<void> {
  exporting.value = true
  try {
    const res = await talentApi.export({ ...buildQuery(), format: 'csv' })
    const payload = res.data
    if (!payload) {
      notify.warn('导出未返回内容')
      return
    }
    saveTextFile(payload.content, payload.filename)
    notify.ok(`已导出 ${payload.row_count} 条（本次导出已留痕）`)
  } catch (e) {
    if (e instanceof ApiError && e.code === 40002) {
      notify.warn('导出条数超出上限，请缩小筛选范围后重试')
    } else {
      notify.fail(e, '导出失败')
    }
  } finally {
    exporting.value = false
  }
}

// ---------------- 参考数据（失败不阻断主列表） ----------------
onMounted(async () => {
  // 标签下拉：受控词表（已登录即可读）
  try {
    const res = await tagApi.flat({ status: 1, per_page: 100 })
    const items = res.data ?? []
    tagOptions.value = items.map((t) => ({ tag_code: t.tag_code, tag_name: t.tag_name }))
    tagLabelMap.value = Object.fromEntries(items.map((t) => [t.tag_code, t.tag_name]))
  } catch {
    // 标签拉不到只影响筛选器，不影响列表
  }

  // 部门树：仅在有 org:view 时拉（MGR 没有）
  if (auth.hasPerm('org:view')) {
    try {
      const res = await orgApi.tree({ status: 1 })
      orgTree.value = res.data?.items ?? []
    } catch {
      /* 同上：非阻断 */
    }
  }
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
.toolbar {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.tag-gap {
  margin-right: 4px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.ops {
  display: flex;
  gap: 9px;
}
.ops a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover {
  text-decoration: underline;
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
</style>
