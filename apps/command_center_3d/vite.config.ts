import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server binds 127.0.0.1:4781 only (v1.2 dev UI port). The final v1.2 UI
// will take 4780 and the Legacy UI is demoted to /legacy (see V1_2_BASELINE.json).
export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 4781, strictPort: true },
  preview: { host: "127.0.0.1", port: 4781, strictPort: true },
  build: { outDir: "dist", sourcemap: false },
});
