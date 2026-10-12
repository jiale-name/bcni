<template>
  <div>
    <PageHeader title="角色与权限" :desc="desc">
      <template #actions>
        <el-button :disabled="loading" @click="loadRoles">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button v-perm="'rbac:role:create'" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新建角色
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 角色删除明确不开放：说明原因比默默不给按钮更有价值 -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>本系统<b>不提供「删除角色」</b></template>
      <template #default>
        角色不在接口矩阵内开放删除（`rbac_dao.soft_delete_role` 仅保留备询）—— 删除角色会让
        已绑定该角色的账号后续决策出现空档。如需下线一个角色，请把它<b>设为「停用」</b>。
      </template>
    </el-alert>

    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        绑定权限点是<b>全量替换</b>：提交的权限集合就是最终结果。保存后返回
        <code class="mono">affected_users</code>（受影响账号数）与
        <code class="mono">perm_version</code>（权限快照版本）——本页会据此二次确认并提示。
      </template>
    </el-alert>

    <!-- 🔴 无读取接口 = 无法预填，必须显著告知（否则用户会把权限清空） -->
    <el-alert type="error" :closable="false" show-icon class="mb12">
      <template #title>本页<b>无法预填角色已有的权限勾选</b></template>
      <template #default>
        接口矩阵里只有写入侧 <code class="mono">PUT /roles/{id}/permissions</code>，
        <b>没有</b>「读取某角色已绑定权限」的端点。因此切换角色后勾选会被清空 ——
        这<b>不代表该角色没有权限</b>。若只想增补权限，请先按现有权限范围重新勾选再保存，
        避免误将既有权限清空。建议操作顺序：<b>先「全选」，再取消不需要的项</b>。
      </template>
    </el-alert>

    <div class="layout">
      <!-- ============ 左：角色列表 ============ -->
      <el-card shadow="never" class="role-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />角色
              <span class="sub">{{ roleTotal }} 个</span>
            </span>
          </div>
        </template>

        <StatePanel
          :status="roleStatus === 'ready' && roles.length === 0 ? 'empty' : roleStatus"
          :loading="loading"
          :code="roleErrCode"
          :message="roleErrMsg"
          :trace-id="roleTraceId"
          :rows="6"
          empty-title="没有角色"
          empty-hint="系统预置角色缺失，请联系管理员检查种子数据"
          @retry="loadRoles"
        >
          <div class="role-list">
            <div
              v-for="r in roles"
              :key="r.id"
              class="role"
              :class="{ active: current?.id === r.id }"
              @click="selectRole(r)"
            >
              <div class="r-line1">
                <b>{{ r.role_name }}</b>
                <el-tag v-if="r.is_builtin" size="small" type="info" effect="light">预置</el-tag>
                <StatusTag
                  :text="r.status_name"
                  :tone="r.status === 1 ? 'success' : 'info'"
                />
              </div>
              <div class="r-line2">
                <span class="mono xs">{{ r.role_code }}</span>
                <span class="muted xs">数据域：{{ r.data_scope_name || r.data_scope }}</span>
              </div>
            </div>
          </div>
        </StatePanel>
      </el-card>

      <!-- ============ 右：权限点树 ============ -->
      <el-card shadow="never" class="perm-card">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />权限点
              <span v-if="current" class="sub">{{ current.role_name }}（{{ current.role_code }}）</span>
            </span>
            <div class="perm-tools" v-if="current">
              <el-input
                v-model="permKeyword"
                placeholder="过滤权限码 / 名称"
                clearable
                size="small"
                style="width: 180px"
              />
              <el-button
                v-perm="'rbac:role:update'"
                size="small"
                @click="openEditRole"
              >
                编辑角色
              </el-button>
              <el-button
                v-perm="'rbac:role:bind'"
                size="small"
                type="primary"
                :loading="submitting"
                @click="submitBind"
              >
                保存权限绑定
              </el-button>
            </div>
          </div>
        </template>

        <div v-if="!current" class="muted sm" style="padding: 30px; text-align: center">
          请先在左侧选择一个角色。
        </div>

        <StatePanel
          v-else
          :status="permStatus === 'ready' && permTree.length === 0 ? 'empty' : permStatus"
          :loading="permLoading"
          :code="permErrCode"
          :message="permErrMsg"
          :trace-id="permTraceId"
          :rows="6"
          empty-title="没有权限点"
          empty-hint="权限点字典为空，请检查后端种子数据"
          @retry="loadPermTree"
        >
          <div class="perm-head">
            <el-checkbox
              v-model="checkAll"
              :indeterminate="isIndeterminate"
              @change="onCheckAll"
            >
              全选 / 全不选
            </el-checkbox>
            <span class="muted xs">已选 {{ checkedCodes.length }} 个权限点</span>
            <el-tag size="small" type="info" effect="plain">{{ permTypeFilterLabel }}</el-tag>
          </div>

          <el-tree
            ref="treeRef"
            :data="filteredTree"
            :props="{ label: 'perm_name', children: 'children' }"
            node-key="id"
            show-checkbox
            default-expand-all
            :filter-node-method="filterNode"
            :expand-on-click-node="false"
            @check="onCheck"
          >
            <template #default="{ data }">
              <div class="pnode">
                <span class="pname">{{ data.perm_name }}</span>
                <span class="mono xs muted">{{ data.perm_code }}</span>
                <el-tag size="small" effect="plain" :type="permTypeTag(data.perm_type)">
                  {{ data.perm_type_name || permTypeFallback(data.perm_type) }}
                </el-tag>
                <StatusTag v-if="data.status !== 1" text="停用" tone="info" />
              </div>
            </template>
          </el-tree>
          <TraceFooter :trace-id="permTraceId" extra="权限绑定为全量替换" />
        </StatePanel>
      </el-card>
    </div>

    <!-- ============ 新建角色 ============ -->
    <el-dialog v-model="createVisible" title="新建自定义角色" width="520px">
      <div class="form">
        <div class="row">
          <span class="k">角色编码</span>
          <el-input v-model="createForm.role_code" placeholder="如 DEPT_MGR（唯一，建议大写）" />
        </div>
        <div class="row">
          <span class="k">角色名称</span>
          <el-input v-model="createForm.role_name" placeholder="如 部门经理" />
        </div>
        <div class="row">
          <span class="k">数据域</span>
          <el-select v-model="createForm.data_scope" style="width: 100%">
            <el-option v-for="o in DATA_SCOPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="createForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
        <div class="muted xs">
          新角色的权限点是空的 —— 创建后请在右侧勾选权限点并保存绑定。
        </div>
      </div>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 编辑角色 ============ -->
    <el-dialog v-model="editVisible" title="编辑角色" width="520px">
      <div v-if="current" class="form">
        <div class="row">
          <span class="k">角色编码</span>
          <span class="mono">{{ current.role_code }}</span>
          <span class="muted xs">（不可修改）</span>
        </div>
        <div class="row">
          <span class="k">角色名称</span>
          <el-input v-model="editForm.role_name" />
        </div>
        <div class="row">
          <span class="k">数据域</span>
          <div>
            <el-select v-model="editForm.data_scope" style="width: 100%">
              <el-option v-for="o in DATA_SCOPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <div class="muted xs mt8">
              ⚠️ 修改数据域会改变该角色<b>所有账号可见的数据范围</b>，影响面较大。
            </div>
          </div>
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="editForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用（不删除）</el-radio>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEditRole">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-5 · 角色与权限（`/system/role`，F-1.3，**API-18 ~ API-22**）
 *
 * 🔴 四条契约（`api/rbac.ts` 已钉死）：
 *   1. **角色列表 / 权限点树都收在 `data` 对象内**（`{ total, items }`）——
 *      **不是**分页壳。
 *   2. **API-22 绑定权限点是「全量替换」** —— 提交的权限集合即最终结果；
 *      响应回 `affected_users` 与 `perm_version`，本页据此做二次确认与提示。
 *   3. **角色删除接口决定不开放** —— 本模块**没有** `remove()`，
 *      故界面**不得出现「删除角色」按钮**（下线请用「停用」）。
 *   4. **权限数据只从服务端读** —— 本页勾选的是「角色拥有的权限点」，
 *      不是当前登录用户的权限（后者在 `store/auth`，来自 API-05）。
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '@/api/http'
import {
  DATA_SCOPE_OPTIONS,
  rbacApi,
  type PermissionNode,
  type Role,
  type RoleCreateRequest,
  type RoleUpdateRequest,
} from '@/api/rbac'
import type { LoadStatus } from '@/composables/useAsyncData'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()

