/**
 * 枚举选项（用于**筛选下拉**）。
 *
 * 🔴 来源与纪律（这条很重要，写反了会造出「两套真相」）：
 *
 *   - **渲染状态文案**：一律用后端下发的 `*_name`（`data_status_name` / `status_name` /
 *     `change_source_name` …），**前端不自建映射表**（接口 §1.1）。
 *     好处：后端改文案，前端零改动。
 *
 *   - **筛选下拉的「可选值」**：后端**没有**枚举字典接口，筛选器必须在前端列出可选项。
 *     因此本文件是 `backend/app/models/enums.py` 的**逐值镜像**（用脚本从 ORM 枚举导出，
 *     保证与后端同源，不是手抄）。改动后端枚举时，**必须同批更新本文件**。
 *
 *   - ⚠️ `AGENT_TASK_STATUS` / `AGENT_INTERRUPT_STATUS` 的后端 `label` 目前是**英文**
 *     （`pending` / `running` / …）—— 属批次 7 已登记遗留（「两个 Agent 状态枚举的
 *     `*_name` 仍为英文」）。本文件**只给中文显示名**，供前端在 `status_name` 为英文时兜底；
 *     后端 `status_name` 一旦中文化，`agentStatusText()` 会自动优先采用后端值。
 */

export interface EnumOption {
  value: number
  label: string
}

/** TBL-11 `data_status`。 */
export const TALENT_DATA_STATUS: EnumOption[] = [
  { value: 0, label: '待复核' },
  { value: 1, label: '有效' },
  { value: 2, label: '已合并' },
  { value: 3, label: '已归档' },
  { value: 4, label: '已删除' },
]

/** TBL-16 `review_status`。 */
export const PARSE_REVIEW_STATUS: EnumOption[] = [
  { value: 0, label: '待复核' },
  { value: 1, label: '已确认' },
  { value: 2, label: '已修正' },
  { value: 3, label: '免复核' },
]

/** TBL-17 查重对 `status`。 */
export const DUP_PAIR_STATUS: EnumOption[] = [
  { value: 0, label: '待处理' },
  { value: 1, label: '已合并' },
  { value: 2, label: '已忽略' },
]

/** TBL-19 标签 `status`。 */
export const TAG_DICT_STATUS: EnumOption[] = [
  { value: 0, label: '待审核' },
  { value: 1, label: '启用' },
  { value: 2, label: '停用' },
]

/** TBL-19 标签 `source`。 */
export const TAG_SOURCE: EnumOption[] = [
  { value: 0, label: '人工' },
  { value: 1, label: 'AI' },
]

/** TBL-20 导入任务 `status`。 */
export const IMPORT_TASK_STATUS: EnumOption[] = [
  { value: 0, label: '待处理' },
  { value: 1, label: '解析中' },
  { value: 2, label: '已完成' },
  { value: 3, label: '部分失败' },
  { value: 4, label: '失败' },
]

/** TBL-21 导入文件 `parse_status`。 */
export const IMPORT_FILE_PARSE_STATUS: EnumOption[] = [
  { value: 0, label: '待解析' },
  { value: 1, label: '成功' },
  { value: 2, label: '失败' },
]

/**
 * TBL-21 导入文件 `parse_error`：**原因码 → 展示名**。
 *
 * 🔴 唯一事实是后端 `app/services/import_service.py` 的 `FAILURE_REASON_LABELS`
 * （接口 §4.1 约定：新增原因码须同步前端常量表）。本表只做展示翻译。
 *
 * ⚠️ **未命中时原样显示**原因码，不要兜成「未知错误」—— 后端异常兜底路径会写
 * `ValueError: xxx` 这类原文，用「未知错误」覆盖它等于把唯一的线索抹掉。
 */
