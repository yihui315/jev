import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev server binds 127.0.0.1:4781 only (v1.2 dev UI port). The final v1.2 UI
// will take 4780 and the Legacy UI is demoted to /legacy (see V1_2_BASELINE.json).
//
// When the real Night Researcher Paper/Agents API is to be used (paperApi
// `USE_MOCK = false`), set VITE_API_PROXY_TARGET to the NR dev origin (default
// 8787, the wrangler dev port). The proxy is inert while USE_MOCK is true,
// because the SPA then issues no `/api/*` requests at all.
// `globalThis` (not `process`) avoids needing @types/node in this config file.
const nodeEnv = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env;
const API_PROXY_TARGET = nodeEnv?.VITE_API_PROXY_TARGET || "http://127.0.0.1:8787";
const DESK_API_TARGET = nodeEnv?.VITE_DESK_API_TARGET || "http://127.0.0.1:4791";
const paperProxy = {
  // JEV Trading Desk V1.0 — the single backend data adapter. Every page calls
  // /api/jev/* and this proxy forwards to the localhost adapter (4791). The
  // adapter itself is the ONLY thing that talks to the frozen paper engine and
  // the verified public market REST host. No page ever bypasses it.
  "/api/jev": { target: DESK_API_TARGET, changeOrigin: true },
  "/api/paper": { target: API_PROXY_TARGET, changeOrigin: true },
  "/api/agents": { target: API_PROXY_TARGET, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  // Force vitest/vite transform caches into the project tree (writable) instead
  // of TEMP — the sandbox shim rejects TEMP cache writes, which otherwise races
  // and intermittently drops a test file from the parallel collection.
  cacheDir: ".vite-cache",
  server: { host: "127.0.0.1", port: 4781, strictPort: true, proxy: paperProxy },
  preview: { host: "127.0.0.1", port: 4781, strictPort: true, proxy: paperProxy },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      // Two independent entry points: the legacy command center (index.html)
      // and the new Trading Desk (trading.html). Neither rewrites the other.
      // Paths are relative to the Vite root (project dir).
      input: {
        main: "index.html",
        trading: "trading.html",
      },
      output: {
        /**
         * WHY THERE IS NO `three` ENTRY HERE
         * ----------------------------------
         * v1.2 named three.js as a manual chunk. That produced a separate
         * `three-*.js` file, but the MAIN chunk still held a static
         * `import … from "./three-*.js"`, so the browser preloaded it from
         * index.html and the home page paid ~250 kB gzip for a renderer it never
         * draws. Naming a chunk does not make it lazy.
         *
         * Three.js is now genuinely lazy: `App.tsx` reaches the 3D surfaces only
         * through `React.lazy(() => import("./scene/…"))`, so Rollup puts every
         * three-dependent module in the async chunk graph on its own. Leaving
         * `manualChunks` unset for three is what keeps it out of the entry
         * chunk's static imports — the split has to come from the import graph,
         * not from a chunk name.
         *
         * Radix/cmdk are still named explicitly: they are small, shared by the
         * entry, and a stable name makes them cacheable across releases.
         */
        manualChunks: (id) => {
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
    // NOT vitest suites: these two are hand-written `node <path>` assertion
    // scripts (node:assert + process.exit, no describe/it). They matched the
    // default `**/*.test.mjs` glob and made every `vitest run` fail with
    // "No test suite found in file" — a red unit-test job for a test-runner
    // mismatch, not for a real defect. Their correct entry point is `node`,
    // which the CI gap-closure gate below uses. `configDefaults` is spread in
    // on purpose: setting `exclude` replaces vitest's defaults wholesale, and
    // a bare array would silently stop excluding node_modules/dist.
    exclude: [
      ...configDefaults.exclude,
      "scripts/supervisor/supervisor.test.mjs",
      "server/ai/evidence/tests/inference-evidence.test.mjs",
    ],
  },
});
