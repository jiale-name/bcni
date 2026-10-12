<template>
  <div v-if="degraded" class="degraded">
    <span class="dot" />
    <b>服务已降级</b>
    <span class="reason">{{ reason || 'AI / 检索能力受限，已使用兜底策略' }}</span>
    <span v-if="mode" class="mode">当前模式：{{ mode }}</span>
    <span class="tip">结果仍然可用，但可能不如完整能力精确</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 降级徽标（接口 §1.5 / F-1.6）。
 *
 * 🔴 三条口径：
 *   1. 降级是 **HTTP 200 + `degraded=true`**（可能是 `code=50301/50302`）——
 *      **不是错误**，绝不能弹红色提示，也**不能**把内容藏起来；
 *   2. 必须显示 **`degraded_reason`**：只说「降级」而不说「为什么」，
 *      用户无法判断要不要继续；
 *   3. **显式 `mode=keyword` 不算降级**（用户主动要的就是词法检索，标降级是误报）——
 *      该判断由调用方负责（`search.ts` 的口径 ②），本组件只负责展示。
 */
withDefaults(
  defineProps<{
    degraded: boolean
    reason?: string | null
    /** 实际使用的模式（如 `keyword`）。 */
    mode?: string | null
  }>(),
  { reason: null, mode: null },
)
</script>

<style scoped>
.degraded {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  background: var(--warn-weak);
  border: 1px solid var(--warn-line);
  color: #7d5209;
  border-radius: var(--radius);
  padding: 8px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  margin-bottom: 12px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--warn);
  flex: 0 0 auto;
}
.degraded b {
  font-weight: 600;
}
.reason {
  color: #8a6519;
}
.mode {
  font-family: var(--mono);
  font-size: 11.5px;
  background: #fff;
  border: 1px solid var(--warn-line);
  border-radius: 3px;
  padding: 0 5px;
}
.tip {
  color: #9c7c39;
  font-size: 11.5px;
}
</style>
