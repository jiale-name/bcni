<template>
  <div>
    <PageHeader title="档案 RAG 问答" :desc="desc">
      <template #actions>
        <el-button :disabled="streaming" @click="onNewSession">
          <el-icon><Refresh /></el-icon>新会话
        </el-button>
      </template>
    </PageHeader>

    <!-- 🔴 拒答是「确定性判定」，不是模型偷懒 -->
    <el-alert type="info" :closable="false" show-icon class="mb12">
      <template #title>
        回答<b>只能基于你可见范围内的档案片段</b>，并附引用编号。若检索不到依据，服务端会
        <b>直接拒答</b>（确定性判定，不调用模型）——这是防幻觉设计，不是故障。
      </template>
    </el-alert>

    <!-- API-64 ~ API-66 未就绪声明 -->
    <el-alert v-if="!ASK_SESSION_API_READY" type="warning" :closable="false" show-icon class="mb12">
      <template #title>会话历史回看 / 清空 / 脱敏自检接口尚未交付</template>
      <template #default>
        API-64 ~ API-66（会话历史、清空会话、脱敏自检）在评审台账 §7.1 中<b>无 CU 认领</b>，
        实测 <code class="mono">openapi.json</code> 不存在。本页因此只在<b>本次会话内存中</b>保留上下文，
        刷新页面即丢失；多轮上下文仍由服务端按 <code class="mono">session_id</code> 维持。
      </template>
    </el-alert>

    <div class="qa-layout">
      <!-- ============ 左：提问区 ============ -->
      <el-card shadow="never" class="left">
        <template #header>
          <div class="card-head">
            <span class="hd"><i class="bar" />提问范围</span>
          </div>
        </template>

        <div class="scope-form">
          <div class="field">
            <label>查询范围</label>
            <el-radio-group v-model="scopeType" :disabled="streaming" @change="onScopeChange">
              <el-radio-button value="all">全部（按我的数据域）</el-radio-button>
              <el-radio-button value="orgs">指定部门</el-radio-button>
              <el-radio-button value="talents">指定档案</el-radio-button>
            </el-radio-group>
          </div>

          <div v-if="scopeType === 'orgs'" class="field">
            <label>部门</label>
            <el-tree-select
              v-model="scopeOrgIds"
              :data="orgTree"
              :props="{ label: 'dept_name', value: 'id', children: 'children' }"
              node-key="id"
              multiple
              show-checkbox
              check-strictly
              collapse-tags
              collapse-tags-tooltip
              :render-after-expand="false"
              placeholder="选择部门"
              style="width: 100%"
              :disabled="streaming || !auth.hasPerm('org:view')"
            />
            <span v-if="!auth.hasPerm('org:view')" class="muted xs">
              你的角色没有 <code class="mono">org:view</code>，无法指定部门范围。
            </span>
          </div>

          <div v-if="scopeType === 'talents'" class="field">
            <label>档案 ID</label>
            <div class="talents-box">
              <el-tag
                v-for="id in scopeTalentIds"
                :key="id"
                closable
                size="small"
                :disable-transitions="streaming"
                @close="removeTalent(id)"
              >
                <span class="mono xs">{{ id }}</span>
              </el-tag>
              <span v-if="!scopeTalentIds.length" class="muted xs">尚未指定档案</span>
            </div>
            <span class="muted xs">
              从档案列表 / 详情 / 语义检索的「问答」入口进入时会自动带入。
              你也可以直接粘贴 ID（<b>字符串形态，不要转成数字</b>）。
            </span>
            <div class="id-input">
              <el-input
                v-model="talentIdInput"
                placeholder="粘贴档案 ID（雪花 ID 字符串）"
                :disabled="streaming"
                @keyup.enter="addTalent"
              />
              <el-button :disabled="streaming" @click="addTalent">添加</el-button>
            </div>
          </div>

          <div class="field">
            <label>检索条数 top_k</label>
            <el-input-number v-model="topK" :min="1" :max="20" :disabled="streaming" />
          </div>
        </div>

        <div class="scope-hint">
          <el-icon><Lock /></el-icon>
          <span class="xs">
            范围只能<b>收窄</b>你的数据域；超出部分由服务端裁剪并在下方提示裁剪数量。
          </span>
        </div>
      </el-card>

      <!-- ============ 右：对话区 ============ -->
      <el-card shadow="never" class="right">
        <template #header>
          <div class="card-head">
            <span class="hd">
              <i class="bar" />对话
              <span v-if="sessionId" class="sub">
                会话 <code class="mono">{{ shortSession }}</code>
              </span>
            </span>
            <span v-if="streaming" class="muted xs">正在生成…</span>
          </div>
        </template>

        <DegradedBadge :degraded="degraded" :reason="degradedReason" />

        <!-- 范围裁剪提示（服务端下发） -->
        <el-alert
          v-if="clipped"
          type="warning"
          :closable="false"
          show-icon
          class="mb12"
        >
          <template #title>
            部分档案超出你的可见范围，已被裁剪（{{ clippedCount }} 条）
          </template>
        </el-alert>

        <!-- 错误态（含握手期 40301 等） -->
        <el-alert v-if="errorMsg" type="error" :closable="false" show-icon class="mb12">
          <template #title>问答失败</template>
          <template #default>
            {{ errorMsg }}
            <span v-if="errorTrace" class="muted xs">｜trace_id: {{ errorTrace }}</span>
          </template>
        </el-alert>

        <!-- 消息流 -->
        <div ref="scrollBox" class="messages">
          <div v-if="messages.length === 0" class="empty-chat">
            <div class="big">◌</div>
            <h4>还没有对话</h4>
            <p class="muted sm">
              试试问：「哪些人有 5 年以上机器学习经验？」「本部门的硕士占比如何？」
            </p>
          </div>

          <template v-for="(m, i) in messages" :key="i">
            <!-- 用户 -->
            <div v-if="m.role === 'user'" class="msg user">
              <div class="bubble">{{ m.content }}</div>
            </div>

            <!-- 助手 -->
            <div v-else class="msg assistant">
              <!-- 拒答（确定性判定） -->
              <div v-if="m.refusal" class="refusal">
                <div class="rhead">
                  <el-icon><WarningFilled /></el-icon>
                  <b>已拒答</b>
                  <span class="muted xs">未检索到可支撑回答的档案依据</span>
                </div>
                <div class="rbody">{{ m.refusal.message || m.refusal.reason }}</div>
              </div>

              <!-- 正常回答 -->
              <div v-else class="bubble">
                <span v-if="m.content">{{ m.content }}</span>
                <span v-else-if="m.streaming" class="muted">正在思考…</span>
                <span v-else class="muted">
                  （服务端未返回正文；本轮引用数为
                  {{ m.citationCount ?? 0 }}，请重新提问）
                </span>
              </div>

              <!-- 引用 -->
              <div v-if="m.citations?.length" class="citations">
                <div class="ctitle xs muted">引用来源（{{ m.citations.length }}）</div>
                <div
                  v-for="c in m.citations"
                  :key="c.index"
                  class="citation"
                  @click="openCitation(c)"
                >
                  <span class="idx">[{{ c.index }}]</span>
                  <span class="cname">{{ c.talent_name || c.talent_id }}</span>
                  <span class="cfield muted xs">{{ c.field_name || '片段' }}</span>
                  <span class="csnip ellipsis" :title="c.snippet || ''">{{ c.snippet || '—' }}</span>
                </div>
              </div>

              <div class="meta-line">
                <span v-if="m.mode" class="muted xs">模式：{{ m.mode }}</span>
                <span v-if="m.citationCount !== undefined" class="muted xs">
                  引用 {{ m.citationCount }} 条
                </span>
                <span v-if="m.elapsedMs" class="muted xs">{{ m.elapsedMs }}ms</span>
              </div>
            </div>
          </template>
        </div>

        <!-- 输入区 -->
        <div class="composer">
          <el-input
            v-model="question"
            type="textarea"
            :rows="2"
            resize="none"
            maxlength="500"
            show-word-limit
            :disabled="streaming"
            placeholder="输入问题，Ctrl + Enter 发送"
            @keydown.enter.ctrl="onAsk"
          />
          <div class="composer-actions">
            <el-button
              v-if="streaming"
              type="danger"
              plain
              @click="onAbort"
            >
              <el-icon><CircleClose /></el-icon>停止
            </el-button>
            <el-button
              v-else
              type="primary"
              :disabled="!question.trim()"
              @click="onAsk"
            >
              <el-icon><ChatDotRound /></el-icon>发送
            </el-button>
            <span class="muted xs">
              ⚠️ 流式为<b>整包拆片</b>而非 token 级（已知遗留），长答会分几次出现，属正常。
            </span>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * P-9 · 档案 RAG 问答（`/talent/ask`，F-3.7 / F-3.9，**API-63**）
 *
 * 🔴 本页承载六条契约（`api/search.ts` 口径 ⑤⑥ + `api/sse.ts`）：
 *   1. **必须用 `fetch` 流而非 `EventSource`** —— `EventSource` 无法携带 `Authorization`
 *      头（`api/sse.ts` 已封装 `openSse`）。
 *   2. **全链在握手期跑完**：越权（`40301`）**不能藏在事件流里**，故握手期非 2xx
 *      由 `openSse` 当普通错误上报，本页渲染成 `error` 提示。
 *   3. **`session_id` 由服务端在 `meta` 事件下发**：首次不传，后续轮次带上（多轮上下文）。
 *   4. **`delta` 是整包拆片、不是 token 级**（已知遗留）—— 界面须容忍「一段一段」出现。
 *   5. **拒答是确定性判定**（`refusal` 事件），必须与「加载失败」区分渲染。
 *   6. **`citation_count=0` 的 `done` 不渲染裸回答** —— 无依据的回答等于幻觉。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { orgApi, type OrgNode } from '@/api/org'
