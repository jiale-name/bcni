/**
 * K · 检索与问答（API-62 / API-63，F-3.6 / F-3.7 / F-3.9）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-62 语义检索（混合召回 + RRF + Rerank） | POST | `/talent/search` | `talent:search` |
 * | API-63 档案 RAG 问答（SSE） | POST | `/talent/ask` | `talent:ask` |
 *
 * 🔴 六条口径（CU-20 / CU-21 交付时钉死，前端必须如实呈现）：
 *   ① **成功响应里 `data` 是对象**（`{ items, meta }`），**不是数组** ——
 *      不要套「`data` 是数组 + 同级 `page`」的分页壳（批次 12 缺陷 ⑥ 即此）。
 *   ② **降级**：`auto` 模式下向量库不可用 → `code=50301` + `meta.degraded=true` +
 *      `meta.mode_used='keyword'`。此时**必须显示降级徽标**，但**内容照常渲染**。
 *      ⚠️ **显式 `mode='keyword'` 不标降级**（用户要的就是词法，标降级是误报）。
 *   ③ **Rerank 是增强不是依赖**：`meta.rerank_used=false` 表示精排失败但**保留了融合序**，
 *      **不算降级**（`degraded` 仍为 false）。
 *   ④ **权限裁剪要显式提示**：`meta.permission_scope.org_ids` 是服务端推导的有效范围；
 *      请求里的 `filters.org_ids` 只能**收窄**，超出 → `40301`（不静默忽略）。
 *   ⑤ **问答（API-63）全链在 SSE 握手期跑完**：越权（40301）**不能藏在事件流里** ——
 *      所以 `openSse` 把非 2xx 当普通错误处理。
 *   ⑥ **`delta` 是整包拆片、不是 token 级**（已知遗留）；`citation` 里的 `[n]` 编号
 *      来自服务端，**剔除越界编号后若为空则服务端拒答**（`citation_count=0` 的 `done`
 *      属缺陷，前端不渲染裸回答）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

// ============================================================================
// API-62 语义检索
// ============================================================================

export interface SearchFilters {
  /** 只能**收窄**服务端推导的范围；出现越界 ID → 40301。 */
  org_ids?: SnowflakeId[]
  /** ⚠️ 上游不一致：接口文档写数值，实现按**字符串**收。 */
  education_level_min?: string | number
  work_years_min?: number
  tag_codes?: string[]
}

export interface SearchRequest {
  query: string
  top_k?: number
  /** `auto`（默认）/ `vector` / `keyword`。显式 `keyword` 不标降级。 */
  mode?: 'auto' | 'vector' | 'keyword'
  filters?: SearchFilters
  with_reason?: boolean
  rerank?: boolean
}

export interface MatchedChunk {
  chunk_pk: string
  field_name: string | null
  /** 命中片段（**已脱敏**）。 */
  snippet: string | null
  score: number
}

export interface SearchHit {
  talent_id: SnowflakeId
  name: string
  org_id: SnowflakeId | null
  org_name: string | null
  work_years: number | null
  score: number
  /** 命中理由（中文短语）。 */
  hit_reasons: string[]
  matched_chunks: MatchedChunk[]
}

export interface PermissionScope {
  tenant_id: SnowflakeId
  /** `null` 表示不限部门（ADM/HR 全体）。 */
  org_ids: SnowflakeId[] | null
}

export interface SearchMeta {
  mode_used: string
  degraded: boolean
  degraded_reason: string | null
  permission_filter_applied: boolean
  permission_scope: PermissionScope
  candidate_pool_size: number
  /** `false` = 精排失败但保留融合序（**不算降级**）。 */
  rerank_used: boolean
  elapsed_ms: number
}

export interface SearchOut {
  items: SearchHit[]
  meta: SearchMeta
}

// ============================================================================
// API-63 档案 RAG 问答
// ============================================================================

export interface AskScope {
  /** `all` / `orgs` / `talents`。 */
  type: 'all' | 'orgs' | 'talents'
  talent_ids: SnowflakeId[]
}

export interface AskRequest {
  scope: AskScope
  question: string
  /** 会话 ID：**首次不传**，服务端在 `meta` 事件里下发，后续轮次带上（多轮上下文）。 */
  session_id?: string | null
  stream?: boolean
  top_k?: number
}

/** `meta` 事件：会话与权限范围。 */
export interface AskMetaEvent {
  session_id: string
  scope_effective?: {
    talent_ids?: SnowflakeId[]
    clipped?: boolean
    clipped_count?: number
  }
  degraded?: boolean
  degraded_reason?: string | null
}

/** `citation` 事件：一条引用。 */
export interface AskCitationEvent {
  index: number
  talent_id: SnowflakeId
  talent_name?: string
  field_name?: string | null
  snippet?: string | null
  score?: number
}

/** `refusal` 事件：无依据拒答（**确定性判定，不调模型**）。 */
export interface AskRefusalEvent {
  reason: string
  message?: string
}

/** `done` 事件：本轮结束。 */
export interface AskDoneEvent {
  session_id?: string
  citation_count: number
  elapsed_ms?: number
  degraded?: boolean
}

export interface AskSessionOut {
  session_id: string
  items: { role: 'user' | 'assistant'; content: string; created_at?: string }[]
}

export const searchApi = {
  search(payload: SearchRequest) {
    return http.post<SearchOut>('/talent/search', payload)
  },

  /** SSE 路径（POST 方式，交给 `openSse` 使用）。 */
  askPath() {
    return '/talent/ask'
  },

  /** 会话历史回看（API-64）—— ⚠️ **后端尚未实现**（见 §7.3 批次 14 上游不一致 ①）。 */
  session(sessionId: string) {
    return http.get<AskSessionOut>(`/talent/ask/sessions/${sessionId}`)
  },
  /** 清空会话（API-65）—— ⚠️ **后端尚未实现**。 */
  clearSession(sessionId: string) {
    return http.del<{ cleared: boolean }>(`/talent/ask/sessions/${sessionId}`)
  },
  /** 脱敏自检（API-66）—— ⚠️ **后端尚未实现**。 */
  maskVerify(samples: string[]) {
    return http.post<{ total: number; leaked: number; findings: unknown[] }>(
      '/talent/mask/verify',
      { samples },
    )
  },
}

/** API-64 ~ API-66 后端未就绪标记（实测 `openapi.json`，2026-09-23）。 */
export const ASK_SESSION_API_READY = false as const
