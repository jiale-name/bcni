<template>
  <div class="shell" :class="{ collapsed: app.sidebarCollapsed, 'nav-open': mobileOpen }">
    <!-- ============ 侧栏 ============ -->
    <aside class="sidebar">
      <div class="brand">
        <span class="dot">才</span>
        <span v-show="!app.sidebarCollapsed" class="brand-copy">
          <b>AI 人才平台</b>
          <small>一期 · 档案与数字画像</small>
        </span>
        <button
          class="collapse-btn"
          type="button"
          :title="app.sidebarCollapsed ? '展开导航' : '收起导航'"
          @click="app.toggleSidebar()"
        >
          {{ app.sidebarCollapsed ? '›' : '‹' }}
        </button>
      </div>

      <nav class="nav" aria-label="主导航">
        <template v-for="group in navGroups" :key="group.key">
          <div v-if="group.items.length" class="grp">{{ group.label }}</div>
          <template v-for="item in group.items" :key="item.path">
            <router-link
              v-show="!item.hidden"
              :to="item.path"
              class="nav-item"
              :class="{ active: isActive(item.path) }"
            >
              <el-icon class="ico"><component :is="item.icon || 'Menu'" /></el-icon>
              <span v-show="!app.sidebarCollapsed" class="lbl">{{ item.title }}</span>
            </router-link>
          </template>
        </template>
      </nav>

      <div class="userbox">
        <span class="avatar">{{ auth.avatarText }}</span>
        <span v-show="!app.sidebarCollapsed" class="meta">
          <b>{{ auth.displayName }}</b>
          <small>{{ roleText }}</small>
        </span>
        <el-tooltip content="退出登录" placement="top">
          <el-icon v-show="!app.sidebarCollapsed" class="out" @click="onLogout"><SwitchButton /></el-icon>
        </el-tooltip>
      </div>
    </aside>

    <button class="nav-backdrop" type="button" aria-label="关闭导航" @click="mobileOpen = false" />

    <!-- ============ 主区 ============ -->
    <main class="main">
      <header class="topbar">
        <button class="mobile-toggle" type="button" aria-label="打开导航" @click="mobileOpen = true">☰</button>
        <div class="crumb">
          <b>{{ currentTitle }}</b>
          <span v-if="currentBreadcrumb">{{ currentBreadcrumb }}</span>
        </div>
        <div class="acts">
          <span class="scope-badge">数据域：{{ auth.scopeLabel }}</span>
          <span class="muted">|</span>
          <span>{{ auth.displayName }}（{{ primaryRole }}）</span>
          <el-tooltip content="修改本人密码" placement="bottom">
            <el-icon class="act-icon" @click="pwdVisible = true"><Key /></el-icon>
          </el-tooltip>
          <el-tooltip content="退出登录" placement="bottom">
            <el-icon class="act-icon" @click="onLogout"><SwitchButton /></el-icon>
          </el-tooltip>
        </div>
      </header>

      <div class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </div>
    </main>

    <!-- ============ 修改本人密码（API-06） ============ -->
    <el-dialog v-model="pwdVisible" title="修改本人密码" width="420px" @closed="resetPwdForm">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="修改成功后，全部旧会话将失效，需要重新登录。"
        class="mb12"
      />
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="88px">
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="pwdForm.old_password" type="password" show-password placeholder="请输入原密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="8~64 字符" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="pwdForm.confirm" type="password" show-password placeholder="再次输入新密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSubmitting" @click="submitPwd">确认修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 应用外壳：侧栏 + 顶栏 + 内容区。
 *
 * 🔴 三条设计口径：
 *   1. **侧栏按 `meta.group` 分组、按 `meta.roles` 过滤** —— 菜单即权限可见性的第一层，
 *      但**不是安全边界**（后端 40301 才是）。
 *   2. **顶栏常驻展示「数据域」** —— 让 CHR/MGR 随时意识到自己看到的是哪一档数据
 *      （接口 §1.7 的字段级 / 行级裁剪是静默生效的，不提示最容易被误解为「数据丢了」）。
 *   3. **修改密码后强制重新登录**（API-06 `relogin_required=true`）—— 旧会话已失效，
 *      留在原地会让用户以为「还没改成功」。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { authApi } from '@/api/auth'
