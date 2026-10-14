import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './workspace.css'
import App from './App.tsx'
import { initTheme } from './theme.ts'

initTheme() // 渲染前同步应用已保存主题，避免首帧闪烁

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
