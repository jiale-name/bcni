<template>
  <div class="stat" :class="tone">
    <div class="k">{{ label }}<span v-if="hint" class="hint">ⓘ</span></div>
    <div class="v num">
      {{ value }}<small v-if="unit">{{ unit }}</small>
    </div>
    <div v-if="delta" class="d" :class="deltaTone">{{ delta }}</div>
    <div v-else-if="sub" class="d">{{ sub }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 指标卡（原型 `.stat`）。
 *
 * ⚠️ 配色遵循**中国股市惯例之外的中性语义**：这里的 `up/down` 是**业务涨跌**，
 * 由调用方显式传入 tone，组件不自行推断（避免「成本上升」被染成红色还是绿色产生歧义）。
 */
withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    sub?: string
    delta?: string
    /** 涨跌色调：`up` / `down`，缺省中性。 */
    deltaTone?: 'up' | 'down'
    tone?: 'default' | 'ok' | 'warn' | 'danger' | 'purple'
    hint?: boolean
  }>(),
  { tone: 'default', deltaTone: 'up', unit: '', sub: '', delta: '', hint: false },
)
</script>

<style scoped>
.stat {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 14px 15px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}
.stat::after {
  content: '';
  position: absolute;
  right: -24px;
  top: -24px;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--primary-weak);
  opacity: 0.55;
}
.stat.ok::after {
  background: var(--ok-weak);
}
.stat.warn::after {
  background: var(--warn-weak);
}
.stat.danger::after {
  background: var(--danger-weak);
}
.stat.purple::after {
  background: var(--purple-weak);
}
.k {
  font-size: 12px;
  color: var(--txt-2);
  position: relative;
  display: flex;
  align-items: center;
  gap: 5px;
}
.k .hint {
  color: var(--txt-3);
  font-size: 11px;
}
.v {
  font-size: 25px;
  font-weight: 700;
  letter-spacing: -0.5px;
  margin-top: 5px;
  position: relative;
  font-variant-numeric: tabular-nums;
}
.v small {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--txt-3);
  margin-left: 3px;
}
.d {
  font-size: 11.5px;
  color: var(--txt-3);
  margin-top: 4px;
  position: relative;
}
.d.up {
  color: var(--danger);
}
.d.down {
  color: var(--ok);
}
</style>
