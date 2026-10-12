import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
// 🔴 令牌与全局样式**必须在 Element Plus 之后**引入：
//    tokens.css 覆盖了 `--el-*` 变量，若先引入会被 EP 默认值盖回去（配色不符原型）。
import '@/styles/tokens.css'
import '@/styles/global.css'

import App from './App.vue'
import router from './router'
import { setupDirectives } from './directives/perm'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// 图标全量注册（21 个页面共用到 30+ 图标，逐个按需引入反而易漏）
for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, comp)
}

setupDirectives(app)

app.mount('#app')
