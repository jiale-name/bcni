/**
 * P · Agent 任务与人工介入（API-90 ~ API-95，F-A0.1 ~ F-A0.5）
 *
 * | 接口 | 方法 | 路径 | 权限 |
 * | --- | --- | --- | --- |
 * | API-90 Agent 任务列表 | GET | `/agent/tasks` | `agent:task:list` |
 * | API-91 任务详情 + 图状态 | GET | `/agent/tasks/{task_id}` | `agent:task:detail` |
 * | API-92 步骤回放（输入 / 输出 / 耗时） | GET | `/agent/tasks/{task_id}/steps` | `agent:task:replay` |
 * | API-93 人工介入待办列表 | GET | `/agent/interrupts` | `agent:interrupt:list` |
 * | ⭐API-94 处理人工待办（**强制幂等**） | POST | `/agent/interrupts/{id}/resolve` | `agent:interrupt:resolve` |
 * | API-95 重试失败任务 | POST | `/agent/tasks/{task_id}/retry` | `agent:task:retry` |
 *
 * 🔴 五条口径（CU-13 交付时钉死）：
 *   ① **幂等判定先于状态判定** —— API-94 的顺序是 **先查 `Idempotency-Key`（`40903`）
 *      再判状态（`40902`）**。顺序颠倒会把「**你自己刚才那次已生效**」误报成
 *      「**别人已处理**」，而两者的前端动作**完全相反**：
 *      前者「继续」、后者「刷新后重试」。因此前端**必须**：
 *      - 收 `40903` → 按成功继续（`http.ts` 已兜，`code` 仍为 40903，页面可据此提示「已生效」）；
 *      - 收 `40902` → 提示「已被 {handled_by_name} 处理」并**刷新列表**。
 *   ② **`step_index` 是「全任务递增序号」**（跨 run 连续），不是节点内序号 ——
 *      步骤回放按它排序即真实执行顺序（`「入库」不会排到「OCR」前面`，批次 7 缺陷 ③）。
 *   ③ **`next_node` 首次为 `null`、回放才有值** —— 未恢复执行时不要据它推断「下一步是空」。
 *   ④ **同一 `interrupt_id` 重复提交返回首次结果（`40903`）**；
 *      **不同人并发提交** → 后者 `40902`（回带 `handled_by_name` / `handled_at`）。
 *   ⑤ **`graph_state` 是内部图状态快照**（本批新增节点级检查点写入），
 *      仅 AIOPS/ADM 排查用 —— 页面折叠展示，不作为业务字段。
 */

import { http } from './http'
import type { SnowflakeId } from './types'

export interface AgentTask {
  id: SnowflakeId
  task_type: string
  task_type_name: string | null
  /** 业务对象 ID（如导入任务 ID）。 */
  biz_id: SnowflakeId | null
  status: number
  status_name: string | null
  current_node: string | null
  retry_count: number
  created_at: string | null
  updated_at: string | null
}

export interface AgentTaskDetail extends AgentTask {
  idempotent_key: string | null
  /** 内部图状态（AIOPS/ADM 排查用）。 */
  graph_state: Record<string, unknown> | null
  step_count: number
}

export interface AgentStep {
  id: SnowflakeId
  task_id: SnowflakeId
  node_name: string
  /** 🔴 **全任务递增序号**，按它排序即真实执行顺序。 */
  step_index: number
  input: unknown
  output: unknown
  latency_ms: number | null
  status: number
  status_name: string | null
  error_msg: string | null
  created_at: string | null
}

export interface AgentInterrupt {
  id: SnowflakeId
  task_id: SnowflakeId
  node_name: string
  interrupt_type: string
  interrupt_type_name: string | null
  /** 待办负载（如「文件级复核：置信度过低」的具体上下文）。 */
  payload: Record<string, unknown> | null
  /** 指派角色（决定谁能处理）。 */
  assignee_role: string | null
  status: number
  status_name: string | null
  decided_by: SnowflakeId | null
  decided_by_name: string | null
  decided_at: string | null
  comment: string | null
  created_at: string | null
  updated_at: string | null
}

export interface InterruptResolveRequest {
  /** `approved` / `rejected`。 */
  decision: 'approved' | 'rejected'
  comment?: string | null
  /** 可回传给图的补充负载（如人工指定的合并主档）。 */
  payload?: Record<string, unknown> | null
}

export interface InterruptResolveOut {
  interrupt_id: SnowflakeId
  interrupt_type: string
  status: number
  status_name: string | null
  task_id: SnowflakeId
  resumed: boolean
  next_node: string | null
  task_status: number | null
  task_status_name: string | null
  handled_by: SnowflakeId | null
  handled_by_name: string | null
  handled_at: string | null
}

export interface AgentTaskRetryOut {
  task_id: SnowflakeId
  status: number
  status_name: string | null
  retry_count: number
  current_node: string | null
  resumed: boolean
  message: string | null
}

export const agentApi = {
  tasks(
    params: {
      page?: number
      per_page?: number
      task_type?: string
      status?: number
      biz_id?: SnowflakeId
    } = {},
  ) {
    return http.get<AgentTask[]>('/agent/tasks', params as Record<string, unknown>)
  },
  taskDetail(taskId: SnowflakeId) {
    return http.get<AgentTaskDetail>(`/agent/tasks/${taskId}`)
  },
  steps(taskId: SnowflakeId, params: { page?: number; per_page?: number } = {}) {
    return http.get<AgentStep[]>(`/agent/tasks/${taskId}/steps`, params as Record<string, unknown>)
  },
  /** 重试失败任务（仅 ADM / AIOPS）。 */
  retryTask(taskId: SnowflakeId) {
    return http.post<AgentTaskRetryOut>(`/agent/tasks/${taskId}/retry`, {}, undefined, {
      idempotencyKey: true,
    })
  },
  interrupts(
    params: {
      page?: number
      per_page?: number
      status?: number
      task_id?: SnowflakeId
      interrupt_type?: string
      assignee_role?: string
    } = {},
  ) {
    return http.get<AgentInterrupt[]>('/agent/interrupts', params as Record<string, unknown>)
  },
  /** ⭐ 处理人工待办 —— **强制幂等**（幂等判定先于状态判定）。 */
  resolveInterrupt(interruptId: SnowflakeId, payload: InterruptResolveRequest) {
    return http.post<InterruptResolveOut>(
      `/agent/interrupts/${interruptId}/resolve`,
      payload,
      undefined,
      { idempotencyKey: true },
    )
  },
}

/**
 * Agent 两枚举的**后端 label 目前是英文**（批次 7 遗留）。
 * 前端不在本文件硬编码中文映射，统一由 `api/enums.ts` 的 `statusText()` 处理：
 * 后端给中文就用后端，给英文才兜底。
 */
