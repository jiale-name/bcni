/**
 * HTTP 客户端（Axios 封装）。
 *
 * 对齐《接口设计文档》§1.1 / §1.4 / §1.5 / §1.6。本文件是**前端唯一**的请求出口，
 * 视图层与 API 模块一律用这里导出的 `http` 助手，**不得**自行 `axios.create`。
 *
 * 🔴 四类最容易写错的语义，本文件逐条兜住：
 *   1. **成功判定是 `code === 0`，不是 HTTP 200** —— 降级返回 HTTP 200 + `code=50301`；
 *   2. **降级不弹错误** —— `50301/50302` 是「能力降级」而非「调用失败」，必须放行并让页面挂降级徽标；
 *   3. **`40903` 幂等回放按成功处理** —— 强制幂等接口（API-36 / API-79 / API-94）重放时返回首次结果，
 *      若按失败处理会让用户看到「操作失败」而实际已生效；
 *   4. **`page` 与 `data` 同级** —— 返回值 `ApiResult` 把两者一起交给调用方，杜绝分页表格漏读 `page`。
 */

import axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'

import {
  ErrorCode,
  FALLBACK_MESSAGE,
  type ApiResult,
  type CommonResp,
  type PageInfo,
} from './types'
import { tokenStorage } from '@/utils/token'

// ============================================================================
// 配置
// ============================================================================

/** 🔴 必须与 `vite.config.ts` 的 proxy 前缀、`deploy/nginx.conf` 的反代前缀保持一致。 */
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/** 普通请求超时（毫秒）。SSE 长连接不经此处，见 `sse.ts`。 */
const TIMEOUT: number = Number(import.meta.env.VITE_API_TIMEOUT || 30000)

// ============================================================================
// 错误对象
// ============================================================================

/**
 * 业务错误。**所有非成功响应都以本类型抛出**，视图层据 `code` 决定渲染哪种态。
 */
export class ApiError extends Error {
  readonly code: number
  readonly httpStatus: number
  readonly traceId: string | null
  readonly data: unknown
  /** `42901` 时的退避秒数（解析自 `Retry-After`）。 */
  readonly retryAfterSeconds: number | null

  constructor(opts: {
    code: number
    message: string
    httpStatus?: number
    traceId?: string | null
    data?: unknown
    retryAfterSeconds?: number | null
  }) {
    super(opts.message)
    this.name = 'ApiError'
    this.code = opts.code
    this.httpStatus = opts.httpStatus ?? 200
    this.traceId = opts.traceId ?? null
    this.data = opts.data ?? null
    this.retryAfterSeconds = opts.retryAfterSeconds ?? null
  }

  /** 越权（含跨租户 / 跨部门）—— 渲染「无访问权限」态。 */
  get isForbidden(): boolean {
    return this.code === ErrorCode.FORBIDDEN
  }

  /** 不存在（含已软删）—— 渲染空态，**不是**错误态。 */
  get isNotFound(): boolean {
    return this.code === ErrorCode.NOT_FOUND
  }

  /** 会话失效。 */
  get isUnauthenticated(): boolean {
    return this.code === ErrorCode.UNAUTHENTICATED
  }

  /** 并发状态冲突 —— 提示「已被他人处理，请刷新」。 */
  get isConflict(): boolean {
    return this.code === ErrorCode.CONFLICT_STATE
  }

  /** 限流 —— 按 `Retry-After` 退避。 */
  get isRateLimited(): boolean {
    return this.code === ErrorCode.RATE_LIMITED
  }

  /** 展示给用户的文案（后端 message 优先，缺失时用兜底表）。 */
  get displayMessage(): string {
    return this.message || FALLBACK_MESSAGE[this.code] || `请求失败（${this.code}）`
  }
}

// ============================================================================
// Axios 实例
// ============================================================================

const instance: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: TIMEOUT,
  headers: { 'Content-Type': 'application/json' },
})

/** 生成幂等键（接口 §1.6）：优先 UUID，无 crypto 时退化为「时间戳 + 随机」。 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

// ============================================================================
// 令牌刷新（单飞：并发 401 只触发一次 refresh）
// ============================================================================

let refreshing: Promise<boolean> | null = null

/**
 * 静默刷新 access_token（API-02）。
 *
 * 🔴 用**裸 axios** 而非本实例发起，避免拦截器递归。
 * 🔴 单飞：页面首次加载常并发 3~5 个请求，若各自刷新会互相吊销（refresh 是一次性语义风险），
 *    故用同一个 Promise 收敛。`refresh_token` 本身不带在请求头，走 body。
 */
