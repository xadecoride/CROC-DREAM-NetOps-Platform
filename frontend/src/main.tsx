import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { loadConsoleConfig } from './api/runtimeConfig'
import { startApiSession } from './api/client'

const root = createRoot(document.getElementById('root')!)

// Порты консолей и роли приходят с бэкенда (config/constants.yaml) до первого рендера.
loadConsoleConfig()
  .then(() => {
    startApiSession()
    root.render(<StrictMode><App /></StrictMode>)
  })
  .catch((err: Error) => {
    root.render(<pre style={{ padding: 24 }}>{err.message}</pre>)
  })