const desc = '角色清单与权限点绑定。权限绑定为全量替换；角色只支持「停用」，不提供删除。'

// ---------------- 角色列表（API-18） ----------------
const loading = ref(false)
const roleStatus = ref<LoadStatus>('idle')
const roles = ref<Role[]>([])
const roleTotal = ref(0)
const roleTraceId = ref<string | null>(null)
const roleErrCode = ref<number | null>(null)
const roleErrMsg = ref<string | null>(null)
const current = ref<Role | null>(null)

async function loadRoles(): Promise<void> {
  loading.value = true
  roleStatus.value = 'loading'
  roleErrCode.value = null
  roleErrMsg.value = null
  try {
    const res = await rbacApi.roles()
    roles.value = res.data?.items ?? []
    roleTotal.value = res.data?.total ?? roles.value.length
    roleTraceId.value = res.traceId
    roleStatus.value = 'ready'
    // 保持当前选中（若已被刷新掉则回退到第一个）
    if (current.value) {
      const again = roles.value.find((r) => r.id === current.value?.id)
      current.value = again ?? roles.value[0] ?? null
    } else {
      current.value = roles.value[0] ?? null
    }
    if (current.value) await loadPermTree()
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    roles.value = []
    roleTotal.value = 0
    roleErrCode.value = err.code
    roleErrMsg.value = err.displayMessage
    roleTraceId.value = err.traceId
    roleStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    loading.value = false
  }
}

