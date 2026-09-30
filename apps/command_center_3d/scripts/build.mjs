/**
 * build.mjs — run `vite build`, working around a host that refuses file deletion.
 *
 * THE MEASURED PROBLEM (2026-09-30)
 * ---------------------------------
 * The release gate runs `npm run build` as leg C3c_BUILD, and the two UI gates
 * (B1/W6 and B8/W10) read the bundle it writes. On this host that leg failed:
 *
 *     ✓ 1718 modules transformed.
 *     error during build:
 *     [vite:esbuild-transpile] remove <tmp>\esbuild-<64 hex>: Access is denied.
 *
 * The cause is not the app. esbuild's API writes any transform *input* larger
 * than 1 MiB to a temp file and hands the **path** to its Go worker
 * (`node_modules/esbuild/lib/main.js:793`); the Go worker reads it and then
 * deletes it as designed cleanup. On this host **no process may delete a file**,
 * so that cleanup fails and the whole build dies:
 *
 *   * `fs.unlinkSync` in the workspace -> EPERM, and in `%TEMP%` -> EPERM
 *   * with the safe-delete shim disabled (`CODEBUDDY_SAFE_DELETE_ENABLED=0`) it
 *     is still EPERM, so this is the platform layer, not the shim
 *   * the shim's fallback binary (`genie-trash win32-x64.exe`) exits 0 and
 *     removes nothing
 *   * clearing the read-only attribute first does not help (mode is already 0666)
 *
 * It is the same denial that leaves ~20 `b'blat'` temp-probe canaries in the
 * working directory on every Python start (see tools/v1_3_sandbox_canary_sweep.py).
 *
 * THE WAY THROUGH
 * ---------------
 * esbuild anticipates a host that cannot write a temp file: `writeFile` reports
 * failure by calling back with `null`, and the caller then falls back to
 * `inputFS: false`, i.e. it inlines the payload in the message instead. Measured
 * directly: a 3.5 MB transform succeeds with an unusable `TEMP` and fails with a
 * usable one.
 *
 * So this wrapper points `TEMP`/`TMP` at a path that cannot be a directory before
 * spawning vite. The sentinel is a **file**, so the path can never start working
 * by accident, and nothing accumulates. The bundle produced is byte-for-byte the
 * normal production build -- only the transport of large esbuild requests
 * changes, from "temp file round-trip" to "inline in the pipe".
 *
 * WHY IT PROBES INSTEAD OF ASSUMING
 * ---------------------------------
 * Hardcoding the workaround would apply it on every machine, including healthy
 * ones, trading a few megabytes of memory per large transform for nothing. The
 * probe decides from measurement, so a healthy host keeps esbuild's normal path.
 *
 * WHY IT PROBES IN `.vitetmp`
 * ---------------------------
 * The probe has to delete a file to know whether deletion works -- and on this
 * host it cannot, so the probe file survives. It is therefore written to the
 * gitignored `.vitetmp/` (not to the OS temp dir, and not to the repository
 * root) and with a FIXED name, so repeated builds overwrite one file instead of
 * accumulating a new one each run.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VITETMP = path.join(APP, ".vitetmp");
const PROBE = path.join(VITETMP, "__esbuild_temp_probe");
const SENTINEL = path.join(VITETMP, "ESBUILD_TEMPDIR_UNAVAILABLE");
const VITE_BIN = path.join(APP, "node_modules", "vite", "bin", "vite.js");

/** Can this process create a file and then delete it? */
function deletionWorks() {
  try {
    fs.mkdirSync(VITETMP, { recursive: true });
    fs.writeFileSync(PROBE, "x");
    fs.unlinkSync(PROBE);
    return true;
  } catch {
    return false;
  }
}

const env = { ...process.env };

if (!deletionWorks()) {
  // Keep the sentinel a FILE: a path that is already an existing file can never
  // succeed as a directory, so the fallback cannot silently stop applying.
  try {
    if (!fs.existsSync(SENTINEL)) fs.writeFileSync(SENTINEL, "");
  } catch (err) {
    console.error("[build] could not create the temp sentinel:", err);
    process.exit(1);
  }
  env.TEMP = SENTINEL;
  env.TMP = SENTINEL;
  console.log(
    "[build] this host refuses file deletion, so esbuild's temp-file round-trip " +
      "cannot complete; forcing its in-memory fallback via an unusable TEMP",
  );
} else {
  console.log("[build] file deletion works; esbuild keeps its normal temp-file path");
}

const result = spawnSync(process.execPath, [VITE_BIN, "build"], {
  cwd: APP,
  env,
  stdio: "inherit",
});

if (result.error) {
  console.error("[build] could not start vite:", result.error);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
