<template>
  <div v-if="traceId || extra" class="trace">
    <span v-if="traceId">
      trace_id: <code class="mono">{{ traceId }}</code>
      <el-icon class="cp" title="复制" @click="onCopy"><DocumentCopy /></el-icon>
    </span>
    <span v-if="extra" class="muted">｜{{ extra }}</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 页脚追踪信息条。
 *
 * `trace_id` 是**唯一能跨层定位一次请求**的标识（接口 §1.4 第 8 层），
 * 因此在数据页常驻展示并支持一键复制 —— 报障时用户才能给出有效信息。
 */
import { ElMessage } from 'element-plus'

import { copyText } from '@/utils/format'

const props = defineProps<{
  traceId?: string | null
  extra?: string | null
}>()

async function onCopy(): Promise<void> {
  if (!props.traceId) return
  const done = await copyText(props.traceId)
  ElMessage({ type: done ? 'success' : 'warning', message: done ? '已复制 trace_id' : '复制失败' })
}
</script>

<style scoped>
.trace {
  margin-top: 10px;
  font-size: 11.5px;
  color: var(--txt-3);
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.mono {
  font-family: var(--mono);
}
.cp {
  cursor: pointer;
  margin-left: 4px;
}
.cp:hover {
  color: var(--primary);
}
</style>
