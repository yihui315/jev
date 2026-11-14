// qa-verify / INDEPENDENT QA — honesty tests for the unified metrics layer.
//
// DATA PROVENANCE
// ---------------
// Every input below comes from the LIVE capture at
//   D:/tmp/jev_real_payload_sample.json
// (local facade 127.0.0.1:4791, captured 2026-11-14T14:34:09.279Z,
//  10 real /api/jev/* endpoints, _cookie_present:true).
// `realRaw()` wires each endpoint's `.data` into CockpitRaw EXACTLY as the
// runtime does: TradingCockpit.tsx:76-83 assigns `useJevQuery(...).data`, and
// client.ts:111 sets `data: parsed.data`, i.e. the envelope's `data` member.
// No hand-written market data, no synthesized curves, no invented fills.
//
// NOTE ON THE NODE IMPORT
// -----------------------
// tsconfig has `types:["vite/client"]` and @types/node is NOT installed, but
// `include:["src"]` means tsc --noEmit checks this file. A non-literal dynamic
// import is not statically resolved by TypeScript (no missing-declaration
// error); at runtime vitest executes in Node and the builtin resolves. This
// affects ONLY how the module is loaded — every assertion below is unchanged.
// There is deliberately NO `@ts-ignore`, NO `.skip`, and NO weakened assertion.

import { describe, it, expect } from "vitest";
import {
  deriveCockpitSnapshot,
  deriveProviderRows,
  displayNumber,
  type CockpitRaw,
  type MetricKey,
} from "./contracts";

const nodeFs = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as {
  readFileSync: (path: string, encoding: string) => string;
  existsSync: (path: string) => boolean;
};

const SAMPLE = "D:/tmp/jev_real_payload_sample.json";
const J = JSON.parse(nodeFs.readFileSync(SAMPLE, "utf8")) as Record<string, unknown>;

type Json = Record<string, unknown>;
const data = (p: string): unknown => (J[p] as { data?: unknown } | undefined)?.data;

function realRaw(): CockpitRaw {
  return {
    account: data("/paper/account"),
    orders: data("/paper/orders"),
    events: data("/paper/events?after=0&limit=50"),
    equity: data("/paper/equity"),
    strategies: data("/strategies/status"),
    providers: data("/providers/status"),
    system: data("/system/status"),
  };
}

// The eight metrics that MUST be honest N/A under the frozen LIVE payload.
// (grossPnl is intentionally not listed: contracts.ts leaves it N/A because
// the server exposes only a NET totalPnl, and the oracle allows NUMERIC_OR_NA.)
const NA_KEYS: MetricKey[] = [
  "winRate", "profitFactor", "maxDrawdown", "slippage",
  "aiInferenceCost", "orderAcceptanceRate", "fillRate", "tradingFees",
];

describe("input provenance — the LIVE capture, not a fixture", () => {
  it("sample exists and carries the real capture markers", () => {
    expect(nodeFs.existsSync(SAMPLE)).toBe(true);
    expect(J["_captured_utc"]).toBe("2026-11-14T14:34:09.279Z");
    expect(J["_cookie_present"]).toBe(true);
    expect((J["/paper/account"] as Json)["status"]).toBe(200);
    expect((J["/paper/equity"] as Json)["status"]).toBe(200);
  });

  it("the real account/equity/orders/providers fields are what the layer reads", () => {
    const acc = data("/paper/account") as Json;
    const acct = acc["account"] as Json;
    const summary = acc["summary"] as Json;
    expect(acct["initialCash"]).toBe(10000);
    expect(summary["equity"]).toBe(10000);
    expect(summary["totalPnl"]).toBe(0);
    // These two are the fabrication traps: a dishonest maxDrawdown could copy
    // them. The layer must ignore both and report N/A from empty snapshots.
    expect(summary["drawdown"]).toBe(0);
    expect(acct["equityPeak"]).toBe(10000);
    expect((data("/paper/equity") as Json)["snapshots"]).toEqual([]);
    expect((data("/paper/equity") as Json)["equity_curve_synthesized"]).toBe(false);
    expect((data("/paper/orders") as Json)["orders"]).toEqual([]);
    expect((data("/providers/status") as Json)["verdict"]).toBe("BLOCKED");
    expect((data("/providers/status") as Json)["real_inference_records"]).toBe(0);
    expect((data("/strategies/status") as Json)["verdict"]).toBe("NO_DATA");
  });
});

