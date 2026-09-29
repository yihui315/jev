import { describe, expect, it } from "vitest";
import { DEFAULT_API_BASE, SAME_ORIGIN, resolveApiBase } from "./apiBase";

/**
 * The falsifiers for "one API base, four definitions".
 *
 * Before this module existed, `App.tsx` collapsed `same-origin` to "" while
 * `JevProviderCard.tsx` and `ConnectionsView.tsx` concatenated the raw value,
 * producing `same-origin/api/...` — a path, not an origin. The knob looked
 * supported and silently half-worked. These tests pin the semantics so a second
 * definition cannot reappear unnoticed.
 */
describe("resolveApiBase -- the single product API base", () => {
  it("defaults to the shipped two-port origin when the env is unset", () => {
    // The shipped topology: API on 4779, SPA served by serve_visual.py on 4780,
    // talking cross-origin. So an unset VITE_API_BASE is NOT "relative".
    expect(resolveApiBase(undefined)).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase(null)).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase("")).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase("   ")).toBe(DEFAULT_API_BASE);
  });

  it("collapses the same-origin sentinel to the empty string", () => {
    expect(resolveApiBase(SAME_ORIGIN)).toBe("");
  });

  it("honours an explicit origin and strips trailing slashes", () => {
    expect(resolveApiBase("http://127.0.0.1:9999")).toBe("http://127.0.0.1:9999");
    expect(resolveApiBase("http://127.0.0.1:9999/")).toBe("http://127.0.0.1:9999");
    expect(resolveApiBase("http://127.0.0.1:9999///")).toBe("http://127.0.0.1:9999");
  });

  it("never produces a doubled slash when a path is appended", () => {
    // Every caller writes `resolveApiBase(v) + "/api/..."`, so a trailing slash
    // would silently produce `//api/...`.
    const joined = resolveApiBase("http://127.0.0.1:9999/") + "/api/health";
    expect(joined).toBe("http://127.0.0.1:9999/api/health");
    expect(joined).not.toContain("//api");
    const relative = resolveApiBase(SAME_ORIGIN) + "/api/health";
    expect(relative).toBe("/api/health");
  });

  it("never points at the protected GREEN instance (4778)", () => {
    expect(DEFAULT_API_BASE).not.toContain("4778");
    for (const raw of [undefined, "", SAME_ORIGIN, "http://127.0.0.1:4780"]) {
      expect(resolveApiBase(raw)).not.toContain("4778");
    }
  });
});
