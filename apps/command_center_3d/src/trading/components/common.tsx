// Shared presentational components for JEV Trading Desk V1.0.
import type { ReactNode } from "react";
import { labelClass, type JevMeta, type JevResult } from "../api/client";

export function StatusPill({ label }: { label?: string }) {
  const cls = labelClass((label as never) || "NO_DATA");
  return <span className={cls}>{label ?? "NO DATA"}</span>;
}

export function Panel({ title, sub, right, children }: {
  title: string; sub?: string; right?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="jev-panel">
      <div className="ph">
        <h2>{title}</h2>
        {sub ? <span className="ph-sub">{sub}</span> : null}
        {right}
      </div>
      {children}
    </section>
  );
}

export function Metric({ label, value, tone }: { label: string; value: ReactNode; tone?: "up" | "down" }) {
  return (
    <div className="jev-metric">
      <span className="label">{label}</span>
      <span className={`value ${tone ? "jev-" + tone : ""}`}>{value}</span>
    </div>
  );
}

export function Banner({ kind, children }: { kind?: "crit" | "info"; children: ReactNode }) {
  return <div className={`jev-banner ${kind ?? "info"}`}>{children}</div>;
}

/** Honest state rendering: loading / error / blocked / no-data. */
export function StateView({ q, emptyText }: {
  q: { loading: boolean; error?: string; unreachable: boolean; data?: unknown };
  emptyText?: string;
}) {
  if (q.loading && q.data === undefined) {
    return (
      <div className="jev-state">
        <span className="jev-spinner" />
        正在从统一适配层读取真实数据…
      </div>
    );
  }
  if (q.unreachable || q.error) {
    return (
      <div className="jev-state error">
        <div className="big">无法从适配层获取数据</div>
        <div>{q.error ?? "网络不可达 / 适配器未运行"}</div>
      </div>
    );
  }
  if (emptyText && (q.data === undefined || q.data === null ||
      (Array.isArray(q.data) && q.data.length === 0) ||
      (typeof q.data === "object" && Object.keys(q.data as object).length === 0))) {
    return (
      <div className="jev-state">
        <div className="big">{emptyText}</div>
        <div>不是故障，而是该数据尚未产生（例如：无真实 OOS 样本、无 Equity 快照）。</div>
      </div>
    );
  }
  return null;
}

export function SourceFooter({ meta }: { meta?: JevMeta }) {
  if (!meta) return null;
  const rows: [string, string | undefined][] = [
    ["来源", meta.source],
    ["通道", meta.channel],
    ["标签", meta.label],
    ["生成UTC", meta.generated_utc || meta.fetched_at_utc],
    ["HTTP", meta.http_status !== undefined ? String(meta.http_status) : undefined],
  ];
  return (
    <div className="jev-source">
      {rows.filter(([, v]) => v !== undefined).map(([k, v]) => (
        <span key={k}><span className="k">{k}:</span> <span className="v">{v}</span></span>
      ))}
      {meta.websocket_note ? <span className="v" style={{ color: "var(--amber)" }}>⚠ {meta.websocket_note}</span> : null}
    </div>
  );
}

export function DataTable<T>({ columns, rows, keyOf }: {
  columns: { key: string; label: string }[];
  rows: T[];
  keyOf: (r: T, i: number) => string;
}) {
  return (
    <div className="jev-scroll" style={{ overflowX: "auto" }}>
      <table className="jev-table">
        <thead>
          <tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={keyOf(r, i)}>
              {columns.map((c) => {
                const v = (r as Record<string, unknown>)[c.key];
                const tone = typeof v === "number" && (c.key.toLowerCase().includes("pnl") || c.key.toLowerCase().includes("realized") || c.key.toLowerCase().includes("unreal"))
                  ? (v as number) >= 0 ? "jev-up" : "jev-down" : "";
                return <td key={c.key} className={tone}>{fmt(v)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}

export function fmtUsd(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
