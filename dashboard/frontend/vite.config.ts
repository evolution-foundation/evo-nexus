import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': 'http://localhost:8080' } },
  resolve: {
    // Resolve workspace packages from TypeScript source in dev/build.
    // 'source' maps to ./src/index.ts in @evonexus/ui's exports field.
    conditions: ['source', 'import', 'module', 'browser', 'default'],
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
