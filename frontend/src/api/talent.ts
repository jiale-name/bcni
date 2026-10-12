/**
 * F · 人才档案（API-30 ~ API-35，F-3.2 / F-3.4）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | ⭐API-30 档案列表（分页 + 多条件筛选） | GET | `/talents` | ADM / HR / MGR（本部门） |
 * | ⭐API-31 档案详情（字段级裁剪） | GET | `/talents/{id}` | ADM / HR / MGR / EMP（本人） |
 * | API-32 编辑档案（写变更留痕） | PUT | `/talents/{id}` | ADM / HR |
 * | API-33 归档档案（软删 + 向量同步清理） | DELETE | `/talents/{id}` | ADM / HR |
 * | API-34 数字画像（标签 + 向量摘要） | GET | `/talents/{id}/profile` | ADM / HR / MGR / EMP（本人） |
 * | API-35 导出档案列表（留痕） | POST | `/talents/export` | ADM / HR |
 *
 * ============================================================================
 * 🔴 未就绪声明（**CU-23 交付时后端尚无这些端点**）
 * ----------------------------------------------------------------------------
 * 台账 §7.1 声称「一期 F-3 后端功能面全部交付」，但**实测 `openapi.json` 里没有
 * 这 6 个端点**：`/talents`（列表）、`/talents/{id}`（详情）、`/talents/{id}/profile`、
 * `/talents/export` 全部不存在，`PUT` / `DELETE /talents/{id}` 亦无。
 * 根因：§7.1 的 CU-01 ~ CU-28 **没有任何一个 CU 认领 API-30 ~ API-35**（详见 §7.3 批次 14
 * 「上游不一致登记 ①」）。
 *
 * 本文件**按接口设计文档 §2.2 的既定契约实现**（路径 / 参数 / 形状逐条对齐），
 * 因此后端补齐 CU 后前端**无需改动即可点亮**；在此之前：
 *   - 页面会收到 `40401`（资源不存在）或 HTTP 404 → 渲染**空态**，不会白屏；
 *   - 页面顶部显示「接口未就绪」提示条，避免被误读为「前端做错了」。
 * ============================================================================
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface TalentTopTag {
  tag_code: string
  tag_name: string
}

/** API-30 列表行。 */
export interface TalentListItem {
  id: SnowflakeId
  name: string
  gender: number | null
  gender_name: string | null
  org_id: SnowflakeId | null
  org_name: string | null
  education_level: number | null
  education_level_name: string | null
  work_years: number | null
  /** 最多 5 个，按权重降序；完整标签走 `GET /talents/{id}/tags`。 */
  top_tags: TalentTopTag[]
  data_status: number
  data_status_name: string | null
  updated_at: string | null
}

export interface TalentListQuery {
  /** 姓名模糊匹配（**不做全文检索**；语义检索走 API-62）。 */
  keyword?: string
  /** 逗号分隔 ≤50 个；**必须是本部门子树子集**，超出 → 40301（不静默忽略）。 */
  'filters.org_ids'?: string
  /** 字典 `education_level`；0 = 不限。 */
  'filters.education_level'?: number
  'filters.work_years_min'?: number
  'filters.work_years_max'?: number
  /** 逗号分隔 ≤10 个；**只能取启用项**。 */
  'filters.tag_codes'?: string
  /** 默认仅 `1 有效`；查 `2/4` 需显式传参且仅 ADM/HR 可用。 */
  'filters.data_status'?: number
  /** 白名单：`updated_at` / `created_at` / `work_years`；前缀 `-` 表示倒序。默认 `-updated_at`。 */
  sort?: string
  page?: number
  per_page?: number
}

/** API-31 详情中的已建模履历明细（TBL-12/TBL-13）。 */
export interface WorkExp {
  id: SnowflakeId
  company: string
  dept_name: string | null
  title: string | null
  start_date: string | null
  end_date: string | null
  is_current: boolean
  description: string | null
}

export interface ProjectExp {
  id: SnowflakeId
  project_name: string
  role: string | null
  start_date: string | null
  end_date: string | null
  description: string | null
  tech_stack: string[]
}

export interface SkillItem {
  id: SnowflakeId
  skill_name: string
  proficiency: number | null
  proficiency_name: string | null
}

export interface CertItem {
  id: SnowflakeId
  cert_name: string
  issue_date: string | null
}