import {
  ASK_SESSION_API_READY,
  searchApi,
  type AskCitationEvent,
  type AskRefusalEvent,
  type AskRequest,
  type AskScope,
} from '@/api/search'
import { openSse, type SseEvent } from '@/api/sse'
import DegradedBadge from '@/components/DegradedBadge.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useAuthStore } from '@/store/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const desc =
  '基于档案片段的问答（SSE 流式）。回答受数据域约束、必须带引用；无依据时服务端直接拒答。'

// ---------------- 范围 ----------------
const scopeType = ref<'all' | 'orgs' | 'talents'>('all')
const scopeOrgIds = ref<string[]>([])
const scopeTalentIds = ref<string[]>([])
const talentIdInput = ref('')
const topK = ref(5)

const orgTree = ref<OrgNode[]>([])

// ---------------- 会话状态 ----------------
interface AssistantMsg {
  role: 'assistant'
  content: string
  streaming: boolean
  citations: AskCitationEvent[]
  citationCount?: number
  refusal?: AskRefusalEvent
  mode?: string
  elapsedMs?: number
}
interface UserMsg {
  role: 'user'
  content: string
}
type Msg = UserMsg | AssistantMsg

const messages = ref<Msg[]>([])
const question = ref('')
const streaming = ref(false)
const sessionId = ref<string | null>(null)
const degraded = ref(false)
const degradedReason = ref<string | null>(null)
const clipped = ref(false)
const clippedCount = ref(0)
const errorMsg = ref<string | null>(null)
const errorTrace = ref<string | null>(null)

