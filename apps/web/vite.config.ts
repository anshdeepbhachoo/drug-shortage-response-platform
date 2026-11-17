import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Node globals without pulling in @types/node. WEB_PORT / API_PORT let a second copy run beside yours.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.WEB_PORT ?? 5173),
    // The platform API (apps/api). Start it with: .venv/bin/uvicorn apps.api.main:app --port 8000
    proxy: { "/api": `http://127.0.0.1:${process.env.API_PORT ?? "8000"}` },
  },
});
