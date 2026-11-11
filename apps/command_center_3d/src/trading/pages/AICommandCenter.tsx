// Page 1 — AI 交易指挥中心. Aggregate real evidence: system status, data-source
// audit, six-role provider state (BLOCKED), and a critical-truth banner that
// separates the 14 server fills (deterministic driver) from six-role AI.
import { useJevQuery } from "../api/useJev";
import { Panel, Metric, Banner, StatusPill, SourceFooter, StateView } from "../components/common";
import { TopBar } from "../components/Shell";
import { deriveStatusLabel } from "../../views/trading/components/PageContainer";

export function AICommandCenter({ topbar }: { topbar: React.ReactNode }) {
  const sys = useJevQuery<any>("system/status");
  const ds = useJevQuery<any[]>("datasource/audit");
  const agents = useJevQuery<any>("agents/status");
  const prov = useJevQuery<any>("providers/status");
  const ticker = useJevQuery<any[]>("market/ticker?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%2C%22ORDIUSDT%22%2C%221000SATSUSDT%22%5D");

  const d = sys.data;
  const day = d?.day;
  const wl = d?.work_lines || [];
  const todo = (t: any) => (t?.blocked?.length ? t.blocked.length : 0);

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="crit">
          <b>关键事实（必须诚实区分）：</b> 服务端 Paper Trading 已通过两次真实行情驱动 Gate 验收，
          累计 <b>14 笔模拟成交</b>，但这些成交来自<b>确定性验收 Driver</b>，<b>并非 AI 六角色自主决策</b>。
          当前 AI Provider 凭据为 <b>0</b>，真实推理记录为 <b>0</b>。下方「AI 专家团队」页同样标注 BLOCKED。
        </Banner>

        <div className="jev-grid cols-4">
          <Panel title="30 天运行时钟"><Metric label="当前进度" value={day ? `${day.day} / ${day.of} 天` : "—"} /><Metric label="真实运行小时" value={day ? day.elapsed_real_hours?.toFixed(1) + " h" : "—"} /><SourceFooter meta={sys.meta} /></Panel>
          <Panel title="工程门禁"><Metric label="独立审计" value={d?.gate?.independent_audit?.verdict ?? "—"} /><Metric label="已验证检查" value={d?.gate?.independent_audit?.verified_checks ?? "—"} /><Metric label="失败" value={d?.gate?.independent_audit?.failed_checks ?? "—"} /><SourceFooter meta={sys.meta} /></Panel>
          <Panel title="真实行情"><Metric label="通道" value={d?.limits?.websocket === "BLOCKED" ? "REST_POLLING" : "—"} /><Metric label="WebSocket" value={<span style={{ color: "var(--down)" }}>BLOCKED</span>} /><SourceFooter meta={sys.meta} /></Panel>
          <Panel title="AI 六角色"><Metric label="状态" value={<span style={{ color: "var(--down)" }}>BLOCKED</span>} /><Metric label="凭据" value={prov.data?.credentials ?? 0} /><Metric label="真实推理" value={agents.data?.inference_records?.length ?? 0} /><SourceFooter meta={agents.meta} /></Panel>
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <Panel title="七条工作线状态" sub={(() => { const b = wl.filter((w: any) => w.status === "BLOCKED").length; return `${b} 阻塞 / ${wl.length} 总数`; })()}>
            <StateView q={sys} />
            {wl.length ? (
              <table className="jev-table">
                <thead><tr><th>ID</th><th>标题</th><th>状态</th><th>门禁</th><th>阻塞项</th></tr></thead>
                <tbody>
                  {wl.map((w: any) => (
                    <tr key={w.id}>
                      <td>{w.id}</td><td style={{ textAlign: "left" }}>{w.title}</td>
                      <td><StatusPill label={w.status} /></td>
                      <td>{w.gate_verdict ?? "—"}</td>
                      <td style={{ textAlign: "left" }}>{todo(w) > 0 ? `${todo(w)} 项` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            <SourceFooter meta={sys.meta} />
          </Panel>

          <Panel title="数据来源审计" sub={ds.data?.length + " 项"}>
            <StateView q={ds} />
            {ds.data?.map((s: any) => (
              <div className="jev-row" key={s.id} style={{ justifyContent: "space-between", padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ textAlign: "left" }}>{s.id}<div style={{ color: "var(--muted)", fontSize: 11 }}>{s.source || s.note}</div></span>
                <StatusPill label={s.label ?? s.status} />
              </div>
            ))}
            <SourceFooter meta={ds.meta} />
          </Panel>
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <Panel title="实时行情快照（REST_POLLING）" sub="BTC/ETH/ORDI/1000SATS" right={<StatusPill label={deriveStatusLabel(ticker)} />}>
            <StateView q={ticker} emptyText="暂无行情" />
            {ticker.data?.map((t: any) => {
              const chg = Number(t.priceChangePercent);
              return (
                <div className="jev-row" key={t.symbol} style={{ justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ textAlign: "left" }}>{t.symbol}</span>
                  <span>{(Number(t.lastPrice)).toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>
                  <span className={chg >= 0 ? "jev-up" : "jev-down"}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span>
                </div>
              );
            })}
            <SourceFooter meta={ticker.meta} />
          </Panel>

          <Panel title="AI Provider 状态" sub="BLOCKED">
            <StateView q={prov} />
            {prov.data ? (
              <>
                <div className="jev-code">{prov.data.report_excerpt || "—"}</div>
                <div style={{ marginTop: 10, color: "var(--muted)", fontSize: 11 }}>
                  命名差异：下单角色 [{prov.data.naming_discrepancy?.order_roles?.join(", ")}] 与代码角色 [{prov.data.naming_discrepancy?.code_roles?.join(", ")}] 不完全一致，需人工裁定映射。
                </div>
              </>
            ) : null}
            <SourceFooter meta={prov.meta} />
          </Panel>
        </div>
      </div>
    </>
  );
}
