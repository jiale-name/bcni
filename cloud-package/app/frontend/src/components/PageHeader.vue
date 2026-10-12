<template>
  <div class="page">
    <div class="page-head">
      <div class="left">
        <h2>{{ title }}</h2>
        <div v-if="desc" class="desc">{{ desc }}</div>
      </div>
      <div class="right">
        <slot name="actions" />
      </div>
    </div>
    <slot />
  </div>
</template>

<script setup lang="ts">
/**
 * 页面容器：标题 + 说明 + 操作区。
 *
 * 说明文案（`desc`）用于承载**页面级口径**（如「本页是结构化筛选，不是语义检索」），
 * 原型里这类提示反复出现且价值很高 —— 它把「接口语义」前置到界面上，
 * 减少「用了错的入口」导致的无效操作。
 */
defineProps<{
  title: string
  desc?: string
}>()
</script>

<style scoped>
.page {
  display: block;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.page-head h2 {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 0.2px;
}
.desc {
  font-size: 12.5px;
  color: var(--txt-3);
  margin-top: 4px;
  line-height: 1.6;
  max-width: 900px;
}
.right {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
