<template>
  <div>
    <PageHeader title="标签字典管理" :desc="desc">
      <template #actions>
        <el-button @click="suggestVisible = true">
          <el-icon><MagicStick /></el-icon>提交 AI 建议
        </el-button>
        <el-button v-perm="'tag:create'" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新增标签
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置（AI 不得自由造词是产品红线） -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        <b>AI 不得自由造词</b>：AI 只能「建议」，一律落 <code class="mono">status=0 待审核</code>；
        经人工审核通过后才变为 <code class="mono">1 启用</code>。
        打标时<b>只能选启用项</b> —— 选中停用项后端返回 <code class="mono">40001</code>（不会静默过滤）。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        标签 <code class="mono">tag_code</code> 是<b>业务主键，创建后不可修改</b> ——
        只允许改名 / 分类 / 同义词 / 状态。给档案打标是<b>全量替换</b>（API-56），不是增量追加。
      </template>
    </el-alert>

    <!-- ============ 工具栏 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-radio-group v-model="viewMode" @change="onViewChange">
          <el-radio-button value="tree">三级分类树</el-radio-button>
          <el-radio-button value="flat">平铺列表</el-radio-button>
        </el-radio-group>

        <el-select v-model="statusFilter" clearable placeholder="状态不限" style="width: 140px">
          <el-option v-for="o in TAG_DICT_STATUS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>

        <el-input
          v-model="keywordInput"
          placeholder="标签名 / 编码"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
        />
        <el-button type="primary" @click="reload">查询</el-button>

        <span class="muted xs">
          {{ viewMode === 'tree' ? '树视图返回数组（收在 data）' : '平铺视图走分页壳（data + 同级 page）' }}
        </span>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />受控标签词表
            <span class="sub">{{ viewMode === 'tree' ? `${flatCount} 个标签` : `共 ${total} 个` }}</span>
          </span>
          <el-link type="primary" :underline="false" @click="assignVisible = true">
            给档案打标（全量替换）→
          </el-link>
        </div>
      </template>

      <StatePanel
        :status="emptyNow ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有匹配的标签"
        empty-hint="试试清空筛选条件，或新增一个标签"
        @retry="load"
      >
        <!-- ===== 树视图 ===== -->
        <div v-if="viewMode === 'tree'" class="tree-wrap">
          <el-tree
            :data="tree"
            :props="{ label: 'category', children: 'children' }"
            node-key="key"
            default-expand-all
            :expand-on-click-node="false"
          >
            <template #default="{ data }">
              <!-- 分类节点 -->
              <div v-if="data.level < 3 || data.tags.length === 0" class="node-cat">
                <el-icon><Folder /></el-icon>
                <span>{{ data.category }}</span>
                <span class="muted xs">L{{ data.level }}</span>
              </div>
              <!-- 叶子：分类 + 标签 -->
              <div v-else class="node-leaf">
                <span class="cat">{{ data.category }}</span>
                <span class="tags">
                  <el-tag
                    v-for="t in data.tags"
                    :key="t.tag_id"
                    size="small"
                    :type="tagType(t)"
                    effect="light"
                    class="clickable"
                    @click="openEdit(t)"
                  >
                    {{ t.tag_name }}
                    <span class="code">{{ t.tag_code }}</span>
                  </el-tag>
                </span>
              </div>
            </template>
          </el-tree>
          <div v-if="tree.length === 0" class="muted sm">词表为空。</div>
        </div>

        <!-- ===== 平铺视图 ===== -->
        <template v-else>
          <el-table :data="flat" v-loading="loading" stripe row-key="tag_id">
            <el-table-column label="标签名" min-width="160">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openEdit(row)">
                  {{ row.tag_name }}
                </el-link>
              </template>
            </el-table-column>
            <el-table-column label="编码（不可改）" width="170">
              <template #default="{ row }">
                <span class="mono xs">{{ row.tag_code }}</span>
              </template>
            </el-table-column>
            <el-table-column label="分类" min-width="180">
              <template #default="{ row }">
                <span class="xs muted">
                  {{ [row.category_l1, row.category_l2, row.category_l3].filter(Boolean).join(' / ') || '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="同义词" min-width="180">
              <template #default="{ row }">
                <template v-if="row.synonyms?.length">
                  <el-tag v-for="(s, i) in row.synonyms" :key="i" size="small" effect="plain" type="info">
                    {{ s }}
                  </el-tag>
                </template>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="90">
              <template #default="{ row }">
                <StatusTag :text="row.source_name" :tone="row.source === 1 ? 'purple' : 'plain'" />
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="statusTone(row.status)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <div class="ops">
                  <a @click="openEdit(row)">编辑</a>
                  <a v-if="row.status === 1" @click="toggleStatus(row, 'disable')">停用</a>
                  <a v-else-if="row.status === 2" @click="toggleStatus(row, 'enable')">启用</a>
                  <a v-else @click="openAudit(row)">审核</a>
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
        </template>

        <TraceFooter :trace-id="traceId" />
      </StatePanel>
    </el-card>

    <!-- ============ 新增 / 编辑 ============ -->
    <el-dialog v-model="editVisible" :title="editing ? '编辑标签' : '新增标签'" width="560px">
      <div class="form">
        <div v-if="editing" class="row">
          <span class="k">标签编码</span>
          <span class="mono">{{ editing.tag_code }}</span>
          <span class="muted xs">（业务主键，创建后不可修改）</span>
        </div>
        <div v-else class="row">
          <span class="k">标签编码</span>
          <el-input v-model="form.tag_code" placeholder="留空由后端生成" />
        </div>

        <div class="row">
          <span class="k">标签名</span>
          <el-input v-model="form.tag_name" placeholder="如：数据治理" />
        </div>
        <div class="row">
          <span class="k">一级分类</span>
          <el-input v-model="form.category_l1" placeholder="如：技术能力" />
        </div>
        <div class="row">
          <span class="k">二级分类</span>
          <el-input v-model="form.category_l2" placeholder="可选" />
        </div>
        <div class="row">
          <span class="k">三级分类</span>
          <el-input v-model="form.category_l3" placeholder="可选" />
        </div>
        <div class="row">
          <span class="k">同义词</span>
          <el-select
            v-model="form.synonyms"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="回车添加，如：数据资产治理"
            style="width: 100%"
          />
        </div>
        <div class="muted xs">
          管理员新增的标签<b>直接启用</b>；AI 建议必须走审核（见「提交 AI 建议」）。
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">
          {{ editing ? '保存修改' : '创建并启用' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ AI 建议 ============ -->
    <el-dialog v-model="suggestVisible" title="提交 AI 建议（落待审核）" width="560px">
      <el-alert type="info" :closable="false" show-icon class="mb12">
        <template #title>
          AI 只能<b>建议</b>：提交后状态为 <code class="mono">0 待审核</code>，
          需人工审核通过才可启用。审核通过时可指定最终 <code class="mono">tag_code</code>。
        </template>
      </el-alert>
      <div class="form">
        <div class="row">
          <span class="k">建议标签名</span>
          <el-input v-model="suggestForm.tag_name" placeholder="如：多模态检索" />
        </div>
        <div class="row">
          <span class="k">分类编码</span>
          <el-input v-model="suggestForm.category_code" placeholder="如：TECH" />
        </div>
        <div class="row">
          <span class="k">同义词</span>
          <el-select
            v-model="suggestForm.synonyms"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="回车添加"
            style="width: 100%"
          />
        </div>
        <div class="row">
          <span class="k">建议理由</span>
          <el-input
            v-model="suggestForm.reason"
            type="textarea"
            :rows="2"
            placeholder="为什么需要这个标签（供审核人判断）"
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="suggestVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitSuggest">提交建议</el-button>
      </template>
    </el-dialog>

    <!-- ============ 审核建议 ============ -->
    <el-dialog v-model="auditVisible" title="审核 AI 建议" width="520px">
      <div v-if="auditing" class="form">
        <div class="row">
          <span class="k">标签名</span>
          <span>{{ auditing.tag_name }}</span>
        </div>
        <div class="row">
          <span class="k">当前编码</span>
          <span class="mono xs">{{ auditing.tag_code }}</span>
        </div>
        <div class="row">
          <span class="k">最终编码</span>
          <el-input v-model="auditForm.tag_code" placeholder="留空沿用当前编码" />
        </div>
        <div class="row">
          <span class="k">驳回理由</span>
          <el-input v-model="auditForm.reject_reason" placeholder="选择「驳回」时必填" />
        </div>
      </div>
      <template #footer>
        <el-button @click="auditVisible = false">取消</el-button>
        <el-button :loading="submitting" @click="submitAudit('reject')">驳回</el-button>
        <el-button type="primary" :loading="submitting" @click="submitAudit('approve')">
          通过并启用
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ 给档案打标（API-55 / 56） ============ -->
    <el-dialog v-model="assignVisible" title="给档案打标（全量替换）" width="620px">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>
          <b>全量替换</b>：提交的标签集合就是最终结果，未勾选的会被移除。
          只能选 <code class="mono">1 启用</code> 的标签 —— 选中停用项后端返回
          <code class="mono">40001</code>。
        </template>
      </el-alert>

      <div class="form">
        <div class="row">
          <span class="k">档案 ID</span>
          <el-input
            v-model="assignTalentId"
            placeholder="雪花 ID 字符串"
            @keyup.enter="loadTalentTags"
          >
            <template #append>
              <el-button @click="loadTalentTags">读取</el-button>
            </template>
          </el-input>
        </div>

        <div v-if="assignLoaded" class="row">
          <span class="k">标签</span>
          <el-select
            v-model="assignCodes"
            multiple
            filterable
            clearable
            placeholder="只能取启用项"
            style="width: 100%"
          >
            <el-option
              v-for="t in enabledTags"
              :key="t.tag_code"
              :label="`${t.tag_name}（${t.tag_code}）`"
              :value="t.tag_code"
            />
          </el-select>
        </div>

        <div v-if="assignLoaded" class="muted xs">
          当前库中：{{ assignCurrent.length }} 个标签；本次将提交 {{ assignCodes.length }} 个。
        </div>
      </div>

      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!assignLoaded" :loading="submitting" @click="submitAssign">
          提交替换
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-12 · 标签字典管理（`/talent/tag-dict`，F-3.4 / F-3.4.1，**API-50 ~ API-56**）
 *
 * 🔴 本页承载四条契约（`api/tag.ts` 已钉死）：
 *   1. **AI 不得自由造词**：建议一律 `status=0`；打标只能选 `1 启用`，
 *      选停用项 → `40001`（**不静默过滤**）。
 *   2. **`view` 决定响应形状**：`tree` → `data` 是**树数组**；`flat` → **分页壳**
 *      （`data` 数组 + **同级** `page`）。两种形状不可混用 —— 本页分两条加载路径。
 *   3. **API-56 是全量替换**（提交的集合即最终结果），不是增量追加。
 *   4. **`tag_code` 不可修改**（业务主键）—— 编辑时只读展示。
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '@/api/http'
import { TAG_DICT_STATUS } from '@/api/enums'
import {
  tagApi,
  type TagDictItem,
  type TagTreeNode,
} from '@/api/tag'
import type { LoadStatus } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc = '受控标签词表（三级分类）。AI 只能建议，人工审核通过后才可启用。'

// ---------------- 加载状态 ----------------
const viewMode = ref<'tree' | 'flat'>('tree')
const loading = ref(false)
const submitting = ref(false)
const status = ref<LoadStatus>('idle')
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)
const traceId = ref<string | null>(null)

const tree = ref<TagTreeNode[]>([])
const flat = ref<TagDictItem[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(20)

const statusFilter = ref<number | null>(null)
const keywordInput = ref('')

const flatCount = computed(() => {
  let n = 0
  const walk = (nodes: TagTreeNode[]) => {
    for (const n2 of nodes) {
      n += n2.tags?.length ?? 0
      if (n2.children?.length) walk(n2.children)
    }
  }
  walk(tree.value)
  return n
})

const emptyNow = computed(() =>
  viewMode.value === 'tree' ? tree.value.length === 0 : flat.value.length === 0,
)

async function loadTree(): Promise<void> {
  const res = await tagApi.tree()
  // 🔴 `data` 是树数组，不是 `{items}`
  tree.value = res.data ?? []
  traceId.value = res.traceId
}

async function loadFlat(): Promise<void> {
  const res = await tagApi.flat({
    status: statusFilter.value ?? undefined,
    keyword: keywordInput.value.trim() || undefined,
    page: page.value,
    per_page: perPage.value,
  })
  // 🔴 `data` 是数组 + **同级** `page`
  flat.value = res.data ?? []
  total.value = res.page?.total ?? flat.value.length
  if (res.page?.per_page) perPage.value = res.page.per_page
  traceId.value = res.traceId
}

async function load(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    if (viewMode.value === 'tree') await loadTree()
    else await loadFlat()
    status.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    tree.value = []
    flat.value = []
    total.value = 0
    errCode.value = err.code
    errMsg.value = err.displayMessage
    traceId.value = err.traceId
    status.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

function onViewChange(): void {
  page.value = 1
  void load()
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

function tagType(t: TagDictItem): 'success' | 'warning' | 'info' {
  if (t.status === 1) return 'success'
  if (t.status === 0) return 'warning'
  return 'info'
}

function statusTone(code: number): 'success' | 'warning' | 'info' {
  if (code === 1) return 'success'
  if (code === 0) return 'warning'
  return 'info'
}

// ---------------- 新增 / 编辑（API-51 / API-52） ----------------
const editVisible = ref(false)
const editing = ref<TagDictItem | null>(null)
const form = ref({
  tag_code: '',
  tag_name: '',
  category_l1: '',
  category_l2: '',
  category_l3: '',
  synonyms: [] as string[],
})

function openCreate(): void {
  editing.value = null
  form.value = {
    tag_code: '',
    tag_name: '',
    category_l1: '',
    category_l2: '',
    category_l3: '',
    synonyms: [],
  }
  editVisible.value = true
}

function openEdit(t: TagDictItem): void {
  editing.value = t
  form.value = {
    tag_code: t.tag_code,
    tag_name: t.tag_name,
    category_l1: t.category_l1 ?? '',
    category_l2: t.category_l2 ?? '',
    category_l3: t.category_l3 ?? '',
    synonyms: [...(t.synonyms ?? [])],
  }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  if (!form.value.tag_name.trim()) {
    notify.warn('标签名不能为空')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      // 🔴 不提交 `tag_code`（业务主键不可改）
      await tagApi.update(editing.value.tag_id, {
        tag_name: form.value.tag_name.trim(),
        category_l1: form.value.category_l1 || undefined,
        category_l2: form.value.category_l2 || undefined,
        category_l3: form.value.category_l3 || undefined,
        synonyms: form.value.synonyms,
      })
      notify.ok('已保存')
    } else {
      await tagApi.create({
        tag_code: form.value.tag_code.trim() || undefined,
        tag_name: form.value.tag_name.trim(),
        category_l1: form.value.category_l1 || undefined,
        category_l2: form.value.category_l2 || undefined,
        category_l3: form.value.category_l3 || undefined,
        synonyms: form.value.synonyms,
      })
      notify.ok('已创建并启用')
    }
    editVisible.value = false
    await load()
  } catch (e) {
    notify.fail(e, editing.value ? '保存失败' : '创建失败')
  } finally {
    submitting.value = false
  }
}

/** 启停（走 API-52 的 `action`）。 */
async function toggleStatus(t: TagDictItem, action: 'enable' | 'disable'): Promise<void> {
  const okToGo = await notify.confirm(
    `确认${action === 'enable' ? '启用' : '停用'}标签「${t.tag_name}」？` +
      (action === 'disable' ? '停用后不能再用于打标（已打的标签保留）。' : ''),
  )
  if (!okToGo) return
  try {
    await tagApi.update(t.tag_id, { action })
    notify.ok(action === 'enable' ? '已启用' : '已停用')
    await load()
  } catch (e) {
    notify.fail(e, '操作失败')
  }
}

// ---------------- AI 建议（API-53）与审核（API-54） ----------------
const suggestVisible = ref(false)
const suggestForm = ref({
  tag_name: '',
  category_code: '',
  synonyms: [] as string[],
  reason: '',
})

async function submitSuggest(): Promise<void> {
  if (!suggestForm.value.tag_name.trim() || !suggestForm.value.category_code.trim()) {
    notify.warn('标签名与分类编码必填')
    return
  }
  submitting.value = true
  try {
    const res = await tagApi.suggest({
      tag_name: suggestForm.value.tag_name.trim(),
      category_code: suggestForm.value.category_code.trim(),
      synonyms: suggestForm.value.synonyms,
      reason: suggestForm.value.reason || undefined,
    })
    notify.ok(
      `建议已提交（${res.data?.status_name || '待审核'}）。审核通过前不会被启用，也不会可用于打标。`,
    )
    suggestVisible.value = false
    await load()
  } catch (e) {
    notify.fail(e, '提交建议失败')
  } finally {
    submitting.value = false
  }
}

const auditVisible = ref(false)
const auditing = ref<TagDictItem | null>(null)
const auditForm = ref({ tag_code: '', reject_reason: '' })

function openAudit(t: TagDictItem): void {
  auditing.value = t
  auditForm.value = { tag_code: t.tag_code, reject_reason: '' }
  auditVisible.value = true
}

async function submitAudit(decision: 'approve' | 'reject'): Promise<void> {
  const t = auditing.value
  if (!t) return
  // 🔴 驳回必须给理由（`test_reject_requires_reason`）
  if (decision === 'reject' && !auditForm.value.reject_reason.trim()) {
    notify.warn('驳回必须填写理由')
    return
  }
  submitting.value = true
  try {
    await tagApi.reviewSuggestion(t.tag_id, {
      decision,
      tag_code: decision === 'approve' ? auditForm.value.tag_code.trim() || undefined : undefined,
      reject_reason: decision === 'reject' ? auditForm.value.reject_reason.trim() : undefined,
    })
    notify.ok(decision === 'approve' ? '已通过并启用' : '已驳回')
    auditVisible.value = false
    await load()
  } catch (e) {
    notify.fail(e, '审核失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 档案打标（API-55 / API-56） ----------------
const assignVisible = ref(false)
const assignTalentId = ref('')
const assignLoaded = ref(false)
const assignCodes = ref<string[]>([])
const assignCurrent = ref<string[]>([])
const enabledTags = computed(() => {
  const out: TagDictItem[] = []
  const walk = (nodes: TagTreeNode[]) => {
    for (const n of nodes) {
      for (const t of n.tags ?? []) if (t.status === 1) out.push(t)
      if (n.children?.length) walk(n.children)
    }
  }
  walk(tree.value)
  // 树视图可能未加载（当前在 flat 模式），退化为用 flat 数据兜底
  if (out.length === 0) return flat.value.filter((t) => t.status === 1)
  return out
})

async function loadTalentTags(): Promise<void> {
  const id = assignTalentId.value.trim()
  if (!/^\d+$/.test(id)) {
    notify.warn('档案 ID 必须是纯数字字符串（雪花 ID）')
    return
  }
  try {
    const res = await tagApi.talentTags(id)
    assignCurrent.value = (res.data?.tags ?? []).map((t) => t.tag_code)
    assignCodes.value = [...assignCurrent.value]
    assignLoaded.value = true
    if (viewMode.value !== 'tree') await loadTree()
  } catch (e) {
    // 主档接口未交付时这里会 40401 —— 明确提示，不装作是「没有标签」
    if (e instanceof ApiError && e.isNotFound) {
      notify.warn('读取不到该档案（可能 ID 错误，或档案主档接口 API-30~35 尚未交付）。')
    } else {
      notify.fail(e, '读取档案标签失败')
    }
  }
}

async function submitAssign(): Promise<void> {
  const id = assignTalentId.value.trim()
  if (!id) return
  submitting.value = true
  try {
    await tagApi.assign(id, assignCodes.value)
    notify.ok('已全量替换该档案的标签')
    assignVisible.value = false
  } catch (e) {
    // 🔴 选中停用项 → 40001（后端不静默过滤）
    if (e instanceof ApiError && e.code === 40001) {
      notify.warn('提交失败：包含不可用的标签（仅「启用」状态可打标）。请刷新词表后重试。')
    } else {
      notify.fail(e, '打标失败')
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
.tree-wrap {
  max-height: 62vh;
  overflow: auto;
}
/* 🔴 修复「标签文字显示不全」（2026-09-23）：
   Element Plus 给 `.el-tree-node__content` 固定行高 26px（--el-tree-node-content-height），
   而本页叶子节点（.node-leaf）的标签是 flex-wrap **多行**流 —— 超出 26px 的部分被
   下一行节点盖住，表现为「每个标签的文字只剩一半」。
   height:auto 让多行内容自然撑开；min-height:26px 保住单行节点（含展开箭头）的原状。 */
:deep(.el-tree-node__content) {
  height: auto;
  min-height: 26px;
}
.node-cat {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}
.node-leaf {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 0;
  flex-wrap: wrap;
}
.node-leaf .cat {
  font-size: 12.5px;
  color: var(--txt-2);
  min-width: 110px;
}
.node-leaf .tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex: 1;
}
.code {
  font-family: var(--mono);
  font-size: 10.5px;
  opacity: 0.7;
  margin-left: 4px;
}
.clickable {
  cursor: pointer;
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
.form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.form .row {
  display: grid;
  grid-template-columns: 84px 1fr;
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
.mb12 {
  margin-bottom: 12px;
}
</style>
