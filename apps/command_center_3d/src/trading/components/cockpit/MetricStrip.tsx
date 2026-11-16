// Professional KPI strip — seven source-backed headline metrics.
// Every cell retains its PAPER / REAL / UNAVAILABLE domain and explains N/A.
import type { CockpitSnapshot, DataDomain } from "../../metrics/contracts";

interface KpiItem {
  label: string;
  display: string;
  domain: DataDomain;
  note: string;
  naReason: string | null;
  tone?: "up" | "down";
}

function KpiCell(props: KpiItem) {
  const tone = props.display === "N/A" ? "is-na" : props.tone ? "tone-" + props.tone : "";
  const note = props.naReason ?? props.note;
  return (
    <div className={"ck-kpi-card " + tone} data-domain={props.domain}>
      <div className="ck-kpi-top">
        <span className="ck-kpi-label">{props.label}</span>
        <span className={"ck-kpi-domain " + props.domain.toLowerCase()}>{props.domain}</span>
      </div>
      <div
        className="ck-kpi-value"
        title={props.naReason ?? props.note}
        aria-label={props.label + ": " + props.display}
      >
        {props.display}
      </div>
      <div className="ck-kpi-note">{note}</div>
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
      label: "账户权益 · EQUITY",
      display: s.equity.display,
      domain: s.equity.domain,
      note: "来源：PAPER 账户",
      naReason: s.equity.value === null ? "paper/account 未提供可核验权益字段" : null,
    },
    {
      label: "可用现金 · CASH",
      display: s.cash.display,
      domain: s.cash.domain,
      note: "来源：PAPER 账户",
      naReason: s.cash.value === null ? "paper/account 未提供可核验现金字段" : null,
    },
    {
      label: "净收益 · NET P&L",
      display: net.display,
      domain: net.domain,
      note: "来源：服务端模拟账本",
      naReason: net.naReason,
      tone,
    },
    {
      label: "投资回报率 · ROI",
      display: s.metrics.roi.display,
      domain: s.metrics.roi.domain,
      note: "基于服务端初始资金与当前权益",
      naReason: s.metrics.roi.naReason,
    },
    {
      label: "策略胜率 · WIN RATE",
      display: s.metrics.winRate.display,
      domain: s.metrics.winRate.domain,
      note: "需要真实已平仓交易样本",
      naReason: s.metrics.winRate.naReason,
    },
    {
      label: "订单成交率 · FILL RATE",
      display: s.metrics.fillRate.display,
      domain: s.metrics.fillRate.domain,
      note: "基于服务端订单状态",
      naReason: s.metrics.fillRate.naReason,
    },
    {
      label: "最大回撤 · MAX DD",
      display: s.metrics.maxDrawdown.display,
      domain: s.metrics.maxDrawdown.domain,
      note: "仅基于真实权益快照",
      naReason: s.metrics.maxDrawdown.naReason,
    },
  ];

  return (
    <section className="ck-kpi-strip" aria-label="核心绩效指标">
      {items.map((item) => <KpiCell key={item.label} {...item} />)}
    </section>
  );
}
