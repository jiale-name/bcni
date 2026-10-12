/**
 * C · 角色与权限（API-18 ~ API-22）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-18 角色列表（预置 + 自定义） | GET | `/roles` | `rbac:role:list` |
 * | API-19 新建自定义角色 | POST | `/roles` | `rbac:role:create` |
 * | API-20 修改角色（含 `data_scope`） | PUT | `/roles/{role_id}` | `rbac:role:update` |
 * | API-21 权限点树（菜单 / 按钮 / 接口） | GET | `/permissions/tree` | `rbac:perm:tree` |
 * | API-22 绑定权限点（全量替换） | PUT | `/roles/{role_id}/permissions` | `rbac:role:bind` |
 *
 * ⚠️ 角色删除接口**决定不开放**（不在接口矩阵内）—— `rbac_dao.soft_delete_role` 保留备询。
 *    因此本模块**没有** `remove()`，前端也不得出现「删除角色」按钮。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface Role {
  id: SnowflakeId
  role_code: string
  role_name: string
  data_scope: number
  data_scope_name: string | null
  is_builtin: boolean
  status: number
  status_name: string | null
}

export interface RoleListOut {
  total: number
  items: Role[]
}

export interface RoleCreateRequest {
  role_code: string
  role_name: string
  data_scope: number
  status?: number
}

export interface RoleUpdateRequest {
  role_name?: string
  data_scope?: number
  status?: number
}

export interface PermissionNode {
  id: SnowflakeId
  perm_code: string
  perm_name: string
  /** 权限类型：菜单 / 按钮 / 接口。 */
  perm_type: number
  perm_type_name: string | null
  parent_id: SnowflakeId
  sort_no: number
  status: number
  children: PermissionNode[]
}

export interface PermissionTreeOut {
  total: number
  items: PermissionNode[]
}

export interface RolePermissionsOut {
  role_id: SnowflakeId
  perm_ids: SnowflakeId[]
  perm_codes: string[]
  /** 受影响用户数 —— 供二次确认弹窗提示「本次变更将影响 N 个账号」。 */
  affected_users: number
  perm_version: number
}

export const rbacApi = {
  roles() {
    return http.get<RoleListOut>('/roles')
  },
  createRole(payload: RoleCreateRequest) {
    return http.post<Role>('/roles', payload)
  },
  updateRole(roleId: SnowflakeId, payload: RoleUpdateRequest) {
    return http.put<Role>(`/roles/${roleId}`, payload)
  },
  permissionTree() {
    return http.get<PermissionTreeOut>('/permissions/tree')
  },
  /** 全量替换该角色的权限点集合。 */
  bindPermissions(roleId: SnowflakeId, permIds: SnowflakeId[]) {
    return http.put<RolePermissionsOut>(`/roles/${roleId}/permissions`, { perm_ids: permIds })
  },
}

/** 数据范围选项（F-1.3）。 */
export const DATA_SCOPE_OPTIONS = [
  { value: 1, label: '全部数据' },
  { value: 2, label: '本部门（含子树）' },
  { value: 3, label: '仅本人' },
  { value: 4, label: '仅统计值（不可见明细）' },
] as const
