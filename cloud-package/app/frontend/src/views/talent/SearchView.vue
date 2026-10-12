<template>
  <div>
    <PageHeader title="语义人才检索" :desc="desc">
      <template #actions>
        <el-button @click="router.push('/talent/list')">
          <el-icon><Tickets /></el-icon>结构化筛选
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 把接口语义前置到界面：本页与「结构化筛选」是两条不同通道 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        用自然语言找人，例如「找擅长数字化转型、3 年以上 AI 项目经验的骨干」。
        本页走<b>混合召回 + RRF 融合 + 精排</b>（API-62），与
        <router-link to="/talent/list">结构化筛选</router-link>不是同一条通道。
      </template>
    </el-alert>

    <!-- ============ 检索输入 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="search-box">
        <el-input
          v-model="queryText"
          type="textarea"
          :rows="2"
          resize="none"
          maxlength="500"
          show-word-limit
          placeholder="用一句话描述你要找的人，例如：5 年以上机器学习经验、做过推荐系统的硕士"
          @keydown.enter.ctrl="onSearch"
        />
        <div class="search-actions">
          <el-button type="primary" :loading="loading" @click="onSearch">
            <el-icon><Aim /></el-icon>检索
          </el-button>
          <el-button :disabled="loading" @click="onClear">清空</el-button>
          <span class="muted xs">Ctrl + Enter 直接检索</span>
        </div>
      </div>

      <!-- 参数区：默认收起，避免主路径被淹没 -->
      <el-collapse class="mt10">
        <el-collapse-item name="adv">
          <template #title>
            <span class="xs">高级参数（模式 / 条数 / 精排 / 范围收窄）</span>
          </template>

          <div class="adv">
            <div class="field">
              <label>召回模式</label>
              <el-select v-model="mode" style="width: 200px">
                <el-option value="auto" label="auto（向量 + 词法，自动兜底）" />
                <el-option value="vector" label="vector（仅向量）" />
                <el-option value="keyword" label="keyword（仅词法）" />
              </el-select>
              <!-- 🔴 关键口径：显式 keyword 不是降级 -->
              <span class="muted xs">
                选 <code class="mono">keyword</code> 表示「我就要词法结果」，
                此时<b>不会</b>标记降级；只有 auto 下向量库失联才提示降级。
              </span>
            </div>

            <div class="field">
              <label>返回条数 top_k</label>
              <el-input-number v-model="topK" :min="1" :max="50" />
              <span class="muted xs">上限 50，超出由后端按上限处理。</span>
            </div>

            <div class="field">
              <label>精排 Rerank</label>
              <el-switch v-model="rerank" />
              <span class="muted xs">
                Rerank 是<b>增强</b>不是依赖：精排失败时保留融合序，
                <b>不算降级</b>（见下方 meta 说明）。
              </span>
            </div>

            <div class="field">
              <label>命中理由</label>
              <el-switch v-model="withReason" />
              <span class="muted xs">开启后返回中文短语解释「为什么命中」。</span>
            </div>

            <div class="field">
              <label>教育程度下限</label>
              <el-select v-model="eduMin" clearable placeholder="不限" style="width: 180px">
                <el-option
                  v-for="o in EDUCATION_LEVEL"
                  :key="o.value"
                  :label="o.label"
                  :value="String(o.value)"
                />
              </el-select>
              <span class="muted xs">⚠️ 该参数上游文档写数值、实现按字符串收，此处按实现传字符串。</span>
            </div>

            <div class="field">
              <label>工作年限 ≥</label>
              <el-input-number v-model="workYearsMin" :min="0" :max="60" />
            </div>

            <div class="field">
              <label>标签（≤10 个）</label>
              <el-select
                v-model="tagCodes"
                multiple
                filterable
                clearable
                placeholder="只能取启用项"
                style="width: 100%; max-width: 560px"
              >
                <el-option
                  v-for="t in tagOptions"
                  :key="t.tag_code"
                  :label="t.tag_name"
                  :value="t.tag_code"
                />
              </el-select>
            </div>

            <div v-if="auth.hasPerm('org:view')" class="field">
              <label>部门范围（只能收窄）</label>
              <el-tree-select
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
                placeholder="不限（按我的数据域）"
                style="width: 300px"
                clearable
              />
              <span class="muted xs">
                传超出数据域的 ID 会返回 <code class="mono">40301</code>，
                <b>不会</b>被静默忽略。
              </span>
            </div>
            <div v-else class="field">
              <label>部门范围</label>
              <span class="muted xs">服务端按数据域自动裁剪（你的角色没有 <code class="mono">org:view</code>）。</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <!-- ============ 降级徽标（只有 meta.degraded 才出现） ============ -->
    <DegradedBadge
      :degraded="meta?.degraded === true"
      :reason="meta?.degraded_reason"
      :mode="meta?.mode_used"
    />

    <!-- ============ 结果 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />检索结果
            <span v-if="meta" class="sub">命中 {{ results.length }} 条</span>
          </span>
          <span v-if="meta" class="muted xs">
            实际模式 <b>{{ meta.mode_used }}</b> ·
            候选池 {{ meta.candidate_pool_size }} ·
            耗时 {{ meta.elapsed_ms }}ms
          </span>
        </div>
      </template>

      <!-- 🔴 权限范围显式提示（接口 §4 / 检索口径 ④） -->
      <div v-if="meta" class="scope-bar">
        <el-icon><Lock /></el-icon>
        <span class="xs">
          服务端推导的有效范围：<b>{{ scopeText }}</b>
          <template v-if="meta.permission_filter_applied">（本次已应用权限过滤）</template>
        </span>
        <span v-if="!meta.rerank_used" class="muted xs">｜精排未生效，已保留融合序（非降级）</span>
      </div>

      <StatePanel
        :status="status === 'ready' && results.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="code"
        :message="message"
        :trace-id="traceId"
        :rows="5"
        empty-title="没有命中的人才"
        empty-hint="换一种说法，或改用结构化筛选按硬条件查找"
        @retry="onSearch"
      >
        <template #empty-actions>
          <el-button @click="onSearch">重新检索</el-button>
          <el-button type="primary" @click="router.push('/talent/list')">去结构化筛选</el-button>
        </template>

        <div class="hit-list">
          <div v-for="hit in results" :key="hit.talent_id" class="hit">
            <div class="hit-head">
              <el-link type="primary" :underline="false" @click="openDetail(hit.talent_id)">
                <b>{{ hit.name }}</b>
              </el-link>
              <span class="muted sm">{{ dash(hit.org_name) }}</span>
              <span class="muted sm">
                工作年限：{{ hit.work_years === null ? '—' : `${hit.work_years} 年` }}
              </span>
              <div class="score">
                <span class="k">相关度</span>
                <span class="v num">{{ hit.score.toFixed(4) }}</span>
              </div>
              <div class="ops">
                <a @click="openDetail(hit.talent_id)">详情</a>
                <a @click="askAbout(hit.talent_id)">就该档案问答</a>
              </div>
            </div>

            <!-- 命中理由（中文短语，由服务端产出） -->
            <div v-if="withReason && hit.hit_reasons?.length" class="reasons">
              <el-tag v-for="(r, i) in hit.hit_reasons" :key="i" size="small" type="info" effect="plain">
                {{ r }}
              </el-tag>
            </div>

            <!-- 命中片段（**已脱敏**，前端不再加工） -->
            <div v-if="hit.matched_chunks?.length" class="chunks">
              <div v-for="c in hit.matched_chunks.slice(0, 3)" :key="c.chunk_pk" class="chunk">
                <span class="cfield">{{ c.field_name || '片段' }}</span>
                <span class="ctext">{{ c.snippet || '（片段为空）' }}</span>
                <span class="cscore num">{{ c.score.toFixed(3) }}</span>
              </div>
            </div>

            <div v-if="!hit.hit_reasons?.length && !hit.matched_chunks?.length" class="muted xs">
              未返回命中理由与片段（请求时未开启 <code class="mono">with_reason</code>，或服务端未产出）。
            </div>
          </div>
        </div>
      </StatePanel>
    </el-card>
  </div>