import { useNotify } from '@/composables/useNotify'
import routes, { NAV_GROUPS, type RouteMeta } from '@/router/routes'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'

const app = useAppStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const notify = useNotify()

const mobileOpen = ref(false)

interface NavItem {
  path: string
  title: string
  icon?: string
  hidden?: boolean
}

/** 展平路由 children 并按分组归类（只保留可见项）。 */
const navGroups = computed(() => {
  const layout = routes.find((r) => r.path === '/')
  const children = (layout?.children ?? []) as unknown as {
    path: string
    meta?: RouteMeta
  }[]

  return NAV_GROUPS.map((g) => {
    const items: NavItem[] = []
    for (const c of children) {
      const meta = c.meta
      if (!meta || meta.public || meta.group === 'none') continue
      if (meta.group !== g.key) continue
      // 角色不满足 → 不显示（体验层；后端仍会 40301）
      if (meta.roles?.length && !auth.hasRole(meta.roles)) continue
      // 额外权限码（如 privacy:self_view）：ADM 由 isAdmin 放行
      if (meta.perm && !auth.hasPerm(meta.perm)) continue
      items.push({
        path: `/${c.path}`,
        title: meta.title,
        icon: meta.icon,
        hidden: meta.hidden,
      })
    }
    return { key: g.key, label: g.label, items }
  }).filter((g) => g.label !== '' || g.items.length > 0)
})

// 🔴 经 `unknown` 中转：vue-router 的 `RouteMeta` 与本项目 `RouteMeta` 无直接可比性。
const currentMeta = computed<RouteMeta>(() => (route.meta ?? {}) as unknown as RouteMeta)
const currentTitle = computed(() => currentMeta.value.title || '')
const currentBreadcrumb = computed(() => (currentMeta.value.breadcrumb ?? []).join(' / '))

const primaryRole = computed(() => auth.roles[0] || '—')
const roleText = computed(() => `${auth.roles.join('/') || '无角色'} · ${auth.user?.org_name || '未分配部门'}`)

/** 详情页要让父级（列表）保持高亮。 */
function isActive(path: string): boolean {
  const current = route.path
  if (current === path) return true
  if (path === '/talent/list' && current.startsWith('/talent/detail')) return true
  if (path === '/talent/change-log' && current.startsWith('/talent/change-log')) return true
  return false
}

async function onLogout(): Promise<void> {
  const yes = await notify.confirm('确认退出登录？', '退出登录')
  if (!yes) return
  await auth.logout()
  void router.replace({ name: 'login' })
}

// ---------------- 修改本人密码 ----------------
const pwdVisible = ref(false)
const pwdSubmitting = ref(false)
const pwdFormRef = ref<FormInstance>()
const pwdForm = ref({ old_password: '', new_password: '', confirm: '' })

