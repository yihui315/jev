/**
 * WebGL2 capability detection for the Visual Command Center (Wave 8 fallback).
 *
 * The 3D world (CommandCenter3D) requires a WebGL2 context. On hosts/browsers
 * without WebGL2 (headless CI, locked-down GPU, RDP sessions), rendering into a
 * dead canvas must NEVER be the only option — the app falls back to the
 * Graph/Table views, which need no GPU. Detection is done ONCE at module load
 * and is the single source of truth the App keys its initial view off.
 */

/**Return true iff a WebGL2 rendering context can be created in this environment.*/
export function detectWebGL2(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2"));
  } catch {
    return false;
  }
}

/**
 * The view the app should open with when WebGL2 is (un)available.
 *
 * With WebGL2 we start in the 3D world; without it we start in the tabular
 * view so the user always lands on something that actually renders.
 */
export function fallbackViewForWebGL2(supported: boolean): "world" | "table" {
  return supported ? "world" : "table";
}
