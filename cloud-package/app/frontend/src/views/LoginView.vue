<template>
  <div class="login-wrap">
    <div class="login-grid">
      <!-- ============ 左：登录表单 ============ -->
      <div class="login-card">
        <div class="lg">
          <span class="dot">才</span>
          <div>
            <h1>AI 数字化人才平台</h1>
            <div class="pjt">2026 顶配版 · 一期（档案与数字画像）</div>
          </div>
        </div>

        <!-- 错误态：按 code 区分文案（原型 P-1 的三种失败态） -->
        <el-alert
          v-if="errorMsg"
          :type="errorType"
          :closable="false"
          show-icon
          class="errline"
        >
          {{ errorMsg }}<code v-if="errorCode">（code {{ errorCode }}）</code>
        </el-alert>

        <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent>
          <el-form-item prop="username">
            <template #label>登录账号 <span class="req">*</span></template>
            <el-input
              v-model="form.username"
              placeholder="请输入账号"
              autocomplete="username"
              :disabled="locked"
              @keyup.enter="onSubmit"
            />
          </el-form-item>
          <el-form-item prop="password">
            <template #label>密码 <span class="req">*</span></template>
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="请输入密码"
              autocomplete="current-password"
              :disabled="locked"
              @keyup.enter="onSubmit"
            />
          </el-form-item>
          <el-form-item prop="tenant_code">
            <template #label>租户编码</template>
            <el-input
              v-model="form.tenant_code"
              placeholder="多租户预留，一期留空即默认租户"
              :disabled="locked"
              @keyup.enter="onSubmit"
            />
          </el-form-item>

          <el-button
            class="btn-login"
            type="primary"
            size="large"
            :loading="submitting"
            :disabled="locked"
            @click="onSubmit"
          >
            登 录
          </el-button>
        </el-form>

        <div class="login-foot">
          {{ lockHint }}<br />
          支持对接甲方 SSO（OIDC / CAS 适配层预留，一期不实现）<br />
          密码 bcrypt 存储 · 传输依赖 HTTPS
        </div>

        <!--
          🔧 开发环境测试账号（2026-09-23 加，为联调便利）

          🔴 安全边界：`DEV_TEST_ACCOUNTS` 由 `import.meta.env.DEV` 守卫 ——
             `npm run build` 时 Vite 把该表达式**静态替换为 `false`**，
             整个数组连同明文密码被 DCE 从产物中移除，本区块随之永不渲染。
             回归方式见 `<script setup>` 顶部注释（构建后 grep dist）。
        -->
        <div v-if="DEV_TEST_ACCOUNTS.length" class="dev-accounts">
          <div class="da-head">
            <span class="da-tag">DEV</span>
            <span>测试账号 · 仅开发环境可见</span>
          </div>
          <div v-for="acc in DEV_TEST_ACCOUNTS" :key="acc.username" class="da-row">
            <div class="da-info">
              <code>{{ acc.username }}</code>
              <span class="da-sep">/</span>
              <code>{{ acc.password }}</code>
              <span class="da-role">{{ acc.role }}</span>
            </div>
            <el-button link type="primary" size="small" :disabled="locked" @click="fillAccount(acc)">
              填入
            </el-button>
          </div>
        </div>
      </div>

      <!-- ============ 右：说明 ============ -->
      <div class="login-aside">
        <h4>一期范围</h4>
        <ul>
          <li><i>①</i><span>档案与数字画像模块（F-3）全链：导入 → 抽取 → 复核 → 标签 → 检索问答</span></li>
          <li><i>②</i><span>AI 中台：模型网关（主备路由 + 熔断）、Prompt 版本、调用日志与成本看板</span></li>
          <li><i>③</i><span>Agent 平台：图运行时 + 人工介入待办（HITL）</span></li>
          <li><i>④</i><span>系统基座：RBAC 三层权限、数据域裁剪、审计留痕、限流降级</span></li>
        </ul>
        <div class="login-badge">🔒 到期令牌自动续期；续期失败请重新登录</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * P-1 · 登录（`/login`，F-1.2，API-01）
 *
 * 🔴 四种失败态的**文案与动作**必须区分（原型 P-1 状态覆盖 + 接口 §1.5）：
 *   | code | 含义 | 展示 | 下一步 |
 *   | --- | --- | --- | --- |
 *   | 40102 | 账号或密码错误 | 红色警示 | 提示剩余尝试次数，可重试 |
 *   | 40103 | 账号被锁定 | 橙色警示 | 显示 `data.locked_seconds` 剩余秒数，**禁用提交** |
 *   | 40301 | 账号被停用 | 红色警示 | 提示联系管理员（ADM） |
 *   | 40001 | 参数校验失败 | 红色警示 | 提示字段问题 |
 *
 * ⚠️ **不在本页做「连续 5 次锁定」计数** —— 锁定由**服务端**裁决（Redis 计数），
 *    前端本地计数既不可信（换浏览器即绕过），也会与后端状态不一致。
 *    前端只用 `locked_seconds` 做「倒计时禁用」的**表现层**处理。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { ApiError } from '@/api/http'