async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = tokenStorage.getRefresh()
  if (!refreshToken) return false
  if (refreshing) return refreshing

  refreshing = (async () => {
    try {
      const resp = await axios.post<CommonResp<{ access_token: string; expires_in: number }>>(
        `${BASE_URL}/auth/refresh`,
        { refresh_token: refreshToken },
        { timeout: TIMEOUT, headers: { 'Content-Type': 'application/json' } },
      )
      const body = resp.data
      // 🔴 仍按 code 判定：HTTP 200 也可能是 40101（refresh_token 已过期）
      if (body && body.code === ErrorCode.OK && body.data?.access_token) {
        // 刷新**不返回**新 refresh_token，故只覆盖 access（保留原 refresh）
        tokenStorage.set(body.data.access_token)
        return true
      }
      return false
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()

  return refreshing
}

// ============================================================================
// 请求拦截器
// ============================================================================

/** 标记「已因 401 重试过一次」，防止无限循环。 */
const RETRIED_FLAG = '__tpRetried'

interface RetriableConfig extends InternalAxiosRequestConfig {
  [RETRIED_FLAG]?: boolean
}

instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStorage.getAccess()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  // 🔴 必须删掉实例默认的 `Content-Type: application/json`：
  //    axios 默认 transformRequest 在「Content-Type 为 json 且 data 是 FormData」时，
  //    会把 FormData 序列化成 JSON（文件变成 `[object File]`），后端 multipart 解析不到
  //    `files` 字段 → 422 Field required。删掉后由浏览器自动补 `multipart/form-data; boundary=...`。
  if (config.data instanceof FormData) {
    config.headers.delete('Content-Type')
  }
  return config
})

// ============================================================================
// 响应拦截器
// ============================================================================

/** 把「响应体」翻译成 `ApiResult`；非成功码抛 `ApiError`。 */
function parseBody<T, E = never>(body: CommonResp<T>, httpStatus: number): ApiResult<T, E> {
  const code = typeof body?.code === 'number' ? body.code : ErrorCode.INTERNAL

  // 🔴 幂等回放（40903）与降级（50301/50302）都**不是失败**。
  //    判定逻辑与错误拦截器共用 `isTolerable`，保持**单点定义**。
  if (!isTolerable(code)) {
    throw new ApiError({
      code,
      message: body?.message || '',
      httpStatus,
      traceId: body?.trace_id ?? null,
      data: body?.data ?? null,
    })
  }

  const degradedCode = code === ErrorCode.AI_DEGRADED || code === ErrorCode.VECTOR_DEGRADED

  const raw = body?.data as unknown
  let degraded = degradedCode
  let degradedReason: string | null = null
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const rec = raw as Record<string, unknown>
    if (rec.degraded === true) degraded = true
    if (typeof rec.degraded_reason === 'string') degradedReason = rec.degraded_reason
  }

  /**
   * 🔴 **顶层附加聚合字段的透传**（这是本文件最容易漏的一处）。
   *
   * 复核队列把 `meta`、AI 调用日志把 `summary` 放在响应的**顶层**（与 `page` 同级），
   * **不在 `data` 里**。若这里不透传，调用方 `res.extra` 恒为 `undefined` ——
   * 表现为「页面永远不显示 meta.warning / summary」，而**接口其实正常返回了**。
   * 两个键名不同（`meta` / `summary`），故此处归一到一个 `extra` 槽位。
   */
  const bodyRec = body as unknown as Record<string, unknown> | undefined
  const extra = (bodyRec?.meta ?? bodyRec?.summary ?? null) as E | null

  return {
    data: (body?.data ?? null) as T,
    page: (body?.page ?? null) as PageInfo | null,
    code,
    message: body?.message || 'ok',
    degraded,
    degradedReason,
    traceId: body?.trace_id ?? null,
    extra,
  }
}

