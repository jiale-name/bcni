/**
 * O · AI 中台（API-81 ~ API-89，F-9.1 / F-9.2 / F-9.5）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-81 模型配置列表 | GET | `/ai/models` | `ai:model:list` |
 * | API-82 修改模型配置与主备路由 | PUT | `/ai/models/{model_id}` | `ai:model:update` |
 * | API-83 模型健康与熔断状态 | GET | `/ai/models/health` | `ai:model:health` |
 * | API-84 Prompt 模板列表（含版本） | GET | `/ai/prompts` | `ai:prompt:list` |
 * | API-85 新建 Prompt 模板 | POST | `/ai/prompts` | `ai:prompt:create` |
 * | API-86 修改 Prompt（**生成新版本，不改历史**） | PUT | `/ai/prompts/{prompt_id}` | `ai:prompt:update` |
 * | API-87 回滚到指定版本 | POST | `/ai/prompts/{prompt_id}/rollback` | `ai:prompt:rollback` |
 * | API-88 AI 调用日志 | GET | `/ai/call-logs` | `ai:log:view` |
 * | API-89 Token 成本看板 | GET | `/ai/cost-dashboard` | `ai:cost:view` |
 *
 * 🔴 五条口径（CU-10 ~ CU-12 交付时钉死）：
 *   ① **Prompt 回滚 = 追加新版本**（不是改历史行）—— 历史是审计证据；
 *      因此回滚后 `version` 会**继续增长**，前端不要期待版本号回退。
 *   ② **同内容改版 → `42201`**；族内唯一启用（同一 `prompt_code` 只能一个 `status=1`）。
 *   ③ **脱敏率的分母是「调用时刻的事实列 `deployment`」**，不是模型配置的**当前值**
 *      —— 改一次配置不会让历史合规统计集体失真。故日志/看板的 `mask_applied_rate`
 *      **只能按日志行自身统计**，前端不得用「当前配置」重新推算。
 *   ④ **单价为 0 表示本地部署**（不走云端计费）；`cost_yuan` 是**字符串**（`Decimal` 序列化），
 *      前端**必须按字符串渲染或 `Number()` 后禁用于金额累加**，不要用浮点直接相乘累加。
 *   ⑤ **熔断 `blocked=true` 是「已被熔断拦截」**，`cooldown_remaining_seconds` 给出剩余冷却；
 *      `all_primary_healthy=false` 表示主通道有不可用项 —— 页面要显著提示。
 *   ⑥ **API-88 的响应在分页壳上追加顶层 `summary`**（与 `page` 同级，不在 `data` 里）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

// ============================================================================
// 模型配置
// ============================================================================

export interface ModelConfig {
  id: SnowflakeId
  scene: string
  scene_name: string | null
  model_code: string
  display_name: string
  provider: string
  provider_name: string | null
  /** 🔴 调用时刻的事实列：本地 / 云端（脱敏率统计的分母依据）。 */
  deployment: string
  deployment_name: string | null
  base_url: string
  model_name: string
  /** 主备路由优先级：越小越优先。 */
  priority: number
  timeout_seconds: number
  max_retries: number
  breaker_threshold: number
  breaker_cooldown_seconds: number
  /** 单价（元 / 1K tokens），字符串；`"0"` = 本地部署不计费。 */
  price_in_per_1k: string
  price_out_per_1k: string
  status: number
  status_name: string | null
  remark: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ModelConfigUpdateRequest {
  display_name?: string
  provider?: string
  deployment?: string
  base_url?: string
  model_name?: string
  priority?: number
  timeout_seconds?: number
  max_retries?: number
  breaker_threshold?: number
  breaker_cooldown_seconds?: number
  price_in_per_1k?: string | number
  price_out_per_1k?: string | number
  status?: number
  remark?: string
}

export interface ModelTarget {
  id: SnowflakeId
  scene: string
  scene_name: string | null
  model_code: string
  display_name: string
  provider: string
  deployment: string
  model_name: string
  priority: number
  status: number
  status_name: string | null
  healthy: boolean
  latency_ms: number | null
  error: string | null
  /** 已被熔断拦截。 */
  blocked: boolean
  consecutive_failures: number
  cooldown_remaining_seconds: number
}

export interface ModelHealth {
  checked_at: string | null
  total: number
  healthy: number
  unavailable: number
  blocked: number
  all_primary_healthy: boolean
  breaker_backend_ok: boolean
  items: ModelTarget[]
}

// ============================================================================
// Prompt
// ============================================================================

export interface Prompt {
  id: SnowflakeId
  prompt_code: string
  scene: string
  scene_name: string | null
  /** 🔴 版本只增不减（回滚 = 追加新版本）。 */
  version: number
  content: string
  status: number
  status_name: string | null
  created_by: SnowflakeId | null
  created_at: string | null
  updated_at: string | null
}

