<template>
  <div>
    <PageHeader title="重复档案合并" :desc="desc">
      <template #actions>
        <el-button @click="rollbackVisible = true">
          <el-icon><RefreshLeft /></el-icon>回滚合并
        </el-button>
        <el-button :disabled="loading" @click="reload">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置说明 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        <b>联系方式在候选对里是脱敏的</b>（如 <code class="mono">138****8888</code>）——
        合并决策<b>不依赖明文</b>，前端也不会去请求明文来「人工核对」。
        未显式指定策略的字段按<b>「非空优先」</b>自动取值。
      </template>
    </el-alert>

    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        回滚依赖<b>合并快照</b>。快照缺失时返回 <code class="mono">42203</code>，
        后端<b>绝不做尽力还原</b>；此时界面明确显示「无法回滚」，而不是「回滚失败请重试」。
        同一对重复合并返回 <code class="mono">40901</code>。
      </template>
    </el-alert>

    <!-- ============ 筛选 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-radio-group v-model="statusFilter">
          <el-radio-button :value="0">待处理</el-radio-button>
          <el-radio-button :value="1">已合并</el-radio-button>
          <el-radio-button :value="2">已忽略</el-radio-button>
          <el-radio-button :value="null">全部</el-radio-button>
        </el-radio-group>
        <el-input
          v-model="keywordInput"
          placeholder="按姓名过滤（本页内）"
          clearable
          style="width: 200px"
        />
        <el-button type="primary" @click="reload">查询</el-button>
      </div>
    </el-card>

    <!-- ============ 候选对列表 ============ -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />重复候选对
            <span class="sub">共 {{ total }} 组</span>
          </span>
          <span class="muted xs">按相似度降序；精确键豁免阈值的项相似度显示为 —</span>
        </div>
      </template>

      <StatePanel
        :status="status === 'ready' && filtered.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有重复候选对"
        empty-hint="当前状态下没有待处理的重复档案"
        @retry="load"
      >
        <div class="pair-list">
          <div v-for="p in filtered" :key="p.pair_id" class="pair">
            <div class="pair-head">
              <div class="sim">
                <span class="k">相似度</span>
                <span class="v num">{{ fmtSimilarity(p.similarity) }}</span>
              </div>
              <StatusTag :text="p.status_name" :tone="pairTone(p.status)" />
              <div class="match-fields">
                <span class="muted xs">命中：</span>
                <el-tag v-for="(f, i) in p.match_fields" :key="i" size="small" type="info" effect="plain">
                  {{ f }}
                </el-tag>
                <span v-if="!p.match_fields?.length" class="muted xs">—</span>
              </div>
              <div class="ops">
                <a @click="openCompare(p)">并排对比</a>
                <template v-if="p.status === 0">
                  <a @click="openCompare(p)">合并</a>
                  <a @click="onIgnore(p)">忽略</a>
                </template>
              </div>
            </div>

            <div class="pair-body">
              <BriefCard :brief="p.talent_a" tone="a" />
              <div class="vs">VS</div>
              <BriefCard :brief="p.talent_b" tone="b" />
            </div>
          </div>
        </div>

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
        <TraceFooter :trace-id="traceId" extra="联系方式为服务端脱敏形态" />
      </StatePanel>
    </el-card>

    <!-- ============ 并排对比 + 合并 ============ -->
    <el-dialog v-model="compareVisible" title="并排对比与合并" width="920px" top="6vh">
      <div v-if="current" class="compare">
        <el-alert type="info" :closable="false" show-icon class="mb12">
          <template #title>
            选择<b>保留主档</b>（另一档合并后软删）。未指定策略的字段按「非空优先」；
            显式指定 <code class="mono">master</code> / <code class="mono">slave</code> 优先于非空优先。
          </template>
        </el-alert>

        <table class="cmp-table">
          <thead>
            <tr>
              <th class="f">字段</th>
              <th>
                <el-radio v-model="masterSide" value="a">保留 A</el-radio>
              </th>
              <th>
                <el-radio v-model="masterSide" value="b">保留 B</el-radio>
              </th>
              <th class="s">取值</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in compareFields" :key="f.key">
              <td class="f">{{ f.label }}</td>
              <td :class="{ master: masterSide === 'a' }">{{ dash(f.a) }}</td>
              <td :class="{ master: masterSide === 'b' }">{{ dash(f.b) }}</td>
              <td class="s">
                <el-select
                  v-model="strategy[f.key]"
                  clearable
                  placeholder="非空优先"
                  style="width: 130px"
                >
                  <el-option value="master" label="取主档" />
                  <el-option value="slave" label="取从档" />
                </el-select>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="opt-row">
          <el-checkbox v-model="mergeTags">合并双方标签（取并集）</el-checkbox>
          <el-checkbox v-model="mergeDetail">合并履历明细</el-checkbox>
        </div>

        <div class="muted xs">
          ℹ️ 合并会写入变更留痕（来源 = 合并），并作为回滚依据。合并后主档会尝试重索引向量；
          <b>无向量时 <code class="mono">vector_reindexed=false</code> 不代表失败</b>。
        </div>
      </div>

      <template #footer>
        <el-button @click="compareVisible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="submitting"
          :disabled="current?.status !== 0"
          @click="doMerge"
        >
          确认合并
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ 回滚 ============ -->
    <el-dialog v-model="rollbackVisible" title="回滚合并" width="520px">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>
          回滚按 <code class="mono">merge_id</code> 执行（不是 <code class="mono">pair_id</code>）。
          若合并快照缺失，后端返回 <code class="mono">42203</code> 且<b>不做尽力还原</b>。
        </template>
      </el-alert>
      <el-input v-model="rollbackMergeId" placeholder="合并记录 ID（merge_id）" />
      <div class="muted xs mt8">
        提示：合并成功后返回体里的 <code class="mono">pair_id</code> / <code class="mono">master_id</code>
        <b>不是</b> <code class="mono">merge_id</code>，请从变更留痕或后端记录中获取。
      </div>
      <template #footer>
        <el-button @click="rollbackVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="doRollback">执行回滚</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-11 · 重复档案合并（`/talent/dedup`，F-3.5 / F-3.10，**API-57 ~ API-61**）
 *
 * 🔴 本页承载五条契约（`api/dedup.ts` 已钉死）：
 *   1. **合并幂等返回 `40901`**（不是 `40903`）—— 「同一对重复合并」是**唯一约束冲突**，
 *      前端按「已经合并过了」提示，而不是按幂等回放当成功。
 *   2. **回滚依赖快照**：缺快照 → **`42203`**，必须渲染成「无法回滚（缺少合并快照）」，
 *      **不是**通用错误（通用错误会诱导用户反复重试，而它永远不会成功）。
 *   3. **回滚用 `merge_id`**，不是 `pair_id` —— 两者不同（一次合并 = 一条 merge 记录）。
 *   4. **`field_strategy` 未指定字段走「非空优先」**；显式 `master`/`slave` 优先于它。
 *   5. **候选对里的联系方式是脱敏的** —— 界面**不提供**「查看明文」入口。
 */
