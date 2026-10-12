/**
 * J · 查重与合并（API-57 ~ API-61，F-3.5 / F-3.10）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-57 重复候选对列表（相似度降序） | GET | `/talent/dedup` | `dedup:list` |
 * | API-58 并排对比 | GET | `/talent/dedup/{pair_id}` | `dedup:compare` |
 * | API-59 一键合并 | POST | `/talent/dedup/{pair_id}/merge` | `dedup:merge` |
 * | API-60 忽略候选对 | POST | `/talent/dedup/{pair_id}/ignore` | `dedup:ignore` |
 * | API-61 合并回滚 | POST | `/talent/dedup/merges/{merge_id}/rollback` | `dedup:rollback` |
 *
 * 🔴 四条口径：
 *   ① **合并幂等**：同一对重复合并 → `40901`（不是 40903）。
 *   ② **回滚依赖快照**：快照缺失 → **`42203`**，后端**绝不做尽力还原**。
 *      前端必须把 42203 渲染成「无法回滚（缺少合并快照）」而不是通用错误。
 *   ③ **回滚用 `merge_id` 而非 `pair_id`** —— 两者不同（一次合并 = 一条 merge 记录）。
 *   ④ **`field_strategy` 未指定的字段走「非空优先」**（字段级「高版本优先」未建模，
 *      因 TBL-12~15 履历明细表未落地，属已登记遗留）。
 *   ⑤ **联系方式在候选对里是脱敏的**（`phone_masked` / `email_masked`），
 *      合并决策**不依赖明文** —— 前端不得请求明文来做「人工核对」。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface TalentBrief {
  talent_id: SnowflakeId
  name: string
  school: string | null
  major: string | null
  education_level: string | null
  work_years: number | null
  phone_masked: string | null
  email_masked: string | null
  tags: string[]
}

export interface DupCandidate {
  pair_id: SnowflakeId
  talent_a: TalentBrief
  talent_b: TalentBrief
  /** 相似度（0~1）。精确键豁免阈值时为 `null`。 */
  similarity: number | null
  /** 命中字段（中文名列表），用于解释「为什么判为重复」。 */
  match_fields: string[]
  status: number
  status_name: string | null
}

export interface DedupMergeRequest {
  /** 保留的主档 ID；另一档合并后软删。 */
  master_id: SnowflakeId
  /** `{字段名: 'master'|'slave'}`；**未指定字段按「非空优先」**。 */
  field_strategy?: Record<string, 'master' | 'slave'>
  merge_tags?: boolean
  merge_detail?: boolean
}

export interface DedupMergeOut {
  pair_id: SnowflakeId
  master_id: SnowflakeId
  slave_id: SnowflakeId
  /** 合并后主档是否已重索引向量；无向量时为 `false`（**不是失败**）。 */
  vector_reindexed: boolean
  merged_at: string | null
}

export interface DedupIgnoreOut {
  pair_id: SnowflakeId
  status: number
  status_name: string | null
}

export interface DedupRollbackOut {
  merge_id: SnowflakeId
  master_id: SnowflakeId
  slave_id: SnowflakeId
  rolled_back_at: string | null
}

export const dedupApi = {
  list(params: { status?: number; page?: number; per_page?: number } = {}) {
    return http.get<DupCandidate[]>('/talent/dedup', params as Record<string, unknown>)
  },
  compare(pairId: SnowflakeId) {
    return http.get<DupCandidate>(`/talent/dedup/${pairId}`)
  },
  merge(pairId: SnowflakeId, payload: DedupMergeRequest) {
    return http.post<DedupMergeOut>(`/talent/dedup/${pairId}/merge`, payload)
  },
  ignore(pairId: SnowflakeId) {
    return http.post<DedupIgnoreOut>(`/talent/dedup/${pairId}/ignore`)
  },
  /** 回滚合并（依赖 `merge_id`；缺快照 → 42203）。 */
  rollback(mergeId: SnowflakeId) {
    return http.post<DedupRollbackOut>(`/talent/dedup/merges/${mergeId}/rollback`, {}, undefined, {
      idempotencyKey: true,
    })
  },
}
