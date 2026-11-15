// Pure presentational KPI strip. Self-contained: COCKPIT_PALETTE + inline
// styles only, no dependency on styles.css class names.
import type { CSSProperties } from "react";
import {
  COCKPIT_PALETTE as P,
  type CockpitSnapshot,
  type DataDomain,
} from "../../metrics/contracts";

function domainBadgeStyle(domain: DataDomain): CSSProperties {
  const color = domain === "PAPER" ? P.accent : domain === "REAL" ? P.up : P.muted;
  const border = domain === "PAPER" ? P.accent : domain === "REAL" ? P.up : P.border;
  return {
    fontFamily: "ui-monospace, Consolas, monospace",
    fontSize: 10, lineHeight: 1.4, padding: "1px 6px", borderRadius: 999,
    border: `1px solid ${border}`, color, background: "transparent",
    whiteSpace: "nowrap", letterSpacing: 0.3,
  };
}

interface KpiItem {
  label: string;
  display: string;
  domain: DataDomain;
  naReason: string | null;
  tone?: "up" | "down";
}

function KpiCell(props: KpiItem) {
  const valueColor = props.display === "N/A"
    ? P.muted
    : props.tone === "up" ? P.up : props.tone === "down" ? P.down : P.text;
  return (
    <div style={{
      flex: "1 1 170px", minWidth: 160, display: "flex", flexDirection: "column", gap: 6,
      background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10,
      padding: "12px 14px",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ color: P.muted, fontSize: 11 }}>{props.label}</span>
        <span style={domainBadgeStyle(props.domain)}>{props.domain}</span>
      </div>
      <div
        title={props.naReason ?? undefined}
        style={{
          fontFamily: "ui-monospace, Consolas, monospace", fontSize: 22,
          letterSpacing: 0.3, color: valueColor, lineHeight: 1.2,
        }}
      >
        {props.display}
      </div>
      <div style={{ color: P.muted, fontSize: 10.5, minHeight: 14, lineHeight: 1.3 }}>
        {props.naReason ?? (props.domain === "PAPER" ? "来源：PAPER 模拟盘" : "")}
      </div>
    </div>
  );
}

export function MetricStrip(props: { snapshot: CockpitSnapshot }) {
  const s = props.snapshot;
  const net = s.metrics.netPnl;
  const tone: "up" | "down" | undefined =
    net.value === null ? undefined : net.value >= 0 ? "up" : "down";

  const items: KpiItem[] = [
    {
      label: "账户权益 Equity",
      display: s.equity.display,
      domain: s.equity.domain,
      naReason: s.equity.value === null ? "paper/account 未提供真实权益快照" : null,
    },
    {
      label: "净收益 Net P&L",
      display: net.display,
      domain: net.domain,
      naReason: net.naReason,
      tone,
    },
    {
      label: "投资回报率 ROI",
      display: s.metrics.roi.display,
      domain: s.metrics.roi.domain,
      naReason: s.metrics.roi.naReason,
    },
    {
      label: "策略胜率 Win Rate",
      display: s.metrics.winRate.display,
      domain: s.metrics.winRate.domain,
      naReason: s.metrics.winRate.naReason,
    },
    {
      label: "最大回撤 Max DD",
      display: s.metrics.maxDrawdown.display,
      domain: s.metrics.maxDrawdown.domain,
      naReason: s.metrics.maxDrawdown.naReason,
    },
  ];

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, width: "100%" }}>
      {items.map((it) => <KpiCell key={it.label} {...it} />)}
    </div>
  );
}
