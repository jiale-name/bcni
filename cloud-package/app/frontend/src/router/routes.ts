/**
 * 路由表（**逐行对齐架构设计文档 §4.2「前端页面切片」**）。
 *
 * | 架构 §4.2 # | 页面 | 路由 | 可见角色 |
 * | :-: | --- | --- | --- |
 * | 1 | 登录 | `/login` | 全部 |
 * | 2 | 工作台首页 | `/dashboard` | 全部 |
 * | 3 | 组织架构管理 | `/system/org` | ADM, HR |
 * | 4 | 用户管理 | `/system/user` | ADM |
 * | 5 | 角色与权限 | `/system/role` | ADM |
 * | 6 | 数据字典与配置 | `/system/config` | ADM |
 * | 7 | 审计日志 | `/system/audit-log` | ADM, AUDIT |
 * | 8 | 人才档案列表 | `/talent/list` | ADM, HR, MGR |
 * | 9 | 人才档案详情 | `/talent/detail/:id` | ADM, HR, MGR, EMP |
 * | 10 | 批量导入与任务进度 | `/talent/import` | ADM, HR |
 * | 11 | 字段复核工作台 | `/talent/review` | ADM, HR |
 * | 12 | 重复档案合并 | `/talent/dedup` | ADM, HR |
 * | 13 | 语义人才检索 | `/talent/search` | ADM, HR, MGR |
 * | 14 | 档案 RAG 问答 | `/talent/ask` | ADM, HR, MGR, EMP |
 * | 15 | 标签字典管理 | `/talent/tag-dict` | ADM, HR |
 * | 16 | **档案变更历史** | `/talent/change-log` | ADM, HR, AUDIT |
 * | 17 | 我的画像 / 个人权利 | `/my/profile` | EMP（本人） |
 * | 18 | 模型配置 / Prompt / AI 日志 | `/ai/model` `/ai/prompt` `/ai/call-log` | AIOPS, ADM |
 * | 19 | Agent 任务与人工介入待办 | `/ai/agent-task` | ADM, HR, AIOPS |
 *
 * 权限**双层**（架构 §4.2 末注）：路由级 `meta.roles` + 按钮级 `v-perm`。
 * 🔴 两者都只是**体验层**；最终裁决是后端 `40301`，故每个页面都必须能渲染「无权限」态。
 *
 * ⚠️ 与原型的关系（**数字已用脚本数过**）：
 *    - 原型 `prototype/p01~p19` 共 **19 页**，其中 P-16 把「模型配置」与「Prompt 管理」
 *      **合成一页**；CU-23 按架构 §4.2 拆成 `/ai/model` + `/ai/prompt` 两条路由。
 *    - 原型**没有**「档案变更历史」页（P-8 把变更时间线内嵌在详情里）；架构 §4.2 第 16 行
 *      列了独立路由，且后端 API-67 ~ API-70 已成体系，故 CU-23 **补出该页**（P-20）。
 *    - ⇒ 业务路由 = 19 − 1（P-16 合并）+ 1（P-16 拆分增页）+ 1（变更历史）= **21 条**；
 *      再加 `/403`、`/404` 两个状态页 = **23 条具名路由**，对应 **23 个页面文件**。
 */

import type { RouteRecordRaw } from 'vue-router'

/**
 * 路由 `meta` 约定。
 *
 * 🔴 必须带**索引签名**，且键类型必须是 `PropertyKey`（= `string | number | symbol`）：
 *    vue-router 的 `RouteRecordRaw.meta` 断言为 `Record<PropertyKey, unknown>`。
 *    - 不加索引签名 → 每条路由报「Index signature is missing」；
 *    - 只加 `Record<string, unknown>` → 仍报「Index signature for type 'symbol' is missing」
 *      （23 条路由 = 23 个 TS2322）。
 *    故此处用**交叉类型**而非 `extends`：`interface X extends Record<PropertyKey, unknown>`
 *    虽也能通过，但类型别名写法对 `symbol` 索引签名的推导更稳。
 */