const pwdRules: FormRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, max: 64, message: '长度 8~64 字符', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_r, v: string, cb) => {
        if (v !== pwdForm.value.new_password) cb(new Error('两次输入不一致'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
}

function resetPwdForm(): void {
  pwdForm.value = { old_password: '', new_password: '', confirm: '' }
  pwdFormRef.value?.clearValidate()
}

async function submitPwd(): Promise<void> {
  const form = pwdFormRef.value
  if (!form) return
  const valid = await form.validate().catch(() => false)
  if (!valid) return
  pwdSubmitting.value = true
  try {
    const res = await authApi.changePassword({
      old_password: pwdForm.value.old_password,
      new_password: pwdForm.value.new_password,
    })
    pwdVisible.value = false
    if (res.data?.relogin_required) {
      ElMessage({ type: 'success', message: '密码已修改，请重新登录', duration: 3000 })
      auth.reset()
      void router.replace({ name: 'login' })
    } else {
      notify.ok('密码已修改')
    }
  } catch (e) {
    notify.fail(e, '修改密码失败')
  } finally {
    pwdSubmitting.value = false
  }
}
</script>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* ============ 侧栏 ============ */
.sidebar {
  width: var(--sidebar);
  flex: 0 0 var(--sidebar);
  border-right: 1px solid var(--nav-line);
  display: flex;
  flex-direction: column;
  background: var(--nav-bg);
  transition: width 0.18s ease, flex-basis 0.18s ease;
  z-index: 20;
}
.collapsed .sidebar {
  width: 64px;
  flex-basis: 64px;
}
.brand {
  height: var(--topbar);
  flex: 0 0 var(--topbar);
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 0 12px;
  border-bottom: 1px solid var(--nav-line);
}
.brand .dot {
  width: 22px;
  height: 22px;
  border-radius: 5px;
  background: #168c92;
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}
.brand-copy {
  min-width: 0;
  flex: 1;
}
.brand-copy b {
  display: block;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.2;
}
.brand-copy small {
  display: block;
  font-size: 10.5px;
  color: var(--txt-3);
}
.collapse-btn {
  border: none;
  background: transparent;
  color: var(--nav-group);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 2px 4px;
  flex: 0 0 auto;
}
.collapse-btn:hover {
  color: var(--primary);
}
.collapsed .brand {
  justify-content: center;
  padding: 0;
}
.collapsed .collapse-btn {
  display: none;
}

.nav {
  padding: 8px 8px 14px;
  flex: 1;
  overflow-y: auto;
}
.nav .grp {
  font-size: 11px;
  color: var(--nav-group);
  padding: 12px 12px 5px;
  letter-spacing: 0.6px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 11px;
  border-radius: 5px;
  color: #46656a;
  font-size: 13.5px;
  margin-bottom: 1px;
  text-decoration: none;
}
.nav-item:hover {
  background: var(--nav-hover);
  color: #1a5b63;
  text-decoration: none;
}
.nav-item.active {
  background: var(--nav-active);
  color: var(--nav-active-txt);
  font-weight: 600;
  box-shadow: inset 3px 0 0 #168c92;
}
.nav-item .ico {
  width: 15px;
  font-size: 13px;
  flex: 0 0 auto;
}
.collapsed .nav .grp {
  display: none;
}
.collapsed .nav-item {
  justify-content: center;
  padding: 9px 0;
}

.userbox {
  border-top: 1px solid var(--nav-line);
  padding: 11px 14px;
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12.5px;
  color: var(--txt-2);
}
.avatar {
  width: 27px;
  height: 27px;
  border-radius: 50%;
  background: #d4edeb;
  color: #0f6670;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex: 0 0 auto;
}
.userbox .meta {
  min-width: 0;
  flex: 1;
}
.userbox .meta b {
  display: block;
  font-size: 12.5px;
  line-height: 1.3;
}
.userbox .meta small {
  font-size: 11px;
  color: var(--txt-3);
}
.userbox .out {
  cursor: pointer;
  color: var(--txt-3);
  flex: 0 0 auto;
}
.userbox .out:hover {
  color: var(--danger);
}
.collapsed .userbox {
  justify-content: center;
  padding: 11px 0;
}

/* ============ 主区 ============ */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.topbar {
  height: var(--topbar);
  flex: 0 0 var(--topbar);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  gap: 12px;
}
.crumb {
  min-width: 0;
  font-size: 12.5px;
  color: var(--txt-3);
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.crumb b {
  color: var(--txt);
  font-weight: 600;
  font-size: 15px;
}
.acts {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
  color: var(--txt-2);
  flex: 0 0 auto;
}
.scope-badge {
  background: var(--warn-weak);
  color: var(--warn);
  border: 1px solid var(--warn-line);
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}
.act-icon {
  cursor: pointer;
  color: var(--txt-3);
}
.act-icon:hover {
  color: var(--primary);
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 40px;
}
.mobile-toggle {
  display: none;
  border: none;
  background: transparent;
  font-size: 17px;
  cursor: pointer;
  color: var(--txt-2);
}
.nav-backdrop {
  display: none;
}

@media (max-width: 1080px) {
  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    transform: translateX(-100%);
    transition: transform 0.22s ease;
    width: var(--sidebar);
    flex-basis: var(--sidebar);
  }
  .nav-open .sidebar {
    transform: none;
  }
  .nav-open .nav-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(16, 30, 54, 0.42);
    border: none;
    z-index: 15;
  }
  .mobile-toggle {
    display: block;
  }
  .acts span.muted,
  .acts span:nth-child(3) {
    display: none;
  }
}
</style>
