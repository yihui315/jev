import { describe, expect, it } from "vitest";
import { detectWebGL2, fallbackViewForWebGL2 } from "./webgl";

describe("webgl detection (Wave 8 fallback)", () => {
  it("returns a boolean, never throws", () => {
    expect(typeof detectWebGL2()).toBe("boolean");
  });

  it("maps support to the 3D world view and absence to the table view", () => {
    expect(fallbackViewForWebGL2(true)).toBe("world");
    expect(fallbackViewForWebGL2(false)).toBe("table");
  });

  it("jsdom has no WebGL2, so the app must fall back to the table view", () => {
    // The Visual Command Center must not open a 3D world into a dead canvas in
    // headless/CI environments — the falsifier for the WebGL2->Table fallback.
    expect(detectWebGL2()).toBe(false);
    expect(fallbackViewForWebGL2(detectWebGL2())).toBe("table");
  });
});