const scrollBox = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

const shortSession = computed(() =>
  sessionId.value ? `${sessionId.value.slice(0, 8)}…` : '',
)

watch(
  messages,
  async () => {
    await nextTick()
    const el = scrollBox.value
    if (el) el.scrollTop = el.scrollHeight
  },
  { deep: true },
)

function onScopeChange(): void {
  if (scopeType.value !== 'talents') scopeTalentIds.value = []
  if (scopeType.value !== 'orgs') scopeOrgIds.value = []
}

function addTalent(): void {
  const v = talentIdInput.value.trim()
  if (!v) return
  // 🔴 只做「是数字字符串」的形态校验；**绝不** Number(v) 转换（会丢精度）
  if (!/^\d+$/.test(v)) {
    errorMsg.value = '档案 ID 必须是纯数字字符串（雪花 ID）。请从列表/详情页复制，不要手改。'
    return
  }
  errorMsg.value = null
  if (!scopeTalentIds.value.includes(v)) scopeTalentIds.value.push(v)
  scopeType.value = 'talents'
  talentIdInput.value = ''
}

function removeTalent(id: string): void {
  scopeTalentIds.value = scopeTalentIds.value.filter((x) => x !== id)
}

function buildScope(): AskScope {
  if (scopeType.value === 'orgs') {
    return { type: 'orgs', talent_ids: [] }
  }
  if (scopeType.value === 'talents') {
    return { type: 'talents', talent_ids: [...scopeTalentIds.value] }
  }
  return { type: 'all', talent_ids: [] }
}

