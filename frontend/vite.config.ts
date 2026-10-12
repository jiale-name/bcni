import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * 架构 §4.2 / §7.1：`/api` 由 Vite（开发）/ Nginx（生产）代理至后端 8002。
 * axios 的 baseURL 为 `/api/v1`（见 src/api/http.ts），因此本代理只需转发 `/api` 前缀。
 *
 * 🔴 生产形态（deploy/nginx.conf）与开发形态**必须同源**：
 *    前端与后端同源 → 无需 CORS；若改成跨域直连，会同时引入预检、凭证与 Cookie 策略变更。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        // 🔴 **必须写 `127.0.0.1`，不能写 `localhost`**（批次 24，实测根因）：
        //    Node 17+ 的 `dns.lookup` 按**操作系统返回顺序**（verbatim）解析 `localhost`，
        //    Windows 常把 `::1`（IPv6）排在前面 —— 而 uvicorn 只绑 IPv4 `0.0.0.0`，
        //    于是代理连 `::1:8002` 被拒（10061），表现为「SSE 面板空白 / 接口时好时坏」。
        //    写死 `127.0.0.1` 后解析无歧义，流式响应（SSE）与普通请求同走一条稳定通路。
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 把体积最大的两个依赖单独成块：首屏只加载 vue 核心，图表与组件库按需延后。
        manualChunks: {
          vendor: ['vue', 'vue-router', 'pinia', 'axios'],
          element: ['element-plus', '@element-plus/icons-vue'],
          chart: ['echarts'],
        },
      },
    },
  },
})