// ---------------- 权限点树（API-21） ----------------
const permLoading = ref(false)
const permStatus = ref<LoadStatus>('idle')
const permTree = ref<PermissionNode[]>([])
const permTraceId = ref<string | null>(null)
const permErrCode = ref<number | null>(null)
const permErrMsg = ref<string | null>(null)
const permKeyword = ref('')
const checkedCodes = ref<string[]>([])
const treeRef = ref<unknown>()

const submitting = ref(false)

function flatten(nodes: PermissionNode[], out: PermissionNode[] = []): PermissionNode[] {
  for (const n of nodes) {
    out.push(n)
    if (n.children?.length) flatten(n.children, out)
  }
  return out
}

const allPerms = computed(() => flatten(permTree.value))

/** 前端过滤（`filter-node-method` 由 el-tree 调用）：只留命中项及其祖先链。 */
const filteredTree = computed<PermissionNode[]>(() => {
  const k = permKeyword.value.trim().toLowerCase()
  if (!k) return permTree.value
  const prune = (nodes: PermissionNode[]): PermissionNode[] =>
    nodes
      .map((n) => ({ ...n, children: prune(n.children ?? []) }))
      .filter(
        (n) =>
          n.perm_code.toLowerCase().includes(k) ||
          n.perm_name.toLowerCase().includes(k) ||
          n.children.length > 0,
      )
  return prune(permTree.value)
})

function filterNode(value: string, data: PermissionNode): boolean {
  if (!value) return true
  const k = value.toLowerCase()
  return data.perm_code.toLowerCase().includes(k) || data.perm_name.toLowerCase().includes(k)
}

function permTypeFallback(code: number): string {
  if (code === 1) return '菜单'
  if (code === 2) return '按钮'
  if (code === 3) return '接口'
  return '—'
}

function permTypeTag(code: number): 'primary' | 'warning' | 'info' {
  if (code === 1) return 'primary'
  if (code === 2) return 'warning'
  return 'info'
}

const permTypeFilterLabel = computed(() => {
  const codes = new Set(allPerms.value.map((p) => p.perm_type))
  const names = [...codes].map((c) => permTypeFallback(c))
  return names.length ? `类型：${names.join(' / ')}` : '类型：—'
})

async function loadPermTree(): Promise<void> {
  permLoading.value = true
  permStatus.value = 'loading'
  permErrCode.value = null
  permErrMsg.value = null
  try {
    const res = await rbacApi.permissionTree()
    permTree.value = res.data?.items ?? []
    permTraceId.value = res.traceId
    permStatus.value = 'ready'
    // 🔴 无「读取角色已绑定权限」接口 → 勾选**不预填**（见页面顶部红色提示）。
    //    这里只按已有勾选做一次树同步，保证刷新后用户已勾的项不丢。
    syncTreeChecks()
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
    permTree.value = []
    permErrCode.value = err.code
    permErrMsg.value = err.displayMessage
    permTraceId.value = err.traceId
    permStatus.value = err.isForbidden ? 'forbidden' : err.isNotFound ? 'notfound' : 'error'
  } finally {
    permLoading.value = false
  }
}

function syncTreeChecks(): void {
  const t = treeRef.value as { setCheckedKeys?: (keys: string[]) => void } | null
  if (!t?.setCheckedKeys) return
  const ids = allPerms.value.filter((p) => checkedCodes.value.includes(p.perm_code)).map((p) => p.id)
  t.setCheckedKeys(ids)
}