// ---------------- 提问（SSE） ----------------
function onAsk(): void {
  const q = question.value.trim()
  if (!q || streaming.value) return

  if (scopeType.value === 'talents' && scopeTalentIds.value.length === 0) {
    errorMsg.value = '范围选择「指定档案」时，至少需要指定一个档案 ID。'
    return
  }
  if (scopeType.value === 'orgs' && scopeOrgIds.value.length === 0) {
    errorMsg.value = '范围选择「指定部门」时，至少需要选择一个部门。'
    return
  }

  errorMsg.value = null
  errorTrace.value = null
  clipped.value = false
  clippedCount.value = 0

  messages.value.push({ role: 'user', content: q })
  question.value = ''

  const reply: AssistantMsg = {
    role: 'assistant',
    content: '',
    streaming: true,
    citations: [],
  }
  messages.value.push(reply)
  const idx = messages.value.length - 1

  const payload: AskRequest = {
    scope: buildScope(),
    question: q,
    session_id: sessionId.value,
    stream: true,
    top_k: topK.value,
  }

  // 部门范围通过 filters 附带（若后端以此为入口则生效；否则服务端按数据域裁剪）
  const body: Record<string, unknown> = { ...payload }
  if (scopeType.value === 'orgs' && scopeOrgIds.value.length) {
    body.filters = { org_ids: [...scopeOrgIds.value] }
  }

  streaming.value = true
  // 🔴 **连接代际守卫**（与 `ImportView.startSse` 同款）：每次连接持有自己的 controller，
  //    `isStale()` 判断它是否已被新一轮或 `onAbort()` 取代。没有它的话，上一轮
  //    `abort()` 后**异步**到达的 `onClose` 会把**新一轮**的 `streaming` 置回 false
  //    （用户看到「正在生成」中途消失），`onError` 还会把错误写进新一轮的 `errorMsg`。
  const ctrl = new AbortController()
  controller = ctrl
  const isStale = (): boolean => controller !== ctrl

  void openSse(
    searchApi.askPath(),
    {
      onEvent(evt: SseEvent) {
        if (isStale()) return
        handleEvent(evt, idx)
      },
      onClose() {
        if (isStale()) return
        finishStream(idx)
      },
      onError(err: Error) {
        // 握手期失败（含 40301）会走这里 —— 必须渲染成明确的错误，不静默
        if (isStale()) return
        errorMsg.value = err.message
        finishStream(idx)
      },
    },
    { method: 'POST', body, signal: ctrl.signal },
  )
}

function parse<T>(evt: SseEvent): T | null {
  try {
    return JSON.parse(evt.data) as T
  } catch {
    return null
  }
}

function handleEvent(evt: SseEvent, idx: number): void {
  const msg = messages.value[idx]
  if (!msg || msg.role !== 'assistant') return

  switch (evt.event) {
    case 'meta': {
      const m = parse<{
        session_id: string
        scope_effective?: { clipped?: boolean; clipped_count?: number }
        degraded?: boolean
        degraded_reason?: string | null
      }>(evt)
      if (!m) return
      if (m.session_id) sessionId.value = m.session_id
      if (m.scope_effective?.clipped) {
        clipped.value = true
        clippedCount.value = m.scope_effective.clipped_count ?? 0
      }
      // 🔴 降级标记进入页面级徽标，不弹错误框
      if (m.degraded) {
        degraded.value = true
        degradedReason.value = m.degraded_reason ?? null
      }
      break
    }
    case 'delta': {
      const d = parse<{ text: string }>(evt)
      if (d?.text) msg.content += d.text
      break
    }
    case 'citation': {
      const c = parse<AskCitationEvent>(evt)
      if (c) msg.citations.push(c)
      break
    }
    case 'refusal': {
      const r = parse<AskRefusalEvent>(evt)
      if (r) {
        msg.refusal = r
        msg.content = ''
      }
      break
    }
    case 'done': {
      const d = parse<{
        session_id?: string
        citation_count: number
        elapsed_ms?: number
        degraded?: boolean
      }>(evt)
      if (d) {
        msg.citationCount = d.citation_count
        msg.elapsedMs = d.elapsed_ms
        if (d.session_id) sessionId.value = d.session_id
        if (d.degraded) degraded.value = true
        // 🔴 citation_count=0 且无拒答事件：不渲染裸回答（那是幻觉）
        if (d.citation_count === 0 && !msg.refusal) {
          msg.content = ''
        }
      }
      break
    }
    case 'error': {
      const e = parse<{ code?: number; message?: string }>(evt)
      errorMsg.value = e?.message ? `[${e.code ?? '—'}] ${e.message}` : '服务端在流中返回错误'
      break
    }
    default:
      // 未知事件名：忽略而非报错（服务端未来新增事件不应让旧前端崩掉）
      break
  }
}

