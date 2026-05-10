/// <reference types="vitest/config" />
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
  },
  build: {
    chunkSizeWarningLimit: 1300,
    rolldownOptions: {
      output: {
        manualChunks(moduleId: string) {
          if (moduleId.includes('node_modules/echarts') || moduleId.includes('node_modules/zrender')) return 'charts'
          if (moduleId.includes('node_modules/antd') || moduleId.includes('node_modules/@ant-design')) return 'antd'
          if (moduleId.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
})