export interface PromptCreateRequest {
  prompt_code: string
  scene: string
  content: string
  status?: number
  remark?: string
}

export interface PromptUpdateRequest {
  content: string
  status?: number
  remark?: string
}

// ============================================================================
// 调用日志与成本
// ============================================================================

export interface AiCallLog {
  id: SnowflakeId
  trace_id: string | null
  scene: string | null
  scene_name: string | null
  scene_stage: string | null
  model: string | null
  provider: string | null
  prompt_id: SnowflakeId | null
  prompt_code: string | null
  prompt_version: number | null
  tokens_in: number
  tokens_out: number
  /** 字符串（`Decimal` 序列化）。 */
  cost_yuan: string
  latency_ms: number | null
  first_token_ms: number | null
  cache_hit: boolean
  fallback_used: boolean
  mask_applied: boolean
  status: number
  status_name: string | null
  error_code: string | null
  error_msg: string | null
  created_at: string | null
}

export interface AiCallSummary {
  calls: number
  cost_total_yuan: string
  avg_latency_ms: number
  failed: number
  mask_applied_rate: number
  cache_hit_rate: number
  cloud_calls: number
  cost_alert: boolean
  mask_alert: boolean
}

export interface AiCallLogQuery {
  page?: number
  per_page?: number
  scene?: string
  model?: string
  status?: number
  cache_hit?: boolean
  mask_applied?: boolean
  user_id?: SnowflakeId
  trace_id?: string
  start_time?: string
  end_time?: string
}

export interface CostBucket {
  key: string
  label: string | null
  calls: number
  tokens_in: number
  tokens_out: number
  cost_yuan: string
  avg_latency_ms: number
}

export interface CostDashboard {
  start_time: string | null
  end_time: string | null
  total: AiCallSummary
  by_scene: CostBucket[]
  by_model: CostBucket[]
  by_time: CostBucket[]
}

export const aiApi = {
  models(params: { scene?: string; status?: number; page?: number; per_page?: number } = {}) {
    return http.get<ModelConfig[]>('/ai/models', params as Record<string, unknown>)
  },
  modelHealth(params: { scene?: string } = {}) {
    return http.get<ModelHealth>('/ai/models/health', params)
  },
  updateModel(modelId: SnowflakeId, payload: ModelConfigUpdateRequest) {
    return http.put<ModelConfig>(`/ai/models/${modelId}`, payload)
  },

  prompts(params: {
    scene?: string
    prompt_code?: string
    status?: number
    page?: number
    per_page?: number
  } = {}) {
    return http.get<Prompt[]>('/ai/prompts', params as Record<string, unknown>)
  },
  createPrompt(payload: PromptCreateRequest) {
    return http.post<Prompt>('/ai/prompts', payload)
  },
  /** 修改 = **生成新版本**（不改历史行）。 */
  updatePrompt(promptId: SnowflakeId, payload: PromptUpdateRequest) {
    return http.put<Prompt>(`/ai/prompts/${promptId}`, payload)
  },
  /** 回滚 = **追加新版本**；版本号继续增长，不会回退。 */
  rollbackPrompt(promptId: SnowflakeId, targetVersion: number, remark?: string) {
    return http.post<Prompt>(`/ai/prompts/${promptId}/rollback`, {
      target_version: targetVersion,
      remark,
    })
  },

  /**
   * ⚠️ 响应在分页壳上追加**顶层** `summary`（同级 `page`）——
   * 已由 `http.ts` 归一进 **`res.extra`**（**不是** `res.data.summary`）。
   */
  callLogs(query: AiCallLogQuery) {
    return http.get<AiCallLog[], AiCallSummary>('/ai/call-logs', query as Record<string, unknown>)
  },
  costDashboard(params: {
    scene?: string
    model?: string
    start_time?: string
    end_time?: string
  } = {}) {
    return http.get<CostDashboard>('/ai/cost-dashboard', params as Record<string, unknown>)
  },
}

/**
 * 金额安全处理：`cost_yuan` 是**字符串**（后端 `Decimal`）。
 * 展示优先直接显示字符串；确需累加时用「分为单位的整数」运算，避免浮点误差。
 */
export function yuanToCents(s: string | null | undefined): number {
  if (!s) return 0
  // 🔴 先从字符串解析成整数分，**不要** parseFloat(s) * 100（浮点会引入 0.1+0.2 类误差）
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(s).trim())
  if (!m) return 0
  const sign = m[1] === '-' ? -1 : 1
  const intPart = Number(m[2]) * 100
  const frac = Number((m[3] || '').padEnd(2, '0'))
  return sign * (intPart + frac)
}

/** 分 → 元字符串（两位小数）。 */
export function centsToYuan(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(cents)
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}
