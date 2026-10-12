/**
 * N · 个人权利响应（API-71 ~ API-80，F-3.11）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-71 查阅本人档案 | GET | `/privacy/me/profile` | `privacy:self_view` |
 * | API-72 提交更正申请 | POST | `/privacy/corrections` | `privacy:correct` |
 * | API-73 更正申请列表 | GET | `/privacy/corrections` | `privacy:list` |
 * | API-74 处理更正申请 | POST | `/privacy/corrections/{id}/resolve` | `privacy:correct` |
 * | API-75 提交删除申请 | POST | `/privacy/deletions` | `privacy:delete` |
 * | API-76 删除申请列表 | GET | `/privacy/deletions` | `privacy:list` |
 * | API-77 审批删除（**强制幂等**） | POST | `/privacy/deletions/{id}/approve` | `privacy:approve` |
 * | API-78 提交导出申请（**提交即完成**） | POST | `/privacy/exports` | `privacy:export` |
 * | API-79 下载个人数据包 | GET | `/privacy/exports/{id}/download` | `privacy:export` |
 * | API-80 权利请求统一台账 | GET | `/privacy/requests` | `privacy:list` |
 *
 * 🔴 八条口径（CU-22 交付时钉死）：
 *   ① **可更正字段白名单由后端下发**（`SelfProfileOut.correction_field_codes`）——
 *      前端**不得自建白名单**（排除派生字段与唯一键字段的规则在后端）。
 *   ② **无实际差异的更正不该产生请求**（`test_correction_request_rejects_same_value`）——
 *      `current_value` 与 `expected_value` 相同时前端就要拦住并提示。
 *   ③ **`current_value` 是乐观锁**：与库中最新值不一致 → `40902`（防「基于旧值覆盖刚修好的值」）。
 *   ④ **驳回必须给理由**（`test_reject_requires_reason`）。
 *   ⑤ **SM-3 内部态不外露**：界面只会看到 `0 待处理 / 1 已通过 / 2 已驳回`。
 *   ⑥ **API-77 是跨库五步编排**（先删向量、后软删 MySQL，与 SQ-4 顺序**刻意相反**）——
 *      前端只呈现结果：`talent_soft_deleted` / `vector_cleaned` / `vector_degraded`。
 *   ⑦ **API-78 提交即 `status=1`（完成）** —— 接口集没有「审批导出」这一步；
 *      因此导出**处理时长恒为 0**，会拉低跨类型均值 —— 台账页用 `summary.by_type` **分类型**看。
 *   ⑧ **API-79 下载留痕**（`download_count` 自增）；PDF **明确不支持**（不谎称）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

// ============================================================================
// API-71 本人档案
// ============================================================================

export interface SelfField {
  field_code: string
  field_label: string | null
  value: string | null
  /** 来源 0 AI 解析 / 1 人工 / 2 合并 / 3 重解析。 */
  source: number | null
  source_name: string | null
  confidence: number | null
  review_status: number | null
  review_status_name: string | null
  /** 对应复核队列条目 ID（提交更正时复用同一队列）。 */
  review_field_id: SnowflakeId | null
  source_file: string | null
}

export interface SelfTag {
  tag_code: string
  tag_name: string | null
  source: number | null
  source_name: string | null
}

export interface SelfProfile {
  talent_id: SnowflakeId
  name: string | null
  gender: number | null
  gender_name: string | null
  birth_date: string | null
  education_level: string | null
  school: string | null
  major: string | null
  work_years: number | null
  /** 本人可见明文。 */
  phone: string | null
  email: string | null
  /** 身份证**只下发脱敏形态**（明文不经接口）。 */
  id_card_masked: string | null
  data_status: number | null
  data_status_name: string | null
  version_no: number | null
  fields: SelfField[]
  tags: SelfTag[]
  /** 尚在处理的更正申请 ID（**避免重复提交**）。 */
  pending_corrections: SnowflakeId[]
  /** 🔴 可申请更正的字段白名单（由后端派生，前端直接渲染）。 */
  correction_field_codes: string[]
  /** 被排除字段 → 排除原因（用于「为什么这些字段不能改」的解释）。 */
  correction_excluded_reason: Record<string, string>
}

// ============================================================================
// 更正 / 删除 / 导出
// ============================================================================

export interface CorrectionSubmitRequest {
  talent_id: SnowflakeId
  field_code: string
  current_value: string
  expected_value: string
  reason: string
  attachments?: string[]
}

export interface CorrectionSubmitOut {
  request_id: SnowflakeId
  request_no: string
  request_type: string
  status: number
  status_name: string | null
  /** 分流去向（是否同时进入复核队列）。 */
  review_queue: string
  review_field_id: SnowflakeId | null
  /** 处理时限（**创建时刻快照**，改配置不翻转历史逾期判定）。 */
  sla_hours: number
  submitted_at: string | null
}

export interface CorrectionResolveRequest {
  approved: boolean
  /** **驳回必填**。 */
  reason?: string | null
  /** 采纳时可覆盖为其他值。 */
  applied_value?: string | null
}

export interface CorrectionResolveOut {
  request_id: SnowflakeId
  status: number
  status_name: string | null
  talent_id: SnowflakeId
  field_code: string | null
  applied: boolean
  applied_value: string | null
  change_log_id: SnowflakeId | null
  notified: boolean
  processed_at: string | null
}

export interface DeletionSubmitRequest {
  talent_id: SnowflakeId
  reason: string
}