describe("deriveCockpitSnapshot — real PAPER account", () => {
  it("roi and netPnl are REAL numeric values with domain PAPER", () => {
    const s = deriveCockpitSnapshot(realRaw());
    for (const k of ["roi", "netPnl"] as MetricKey[]) {
      const m = s.metrics[k];
      expect(m.value).not.toBeNull();
      expect(typeof m.value).toBe("number");
      expect(Number.isFinite(m.value)).toBe(true);
      expect(m.domain).toBe("PAPER");
      expect(m.display).not.toBe("N/A");
      expect(m.naReason).toBeNull();
    }
    // Deterministic from real fields: (equity 10000 - initialCash 10000)/10000 = 0,
    // and summary.totalPnl = 0. Asserting the exact value blocks any fabricated
    // non-zero "performance" number.
    expect(s.metrics.roi.value).toBe(0);
    expect(s.metrics.netPnl.value).toBe(0);
    expect(s.equity.domain).toBe("PAPER");
    expect(s.equity.value).toBe(10000);
    expect(s.cash.domain).toBe("PAPER");
    expect(s.cash.value).toBe(10000);
  });

  it("the 8 no-data metrics are honestly N/A (value null, display N/A, naReason set)", () => {
    const s = deriveCockpitSnapshot(realRaw());
    for (const k of NA_KEYS) {
      const m = s.metrics[k];
      expect({ key: k, value: m.value, display: m.display, domain: m.domain })
        .toEqual({ key: k, value: null, display: "N/A", domain: "UNAVAILABLE" });
      expect(m.naReason).not.toBeNull();
      expect(String(m.naReason).length).toBeGreaterThan(0);
    }
  });

  it("maxDrawdown N/A reason explicitly forbids a synthesized equity curve", () => {
    const s = deriveCockpitSnapshot(realRaw());
    expect(s.metrics.maxDrawdown.value).toBeNull();
    expect(s.metrics.maxDrawdown.naReason).toMatch(/严禁合成曲线|不得合成|未合成曲线/);
  });

  it("NEGATIVE: deleting account.initialCash forces roi back to N/A", () => {
    const raw = realRaw();
    const acc = structuredClone(raw.account) as Json;
    delete (acc["account"] as Json)["initialCash"];
    const s = deriveCockpitSnapshot({ ...raw, account: acc });
    expect({ value: s.metrics.roi.value, display: s.metrics.roi.display })
      .toEqual({ value: null, display: "N/A" });
    expect(s.metrics.roi.naReason).not.toBeNull();
    // netPnl is independent of initialCash and must stay numeric.
    expect(s.metrics.netPnl.value).toBe(0);
  });

  it("NEGATIVE: equity.snapshots=[] stays N/A even with summary.drawdown / equityPeak decoys", () => {
    const raw = realRaw();
    const eq = structuredClone(raw.equity) as Json;
    eq["snapshots"] = [];
    const acc = structuredClone(raw.account) as Json;
    (acc["summary"] as Json)["drawdown"] = 42;      // decoy
    (acc["account"] as Json)["equityPeak"] = 99999; // decoy
    const s = deriveCockpitSnapshot({ ...raw, equity: eq, account: acc });
    expect({ value: s.metrics.maxDrawdown.value, display: s.metrics.maxDrawdown.display })
      .toEqual({ value: null, display: "N/A" });
    expect(s.metrics.maxDrawdown.naReason).toMatch(/严禁合成曲线|不得合成|未合成曲线/);
  });

  it("displayNumber(null, ...) === 'N/A' for every unit; non-finite also N/A", () => {
    for (const u of ["USD", "PCT", "RATIO", "COUNT"] as const) {
      expect(displayNumber(null, u)).toBe("N/A");
    }
    expect(displayNumber(NaN, "USD")).toBe("N/A");
    expect(displayNumber(Infinity, "PCT")).toBe("N/A");
    expect(displayNumber(-Infinity, "RATIO")).toBe("N/A");
    // Sanity: a finite number is NOT N/A (guards against a stub formatter).
    expect(displayNumber(12.5, "USD")).not.toBe("N/A");
  });

  it("NO metric, equity, or cash has domain REAL (no verified real-money fills)", () => {
    const s = deriveCockpitSnapshot(realRaw());
    for (const [k, m] of Object.entries(s.metrics)) {
      expect({ key: k, domain: m.domain }).not.toEqual({ key: k, domain: "REAL" });
    }
    expect(s.equity.domain).not.toBe("REAL");
    expect(s.cash.domain).not.toBe("REAL");
  });

  it("defensive: null / undefined / malformed raw never throws, degrades to honest N/A", () => {
    const bads: unknown[] = [
      null, undefined, 42, "not-an-object", [],
      { account: null }, { account: 3 }, { account: { summary: "oops" } },
      { equity: { snapshots: [1, 2, 3] } },
      { orders: { orders: [null, 5, { status: 99 }] } },
      { providers: { verdict: "BLOCKED" } },
      { account: { summary: { equity: { nested: 1 } } } },
      { account: { account: { initialCash: "10000.00000000" } } }, // string, from events payload
    ];
    for (const b of bads) {
      expect(() => deriveCockpitSnapshot(b as CockpitRaw)).not.toThrow();
      const s = deriveCockpitSnapshot(b as CockpitRaw);
      for (const m of Object.values(s.metrics)) {
        if (m.value === null) expect(m.display).toBe("N/A");
        else expect(Number.isFinite(m.value)).toBe(true); // never NaN/Infinity leaks
      }
    }
  });

  it("sources list the real endpoints and warnings disclose NO_DATA / BLOCKED / no REAL", () => {
    const s = deriveCockpitSnapshot(realRaw());
    expect(s.sources).toContain("/api/jev/paper/account");
    expect(s.sources).toContain("/api/jev/paper/orders");
    expect(s.sources).toContain("/api/jev/paper/equity");
    expect(s.sources).toContain("/api/jev/providers/status");
    expect(s.warnings.some((w) => w.includes("NO_DATA"))).toBe(true);
    expect(s.warnings.some((w) => w.includes("BLOCKED"))).toBe(true);
    expect(s.warnings.some((w) => w.includes("REAL"))).toBe(true);
  });
});

