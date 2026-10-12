/**
 * 路由实例与全局守卫。
 *
 * 🔴 守卫的四条职责（顺序不可换）：
 *   1. **免登录放行**（`/login`）——已登录访问登录页则跳工作台；
 *   2. **无令牌 → 登录页**（带 `redirect`，登录后原地返回）；
 *   3. **初始化身份与权限**（`auth.ensureReady()`）——失败即视为会话失效，清态回登录；
 *   4. **角色准入**（`meta.roles`）——不满足则进 `/403`，**不静默跳首页**
 *      （静默跳转会让人以为「点了没反应」，不利于排查权限配置）。
 *
 * ⚠️ 守卫只做**体验层**准入：真正的裁决是后端 `40301`（接口 §4）。
 *    因此页面内仍必须处理 40301（用户可能通过 URL 直达或权限在会话中被回收）。
 */

import { createRouter, createWebHistory } from 'vue-router'

import { tokenStorage } from '@/utils/token'
import { useAuthStore } from '@/store/auth'
import routes, { type RouteMeta } from './routes'

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

/** 会话彻底失效时的统一出口（`http.ts` 在 refresh 也失败时触发）。 */
let expiredRedirecting = false
tokenStorage.onExpired(() => {
  if (expiredRedirecting) return
  expiredRedirecting = true
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
  setTimeout(() => {
    expiredRedirecting = false
  }, 1000)
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // 🔴 经 `unknown` 中转：vue-router 的 `RouteMeta` 是 `Record<PropertyKey, unknown>`，
  //    与本项目 `RouteMeta`（required `title`）无直接可比性，直接 `as` 会被 TS2352 拒绝。
  const meta = (to.meta ?? {}) as unknown as RouteMeta

  // ---- 1. 免登录页 ----
  if (meta.public) {
    if (tokenStorage.hasToken() && to.name === 'login') {
      return { name: 'dashboard' }
    }
    return true
  }

  // ---- 2. 未登录 ----
  if (!tokenStorage.hasToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // ---- 3. 初始化（幂等 + 单飞）----
  if (!auth.ready) {
    try {
      await auth.ensureReady()
    } catch {
      // 身份/权限拉取失败（401 已由拦截器处理）→ 清态回登录，避免「半登录」状态
      auth.reset()
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }

  // ---- 4. 角色准入 ----
  if (meta.roles?.length && !auth.hasRole(meta.roles)) {
    return { name: 'forbidden', query: { from: to.fullPath } }
  }

  return true
})

router.afterEach((to) => {
  const meta = (to.meta ?? {}) as unknown as RouteMeta
  const appTitle = import.meta.env.VITE_APP_TITLE || 'AI 数字化人才平台'
  document.title = meta.title ? `${meta.title} · ${appTitle}` : appTitle
})

export default router
