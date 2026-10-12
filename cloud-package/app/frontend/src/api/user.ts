/**
 * B · 组织与用户 —— 用户管理部分（API-13 ~ API-17）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-13 用户列表（分页） | GET | `/users` | `user:view` |
 * | API-14 新建用户 | POST | `/users` | `user:create` |
 * | API-15 修改用户（含启用 / 禁用） | PUT | `/users/{user_id}` | `user:update` |
 * | API-16 分配角色（全量替换） | PUT | `/users/{user_id}/roles` | `user:role` |
 * | API-17 批量导入用户（Excel / CSV 模板） | POST | `/users/import` | `user:import` |
 *
 * 🔴 API-13 用的是**分页壳**（`data` 为数组 + 同级 `page`），不是 `{total, items}`。
 */

import { http } from './http'
import type { PageInfo, SnowflakeId } from './types'

export interface AdminUser {
  id: SnowflakeId
  username: string
  real_name: string | null
  org_id: SnowflakeId | null
  org_name: string | null
  position_id: SnowflakeId | null
  position_name: string | null
  phone: string | null
  email: string | null
  status: number
  status_name: string | null
  role_ids: SnowflakeId[]
  roles: string[]
  last_login_at: string | null
  created_at: string | null
}

export interface UserListQuery {
  keyword?: string
  org_id?: SnowflakeId
  status?: number
  role_code?: string
  page?: number
  per_page?: number
}

export interface UserCreateRequest {
  username: string
  /** 留空则后端生成初始口令（响应 `initial_password` 只回一次）。 */
  password?: string
  real_name?: string
  org_id?: SnowflakeId | null
  position_id?: SnowflakeId | null
  phone?: string
  email?: string
  role_ids?: SnowflakeId[]
  status?: number
}

export interface UserUpdateRequest {
  real_name?: string
  org_id?: SnowflakeId | null
  position_id?: SnowflakeId | null
  phone?: string
  email?: string
  status?: number
}

export interface UserCreateOut {
  user: AdminUser
  /** 🔴 仅在创建响应中出现一次；不得落库到前端持久化层（权限与安全）。 */
  initial_password: string | null
}

export interface UserRolesOut {
  user_id: SnowflakeId
  role_ids: SnowflakeId[]
  roles: string[]
}

export interface UserImportError {
  row: number
  username: string | null
  reason: string
}

export interface UserImportOut {
  total: number
  success: number
  failed: number
  errors: UserImportError[]
}

export const userApi = {
  list(query: UserListQuery) {
    return http.get<AdminUser[]>('/users', query as Record<string, unknown>)
  },
  create(payload: UserCreateRequest) {
    return http.post<UserCreateOut>('/users', payload)
  },
  update(userId: SnowflakeId, payload: UserUpdateRequest) {
    return http.put<AdminUser>(`/users/${userId}`, payload)
  },
  /** 全量替换角色（不是增量追加）。 */
  assignRoles(userId: SnowflakeId, roleIds: SnowflakeId[]) {
    return http.put<UserRolesOut>(`/users/${userId}/roles`, { role_ids: roleIds })
  },
  importUsers(file: File, onProgress?: (p: number) => void) {
    const form = new FormData()
    form.append('file', file)
    return http.upload<UserImportOut>('/users/import', form, { onProgress })
  },
}

export type { PageInfo }
