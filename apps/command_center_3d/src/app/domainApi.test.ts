import { describe, it, expect } from "vitest";
import { DOMAIN_LIST_LIMIT, DomainApi, fmtBytes, fmtRelative, fmtTime } from "./domainApi";

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
 * Wave 9.2 — the START path's client contract.
 *
 * The client may not re-derive anything the server already decided. In
 * particular `would_accept` is composed server-side from the nine checks AND the
 * command buildability; a client that recomputed it (or that treated "the
 * request succeeded" as "the launch was accepted") would be the second source of
 * truth this whole surface exists to avoid. These tests pin the two calls to
 * their paths, keep the token in a header, and prove the fields are passed
 * through verbatim.
 */
describe("launch control client contract", () => {
  function spyApi(payload: Record<string, unknown>) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { calls, api: new DomainApi("http://x"), restore: () => { globalThis.fetch = original; } };
  }

  const PREVIEW = {
    schema: "jcc/v1.3-launch-request/1",
    mission_id: "m1", project_id: "p1", committed: false,
    capability: "USER_INITIATED_MISSION_LAUNCH",
    capability_version: "sha256:anchor", policy_version: "p3.0.0",
    directive: { directive_id: "d1", signed: true, ttl_s: 120 },
    preview: {
      schema: "jcc/v1.3-launch-preview/1", launchable: true, problems: [],
      argv: ["node", "cli.js", "-p", "say hi"], argv_goal_slot: 3,
      project_root_bound: true, args_hash: "sha256:x",
      shell: false, actuation: false, note: "preview only",
    },
    checks_ok: true, command_buildable: true,
    would_accept: false, would_reason: "COMMAND_NOT_BUILDABLE:PROJECT_ROOT_MISSING",
    steps: [{ step: "LAUNCH_REQUESTED", ok: true, detail: "template=t" }],
    actuation: false, elapsed_s: 0.01,
  };

  it("posts the preview to launch-request with the token in a header", async () => {
    const { calls, api, restore } = spyApi(PREVIEW);
    try {
      await api.launchRequest("m1", "sekret");
      expect(calls.length).toBe(1);
      expect(calls[0].url).toContain("/api/v1/domain/missions/m1/launch-request");
      expect(calls[0].url).not.toContain("sekret");
      const headers = calls[0].init.headers as Record<string, string>;
      expect(headers["X-JCC-Token"]).toBe("sekret");
    } finally {
      restore();
    }
  });

  it("posts the commit to launch-commit and nothing else", async () => {
    const { calls, api, restore } = spyApi({ ...PREVIEW, committed: true });
    try {
      await api.launchCommit("m1", "sekret");
      expect(calls.length).toBe(1);
      expect(calls[0].url).toContain("/api/v1/domain/missions/m1/launch-commit");
    } finally {
      restore();
    }
  });

  it("passes would_accept through unchanged and never upgrades it", async () => {
    const { api, restore } = spyApi(PREVIEW);
    try {
      const r = await api.launchRequest("m1", "sekret");
      // The server said NO; the client must not disagree.
      expect(r.data?.would_accept).toBe(false);
      expect(r.data?.would_reason).toContain("COMMAND_NOT_BUILDABLE");
      expect(r.data?.checks_ok).toBe(true);
      expect(r.data?.command_buildable).toBe(true);
    } finally {
      restore();
    }
  });

  it("keeps the goal in one argv slot on the preview it renders", async () => {
    const { api, restore } = spyApi(PREVIEW);
    try {
      const r = await api.launchRequest("m1", "sekret");
      const argv = r.data?.preview.argv ?? [];
      expect(argv[argv.length - 1]).toBe("say hi");
      expect(argv.filter((a) => a === "say hi").length).toBe(1);
      expect(r.data?.preview.shell).toBe(false);
      expect(r.data?.preview.actuation).toBe(false);
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

/**
 * Wave 9.3 — the domain-totals read.
 *
 * This endpoint exists so a bounded list can be recognised as bounded. If the
 * client ever stops asking for it, or asks for it with a token it does not need,
 * the disclosure it enables quietly disappears — so the wire shape is pinned.
 */
describe("domain totals client contract", () => {
  function spyApi(payload: unknown) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { calls, api: new DomainApi("http://x"), restore: () => { globalThis.fetch = original; } };
  }

  it("reads the uncapped totals from the open, token-free route", async () => {
    const { calls, api, restore } = spyApi({
      schema: "jcc/v1.3-domain-counts/1",
      projects: 3, archived_projects: 1, missions: 500, deliverables: 42,
    });
    try {
      const r = await api.counts();
      expect(calls.length).toBe(1);
      expect(calls[0].url).toContain("/api/v1/domain/counts");
      // No token: it is a declared-open read, and leaking the control token into
      // it would be a widening of where the token travels.
      expect(String(calls[0].url)).not.toContain("token");
      expect(calls[0].init.headers).toEqual({});
      expect(r.ok).toBe(true);
      expect(r.data?.missions).toBe(500);
    } finally {
      restore();
    }
  });

  it("asks the bounded list reads for the DOCUMENTED ceiling, not the server default", async () => {
    // The original defect was a client whose own bound (200) was invisible and
    // happened to equal the server's default, so nobody could see which one bit.
    const { calls, api, restore } = spyApi([]);
    try {
      await api.missions({ limit: DOMAIN_LIST_LIMIT });
      expect(calls[0].url).toContain(`limit=${DOMAIN_LIST_LIMIT}`);
      expect(calls[0].url).not.toContain("limit=200");
    } finally {
      restore();
    }
  });

  it("surfaces a FAILED counts read as an error, never as zeros", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (async () => ({
      ok: false, status: 503,
      text: async () => JSON.stringify({ detail: "upstream down" }),
    })) as unknown as typeof fetch;
    try {
      const r = await new DomainApi("http://x").counts();
      expect(r.ok).toBe(false);
      // `null`, never `{}`/zeros: a caller that reads zeros here would conclude
      // the domain is empty, which is precisely the collapse being prevented.
      expect(r.data).toBeNull();
      expect(r.error).toContain("upstream down");
    } finally {
      globalThis.fetch = original;
    }
  });
});
