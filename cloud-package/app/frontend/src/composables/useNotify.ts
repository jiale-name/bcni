/**
 * 统一的用户提示（ElMessage 封装）。
 *
 * 🔴 三条口径：
 *   1. **降级不弹错误** —— `50301/50302` 由页面挂降级徽标，**不**走本模块的 `error()`。
 *   2. **40301 / 40401 不弹全局错误** —— 它们是**状态**（页面渲染无权限态 / 空态），
 *      弹一个红色 toast 只会与页面上的空态自相矛盾。
 *   3. **`40902`（并发冲突）文案要给出「下一步动作」** —— 提示「已被他人处理，请刷新后重试」，
 *      而不是笼统的「操作失败」；`40903`（幂等回放）按**成功**提示。
 */

import { ElMessage, ElMessageBox } from 'element-plus'

import { ApiError } from '@/api/http'
import { ErrorCode } from '@/api/types'

export interface Notify {
  ok: (msg: string) => void
  warn: (msg: string) => void
  info: (msg: string) => void
  /** 通用失败提示（自动跳过 40301 / 40401：那两类由页面渲染状态）。 */
  fail: (e: unknown, fallback?: string) => void
  /** 需用户确认的危险操作（归档 / 合并 / 审批删除）。 */
  confirm: (message: string, title?: string) => Promise<boolean>
}

export function useNotify(): Notify {
  function ok(msg: string) {
    ElMessage({ type: 'success', message: msg, duration: 2500 })
  }
  function warn(msg: string) {
    ElMessage({ type: 'warning', message: msg, duration: 3500 })
  }
  function info(msg: string) {
    ElMessage({ type: 'info', message: msg, duration: 2500 })
  }

  function fail(e: unknown, fallback = '操作失败'): void {
    if (e instanceof ApiError) {
      // 状态类错误：页面自行渲染，不重复弹窗
      if (e.isForbidden || e.isNotFound) return

      let msg = e.displayMessage || fallback
      if (e.code === ErrorCode.CONFLICT_STATE) {
        msg = `${msg}（请刷新后重试）`
      }
      if (e.code === ErrorCode.RATE_LIMITED && e.retryAfterSeconds) {
        msg = `${msg}（${e.retryAfterSeconds} 秒后重试）`
      }
      if (e.traceId) {
        msg = `${msg}｜trace_id: ${e.traceId}`
      }
      ElMessage({ type: 'error', message: msg, duration: 5000, showClose: true })
      return
    }
    ElMessage({ type: 'error', message: fallback, duration: 4000, showClose: true })
  }

  async function confirm(message: string, title = '请确认'): Promise<boolean> {
    try {
      await ElMessageBox.confirm(message, title, {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        type: 'warning',
      })
      return true
    } catch {
      return false
    }
  }

  return { ok, warn, info, fail, confirm }
}