export type RouteMeta = Record<PropertyKey, unknown> & {
  /** 浏览器标题与顶栏标题。 */
  title: string
  /** 面包屑（从模块到页面）。 */
  breadcrumb?: string[]
  /** 可见角色；空/缺省 = 全部登录用户。 */
  roles?: string[]
  /** 侧栏图标（Element Plus 图标组件名）。 */
  icon?: string
  /** 侧栏分组。 */
  group?: 'workbench' | 'talent' | 'ai' | 'system' | 'none'
  /** 是否在侧栏隐藏（详情页等）。 */
  hidden?: boolean
  /** 免登录。 */
  public?: boolean
  /** 额外权限码（同时满足角色与权限才显示菜单；路由守卫只校验角色）。 */
  perm?: string
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true, hidden: true, group: 'none' } as RouteMeta,
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '工作台首页', breadcrumb: ['工作台'], icon: 'HomeFilled', group: 'workbench' } as RouteMeta,
      },

      // ---------------- 人才档案 ----------------
      {
        path: 'talent/list',
        name: 'talent-list',
        component: () => import('@/views/talent/TalentListView.vue'),
        meta: {
          title: '人才档案列表',
          breadcrumb: ['人才档案', '档案列表'],
          roles: ['ADM', 'HR', 'MGR'],
          icon: 'Tickets',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/detail/:id',
        name: 'talent-detail',
        component: () => import('@/views/talent/TalentDetailView.vue'),
        meta: {
          title: '人才档案详情',
          breadcrumb: ['人才档案', '档案详情'],
          roles: ['ADM', 'HR', 'MGR', 'EMP'],
          hidden: true,
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/search',
        name: 'talent-search',
        component: () => import('@/views/talent/SearchView.vue'),
        meta: {
          title: '语义人才检索',
          breadcrumb: ['人才档案', '语义检索'],
          roles: ['ADM', 'HR', 'MGR'],
          icon: 'Aim',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/ask',
        name: 'talent-ask',
        component: () => import('@/views/talent/AskView.vue'),
        meta: {
          title: '档案 RAG 问答',
          breadcrumb: ['人才档案', 'RAG 问答'],
          roles: ['ADM', 'HR', 'MGR', 'EMP'],
          icon: 'ChatDotRound',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/import',
        name: 'talent-import',
        component: () => import('@/views/talent/ImportView.vue'),
        meta: {
          title: '批量导入与任务进度',
          breadcrumb: ['人才档案', '批量导入'],
          roles: ['ADM', 'HR'],
          icon: 'UploadFilled',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/review',
        name: 'talent-review',
        component: () => import('@/views/talent/ReviewView.vue'),
        meta: {
          title: '字段复核工作台',
          breadcrumb: ['人才档案', '字段复核'],
          roles: ['ADM', 'HR'],
          icon: 'EditPen',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/dedup',
        name: 'talent-dedup',
        component: () => import('@/views/talent/DedupView.vue'),
        meta: {
          title: '重复档案合并',
          breadcrumb: ['人才档案', '重复合并'],
          roles: ['ADM', 'HR'],
          icon: 'CopyDocument',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/tag-dict',
        name: 'talent-tag-dict',
        component: () => import('@/views/talent/TagDictView.vue'),
        meta: {
          title: '标签字典管理',
          breadcrumb: ['人才档案', '标签字典'],
          roles: ['ADM', 'HR'],
          icon: 'Collection',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'talent/change-log',
        name: 'talent-change-log',
        component: () => import('@/views/talent/ChangeLogView.vue'),
        meta: {
          title: '档案变更历史',
          breadcrumb: ['人才档案', '变更历史'],
          roles: ['ADM', 'HR', 'AUDIT'],
          icon: 'Clock',
          group: 'talent',
        } as RouteMeta,
      },
      {
        path: 'my/profile',
        name: 'my-profile',
        component: () => import('@/views/my/MyProfileView.vue'),
        meta: {
          title: '我的画像 / 个人权利',
          breadcrumb: ['我的画像'],
          roles: ['EMP'],
          icon: 'User',
          group: 'talent',
          perm: 'privacy:self_view',
        } as RouteMeta,
      },

      // ---------------- AI 中台 ----------------
      {
        path: 'ai/model',
        name: 'ai-model',
        component: () => import('@/views/ai/ModelView.vue'),
        meta: {
          title: '模型配置与路由',
          breadcrumb: ['AI 中台', '模型与路由'],
          roles: ['ADM', 'AIOPS'],
          icon: 'Setting',
          group: 'ai',
        } as RouteMeta,
      },
      {
        path: 'ai/prompt',
        name: 'ai-prompt',
        component: () => import('@/views/ai/PromptView.vue'),
        meta: {
          title: 'Prompt 版本管理',
          breadcrumb: ['AI 中台', 'Prompt 管理'],
          roles: ['ADM', 'AIOPS'],
          icon: 'Document',
          group: 'ai',
        } as RouteMeta,
      },
      {
        path: 'ai/call-log',
        name: 'ai-call-log',
        component: () => import('@/views/ai/CallLogView.vue'),
        meta: {
          title: 'AI 调用日志与成本看板',
          breadcrumb: ['AI 中台', '调用日志与成本'],
          roles: ['ADM', 'AIOPS', 'AUDIT'],
          icon: 'DataAnalysis',
          group: 'ai',
        } as RouteMeta,
      },
      {
        path: 'ai/agent-task',
        name: 'ai-agent-task',
        component: () => import('@/views/ai/AgentTaskView.vue'),
        meta: {
          title: 'Agent 任务与人工待办',
          breadcrumb: ['AI 中台', 'Agent 任务'],
          roles: ['ADM', 'HR', 'AIOPS'],
          icon: 'Flag',
          group: 'ai',
        } as RouteMeta,
      },

      // ---------------- 系统管理 ----------------
      {
        path: 'system/org',
        name: 'system-org',
        component: () => import('@/views/system/OrgView.vue'),
        meta: {
          title: '组织架构管理',
          breadcrumb: ['系统管理', '组织架构'],
          roles: ['ADM', 'HR'],
          icon: 'OfficeBuilding',
          group: 'system',
        } as RouteMeta,
      },
      {
        path: 'system/user',
        name: 'system-user',
        component: () => import('@/views/system/UserView.vue'),
        meta: {
          title: '用户管理',
          breadcrumb: ['系统管理', '用户管理'],
          roles: ['ADM'],
          icon: 'UserFilled',
          group: 'system',
        } as RouteMeta,
      },
      {
        path: 'system/role',
        name: 'system-role',
        component: () => import('@/views/system/RoleView.vue'),
        meta: {
          title: '角色与权限',
          breadcrumb: ['系统管理', '角色与权限'],
          roles: ['ADM'],
          icon: 'Lock',
          group: 'system',
        } as RouteMeta,
      },
      {
        path: 'system/config',
        name: 'system-config',
        component: () => import('@/views/system/ConfigView.vue'),
        meta: {
          title: '数据字典与系统配置',
          breadcrumb: ['系统管理', '字典与配置'],
          roles: ['ADM'],
          icon: 'Menu',
          group: 'system',
        } as RouteMeta,
      },
      {
        path: 'system/audit-log',
        name: 'system-audit-log',
        component: () => import('@/views/system/AuditLogView.vue'),
        meta: {
          title: '审计日志',
          breadcrumb: ['系统管理', '审计日志'],
          roles: ['ADM', 'AUDIT'],
          icon: 'Memo',
          group: 'system',
        } as RouteMeta,
      },

      // ---------------- 状态页 ----------------
      {
        path: '403',
        name: 'forbidden',
        component: () => import('@/views/status/ForbiddenView.vue'),
        meta: { title: '无访问权限', hidden: true, group: 'none' } as RouteMeta,
      },
      {
        path: '404',
        name: 'not-found',
        component: () => import('@/views/status/NotFoundView.vue'),
        meta: { title: '页面不存在', hidden: true, group: 'none' } as RouteMeta,
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/404',
  },
]

export default routes

/** 侧栏分组标题。 */
export const NAV_GROUPS: { key: string; label: string }[] = [
  { key: 'workbench', label: '' },
  { key: 'talent', label: '人才档案' },
  { key: 'ai', label: 'AI 中台' },
  { key: 'system', label: '系统管理' },
]
