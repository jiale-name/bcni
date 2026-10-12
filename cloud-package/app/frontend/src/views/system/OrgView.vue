<template>
  <div>
    <PageHeader title="组织架构管理" :desc="desc">
      <template #actions>
        <el-button :disabled="treeLoading" @click="loadTree">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button v-perm="'org:create'" type="primary" @click="openCreate(null)">
          <el-icon><Plus /></el-icon>新建根部门
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 形状提醒：组织树收在 data 对象内，与「分页壳」不是同一种 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        组织树与岗位列表都收在 <code class="mono">data</code> <b>对象</b>内
        （<code class="mono">{ total, items }</code>）—— 与「<code class="mono">data</code> 是数组 +
        同级 <code class="mono">page</code>」的分页壳<b>不是同一种形状</b>，本页分别读取。
      </template>
    </el-alert>

    <div class="layout">
      <!-- ============ 左：组织树 ============ -->
      <el-card shadow="never" class="tree-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />部门树
              <span class="sub">{{ orgTotal }} 个部门</span>
            </span>
          </div>
        </template>

        <div class="tree-tools">
          <el-input
            v-model="keywordInput"
            placeholder="部门名 / 编码"
            clearable
            size="small"
            @keyup.enter="loadTree"
          />
          <el-select v-model="statusFilter" clearable placeholder="状态" size="small">
            <el-option :value="1" label="启用" />
            <el-option :value="0" label="停用" />
          </el-select>
          <el-button size="small" type="primary" @click="loadTree">查询</el-button>
        </div>

        <StatePanel
          :status="treeStatus === 'ready' && orgTree.length === 0 ? 'empty' : treeStatus"
          :loading="treeLoading"
          :code="treeErrCode"
          :message="treeErrMsg"
          :trace-id="treeTraceId"
          :rows="6"
          empty-title="没有部门"
          empty-hint="还没有创建任何部门，或当前筛选条件无匹配"
          @retry="loadTree"
        >
          <el-tree
            :data="orgTree"
            :props="{ label: 'dept_name', children: 'children' }"
            node-key="id"
            default-expand-all
            :expand-on-click-node="false"
            highlight-current
            :current-node-key="selected?.id"
            @node-click="onNodeClick"
          >
            <template #default="{ data }">
              <div class="node">
                <span class="name">{{ data.dept_name }}</span>
                <span class="muted xs mono">{{ data.dept_code }}</span>
                <span class="muted xs">L{{ data.dept_level }}</span>
                <StatusTag
                  v-if="data.status !== 1"
                  :text="data.status_name || '停用'"
                  tone="info"
                />
                <span class="node-ops">
                  <a @click.stop="openCreate(data)">新增子部门</a>
                  <a @click.stop="openEdit(data)">编辑</a>
                  <a @click.stop="onDelete(data)">删除</a>
                </span>
              </div>
            </template>
          </el-tree>
          <TraceFooter :trace-id="treeTraceId" />
        </StatePanel>
      </el-card>

      <!-- ============ 右：岗位 ============ -->
      <el-card shadow="never" class="pos-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />岗位
              <span class="sub">{{ positionTotal }} 个</span>
            </span>
            <div class="pos-tools">
              <el-input
                v-model="posKeyword"
                placeholder="岗位名 / 编码"
                clearable
                size="small"
                style="width: 150px"
                @keyup.enter="loadPositions"
              />
              <el-checkbox v-model="posLimitToSelected" size="small">
                仅当前部门
              </el-checkbox>
              <el-button v-perm="'position:create'" size="small" type="primary" @click="openCreatePosition">
                新建岗位
              </el-button>
            </div>
          </div>
        </template>

        <div v-if="selected" class="cur-org">
          当前部门：<b>{{ selected.dept_name }}</b>
          <span class="muted xs">
            ｜{{ selected.dept_code }}｜层级 L{{ selected.dept_level }}｜路径
            <span class="mono">{{ selected.dept_path || '—' }}</span>
          </span>
        </div>

        <StatePanel
          :status="posStatus === 'ready' && positions.length === 0 ? 'empty' : posStatus"
          :loading="posLoading"
          :code="posErrCode"
          :message="posErrMsg"
          :trace-id="posTraceId"
          :rows="5"
          empty-title="没有岗位"
          empty-hint="该部门下还没有岗位，或未匹配到筛选条件"
          @retry="loadPositions"
        >
          <el-table :data="positions" v-loading="posLoading" stripe row-height="38">
            <el-table-column label="岗位" min-width="160">
              <template #default="{ row }">
                {{ row.position_name }}
                <div class="muted xs mono">{{ row.position_code }}</div>
              </template>
            </el-table-column>
            <el-table-column label="层级" width="90" align="right">
              <template #default="{ row }">{{ dash(row.position_level) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <StatusTag :text="row.status_name" :tone="row.status === 1 ? 'success' : 'info'" />
              </template>
            </el-table-column>
          </el-table>
          <TraceFooter :trace-id="posTraceId" />
        </StatePanel>
      </el-card>
    </div>

    <!-- ============ 部门 新建 / 编辑 ============ -->
    <el-dialog v-model="editVisible" :title="editing ? '编辑部门' : '新建部门'" width="560px">
      <div class="form">
        <div class="row">
          <span class="k">上级部门</span>
          <el-tree-select
            v-model="form.parent_id"
            :data="orgTree"
            :props="{ label: 'dept_name', value: 'id', children: 'children' }"
            node-key="id"
            check-strictly
            :render-after-expand="false"
            clearable
            placeholder="不选 = 根部门"
            style="width: 100%"
          />
        </div>
        <div class="row">
          <span class="k">部门编码</span>
          <el-input v-model="form.dept_code" placeholder="如 D001" />
        </div>
        <div class="row">
          <span class="k">部门名称</span>
          <el-input v-model="form.dept_name" placeholder="如 技术研发中心" />
        </div>
        <div class="row">
          <span class="k">负责人用户 ID</span>
          <el-input v-model="form.leader_user_id" placeholder="可留空（雪花 ID 字符串）" />
        </div>
        <div class="row">
          <span class="k">排序号</span>
          <el-input-number v-model="form.sort_no" :min="0" :max="9999" />
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="form.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
        <div v-if="editing" class="muted xs">
          ⚠️ 修改「上级部门」即<b>移动节点</b>；移动后会造成部门层级与数据域的重新计算。
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 岗位 新建 ============ -->
    <el-dialog v-model="posVisible" title="新建岗位" width="520px">
      <div class="form">
        <div class="row">
          <span class="k">所属部门</span>
          <el-tree-select
            v-model="posForm.dept_id"
            :data="orgTree"
            :props="{ label: 'dept_name', value: 'id', children: 'children' }"
            node-key="id"
            check-strictly
            :render-after-expand="false"
            clearable
            placeholder="可留空（通用岗位）"
            style="width: 100%"
          />
        </div>
        <div class="row">
          <span class="k">岗位编码</span>
          <el-input v-model="posForm.position_code" placeholder="如 P001" />
        </div>
        <div class="row">
          <span class="k">岗位名称</span>
          <el-input v-model="posForm.position_name" placeholder="如 高级算法工程师" />
        </div>
        <div class="row">
          <span class="k">岗位层级</span>
          <el-input-number v-model="posForm.position_level" :min="1" :max="20" />
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="posForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <el-button @click="posVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitPosition">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-3 · 组织架构管理（`/system/org`，F-1.2，**API-07 ~ API-12**）
 *
 * 🔴 三条契约（`api/org.ts` 已钉死）：
 *   1. **树 / 岗位列表收在 `data` 对象内**（`{ total, items }`），
 *      **不是**「`data` 数组 + 同级 `page`」的分页壳 —— 两种形状不可混用。
 *   2. **删除有子部门 / 在职账号的部门 → `40904`**（依赖前置未满足），**不是** `40001`；
 *      因此提示要是「请先处理下级」，而不是笼统的「参数错误」。
 *   3. 岗位 `position_level`、`sort_no` 等为**可选数值**，空值渲染 `—`（不显示 0）。
 */
