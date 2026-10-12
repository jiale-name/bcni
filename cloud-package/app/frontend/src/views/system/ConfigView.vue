<template>
  <div>
    <PageHeader title="数据字典与系统配置" :desc="desc">
      <template #actions>
        <el-button :disabled="typesLoading" @click="loadTypes">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 三条口径前置（写反了会造出「两套真相」） -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        <b>「停用」不是「删除」</b>：API-27 只把字典项置为停用，<b>重复停用返回
        <code class="mono">already_disabled=true</code></b>（幂等，不报错）。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        字典项修改的留痕落在<b>审计日志（TBL-28）</b>，<b>不落</b>
        <code class="mono">talent_change_log</code> —— 字典项不是人才数据，
        去「档案变更历史」里找字典改动是找不到的。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        口径区分：<b>字典类型列表（API-23）仅 ADM</b>，而<b>字典项列表（API-24）广授</b>
        （HR / MGR / EMP / EXEC 都要用它填下拉）。所以本页需要 ADM 权限；
        而业务页面的下拉一律走 API-24，不会因为「先拉类型列表」而在非 ADM 下变空。
      </template>
    </el-alert>

    <div class="layout">
      <!-- ============ 左：字典类型 ============ -->
      <el-card shadow="never" class="type-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />字典类型
              <span class="sub">{{ typeTotal }} 个</span>
            </span>
          </div>
        </template>

        <StatePanel
          :status="typesStatus === 'ready' && types.length === 0 ? 'empty' : typesStatus"
          :loading="typesLoading"
          :code="typesErrCode"
          :message="typesErrMsg"
          :trace-id="typesTraceId"
          :rows="6"
          empty-title="没有字典类型"
          empty-hint="字典类型由后端种子数据维护，请检查数据库初始化"
          @retry="loadTypes"
        >
          <div class="type-list">
            <div
              v-for="t in types"
              :key="t.id"
              class="type"
              :class="{ active: current?.id === t.id }"
              @click="selectType(t)"
            >
              <div class="t-line1">
                <b>{{ t.type_name }}</b>
                <StatusTag :text="t.status_name" :tone="t.status === 1 ? 'success' : 'info'" />
              </div>
              <div class="t-line2">
                <span class="mono xs">{{ t.type_code }}</span>
                <span class="muted xs">
                  启用 {{ t.enabled_item_count }} / 共 {{ t.item_count }} 项
                </span>
              </div>
              <div v-if="t.remark" class="muted xs ellipsis" :title="t.remark">{{ t.remark }}</div>
            </div>
          </div>
        </StatePanel>
      </el-card>

      <!-- ============ 右：字典项 ============ -->
      <el-card shadow="never" class="item-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />字典项
              <span v-if="current" class="sub">{{ current.type_name }}（{{ current.type_code }}）</span>
            </span>
            <div class="item-tools" v-if="current">
              <el-select v-model="itemStatusFilter" clearable placeholder="状态不限" size="small" style="width: 130px">
                <el-option :value="1" label="启用" />
                <el-option :value="0" label="停用" />
              </el-select>
              <el-button size="small" @click="loadItems">刷新</el-button>
              <el-button
                v-perm="'dict:create'"
                size="small"
                type="primary"
                @click="openCreateItem"
              >
                新增字典项
              </el-button>
            </div>
          </div>
        </template>

        <div v-if="!current" class="muted sm" style="padding: 30px; text-align: center">
          请先在左侧选择一个字典类型。
        </div>

        <StatePanel
          v-else
          :status="itemsStatus === 'ready' && items.length === 0 ? 'empty' : itemsStatus"
          :loading="itemsLoading"
          :code="itemsErrCode"
          :message="itemsErrMsg"
          :trace-id="itemsTraceId"
          :rows="6"
          empty-title="没有字典项"
          empty-hint="该字典类型下还没有条目"
          @retry="loadItems"
        >
          <el-table :data="items" v-loading="itemsLoading" stripe row-key="id">
            <el-table-column label="字典项" min-width="170">
              <template #default="{ row }">
                <b>{{ row.item_name }}</b>
                <div class="muted xs mono">{{ row.item_code }}</div>
              </template>
            </el-table-column>

            <el-table-column label="父级" width="150">
              <template #default="{ row }">
                <span class="mono xs">{{ row.parent_code || '—' }}</span>
              </template>
            </el-table-column>

            <el-table-column label="排序" width="80" align="right">
              <template #default="{ row }">
                <span class="num">{{ row.sort_no }}</span>
              </template>
            </el-table-column>

            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="row.status === 1 ? 'success' : 'info'" />
              </template>
            </el-table-column>

            <el-table-column label="更新时间" width="150">
              <template #default="{ row }">
                <span class="muted xs">{{ fmtDateTime(row.updated_at) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <div class="ops">
                  <a v-perm="'dict:update'" @click="openEditItem(row)">编辑</a>
                  <a v-if="row.status === 1" v-perm="'dict:update'" @click="onDisable(row)">停用</a>
                  <span v-else class="muted xs">已停用</span>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <div class="hint-row">
            <span class="muted xs">
              字典项的下拉取值（API-24）被业务页面按 <code class="mono">item_code</code> 使用 ——
              <b>停用后既有数据仍保留原值，但新选择不再出现该项</b>。
            </span>
          </div>
          <TraceFooter :trace-id="itemsTraceId" extra="字典项修改留痕落审计日志（TBL-28）" />
        </StatePanel>
      </el-card>
    </div>

    <!-- ============ 新增 / 编辑字典项 ============ -->
    <el-dialog v-model="itemEditVisible" :title="editingItem ? '编辑字典项' : '新增字典项'" width="540px">
      <div class="form">
        <div class="row">
          <span class="k">字典类型</span>
          <span class="mono">{{ current?.type_code }}</span>
        </div>
        <div class="row">
          <span class="k">项编码</span>
          <span v-if="editingItem" class="mono">{{ editingItem.item_code }}</span>
          <el-input v-else v-model="itemForm.item_code" placeholder="如 3（对应 education_level=3）" />
        </div>
        <div class="row">
          <span class="k">项名称</span>
          <el-input v-model="itemForm.item_name" placeholder="如 本科" />
        </div>
        <div class="row">
          <span class="k">父级编码</span>
          <el-input v-model="itemForm.parent_code" placeholder="可选，用于层级字典" />
        </div>
        <div class="row">
          <span class="k">排序号</span>
          <el-input-number v-model="itemForm.sort_no" :min="0" :max="9999" />
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="itemForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
        <div v-if="editingItem" class="muted xs">
          ⚠️ 修改<b>父级编码</b>会改变该字典项的层级结构；后端会在审计日志里记录前后值。
        </div>
      </div>
      <template #footer>
        <el-button @click="itemEditVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitItem">
          {{ editingItem ? '保存' : '新增' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-6 · 数据字典与系统配置（`/system/config`，F-1.7，**API-23 ~ API-27**）
 *
 * 🔴 三条契约（`api/dict.ts` 已钉死）：
 *   ① **API-23 仅 ADM**，而 API-24 广授（HR/MGR/EMP/EXEC 都要用它填下拉）——
 *      业务页面拉下拉走 **API-24**，**不要**先去拉字典类型列表。
 *   ② **API-26 的留痕落 TBL-28 审计日志**，**不落** `talent_change_log`
 *      （字典项不是人才数据）。
 *   ③ **API-27 是「停用」不是删除**：重复停用返回 `already_disabled=true`
 *      （幂等，**不报错**）—— 前端按「已停用」提示，不能弹红色失败。
 */
import { onMounted, ref } from 'vue'

import { dictApi, type DictItem, type DictItemCreateRequest, type DictItemUpdateRequest, type DictType } from '@/api/dict'
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
  '数据字典维护（字典项被所有业务下拉复用）。字典项只支持停用，不物理删除；改动留痕落审计日志。'

// ---------------- 字典类型（API-23，仅 ADM） ----------------
const typesLoading = ref(false)
const typesStatus = ref<LoadStatus>('idle')
const types = ref<DictType[]>([])
const typeTotal = ref(0)
const typesTraceId = ref<string | null>(null)
const typesErrCode = ref<number | null>(null)
const typesErrMsg = ref<string | null>(null)
const current = ref<DictType | null>(null)

async function loadTypes(): Promise<void> {
  typesLoading.value = true
  typesStatus.value = 'loading'
  typesErrCode.value = null
  typesErrMsg.value = null
  try {
    const res = await dictApi.types()
    types.value = res.data?.items ?? []
    typeTotal.value = res.data?.total ?? types.value.length
    typesTraceId.value = res.traceId
    typesStatus.value = 'ready'
    if (current.value) {
      current.value = types.value.find((t) => t.id === current.value?.id) ?? types.value[0] ?? null
    } else {
      current.value = types.value[0] ?? null
    }
    if (current.value) await loadItems()
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    types.value = []
    typeTotal.value = 0
    typesErrCode.value = err.code
    typesErrMsg.value = err.displayMessage
    typesTraceId.value = err.traceId
    typesStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    typesLoading.value = false
  }
}

// ---------------- 字典项（API-24） ----------------
const itemsLoading = ref(false)
const itemsStatus = ref<LoadStatus>('idle')
const items = ref<DictItem[]>([])
const itemsTraceId = ref<string | null>(null)
const itemsErrCode = ref<number | null>(null)
const itemsErrMsg = ref<string | null>(null)
const itemStatusFilter = ref<number | null>(null)
const submitting = ref(false)

async function loadItems(): Promise<void> {
  const t = current.value
  if (!t) return
  itemsLoading.value = true
  itemsStatus.value = 'loading'
  itemsErrCode.value = null
  itemsErrMsg.value = null
  try {
    const res = await dictApi.items(t.type_code, {
      status: itemStatusFilter.value ?? undefined,
    })
    items.value = res.data?.items ?? []
    itemsTraceId.value = res.traceId
    itemsStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    items.value = []
    itemsErrCode.value = err.code
    itemsErrMsg.value = err.displayMessage
    itemsTraceId.value = err.traceId
    itemsStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    itemsLoading.value = false
  }
}

function selectType(t: DictType): void {
  current.value = t
  itemStatusFilter.value = null
  void loadItems()
}

// ---------------- 新增 / 编辑字典项（API-25 / API-26） ----------------
const itemEditVisible = ref(false)
const editingItem = ref<DictItem | null>(null)
const itemForm = ref<{
  item_code: string
  item_name: string
  parent_code: string
  sort_no: number
  status: number
}>({ item_code: '', item_name: '', parent_code: '', sort_no: 0, status: 1 })

function openCreateItem(): void {
  editingItem.value = null
  itemForm.value = { item_code: '', item_name: '', parent_code: '', sort_no: 0, status: 1 }
  itemEditVisible.value = true
}

function openEditItem(row: DictItem): void {
  editingItem.value = row
  itemForm.value = {
    item_code: row.item_code,
    item_name: row.item_name,
    parent_code: row.parent_code ?? '',
    sort_no: row.sort_no,
    status: row.status,
  }
  itemEditVisible.value = true
}

async function submitItem(): Promise<void> {
  const t = current.value
  if (!t) return
  if (!itemForm.value.item_name.trim()) {
    notify.warn('项名称必填')
    return
  }
  if (!editingItem.value && !itemForm.value.item_code.trim()) {
    notify.warn('项编码必填（字典项以 item_code 被业务引用）')
    return
  }

  submitting.value = true
  try {
    if (editingItem.value) {
      // 🔴 不提交 `item_code`（字典项的业务主键）
      const payload: DictItemUpdateRequest = {
        item_name: itemForm.value.item_name.trim(),
        sort_no: itemForm.value.sort_no,
        parent_code: itemForm.value.parent_code.trim() || null,
        status: itemForm.value.status,
      }
      await dictApi.updateItem(editingItem.value.id, payload)
      notify.ok('已保存（本次改动已写入审计日志）')
    } else {
      const payload: DictItemCreateRequest = {
        item_code: itemForm.value.item_code.trim(),
        item_name: itemForm.value.item_name.trim(),
        sort_no: itemForm.value.sort_no,
        parent_code: itemForm.value.parent_code.trim() || null,
        status: itemForm.value.status,
      }
      await dictApi.createItem(t.type_code, payload)
      notify.ok('已新增')
    }
    itemEditVisible.value = false
    await loadItems()
    await loadTypes()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40901) {
      notify.warn('该字典类型下已存在相同项编码（40901）。')
    } else {
      notify.fail(e, editingItem.value ? '保存失败' : '新增失败')
    }
  } finally {
    submitting.value = false
  }
}

// ---------------- 停用（API-27，幂等） ----------------
async function onDisable(row: DictItem): Promise<void> {
  const okToGo = await notify.confirm(
    `停用字典项「${row.item_name}」（${row.item_code}）？\n\n` +
      `停用<b>不是删除</b>：既有数据中的该取值会保留，只是不再出现在新的下拉选项中。`,
    '确认停用',
  )
  if (!okToGo) return
  try {
    const res = await dictApi.disableItem(row.id)
    // 🔴 重复停用 → already_disabled=true，属**幂等成功**，不能报错
    if (res.data?.already_disabled) {
      notify.info('该项此前已停用（幂等，无需重复操作）。')
    } else {
      notify.ok('已停用')
    }
    await loadItems()
    await loadTypes()
  } catch (e) {
    notify.fail(e, '停用失败')
  }
}

onMounted(loadTypes)
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: 1fr;
  }
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
.item-tools {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.type-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.type {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 9px 11px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.type:hover {
  border-color: var(--primary);
}
.type.active {
  border-color: var(--primary);
  background: var(--primary-weak);
}
.t-line1 {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-bottom: 3px;
}
.t-line2 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ops {
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
}
.ops a {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--primary);
}
.ops a:hover {
  text-decoration: underline;
}
.hint-row {
  margin-top: 10px;
  border-top: 1px dashed var(--line);
  padding-top: 9px;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.form .row {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 10px;
  align-items: center;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