function finishStream(idx: number): void {
  const msg = messages.value[idx]
  if (msg && msg.role === 'assistant') {
    msg.streaming = false
    if (msg.content) msg.mode = degraded.value ? 'degraded' : undefined
  }
  streaming.value = false
  controller = null
}

function onAbort(): void {
  controller?.abort()
  // 主动停止不是错误：把已收到的内容保留，标记结束
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant') {
    last.streaming = false
    if (!last.content) last.content = '（已手动停止，本轮未收到内容）'
  }
  streaming.value = false
  controller = null
}

function onNewSession(): void {
  onAbort()
  sessionId.value = null
  messages.value = []
  degraded.value = false
  degradedReason.value = null
  clipped.value = false
  errorMsg.value = null
  errorTrace.value = null
}

function openCitation(c: AskCitationEvent): void {
  void router.push({ name: 'talent-detail', params: { id: c.talent_id } })
}

onMounted(async () => {
  // 从其它页面带入的档案 ID（列表 / 详情 / 语义检索的「问答」入口）
  const preset = route.query.talent_id
  if (typeof preset === 'string' && /^\d+$/.test(preset)) {
    scopeTalentIds.value = [preset]
    scopeType.value = 'talents'
  }

  if (auth.hasPerm('org:view')) {
    try {
      const res = await orgApi.tree({ status: 1 })
      orgTree.value = res.data?.items ?? []
    } catch {
      /* 部门树拉不到只影响「指定部门」这一支 */
    }
  }
})

onUnmounted(() => {
  // 离开页面必须断开连接，否则服务端会一直挂着一个流
  controller?.abort()
  // 置 null 后 `isStale()` 恒为 true ⇒ 断开后**异步**到达的回调不会再改已卸载组件的状态
  controller = null
})
</script>

<style scoped>
.qa-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 1100px) {
  .qa-layout {
    grid-template-columns: 1fr;
  }
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.card-head .hd {
  font-size: 13.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-head .bar {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: var(--primary);
}
.card-head .sub {
  font-weight: 400;
  font-size: 11.5px;
  color: var(--txt-3);
}
.scope-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field > label {
  font-size: 12.5px;
  color: var(--txt-2);
}
.talents-box {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 22px;
}
.id-input {
  display: flex;
  gap: 8px;
}
.scope-hint {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--txt-3);
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}
.messages {
  min-height: 320px;
  max-height: 56vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 2px;
}
.empty-chat {
  text-align: center;
  padding: 48px 0;
}
.empty-chat .big {
  font-size: 30px;
  color: var(--txt-3);
}
.empty-chat h4 {
  margin: 8px 0 4px;
  font-size: 14px;
}
.msg {
  display: flex;
  flex-direction: column;
}
.msg.user {
  align-items: flex-end;
}
.msg .bubble {
  max-width: 82%;
  padding: 9px 13px;
  border-radius: var(--radius-lg);
  font-size: 13.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .bubble {
  background: var(--primary);
  color: #fff;
}
.msg.assistant .bubble {
  background: var(--panel-2, #fafbfd);
  border: 1px solid var(--line);
  color: var(--txt-1);
  max-width: 100%;
}
.refusal {
  border: 1px solid var(--warn-line);
  background: var(--warn-weak);
  border-radius: var(--radius);
  padding: 10px 13px;
}
.refusal .rhead {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #7d5209;
  margin-bottom: 5px;
}
.refusal .rbody {
  font-size: 12.5px;
  color: #8a6519;
  line-height: 1.7;
}
.citations {
  margin-top: 9px;
  border-top: 1px dashed var(--line);
  padding-top: 8px;
}
.citations .ctitle {
  margin-bottom: 5px;
}
.citation {
  display: grid;
  grid-template-columns: 32px 120px 80px 1fr;
  gap: 8px;
  align-items: baseline;
  font-size: 12.5px;
  padding: 3px 0;
  cursor: pointer;
}
.citation:hover .cname {
  color: var(--primary);
  text-decoration: underline;
}
.citation .idx {
  color: var(--primary);
  font-family: var(--mono);
  font-size: 11.5px;
}
.citation .cname {
  font-weight: 500;
}
.citation .csnip {
  color: var(--txt-2);
  max-width: 100%;
}
.meta-line {
  display: flex;
  gap: 12px;
  margin-top: 5px;
  flex-wrap: wrap;
}
.composer {
  margin-top: 14px;
  border-top: 1px solid var(--line);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
