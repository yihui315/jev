import { describe, it, expect } from "vitest";
import { DomainApi, fmtBytes, fmtRelative, fmtTime } from "./domainApi";

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

/**
 * Wave 4 — the control surface is PREVIEW-FIRST. The one property a client can
 * prove on its own is that the resolve call sends the exact body it is given and
 * puts the token in a header (never a URL). The dry_run default lives on the
 * server; the client must not silently upgrade a preview into an apply.
 */
describe("attention control client contract", () => {
  function spyApi() {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      const payload = { ok: true, mission_id: "m1", action: "verify",
                        dry_run: true, applied: false };
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { calls, api: new DomainApi("http://x"), restore: () => { globalThis.fetch = original; } };
  }

  it("posts the preview to the resolve endpoint with the token in a header", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.resolveAttention("sekret", "m1", { action: "verify", dry_run: true });
      expect(calls.length).toBe(1);
      const { url, init } = calls[0];
      expect(url).toContain("/api/v1/domain/needs-attention/m1/resolve");
      expect(url).not.toContain("sekret");
      const headers = init.headers as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBe("sekret");
      expect(String(init.body)).toContain('"dry_run":true');
    } finally {
      restore();
    }
  });

  it("does not turn a preview into an apply", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.resolveAttention("t", "m1", { action: "verify", dry_run: true });
      expect(String(calls[0].init.body)).not.toContain('"dry_run":false');
    } finally {
      restore();
    }
  });

  it("reads resolutions from the open GET endpoint with no token", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.attentionResolutions();
      expect(calls[0].url).toContain("/api/v1/domain/needs-attention/resolutions");
      const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBeUndefined();
    } finally {
      restore();
    }
  });
});

/**
 * Wave 5 — a deliverable has an identity and a human verdict. The client can
 * prove: the by-id read hits the right path, the verify POST puts the token in a
 * header (never a URL) and names the human, and the deliverable view reads from
 * the server-side endpoint (no client fan-out).
 */
describe("deliverable client contract", () => {
  function spyApi() {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      const payload = { artifact_id: "art_1", mission_id: "m1", kind: "file",
                        name: "out.txt", uri: "", sha256: null, bytes: 0,
                        status: "VERIFIED", provenance: { verified_by: "alice" },
                        created_at: 0, mission_title: "M" };
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { calls, api: new DomainApi("http://x"), restore: () => { globalThis.fetch = original; } };
  }

  it("reads one deliverable by identity", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.deliverable("art_1");
      expect(calls[0].url).toContain("/api/v1/domain/artifacts/art_1");
      const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBeUndefined();
    } finally {
      restore();
    }
  });

  it("records a verdict with the token in a header and the human named", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.verifyDeliverable("sekret", "art_1", { status: "VERIFIED", decided_by: "alice" });
      const { url, init } = calls[0];
      expect(url).toContain("/api/v1/domain/artifacts/art_1/verify");
      expect(url).not.toContain("sekret");
      const headers = init.headers as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBe("sekret");
      expect(String(init.body)).toContain('"decided_by":"alice"');
      expect(String(init.body)).toContain('"status":"VERIFIED"');
    } finally {
      restore();
    }
  });

  it("reads the server-side deliverable view with a status filter", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.deliverables({ status: "VERIFIED" });
      expect(calls[0].url).toContain("/api/v1/domain/deliverables");
      expect(calls[0].url).toContain("status=VERIFIED");
      const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBeUndefined();
    } finally {
      restore();
    }
  });
});

/**
 * Wave 7 — a capability is MEASURED, not asserted. The client can prove:
 * the by-id read is token-free, the verify POST carries the token in a header
 * (never a URL) and CANNOT state the outcome — the client never puts a `kind`
 * on the wire, because the outcome belongs to the probe.
 */
describe("connection verification client contract", () => {
  function spyApi() {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      const payload = {
        connection_id: "con_1", agent: "claude",
        observed: { kind: "TELEMETRY_ONLY", detail: "seen", evidence: {},
                    evidence_ref: "sha256:" + "a".repeat(64), reason: null,
                    measured_at: 0 },
        health: "DEGRADED", control_verified: false, capabilities: [],
      };
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { calls, api: new DomainApi("http://x"), restore: () => { globalThis.fetch = original; } };
  }

  it("reads one connection (with provenance) with no token", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.connection("con_1");
      expect(calls[0].url).toContain("/api/v1/domain/connections/con_1");
      const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBeUndefined();
    } finally {
      restore();
    }
  });

  it("runs verification with the token in a header", async () => {
    const { calls, api, restore } = spyApi();
    try {
      const r = await api.verifyConnection("sekret", "con_1", { probe: "auto" });
      const { url, init } = calls[0];
      expect(url).toContain("/api/v1/domain/connections/con_1/verify");
      expect(url).not.toContain("sekret");
      const headers = init.headers as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBe("sekret");
      expect(r.ok).toBe(true);
    } finally {
      restore();
    }
  });

  it("never sends a capability kind on the verify wire (the caller cannot state the outcome)", async () => {
    const { calls, api, restore } = spyApi();
    try {
      await api.verifyConnection("t", "con_1");
      const body = String(calls[0].init.body ?? "");
      expect(body).not.toContain("kind");
      expect(body).not.toContain("CONTROL_VERIFIED");
    } finally {
      restore();
    }
  });
});
