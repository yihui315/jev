// Pure presentational 3-model comparison table. Self-contained inline styles.
import type { CSSProperties, ReactNode } from "react";
import {
  COCKPIT_PALETTE as P,
  type MetricCell,
  type ProviderRow,
} from "../../metrics/contracts";

const thStyle: CSSProperties = {
  textAlign: "right", padding: "6px 8px", borderBottom: `1px solid ${P.border}`,
  color: P.muted, fontWeight: 500, whiteSpace: "nowrap",
};
const thFirstStyle: CSSProperties = { ...thStyle, textAlign: "left" };
const tdStyle: CSSProperties = {
  textAlign: "right", padding: "6px 8px", borderBottom: `1px solid ${P.border}`,
  whiteSpace: "nowrap", color: P.text,
};
const tdFirstStyle: CSSProperties = { ...tdStyle, textAlign: "left" };

function verdictStyle(v: ProviderRow["verdict"]): CSSProperties {
  const color = v === "BLOCKED" ? P.down : v === "NO_DATA" ? P.muted : P.up;
  return {
    fontFamily: "ui-monospace, Consolas, monospace", fontSize: 10.5,
    padding: "1px 7px", borderRadius: 999, border: `1px solid ${color}`,
    color, whiteSpace: "nowrap",
  };
}

function NaValue({ cell }: { cell: MetricCell | null }) {
  if (!cell || cell.value === null) {
    return (
      <span style={{ color: P.muted }} title={cell?.naReason ?? "无真实推理记录"}>
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
    { key: "isr", label: "推理成功率", render: (r) => <NaValue cell={r.metrics.inferenceSuccessRate} /> },
    { key: "lat", label: "平均延迟", render: (r) => <NaValue cell={r.metrics.avgLatencyMs} /> },
    { key: "tok", label: "Token", render: (r) => <NaValue cell={r.metrics.tokenUsage} /> },
    { key: "cost", label: "API 成本", render: (r) => <NaValue cell={r.metrics.apiCostUsd} /> },
    { key: "dir", label: "决策方向", render: (r) => r.decisionDirection ?? "N/A" },
    { key: "risk", label: "风险拒绝率", render: (r) => <NaValue cell={r.metrics.riskRejectRate} /> },
    { key: "pnl", label: "归因净收益", render: (r) => <NaValue cell={r.metrics.attributedNetPnl} /> },
    { key: "verdict", label: "Verdict", render: (r) => <span style={verdictStyle(r.verdict)}>{r.verdict}</span> },
  ];

  return (
    <section style={{
      background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10,
      padding: 14, minWidth: 0,
    }}>
      <div style={{
        color: P.amber, fontSize: 11.5, lineHeight: 1.5, marginBottom: 12,
        borderBottom: `1px dashed ${P.border}`, paddingBottom: 10,
      }}>
        统一行情窗口、统一策略规则、统一风险预算（待真实推理记录）；API 成功率 ≠ 策略胜率
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{
          width: "100%", borderCollapse: "collapse",
          fontFamily: "ui-monospace, Consolas, monospace", fontSize: 11.5,
        }}>
          <thead>
            <tr>
              <th style={thFirstStyle}>模型</th>
              {cols.map((c) => <th key={c.key} style={thStyle}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={cols.length + 1} style={{ ...tdStyle, textAlign: "center", color: P.muted }}>
                  无模型对比数据（N/A）
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
      <div style={{ color: P.muted, fontSize: 10.5, lineHeight: 1.5, marginTop: 10 }}>
        {rows[0]?.note ?? ""}
      </div>
    </section>
  );
}
