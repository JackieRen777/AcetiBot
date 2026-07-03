import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { port: 8012 },
  build: {
    rolldownOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('recharts')) return 'vendor-chart'
          if (id.includes('react-markdown') || id.includes('remark-gfm')) return 'vendor-markdown'
          if (id.includes('lucide-react')) return 'vendor-ui'
          if (id.includes('react-router-dom') || id.includes('/react/') || id.includes('react-dom')) {
            return 'vendor-react'
          }
        },
      },
    },
  },
})
