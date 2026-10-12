/**
 * 全局契约类型（对接口设计文档 §1.1 / §1.2 / §1.5 的**逐字**落地）。
 *
 * 🔴 本文件承载三条前端最容易写错、且后果最严重的约定：
 *    1. **雪花 ID 一律 `string`** —— 见 `SnowflakeId`；
 *    2. **`page` 与 `data` 同级** —— 见 `CommonResp` / `ApiResult`；
 *    3. **成功判定是 `code === 0`，不是 HTTP 200** —— 见 `ErrorCode` / `isDegraded`。
 */

// ============================================================================
// 1. 雪花 ID（接口 §1.2 —— 本项目最强约束）
// ============================================================================

/**
 * 雪花 ID：**必须是字符串**。
 *
 * 主键为 int64 雪花 ID，可达 1.8e19，**远超** JS `Number.MAX_SAFE_INTEGER`（9.007e15）。
 * 一旦被当作 number 经手（含 `JSON.parse` 前的隐式转换、`parseInt`、`+id`、`[id].sort()`），
 * 末位会**静默截断**，得到的 ID 与真实 ID 不一致 → 用该 ID 请求详情**必然 404**。
 *
 * 同类坑已在同项目的合同要素接口上真实发生过（排查耗时 > 1 天），故此处用**类型别名**把
 * 约束写进类型系统：任何 `number` 都无法赋给 `SnowflakeId`。
 *
 * ❌ 禁止：`Number(talent.id)` / `parseInt(id)` / `id * 1`
 * ✅ 允许：`String(id)` / 直接透传 / `id === otherId`
 */
export type SnowflakeId = string

/** 时间字段：后端统一下发 `YYYY-MM-DD HH:mm:ss`（**不是 ISO-8601，没有 `T`**）。 */
export type DateTimeStr = string

/** 日期字段：`YYYY-MM-DD`。 */
export type DateStr = string

// ============================================================================
// 2. 统一响应壳（接口 §1.1）
// ============================================================================

/** 分页信息。**与 `data` 同级**，不得塞进 `data`。 */
export interface PageInfo {
  total: number
  page: number
  per_page: number
  pages: number
}

/** 统一响应体：`{ code, message, data, page, trace_id }`。 */
export interface CommonResp<T> {
  code: number
  message: string
  data: T | null
  /** 🔴 与 `data` 同级 —— 分页表格必须同时读 `data` 与 `page`。 */
  page: PageInfo | null
  trace_id: string | null
}

/**
 * 部分端点（复核队列 / AI 调用日志）在响应**顶层**追加聚合字段。
 *
 * ⚠️ 这是**线缆形状**（`CommonResp` 原样）—— 调用方拿到的是 `HttpClient` 解析后的
 * `ApiResult<T, E>`，聚合字段在 **`result.extra`**（不是 `result.data.meta`）。
 */
export interface CommonRespExtra<T, E> extends CommonResp<T> {
  meta?: E
  summary?: E
}


/**
 * `request()` 的返回值：**已解析**的响应。
 *
 * 为什么不直接返回 `data`？因为 `page` / `degraded` / `trace_id` 都是调用方必须看到的信息，
 * 若只返回 `data`，分页表格就得自己再拼一次分页状态 —— 这是本项目的高频错点
 * （批次 13 缺陷 ②：误按 `paginated(items, total, current, size)` 调用，根因就是**响应壳形状没被强制**）。
 *
 * @typeParam T `data` 的类型
 * @typeParam E 该端点**顶层**附加聚合字段的类型（绝大多数端点为 `never`）。
 *   只有「复核队列」（`meta`）与「AI 调用日志」（`summary`）两类端点用到，
 *   见 `CommonRespExtra`。
 */