import { computed, onMounted, ref, defineComponent, h } from 'vue'

import { dedupApi, type DedupMergeRequest, type DupCandidate, type TalentBrief } from '@/api/dedup'
import { ApiError } from '@/api/http'
import { dash } from '@/api/types'
import { fmtSimilarity } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc =
  '识别并合并重复档案。合并写留痕、可回滚；联系方式全程密文，不参与人工核对。'

// ---------------- 简易「档案摘要卡」内联组件 ----------------
/**
 * 用 `h()` 而不是再开一个 `.vue` 文件：本卡只在**本页**使用，
 * 且仅为排版服务；独立成文件会拉长「读一个页面要看 N 个文件」的链路。
 */
const BriefCard = defineComponent({
  name: 'BriefCard',
  props: {
    brief: { type: Object as () => TalentBrief, required: true },
    tone: { type: String, required: true },
  },
  setup(props) {
    return () =>
      h('div', { class: ['brief', props.tone] }, [
        h('div', { class: 'bhead' }, [
          h('b', props.brief.name),
          h('span', { class: 'muted xs mono' }, props.brief.talent_id),
        ]),
        h('div', { class: 'brow' }, [h('span', { class: 'k' }, '学校'), h('span', { class: 'v' }, dash(props.brief.school))]),
        h('div', { class: 'brow' }, [h('span', { class: 'k' }, '专业'), h('span', { class: 'v' }, dash(props.brief.major))]),
        h('div', { class: 'brow' }, [h('span', { class: 'k' }, '学历'), h('span', { class: 'v' }, dash(props.brief.education_level))]),
        h('div', { class: 'brow' }, [
          h('span', { class: 'k' }, '年限'),
          h('span', { class: 'v' }, props.brief.work_years === null ? '—' : `${props.brief.work_years} 年`),
        ]),
        h('div', { class: 'brow' }, [
          h('span', { class: 'k' }, '手机'),
          h('span', { class: 'v masked', title: '服务端已脱敏' }, props.brief.phone_masked ? `🔒 ${props.brief.phone_masked}` : '—'),
        ]),
        h('div', { class: 'brow' }, [
          h('span', { class: 'k' }, '邮箱'),
          h('span', { class: 'v masked', title: '服务端已脱敏' }, props.brief.email_masked ? `🔒 ${props.brief.email_masked}` : '—'),
        ]),
        h('div', { class: 'btags' }, props.brief.tags?.length
          ? props.brief.tags.map((t) => h('span', { class: 'tg' }, t))
          : [h('span', { class: 'muted xs' }, '无标签')]),
      ])
  },
})

