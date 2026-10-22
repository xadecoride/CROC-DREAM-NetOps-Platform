import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import compression from 'compression'

const backendTarget = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8001'
const sharedProxy = {
  '/api': {
    target: backendTarget,
    changeOrigin: true,
  },
  '/healthz': backendTarget,
  '/readyz': backendTarget,
  '/metrics': backendTarget,
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'preview-gzip-cache',
      configurePreviewServer(server) {
        server.middlewares.use(compression() as never)
        server.middlewares.use((req, res, next) => {
          if (req.url?.startsWith('/assets/')) res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
          next()
        })
      },
    },
  ],
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor';
          }
          if (id.includes('node_modules/lucide-react/')) {
            return 'icons';
          }
          if (id.includes('@monaco-editor')) {
            return 'monaco';
          }
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: sharedProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: sharedProxy,
  },
})