/** `code` 是否属于「不是失败」的三类：成功 / 幂等回放 / 降级。 */
function isTolerable(code: number): boolean {
  return (
    code === ErrorCode.OK ||
    code === ErrorCode.IDEMPOTENT_REPLAY ||
    code === ErrorCode.AI_DEGRADED ||
    code === ErrorCode.VECTOR_DEGRADED
  )
}

/** 判断本请求是否属于认证端点（**不得**对其触发静默 refresh，否则会吞掉 40102 的真实文案）。 */
function isAuthEndpoint(url: string | undefined): boolean {
  if (!url) return false
  return /\/auth\/(login|refresh)$/.test(url)
}

instance.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: unknown) => {
    // ---- 非 axios 错误（编程错误）直接上抛 ----
    if (!axios.isAxiosError(error)) throw error

    const resp = error.response
    const config = error.config as RetriableConfig | undefined

    // ---- 无响应：超时 / 网络不可达 / 主动取消 ----
    if (!resp) {
      const aborted = error.code === 'ERR_CANCELED'
      throw new ApiError({
        code: ErrorCode.NETWORK,
        message: aborted ? '请求已取消' : '网络异常，请确认后端服务已启动（:8002）',
        httpStatus: 0,
      })
    }

    const body = resp.data as CommonResp<unknown> | undefined
    const hasBody = !!body && typeof body === 'object' && typeof body.code === 'number'
    const bizCode = hasBody ? (body as CommonResp<unknown>).code : null

    // ---- 401：静默刷新一次后重放原请求 ----
    // 🔴 三个前置条件缺一不可：
    //    ① 不是认证端点（登录失败是 40102、刷新失败是 40101，都不该进刷新流程）；
    //    ② 原本持有 access_token（否则是无会话的匿名请求，刷新无意义）；
    //    ③ 尚未重试过（防循环）。
    const looksUnauthorized = resp.status === 401 || bizCode === ErrorCode.UNAUTHENTICATED
    if (
      looksUnauthorized &&
      !isAuthEndpoint(config?.url) &&
      !!tokenStorage.getAccess() &&
      config &&
      !config[RETRIED_FLAG]
    ) {
      config[RETRIED_FLAG] = true
      const okRefresh = await refreshAccessToken()
      if (okRefresh) return instance.request(config)

      // 刷新也失败 → 会话彻底失效：清令牌并通知外部跳登录
      tokenStorage.clear()
      tokenStorage.emitExpired()
      throw new ApiError({
        code: ErrorCode.UNAUTHENTICATED,
        message: '登录状态已失效，请重新登录',
        httpStatus: 401,
        traceId: hasBody ? (body as CommonResp<unknown>).trace_id : null,
      })
    }

    // ---- 有响应：一律以响应体里的 `code` 为准（HTTP 200 也可能是失败，HTTP 4xx 也可能是降级）----
    if (hasBody) {
      const b = body as CommonResp<unknown>
      if (isTolerable(b.code)) {
        // 放行为「类 200」响应：交给 send() 的 parseBody 统一翻译（保持单点判定）
        return {
          data: b,
          status: resp.status,
          statusText: resp.statusText,
          headers: resp.headers,
          config: resp.config,
          request: resp.request,
        } as AxiosResponse
      }
      const ra = Number(resp.headers?.['retry-after'])
      throw new ApiError({
        code: b.code,
        message: b.message || '',
        httpStatus: resp.status,
        traceId: b.trace_id ?? null,
        data: b.data ?? null,
        retryAfterSeconds: Number.isFinite(ra) && ra > 0 ? ra : null,
      })
    }

    throw new ApiError({
      code: ErrorCode.INTERNAL,
      message: `服务异常（HTTP ${resp.status}）`,
      httpStatus: resp.status,
      traceId: (resp.headers?.['x-trace-id'] as string) ?? null,
    })
  },
)

// ============================================================================
// 统一出口
// ============================================================================

/** 单个请求的附加选项。 */
export interface RequestOptions {
  /** 强制携带 `Idempotency-Key`（接口 §1.6 的三个强制幂等接口）。 */
  idempotencyKey?: string | boolean
  /** 覆盖超时（导出 / 大批量操作可放宽）。 */
  timeout?: number
  /** 返回二进制（文件下载）。 */
  responseType?: 'json' | 'blob'
}