function selectRole(r: Role): void {
  current.value = r
  // 🔴 **后端未提供「读取角色已绑定权限」的接口**（接口矩阵 API-18~22 里只有
  //    `PUT /roles/{id}/permissions` 写入侧，没有对应的读取侧）。
  //    因此本页**无法预填勾选** —— 这个限制必须如实告知用户，
  //    否则用户会以为「页面显示没勾 = 该角色没权限」，从而把权限清空。
  checkedCodes.value = []
  permKeyword.value = ''
  const t = treeRef.value as { setCheckedKeys?: (keys: string[]) => void } | null
  t?.setCheckedKeys?.([])
}

function onCheck(): void {
  const t = treeRef.value as { getCheckedNodes?: (leafOnly?: boolean) => PermissionNode[] } | null
  const nodes = t?.getCheckedNodes?.(false) ?? []
  checkedCodes.value = nodes.map((n) => n.perm_code)
}

const checkAll = ref(false)
const isIndeterminate = computed(
  () => checkedCodes.value.length > 0 && checkedCodes.value.length < allPerms.value.length,
)

function onCheckAll(val: string | number | boolean): void {
  const ids = val ? allPerms.value.map((p) => p.id) : []
  const t = treeRef.value as { setCheckedKeys?: (keys: string[]) => void } | null
  t?.setCheckedKeys?.(ids)
  checkedCodes.value = val ? allPerms.value.map((p) => p.perm_code) : []
}

/** 全量替换提交（API-22）。 */
async function submitBind(): Promise<void> {
  const r = current.value
  if (!r) return
  const t = treeRef.value as { getCheckedNodes?: () => PermissionNode[] } | null
  const nodes = t?.getCheckedNodes?.() ?? []
  const ids = nodes.map((n) => n.id)

  const okToGo = await notify.confirm(
    `将角色「${r.role_name}」的权限点<b>全量替换</b>为 ${ids.length} 个？\n\n` +
      `未勾选的权限点会被移除，影响范围覆盖所有绑定该角色的账号。`,
    '确认绑定权限',
  )
  if (!okToGo) return

  submitting.value = true
  try {
    const res = await rbacApi.bindPermissions(r.id, ids)
    const out = res.data
    notify.ok(
      `已保存：绑定 ${out?.perm_ids?.length ?? ids.length} 个权限点；` +
        `本次变更影响 ${out?.affected_users ?? 0} 个账号（权限版本 v${out?.perm_version ?? '—'}）。`,
    )
  } catch (e) {
    notify.fail(e, '保存权限绑定失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 新建 / 编辑角色（API-19 / API-20） ----------------
const createVisible = ref(false)
const createForm = ref<RoleCreateRequest>({ role_code: '', role_name: '', data_scope: 2, status: 1 })

function openCreate(): void {
  createForm.value = { role_code: '', role_name: '', data_scope: 2, status: 1 }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createForm.value.role_code.trim() || !createForm.value.role_name.trim()) {
    notify.warn('角色编码与名称均为必填')
    return
  }
  submitting.value = true
  try {
    await rbacApi.createRole({
      role_code: createForm.value.role_code.trim(),
      role_name: createForm.value.role_name.trim(),
      data_scope: createForm.value.data_scope,
      status: createForm.value.status,
    })
    notify.ok('已创建角色，请在右侧勾选并保存其权限点')
    createVisible.value = false
    await loadRoles()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40901) {
      notify.warn('角色编码已存在（唯一约束冲突 40901）。')
    } else {
      notify.fail(e, '创建失败')
    }
  } finally {
    submitting.value = false
  }
}

const editVisible = ref(false)
const editForm = ref<RoleUpdateRequest>({})

function openEditRole(): void {
  const r = current.value
  if (!r) return
  editForm.value = { role_name: r.role_name, data_scope: r.data_scope, status: r.status }
  editVisible.value = true
}

async function submitEditRole(): Promise<void> {
  const r = current.value
  if (!r) return
  submitting.value = true
  try {
    await rbacApi.updateRole(r.id, editForm.value)
    notify.ok('已保存')
    editVisible.value = false
    await loadRoles()
  } catch (e) {
    notify.fail(e, '保存失败')
  } finally {
    submitting.value = false
  }
}

onMounted(loadRoles)
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 330px 1fr;
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
.perm-tools {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.role-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.role {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 9px 11px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.role:hover {
  border-color: var(--primary);
}
.role.active {
  border-color: var(--primary);
  background: var(--primary-weak);
}
.r-line1 {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.r-line2 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.perm-head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  border-bottom: 1px dashed var(--line);
  padding-bottom: 9px;
  margin-bottom: 10px;
}
.pnode {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  font-size: 12.5px;
}
.pnode .pname {
  font-weight: 500;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.form .row {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 10px;
  align-items: center;
}
.form .row .k {
  font-size: 12.5px;
  color: var(--txt-3);
}
.mt8 {
  margin-top: 8px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