import { ErrorCode } from '@/api/types'
import { useAuthStore } from '@/store/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const formRef = ref<FormInstance>()
const form = ref({ username: '', password: '', tenant_code: '' })
const submitting = ref(false)
const errorMsg = ref('')
const errorCode = ref<number | null>(null)
const errorType = ref<'error' | 'warning'>('error')

/** 锁定倒计时（秒）；>0 时禁用提交。 */
const lockedSeconds = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

const locked = computed(() => lockedSeconds.value > 0)
const lockHint = computed(() =>
  locked.value
    ? `账号已锁定，请 ${lockedSeconds.value} 秒后重试（F-1.2）`
    : '连续 5 次失败将锁定 10 分钟（F-1.2）',
)

const rules: FormRules = {
  username: [
    { required: true, message: '请输入登录账号', trigger: 'blur' },
    { min: 3, max: 64, message: '账号长度 3~64 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 64, message: '密码长度 8~64 字符', trigger: 'blur' },
  ],
}

/**
 * 🔧 **开发环境测试账号**（登录页便利功能，2026-09-23 加）。
 *
 * 🔴 **安全边界（改动前必读）**
 *   整个数组由 `import.meta.env.DEV` 守卫：
 *   - `npm run dev` → 该表达式为 `true`，页面显示并支持「一键填入」；
 *   - `npm run build` → Vite **静态替换为 `false`**，`false ? [...] : []`
 *     经常量折叠后只剩 `[]`，**明文密码字符串被 DCE 从产物中彻底移除**。
 *
 *   ⚠️ **禁止**把守卫换成运行时判断（如 `location.hostname === 'localhost'`）——
 *      那会把密码**真正打进生产 bundle**，只是不显示而已，性质完全不同。
 *   ⚠️ **禁止**把模板里的 `v-if` 条件换成非 dev 常量。
 *   ✅ **回归方式**：`npm run build:only` 后执行
 *      `grep -r "Admin@12345" dist/` —— **必须无命中**。
 *
 * 凭据与 `backend/scripts/seed_dev.py --username admin --password 'Admin@12345'`
 * 保持一致（批次 18 已重置为文档示例值）。
 */
interface DevAccount {
  username: string
  password: string
  role: string
}

const DEV_TEST_ACCOUNTS: DevAccount[] = import.meta.env.DEV
  ? [{ username: 'admin', password: 'Admin@12345', role: 'ADM · 系统管理员' }]
  : []

/** 一键填入测试账号（顺带清掉上一轮可能残留的校验红字）。 */
function fillAccount(acc: DevAccount): void {
  form.value.username = acc.username
  form.value.password = acc.password
  form.value.tenant_code = ''
  formRef.value?.clearValidate()
}

function startLockCountdown(seconds: number): void {
  lockedSeconds.value = Math.max(0, Math.floor(seconds))
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    lockedSeconds.value -= 1
    if (lockedSeconds.value <= 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  }, 1000)
}

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

