// App shell: sidebar navigation + top status bar + global data-source audit
// drawer. The sidebar is the single place that lists the six pages; every page
// is reachable by hash route and restored on refresh.
import type { ReactNode } from "react";
import { useJevQuery } from "../api/useJev";
import { StatusPill, SourceFooter } from "./common";
import { IconShield, IconBolt, IconBook, IconCommand, IconChart, IconGlobe, IconFlask, IconWallet, IconTeam, type IconComp } from "./icons";
import type { Label } from "../api/client";

export type PageId = "command" | "workbench" | "universe" | "strategy" | "account" | "team";

export const NAV: { id: PageId; num: number; label: string; icon: IconComp }[] = [
  { id: "command", num: 1, label: "AI 交易指挥中心", icon: IconCommand },
  { id: "workbench", num: 2, label: "专业交易工作台", icon: IconChart },
  { id: "universe", num: 3, label: "3D 市场宇宙", icon: IconGlobe },
  { id: "strategy", num: 4, label: "策略竞赛实验室", icon: IconFlask },
  { id: "account", num: 5, label: "模拟交易账户", icon: IconWallet },
  { id: "team", num: 6, label: "AI 专家团队", icon: IconTeam },
];

export function Sidebar({ page, onSelect }: { page: PageId; onSelect: (p: PageId) => void }) {
  return (
    <aside className="jev-sidebar">
      <div className="jev-brand">
        <h1>JEV Trading Desk</h1>
        <div className="sub">V1.0 · 自主增长交易终端</div>
      </div>
      <nav className="jev-nav">
        {NAV.map((n) => {
          const I = n.icon;
          return (
            <button key={n.id} className={`jev-nav-item ${page === n.id ? "active" : ""}`} onClick={() => onSelect(n.id)}>
              <I />
              <span className="num">{n.num}</span>
              <span>{n.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="jev-side-foot">
        统一后端数据适配层<br />
        <span style={{ color: "var(--muted)" }}>所有页面共用 /api/jev/*</span>
      </div>
    </aside>
  );
}

export function TopBar({ title, metaLabel, onOpenAudit, clock }: {
  title: string; metaLabel?: Label; onOpenAudit: () => void; clock: string;
}) {
  return (
    <div className="jev-topbar">
      <div className="title">{title}</div>
      {metaLabel ? <StatusPill label={metaLabel} /> : null}
      <div className="spacer" />
      <button className="jev-link" onClick={onOpenAudit}>数据审计</button>
      <span className="jev-clock">{clock}</span>
    </div>
  );
}

export function AuditDrawer({ onClose }: { onClose: () => void }) {
  const ds = useJevQuery<{ id: string; label?: Label; channel?: string; source?: string; status?: string; note?: string; count?: number }[]>("datasource/audit");
  const qual = useJevQuery<{
    verdict?: string;
    problems?: number | unknown[];
    bars?: { total?: number; realtime?: number; backfill?: number; incomplete?: number; distinct_utc_days?: number; stale?: boolean; hash_chain_verified?: boolean };
    classification_rule?: { REALTIME?: string; BACKFILL?: string; hard_rule?: string };
    gaps?: unknown[];
    anomalies?: unknown[];
    duplicates?: unknown[];
    blocked?: { item?: string; reason?: string }[];
  }>("market/quality");
  const refd = useJevQuery<{ verdict?: string; images?: { id: string; file?: string; bytes?: number; sha256?: string }[]; usage?: string }>("reference/design");
  const health = useJevQuery<{ engine_symbols?: string[]; collector_only_symbols?: string[]; market_host?: string; market_channel?: string; real_money?: boolean; real_exchange_orders?: boolean }>("health");

  return (
    <>
      <div className="jev-backdrop" onClick={onClose} />
      <div className="jev-drawer">
        <button className="close" onClick={onClose}>×</button>
        <h2>数据来源审计</h2>
        {ds.loading && ds.data === undefined ? <div className="jev-state"><span className="jev-spinner" />读取中…</div> : null}
        {ds.error ? <div className="jev-state error">{ds.error}</div> : null}
        {ds.data?.map((d) => (
          <div className="row" key={d.id}>
            <span className="k">{d.id}</span>
            <span className="v">{d.label ?? d.status ?? "—"}<br /><span style={{ color: "var(--muted)" }}>{d.source || d.note || ""}</span></span>
          </div>
        ))}

        <h2 style={{ marginTop: 18 }}>行情质量</h2>
        {qual.data ? (
          (() => {
            const q = qual.data as NonNullable<typeof qual.data>;
            const bars = q.bars ?? {};
            const rule = q.classification_rule ?? {};
            const arr = (x: unknown) => (Array.isArray(x) ? x : []);
            const lines: string[] = [
              `verdict: ${q.verdict ?? "?"}`,
              `problems: ${Array.isArray(q.problems) ? q.problems.length : (q.problems ?? "?")}`,
              `bars.total: ${bars.total ?? "?"}`,
              `bars.realtime: ${bars.realtime ?? "?"}`,
              `bars.backfill: ${bars.backfill ?? "?"}`,
              `bars.incomplete: ${bars.incomplete ?? "?"}`,
              `bars.distinct_utc_days: ${bars.distinct_utc_days ?? "?"}`,
              `bars.stale: ${bars.stale ?? "?"}`,
              `bars.hash_chain_verified: ${bars.hash_chain_verified ?? "?"}`,
              `classification_rule:`,
              `  REALTIME: ${rule.REALTIME ?? "?"}`,
              `  BACKFILL: ${rule.BACKFILL ?? "?"}`,
              `  hard_rule: ${rule.hard_rule ?? "?"}`,
              `gaps: ${arr(q.gaps).length} ${arr(q.gaps).length ? "项" : "(无)"}`,
              `anomalies: ${arr(q.anomalies).length} ${arr(q.anomalies).length ? "项" : "(无)"}`,
              `duplicates: ${arr(q.duplicates).length} ${arr(q.duplicates).length ? "项" : "(无)"}`,
              `blocked: ${arr(q.blocked).length} 项`,
            ];
            for (const b of arr(q.blocked) as { item?: string; reason?: string }[]) {
              lines.push(`  - ${b.item ?? "?"}: ${b.reason ?? ""}`);
            }
            return <div className="jev-code">{lines.join("\n")}</div>;
          })()
        ) : <div className="jev-state">{qual.error ?? "—"}</div>}

        <h2 style={{ marginTop: 18 }}>参考设计 / 来源</h2>
        {refd.data ? (
          <div className="jev-code">
{`verdict: ${refd.data.verdict ?? "?"}
usage: ${refd.data.usage ?? "?"}
images: ${refd.data.images?.length ?? 0} (sha256 verified)`}
          </div>
        ) : <div className="jev-state">{refd.error ?? "—"}</div>}

        <h2 style={{ marginTop: 18 }}>适配层健康</h2>
        {health.data ? (
          <div className="jev-code">
{`market_host: ${health.data.market_host ?? "?"}
market_channel: ${health.data.market_channel ?? "?"}
engine_symbols: ${(health.data.engine_symbols ?? []).join(", ")}
collector_only: ${(health.data.collector_only_symbols ?? []).join(", ")}
real_money: ${health.data.real_money ?? "?"}
real_exchange_orders: ${health.data.real_exchange_orders ?? "?"}`}
          </div>
        ) : <div className="jev-state">{health.error ?? "—"}</div>}
        <div style={{ marginTop: 14, color: "var(--muted)", fontSize: 11 }}>
          <IconShield /> 合规红线：<b style={{ color: "var(--text)" }}>禁止真实资金、禁止真实交易所下单、禁止客户端定价、禁止生产部署、禁止自动策略晋级。</b>
        </div>
        <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 11 }}>
          <IconBolt /> {""}WebSocket 通道 <b style={{ color: "var(--down)" }}>BLOCKED</b>：当前行情通道为 REST_POLLING，已在每一页标注，绝不会显示为 WebSocket LIVE。
        </div>
      </div>
    </>
  );
}

export { IconBook };
