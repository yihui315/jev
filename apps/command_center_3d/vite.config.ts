import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server binds 127.0.0.1:4781 only (v1.2 dev UI port). The final v1.2 UI
// will take 4780 and the Legacy UI is demoted to /legacy (see V1_2_BASELINE.json).
export default defineConfig({
  plugins: [react()],
  // Force vitest/vite transform caches into the project tree (writable) instead
  // of TEMP — the sandbox shim rejects TEMP cache writes, which otherwise races
  // and intermittently drops a test file from the parallel collection.
  cacheDir: ".vite-cache",
  server: { host: "127.0.0.1", port: 4781, strictPort: true },
  preview: { host: "127.0.0.1", port: 4781, strictPort: true },
  build: {
    outDir: "dist",
    sourcemap: false,
    // three.js is ~1MB and is only needed on the World/Graph surfaces. Split it
    // into its own chunk so the product views (Home, Missions, Studio…) load
    // without paying for the renderer.
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes("node_modules/three") || id.includes("@react-three")) {
            return "three";
          }
          if (id.includes("node_modules/@radix-ui") || id.includes("node_modules/cmdk")) {
            return "radix";
          }
          return undefined;
        },
      },
    },
    chunkSizeWarningLimit: 900,
  },
  test: {
    // v2.1.9 deprecates `test.cache` in favour of Vite's own cacheDir above.
    cache: false,
    // The sandbox's brokered-fs shim rejects the parallel workers' SSR
    // transform-cache writes (`EPERM …/<tmp>/ssr/<hash>`), which vitest 2.1.9
    // reports as an *unhandled error* and marks the run failed. Running test
    // files serially keeps the cache single-writer and the run clean; the suite
    // is small, so the time cost is negligible.
    fileParallelism: false,
  },
});