/** 组装 axios 配置（含幂等键）。 */
function buildConfig(
  method: 'get' | 'post' | 'put' | 'delete',
  url: string,
  payload?: unknown,
  params?: Record<string, unknown>,
  opts?: RequestOptions,
): AxiosRequestConfig {
  const headers: Record<string, string> = {}
  if (opts?.idempotencyKey) {
    headers['Idempotency-Key'] =
      typeof opts.idempotencyKey === 'string' ? opts.idempotencyKey : newIdempotencyKey()
  }
  const cfg: AxiosRequestConfig = {
    url,
    method,
    params: params ? pruneEmpty(params) : undefined,
    headers,
    timeout: opts?.timeout ?? TIMEOUT,
  }
  if (payload !== undefined) cfg.data = payload
  return cfg
}

/**
 * 剪掉 `undefined` / `null` / `''` 的查询参数。
 *
 * 🔴 理由：后端为「统一有值」的强契约（响应 `null` 而非空串、请求 `extra="forbid"`），
 *    查询串里带上 `keyword=`（空串）会与「不筛选」语义产生歧义；
 *    而 `?a=undefined` 这种字符串更会直接触发 40001。
 */
function pruneEmpty(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

async function send<T, E = never>(cfg: AxiosRequestConfig): Promise<ApiResult<T, E>> {
  const resp = await instance.request<CommonResp<T>>(cfg)
  return parseBody<T, E>(resp.data, resp.status)
}

export const http = {
  get<T, E = never>(url: string, params?: Record<string, unknown>, opts?: RequestOptions) {
    return send<T, E>(buildConfig('get', url, undefined, params, opts))
  },
  post<T, E = never>(
    url: string,
    payload?: unknown,
    params?: Record<string, unknown>,
    opts?: RequestOptions,
  ) {
    return send<T, E>(buildConfig('post', url, payload ?? {}, params, opts))
  },
  put<T, E = never>(
    url: string,
    payload?: unknown,
    params?: Record<string, unknown>,
    opts?: RequestOptions,
  ) {
    return send<T, E>(buildConfig('put', url, payload ?? {}, params, opts))
  },
  del<T, E = never>(url: string, params?: Record<string, unknown>, opts?: RequestOptions) {
    return send<T, E>(buildConfig('delete', url, undefined, params, opts))
  },

  /**
   * 文件上传（multipart）。**不设置 `Content-Type`** —— 交给浏览器带 boundary。
   */
  async upload<T, E = never>(
    url: string,
    form: FormData,
    opts?: RequestOptions & { onProgress?: (percent: number) => void },
  ): Promise<ApiResult<T, E>> {
    const resp = await instance.request<CommonResp<T>>({
      url,
      method: 'post',
      data: form,
      timeout: opts?.timeout ?? 0, // 上传不设超时，由用户主动取消
      headers: opts?.idempotencyKey
        ? {
            'Idempotency-Key':
              typeof opts.idempotencyKey === 'string' ? opts.idempotencyKey : newIdempotencyKey(),
          }
        : undefined,
      onUploadProgress: opts?.onProgress
        ? (e) => {
            if (e.total) opts.onProgress!(Math.round((e.loaded * 100) / e.total))
          }
        : undefined,
    })
    return parseBody<T, E>(resp.data, resp.status)
  },

  /**
   * 下载文件（CSV / 数据包）。
   * 后端返回的是 `CommonResp` 内嵌 `content` 字符串而非二进制流，故仍走统一壳解析；
   * 这里额外提供 blob 直取，供未来切换为流式下载时使用。
   */
  async download(url: string, params?: Record<string, unknown>): Promise<Blob> {
    const resp = await instance.request<Blob>({
      url,
      method: 'get',
      params: params ? pruneEmpty(params) : undefined,
      responseType: 'blob',
      timeout: 120000,
    })
    return resp.data
  },

  /** 取原始 axios 实例（仅 SSE / 特殊场景使用）。 */
  raw: instance,
}

/** 当前生效的 API 前缀（SSE 拼接 URL 时需要）。 */
export const API_BASE_URL = BASE_URL

/** 组装带查询串的绝对（同源相对）URL。 */
export function buildUrl(path: string, params?: Record<string, unknown>): string {
  const qs = params
    ? new URLSearchParams(
        Object.entries(pruneEmpty(params)).map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  return `${BASE_URL}${path}${qs ? `?${qs}` : ''}`
}
