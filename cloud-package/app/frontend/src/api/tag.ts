/**
 * I · 受控标签词表（API-50 ~ API-56，F-3.4 / F-3.4.1）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-50 受控标签词表（三级树 + 平铺） | GET | `/talent/tag-dict?view=tree\|flat` | 已登录 |
 * | API-51 新增标签（管理员直接启用） | POST | `/talent/tag-dict` | `tag:create` |
 * | API-52 修改标签（含启用 / 停用） | PUT | `/talent/tag-dict/{tag_id}` | `tag:update` |
 * | API-53 提交 AI 建议 | POST | `/talent/tag-dict/suggestions` | `tag:suggest` |
 * | API-54 审核 AI 建议 | POST | `/talent/tag-dict/suggestions/{id}/review` | `tag:audit` |
 * | API-55 档案标签查询 | GET | `/talents/{talent_id}/tags` | 已登录 |
 * | API-56 档案标签全量替换 | PUT | `/talents/{talent_id}/tags` | `tag:assign` |
 *
 * 🔴 四条口径：
 *   ① **AI 不得自由造词**：AI 建议一律落 `status=0 待审核`；**只有 `1 启用` 可被打标**，
 *      选择停用项 → `40001`（不是静默过滤）。
 *   ② **API-50 的 `view` 决定响应形状**：`tree` 返回**树数组**（收在 `data`），
 *      `flat` 走**分页壳**（`data` 为数组 + 同级 `page`）。两者不可混用。
 *   ③ **API-56 是全量替换**，不是增量追加 —— 提交的 `tag_codes` 就是最终集合。
 *   ④ **API-52 的 `tag_code` 不可改**（`tag_code` 是业务主键），只允许改名 / 分类 / 同义词 / 状态。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface TagDictItem {
  tag_id: SnowflakeId
  tag_code: string
  tag_name: string
  category_l1: string | null
  category_l2: string | null
  category_l3: string | null
  synonyms: string[]
  status: number
  status_name: string | null
  source: number
  source_name: string | null
  approved_by: SnowflakeId | null
  approved_at: string | null
  created_at: string | null
}

/** 三级分类树节点（`view=tree`）。 */
export interface TagTreeNode {
  category: string
  level: number
  children: TagTreeNode[]
  tags: TagDictItem[]
}

export interface TagCreateRequest {
  /** 留空则由后端生成。 */
  tag_code?: string
  tag_name: string
  category_l1?: string
  category_l2?: string
  category_l3?: string
  synonyms?: string[]
}

export interface TagUpdateRequest {
  tag_name?: string
  category_l1?: string
  category_l2?: string
  category_l3?: string
  synonyms?: string[]
  /** 状态动作（如启用 / 停用），具体取值由后端校验。 */
  action?: string
}

export interface TagSuggestRequest {
  tag_name: string
  category_code: string
  synonyms?: string[]
  sample_talent_ids?: SnowflakeId[]
  reason?: string
}

export interface TagSuggestOut {
  suggestion_id: SnowflakeId
  status: number
  status_name: string | null
  tag_code: string | null
}

export interface TagReviewRequest {
  /** `approve` / `reject`。 */
  decision: 'approve' | 'reject'
  /** 审核通过时可指定最终 `tag_code`。 */
  tag_code?: string
  reject_reason?: string
}

export interface TagReviewOut {
  suggestion_id: SnowflakeId
  status: number
  status_name: string | null
  tag_code: string | null
  activated_at: string | null
}

export interface TalentTag {
  tag_code: string
  tag_name: string
  confidence: number | null
  source: number
  source_name: string | null
  category_l1: string | null
  category_l2: string | null
  category_l3: string | null
}

export interface TalentTagsOut {
  talent_id: SnowflakeId
  tags: TalentTag[]
}

export const tagApi = {
  /** ⚠️ `view=tree` 返回树（`data` 为 `TagTreeNode[]`），`view=flat` 返回分页壳。 */
  tree() {
    return http.get<TagTreeNode[]>('/talent/tag-dict', { view: 'tree' })
  },
  flat(params: { status?: number; keyword?: string; page?: number; per_page?: number } = {}) {
    return http.get<TagDictItem[]>('/talent/tag-dict', {
      view: 'flat',
      ...params,
    } as Record<string, unknown>)
  },
  create(payload: TagCreateRequest) {
    return http.post<TagDictItem>('/talent/tag-dict', payload)
  },
  update(tagId: SnowflakeId, payload: TagUpdateRequest) {
    return http.put<TagDictItem>(`/talent/tag-dict/${tagId}`, payload)
  },
  suggest(payload: TagSuggestRequest) {
    return http.post<TagSuggestOut>('/talent/tag-dict/suggestions', payload)
  },
  reviewSuggestion(suggestionId: SnowflakeId, payload: TagReviewRequest) {
    return http.post<TagReviewOut>(`/talent/tag-dict/suggestions/${suggestionId}/review`, payload)
  },
  talentTags(talentId: SnowflakeId) {
    return http.get<TalentTagsOut>(`/talents/${talentId}/tags`)
  },
  /** 全量替换该档案的标签集合。 */
  assign(talentId: SnowflakeId, tagCodes: string[]) {
    return http.put<TalentTagsOut>(`/talents/${talentId}/tags`, { tag_codes: tagCodes })
  },
}
