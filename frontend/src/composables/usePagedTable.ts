/**
 * 分页表格状态（**统一封装「分页壳」的读法**）。
 *
 * 🔴 这是本项目最容易写错的一处契约，故单独成 composable：
 *    后端分页响应的 `page` 是 `data` 的**同级字段**，不在 `data` 里：
 *    ```json
 *    { "code": 0, "data": [ ... ], "page": { "total": 382, "page": 1, ... } }
 *    ```
 *    若把 `page` 当 `data.page` 读，`total` 恒为 `undefined` → 分页器永远显示 0 条。
 *    （批次 13 缺陷 ② 就是这个形状被写错：`paginated(items, total, current, size)`。）
 *
 * 同时统一处理：查询条件 → 页码重置 → 加载 → 40301/40401 状态映射。
 */

import { computed, onMounted, reactive, ref, type ComputedRef, type Ref } from 'vue'

import { ApiError } from '@/api/http'
import type { ApiResult } from '@/api/types'
import type { LoadStatus } from './useAsyncData'

export interface PagedTable<T, Q extends Record<string, unknown>> {
  query: Q
  rows: Ref<T[]>
  loading: Ref<boolean>
  status: Ref<LoadStatus>
  error: Ref<ApiError | null>
  traceId: Ref<string | null>
  degraded: Ref<boolean>
  degradedReason: Ref<string | null>
  page: Ref<number>
  perPage: Ref<number>
  total: Ref<number>
  pages: Ref<number>
  /** 是否已有数据（空态判定用）。 */
  isEmpty: ComputedRef<boolean>
  /** 重新加载当前页。 */
  load: () => Promise<void>
  /** 从第 1 页开始查询（点「查询」按钮）。 */
  search: () => void
  /** 重置条件并回到第 1 页。 */
  reset: () => void
  /** 翻页 / 改每页条数。 */
  onPageChange: (p: number) => void
  onSizeChange: (s: number) => void
}

export function usePagedTable<T, Q extends Record<string, unknown>>(
  fetcher: (params: Q & { page: number; per_page: number }) => Promise<ApiResult<T[]>>,
  initialQuery: Q,
  options: { perPage?: number; immediate?: boolean } = {},
): PagedTable<T, Q> {
  const query = reactive({ ...initialQuery }) as Q
  const rows = ref([]) as Ref<T[]>
  const loading = ref(false)
  const status = ref<LoadStatus>('idle')
  const error = ref<ApiError | null>(null)
  const traceId = ref<string | null>(null)
  const degraded = ref(false)
  const degradedReason = ref<string | null>(null)

  const page = ref(1)
  const perPage = ref(options.perPage ?? 20)
  const total = ref(0)
  const pages = ref(0)

  const isEmpty = computed(() => status.value === 'ready' && rows.value.length === 0)

  async function load(): Promise<void> {
    loading.value = true
    status.value = 'loading'
    error.value = null
    try {
      const res = await fetcher({ ...(query as Q), page: page.value, per_page: perPage.value })
      // 🔴 `data` 是数组、`page` 是同级字段；两者**必须分别读**
      rows.value = res.data ?? []
      total.value = res.page?.total ?? rows.value.length
      pages.value = res.page?.pages ?? 0
      // 回填后端实际生效的每页条数（接口 §1.1：`per_page>100` 按 100 处理）
      if (res.page?.per_page) perPage.value = res.page.per_page
      if (res.page?.page) page.value = res.page.page
      traceId.value = res.traceId
      degraded.value = res.degraded
      degradedReason.value = res.degradedReason
      status.value = 'ready'
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
      error.value = err
      traceId.value = err.traceId
      rows.value = []
      total.value = 0
      pages.value = 0
      if (err.isForbidden) status.value = 'forbidden'
      else if (err.isNotFound) status.value = 'notfound'
      else status.value = 'error'
    } finally {
      loading.value = false
    }
  }

  function search(): void {
    page.value = 1
    void load()
  }

  function reset(): void {
    Object.assign(query, initialQuery)
    page.value = 1
    perPage.value = options.perPage ?? 20
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

  if (options.immediate !== false) {
    onMounted(load)
  }

  return {
    query,
    rows,
    loading,
    status,
    error,
    traceId,
    degraded,
    degradedReason,
    page,
    perPage,
    total,
    pages,
    isEmpty,
    load,
    search,
    reset,
    onPageChange,
    onSizeChange,
  }
}
