import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/state": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/plan": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/traveler": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/profiles": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/itineraries": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/advisor": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/replay": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/compare": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  plugins: [
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],
})