</template>

<script setup lang="ts">
/**
 * P-8 · 语义人才检索（`/talent/search`，F-3.6，**API-62**）
 *
 * 🔴 本页承载六条最容易写错的契约（`api/search.ts` 已钉死，此处如实呈现）：
 *   1. **`data` 是对象 `{ items, meta }`，不是数组** —— 不套分页壳，不读 `data.page`。
 *   2. **降级只看 `meta.degraded`**：`code=50301` + `meta.mode_used='keyword'` 时
 *      内容**照常渲染**，只挂徽标；而**显式 `mode=keyword` 不标降级**（用户要的就是词法）。
 *   3. **`rerank_used=false` 不算降级** —— 精排失败但保留融合序，`degraded` 仍为 `false`。
 *   4. **权限范围显式提示**：`meta.permission_scope.org_ids` 是服务端推导的有效范围；
 *      请求里的 `filters.org_ids` 只能**收窄**，超出 → `40301`（不静默忽略）。
 *   5. **命中片段已脱敏**（`snippet`），前端**不再二次加工**。
 *   6. 无权限时页面渲染「无访问权限」态，**不谎称没有数据**。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { EDUCATION_LEVEL } from '@/api/enums'
import { ApiError } from '@/api/http'
import { orgApi, type OrgNode } from '@/api/org'
import { searchApi, type SearchMeta, type SearchHit } from '@/api/search'
import { tagApi } from '@/api/tag'
import { dash } from '@/api/types'
import type { LoadStatus } from '@/composables/useAsyncData'
import DegradedBadge from '@/components/DegradedBadge.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const auth = useAuthStore()

const desc =
  '自然语言语义检索（混合召回 + RRF 融合 + 精排）。结果受数据域约束，越权范围会被拒绝而不是被忽略。'

// ---------------- 输入 ----------------
const queryText = ref('')
const mode = ref<'auto' | 'vector' | 'keyword'>('auto')
const topK = ref(10)
const rerank = ref(true)
const withReason = ref(true)
const eduMin = ref<string | null>(null)
const workYearsMin = ref<number | null>(null)
const tagCodes = ref<string[]>([])
const orgIds = ref<string[]>([])

// ---------------- 结果状态 ----------------
const loading = ref(false)
const status = ref<LoadStatus>('idle')
const results = ref<SearchHit[]>([])
const meta = ref<SearchMeta | null>(null)
const code = ref<number | null>(null)
const message = ref<string | null>(null)
const traceId = ref<string | null>(null)

const scopeText = computed(() => {
  const s = meta.value?.permission_scope
  if (!s) return '—'
  if (s.org_ids === null) return '不限部门（全量可见）'
  if (s.org_ids.length === 0) return '仅本人'
  return `${s.org_ids.length} 个部门`
})

async function onSearch(): Promise<void> {
  const q = queryText.value.trim()
  if (!q) {
    // 🔴 `LoadStatus` 没有 `empty` 这个值 —— 空查询回到 `idle`，
    //    由「结果为空 + 已 ready」的判定去驱动空态（StatePanel 的 `empty` 是**展示态**，不是加载态）。
    status.value = 'idle'
    results.value = []
    meta.value = null
    return
  }

  loading.value = true
  status.value = 'loading'
  code.value = null
  message.value = null
  try {
    const filters: Record<string, unknown> = {}
    if (orgIds.value.length) filters.org_ids = [...orgIds.value]
    if (eduMin.value) filters.education_level_min = eduMin.value
    if (workYearsMin.value !== null && workYearsMin.value !== undefined) {
      filters.work_years_min = workYearsMin.value
    }
    if (tagCodes.value.length) filters.tag_codes = [...tagCodes.value]

    const res = await searchApi.search({
      query: q,
      top_k: topK.value,
      mode: mode.value,
      filters: Object.keys(filters).length ? filters : undefined,
      with_reason: withReason.value,
      rerank: rerank.value,
    })

    // 🔴 `data` 是对象；`meta` 在 `data` 内（与「分页壳的顶层 page」不同）
    const payload = res.data
    results.value = payload?.items ?? []
    meta.value = payload?.meta ?? null
    traceId.value = res.traceId
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    results.value = []
    meta.value = null
    code.value = err.code
    message.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

function onClear(): void {
  queryText.value = ''
  results.value = []
  meta.value = null
  status.value = 'idle'
  code.value = null
  message.value = null
}

function openDetail(id: string): void {
  // 🔴 雪花 ID 全程字符串透传
  void router.push({ name: 'talent-detail', params: { id } })
}

function askAbout(id: string): void {
  void router.push({ name: 'talent-ask', query: { talent_id: id } })
}

// ---------------- 参考数据 ----------------
const tagOptions = ref<{ tag_code: string; tag_name: string }[]>([])
const orgTree = ref<OrgNode[]>([])

onMounted(async () => {
  try {
    const res = await tagApi.flat({ status: 1, per_page: 100 })
    tagOptions.value = (res.data ?? []).map((t) => ({ tag_code: t.tag_code, tag_name: t.tag_name }))
  } catch {
    /* 标签下拉失败不影响检索主路径 */
  }
  if (auth.hasPerm('org:view')) {
    try {
      const res = await orgApi.tree({ status: 1 })
      orgTree.value = res.data?.items ?? []
    } catch {
      /* 同上 */
    }
  }
})
</script>

