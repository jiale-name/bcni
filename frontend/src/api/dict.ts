/**
 * D · 数据字典与系统配置（API-23 ~ API-27）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-23 字典类型列表 | GET | `/dicts` | 仅 ADM（`dict:type:list`） |
 * | API-24 字典项列表 | GET | `/dicts/{type_code}/items` | 已登录（`dict:view`，广授） |
 * | API-25 新增字典项 | POST | `/dicts/{type_code}/items` | `dict:create` |
 * | API-26 修改字典项（留痕） | PUT | `/dict-items/{item_id}` | `dict:update` |
 * | API-27 停用字典项（不物理删） | DELETE | `/dict-items/{item_id}` | `dict:update` |
 *
 * 🔴 两个易混口径：
 *    ① **API-23 仅 ADM**，而 API-24 广授（HR/MGR/EMP/EXEC 都要用它下拉）——
 *       所以业务页面拉下拉走 API-24，**不要**先去拉字典类型列表。
 *    ② **API-26 的留痕落 TBL-28 审计日志**，不落 `talent_change_log`（字典项不是人才数据）。
 *    ③ API-27 是「停用」不是删除：重复停用返回 `already_disabled=true`（幂等，不报错）。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface DictType {
  id: SnowflakeId
  type_code: string
  type_name: string
  remark: string | null
  status: number
  status_name: string | null
  item_count: number
  enabled_item_count: number
}

export interface DictTypeListOut {
  total: number
  items: DictType[]
}

export interface DictItem {
  id: SnowflakeId
  type_code: string
  item_code: string
  item_name: string
  sort_no: number
  parent_code: string | null
  status: number
  status_name: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DictItemListOut {
  total: number
  items: DictItem[]
}

export interface DictItemCreateRequest {
  item_code: string
  item_name: string
  sort_no?: number
  parent_code?: string | null
  status?: number
}

export interface DictItemUpdateRequest {
  item_name?: string
  sort_no?: number
  /** ⚠️ 修改 `parent_code` 会改变层级；后端留痕记录前后值。 */
  parent_code?: string | null
  status?: number
}

export interface DictItemDeleteOut {
  id: SnowflakeId
  type_code: string
  item_code: string
  item_name: string
  status: number
  status_name: string | null
  /** 重复停用时为 `true`（幂等，不报错）。 */
  already_disabled: boolean
}

export const dictApi = {
  types() {
    return http.get<DictTypeListOut>('/dicts')
  },
  items(typeCode: string, params: { status?: number } = {}) {
    return http.get<DictItemListOut>(`/dicts/${typeCode}/items`, params)
  },
  createItem(typeCode: string, payload: DictItemCreateRequest) {
    return http.post<DictItem>(`/dicts/${typeCode}/items`, payload)
  },
  updateItem(itemId: SnowflakeId, payload: DictItemUpdateRequest) {
    return http.put<DictItem>(`/dict-items/${itemId}`, payload)
  },
  disableItem(itemId: SnowflakeId) {
    return http.del<DictItemDeleteOut>(`/dict-items/${itemId}`)
  },
}
