/**
 * A · 认证与会话（API-01 ~ API-06）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-01 登录 | POST | `/auth/login` | 匿名 |
 * | API-02 刷新 | POST | `/auth/refresh` | 持 refresh_token |
 * | API-03 登出 | POST | `/auth/logout` | 已登录 |
 * | API-04 当前用户与数据域 | GET | `/auth/me` | 已登录 |
 * | API-05 权限快照 | GET | `/auth/permissions` | 已登录 |
 * | API-06 修改本人密码 | PUT | `/auth/password` | 已登录 |
 */

import { http } from './http'
import type { DataScope, RoleCode, SnowflakeId } from './types'

export interface LoginRequest {
  username: string
  password: string
  /** 多租户预留；一期留空取默认租户。 */
  tenant_code?: string
}

export interface UserInfo {
  id: SnowflakeId
  username: string
  real_name: string | null
  tenant_id: SnowflakeId
  org_id: SnowflakeId | null
  org_name: string | null
  /** 角色码列表，如 `['HR']`。 */
  roles: RoleCode[] | string[]
  data_scope: number
  data_scope_name: string | null
}

export interface LoginOut {
  access_token: string
  refresh_token: string
  token_type: string
  /** access_token 剩余有效期（秒）—— **由配置下发，前端不得硬编码**。 */
  expires_in: number
  user: UserInfo
}

export interface RefreshOut {
  access_token: string
  token_type: string
  expires_in: number
}

export interface LogoutOut {
  revoked: boolean
  all_devices: boolean
}

export interface PermissionsOut {
  /** 按钮 / 接口级权限码快照，供 `v-perm` 使用。 */
  perm_codes: string[]
  roles: string[]
  is_admin: boolean
  /** 权限快照版本号，前端据此决定是否重新拉取。 */
  perm_version: number
}

export interface ChangePasswordOut {
  changed: boolean
  /** 旧会话是否已全部失效（需重新登录）。 */
  relogin_required: boolean
}

export const authApi = {
  login(payload: LoginRequest) {
    return http.post<LoginOut>('/auth/login', payload)
  },
  refresh(refreshToken: string) {
    return http.post<RefreshOut>('/auth/refresh', { refresh_token: refreshToken })
  },
  logout(payload: { refresh_token?: string; all_devices?: boolean } = {}) {
    return http.post<LogoutOut>('/auth/logout', payload)
  },
  me() {
    return http.get<UserInfo>('/auth/me')
  },
  permissions() {
    return http.get<PermissionsOut>('/auth/permissions')
  },
  changePassword(payload: { old_password: string; new_password: string }) {
    return http.put<ChangePasswordOut>('/auth/password', payload)
  },
}

/** 数据域展示名（后端已回 `data_scope_name`；此处仅作接口未回时的兜底）。 */
export const DATA_SCOPE_LABEL: Record<number, string> = {
  1: '全部',
  2: '本部门',
  3: '本人',
  4: '仅统计值',
}

export type { DataScope }
