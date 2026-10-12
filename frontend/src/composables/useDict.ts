/**
 * 字典下拉 composable（对接 F-1.7 数据字典）。
 *
 * 🔴 走 **API-24**（`dict:view` 广授 HR/MGR/EMP/EXEC），**不是** API-23（仅 ADM）。
 *    页面若先去拉「字典类型列表」再取项，会在非 ADM 角色下 40301 → 下拉全空。
 *
 * 用法：
 * ```ts
 * const { options, loading } = useDict('education_level')
 * ```
 */

import { onMounted, ref, type Ref } from 'vue'

import type { EnumOption } from '@/api/enums'
import { useAppStore } from '@/store/app'

export interface DictOption {
  value: string | number
  label: string
  /** 父级 `item_code`（字典项支持层级）。 */
  parent_code: string | null
}

export function useDict(typeCode: string): {
  options: Ref<DictOption[]>
  loading: Ref<boolean>
  reload: () => Promise<void>
} {
  const app = useAppStore()
  const options = ref<DictOption[]>([])
  const loading = ref(false)

  async function reload(): Promise<void> {
    loading.value = true
    try {
      app.clearDictCache(typeCode)
      const items = await app.loadDict(typeCode)
      options.value = items.map((i) => ({
        value: i.item_code,
        label: i.item_name,
        parent_code: i.parent_code,
      }))
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    void (async () => {
      const items = await app.loadDict(typeCode)
      options.value = items.map((i) => ({
        value: i.item_code,
        label: i.item_name,
        parent_code: i.parent_code,
      }))
    })()
  })

  return { options, loading, reload }
}

/** 把静态枚举选项转成下拉可用结构（供筛选器复用同一渲染路径）。 */
export function fromEnum(list: EnumOption[]): DictOption[] {
  return list.map((e) => ({ value: e.value, label: e.label, parent_code: null }))
}
