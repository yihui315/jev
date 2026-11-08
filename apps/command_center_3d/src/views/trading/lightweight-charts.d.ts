/**
 * Ambient module declaration for `lightweight-charts`.
 *
 * The package is declared in package.json devDependencies only — it is NOT
 * installed in this offline environment, so the dynamic `import("lightweight-charts")`
 * in CandlestickChart will reject at runtime and the code falls back to the SVG
 * renderer. This declaration lets `tsc --noEmit` and the editor treat the import
 * as `any` so the project type-checks without the package present.
 *
 * When the package is actually installed, its own bundled types take over and
 * this shim is shadowed by the real definitions (same module name).
 */
declare module "lightweight-charts" {
  // Intentionally loose: we only use createChart + addCandlestickSeries + setData,
  // and we never call those paths when the package is missing. `any` keeps the
  // offline type-check honest without faking the full API surface.
  export const createChart: any;
  export const ColorType: any;
  export const LineStyle: any;
  export const CrosshairMode: any;
  const _default: any;
  export default _default;
}
