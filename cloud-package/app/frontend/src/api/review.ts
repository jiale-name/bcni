/**
 * H · 分层复核（API-44 ~ API-49，F-3.3 / F-3.3.1）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-44 复核队列 | GET | `/talent/review` | `review:queue` |
 * | API-45 复核详情 | GET | `/talent/review/{field_id}` | `review:detail` |
 * | API-46 逐字段处置 / 批量确认 | POST | `/talent/review/confirm` | `review:confirm` |
 * | API-47 修正字段值 | POST | `/talent/review/{field_id}/correct` | `review:correct` |
 * | API-48 跳过该字段 | POST | `/talent/review/{field_id}/skip` | `review:skip` |
 * | API-49 工作量看板 | GET | `/talent/review/dashboard` | `review:dashboard` |
 *
 * 🔴 四条口径：
 *   ① **队列响应有顶层 `meta`**（与 `page` 同级，不在 `data` 里）—— `ReviewQueueResp`
 *      在分页壳上追加了 `meta`（`high_impact_total` / `low_impact_sampled_total` /
 *      `warning` / `estimated_minutes`）。前端若把它当 `data` 读会拿到 `undefined`。
 *   ② **`meta.warning` 是「非阻断提示」**（如「低影响字段为抽样，非全量」）——
 *      必须显示，但**不拦截**操作。
 *   ③ **批量确认上限 200 条**，超出 `40002`（不是截断）。
 *   ④ **并发处置冲突 → `40902`**，响应体 `conflicted[]` 列出被他人抢先的字段；
 *      前端须把冲突项**从选中集里剔除并高亮**，而不是整批失败。
 *   ⑤ **EDIT（修正）后置信度置 1.0**，与流水线「人工修正」口径一致 —— 前端不得自己写 1.0。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface ReviewQueueItem {
  field_id: SnowflakeId
  talent_id: SnowflakeId
  talent_name: string
  field_code: string
  field_name: string
  raw_value: string | null
  parsed_value: string | null
  /** 置信度；`null` 表示该字段不来自模型（确定性规则）—— **不是 0**。 */
  confidence: number | null
  /** `HIGH` / `LOW`。 */
  impact_level: string
  impact_level_name: string
  review_status: number
  review_status_name: string | null
  source_file: string | null
  source_locator: string | null
}

export interface ReviewQueueMeta {
  high_impact_total: number
  low_impact_sampled_total: number
  /** 非阻断提示（如「低影响字段按抽样下发」）。 */
  warning: string | null
  estimated_minutes: number | null
}

export interface ReviewDetail extends ReviewQueueItem {}

export interface ReviewDashboard {
  pending_total: number
  confirmed_total: number
  corrected_total: number
  skipped_total: number
  by_field: Record<string, unknown>[]
  estimated_remaining_minutes: number
}

/** 批量确认的单条动作。 */
export interface ReviewConfirmAction {
  field_id?: SnowflakeId
  /** `ACCEPT_AI`（采纳 AI 值）/ `ACCEPT_RAW`（采纳原文值）/ `SKIP`。 */
  action?: 'ACCEPT_AI' | 'ACCEPT_RAW' | 'SKIP'
  /** 若该条走 EDIT，则直接给修正值。 */
  value?: string
}

export interface ReviewConfirmOut {
  confirmed_count: number
  corrected_count: number
  skipped_count: number
  /** 🔴 被他人抢先处理的字段（`40902` 的**局部**表现，整批不失败）。 */
  conflicted: Record<string, unknown>[]
  change_log_ids: SnowflakeId[]
}

export interface ReviewCorrectOut {
  field_id: SnowflakeId
  talent_id: SnowflakeId
  review_status: number
  review_status_name: string | null
  change_log_id: SnowflakeId | null
}

export const reviewApi = {
  /**
   * 队列。返回值为 `ApiResult<..., ReviewQueueMeta>` ——
   * 顶层 `meta` 已由 `http.ts` 归一进 **`res.extra`**（**不是** `res.data.meta`）。
   */
  queue(params: {
    page?: number
    per_page?: number
    task_id?: SnowflakeId
    impact_level?: 'HIGH' | 'LOW'
    confidence_max?: number
    field_codes?: string
    sample_only?: boolean
  }) {
    return http.get<ReviewQueueItem[], ReviewQueueMeta>(
      '/talent/review',
      params as Record<string, unknown>,
    )
  },

  dashboard() {
    return http.get<ReviewDashboard>('/talent/review/dashboard')
  },

  detail(fieldId: SnowflakeId) {
    return http.get<ReviewDetail>(`/talent/review/${fieldId}`)
  },

  /** 批量确认（≤200 条）。 */
  confirm(actions: ReviewConfirmAction[]) {
    return http.post<ReviewConfirmOut>('/talent/review/confirm', actions)
  },

  /** 修正字段值（EDIT）—— 置信度由后端置 1.0。 */
  correct(fieldId: SnowflakeId, value: string, reason?: string) {
    return http.post<ReviewCorrectOut>(`/talent/review/${fieldId}/correct`, { value, reason })
  },

  skip(fieldId: SnowflakeId) {
    return http.post<ReviewCorrectOut>(`/talent/review/${fieldId}/skip`)
  },
}

/** 批量确认上限（超出 `40002`）。 */
export const REVIEW_CONFIRM_MAX = 200