<style scoped>
.search-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.search-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.adv {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.field label {
  width: 130px;
  flex: 0 0 auto;
  font-size: 12.5px;
  color: var(--txt-2);
}
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
.scope-bar {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  background: var(--primary-weak);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 7px 11px;
  margin-bottom: 12px;
  color: var(--txt-2);
}
.hit-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hit {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 11px 13px;
  background: var(--panel);
}
.hit-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.hit-head .score {
  margin-left: auto;
  display: flex;
  align-items: baseline;
  gap: 5px;
}
.hit-head .score .k {
  font-size: 11px;
  color: var(--txt-3);
}
.hit-head .score .v {
  font-size: 14px;
  font-weight: 600;
  color: var(--primary);
}
.ops {
  display: flex;
  gap: 10px;
}
.ops a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover {
  text-decoration: underline;
}
.reasons {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.chunks {
  margin-top: 8px;
  border-top: 1px dashed var(--line);
  padding-top: 7px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.chunk {
  display: grid;
  grid-template-columns: 80px 1fr auto;
  gap: 10px;
  align-items: baseline;
  font-size: 12.5px;
}
.chunk .cfield {
  color: var(--txt-3);
  font-size: 11.5px;
}
.chunk .ctext {
  color: var(--txt-2);
  line-height: 1.6;
}
.chunk .cscore {
  color: var(--txt-3);
  font-size: 11.5px;
}
.mt10 {
  margin-top: 10px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
