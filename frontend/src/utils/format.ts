/**
 * 全局格式化工具（**展示层唯一实现点**）。
 *
 * 🔴 纪律：日期 / 金额 / 相似度 / 文件大小 / 脱敏标识的渲染规则**全项目只有一份**。
 *    服务层或视图里各抄一遍，是「同一数据在两页显示不同」的常见根因
 *    （批次 13 缺陷 ⑤ 就是此类：标签来源名在服务层被重抄了一次）。
 */

import { EMPTY_TEXT } from '@/api/types'

// ============================================================================
// 时间
// ============================================================================

/**
 * 后端时间统一为 `YYYY-MM-DD HH:mm:ss`（**不带 `T`**，见 `schemas/common.py`）。
 *
 * 🔴 **不要**用 `new Date(str)` 去解析 —— 带空格的格式在部分浏览器被当作 UTC，
 *    会导致「显示时间比真实时间差 8 小时」。此处只做**字符串层面的裁剪**。
 */
export function fmtDateTime(v: string | null | undefined, withSeconds = false): string {
  if (!v) return EMPTY_TEXT
  const s = String(v).replace('T', ' ')
  return withSeconds ? s.slice(0, 19) : s.slice(0, 16)
}

/** 只取日期部分。 */
export function fmtDate(v: string | null | undefined): string {
  if (!v) return EMPTY_TEXT
  return String(v).replace('T', ' ').slice(0, 10)
}

/**
 * 相对时间（「3 小时前」）。
 * ⚠️ 依赖浏览器本地时区解析 `YYYY-MM-DD HH:mm:ss` —— 这里**需要**时间差，
 *    故显式把空格替换为 `T` 再 `new Date()`，避免各浏览器解析差异。
 */
export function fmtRelative(v: string | null | undefined): string {
  if (!v) return EMPTY_TEXT
  const t = new Date(String(v).replace(' ', 'T')).getTime()
  if (Number.isNaN(t)) return String(v)
  const diff = Date.now() - t
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 30) return `${day} 天前`
  return fmtDate(v)
}

/** 由 `YYYY-MM-DD HH:mm:ss` 生成 Element Plus `el-date-picker` 需要的 `Date`。 */
export function toDateObj(v: string | null | undefined): Date | null {
  if (!v) return null
  const d = new Date(String(v).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

/** `Date` → `YYYY-MM-DD HH:mm:ss`（发给后端的格式）。 */
export function toDateTimeStr(d: Date | null | undefined): string | undefined {
  if (!d) return undefined
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  )
}

// ============================================================================
// 数值
// ============================================================================

/** 文件大小。 */
export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return EMPTY_TEXT
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** 相似度：后端给 0~1 的浮点。 */
export function fmtSimilarity(v: number | null | undefined): string {
  if (v === null || v === undefined) return EMPTY_TEXT
  return `${(v * 100).toFixed(1)}%`
}

/** 比率（0~1）→ 百分比，保留一位。 */
export function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined) return EMPTY_TEXT
  return `${(v * 100).toFixed(1)}%`
}

/** 置信度；`null` 表示「不来自模型」而非 0 —— 必须区分。 */
export function fmtConfidence(v: number | null | undefined): string {
  if (v === null || v === undefined) return '规则命中'
  return `${(v * 100).toFixed(0)}%`
}

/** 数字千分位。 */
export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined) return EMPTY_TEXT
  return v.toLocaleString('zh-CN')
}

// ============================================================================
// 脱敏
// ============================================================================

/**
 * 脱敏标识：后端下发的是**已脱敏串**（如 `138****8888`）。
 * 本函数**不负责脱敏**，只判断「看起来是否已脱敏」，用于决定是否加 🔒 图标。
 *
 * 🔴 前端**绝不做脱敏**（脱敏是服务端序列化层的职责，CL-4）——
 *    前端做脱敏会给人「数据没出网」的错觉，而实际上明文已经到了浏览器。
 */
export function looksMasked(v: string | null | undefined): boolean {
  if (!v) return false
  return /\*{2,}/.test(String(v))
}

/** 渲染敏感字段：已脱敏的加 🔒 前缀并变灰。 */
export function renderSensitive(
  v: string | null | undefined,
  opts: { masked?: boolean } = {},
): string {
  if (!v) return EMPTY_TEXT
  const masked = opts.masked ?? looksMasked(v)
  return masked ? `🔒 ${v}` : String(v)
}

// ============================================================================
// CSV
// ============================================================================

/**
 * 落盘 CSV。
 * 🔴 后端已完成**公式注入防护**（`=`/`+`/`-`/`@` 开头转义），前端**不得**二次加工内容，
 *    否则会把转义前缀一起去掉、把防护绕回来。
 */
export function saveTextFile(content: string, filename: string, mime = 'text/csv;charset=utf-8'): void {
  // BOM：避免 Excel 打开中文乱码
  const blob = new Blob([`\uFEFF${content}`], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** 复制到剪贴板（trace_id 上报用）。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
