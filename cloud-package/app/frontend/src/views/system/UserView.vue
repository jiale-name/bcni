<template>
  <div>
    <PageHeader title="用户管理" :desc="desc">
      <template #actions>
        <el-button v-perm="'user:import'" @click="importVisible = true">
          <el-icon><UploadFilled /></el-icon>批量导入
        </el-button>
        <el-button v-perm="'user:create'" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新建用户
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 初始口令只在创建响应中出现一次 -->
    <el-alert type="warning" :closable="false" show-icon class="mb12">
      <template #title>
        新建用户时，后端下发的<b>初始口令只在创建响应中出现一次</b> ——
        本页立即弹出展示并提示复制，<b>不写入任何前端持久化存储</b>（localStorage / sessionStorage）。
        关闭弹窗后无法再次查看，只能重置。
      </template>
    </el-alert>

    <el-card shadow="never" class="mb12">
      <div class="toolbar">
        <el-input
          v-model="keywordInput"
          placeholder="用户名 / 姓名 / 手机"
          clearable
          style="width: 210px"
          @keyup.enter="reload"
        />
        <el-tree-select
          v-model="orgId"
          :data="orgTree"
          :props="{ label: 'dept_name', value: 'id', children: 'children' }"
          node-key="id"
          check-strictly
          :render-after-expand="false"
          clearable
          placeholder="全部部门"
          style="width: 200px"
        />
        <el-select v-model="roleCode" clearable placeholder="角色不限" style="width: 150px">
          <el-option v-for="r in roleOptions" :key="r.role_code" :label="r.role_name" :value="r.role_code" />
        </el-select>
        <el-select v-model="statusFilter" clearable placeholder="状态不限" style="width: 130px">
          <el-option :value="1" label="启用" />
          <el-option :value="0" label="停用" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="hd">
            <i class="bar" />用户列表
            <span class="sub">共 {{ table.total.value }} 个账号</span>
          </span>
          <span class="muted xs">
            角色为<b>全量替换</b>语义：提交的角色集合就是最终结果
          </span>
        </div>
      </template>

      <StatePanel
        :status="table.status.value === 'ready' && table.isEmpty.value ? 'empty' : table.status.value"
        :loading="table.loading.value"
        :code="table.error.value?.code ?? null"
        :message="table.error.value?.message ?? null"
        :trace-id="table.traceId.value"
        :rows="6"
        empty-title="没有匹配的用户"
        empty-hint="试试放宽筛选条件，或新建一个用户"
        @retry="table.load()"
      >
        <el-table :data="table.rows.value" v-loading="table.loading.value" stripe row-key="id">
          <el-table-column label="用户名" min-width="140">
            <template #default="{ row }">
              <b>{{ row.username }}</b>
              <div v-if="row.real_name" class="muted xs">{{ row.real_name }}</div>
            </template>
          </el-table-column>

          <el-table-column label="部门 / 岗位" min-width="170">
            <template #default="{ row }">
              <div class="ellipsis" :title="row.org_name || ''">{{ dash(row.org_name) }}</div>
              <div class="muted xs ellipsis" :title="row.position_name || ''">
                {{ dash(row.position_name) }}
              </div>
            </template>
          </el-table-column>

          <el-table-column label="联系方式" min-width="180">
            <template #default="{ row }">
              <div class="xs">{{ dash(row.phone) }}</div>
              <div class="muted xs ellipsis" :title="row.email || ''">{{ dash(row.email) }}</div>
            </template>
          </el-table-column>

          <el-table-column label="角色" min-width="190">
            <template #default="{ row }">
              <el-tag v-for="r in row.roles" :key="r" size="small" type="info" effect="light" class="tag-gap">
                {{ r }}
              </el-tag>
              <span v-if="!row.roles?.length" class="muted xs">未分配</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <StatusTag :text="row.status_name" :tone="row.status === 1 ? 'success' : 'info'" />
            </template>
          </el-table-column>

          <el-table-column label="最近登录" width="150">
            <template #default="{ row }">
              <span class="muted xs">{{ fmtDateTime(row.last_login_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <div class="ops">
                <a @click="openEdit(row)">编辑</a>
                <a v-perm="'user:role'" @click="openRoles(row)">分配角色</a>
                <a v-perm="'user:update'" @click="toggleStatus(row)">
                  {{ row.status === 1 ? '停用' : '启用' }}
                </a>
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
        <TraceFooter :trace-id="table.traceId.value" />
      </StatePanel>
    </el-card>

    <!-- ============ 新建用户 ============ -->
    <el-dialog v-model="createVisible" title="新建用户" width="600px">
      <div class="form">
        <div class="row">
          <span class="k">用户名</span>
          <el-input v-model="createForm.username" placeholder="登录名，唯一" />
        </div>
        <div class="row">
          <span class="k">初始口令</span>
          <el-input v-model="createForm.password" placeholder="留空由后端生成并一次性返回" show-password />
        </div>
        <div class="row">
          <span class="k">真实姓名</span>
          <el-input v-model="createForm.real_name" />
        </div>
        <div class="row">
          <span class="k">部门</span>
          <el-tree-select
            v-model="createForm.org_id"
            :data="orgTree"
            :props="{ label: 'dept_name', value: 'id', children: 'children' }"
            node-key="id"
            check-strictly
            :render-after-expand="false"
            clearable
            style="width: 100%"
          />
        </div>
        <div class="row">
          <span class="k">岗位</span>
          <el-select v-model="createForm.position_id" clearable filterable placeholder="可选" style="width: 100%">
            <el-option v-for="p in positions" :key="p.id" :label="p.position_name" :value="p.id" />
          </el-select>
        </div>
        <div class="row">
          <span class="k">手机 / 邮箱</span>
          <div class="inline">
            <el-input v-model="createForm.phone" placeholder="手机" style="width: 160px" />
            <el-input v-model="createForm.email" placeholder="邮箱" style="width: 200px" />
          </div>
        </div>
        <div class="row">
          <span class="k">角色</span>
          <el-select v-model="createForm.role_ids" multiple filterable placeholder="可多选" style="width: 100%">
            <el-option v-for="r in roleOptions" :key="r.id" :label="r.role_name" :value="r.id" />
          </el-select>
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="createForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 编辑用户 ============ -->
    <el-dialog v-model="editVisible" title="编辑用户" width="560px">
      <div v-if="editing" class="form">
        <div class="row">
          <span class="k">用户名</span>
          <span class="mono">{{ editing.username }}</span>
          <span class="muted xs">（不可修改）</span>
        </div>
        <div class="row">
          <span class="k">真实姓名</span>
          <el-input v-model="editForm.real_name" />
        </div>
        <div class="row">
          <span class="k">部门</span>
          <el-tree-select
            v-model="editForm.org_id"
            :data="orgTree"
            :props="{ label: 'dept_name', value: 'id', children: 'children' }"
            node-key="id"
            check-strictly
            :render-after-expand="false"
            clearable
            style="width: 100%"
          />
        </div>
        <div class="row">
          <span class="k">岗位</span>
          <el-select v-model="editForm.position_id" clearable filterable placeholder="可选" style="width: 100%">
            <el-option v-for="p in positions" :key="p.id" :label="p.position_name" :value="p.id" />
          </el-select>
        </div>
        <div class="row">
          <span class="k">手机 / 邮箱</span>
          <div class="inline">
            <el-input v-model="editForm.phone" placeholder="手机" style="width: 160px" />
            <el-input v-model="editForm.email" placeholder="邮箱" style="width: 200px" />
          </div>
        </div>
        <div class="row">
          <span class="k">状态</span>
          <el-radio-group v-model="editForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 分配角色（全量替换） ============ -->
    <el-dialog v-model="rolesVisible" title="分配角色（全量替换）" width="520px">
      <el-alert type="warning" :closable="false" show-icon class="mb12">
        <template #title>提交的是<b>最终角色集合</b>，不是增量追加</template>
        <template #default>
          未勾选的角色会被<b>移除</b>。保存后该用户的权限快照会立即生效（后端 `perm_version` 递增）。
        </template>
      </el-alert>
      <div v-if="roleUser" class="form">
        <div class="row">
          <span class="k">用户</span>
          <span><b>{{ roleUser.username }}</b> {{ roleUser.real_name || '' }}</span>
        </div>
        <div class="row">
          <span class="k">角色</span>
          <el-select v-model="roleIds" multiple filterable style="width: 100%">
            <el-option v-for="r in roleOptions" :key="r.id" :label="r.role_name" :value="r.id" />
          </el-select>
        </div>
      </div>
      <template #footer>
        <el-button @click="rolesVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitRoles">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 一次性初始口令 ============ -->
    <el-dialog
      v-model="pwdVisible"
      title="初始口令（仅此一次）"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-alert type="error" :closable="false" show-icon class="mb12">
        <template #title>请立即复制并通过安全渠道转交本人</template>
        <template #default>
          该口令<b>只在本响应中出现一次</b>，关闭后无法再次查看（只能重置）。
          本页<b>不会</b>把它写入浏览器存储。
        </template>
      </el-alert>
      <div class="pwd-box">
        <code class="pwd">{{ initialPassword }}</code>
        <el-button size="small" @click="copyPwd">
          <el-icon><DocumentCopy /></el-icon>复制
        </el-button>
      </div>
      <div class="muted xs mt8">
        建议要求用户首次登录后立即修改密码（<code class="mono">PUT /auth/password</code>）。
      </div>
      <template #footer>
        <el-button type="primary" @click="pwdVisible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 批量导入 ============ -->
    <el-dialog v-model="importVisible" title="批量导入用户" width="620px">
      <el-alert type="info" :closable="false" show-icon class="mb12">
        <template #title>
          按模板上传 Excel / CSV。逐行校验，失败行不阻塞成功行 ——
          导入结果会分行返回失败原因（行号 + 原因），可据此修正后重传。
        </template>
      </el-alert>
      <el-upload
        :auto-upload="false"
        :limit="1"
        accept=".xlsx,.xls,.csv"
        :on-change="onFileChange"
        :on-remove="() => (importFile = null)"
      >
        <el-button>
          <el-icon><UploadFilled /></el-icon>选择文件
        </el-button>
        <template #tip>
          <div class="muted xs">支持 .xlsx / .xls / .csv</div>
        </template>
      </el-upload>

      <el-progress v-if="uploadPercent > 0" :percentage="uploadPercent" class="mt12" />

      <div v-if="importResult" class="import-result mt12">
        <div class="res-line">
          总 {{ importResult.total }} 行 ｜
          <span class="ok">成功 {{ importResult.success }}</span> ｜
          <span class="danger">失败 {{ importResult.failed }}</span>
        </div>
        <el-table v-if="importResult.errors?.length" :data="importResult.errors" size="small" max-height="240">
          <el-table-column label="行号" width="80">
            <template #default="{ row }">{{ row.row }}</template>
          </el-table-column>
          <el-table-column label="用户名" width="150">
            <template #default="{ row }">{{ row.username || '—' }}</template>
          </el-table-column>
          <el-table-column label="失败原因" min-width="240">
            <template #default="{ row }">
              <span class="danger sm">{{ row.reason }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!importFile" :loading="submitting" @click="submitImport">
          开始导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * P-4 · 用户管理（`/system/user`，F-1.2，**API-13 ~ API-17**）
 *
 * 🔴 四条契约（`api/user.ts` + `api/rbac.ts` 已钉死）：
 *   1. **API-13 用的是「分页壳」**（`data` 为数组 + **同级** `page`），
 *      不是 `{ total, items }` —— 与组织树**不是同一种形状**。
 *   2. **`initial_password` 只在创建响应中出现一次** ——
 *      立即展示 + 支持复制，**绝不落 localStorage / sessionStorage**。
 *   3. **API-16 分配角色是「全量替换」** —— 提交的集合即最终结果。
 *   4. 错误码语义：`40901` 用户名唯一冲突、`40904` 依赖未满足（部门/岗位被引用）。
 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/http'
import { orgApi, type OrgNode, type Position } from '@/api/org'
import { rbacApi, type Role } from '@/api/rbac'
import {
  userApi,
  type AdminUser,
  type UserCreateRequest,
  type UserImportOut,
  type UserListQuery,
  type UserUpdateRequest,
} from '@/api/user'
import { dash } from '@/api/types'
import { fmtDateTime, copyText } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TraceFooter from '@/components/TraceFooter.vue'
import { useNotify } from '@/composables/useNotify'
import { usePagedTable } from '@/composables/usePagedTable'

const notify = useNotify()

const desc = '账号管理、角色分配与批量导入。角色分配为全量替换语义。'

// ---------------- 筛选 ----------------
const keywordInput = ref('')
const orgId = ref<string | null>(null)
const roleCode = ref<string | null>(null)
const statusFilter = ref<number | null>(null)

const orgTree = ref<OrgNode[]>([])
const positions = ref<Position[]>([])
const roleOptions = ref<Role[]>([])

function buildQuery(): UserListQuery {
  const q: UserListQuery = {}
  if (keywordInput.value.trim()) q.keyword = keywordInput.value.trim()
  if (orgId.value) q.org_id = orgId.value
  if (roleCode.value) q.role_code = roleCode.value
  if (statusFilter.value !== null) q.status = statusFilter.value
  return q
}

const table = usePagedTable(
  (params) => userApi.list(params as unknown as UserListQuery),
  buildQuery() as unknown as Record<string, unknown>,
  { perPage: 20 },
)

/** 先删旧键再合并（防脏筛选条件 —— 筛选器类页面的通用纪律）。 */
function applyQuery(): void {
  const next = buildQuery() as unknown as Record<string, unknown>
  const target = table.query as unknown as Record<string, unknown>
  for (const key of Object.keys(target)) {
    if (!(key in next)) delete target[key]
  }
  Object.assign(target, next)
  table.search()
}

function reload(): void {
  applyQuery()
}

function onReset(): void {
  keywordInput.value = ''
  orgId.value = null
  roleCode.value = null
  statusFilter.value = null
  applyQuery()
}

const submitting = ref(false)

// ---------------- 新建（API-14） ----------------
const createVisible = ref(false)
const createForm = ref<UserCreateRequest>({ username: '', status: 1 })

function openCreate(): void {
  createForm.value = {
    username: '',
    password: '',
    real_name: '',
    org_id: null,
    position_id: null,
    phone: '',
    email: '',
    role_ids: [],
    status: 1,
  }
  createVisible.value = true
}

/** 🔴 一次性初始口令 —— 只放在内存里，不落任何持久化存储。 */
const pwdVisible = ref(false)
const initialPassword = ref('')

async function submitCreate(): Promise<void> {
  if (!createForm.value.username.trim()) {
    notify.warn('用户名必填')
    return
  }
  submitting.value = true
  try {
    const res = await userApi.create({
      ...createForm.value,
      username: createForm.value.username.trim(),
      password: createForm.value.password || undefined,
    })
    notify.ok('已创建用户')
    createVisible.value = false
    // 🔴 立即展示一次性口令
    if (res.data?.initial_password) {
      initialPassword.value = res.data.initial_password
      pwdVisible.value = true
    }
    await table.load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 40901) {
      notify.warn('用户名已存在（唯一约束冲突 40901），请更换后重试。')
    } else {
      notify.fail(e, '创建失败')
    }
  } finally {
    submitting.value = false
  }
}

async function copyPwd(): Promise<void> {
  const done = await copyText(initialPassword.value)
  ElMessage({ type: done ? 'success' : 'warning', message: done ? '已复制' : '复制失败，请手动选择' })
}

// ---------------- 编辑（API-15） ----------------
const editVisible = ref(false)
const editing = ref<AdminUser | null>(null)
const editForm = ref<UserUpdateRequest>({})

function openEdit(row: AdminUser): void {
  editing.value = row
  editForm.value = {
    real_name: row.real_name ?? '',
    org_id: row.org_id,
    position_id: row.position_id,
    phone: row.phone ?? '',
    email: row.email ?? '',
    status: row.status,
  }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  const row = editing.value
  if (!row) return
  submitting.value = true
  try {
    await userApi.update(row.id, editForm.value)
    notify.ok('已保存')
    editVisible.value = false
    await table.load()
  } catch (e) {
    notify.fail(e, '保存失败')
  } finally {
    submitting.value = false
  }
}

async function toggleStatus(row: AdminUser): Promise<void> {
  const to = row.status === 1 ? 0 : 1
  const okToGo = await notify.confirm(
    `确认${to === 1 ? '启用' : '停用'}账号「${row.username}」？` +
      (to === 0 ? '停用后该账号将无法登录（已签发的令牌在有效期内可能仍然可用）。' : ''),
  )
  if (!okToGo) return
  try {
    await userApi.update(row.id, { status: to })
    notify.ok(to === 1 ? '已启用' : '已停用')
    await table.load()
  } catch (e) {
    notify.fail(e, '操作失败')
  }
}

// ---------------- 分配角色（API-16，全量替换） ----------------
const rolesVisible = ref(false)
const roleUser = ref<AdminUser | null>(null)
const roleIds = ref<string[]>([])

function openRoles(row: AdminUser): void {
  roleUser.value = row
  roleIds.value = [...(row.role_ids ?? [])]
  rolesVisible.value = true
}

async function submitRoles(): Promise<void> {
  const row = roleUser.value
  if (!row) return
  submitting.value = true
  try {
    const res = await userApi.assignRoles(row.id, roleIds.value)
    notify.ok(
      `已保存。该用户当前角色：${(res.data?.roles ?? []).join(' / ') || '无'}（全量替换）`,
    )
    rolesVisible.value = false
    await table.load()
  } catch (e) {
    notify.fail(e, '保存角色失败')
  } finally {
    submitting.value = false
  }
}

// ---------------- 批量导入（API-17） ----------------
const importVisible = ref(false)
const importFile = ref<File | null>(null)
const importResult = ref<UserImportOut | null>(null)
const uploadPercent = ref(0)

function onFileChange(file: { raw?: File }): void {
  importFile.value = file.raw ?? null
  importResult.value = null
  uploadPercent.value = 0
}

async function submitImport(): Promise<void> {
  const f = importFile.value
  if (!f) return
  submitting.value = true
  uploadPercent.value = 1
  try {
    const res = await userApi.importUsers(f, (p) => (uploadPercent.value = p))
    importResult.value = res.data
    const out = res.data
    if (out) {
      notify.ok(`导入完成：成功 ${out.success} 行，失败 ${out.failed} 行`)
    }
    await table.load()
  } catch (e) {
    if (e instanceof ApiError && e.code === 41501) {
      notify.warn('文件格式不支持，请上传 .xlsx / .xls / .csv。')
    } else if (e instanceof ApiError && e.code === 41301) {
      notify.warn('文件超出大小上限。')
    } else {
      notify.fail(e, '导入失败')
    }
  } finally {
    submitting.value = false
    uploadPercent.value = 0
  }
}

// ---------------- 参考数据（失败不阻断主列表） ----------------
onMounted(async () => {
  await Promise.allSettled([
    (async () => {
      const res = await orgApi.tree({ status: 1 })
      orgTree.value = res.data?.items ?? []
    })(),
    (async () => {
      const res = await orgApi.positions({})
      positions.value = res.data?.items ?? []
    })(),
    (async () => {
      const res = await rbacApi.roles()
      roleOptions.value = res.data?.items ?? []
    })(),
  ])
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
.tag-gap {
  margin-right: 4px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
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
.form .inline {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.pwd-box {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px dashed var(--line);
  border-radius: var(--radius);
  padding: 12px 14px;
  background: var(--panel-2, #fafbfd);
}
.pwd-box .pwd {
  font-family: var(--mono);
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--primary);
  user-select: all;
}
.import-result .res-line {
  font-size: 12.5px;
  margin-bottom: 9px;
}
.ok {
  color: var(--ok);
}
.danger {
  color: var(--danger);
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.mt8 {
  margin-top: 8px;
}
.mt12 {
  margin-top: 12px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
