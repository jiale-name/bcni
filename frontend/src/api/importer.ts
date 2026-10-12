/**
 * G · 简历导入与数据质量（API-36 ~ API-43，F-3.1 / F-3.8 / F-A0.4）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-36 创建导入任务（multipart，**强制幂等**） | POST | `/talent/import` | `import:create` |
 * | API-37 导入任务列表 | GET | `/talent/import` | `import:view` |
 * | API-38 任务详情 | GET | `/talent/import/{task_id}` | `import:view` |
 * | API-39 SSE 实时进度流 | GET | `/talent/import/{task_id}/progress` | `import:view` |
 * | API-40 文件明细 | GET | `/talent/import/{task_id}/files` | `import:view` |
 * | API-41 数据质量报告 | GET | `/talent/import/{task_id}/report` | `import:report` |
 * | API-42 导出质量报告 | GET | `/talent/import/{task_id}/report/export` | `import:report` |
 * | API-43 失败文件批量重试 | POST | `/talent/import/{task_id}/retry` | `import:retry` |
 *
 * 🔴 三条口径：
 *    ① **API-36 强制幂等**（`Idempotency-Key`）—— 重放返回首次结果 + `40903`，
 *       前端按**成功**处理（`http.ts` 已兜）。用户重复点「开始导入」不会建两个任务。
 *    ② **API-39 是 SSE** —— 且**任务不存在 / 越权在握手期就返回**（不藏在事件流里，CR-8 口径）。
 *       事件名：`progress`（进行中）/ `done`（终态）/ `error`。终态后服务端主动关流。
 *    ③ 上传上限（≤100 份 / ≤50MB / 扩展名 / 批内去重）由**后端**校验，
 *       前端只做**前置提示**（体验），**不得**把它当成唯一防线（`41301` / `41501` 必须能正常渲染）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface ImportCreateOut {
  task_id: SnowflakeId
  task_no: string
  status: number
  status_name: string | null
  total_count: number
  /** 服务端给出的**非阻断**告警（如「本批含 X 份重复文件，已自动去重」）。 */
  warn: string | null
}

export interface ImportTask {
  task_id: SnowflakeId
  task_no: string
  status: number
  status_name: string | null
  parse_strategy: string | null
  total_count: number
  success_count: number
  fail_count: number
  dup_pair_count: number
  review_pending_count: number
  review_high_impact_count: number
  review_low_impact_sampled_count: number
  field_complete_rate: number | null
  progress_pct: number
  agent_task_id: SnowflakeId | null
  remark: string | null
  created_by: SnowflakeId | null
  created_by_name: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface ImportFileItem {
  file_id: SnowflakeId
  resume_id: number
  file_name: string
  file_ext: string | null
  file_size: number
  file_hash: string | null
  parse_status: number
  parse_status_name: string | null
  parse_error: string | null
  talent_id: SnowflakeId | null
  agent_task_id: SnowflakeId | null
  /** 是否走过 OCR（一期 OCR 为占位实现，图片件会转人工介入）。 */
  ocr_used: boolean
  created_at: string | null
}

export interface FailureReason {
  reason_code: string
  reason_name: string
  count: number
}

export interface FieldCompleteness {
  field_code: string
  field_name: string
  filled: number
  total: number
  rate: number
}

export interface ImportReport {
  task_id: SnowflakeId
  task_no: string
  summary: Record<string, unknown>
  field_completeness: FieldCompleteness[]
  failure_reasons: FailureReason[]
  /** 🔴 准确率口径说明 —— 金标准集未定稿前，此处的口径**不代表验收值**。 */
  accuracy_note: string
  generated_at: string
}

export interface ImportReportExportOut {
  filename: string
  content: string
  row_count: number
  generated_at: string
}

export interface ImportRetryOut {
  task_id: SnowflakeId
  retry_count: number
  retryable_count: number
  message: string | null
}

/** API-39 SSE 事件负载（`ImportProgressEvent`）。 */
export interface ImportProgressEvent {
  task_id: string
  status: number
  status_name: string | null
  done: number
  total: number
  progress_pct: number
}

export interface ImportCreateForm {
  /** 一次最多 100 份。 */
  files: File[]
  auto_dedup: boolean
  auto_tag: boolean
  /** `auto` | `ocr_force` | `text_only`。 */
  parse_strategy: 'auto' | 'ocr_force' | 'text_only'
  remark?: string
}

export const importApi = {
  /** 创建导入任务。**强制幂等**：同一 `Idempotency-Key` 重放返回首次结果。 */
  create(form: ImportCreateForm, onProgress?: (percent: number) => void) {
    const fd = new FormData()
    for (const f of form.files) fd.append('files', f)
    fd.append('auto_dedup', String(form.auto_dedup))
    fd.append('auto_tag', String(form.auto_tag))
    fd.append('parse_strategy', form.parse_strategy)
    if (form.remark) fd.append('remark', form.remark)
    return http.upload<ImportCreateOut>('/talent/import', fd, {
      idempotencyKey: true,
      onProgress,
    })
  },

  list(params: { status?: number; page?: number; per_page?: number } = {}) {
    return http.get<ImportTask[]>('/talent/import', params as Record<string, unknown>)
  },

  detail(taskId: SnowflakeId) {
    return http.get<ImportTask>(`/talent/import/${taskId}`)
  },

  /** SSE 路径（交给 `openSse` 使用）。 */
  progressPath(taskId: SnowflakeId, interval = 1) {
    return `/talent/import/${taskId}/progress?interval=${interval}`
  },

  files(
    taskId: SnowflakeId,
    params: { parse_status?: number; page?: number; per_page?: number } = {},
  ) {
    return http.get<ImportFileItem[]>(
      `/talent/import/${taskId}/files`,
      params as Record<string, unknown>,
    )
  },

  report(taskId: SnowflakeId) {
    return http.get<ImportReport>(`/talent/import/${taskId}/report`)
  },

  exportReport(taskId: SnowflakeId) {
    return http.get<ImportReportExportOut>(`/talent/import/${taskId}/report/export`)
  },

  retry(taskId: SnowflakeId) {
    return http.post<ImportRetryOut>(`/talent/import/${taskId}/retry`)
  },
}

/** 前端**前置提示**用的限制（真正的防线在后端；见本模块 docstring 口径 ③）。 */
export const IMPORT_LIMITS = {
  MAX_FILES: 100,
  MAX_FILE_SIZE_MB: 50,
  ALLOWED_EXT: ['.pdf', '.doc', '.docx', '.txt', '.md', '.png', '.jpg', '.jpeg'] as const,
}
