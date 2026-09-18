/// <reference types="vitest/config" />
/*
 * 阅读导航 08｜启动、部署与工具
 * 职责：配置 Vite 构建分包、开发端口和 Vitest 的 jsdom 测试环境。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: './src/test/setup.ts',
    testTimeout: 15000,
  },
  build: {
    chunkSizeWarningLimit: 1300,
    rolldownOptions: {
      output: {
        manualChunks(moduleId: string) {
          if (moduleId.includes('node_modules/antd') || moduleId.includes('node_modules/@ant-design')) return 'antd'
          if (moduleId.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
})
