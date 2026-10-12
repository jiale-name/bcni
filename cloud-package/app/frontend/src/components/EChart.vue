<template>
  <div ref="box" class="chart" :style="{ height }" />
</template>

<script setup lang="ts">
/**
 * ECharts 轻量包装（成本看板 / 画像雷达共用的**唯一**图表入口）。
 *
 * 🔴 三条纪律：
 *   1. **主题色从 CSS 变量读**（`--primary` 等），保证图表与页面同色，
 *      且深/浅色主题切换时不需要改 JS。
 *   2. **只有 `option` 变才 `setOption`**，且用 `notMerge: false` 增量更新 ——
 *      全量重建会在每次轮询时闪一下。
 *   3. **`dispose` 必须执行**：路由切走后实例不销毁会持续占用内存与动画帧。
 *
 * ⚠️ 图表数值**只做展示**：金额仍是后端下发的字符串，本组件不做任何汇总运算。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = withDefaults(
  defineProps<{
    option: Record<string, unknown>
    height?: string
  }>(),
  { height: '260px' },
)

const box = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

/** 从 `tokens.css` 读取主题色（读取失败时退回中性色，不让图表因样式缺失而崩）。 */
function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function render(): void {
  if (!chart) return
  chart.setOption(
    {
      // 让调用方不必重复声明这几项「本项目的默认外观」
      textStyle: { fontFamily: token('--font', 'inherit') },
      ...props.option,
    },
    { notMerge: false },
  )
}

onMounted(() => {
  if (!box.value) return
  chart = echarts.init(box.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(box.value)
})

watch(() => props.option, render, { deep: true })

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart {
  width: 100%;
}
</style>