export const IMPORT_FAILURE_REASON: Record<string, string> = {
  OCR_FAILED: '扫描件 OCR 识别失败',
  ENCRYPTED_FILE: '文件加密无法读取',
  FORMAT_UNSUPPORTED: '格式不支持',
  EXTRACTOR_MISSING: '解析器未就绪（待 OCR 选型）',
  DUPLICATE_HASH: '文件内容重复',
  STORAGE_FAILED: '文件存储失败',
  PARSE_INTERRUPTED: '解析被人工驳回或异常终止',
  NAME_MISSING: '未抽取到姓名，待人工补录',
  UNKNOWN: '未知错误',
}

/** TBL-22 变更来源 `change_source`（**合并的 `2` 同时是回滚依据**）。 */
export const CHANGE_SOURCE: EnumOption[] = [
  { value: 0, label: 'AI 解析' },
  { value: 1, label: '人工复核' },
  { value: 2, label: '合并' },
  { value: 3, label: '重解析' },
]

/** Prompt `status`。 */
export const PROMPT_STATUS: EnumOption[] = [
  { value: 0, label: '草稿' },
  { value: 1, label: '启用' },
  { value: 2, label: '停用' },
]

/** AI 调用 `status`。 */
export const AI_CALL_STATUS: EnumOption[] = [
  { value: 0, label: '失败' },
  { value: 1, label: '成功' },
]

/**
 * Agent 任务 `status`。
 * ⚠️ 后端 label 为英文（批次 7 遗留）；此处为**前端兜底中文名**，非新增事实源。
 */
export const AGENT_TASK_STATUS: EnumOption[] = [
  { value: 0, label: '待执行' },
  { value: 1, label: '执行中' },
  { value: 2, label: '等待人工介入' },
  { value: 3, label: '成功' },
  { value: 4, label: '失败' },
]

/** Agent 人工待办 `status`。⚠️ 同上：后端 label 为英文。 */
export const AGENT_INTERRUPT_STATUS: EnumOption[] = [
  { value: 0, label: '待处理' },
  { value: 1, label: '已通过' },
  { value: 2, label: '已驳回' },
]

/** TBL-30 权利请求 `request_type`。 */
export const PRIVACY_REQUEST_TYPE: EnumOption[] = [
  { value: 0, label: '更正' },
  { value: 1, label: '删除' },
  { value: 2, label: '导出' },
]

/**
 * TBL-30 权利请求 `status`（**对外三态**）。
 * ⚠️ 内部态 `3 处理中` 由后端映射为「待处理」下发（SM-3 内部态不外露），
 *    前端**不会**收到 3；此处保留仅为完整镜像，筛选器不提供该选项。
 */
export const PRIVACY_REQUEST_STATUS: EnumOption[] = [
  { value: 0, label: '待处理' },
  { value: 1, label: '已通过' },
  { value: 2, label: '已驳回' },
]

/** 权限点类型。 */
export const PERM_TYPE: EnumOption[] = [
  { value: 1, label: '菜单' },
  { value: 2, label: '按钮' },
  { value: 3, label: '接口' },
]

/** 性别。 */
export const GENDER: EnumOption[] = [
  { value: 0, label: '未知' },
  { value: 1, label: '男' },
  { value: 2, label: '女' },
]

/** 学历（TBL-05 字典 `education_level`；具体档位可由 ADM 在界面维护，此处仅给常见默认）。 */
export const EDUCATION_LEVEL: EnumOption[] = [
  { value: 1, label: '高中及以下' },
  { value: 2, label: '大专' },
  { value: 3, label: '本科' },
  { value: 4, label: '硕士' },
  { value: 5, label: '博士' },
]

/**
 * Agent 状态文案：**优先用后端 `status_name`**，仅当它是英文枚举名时用本地中文兜底。
 * @param code 状态码
 * @param name 后端下发的 `status_name`
 * @param table 对应枚举选项表
 */
export function statusText(code: number | null, name: string | null, table: EnumOption[]): string {
  const local = table.find((o) => o.value === code)
  // 后端已给中文 → 用后端（单一事实源）
  if (name && !/^[a-z_]+$/.test(name)) return name
  if (local) return local.label
  return name || String(code ?? '')
}
