import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import vueDevTools from 'vite-plugin-vue-devtools';

// 后端地址：默认 127.0.0.1:8000（用 IP 而非 localhost，避免 Node 把 localhost 解析成 IPv6 ::1 导致连接被拒）
// 后端换了端口时可用环境变量覆盖：VITE_BACKEND_ORIGIN=http://127.0.0.1:9000 pnpm dev
const BACKEND_ORIGIN = process.env.VITE_BACKEND_ORIGIN || 'http://127.0.0.1:8000';

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), vueDevTools()],

  server: {
    port: 3000,
    strictPort: false,
    // /api 全部转发到 FastAPI（stock-service），后端同时提供 /api 与 /api/v1 两套入口，无需 rewrite
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },

  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
});
