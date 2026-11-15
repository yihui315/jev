// Pure presentational cost attribution + risk red-line panel.
// Red-line values are read ONLY from `system`; missing => "红线状态 N/A",
// never inferred as "safe". Self-contained inline styles.
import {
  COCKPIT_PALETTE as P,
  type CockpitSnapshot,
  type MetricCell,
} from "../../metrics/contracts";

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function unwrap(v: unknown): unknown {
  if (isRecord(v) && ("schema" in v || "meta" in v) && isRecord(v.data)) return v.data;
  return v;
}

function pickValue(root: unknown, paths: string[][]): { value: unknown; hit: string } | null {
  if (!isRecord(root)) return null;
  for (const path of paths) {
    let cur: unknown = root;
    let ok = true;
    for (const key of path) {
      if (isRecord(cur) && key in cur) cur = cur[key];
      else { ok = false; break; }
    }
    if (ok && cur !== null && cur !== undefined) return { value: cur, hit: path.join(".") };
  }
  return null;
}

function flagText(v: unknown): string | null {
  if (typeof v === "boolean") return String(v);
  if (typeof v === "string") return v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}

const RED_LINES: { label: string; paths: string[][] }[] = [
  {
    label: "真实交易开关 enableRealTrading",
    paths: [
      ["enableRealTrading"], ["realTradingEnabled"], ["allow_real_trading"],
      ["red_lines", "real_money"], ["red_lines", "real_exchange_orders"],
    ],
  },
  {
    label: "买卖开关 allowBuySell",
    paths: [["allowBuySell"], ["allow_buy_sell"], ["trading_allowed"]],
  },
  {
    label: "WebSocket 行情通道",
    paths: [["limits", "websocket"], ["websocket"], ["websocket_status"], ["market_channel"]],
  },
];

function CostCell({ label, cell }: { label: string; cell: MetricCell }) {
  return (
    <div style={{
      flex: "1 1 180px", minWidth: 160, background: P.surface2,
      border: `1px solid ${P.border}`, borderRadius: 8, padding: "10px 12px",
      display: "flex", flexDirection: "column", gap: 5,
    }}>
      <span style={{ color: P.muted, fontSize: 11 }}>{label}</span>
      <span
        title={cell.naReason ?? undefined}
        style={{
          fontFamily: "ui-monospace, Consolas, monospace", fontSize: 18,
          color: cell.value === null ? P.muted : P.text,
        }}
      >
        {cell.display}
      </span>
      <span style={{ color: P.muted, fontSize: 10, lineHeight: 1.3, minHeight: 13 }}>
        {cell.naReason ?? cell.domain}
      </span>
    </div>
  );
}

export function CostAttribution(props: { snapshot: CockpitSnapshot; system?: unknown }) {
  const s = props.snapshot;
  const sys = unwrap(props.system);
  const redLines = RED_LINES.map((rl) => {
    const hit = pickValue(sys, rl.paths);
    return {
      label: rl.label,
      value: hit ? flagText(hit.value) : null,
      field: hit ? hit.hit : null,
    };
  });
  const anyKnown = redLines.some((r) => r.value !== null);

  const costItems: { label: string; cell: MetricCell }[] = [
    { label: "AI 推理成本", cell: s.metrics.aiInferenceCost },
    { label: "交易手续费", cell: s.metrics.tradingFees },
    { label: "滑点成本", cell: s.metrics.slippage },
    { label: "净利润归因 Net P&L", cell: s.metrics.netPnl },
  ];

  return (
    <section style={{
      background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10,
      padding: 14, minWidth: 0,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: P.text, marginBottom: 12 }}>
        成本归因与风险告警
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
        {costItems.map((it) => <CostCell key={it.label} label={it.label} cell={it.cell} />)}
      </div>
      <div style={{
        border: `1px solid ${P.amber}`, background: "rgba(245,181,68,.06)",
        borderRadius: 8, padding: "10px 12px",
      }}>
        <div style={{ color: P.amber, fontSize: 11.5, marginBottom: 8, lineHeight: 1.5 }}>
          风险告警 · 红线状态（仅展示从 system 读到的真实值，绝不按「安全」处理）
        </div>
        {anyKnown ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {redLines.map((r) => (
              <div key={r.label} style={{
                display: "flex", justifyContent: "space-between", gap: 10,
                fontFamily: "ui-monospace, Consolas, monospace", fontSize: 11.5,
              }}>
                <span style={{ color: P.muted }}>
                  {r.label}{r.field ? ` [${r.field}]` : ""}
                </span>
                <span style={{ color: r.value === null ? P.muted : P.amber }}>
                  {r.value ?? "N/A"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ color: P.amber, fontSize: 12 }}>红线状态 N/A</div>
        )}
        <div style={{ color: P.muted, fontSize: 10.5, marginTop: 8, lineHeight: 1.5 }}>
          取不到的字段一律 N/A；请勿把缺失值推断为「已启用 / 安全」。
        </div>
      </div>
    </section>
  );
}