/** API-31 详情。 */
export interface TalentDetail {
  id: SnowflakeId
  name: string
  gender: number | null
  gender_name: string | null
  birth_month: string | null
  education_level: number | null
  education_level_name: string | null
  school: string | null
  major: string | null
  work_years: number | null
  /** 身份证：**响应侧即已脱敏**（`3301**********1234`），前端不再二次加工。 */
  id_card: string | null
  phone: string | null
  email: string | null
  /** 当前角色是否可见明文敏感字段（决定是否渲染「🔒 已脱敏」标识）。 */
  sensitive_visible: boolean
  org_id: SnowflakeId | null
  org_name: string | null
  data_status: number
  data_status_name: string | null
  version_no: number
  work_exps: WorkExp[]
  project_exps: ProjectExp[]
  skills: SkillItem[]
  certs: CertItem[]
  updated_at: string | null
}

/** API-32 编辑请求（**只提交发生变化的字段**，后端据此写变更留痕）。 */
export interface TalentUpdateRequest {
  name?: string
  gender?: number
  birth_month?: string
  education_level?: number
  school?: string
  major?: string
  work_years?: number
  phone?: string
  email?: string
  org_id?: SnowflakeId
}

/** API-34 数字画像。 */
export interface TalentProfile {
  talent_id: SnowflakeId
  /** 向量摘要（未做真实向量化时为 `null`，页面须能渲染「暂无向量」）。 */
  vector_summary: string | null
  tags: { tag_code: string; tag_name: string; source: number | null; source_name: string | null }[]
  /** 画像维度得分，供雷达图渲染。 */
  dimensions: { code: string; name: string; score: number }[]
  version_no: number | null
  updated_at: string | null
}

/** API-35 导出请求 / 响应。 */
export interface TalentExportRequest {
  keyword?: string
  'filters.org_ids'?: string
  'filters.education_level'?: number
  'filters.work_years_min'?: number
  'filters.work_years_max'?: number
  'filters.tag_codes'?: string
  'filters.data_status'?: number
  sort?: string
  format?: 'csv' | 'xlsx'
  max_rows?: number
}

export interface TalentExportOut {
  filename: string
  format: string
  row_count: number
  content: string
  generated_at: string | null
}

/**
 * 后端就绪开关（**一个常量，三处页面级的提示条都挂在它上面**）。
 *
 * 🔁 **2026-09-23（批次 16 / CU-29）由 `false` 翻为 `true`**：
 * 该值原先来自「实测 `openapi.json` 中这 9 条端点不存在」—— CU-29 已把
 * API-30~35 / API-64~66 全部落地（`/api/v1/talents` 等 6 组路径，`acceptance_check`
 * 的 S-09 已由「应不存在」反转为「必须持续存在」的回归守卫），故此处点亮。
 *
 * ⚠️ 为什么**保留这个常量而不是删掉提示条**：它现在是一个**命名清晰的 kill-switch**。
 * 若将来这些端点被误删（`routers/__init__.py` 漏一行 `include_router` 就够），
 * 把这里翻回 `false` 即可让三个页面立刻显示「后端未就绪」而不是让用户看到 404。
 */
export const TALENT_MASTER_API_READY = true as const

/** 页面级提示文案（`TALENT_MASTER_API_READY = false` 时才渲染）。 */
export const TALENT_MASTER_API_NOTICE =
  '档案主档接口（API-30 ~ API-35 / API-64 ~ API-66）后端未就绪：' +
  '本页已按《接口设计文档》§2.2 的既定契约完成对接，后端补齐后无需改动前端即可点亮。'

export const talentApi = {
  list(query: TalentListQuery) {
    return http.get<TalentListItem[]>('/talents', query as Record<string, unknown>)
  },
  detail(talentId: SnowflakeId, withDetail = true) {
    return http.get<TalentDetail>(`/talents/${talentId}`, { with_detail: withDetail })
  },
  /** 编辑档案 —— 后端会**写变更留痕**（TBL-22），因此不需要前端额外记日志。 */
  update(talentId: SnowflakeId, payload: TalentUpdateRequest) {
    return http.put<TalentDetail>(`/talents/${talentId}`, payload)
  },
  /** 归档（软删 + 向量同步清理）。不可逆动作 → 走强制幂等。 */
  archive(talentId: SnowflakeId) {
    return http.del<{ talent_id: SnowflakeId; archived: boolean; vector_cleaned: boolean }>(
      `/talents/${talentId}`,
      undefined,
      { idempotencyKey: true },
    )
  },
  profile(talentId: SnowflakeId) {
    return http.get<TalentProfile>(`/talents/${talentId}/profile`)
  },
  /** 导出 —— 导出行为**自身留痕**（类似 API-29）。 */
  export(payload: TalentExportRequest) {
    return http.post<TalentExportOut>('/talents/export', payload, undefined, { timeout: 120000 })
  },
}
