/**
 * 认证与权限状态（Pinia）。
 *
 * 🔴 三条铁律（接口 §1.4 / §4）：
 *   1. **权限数据只从服务端读**（API-04 的 `roles` / API-05 的 `perm_codes`）——
 *      前端不得由 `data_scope`、部门、URL 自行推导权限。
 *   2. **前端隐藏不是安全边界** —— `v-perm` 只决定「要不要显示按钮」，
 *      真正的裁决是后端 `40301`。因此**所有** 40301 都必须有可渲染的状态。
 *   3. **令牌与身份态分离**：令牌读写只在 `utils/token.ts`，本 store 只持有解析后的人物信息。
 */

import { defineStore } from 'pinia'

import { authApi, type PermissionsOut, type UserInfo } from '@/api/auth'
import { tokenStorage } from '@/utils/token'

interface AuthState {
  user: UserInfo | null
  /** 按钮 / 接口级权限码（API-05）。 */
  permCodes: string[]
  roles: string[]
  isAdmin: boolean
  /** 权限快照版本号，用于判断是否需要重取。 */
  permVersion: number
  /** 是否已完成「拉用户 + 拉权限」的一次初始化（路由守卫据此决定是否等待）。 */
  ready: boolean
  /** 初始化是否进行中（并发守卫调用时共享同一个 Promise）。 */
  initializing: Promise<void> | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    user: null,
    permCodes: [],
    roles: [],
    isAdmin: false,
    permVersion: 0,
    ready: false,
    initializing: null,
  }),

  getters: {
    /** 展示名：真实姓名优先，退化到登录名。 */
    displayName(state): string {
      return state.user?.real_name || state.user?.username || '未登录'
    },
    /** 头像首字。 */
    avatarText(state): string {
      const n = state.user?.real_name || state.user?.username || '?'
      return n.slice(0, 1)
    },
    /** 数据域描述（顶栏展示；优先用后端 `data_scope_name`）。 */
    scopeLabel(state): string {
      const u = state.user
      if (!u) return '—'
      if (u.data_scope === 1) return '全部'
      if (u.data_scope_name) return u.data_scope_name
      return '—'
    },
  },

  actions: {
    /**
     * 权限判定。
     * @param codes 任一命中即通过（与后端 `require_perm` 的「任一满足」语义一致）。
     */
    hasPerm(codes: string | string[]): boolean {
      if (this.isAdmin) return true
      const list = Array.isArray(codes) ? codes : [codes]
      if (list.length === 0) return true
      return list.some((c) => this.permCodes.includes(c))
    },

    /** 角色判定（路由 `meta.roles` 用；ADM 视为拥有全部角色可见性）。 */
    hasRole(roles: string | string[]): boolean {
      const list = Array.isArray(roles) ? roles : [roles]
      if (list.length === 0) return true
      if (this.isAdmin) return true
      return list.some((r) => this.roles.includes(r))
    },

    async login(username: string, password: string, tenantCode?: string) {
      const res = await authApi.login({ username, password, tenant_code: tenantCode })
      const payload = res.data
      if (!payload) throw new Error('登录响应缺少数据')
      // 🔴 刷新令牌一并保存；refresh 调用不返回新的 refresh_token（不延长其生命周期）
      tokenStorage.set(payload.access_token, payload.refresh_token)
      this.user = payload.user
      this.roles = [...payload.user.roles]
      await this.loadPermissions()
      this.ready = true
      return payload
    },

    async loadPermissions() {
      const res = await authApi.permissions()
      const p: PermissionsOut | null = res.data
      if (!p) return
      this.permCodes = [...p.perm_codes]
      this.isAdmin = p.is_admin
      this.permVersion = p.perm_version
      // API-04 的角色更权威（含数据域），若 API-05 也回了角色则以它对齐
      if (p.roles?.length) this.roles = [...p.roles]
    },

    async loadMe() {
      const res = await authApi.me()
      if (res.data) {
        this.user = res.data
        this.roles = [...res.data.roles]
      }
    },

    /**
     * 首次进入时的初始化（幂等 + 单飞）。
     * 路由守卫每次导航都会调它，因此必须共享 Promise，避免并发重复请求。
     */
    ensureReady(): Promise<void> {
      if (this.ready) return Promise.resolve()
      if (this.initializing) return this.initializing
      this.initializing = (async () => {
        try {
          await this.loadMe()
          await this.loadPermissions()
          this.ready = true
        } finally {
          this.initializing = null
        }
      })()
      return this.initializing
    },

    async logout(allDevices = false) {
      const refresh = tokenStorage.getRefresh()
      try {
        await authApi.logout({ refresh_token: refresh ?? undefined, all_devices: allDevices })
      } catch {
        // 登出接口失败也要清本地态：否则用户会卡在「已登录但令牌无效」
      } finally {
        this.reset()
      }
    },

    reset() {
      tokenStorage.clear()
      this.user = null
      this.permCodes = []
      this.roles = []
      this.isAdmin = false
      this.permVersion = 0
      this.ready = false
      this.initializing = null
    },
  },
})