// ---------------- 列表 ----------------
const loading = ref(false)
const submitting = ref(false)
const status = ref<LoadStatus>('idle')
const all = ref<DupCandidate[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

const statusFilter = ref<number | null>(0)
const keywordInput = ref('')

/** 关键字过滤只作用于**当前页**（后端无「按姓名筛重复对」参数，故不谎称是全量筛选）。 */
const filtered = computed(() => {
  const k = keywordInput.value.trim()
  if (!k) return all.value
  return all.value.filter(
    (p) => p.talent_a.name.includes(k) || p.talent_b.name.includes(k),
  )
})

async function load(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const res = await dedupApi.list({
      status: statusFilter.value === null ? undefined : statusFilter.value,
      page: page.value,
      per_page: perPage.value,
    })
    all.value = res.data ?? []
    total.value = res.page?.total ?? all.value.length
    if (res.page?.per_page) perPage.value = res.page.per_page
    traceId.value = res.traceId
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    all.value = []
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
function onPageChange(p: number): void {
  page.value = p
  void load()
}
function onSizeChange(s: number): void {
  perPage.value = s
  page.value = 1
  void load()
}

function pairTone(code: number): 'success' | 'info' | 'plain' {
  if (code === 1) return 'success'
  if (code === 2) return 'info'
  return 'plain'
}

// ---------------- 对比 / 合并 ----------------
const compareVisible = ref(false)
const current = ref<DupCandidate | null>(null)
const masterSide = ref<'a' | 'b'>('a')
/** 字段名 → `master` / `slave`。 */
const strategy = ref<Record<string, 'master' | 'slave' | ''>>({})
const mergeTags = ref(true)
const mergeDetail = ref(true)

interface CompareField {
  key: string
  label: string
  a: string | number | null
  b: string | number | null
}

const compareFields = computed<CompareField[]>(() => {
  const c = current.value
  if (!c) return []
  const a = c.talent_a
  const b = c.talent_b
  return [
    { key: 'name', label: '姓名', a: a.name, b: b.name },
    { key: 'school', label: '学校', a: a.school, b: b.school },
    { key: 'major', label: '专业', a: a.major, b: b.major },
    { key: 'education_level', label: '学历', a: a.education_level, b: b.education_level },
    { key: 'work_years', label: '工作年限', a: a.work_years, b: b.work_years },
    // 🔴 脱敏值仅用于「展示差异」，**不提供解密入口**
    { key: 'phone', label: '手机（脱敏）', a: a.phone_masked, b: b.phone_masked },
    { key: 'email', label: '邮箱（脱敏）', a: a.email_masked, b: b.email_masked },
  ]
})

function openCompare(p: DupCandidate): void {
  current.value = p
  masterSide.value = 'a'
  strategy.value = {}
  mergeTags.value = true
  mergeDetail.value = true
  compareVisible.value = true
}

function buildStrategy(): Record<string, 'master' | 'slave'> | undefined {
  const out: Record<string, 'master' | 'slave'> = {}
  for (const [k, v] of Object.entries(strategy.value)) {
    if (v === 'master' || v === 'slave') out[k] = v
  }
  return Object.keys(out).length ? out : undefined
}

async function doMerge(): Promise<void> {
  const c = current.value
  if (!c) return

  const masterId = masterSide.value === 'a' ? c.talent_a.talent_id : c.talent_b.talent_id
  const slaveName = masterSide.value === 'a' ? c.talent_b.name : c.talent_a.name
  const keepName = masterSide.value === 'a' ? c.talent_a.name : c.talent_b.name

  const okToGo = await notify.confirm(
    `将保留「${keepName}」为主档，「${slaveName}」合并后软删。合并不可直接撤销，需依赖快照回滚。是否继续？`,
    '确认合并',
  )
  if (!okToGo) return

  const payload: DedupMergeRequest = {
    master_id: masterId,
    field_strategy: buildStrategy(),
    merge_tags: mergeTags.value,
    merge_detail: mergeDetail.value,
  }

  submitting.value = true
  try {
    const res = await dedupApi.merge(c.pair_id, payload)
    const out = res.data
    notify.ok(
      `已合并（主档 ${out?.master_id ?? masterId}）` +
        (out && out.vector_reindexed === false ? '；主档未重索引向量（无向量，非失败）' : ''),
    )
    compareVisible.value = false
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40901) {
      notify.warn('该候选对已经合并过了（唯一约束冲突 40901），请刷新列表查看最新状态。')
      compareVisible.value = false
      await load()
    } else {
      notify.fail(e, '合并失败')
    }
  } finally {
    submitting.value = false
  }
}

async function onIgnore(p: DupCandidate): Promise<void> {
  const okToGo = await notify.confirm(
    `忽略候选对（${p.talent_a.name} ↔ ${p.talent_b.name}）？忽略后可随时改回待处理。`,
  )
  if (!okToGo) return
  try {
    await dedupApi.ignore(p.pair_id)
    notify.ok('已忽略该候选对')
    await load()
  } catch (e) {
    notify.fail(e, '忽略失败')
  }
}

// ---------------- 回滚 ----------------
const rollbackVisible = ref(false)
const rollbackMergeId = ref('')

async function doRollback(): Promise<void> {
  const id = rollbackMergeId.value.trim()
  if (!id) {
    notify.warn('请填写 merge_id')
    return
  }
  submitting.value = true
  try {
    await dedupApi.rollback(id)
    notify.ok('已回滚合并')
    rollbackVisible.value = false
    rollbackMergeId.value = ''
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 42203) {
      // 🔴 明确是「缺快照」，不是「重试就能好」
      notify.warn('无法回滚：该次合并缺少快照（42203）。后端不会做尽力还原，请联系管理员核查变更留痕。')
    } else {
      notify.fail(e, '回滚失败')
    }
  } finally {
    submitting.value = false
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
.pair-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pair {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 11px 13px;
  background: var(--panel);
}
.pair-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.sim {
  display: flex;
  align-items: baseline;
  gap: 5px;
}
.sim .k {
  font-size: 11px;
  color: var(--txt-3);
}
.sim .v {
  font-size: 14px;
  font-weight: 600;
  color: var(--primary);
}
.match-fields {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.ops {
  margin-left: auto;
  display: flex;
  gap: 11px;
}
.ops a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover {
  text-decoration: underline;
}
.pair-body {
  display: grid;
  grid-template-columns: 1fr 46px 1fr;
  gap: 10px;
  align-items: stretch;
}
.vs {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11.5px;
  color: var(--txt-3);
  font-weight: 600;
}
.brief {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 9px 11px;
  background: var(--panel-2, #fafbfd);
}
.brief.a {
  border-left: 3px solid var(--primary);
}
.brief.b {
  border-left: 3px solid var(--purple);
}
.brief .bhead {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.brief .brow {
  display: grid;
  grid-template-columns: 62px 1fr;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.9;
}
.brief .brow .k {
  color: var(--txt-3);
  font-size: 11.5px;
}
.brief .brow .v.masked {
  color: var(--txt-3);
}
.brief .btags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.brief .tg {
  font-size: 11px;
  background: var(--primary-weak);
  color: var(--primary);
  border-radius: 3px;
  padding: 0 6px;
}
.cmp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.cmp-table th,
.cmp-table td {
  border: 1px solid var(--line);
  padding: 7px 10px;
  text-align: left;
  vertical-align: middle;
}
.cmp-table th {
  background: var(--panel-2, #fafbfd);
  font-weight: 600;
  font-size: 12px;
}
.cmp-table td.f,
.cmp-table th.f {
  width: 130px;
  color: var(--txt-3);
}
.cmp-table td.s,
.cmp-table th.s {
  width: 150px;
}
.cmp-table td.master {
  background: var(--primary-weak);
  font-weight: 500;
}
.opt-row {
  display: flex;
  gap: 18px;
  align-items: center;
  margin: 12px 0 8px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.mt8 {
  margin-top: 8px;
}
.mb12 {
  margin-bottom: 12px;
}
:deep(.brief .brow .v) {
  word-break: break-all;
}
</style>
