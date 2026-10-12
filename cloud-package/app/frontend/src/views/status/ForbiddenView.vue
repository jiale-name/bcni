<template>
  <div class="wrap">
    <div class="big">🔒</div>
    <h4>无访问权限</h4>
    <p>
      当前账号（{{ auth.displayName }} · {{ auth.roles.join('/') || '无角色' }}）无权访问该页面。
    </p>
    <p class="hint">
      🔴 按接口口径，**越权与不存在一律 40301**（不返回 40401）—— 这样对方无法通过状态码探测资源是否存在。
    </p>
    <div class="acts">
      <el-button @click="goBack">返回上一页</el-button>
      <el-button type="primary" @click="goHome">回到工作台</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 403 页（路由级角色不满足时的落点）。
 *
 * 🔴 为什么**不静默跳回首页**：静默跳转会让人以为「点了没反应」，
 *    而权限问题恰恰最需要**显式反馈**（谁、什么角色、被拒的是哪个路径），
 *    否则管理员无法定位是「角色没配」还是「链接过期」。
 */
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/store/auth'

const router = useRouter()
const auth = useAuthStore()

function goBack(): void {
  if (window.history.length > 1) router.back()
  else void router.replace('/dashboard')
}
function goHome(): void {
  void router.replace('/dashboard')
}
</script>

<style scoped>
.wrap {
  max-width: 620px;
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
}
h4 {
  font-size: 16px;
  font-weight: 600;
  color: var(--txt);
  margin-top: 12px;
}
p {
  font-size: 13px;
  color: var(--txt-2);
  margin-top: 8px;
  line-height: 1.7;
}
.hint {
  font-size: 12px;
  color: var(--txt-3);
  background: var(--line-2);
  border-radius: var(--radius);
  padding: 9px 12px;
  margin-top: 14px;
  text-align: left;
}
.acts {
  margin-top: 20px;
  display: flex;
  gap: 9px;
  justify-content: center;
}
</style>
