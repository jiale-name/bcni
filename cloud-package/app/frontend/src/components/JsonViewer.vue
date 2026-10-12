<template>
  <div class="json" :class="{ collapsed: !open && collapsible }">
    <div class="hd" @click="collapsible && (open = !open)">
      <el-icon v-if="collapsible"><component :is="open ? 'ArrowDown' : 'ArrowRight'" /></el-icon>
      <span class="label">{{ label }}</span>
      <span class="muted xs">（{{ summary }}）</span>
      <el-icon v-if="copyable" class="cp" title="复制 JSON" @click.stop="onCopy"><DocumentCopy /></el-icon>
    </div>
    <pre v-show="open || !collapsible" class="body">{{ text }}</pre>
  </div>
</template>

<script setup lang="ts">
/**
 * JSON 折叠查看器（图状态 / 输入输出 / 变更前后值 / 待办负载）。
 *
 * 🔴 两点注意：
 *   1. **只读展示，不做美化加工** —— 排查场景下，「原样」比「好看」重要；
 *   2. 对 `null` / `undefined` 明确显示「（空）」，**不要留白** ——
 *      留白会让人以为组件坏了，而实际是数据本身为空（原型「空字段显示 —」的同一口径）。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { copyText } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    value: unknown
    label?: string
    /** 默认是否展开。 */
    defaultOpen?: boolean
    /** 是否可折叠。 */
    collapsible?: boolean
    /** 是否可复制。 */
    copyable?: boolean
  }>(),
  { label: 'JSON', defaultOpen: false, collapsible: true, copyable: true },
)

const open = ref(props.defaultOpen)

const text = computed(() => {
  const v = props.value
  if (v === null || v === undefined || v === '') return '（空）'
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
})

const summary = computed(() => {
  const v = props.value
  if (Array.isArray(v)) return `数组 · ${v.length} 项`
  if (v && typeof v === 'object') return `对象 · ${Object.keys(v as object).length} 个键`
  return '标量'
})

async function onCopy(): Promise<void> {
  const done = await copyText(text.value)
  ElMessage({ type: done ? 'success' : 'warning', message: done ? '已复制' : '复制失败' })
}
</script>

<style scoped>
.json {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-2, #fafbfd);
  overflow: hidden;
}
.hd {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  font-size: 12.5px;
  color: var(--txt-2);
  cursor: default;
  border-bottom: 1px solid var(--line);
}
.collapsible .hd {
  cursor: pointer;
}
.label {
  font-weight: 600;
}
.cp {
  margin-left: auto;
  cursor: pointer;
  color: var(--txt-3);
}
.cp:hover {
  color: var(--primary);
}
.body {
  margin: 0;
  padding: 10px;
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--txt-2);
  max-height: 340px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
