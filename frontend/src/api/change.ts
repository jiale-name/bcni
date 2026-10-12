/**
 * M · 变更留痕与版本回溯（API-67 ~ API-70，F-3.10）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-67 变更留痕列表 | GET | `/talent/changes` | `change:list` |
 * | API-68 单档案变更链 | GET | `/talents/{talent_id}/changes` | `change:chain` |
 * | API-69 时间点快照回溯（**纯读**） | GET | `/talents/{talent_id}/snapshot?at=` | `change:snapshot` |
 * | API-70 单字段完整变更链 | GET | `/talents/{talent_id}/fields/{field_code}/history` | `change:field` |
 *
 * 🔴 五条口径（CU-22 交付时钉死）：
 *   ① **快照是「确定性重放」不是备份** —— `snapshot.talent` 里的值由日志链回放得出，
 *      **未追踪字段按声明处理**（`untracked_fields`）、**脏历史值降级为 `null`**（不抛错）。
 *      因此页面必须能渲染 `null` 值，并显示「未追踪」字段清单。
 *   ② **`at` 早于建档时间 → `40001`**（不是 40401）—— 回溯是非法参数，不是「找不到」。
 *   ③ **看快照本身是敏感动作**：后端会写审计 `CHANGE_SNAPSHOT_VIEW`（谁在何时看了谁的历史）
 *      —— 前端不需要额外留痕，但 UI 应提示「本次查看已留痕」。
 *   ④ **`history_truncated=true` 表示触达扫描上限**（`SNAPSHOT_LOG_LIMIT=5000` /
 *      `FIELD_HISTORY_LIMIT=1000`）—— **按上限截断且可识别**，**不是静默截断**；
 *      页面必须显示「历史过长，仅回溯最近 N 条」。
 *   ⑤ **时间窗口超限 → 40001**（`test_change_filter_rejects_oversized_window`），
 *      **start > end → 40001**（不是空结果）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface ChangeLogItem {
  id: SnowflakeId
  talent_id: SnowflakeId
  talent_name: string | null
  change_source: number
  change_source_name: string | null
  /** 字段名（英文 code）与中文标签同时下发。 */
  field_name: string | null
  field_label: string | null
  before_value: string | null
  after_value: string | null
  /** 合并快照引用（回滚 API-61 的依据）。 */
  snapshot_ref: string | null
  operator_id: SnowflakeId | null
  operator_name: string | null
  task_id: SnowflakeId | null
  created_at: string | null
}

export interface ChangeLogQuery {
  page?: number
  per_page?: number
  talent_id?: SnowflakeId
  /** ⚠️ 传的是**字段名（英文 code）**，不是中文标签；未知字段名 → `40001`。 */
  field_name?: string
  change_source?: number
  operator_id?: SnowflakeId
  task_id?: SnowflakeId
  /** `YYYY-MM-DD HH:mm:ss`；窗口超限 → 40001。 */
  start_time?: string
  end_time?: string
}

/** API-69 快照出参。 */
export interface SnapshotTalent {
  id: SnowflakeId
  name: string | null
  gender: number | null
  birth_date: string | null
  education_level: string | null
  school: string | null
  major: string | null
  work_years: number | null
  data_status: number | null
  data_status_name: string | null
  version_no: number | null
}

export interface SnapshotTag {
  tag_code: string
  tag_name: string | null
  source: number | null
  source_name: string | null
}

export interface SnapshotOut {
  talent_id: SnowflakeId
  at: string | null
  snapshot_from_change_log_id: SnowflakeId | null
  talent: SnapshotTalent
  tags: SnapshotTag[]
  /** 该快照的**生效区间**（用于在时间轴上画区间）。 */
  effective_from: string | null
  effective_to: string | null
  /** 该时间点之后发生的变更条数。 */
  changes_after: number
  /** 🔴 未被追踪的字段（值按声明处理，不代表 `null` 就是「当时为空」）。 */
  untracked_fields: string[]
  restored_fields: string[]
  /** 触达扫描上限（**可识别**，非静默截断）。 */
  history_truncated: boolean
}

export interface FieldHistoryOut {
  talent_id: SnowflakeId
  talent_name: string | null
  field_code: string
  field_label: string | null
  current_value: string | null
  total: number
  items: ChangeLogItem[]
}

export const changeApi = {
  list(query: ChangeLogQuery) {
    return http.get<ChangeLogItem[]>('/talent/changes', query as Record<string, unknown>)
  },
  chain(talentId: SnowflakeId, query: Omit<ChangeLogQuery, 'talent_id'> = {}) {
    return http.get<ChangeLogItem[]>(
      `/talents/${talentId}/changes`,
      query as Record<string, unknown>,
    )
  },
  /**
   * 时间点快照回溯。
   * @param at `YYYY-MM-DD HH:mm:ss`；**必须 ≥ 建档时间**，否则 `40001`。
   */
  snapshot(talentId: SnowflakeId, at: string, fieldCodes?: string) {
    return http.get<SnapshotOut>(`/talents/${talentId}/snapshot`, {
      at,
      field_codes: fieldCodes,
    })
  },
  fieldHistory(talentId: SnowflakeId, fieldCode: string) {
    return http.get<FieldHistoryOut>(
      `/talents/${talentId}/fields/${fieldCode}/history`,
    )
  },
}
