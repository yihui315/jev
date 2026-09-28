import { describe, it, expect } from "vitest";
import { fmtBytes, fmtRelative, fmtTime } from "./domainApi";

/**
 * Formatters are shared by every domain view, so a regression here misreports
 * every timestamp and size in the product. The important property is that an
 * absent value renders as an explicit dash — never as 0 or "1970".
 */

describe("formatters", () => {
  it("renders an absent timestamp as a dash, not 1970", () => {
    expect(fmtTime(null)).toBe("—");
    expect(fmtTime(undefined)).toBe("—");
    expect(fmtTime(0)).toBe("—");
    expect(fmtTime(Number.NaN)).toBe("—");
  });

  it("formats a real timestamp", () => {
    // 2026-09-28T00:00:00Z
    const out = fmtTime(1790553600);
    expect(out).not.toBe("—");
    expect(out.length).toBeGreaterThan(4);
  });

  it("renders relative time buckets", () => {
    const now = 1_800_000_000_000; // ms
    const secs = now / 1000;
    expect(fmtRelative(secs, now)).toBe("0s ago");
    expect(fmtRelative(secs - 30, now)).toBe("30s ago");
    expect(fmtRelative(secs - 59, now)).toBe("59s ago");
    expect(fmtRelative(secs - 60, now)).toBe("1m ago");
    expect(fmtRelative(secs - 120, now)).toBe("2m ago");
    expect(fmtRelative(secs - 7200, now)).toBe("2h ago");
    expect(fmtRelative(secs - 172800, now)).toBe("2d ago");
  });

  it("renders a future timestamp as 'just now' rather than a negative age", () => {
    const now = 1_800_000_000_000;
    expect(fmtRelative(now / 1000 + 60, now)).toBe("just now");
  });

  it("renders an absent size as a dash and scales real sizes", () => {
    expect(fmtBytes(null)).toBe("—");
    expect(fmtBytes(undefined)).toBe("—");
    expect(fmtBytes(0)).toBe("—");
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
