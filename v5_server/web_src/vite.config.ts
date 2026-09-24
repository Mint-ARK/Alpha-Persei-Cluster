import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'node:path'

export default defineConfig({
  // 相对路径，便于构建产物直接从本地文件系统或服务端相对路径访问
  base: './',
  plugins: [tailwindcss(), react()],
  server: {
    port: 5273,
    open: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true,
      },
      '/web': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true,
      },
      '/extracted_assets': {
        target: 'http://127.0.0.1:80/web',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 构建产物直接输出到同级 static_web，一步到位，不再需要跨仓库同步
    outDir: resolve(__dirname, '../static_web'),
    emptyOutDir: false, // 严禁清空！保护 static_web 中的 extracted_assets 美术资产
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        // 单入口打包，便于生成单文件与独立脚本
        inlineDynamicImports: true,
        entryFileNames: 'app.js',
        assetFileNames: 'app[extname]',
      },
    },
  },
})
