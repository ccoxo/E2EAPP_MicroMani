/*
 * 阅读导航 01｜入口与界面
 * 职责：挂载 React 应用并安装页面退出时的运行资源释放监听。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { installRuntimeReleaseOnClose } from './api'
import './index.css'

installRuntimeReleaseOnClose()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
