import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: true,
    // Only used by the dev overlay; in production nginx proxies /api.
    proxy: { "/api": { target: "http://backend:8000", changeOrigin: true } },
  },
})