async function onSubmit(): Promise<void> {
  if (locked.value) return
  const formEl = formRef.value
  if (!formEl) return
  const valid = await formEl.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  errorMsg.value = ''
  errorCode.value = null
  errorType.value = 'error'
  try {
    await auth.login(
      form.value.username.trim(),
      form.value.password,
      form.value.tenant_code.trim() || undefined,
    )
    ElMessage({ type: 'success', message: `欢迎回来，${auth.displayName}` })
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } catch (e) {
    if (e instanceof ApiError) {
      errorCode.value = e.code
      errorMsg.value = e.displayMessage
      if (e.code === ErrorCode.ACCOUNT_LOCKED) {
        errorType.value = 'warning'
        const data = e.data as { locked_seconds?: number } | null
        startLockCountdown(data?.locked_seconds ?? 600)
      } else if (e.code === ErrorCode.FORBIDDEN) {
        errorMsg.value = '账号已停用，请联系管理员（ADM）'
      }
    } else {
      errorMsg.value = '登录失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: linear-gradient(135deg, #eaf3f4 0%, #f7fafa 46%, #e5f2f4 100%);
}
.login-grid {
  display: grid;
  grid-template-columns: minmax(0, 460px) minmax(0, 400px);
  gap: 20px;
  align-items: start;
  width: min(900px, 100%);
}
.login-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow-md);
  padding: 32px 30px;
}
.lg {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 20px;
}
.lg .dot {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #168c92;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}
.lg h1 {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.25;
}
.lg .pjt {
  font-size: 11.5px;
  color: var(--txt-3);
  margin-top: 2px;
}
.errline {
  margin-bottom: 14px;
}
.req {
  color: var(--danger);
  margin-left: 2px;
}
.btn-login {
  width: 100%;
  margin-top: 4px;
  letter-spacing: 2px;
}
.login-foot {
  margin-top: 18px;
  font-size: 11.5px;
  color: var(--txt-3);
  line-height: 1.75;
  border-top: 1px solid var(--line-2);
  padding-top: 12px;
}
/* 🔧 开发环境测试账号区块（随功能同步；生产构建不入包，见 <script setup> 顶部注释）。
   用 warn（琥珀）系而非主色 —— 视觉上明确区分「临时便利内容」与「正式登录信息」。 */
.dev-accounts {
  margin-top: 14px;
  padding: 11px 13px 9px;
  border: 1px dashed var(--warn-line);
  border-radius: 8px;
  background: var(--warn-weak);
}
.da-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11.5px;
  color: var(--txt-2);
  margin-bottom: 6px;
}
.da-tag {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.6px;
  line-height: 1.5;
  color: #fff;
  background: var(--warn);
  border-radius: 4px;
  padding: 1px 5px;
}
.da-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 3px 0;
}
.da-info {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.da-info code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: var(--txt-2);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 6px;
}
.da-sep {
  font-size: 11.5px;
  color: var(--txt-3);
}
.da-role {
  font-size: 11px;
  color: var(--txt-3);
}
.login-aside {
  background: linear-gradient(160deg, var(--primary), var(--primary-deep));
  color: #fff;
  border-radius: 12px;
  padding: 26px 24px;
  box-shadow: var(--shadow-md);
}
.login-aside h4 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 14px;
}
.login-aside ul {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 11px;
  font-size: 12.5px;
  color: rgba(255, 255, 255, 0.88);
  line-height: 1.65;
}
.login-aside li {
  display: flex;
  gap: 8px;
}
.login-aside li i {
  font-style: normal;
  opacity: 0.75;
}
.login-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 11.5px;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 14px;
  padding: 4px 11px;
  margin-top: 18px;
}

@media (max-width: 1080px) {
  .login-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .login-aside {
    display: none;
  }
}
</style>
