/**
 * B · 组织与用户 —— 组织 / 岗位部分（API-07 ~ API-12）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-07 组织架构树（≥5 级） | GET | `/orgs/tree` | `org:view` |
 * | API-08 新建部门 | POST | `/orgs` | `org:create` |
 * | API-09 修改部门（含移动父节点） | PUT | `/orgs/{org_id}` | `org:update` |
 * | API-10 删除部门（有子部门 / 在职账号时拒绝） | DELETE | `/orgs/{org_id}` | `org:delete` |
 * | API-11 岗位列表 | GET | `/positions` | `position:view` |
 * | API-12 新建岗位 | POST | `/positions` | `position:create` |
 *
 * ⚠️ 响应形状注意：树与列表都收在 `data` **对象**内（`{ total, items }`），
 *    与「`data` 是数组 + 同级 `page`」的分页壳**不是同一种形状**，不要混用。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface OrgNode {
  id: SnowflakeId
  parent_id: SnowflakeId
  dept_code: string
  dept_name: string
  dept_level: number
  dept_path: string | null
  leader_user_id: SnowflakeId | null
  sort_no: number
  status: number
  status_name: string | null
  children: OrgNode[]
}

export interface OrgTreeOut {
  total: number
  items: OrgNode[]
}

export interface OrgCreateRequest {
  parent_id?: SnowflakeId
  dept_code: string
  dept_name: string
  leader_user_id?: SnowflakeId | null
  sort_no?: number
  status?: number
}

export interface OrgUpdateRequest {
  dept_code?: string
  dept_name?: string
  parent_id?: SnowflakeId | null
  leader_user_id?: SnowflakeId | null
  sort_no?: number
  status?: number
}

export interface OrgDeleteOut {
  dept_id: SnowflakeId
  dept_name: string
  deleted: boolean
  affected_children: number
}

export interface Position {
  id: SnowflakeId
  position_code: string
  position_name: string
  dept_id: SnowflakeId | null
  position_level: number | null
  status: number
  status_name: string | null
}

export interface PositionListOut {
  total: number
  items: Position[]
}

export interface PositionCreateRequest {
  position_code: string
  position_name: string
  dept_id?: SnowflakeId | null
  position_level?: number | null
  status?: number
}

export const orgApi = {
  tree(params: { status?: number; keyword?: string } = {}) {
    return http.get<OrgTreeOut>('/orgs/tree', params)
  },
  create(payload: OrgCreateRequest) {
    return http.post<OrgNode>('/orgs', payload)
  },
  update(orgId: SnowflakeId, payload: OrgUpdateRequest) {
    return http.put<OrgNode>(`/orgs/${orgId}`, payload)
  },
  remove(orgId: SnowflakeId) {
    // 🔴 40904：有子部门或在职账号时后端拒绝（不是 40001）
    return http.del<OrgDeleteOut>(`/orgs/${orgId}`)
  },
  positions(params: { keyword?: string; dept_id?: SnowflakeId } = {}) {
    return http.get<PositionListOut>('/positions', params)
  },
  createPosition(payload: PositionCreateRequest) {
    return http.post<Position>('/positions', payload)
  },
}