import { onMounted, ref } from 'vue'

import { ApiError } from '@/api/http'
import {
  orgApi,
  type OrgCreateRequest,
  type OrgNode,
  type OrgUpdateRequest,
  type Position,
  type PositionCreateRequest,
} from '@/api/org'
import { dash } from '@/api/types'
import type { LoadStatus } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc = '部门树（≥5 级）与岗位维护。删除部门前需先处理其下级与在职账号。'

// ---------------- 组织树 ----------------
const treeLoading = ref(false)
const treeStatus = ref<LoadStatus>('idle')
const orgTree = ref<OrgNode[]>([])
const orgTotal = ref(0)
const treeTraceId = ref<string | null>(null)
const treeErrCode = ref<number | null>(null)
const treeErrMsg = ref<string | null>(null)
const keywordInput = ref('')
const statusFilter = ref<number | null>(null)
const selected = ref<OrgNode | null>(null)

async function loadTree(): Promise<void> {
  treeLoading.value = true
  treeStatus.value = 'loading'
  treeErrCode.value = null
  treeErrMsg.value = null
  try {
    const res = await orgApi.tree({
      status: statusFilter.value ?? undefined,
      keyword: keywordInput.value.trim() || undefined,
    })
    // 🔴 `data` 是对象 `{ total, items }`
    orgTree.value = res.data?.items ?? []
    orgTotal.value = res.data?.total ?? countNodes(orgTree.value)
    treeTraceId.value = res.traceId
    treeStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    orgTree.value = []
    orgTotal.value = 0
    treeErrCode.value = err.code
    treeErrMsg.value = err.displayMessage
    treeTraceId.value = err.traceId
    treeStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    treeLoading.value = false
  }
}

