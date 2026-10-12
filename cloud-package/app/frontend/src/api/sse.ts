/**
 * SSE 客户端（**基于 `fetch` 流，而非 `EventSource`**）。
 *
 * 🔴 为什么不用 `EventSource`：
 *    `EventSource` **无法自定义请求头**，而本项目所有接口都要求 `Authorization: Bearer <token>`
 *    （后端从 JWT 读 `tenant_id` / `org_id` / `data_scope`，见接口 §1.4 红线）。
 *    把令牌放进查询串是明确禁止的（会进访问日志、浏览器历史、Referer）。
 *    因此两个长连接端点（API-39 导入进度 / API-63 RAG 问答）一律用 `fetch` + `ReadableStream`
 *    手工解析 SSE 帧。
 *
 * 本模块只做「帧解析」这一件事；业务语义（progress / done / error / delta / citation…）
 * 由调用方在 `onEvent` 里处理。
 */

import { API_BASE_URL } from './http'
import { tokenStorage } from '@/utils/token'

/** 一个已解析的 SSE 事件。 */
export interface SseEvent {
  /** 事件名；缺省为 `message`。 */
  event: string
  /** `data:` 负载原文（多行 `data:` 用 `\n` 连接）。 */
  data: string
  /** 事件 id（`id:` 字段），用于断线续传。 */
  id: string | null
}

export interface SseHandlers {
  /** 收到一个事件（已解析 `data` 为 JSON 的场景建议在回调内自行 `JSON.parse`）。 */
  onEvent: (evt: SseEvent) => void
  /** 流正常结束（服务端关闭连接）。 */
  onClose?: () => void
  /** 网络或解析异常。 */
  onError?: (err: Error) => void
}

export interface SseOptions {
  method?: 'GET' | 'POST'
  /** POST 时的 JSON 请求体（API-63 问答用）。 */
  body?: unknown
  signal?: AbortSignal
  /** 额外请求头（如 `Idempotency-Key`）。 */
  headers?: Record<string, string>
}

/** 把 SSE 原始文本块按空行切分事件帧。 */
function splitFrames(buffer: string): { frames: string[]; rest: string } {
  // SSE 规范：事件之间以**空行**分隔；兼容 \r\n 与 \n
  const normalized = buffer.replace(/\r\n/g, '\n')
  const parts = normalized.split('\n\n')
  const rest = parts.pop() ?? ''
  return { frames: parts, rest }
}

/** 解析单帧文本为 `SseEvent`；忽略注释行（`:` 开头）与空帧。 */
function parseFrame(frame: string): SseEvent | null {
  let event = 'message'
  let id: string | null = null
  const dataLines: string[] = []

  for (const line of frame.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    // SSE 规范：冒号后的**一个**空格应被剥掉
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
    else if (field === 'id') id = value
    // `retry` 字段本项目不使用（重连策略由页面决定）
  }

  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n'), id }
}

/**
 * 建立 SSE 连接并持续读取，直到流结束或被 `signal` 取消。
 *
 * @returns 一个 Promise，在**流结束**时 resolve（不 reject；错误经 `onError` 上报）。
 */
export async function openSse(
  path: string,
  handlers: SseHandlers,
  opts: SseOptions = {},
): Promise<void> {
  const url = `${API_BASE_URL}${path}`
  const headers: Record<string, string> = { Accept: 'text/event-stream', ...(opts.headers ?? {}) }
  const token = tokenStorage.getAccess()
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(url, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    // 🔴 **主动取消除外（根因修复 · 2026-09-23）**
    //    `signal.abort()` 会让 `fetch` 在**握手阶段**就 reject 一个 `AbortError` ——
    //    点「停止」/ 切换任务 / 开新会话 / 组件卸载都会触发。这**不是错误**，
    //    必须与下方「流读取阶段」的 catch 保持一致，静默走 `onClose`。
    //
    //    否则页面会把「我自己主动取消的」渲染成红色报错，用户看到的就是浏览器原文
    //    `signal is aborted without reason`（ImportView / AskView 两个调用方都受影响）。
    //    这与 `AskView.onAbort()` 的既有注释「主动停止不是错误」是**同一条语义** ——
    //    本次修复是让实现回到该设计意图上。
    //
    //    ⚠️ **判据是 `name === 'AbortError'`，不要改成判断 message**：
    //       实测「请求中 abort」与「已 abort 的 signal 再发起 fetch」都是此名，
    //       但**文案两端不同** —— 浏览器 `signal is aborted without reason`、
    //       Node `This operation was aborted`，拿文案匹配必然漏。
    //       另：若将来改用 `abort(reason)` 传入原因，抛出的是 reason 本身
    //       （`name` 不再是 `AbortError`），届时需另行处理。
    if ((e as Error)?.name === 'AbortError') {
      handlers.onClose?.()
      return
    }
    handlers.onError?.(e instanceof Error ? e : new Error('连接失败'))
    return
  }

  // 🔴 握手期即失败：越权（40301）等**不能藏在事件流里**（CR-8 口径），
  //    必须在这里按普通 HTTP 错误处理并让页面渲染对应状态。
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const text = await response.text()
      const parsed = JSON.parse(text) as { code?: number; message?: string }
      if (parsed?.message) detail = `[${parsed.code}] ${parsed.message}`
    } catch {
      /* 非 JSON 响应体，保留 HTTP 状态描述 */
    }
    handlers.onError?.(new Error(detail))
    return
  }
  if (!response.body) {
    handlers.onError?.(new Error('当前浏览器不支持流式响应'))
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { frames, rest } = splitFrames(buffer)
      buffer = rest
      for (const frame of frames) {
        const evt = parseFrame(frame)
        if (evt) handlers.onEvent(evt)
      }
    }
    // 流末尾若仍有残留帧（服务端未以空行收尾），补解析一次，避免丢最后一个事件
    if (buffer.trim()) {
      const evt = parseFrame(buffer)
      if (evt) handlers.onEvent(evt)
    }
    handlers.onClose?.()
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      handlers.onClose?.()
      return
    }
    handlers.onError?.(e instanceof Error ? e : new Error('流读取异常'))
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* ignore */
    }
  }
}