export interface DeletionSubmitOut {
  request_id: SnowflakeId
  request_no: string
  request_type: string
  status: number
  status_name: string | null
  talent_id: SnowflakeId
  sla_hours: number
  approver_hint: string
  submitted_at: string | null
}

export interface DeletionApproveRequest {
  approved: boolean
  reason?: string | null
}

export interface DeletionApproveOut {
  request_id: SnowflakeId
  status: number
  status_name: string | null
  talent_id: SnowflakeId
  talent_soft_deleted: boolean
  /** 各 collection 的清理结果（**逐个**给出，便于识别「部分清理」）。 */
  vector_cleaned: Record<string, unknown>
  vector_degraded: boolean
  change_log_id: SnowflakeId | null
  notified: boolean
  processed_at: string | null
}

export interface ExportSubmitRequest {
  talent_id: SnowflakeId
  /** 一期支持 `json` / `csv`；**不支持 PDF**。 */
  format?: 'json' | 'csv'
}

export interface ExportSubmitOut {
  request_id: SnowflakeId
  request_no: string
  request_type: string
  status: number
  status_name: string | null
  format: string
  download_url: string
  sla_hours: number
  submitted_at: string | null
}

export interface ExportDownloadOut {
  request_id: SnowflakeId
  talent_id: SnowflakeId
  filename: string
  format: string
  content: string
  download_count: number
  generated_at: string | null
}

// ============================================================================
// 台账
// ============================================================================

export interface PrivacyRequest {
  id: SnowflakeId
  request_no: string
  request_type: number
  request_type_name: string | null
  talent_id: SnowflakeId
  talent_name: string | null
  applicant_user_id: SnowflakeId
  applicant_name: string | null
  field_code: string | null
  field_label: string | null
  current_value: string | null
  expected_value: string | null
  review_field_id: SnowflakeId | null
  reason: string | null
  status: number
  status_name: string | null
  /** 处理时限取「请求时刻快照」。 */
  sla_hours: number
  overdue: boolean
  waited_hours: number | null
  processed_by: SnowflakeId | null
  processed_by_name: string | null
  processed_at: string | null
  reject_reason: string | null
  notified_at: string | null
  download_count: number
  created_at: string | null
}

export interface PrivacyLedgerOut {
  items: PrivacyRequest[]
  /**
   * 聚合统计。`data` 是**对象**（不是数组），故不套分页壳。
   * `by_type` 用于化解「导出处理时长恒 0 拉低均值」的问题（**分类型**看）。
   */
  summary: {
    total?: number
    pending?: number
    approved?: number
    rejected?: number
    overdue?: number
    avg_hours?: number
    by_type?: Record<string, { total: number; avg_hours: number; overdue: number }>
  }
  page?: { total: number; page: number; per_page: number; pages: number }
}

export interface PrivacyListQuery {
  page?: number
  per_page?: number
  request_type?: number
  status?: number
  talent_id?: SnowflakeId
  applicant_user_id?: SnowflakeId
  overdue_only?: boolean
  start_time?: string
  end_time?: string
}

export const privacyApi = {
  /** 本人档案（含**可更正字段白名单**与「为什么不能改」的解释）。 */
  myProfile() {
    return http.get<SelfProfile>('/privacy/me/profile')
  },

  submitCorrection(payload: CorrectionSubmitRequest) {
    return http.post<CorrectionSubmitOut>('/privacy/corrections', payload)
  },
  listCorrections(query: PrivacyListQuery) {
    return http.get<PrivacyRequest[]>('/privacy/corrections', query as Record<string, unknown>)
  },
  resolveCorrection(requestId: SnowflakeId, payload: CorrectionResolveRequest) {
    return http.post<CorrectionResolveOut>(`/privacy/corrections/${requestId}/resolve`, payload)
  },

  submitDeletion(payload: DeletionSubmitRequest) {
    return http.post<DeletionSubmitOut>('/privacy/deletions', payload)
  },
  listDeletions(query: PrivacyListQuery) {
    return http.get<PrivacyRequest[]>('/privacy/deletions', query as Record<string, unknown>)
  },
  /** 审批删除 —— **强制幂等**（不可逆动作；同键重放返回首次结果 + 40903）。 */
  approveDeletion(requestId: SnowflakeId, payload: DeletionApproveRequest) {
    return http.post<DeletionApproveOut>(
      `/privacy/deletions/${requestId}/approve`,
      payload,
      undefined,
      { idempotencyKey: true },
    )
  },

  /** 提交导出 —— **提交即完成**（后端置 `status=1`）。 */
  submitExport(payload: ExportSubmitRequest) {
    return http.post<ExportSubmitOut>('/privacy/exports', payload)
  },
  downloadExport(requestId: SnowflakeId) {
    return http.get<ExportDownloadOut>(`/privacy/exports/${requestId}/download`)
  },

  /** 统一台账（聚合在 `data.summary`，`data` 是对象不是数组）。 */
  ledger(query: PrivacyListQuery) {
    return http.get<PrivacyLedgerOut>('/privacy/requests', query as Record<string, unknown>)
  },
}

/** 一期数据包格式（**明确不含 PDF**，`test_export_format_does_not_claim_pdf` 钉）。 */
export const EXPORT_FORMATS = [
  { value: 'json', label: 'JSON（结构化，含字段来源）' },
  { value: 'csv', label: 'CSV（表格，便于人工核对）' },
] as const