describe("deriveProviderRows — providers/status = BLOCKED, 0 real inference records", () => {
  it("exactly 3 BLOCKED rows, all six metrics null on every row", () => {
    const rows = deriveProviderRows(realRaw());
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.displayName)).toEqual(["MiniMax", "M3", "阿里百炼"]);
    for (const r of rows) {
      expect(r.verdict).toBe("BLOCKED");
      const cells = Object.entries(r.metrics);
      expect(cells).toHaveLength(6);
      for (const [k, c] of cells) {
        expect({ row: r.id, metric: k, cell: c }).toEqual({ row: r.id, metric: k, cell: null });
      }
      // Explicitly forbid ANY numeric value anywhere in the metric block.
      expect(JSON.stringify(r.metrics)).not.toMatch(/\d/);
    }
  });

  it("API / inference success rate is NEVER repurposed as strategy win rate", () => {
    const s = deriveCockpitSnapshot(realRaw());
    const rows = deriveProviderRows(realRaw());
    expect(s.metrics.winRate.value).toBeNull();
    for (const r of rows) expect(r.metrics.inferenceSuccessRate).toBeNull();
  });

  it("MUTATION: VERIFIED + records + successRate decoy still keeps ALL cells N/A", () => {
    const raw = realRaw();
    const p = structuredClone(raw.providers) as Json;
    p["verdict"] = "VERIFIED";
    p["real_inference_records"] = 100;
    p["inference_success_rate"] = 0.99; // decoy
    p["providers"] = [
      { id: "MiniMax", successRate: 0.99, avgLatencyMs: 12, winRate: 0.8, tokenUsage: 1234 },
    ];
    const rows = deriveProviderRows({ ...raw, providers: p });
    expect(rows).toHaveLength(3);
    for (const r of rows) {
      for (const c of Object.values(r.metrics)) expect(c).toBeNull();
      expect(JSON.stringify(r.metrics)).not.toMatch(/\d/);
    }
    // The snapshot's strategy winRate must STILL be N/A — success rate != win rate.
    expect(deriveCockpitSnapshot({ ...raw, providers: p }).metrics.winRate.value).toBeNull();
  });

  it("defensive: null / malformed providers raw never throws", () => {
    for (const b of [null, undefined, 7, { providers: 3 }, { providers: { verdict: 5 } }]) {
      expect(() => deriveProviderRows(b as CockpitRaw)).not.toThrow();
      const rows = deriveProviderRows(b as CockpitRaw);
      expect(rows).toHaveLength(3);
      for (const r of rows) for (const c of Object.values(r.metrics)) expect(c).toBeNull();
    }
  });
});