export interface ApiResult<T, E = never> {
  /** 业务数据；无数据时为 `null`。 */
  data: T
  /** 分页信息；非分页接口为 `null`。 */
  page: PageInfo | null
  /** 实际业务码。`0` 为成功；`40903` 表示幂等回放（**按成功处理**）。 */
  code: number
  message: string
  /** 降级标记（接口 §1.5：HTTP 200 + `data.degraded=true`）。 */
  degraded: boolean
  degradedReason: string | null
  /** 全链路追踪号；错误态必须展示给用户（便于上报）。 */
  traceId: string | null
  /**
   * 响应**顶层**（与 `page` 同级）的附加聚合字段：`meta`（复核队列）/ `summary`（AI 调用日志）。
   * 其余端点为 `null`。
   *
   * 🔴 **它不在 `data` 内** —— 写成 `data.meta` 会拿到 `undefined`（批次 13 缺陷 ⑥ 的同类形状）。
   *    `meta.warning` 这类「非阻断提示」一旦读不到，页面就永远不会提示用户
   *    「低影响字段是抽样下发的」，而这恰恰是复核工作台最需要的口径。
   */
  extra: E | null
}


// ============================================================================
// 3. 错误码（接口 §1.5）
// ============================================================================

export const ErrorCode = {
  OK: 0,
  /** 参数校验失败（`data.fields[]` 指明字段） */
  PARAM_INVALID: 40001,
  /** 参数超出业务上限（`per_page>100`、批量确认 >200 条） */
  PARAM_OVER_LIMIT: 40002,
  /** 未登录 / Token 过期 → 静默 refresh */
  UNAUTHENTICATED: 40101,
  /** 账号或密码错误 */
  BAD_CREDENTIALS: 40102,
  /** 账号被锁定（`data.locked_seconds`） */
  ACCOUNT_LOCKED: 40103,
  /** 无权限（含跨租户 / 跨部门越权）—— **不区分不存在与无权限** */
  FORBIDDEN: 40301,
  /** 资源不存在（含已软删） */
  NOT_FOUND: 40401,
  /** 唯一约束冲突 */
  CONFLICT_UNIQUE: 40901,
  /** 并发状态冲突（已被他人处理）→ 刷新后重试 */
  CONFLICT_STATE: 40902,
  /** 幂等键冲突（返回首次结果）→ **按成功处理** */
  IDEMPOTENT_REPLAY: 40903,
  /** 依赖前置未满足 */
  DEPENDENCY_UNMET: 40904,
  /** 上传文件超限 */
  UPLOAD_TOO_LARGE: 41301,
  /** 文件格式不支持 */
  UNSUPPORTED_FORMAT: 41501,
  /** 状态机非法流转 */
  ILLEGAL_TRANSITION: 42201,
  /** 缺少合并快照，无法回滚（批次 11 新增） */
  MISSING_SNAPSHOT: 42203,
  /** 触发限流（按 `Retry-After` 退避） */
  RATE_LIMITED: 42901,
  /** 服务内部错误 */
  INTERNAL: 50000,
  /** 数据库错误 */
  DB_ERROR: 50001,
  /** 对象存储异常 */
  OBJECT_STORAGE_ERROR: 50002,
  /** AI / 检索服务降级（HTTP 200 + `degraded`） */
  AI_DEGRADED: 50301,
  /** 向量检索降级为关键词模式 */
  VECTOR_DEGRADED: 50302,
  /** 模型调用超时 */
  MODEL_TIMEOUT: 50401,
  /** OCR 服务超时 */
  OCR_TIMEOUT: 50402,
  /** 前端本地：网络不可达 / 请求被取消 */
  NETWORK: -1,
} as const

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode]

/** 降级码集合：HTTP 200 但业务上「服务能力降级」，**不得弹错误框**。 */
export const DEGRADED_CODES: readonly number[] = [ErrorCode.AI_DEGRADED, ErrorCode.VECTOR_DEGRADED]

/** 幂等回放：视为成功。 */
export function isIdempotentReplay(code: number): boolean {
  return code === ErrorCode.IDEMPOTENT_REPLAY
}

/** 是否降级（按码集合判定，不按 HTTP 状态）。 */
export function isDegradedCode(code: number): boolean {
  return DEGRADED_CODES.includes(code)
}

