// Page 4 — 策略竞赛实验室. Honest: real bar foundation counts are shown as
// SAMPLE FOUNDATION, but NO backtest performance, NO OOS ranking, and NO
// auto-promotion are displayed because there are zero real OOS samples. Strategy
// cards are explicitly NO_DATA and the rules forbid fabricating any of it.
import { useJevQuery } from "../api/useJev";
import { Panel, Metric, Banner, StatusPill, SourceFooter, StateView } from "../components/common";
import { TopBar } from "../components/Shell";

export function StrategyArena({ topbar }: { topbar: React.ReactNode }) {
  const st = useJevQuery<any>("strategies/status");
  const q = useJevQuery<any>("market/quality");

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="crit">
          <b>策略实验室当前不展示任何绩效数字。</b> 没有真实 OOS（样本外）样本，因此<b>不生成排名、不输出收益/回撤/胜率、不允许自动晋级</b>。
          下方「样本基础」中的 K 线数量是数据采集进度的真实体现，<b>不是策略收益</b>；BACKFILL 历史回填不计入实时增量。
        </Banner>

        <div className="jev-grid cols-4">
          <Panel title="策略状态"><Metric label="判定" value={<span style={{ color: "var(--muted)" }}>NO_DATA</span>} /><Metric label="真实 OOS 样本" value={st.data?.oos_samples ?? 0} /><Metric label="回测运行" value={st.data?.backtest_runs ?? 0} /><SourceFooter meta={st.meta} /></Panel>
          <Panel title="样本基础（非收益）"><Metric label="K 线总数" value={q.data?.bars?.total ?? "—"} /><Metric label="实时" value={q.data?.bars?.realtime ?? "—"} /><Metric label="回填" value={q.data?.bars?.backfill ?? "—"} /><SourceFooter meta={q.meta} /></Panel>
          <Panel title="质量"><Metric label="缺口" value={q.data?.bars?.gaps ?? "—"} /><Metric label="异常" value={q.data?.bars?.anomalies ?? "—"} /><Metric label="陈旧" value={q.data?.bars?.stale ? "是" : "否"} /><SourceFooter meta={q.meta} /></Panel>
          <Panel title="硬规则" sub="不可违反"><div style={{ fontSize: 12, color: "var(--muted)", display: "flex", flexDirection: "column", gap: 6 }}>
            {st.data?.rules?.map((r: string) => <span key={r}>• {r}</span>) ?? ["加载中…"]}
          </div></Panel>
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <Panel title="策略列表" sub={st.data?.strategies?.length ? `${st.data.strategies.length} 个` : "0 个"} right={<StatusPill label="NO_DATA" />}>
            <StateView q={st} emptyText="暂无策略数据：真实 OOS 样本为 0，不展示空排名" />
            <SourceFooter meta={st.meta} />
          </Panel>
          <Panel title="工作线状态" sub="W4">
            <StateView q={st} />
            {st.data?.work_line ? (
              <div className="jev-code">
{`id: ${st.data.work_line.id}
status: ${st.data.work_line.status}
blocked: ${(st.data.work_line.blocked || []).length} 项`}
              </div>
            ) : null}
            <SourceFooter meta={st.meta} />
          </Panel>
        </div>
      </div>
    </>
  );
}