function countNodes(nodes: OrgNode[]): number {
  let n = 0
  for (const x of nodes) {
    n += 1
    if (x.children?.length) n += countNodes(x.children)
  }
  return n
}

function onNodeClick(data: OrgNode): void {
  selected.value = data
  if (posLimitToSelected.value) void loadPositions()
}

// ---------------- 岗位 ----------------
const posLoading = ref(false)
const posStatus = ref<LoadStatus>('idle')
const positions = ref<Position[]>([])
const positionTotal = ref(0)
const posTraceId = ref<string | null>(null)
const posErrCode = ref<number | null>(null)
const posErrMsg = ref<string | null>(null)
const posKeyword = ref('')
const posLimitToSelected = ref(true)

async function loadPositions(): Promise<void> {
  posLoading.value = true
  posStatus.value = 'loading'
  posErrCode.value = null
  posErrMsg.value = null
  try {
    const res = await orgApi.positions({
      keyword: posKeyword.value.trim() || undefined,
      dept_id: posLimitToSelected.value ? selected.value?.id : undefined,
    })
    positions.value = res.data?.items ?? []
    positionTotal.value = res.data?.total ?? positions.value.length
    posTraceId.value = res.traceId
    posStatus.value = 'ready'
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    positions.value = []
    positionTotal.value = 0
    posErrCode.value = err.code
    posErrMsg.value = err.displayMessage
    posTraceId.value = err.traceId
    posStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    posLoading.value = false
  }
}

// ---------------- 部门 新建 / 编辑 ----------------
const editVisible = ref(false)
const editing = ref<OrgNode | null>(null)
const submitting = ref(false)
const form = ref<{
  parent_id: string | null
  dept_code: string
  dept_name: string
  leader_user_id: string
  sort_no: number
  status: number
}>({ parent_id: null, dept_code: '', dept_name: '', leader_user_id: '', sort_no: 0, status: 1 })

function openCreate(parent: OrgNode | null): void {
  editing.value = null
  form.value = {
    parent_id: parent?.id ?? null,
    dept_code: '',
    dept_name: '',
    leader_user_id: '',
    sort_no: 0,
    status: 1,
  }
  editVisible.value = true
}