/**
 * 错误码 → 用户可见文案。
 *
 * 🔴 **只做兜底**：优先使用后端 `message`（后端文案更精确、且随业务演进）。
 *    本表仅在后端未给 message 时使用，避免前端编造与后端不一致的语义。
 */
export const FALLBACK_MESSAGE: Record<number, string> = {
  [ErrorCode.PARAM_INVALID]: '参数校验失败',
  [ErrorCode.PARAM_OVER_LIMIT]: '参数超出上限，请拆批后重试',
  [ErrorCode.UNAUTHENTICATED]: '登录状态已失效，请重新登录',
  [ErrorCode.BAD_CREDENTIALS]: '账号或密码错误',
  [ErrorCode.ACCOUNT_LOCKED]: '账号已被锁定，请稍后重试',
  [ErrorCode.FORBIDDEN]: '无访问权限',
  [ErrorCode.NOT_FOUND]: '资源不存在',
  [ErrorCode.CONFLICT_UNIQUE]: '存在重复数据',
  [ErrorCode.CONFLICT_STATE]: '该数据已被他人处理，请刷新后重试',
  [ErrorCode.DEPENDENCY_UNMET]: '存在未处理的引用，请先处理',
  [ErrorCode.UPLOAD_TOO_LARGE]: '文件超出大小上限',
  [ErrorCode.UNSUPPORTED_FORMAT]: '文件格式不支持',
  [ErrorCode.ILLEGAL_TRANSITION]: '当前状态不允许该操作，请刷新页面',
  [ErrorCode.MISSING_SNAPSHOT]: '缺少合并快照，无法回滚',
  [ErrorCode.RATE_LIMITED]: '操作过于频繁，请稍后重试',
  [ErrorCode.INTERNAL]: '服务内部错误',
  [ErrorCode.DB_ERROR]: '数据服务异常',
  [ErrorCode.OBJECT_STORAGE_ERROR]: '文件服务异常',
  [ErrorCode.AI_DEGRADED]: 'AI 服务已降级',
  [ErrorCode.VECTOR_DEGRADED]: '向量检索已降级为关键词模式',
  [ErrorCode.MODEL_TIMEOUT]: '模型调用超时',
  [ErrorCode.OCR_TIMEOUT]: 'OCR 服务超时',
  [ErrorCode.NETWORK]: '网络异常，请检查后端服务是否已启动',
}

// ============================================================================
// 4. 通用枚举（前端**不得自建映射表**：后端一律 `*_code` + `*_name` 同回）
// ============================================================================

/** 数据域（接口 §1.7）。 */
export const DataScope = {
  ALL: 1,
  ORG: 2,
  SELF: 3,
  STATS_ONLY: 4,
} as const

/** 角色码（种子 `ROLE_GRANTS`）。 */
export const ROLE = {
  ADM: 'ADM',
  HR: 'HR',
  MGR: 'MGR',
  EMP: 'EMP',
  EXEC: 'EXEC',
  AUDIT: 'AUDIT',
  AIOPS: 'AIOPS',
} as const
export type RoleCode = (typeof ROLE)[keyof typeof ROLE]

/** 通用启用 / 停用。 */
export const EnableStatus = { DISABLED: 0, ENABLED: 1 } as const

/** 标签来源（TBL-19 `source`）。 */
export const TagSource = { AI: 1, MANUAL: 2 } as const

/**
 * 展示用「未知值」哨兵。
 *
 * 原型约定：**空字段显示 `—` 而非空白**（P-7 边界态）。
 * 注意与 `null` 的区别 —— `0` 是合法值，不得被吞成 `—`。
 */
export const EMPTY_TEXT = '—'

/** 空值渲染：`null` / `undefined` / `''` → `—`；`0` / `false` 保留。 */
export function dash(v: unknown): string {
  if (v === null || v === undefined || v === '') return EMPTY_TEXT
  return String(v)
}
