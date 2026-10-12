<template>
  <div>
    <PageHeader title="Prompt 版本管理" :desc="desc">
      <template #actions>
        <el-button :disabled="loading" @click="load">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button v-perm="'ai:prompt:create'" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新建模板
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置（回滚不改历史是审计要求） -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        <b>回滚 = 追加新版本</b>（不是改历史行）—— 历史版本是审计证据，不可篡改。
        因此回滚后 <code class="mono">version</code> 会<b>继续增长</b>，不会回退到旧号。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        同一 <code class="mono">prompt_code</code> 族内<b>只能有一个 <code class="mono">status=1 启用</code></b>；
        提交与当前启用版本<b>内容完全相同</b>的修改会返回 <code class="mono">42201</code>（无意义的改版）。
      </template>
    </el-alert>

    <!-- ============ 筛选 ============ -->
    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input
          v-model="codeFilter"
          placeholder="prompt_code"
          clearable
          style="width: 190px"
          @keyup.enter="reload"
        />
        <el-input
          v-model="sceneFilter"
          placeholder="场景 code"
          clearable
          style="width: 160px"
          @keyup.enter="reload"
        />
        <el-select v-model="statusFilter" clearable placeholder="状态不限" style="width: 140px">
          <el-option v-for="o in PROMPT_STATUS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-checkbox v-model="groupByCode">按 prompt_code 归组</el-checkbox>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />Prompt 模板
            <span class="sub">{{ grouped.length }} 个族 · {{ rows.length }} 个版本</span>
          </span>
          <span class="muted xs">版本只增不减；每个族内仅一个启用版本</span>
        </div>
      </template>

      <StatePanel
        :status="status === 'ready' && rows.length === 0 ? 'empty' : status"
        :loading="loading"
        :code="errCode"
        :message="errMsg"
        :trace-id="traceId"
        :rows="6"
        empty-title="没有 Prompt 模板"
        empty-hint="当前筛选条件下没有模板，试试新建一个"
        @retry="load"
      >
        <!-- 按族归组视图 -->
        <div v-if="groupByCode" class="groups">
          <div v-for="g in grouped" :key="g.code" class="group">
            <div class="ghead">
              <b class="mono">{{ g.code }}</b>
              <span class="muted xs">{{ g.items[0]?.scene_name || g.items[0]?.scene }}</span>
              <el-tag v-if="enabledOf(g)" size="small" type="success" effect="light">
                启用版本 v{{ enabledOf(g)?.version }}
              </el-tag>
              <el-tag v-else size="small" type="info" effect="light">无启用版本</el-tag>
              <span class="muted xs">共 {{ g.items.length }} 个版本</span>
            </div>
            <el-table :data="g.items" size="small" row-key="id">
              <el-table-column label="版本" width="80">
                <template #default="{ row }">
                  <span class="mono">v{{ row.version }}</span>
                </template>
              </el-table-column>
              <el-table-column label="场景" width="130">
                <template #default="{ row }">{{ row.scene_name || row.scene }}</template>
              </el-table-column>
              <el-table-column label="内容预览" min-width="300">
                <template #default="{ row }">
                  <span class="preview" :title="row.content">{{ row.content }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <StatusTag :text="row.status_name" :tone="promptTone(row.status)" />
                </template>
              </el-table-column>
              <el-table-column label="更新时间" width="140">
                <template #default="{ row }">
                  <span class="muted xs">{{ fmtDateTime(row.updated_at) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="180" fixed="right">
                <template #default="{ row }">
                  <div class="ops">
                    <a @click="openDetail(row)">查看</a>
                    <a @click="openEdit(row)">改版</a>
                    <a v-if="row.status !== 1" @click="onRollback(g, row)">回滚到此</a>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <!-- 平铺视图 -->
        <template v-else>
          <el-table :data="rows" v-loading="loading" stripe row-key="id">
            <el-table-column label="prompt_code" width="180">
              <template #default="{ row }">
                <span class="mono">{{ row.prompt_code }}</span>
              </template>
            </el-table-column>
            <el-table-column label="场景" width="140">
              <template #default="{ row }">{{ row.scene_name || row.scene }}</template>
            </el-table-column>
            <el-table-column label="版本" width="80">
              <template #default="{ row }">
                <span class="mono">v{{ row.version }}</span>
              </template>
            </el-table-column>
            <el-table-column label="内容预览" min-width="320">
              <template #default="{ row }">
                <span class="preview" :title="row.content">{{ row.content }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="promptTone(row.status)" />
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="140">
              <template #default="{ row }">
                <span class="muted xs">{{ fmtDateTime(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <div class="ops">
                  <a @click="openDetail(row)">查看</a>
                  <a @click="openEdit(row)">改版</a>
                  <a v-if="row.status !== 1" @click="onRollback(null, row)">回滚到此</a>
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

        <TraceFooter :trace-id="traceId" extra="回滚 = 追加新版本，版本号继续增长" />
      </StatePanel>
    </el-card>

    <!-- ============ 新建 ============ -->
    <el-dialog v-model="createVisible" title="新建 Prompt 模板" width="680px">
      <div class="form">
        <div class="row">
          <span class="k">prompt_code</span>
          <el-input v-model="createForm.prompt_code" placeholder="如 resume_extract_v1" />
        </div>
        <div class="row">
          <span class="k">场景</span>
          <el-input v-model="createForm.scene" placeholder="如 resume_parse" />
        </div>
        <div class="row">
          <span class="k">内容</span>
          <el-input
            v-model="createForm.content"
            type="textarea"
            :rows="10"
            placeholder="Prompt 正文（支持 {{变量}} 占位）"
          />
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="createForm.status">
            <el-radio :value="0">草稿</el-radio>
            <el-radio :value="1">启用</el-radio>
          </el-radio-group>
          <span class="muted xs">同族内启用新的会顶掉旧的</span>
        </div>
      </div>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 改版 ============ -->
    <el-dialog v-model="editVisible" title="修改 Prompt（生成新版本）" width="760px">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>保存后会<b>生成一个新版本</b>，当前版本 v{{ editing?.version }} 原样保留</template>
        <template #default>
          若提交内容与当前版本<b>完全一致</b>，后端返回 <code class="mono">42201</code>（无意义改版）。
        </template>
      </el-alert>
      <div v-if="editing" class="form">
        <div class="row">
          <span class="k">当前版本</span>
          <span class="mono">v{{ editing.version }}</span>
        </div>
        <div class="row">
          <span class="k">新内容</span>
          <el-input v-model="editForm.content" type="textarea" :rows="12" />
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="editForm.status">
            <el-radio :value="0">草稿</el-radio>
            <el-radio :value="1">启用</el-radio>
          </el-radio-group>
        </div>
        <div class="row">
          <span class="k">备注</span>
          <el-input v-model="editForm.remark" placeholder="本次改版原因（建议填写）" />
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存为新版本</el-button>
      </template>
    </el-dialog>

    <!-- ============ 详情 ============ -->
    <el-dialog v-model="detailVisible" title="Prompt 版本详情" width="720px">
      <div v-if="detail" class="form">
        <div class="row">
          <span class="k">prompt_code</span>
          <span class="mono">{{ detail.prompt_code }}</span>
        </div>
        <div class="row">
          <span class="k">版本 / 状态</span>
          <span>
            <span class="mono">v{{ detail.version }}</span>
            <StatusTag :text="detail.status_name" :tone="promptTone(detail.status)" />
          </span>
        </div>
        <div class="row">
          <span class="k">内容</span>
          <pre class="content">{{ detail.content }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-18b · Prompt 版本管理（`/ai/prompt`，F-9.2，**API-84 ~ API-87**）
 *
 * 🔴 三条契约（`api/ai.ts` 已钉死）：
 *   ① **回滚 = 追加新版本**（不是改历史行）—— 历史是审计证据；
 *      故回滚后 `version` **继续增长**，前端**不要期待版本号回退**。
 *   ② **同内容改版 → `42201`**；**族内唯一启用**（同一 `prompt_code` 只能一个 `status=1`）。
 *   ③ 版本列表按 `prompt_code` 归组时，**启用版本可能不是最大版本号** ——
 *      界面不假设「最新即启用」。
 */
import { computed, onMounted, ref } from 'vue'

import { aiApi, type Prompt, type PromptCreateRequest, type PromptUpdateRequest } from '@/api/ai'
import { PROMPT_STATUS } from '@/api/enums'
import { ApiError } from '@/api/http'
import { fmtDateTime } from '@/utils/format'
import type { LoadStatus } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc =
  'Prompt 模板与版本管理。改版生成新版本、回滚也是追加新版本；历史版本不可篡改（审计证据）。'

const loading = ref(false)
const submitting = ref(false)
const status = ref<LoadStatus>('idle')
const rows = ref<Prompt[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(20)
const traceId = ref<string | null>(null)
const errCode = ref<number | null>(null)
const errMsg = ref<string | null>(null)

const codeFilter = ref('')
const sceneFilter = ref('')
const statusFilter = ref<number | null>(null)
const groupByCode = ref(true)

/** 按 `prompt_code` 归组；族内按版本倒序（**但不假设第一个就是启用版本**）。 */
const grouped = computed(() => {
  const map = new Map<string, Prompt[]>()
  for (const p of rows.value) {
    const list = map.get(p.prompt_code) ?? []
    list.push(p)
    map.set(p.prompt_code, list)
  }
  return [...map.entries()]
    .map(([code, items]) => ({
      code,
      items: [...items].sort((a, b) => b.version - a.version),
    }))
    .sort((a, b) => a.code.localeCompare(b.code))
})

function enabledOf(g: { items: Prompt[] }): Prompt | undefined {
  return g.items.find((p) => p.status === 1)
}

function promptTone(code: number): 'success' | 'warning' | 'info' {
  if (code === 1) return 'success'
  if (code === 0) return 'warning'
  return 'info'
}

async function load(): Promise<void> {
  loading.value = true
  status.value = 'loading'
  errCode.value = null
  errMsg.value = null
  try {
    const res = await aiApi.prompts({
      prompt_code: codeFilter.value.trim() || undefined,
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

// ---------------- 新建（API-85） ----------------
const createVisible = ref(false)
const createForm = ref<PromptCreateRequest>({ prompt_code: '', scene: '', content: '', status: 0 })

function openCreate(): void {
  createForm.value = { prompt_code: '', scene: '', content: '', status: 0 }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  const f = createForm.value
  if (!f.prompt_code.trim() || !f.scene.trim() || !f.content.trim()) {
    notify.warn('prompt_code / 场景 / 内容均为必填')
    return
  }
  submitting.value = true
  try {
    await aiApi.createPrompt({
      prompt_code: f.prompt_code.trim(),
      scene: f.scene.trim(),
      content: f.content,
      status: f.status,
    })
    notify.ok('已创建')
    createVisible.value = false
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 42201) {
      notify.warn('创建被拒绝（42201）：同族内状态冲突，或同族已有启用版本。请检查后重试。')
    } else {
      notify.fail(e, '创建失败')
    }
  } finally {
    submitting.value = false
  }
}

// ---------------- 改版（API-86） ----------------
const editVisible = ref(false)
const editing = ref<Prompt | null>(null)
const editForm = ref<PromptUpdateRequest & { remark?: string }>({ content: '', status: 0 })

function openEdit(p: Prompt): void {
  editing.value = p
  editForm.value = { content: p.content, status: p.status, remark: '' }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  const p = editing.value
  if (!p) return
  if (!editForm.value.content.trim()) {
    notify.warn('内容不能为空')
    return
  }
  // 🔴 口径 ②：同内容改版 → 42201。本地先拦一道，省一次往返。
  if (editForm.value.content === p.content) {
    notify.warn('新内容与当前版本完全一致，未产生改版（后端会返回 42201）。')
    return
  }
  submitting.value = true
  try {
    const res = await aiApi.updatePrompt(p.id, {
      content: editForm.value.content,
      status: editForm.value.status,
      remark: editForm.value.remark || undefined,
    })
    notify.ok(`已生成新版本 v${res.data?.version ?? '—'}（旧版本保留）`)
    editVisible.value = false
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 42201) {
      notify.warn('改版被拒绝（42201）：内容与当前启用版本相同，或同族状态冲突。')
    } else {
      notify.fail(e, '保存失败')
    }
  } finally {
    submitting.value = false
  }
}

// ---------------- 回滚（API-87） ----------------
async function onRollback(g: { code: string; items: Prompt[] } | null, target: Prompt): Promise<void> {
  const code = g?.code ?? target.prompt_code
  const okToGo = await notify.confirm(
    `将 ${code} 回滚到 v${target.version}？\n\n` +
      `⚠️ 回滚会**追加一个新版本**（内容取自 v${target.version}），` +
      `版本号会继续增长（不会变成 v${target.version}）。历史版本全部保留。`,
    '确认回滚',
  )
  if (!okToGo) return

  submitting.value = true
  try {
    const res = await aiApi.rollbackPrompt(target.id, target.version)
    notify.ok(
      `已回滚：新版本号为 v${res.data?.version ?? '—'}（内容取自 v${target.version}，历史未改动）`,
    )
    await load()
  } catch (e) {
    notify.fail(e, '回滚失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 详情 ----------------
const detailVisible = ref(false)
const detail = ref<Prompt | null>(null)

function openDetail(p: Prompt): void {
  detail.value = p
  detailVisible.value = true
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
.groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.group {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 10px 12px;
  background: var(--panel);
}
.ghead {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.preview {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  color: var(--txt-2);
  font-size: 12.5px;
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
  grid-template-columns: 110px 1fr;
  gap: 10px;
  align-items: start;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
  padding-top: 6px;
}
.content {
  background: var(--panel-2, #fafbfd);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 10px;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
  margin: 0;
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