function openEdit(node: OrgNode): void {
  editing.value = node
  form.value = {
    parent_id: node.parent_id || null,
    dept_code: node.dept_code,
    dept_name: node.dept_name,
    leader_user_id: node.leader_user_id ?? '',
    sort_no: node.sort_no,
    status: node.status,
  }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  if (!form.value.dept_code.trim() || !form.value.dept_name.trim()) {
    notify.warn('部门编码与名称均为必填')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      const payload: OrgUpdateRequest = {
        dept_code: form.value.dept_code.trim(),
        dept_name: form.value.dept_name.trim(),
        parent_id: form.value.parent_id || null,
        leader_user_id: form.value.leader_user_id.trim() || null,
        sort_no: form.value.sort_no,
        status: form.value.status,
      }
      await orgApi.update(editing.value.id, payload)
      notify.ok('已保存')
    } else {
      const payload: OrgCreateRequest = {
        parent_id: form.value.parent_id || undefined,
        dept_code: form.value.dept_code.trim(),
        dept_name: form.value.dept_name.trim(),
        leader_user_id: form.value.leader_user_id.trim() || null,
        sort_no: form.value.sort_no,
        status: form.value.status,
      }
      await orgApi.create(payload)
      notify.ok('已创建')
    }
    editVisible.value = false
    await loadTree()
  } catch (e) {
    notify.fail(e, editing.value ? '保存失败' : '创建失败')
  } finally {
    submitting.value = false
  }
}

async function onDelete(node: OrgNode): Promise<void> {
  const okToGo = await notify.confirm(
    `删除部门「${node.dept_name}」？\n\n` +
      `若该部门存在<b>子部门</b>或<b>在职账号</b>，后端会拒绝（返回 40904），需要先处理下级引用。`,
    '确认删除部门',
  )
  if (!okToGo) return
  try {
    const res = await orgApi.remove(node.id)
    notify.ok(
      `已删除「${res.data?.dept_name ?? node.dept_name}」` +
        (res.data?.affected_children ? `（影响下级 ${res.data.affected_children} 个）` : ''),
    )
    if (selected.value?.id === node.id) selected.value = null
    await loadTree()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40904) {
      // 🔴 依赖前置未满足：明确告诉用户「要做什么」，而不是「参数错误」
      notify.warn(
        '删除被拒绝（40904）：该部门下仍有子部门或在职账号。请先迁移 / 停用下级引用后重试。',
      )
    } else {
      notify.fail(e, '删除失败')
    }
  }
}

// ---------------- 岗位 新建 ----------------
const posVisible = ref(false)
const posForm = ref<PositionCreateRequest & { dept_id: string | null }>({
  position_code: '',
  position_name: '',
  dept_id: null,
  position_level: null,
  status: 1,
})

function openCreatePosition(): void {
  posForm.value = {
    position_code: '',
    position_name: '',
    dept_id: selected.value?.id ?? null,
    position_level: null,
    status: 1,
  }
  posVisible.value = true
}

async function submitPosition(): Promise<void> {
  if (!posForm.value.position_code.trim() || !posForm.value.position_name.trim()) {
    notify.warn('岗位编码与名称均为必填')
    return
  }
  submitting.value = true
  try {
    await orgApi.createPosition({
      position_code: posForm.value.position_code.trim(),
      position_name: posForm.value.position_name.trim(),
      dept_id: posForm.value.dept_id || null,
      position_level: posForm.value.position_level ?? null,
      status: posForm.value.status,
    })
    notify.ok('已创建岗位')
    posVisible.value = false
    await loadPositions()
  } catch (e) {
    notify.fail(e, '创建岗位失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await loadTree()
  await loadPositions()
})
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 420px 1fr;
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
.tree-tools,
.pos-tools {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.pos-tools {
  margin-bottom: 0;
}
.node {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  padding-right: 6px;
  font-size: 12.5px;
}
.node .name {
  font-weight: 500;
}
.node-ops {
  margin-left: auto;
  display: none;
  gap: 9px;
}
.node:hover .node-ops {
  display: flex;
}
.node-ops a {
  cursor: pointer;
  font-size: 12px;
  color: var(--primary);
}
.node-ops a:hover {
  text-decoration: underline;
}
.cur-org {
  font-size: 12.5px;
  border-bottom: 1px dashed var(--line);
  padding-bottom: 9px;
  margin-bottom: 10px;
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
  align-items: center;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.mb12 {
  margin-bottom: 12px;
}
</style>
