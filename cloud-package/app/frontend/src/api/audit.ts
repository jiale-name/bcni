/**
 * E · 审计日志（API-28 / API-29，F-1.5）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-28 审计日志查询 | GET | `/audit-logs` | `audit:view` |
 * | API-29 导出审计日志（**导出行为自身留痕**） | POST | `/audit-logs/export` | `audit:export` |
 *
 * 🔴 三条口径（CU-08 交付时钉死，前端必须如实呈现）：
 *    1. **导出超上限报 `40002` 而非静默截断** —— 前端要把「请缩小时间窗」明确提示给用户，
 *       不得把 `max_rows` 当成「最多给我这么多条」的分页参数；
 *    2. 导出返回的是 `CommonResp` 内嵌 `content` 字符串（CSV），**不是二进制流**；
 *    3. CSV 走**公式注入防护**（`=`/`+`/`-`/`@` 开头会被转义），前端不要二次加工内容。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface AuditLog {
  id: SnowflakeId
  created_at: string | null
  user_id: SnowflakeId | null
  username: string | null
  action: string
  action_name: string | null
  target_type: string | null
  target_name: string | null
  target_id: string | null
  before: unknown
  after: unknown
  ip: string | null
  ua: string | null
  trace_id: string | null
}

export interface AuditLogQuery {
  user_id?: SnowflakeId
  action?: string
  target_type?: string
  target_id?: string
  /** ⚠️ CU-08 起的既有口径：时间参数用**扁平** `start_time` / `end_time`（`YYYY-MM-DD HH:mm:ss`）。 */
  start_time?: string
  end_time?: string
  keyword?: string
  page?: number
  per_page?: number
}

export interface AuditExportRequest {
  user_id?: SnowflakeId
  action?: string
  target_type?: string
  target_id?: string
  start_time?: string
  end_time?: string
  keyword?: string
  /** 仅支持 `csv`。 */
  format?: string
  /** 导出上限；**超出报 40002**，不截断。 */
  max_rows?: number
}

export interface AuditExportOut {
  filename: string
  format: string
  row_count: number
  filters: Record<string, unknown>
  content: string
  generated_at: string | null
}

export const auditApi = {
  list(query: AuditLogQuery) {
    return http.get<AuditLog[]>('/audit-logs', query as Record<string, unknown>)
  },
  export(query: AuditExportRequest) {
    // 导出可能较慢（大时间窗），放宽超时；但仍有 max_rows 防爆上限。
    return http.post<AuditExportOut>('/audit-logs/export', query, undefined, { timeout: 120000 })
  },
}

/** 把 CSV 文本落盘为文件（浏览器端）。 */
export function saveCsv(content: string, filename: string): void {
  // 加 BOM，避免 Excel 打开中文乱码
  const blob = new Blob([`\uFEFF${content}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
