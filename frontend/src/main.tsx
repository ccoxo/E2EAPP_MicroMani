import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { installAutoShutdownOnClose, installRuntimeReleaseOnClose } from './api'
import './index.css'

installRuntimeReleaseOnClose()
installAutoShutdownOnClose()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
