<template>
  <!-- 加载中 -->
  <div v-if="status === 'loading'" class="state">
    <el-skeleton :rows="rows" animated />
  </div>

  <!-- 无权限（40301）—— 🔴 绝不透露资源是否存在 -->
  <div v-else-if="status === 'forbidden'" class="state">
    <div class="big">🔒</div>
    <h4>无访问权限</h4>
    <p>{{ forbiddenHint }}</p>
    <div class="acts">
      <el-button @click="emit('back')">返回上一页</el-button>
      <el-button type="primary" plain @click="emit('retry')">重新加载</el-button>
    </div>
  </div>

  <!-- 不存在（40401）—— 空态，不是错误 -->
  <div v-else-if="status === 'notfound'" class="state">
    <div class="big">◌</div>
    <h4>{{ notFoundTitle }}</h4>
    <p>{{ notFoundHint }}</p>
    <div class="acts">
      <el-button type="primary" plain @click="emit('retry')">重新加载</el-button>
      <slot name="notfound-actions" />
    </div>
  </div>

  <!-- 空态 -->
  <div v-else-if="status === 'empty'" class="state">
    <div class="big">◌</div>
    <h4>{{ emptyTitle }}</h4>
    <p>{{ emptyHint }}</p>
    <div class="acts">
      <slot name="empty-actions">
        <el-button @click="emit('retry')">重新加载</el-button>
      </slot>
    </div>
  </div>

  <!-- 错误态 —— **必须展示错误码与 trace_id**（便于上报） -->
  <div v-else-if="status === 'error'" class="state">
    <div class="big err">!</div>
    <h4>{{ errorTitle }}</h4>
    <p>
      <span v-if="code">错误码 {{ code }}</span>
      <span v-if="traceId"> ｜ trace_id: <code class="mono">{{ traceId }}</code></span>
    </p>
    <p v-if="message" class="muted sm">{{ message }}</p>
    <div class="acts">
      <el-button type="primary" plain :loading="loading" @click="emit('retry')">重新加载</el-button>
      <el-button v-if="traceId" @click="copyTrace">复制 trace_id</el-button>
    </div>
  </div>

  <!-- 正常 -->
  <slot v-else />
</template>

<script setup lang="ts">
/**
 * 统一状态面板：`loading` / `empty` / `error` / `forbidden` / `notfound`。
 *
 * 🔴 为什么必须**五种分开**（原型 P-1 ~ P-19 的「状态覆盖」要求）：
 *   - `40301` 与 `40401` 语义不同：前者是**无权限**（不透露资源存在性，接口 §1.7），
 *     后者是**不存在**（含已软删）。把两者合成一个「没找到」会违背防探测设计。
 *   - 两者都**不该**显示成「系统错误」——那会让用户以为要重试，
 *     而实际上重试永远失败，应当联系管理员调整权限。
 *   - 错误态**必须带 `trace_id`**：没有它，后端无法在日志里定位这次请求。
 */
import { computed } from 'vue'

import type { LoadStatus } from '@/composables/useAsyncData'
import { copyText } from '@/utils/format'
import { ElMessage } from 'element-plus'

const props = withDefaults(
  defineProps<{
    status: LoadStatus | 'empty'
    loading?: boolean
    code?: number | null
    message?: string | null
    traceId?: string | null
    rows?: number
    emptyTitle?: string
    emptyHint?: string
    errorTitle?: string
    notFoundTitle?: string
    notFoundHint?: string
    forbiddenHint?: string
  }>(),
  {
    loading: false,
    code: null,
    message: null,
    traceId: null,
    rows: 5,
    emptyTitle: '暂无数据',
    emptyHint: '当前条件下没有记录',
    errorTitle: '加载失败',
    notFoundTitle: '未找到相关记录',
    notFoundHint: '该记录可能已被删除，或不在您的数据范围内',
    forbiddenHint: '当前角色无该页面的数据权限。若确需访问，请联系管理员调整角色与数据范围。',
  },
)

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'back'): void
}>()

const traceId = computed(() => props.traceId)

async function copyTrace(): Promise<void> {
  if (!traceId.value) return
  const done = await copyText(traceId.value)
  ElMessage({ type: done ? 'success' : 'warning', message: done ? '已复制 trace_id' : '复制失败，请手动选择' })
}
</script>

<style scoped>
.state {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 34px 18px;
  text-align: center;
  color: var(--txt-3);
}
.state .big {
  font-size: 32px;
  opacity: 0.4;
  line-height: 1;
}
.state .big.err {
  color: var(--danger);
  opacity: 0.8;
  font-weight: 700;
}
.state h4 {
  color: var(--txt-2);
  margin-top: 8px;
  font-size: 14px;
  font-weight: 600;
}
.state p {
  font-size: 12.5px;
  margin-top: 5px;
}
.state .acts {
  margin-top: 14px;
  display: flex;
  gap: 8px;
  justify-content: center;
}
code.mono {
  font-family: var(--mono);
  font-size: 12px;
}
</style>
