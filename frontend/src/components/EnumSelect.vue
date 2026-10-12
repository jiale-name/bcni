<template>
  <el-select
    :model-value="modelValue"
    :placeholder="placeholder"
    :clearable="clearable"
    :disabled="disabled"
    :multiple="multiple"
    :style="{ width }"
    @update:model-value="onChange"
  >
    <el-option v-for="o in options" :key="String(o.value)" :label="o.label" :value="o.value" />
  </el-select>
</template>

<script setup lang="ts">
/**
 * 枚举 / 参数字典下拉（统一「筛选器」的取值来源）。
 *
 * 🔴 选项来源有两类，**渲染路径必须一致**（否则同一个枚举在两个页面表现不同）：
 *   1. **静态枚举** → `api/enums.ts`（与后端 `models/enums.py` 逐值镜像）；
 *   2. **业务字典** → `useDict()`（走 API-24，`dict:view` 广授）。
 *
 * 展示**用后端下发的 `*_name`**；本组件只负责「选什么值」，不负责渲染已有数据的中文名。
 */
import type { EnumOption } from '@/api/enums'

withDefaults(
  defineProps<{
    modelValue: string | number | (string | number)[] | null | undefined
    options: EnumOption[]
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
    multiple?: boolean
    width?: string
  }>(),
  {
    placeholder: '全部',
    clearable: true,
    disabled: false,
    multiple: false,
    width: '150px',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string | number | (string | number)[] | null): void
}>()

function onChange(v: string | number | (string | number)[] | null): void {
  emit('update:modelValue', v)
}
</script>
