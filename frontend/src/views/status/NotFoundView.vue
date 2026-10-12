<template>
  <div class="wrap">
    <div class="big">◌</div>
    <h4>页面不存在</h4>
    <p>路由 <code class="mono">{{ route.fullPath }}</code> 没有匹配的页面。</p>
    <div class="acts">
      <el-button type="primary" @click="goHome">回到工作台</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 404 页（路由未匹配）。
 *
 * ⚠️ 与「后端 40401」不同：本页是**前端路由**层面的 404。
 *    后端 `40401` 表示「资源确实不存在（含软删）」，由各页面用 `StatePanel` 的
 *    `notfound` 态渲染在**页面内部**，不会走到这里。
 */
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function goHome(): void {
  void router.replace('/dashboard')
}
</script>

<style scoped>
.wrap {
  max-width: 560px;
  margin: 60px auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 40px 28px;
  text-align: center;
}
.big {
  font-size: 40px;
  line-height: 1;
  color: var(--txt-3);
}
h4 {
  font-size: 16px;
  font-weight: 600;
  margin-top: 12px;
}
p {
  font-size: 13px;
  color: var(--txt-2);
  margin-top: 8px;
}
.mono {
  font-family: var(--mono);
  font-size: 12px;
}
.acts {
  margin-top: 20px;
}
</style>
