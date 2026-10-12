/**
 * 统一异步加载状态（页面级）。
 *
 * 把「一次请求」的六种结果映射成**可直接渲染的状态**，避免每个页面各写一套
 * `loading / error / empty` 布尔量（那是「某些页面忘了处理 40301」的根源）。
 *
 * | status | 触发条件 | 页面应对 |
 * | --- | --- | --- |
 * | `loading` | 请求进行中 | 骨架屏；**禁用**操作按钮 |
 * | `ready` | `code=0`（或 40903 幂等回放） | 正常渲染（空数组由页面自行判断为空态） |
 * | `forbidden` | `40301` | 「无访问权限」态，**绝不暴露资源是否存在** |
 * | `notfound` | `40401` | 空态（**不是**错误态） |
 * | `error` | 其他错误码 / 网络 | 错误态 + `trace_id` + 重试 |
 *
 * 🔴 降级（`50301/50302`）**不进入 error**：`status` 仍为 `ready`，
 *    由 `degraded` 标记驱动「降级徽标」，内容照常渲染。
 */

import { onMounted, ref, type Ref } from 'vue'

import { ApiError } from '@/api/http'
import type { ApiResult } from '@/api/types'

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'forbidden' | 'notfound' | 'error'

export interface AsyncDataOptions {
  /** 是否在 `onMounted` 时立即加载（默认 `true`）。 */
  immediate?: boolean
}

export interface AsyncData<T> {
  loading: Ref<boolean>
  data: Ref<T | null>
  status: Ref<LoadStatus>
  error: Ref<ApiError | null>
  traceId: Ref<string | null>
  degraded: Ref<boolean>
  degradedReason: Ref<string | null>
  run: () => Promise<void>
  reset: () => void
}

export function useAsyncData<T>(
  fetcher: () => Promise<ApiResult<T>>,
  options: AsyncDataOptions = {},
): AsyncData<T> {
  const loading = ref(false)
  const data = ref<T | null>(null) as Ref<T | null>
  const status = ref<LoadStatus>('idle')
  const error = ref<ApiError | null>(null)
  const traceId = ref<string | null>(null)
  const degraded = ref(false)
  const degradedReason = ref<string | null>(null)

  async function run(): Promise<void> {
    loading.value = true
    status.value = 'loading'
    error.value = null
    try {
      const res = await fetcher()
      data.value = res.data
      traceId.value = res.traceId
      degraded.value = res.degraded
      degradedReason.value = res.degradedReason
      status.value = 'ready'
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError({ code: -1, message: '未知错误' })
      error.value = err
      traceId.value = err.traceId
      // 🔴 40301 与 40401 是**状态**不是**故障**，分开渲染
      if (err.isForbidden) status.value = 'forbidden'
      else if (err.isNotFound) status.value = 'notfound'
      else status.value = 'error'
    } finally {
      loading.value = false
    }
  }

  function reset(): void {
    data.value = null
    status.value = 'idle'
    error.value = null
    traceId.value = null
    degraded.value = false
    degradedReason.value = null
  }

  if (options.immediate !== false) {
    onMounted(run)
  }

  return { loading, data, status, error, traceId, degraded, degradedReason, run, reset }
}
