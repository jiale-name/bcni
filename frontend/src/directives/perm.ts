/**
 * 按钮级权限指令 `v-perm`（架构 §4.2 的「双层权限」之第二层）。
 *
 * 用法：
 * ```html
 * <el-button v-perm="'tag:create'">新增标签</el-button>
 * <el-button v-perm="['dedup:merge', 'dedup:rollback']">合并</el-button>  <!-- 任一命中即可 -->
 * <el-button v-perm.disable="'privacy:approve'">审批删除</el-button>     <!-- 无权限时置灰而非移除 -->
 * ```
 *
 * 🔴 **这不是安全边界**：
 *    前端隐藏按钮**只影响体验**，真正的裁决是后端 `40301`（接口 §4 明文）。
 *    因此本指令移除按钮的同时，页面**仍必须**处理 40301 —— 用户可能通过
 *    URL / 重放 / 权限在会话中被回收而触达无权限接口。
 *
 * 为什么用 `.disable` 变体：某些动作是「流程的一环」（如审批删除），
 * 按钮直接消失会让人误以为流程坏了；置灰 + tooltip 说明原因更可诊断。
 */

import type { App, Directive, DirectiveBinding } from 'vue'

import { useAuthStore } from '@/store/auth'

function removeEl(el: HTMLElement) {
  // 用占位注释替换，保证后续 v-if / 重渲染时不产生孤立节点
  const placeholder = document.createComment('v-perm')
  el.parentNode?.replaceChild(placeholder, el)
  // 记录占位，便于潜在恢复（当前实现不做动态恢复：权限在同一会话内不变）
  ;(el as unknown as Record<string, unknown>).__permPlaceholder = placeholder
}

const permDirective: Directive<HTMLElement, string | string[] | undefined> = {
  mounted(el: HTMLElement, binding: DirectiveBinding<string | string[] | undefined>) {
    const auth = useAuthStore()
    const need = binding.value
    if (!need) return
    const allowed = auth.hasPerm(need)
    if (allowed) return

    if (binding.modifiers.disable) {
      // 置灰 + 阻断交互；保留可见性以便用户知道「有这个能力但当前无权」
      el.classList.add('is-disabled')
      el.setAttribute('disabled', 'disabled')
      el.setAttribute('aria-disabled', 'true')
      el.setAttribute('title', '当前角色无该操作权限')
      el.style.pointerEvents = 'none'
      el.style.opacity = '0.5'
      return
    }
    removeEl(el)
  },
}

export function setupDirectives(app: App): void {
  app.directive('perm', permDirective)
}

export default permDirective
