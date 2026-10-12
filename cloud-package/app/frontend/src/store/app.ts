/**
 * 应用外壳状态：侧栏折叠、字典缓存、全局降级提示。
 */

import { defineStore } from 'pinia'

import { dictApi, type DictItem } from '@/api/dict'

interface AppState {
  sidebarCollapsed: boolean
  /** 字典项缓存：`type_code` → items。 */
  dictCache: Record<string, DictItem[]>
  /** 正在拉取的字典类型（防止并发重复请求同一字典）。 */
  dictLoading: Record<string, Promise<DictItem[]> | undefined>
}

export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    sidebarCollapsed: false,
    dictCache: {},
    dictLoading: {},
  }),

  actions: {
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
    },

    /**
     * 取字典项（带缓存 + 单飞）。
     *
     * 🔴 走 **API-24**（`dict:view` 广授），**不是** API-23（仅 ADM）——
     *    HR/MGR/EMP/EXEC 都要用它填下拉（见 `api/dict.ts` docstring 口径 ①）。
     * 🔴 失败时**不抛异常**：字典拉不到只应导致下拉为空，
     *    不该让整页进入错误态（后端 40301 时页面主体仍应可用）。
     */
    async loadDict(typeCode: string): Promise<DictItem[]> {
      if (this.dictCache[typeCode]) return this.dictCache[typeCode]
      const inflight = this.dictLoading[typeCode]
      if (inflight) return inflight

      const task = (async () => {
        try {
          const res = await dictApi.items(typeCode, { status: 1 })
          const items = res.data?.items ?? []
          this.dictCache[typeCode] = items
          return items
        } catch {
          this.dictCache[typeCode] = []
          return []
        } finally {
          this.dictLoading[typeCode] = undefined
        }
      })()

      this.dictLoading[typeCode] = task
      return task
    },

    /** 清空字典缓存（ADM 改完字典后调用，避免下拉仍显示旧值）。 */
    clearDictCache(typeCode?: string) {
      if (typeCode) delete this.dictCache[typeCode]
      else this.dictCache = {}
    },
  },
})
