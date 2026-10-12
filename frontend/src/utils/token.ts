/**
 * 令牌与身份态的最小存储（**唯一读写点**）。
 *
 * 为什么单独一个模块、而不是直接放在 Pinia store 里？
 * —— `http.ts`（拦截器）需要读 token，而 `store/auth.ts` 需要调 `http.ts`。
 *    若拦截器直接 import store，会形成 **模块循环依赖**（Vite 下表现为首次请求时
 *    拿到 `undefined` 的 store，且报错位置与真实根因无关）。
 *    故把「读写令牌 + 过期回调」下沉到本模块，两侧都只依赖它，依赖图保持无环。
 *
 * 🔴 纪律：`tenant_id` / `org_id` / `data_scope` 与令牌一样**只来自服务端**，
 *    前端不得把任何身份字段放进请求体或查询串（接口 §1.4 红线）。
 */

const ACCESS_KEY = 'tp.access_token'
const REFRESH_KEY = 'tp.refresh_token'

/** 令牌变化 / 失效时通知外部（路由守卫、store）的回调。 */
type ExpiredHandler = () => void
const expiredHandlers: ExpiredHandler[] = []

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    // 隐私模式 / 禁用 localStorage：降级为「内存态」（刷新页面即登出，但不影响本次会话）
    return memory[key] ?? null
  }
}

function safeSet(key: string, value: string): void {
  memory[key] = value
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

function safeRemove(key: string): void {
  delete memory[key]
  try {
    window.localStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}

/** localStorage 不可用时的兜底内存存储。 */
const memory: Record<string, string> = {}

export const tokenStorage = {
  getAccess(): string | null {
    return safeGet(ACCESS_KEY)
  },
  getRefresh(): string | null {
    return safeGet(REFRESH_KEY)
  },
  /**
   * 写入令牌对。
   * @param access access_token（必填）
   * @param refresh refresh_token；刷新（API-02）**不返回**新的 refresh_token，
   *                此时传 `undefined` 表示「保留原值」（刷新不延长刷新令牌生命周期）。
   */
  set(access: string, refresh?: string | null): void {
    safeSet(ACCESS_KEY, access)
    if (refresh) safeSet(REFRESH_KEY, refresh)
  },
  clear(): void {
    safeRemove(ACCESS_KEY)
    safeRemove(REFRESH_KEY)
  },
  hasToken(): boolean {
    return !!safeGet(ACCESS_KEY)
  },
  /** 注册「会话彻底失效」回调（refresh 也失败时触发）。 */
  onExpired(handler: ExpiredHandler): void {
    expiredHandlers.push(handler)
  },
  emitExpired(): void {
    for (const h of expiredHandlers) {
      try {
        h()
      } catch {
        /* 单个回调异常不得阻断其余回调 */
      }
    }
  },
}
