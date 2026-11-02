import { describe, it, expect } from "vitest";
import { humanizeProviderError } from "./humanizeProviderError";

describe("humanizeProviderError", () => {
  it("turns the upstream geo-block (HTTP 451 bytes-repr) into a readable line", () => {
    const raw =
      'Jev did not return a usable decision: HTTP 451: b\'{"title":"Typesafe is not available in your region.","status":451}\'';
    const out = humanizeProviderError(raw);
    expect(out).toContain("HTTP 451");
    expect(out).toContain("上游地域限制");
    // The Python bytes-repr must NOT leak into the UI.
    expect(out).not.toContain("b'{");
    expect(out).not.toContain("not available in your region");
  });

  it("unwraps the 'usable decision' wrapper for other causes", () => {
    expect(humanizeProviderError("Jev did not return a usable decision: HTTP 429: rate limited")).toContain("限流");
    expect(
      humanizeProviderError("Jev did not return a usable decision: TypeSafe Jev has no credential (REAL_AUTH_MISSING)"),
    ).toContain("REAL_AUTH_MISSING");
  });

  it("returns '' for unrecognised shapes so callers can fall through", () => {
    expect(humanizeProviderError("Jev chose 'x', which was not offered ['a','b']")).toBe("");
    expect(humanizeProviderError(null)).toBe("");
    expect(humanizeProviderError("")).toBe("");
  });

  it("turns a TLS / network transport failure into a readable line", () => {
    const raw =
      "Jev did not return a usable decision: transport failure: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1032)>";
    const out = humanizeProviderError(raw);
    expect(out).toContain("网络 / SSL 错误");
    expect(out).not.toContain("CERTIFICATE_VERIFY_FAILED");
    expect(out).not.toContain("self-signed certificate");
    // The Python stack frame must not leak either.
    expect(out).not.toContain("_ssl.c");
  });
});
