# JCC v1.2 — Host Compatibility Decision (build toolchain)

**Decision: pin `vite` to `5.4.11` (and `vitest` to the `^2.1.x` line). Do NOT bump to
Vite 7 / 8 on this host.**

## 1. Hard constraint discovered on this host

`apps/command_center_3d` builds and tests on a Windows host whose native toolchain
cannot load the **rolldown**-based native binary shipped with Vite 7+/8:

```
Vite 8 (rolldown) native binding: %1 is not a valid Win32 application
  (ERROR_BAD_EXE_FORMAT / STATUS_INVALID_IMAGE_FORMAT)
```

Vite 5.4.11 is the last stable release that is **purely JS (Esbuild + Rollup) with no
rolldown native binding**, so `npm ci && npm run build` and `npm test` are reproducible
and hermetic here. The build was verified on this host:

```
npm ci            -> 114 packages
npm run build     -> 67 modules, dist/assets/index-*.js (~1.17 MB), dist/ emitted
```

## 2. What is allowed vs forbidden

| Action | Status |
|--------|--------|
| `vite@5.4.11` + `vitest@^2.1.9` | ✅ locked, builds & tests pass on host |
| Bump to `vite@7` / `vite@8` (rolldown) | ❌ blocked until the host native toolchain (or a different build host) supports the rolldown binary |
| Vite **dev server** (`npm run dev`, port 4781) as production UI | ❌ forbidden — dev server holds an HMR websocket and is not a durable static surface; production serves `dist/` via `apps/command_center_3d/serve_visual.py` |
| `npm run build` then serve `dist/` via `serve_visual.py` (4780) | ✅ the supported production path |

## 3. Why this matters for the release

The Visual Command Center ships as a **static SPA** (`dist/`) served by the
loopback-only `serve_visual.py` (127.0.0.1:4780). The build must be reproducible on
the deployment host; a toolchain that fails to load its native binary would break the
only supported production path. Pinning 5.4.11 keeps `npm ci` + `npm run build`
deterministic and keeps the dependency surface inside what this host can actually run.

## 4. Revisit trigger

Re-evaluate the pin only when ONE of:
- the host's VS/Windows SDK / Node native ABI is upgraded and the rolldown binary
  loads cleanly (`vite build` succeeds with Vite 8), or
- the build is moved to a build host whose toolchain supports rolldown.

Until then, 5.4.11 stands.
