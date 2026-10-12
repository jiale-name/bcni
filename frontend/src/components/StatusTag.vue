<template>
  <el-tag
    :type="tagType"
    :effect="effect"
    size="small"
    disable-transitions
    :class="{ 'tag-purple': tone === 'purple' }"
  >
    <span v-if="prefix" class="prefix">{{ prefix }}</span>{{ text }}
  </el-tag>
</template>

<script setup lang="ts">
/**
 * 状态标签（原型 `.tag` 系列）。
 *
 * 🔴 文案来源：**优先后端 `*_name`**（接口 §1.1 要求 `*_code` + `*_name` 同回）。
 *    只有在后端未下发名称时，才用 `fallbackTable` 兜底 —— 这条纪律的目的
 *    是避免前端自建一套中文映射，导致后端改文案后界面不同步。
 *
 * `tone` 由调用方显式给出（组件不猜语义）：同一个数值在不同业务下含义不同，
 * 例如 `status=1` 在「标签」里是「启用」（绿），在「AI 调用」里是「成功」（绿），
 * 但在「隐私请求」里是「已通过」（绿）—— 看起来相同，但 **0 的语义差异极大**
 * （`0` 在标签是「待审核」（黄）、在调用日志是「失败」（红））。
 *
 * ⚠️ `purple` 是**本项目自定义色调**（用于「合并」来源 / 云端部署 / 标签来源=AI 等
 *    需要与「成功 / 警告 / 失败 / 信息」都区分开的语义）。Element Plus 的 `el-tag`
 *    只有 primary/success/warning/danger/info 五种 `type`，**没有 purple** ——
 *    故此处用 `tag-purple` 类叠加 `tokens.css` 的 `--purple*` 变量，
 *    而 `type` 传 `undefined` 避免 EP 的默认底色盖住自定义色。
 */
import { computed } from 'vue'

type Tone = 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'plain' | 'purple'

const props = withDefaults(
  defineProps<{
    text: string | number | null | undefined
    tone?: Tone
    prefix?: string
    effect?: 'dark' | 'light' | 'plain'
  }>(),
  { tone: 'plain', prefix: '', effect: 'light' },
)

const tagType = computed(() => {
  const map: Record<Tone, 'primary' | 'success' | 'warning' | 'danger' | 'info' | undefined> = {
    primary: 'primary',
    success: 'success',
    warning: 'warning',
    danger: 'danger',
    info: 'info',
    plain: undefined,
    // 自定义色：走 CSS 类，不走 EP 的 type
    purple: undefined,
  }
  return map[props.tone]
})
</script>

<style scoped>
.prefix {
  margin-right: 2px;
  opacity: 0.8;
}
/* 自定义紫色色调（EP 无 purple type）：底色/描边/文字全部取自 tokens.css */
.el-tag.tag-purple {
  background: var(--purple-weak);
  border-color: var(--purple-line);
  color: var(--purple);
}
</style>
