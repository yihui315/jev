// Pure presentational 3-model comparison table. Self-contained inline styles.
import type { CSSProperties, ReactNode } from "react";
import {
  COCKPIT_PALETTE as P,
  type MetricCell,
  type ProviderRow,
} from "../../metrics/contracts";

const MONO = "ui-monospace, SFMono-Regular, Consolas, monospace";

const thStyle: CSSProperties = {
  textAlign: "right", padding: "8px 8px", borderBottom: `1px solid ${P.border}`,
  color: P.muted, fontFamily: MONO, fontSize: 10, fontWeight: 700,
  letterSpacing: 0.6, textTransform: "uppercase", whiteSpace: "nowrap",
};
const thFirstStyle: CSSProperties = { ...thStyle, textAlign: "left" };
const tdStyle: CSSProperties = {
  textAlign: "right", padding: "8px 8px", borderBottom: `1px solid ${P.border}`,
  whiteSpace: "nowrap", color: P.text, fontFamily: MONO, fontSize: 11.5,
};
const tdFirstStyle: CSSProperties = { ...tdStyle, textAlign: "left", fontWeight: 600 };

function verdictStyle(v: ProviderRow["verdict"]): CSSProperties {
  const color = v === "BLOCKED" ? P.down : v === "NO_DATA" ? P.muted : P.up;
  const bg = v === "BLOCKED"
    ? "rgba(255,90,106,.12)"
    : v === "VERIFIED" ? "rgba(34,211,166,.12)" : "transparent";
  return {
    fontFamily: MONO, fontSize: 10, fontWeight: 700, letterSpacing: 0.6,
    padding: "2px 8px", borderRadius: 999, border: `1px solid ${color}`,
    background: bg, color, whiteSpace: "nowrap",
  };
}

function NaValue({ cell }: { cell: MetricCell | null }) {
  if (!cell || cell.value === null) {
    return (
      <span
        style={{ color: P.muted, borderBottom: `1px dotted ${P.border}`, cursor: "help" }}
        title={cell?.naReason ?? "无真实推理记录；禁止以 API 成功率冒充策略胜率"}
      >
        N/A
      </span>
    );
  }
  return <span title={cell.naReason ?? undefined}>{cell.display}</span>;
}

interface Col {
  key: string;
  label: string;
  render: (r: ProviderRow) => ReactNode;
}

export function ProviderComparison(props: { rows: ProviderRow[] }) {
  const rows = Array.isArray(props.rows) ? props.rows : [];
  const cols: Col[] = [
    { key: "isr", label: "INFERENCE 推理成功率", render: (r) => <NaValue cell={r.metrics.inferenceSuccessRate} /> },
    { key: "lat", label: "LATENCY 平均延迟", render: (r) => <NaValue cell={r.metrics.avgLatencyMs} /> },
    { key: "tok", label: "TOKENS", render: (r) => <NaValue cell={r.metrics.tokenUsage} /> },
    { key: "cost", label: "API COST 成本", render: (r) => <NaValue cell={r.metrics.apiCostUsd} /> },
    { key: "dir", label: "DECISION 决策方向", render: (r) => r.decisionDirection ?? <NaValue cell={null} /> },
    { key: "risk", label: "RISK REJECT 风险拒绝", render: (r) => <NaValue cell={r.metrics.riskRejectRate} /> },
    { key: "pnl", label: "ATTRIBUTED P&L", render: (r) => <NaValue cell={r.metrics.attributedNetPnl} /> },
    { key: "verdict", label: "VERDICT", render: (r) => <span style={verdictStyle(r.verdict)}>{r.verdict}</span> },
  ];

  return (
    <section style={{
      background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10,
      padding: 14, minWidth: 0,
    }}>
      <div style={{
        color: P.amber, fontSize: 11.5, fontWeight: 600, lineHeight: 1.5, marginBottom: 12,
        borderBottom: `1px dashed ${P.border}`, paddingBottom: 10, fontFamily: MONO,
      }}>
        统一行情窗口、统一策略规则、统一风险预算（待真实推理记录）；API 成功率 ≠ 策略胜率
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{
          width: "100%", borderCollapse: "collapse", fontFamily: MONO,
        }}>
          <thead>
            <tr>
              <th style={thFirstStyle}>MODEL 模型</th>
              {cols.map((c) => <th key={c.key} style={thStyle}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={cols.length + 1} style={{
                  ...tdStyle, textAlign: "center", color: P.muted, padding: "28px 8px",
                }}>
                  <div style={{ fontSize: 12, color: P.text, marginBottom: 6 }}>N/A · 无模型对比数据</div>
                  <div style={{ fontSize: 10.5 }}>未接收到 providers/status，全部指标按无真实推理记录处理</div>
                </td>
              </tr>
            ) : rows.map((r) => (
              <tr key={r.id}>
                <td style={tdFirstStyle}>{r.displayName}</td>
                {cols.map((c) => <td key={c.key} style={tdStyle}>{c.render(r)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ color: P.muted, fontSize: 10.5, lineHeight: 1.5, marginTop: 10, fontFamily: MONO }}>
        {rows[0]?.note ?? "N/A（无真实推理记录）"}
      </div>
    </section>
  );
}
