// Page 6 — AI 专家团队. Shows the six-role workflow with real status (all
// BLOCKED: zero Provider credentials, zero real inference). The naming
// discrepancy between order roles and code roles is surfaced, not hidden, and a
// real audit log is rendered. No fabricated tokens, latencies, or decisions.
import { useJevQuery } from "../api/useJev";
import { Panel, Banner, StatusPill, SourceFooter, StateView } from "../components/common";
import { TopBar } from "../components/Shell";
import { IconTeam } from "../components/icons";

export function AIExpertTeam({ topbar }: { topbar: React.ReactNode }) {
  const agents = useJevQuery<any>("agents/status");
  const prov = useJevQuery<any>("providers/status");

  const workflow = agents.data?.workflow || [];
  const nd = agents.data?.naming_discrepancy || prov.data?.naming_discrepancy;

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="crit">
          <b>AI 六角色当前全部 BLOCKED。</b> 真实 Provider 凭据为 <b>0</b>，真实推理记录为 <b>0</b>。
          下方每个角色的「状态 / Provider / 模型 / token / 决策」均为 <b>未知（NOT_CONFIGURED）</b>，
          界面<b>不会</b>把 0 渲染成"已运行"，也<b>不会</b>伪造任何推理结果。
        </Banner>

        <div className="jev-grid cols-3">
          {workflow.map((r: any) => (
            <Panel key={r.role} title={`${r.order}. ${r.role}`} sub={r.code_role ? `代码: ${r.code_role}` : ""} right={<StatusPill label="BLOCKED" />}>
              <div style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12 }}>
                <div className="jev-row" style={{ justifyContent: "space-between" }}><span style={{ color: "var(--muted)" }}>状态</span><span>{r.state}</span></div>
                <div className="jev-row" style={{ justifyContent: "space-between" }}><span style={{ color: "var(--muted)" }}>Provider</span><span>{r.provider ?? "—"}</span></div>
                <div className="jev-row" style={{ justifyContent: "space-between" }}><span style={{ color: "var(--muted)" }}>模型</span><span>{r.model ?? "—"}</span></div>
                <div className="jev-row" style={{ justifyContent: "space-between" }}><span style={{ color: "var(--muted)" }}>输入token</span><span>{r.input_tokens ?? "—"}</span></div>
                <div className="jev-row" style={{ justifyContent: "space-between" }}><span style={{ color: "var(--muted)" }}>决策</span><span>{r.decision ?? "—"}</span></div>
                <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 4 }}>{r.note}</div>
              </div>
            </Panel>
          ))}
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <Panel title="命名差异（需人工裁定）" sub={nd?.exact_match ? "匹配" : "不一致"} right={<StatusPill label={nd?.exact_match ? "VERIFIED" : "BLOCKED"} />}>
            <div className="jev-code">
{`下单角色 : ${(nd?.order_roles || []).join(" > ")}
代码角色 : ${(nd?.code_roles || []).join(" > ")}
完全匹配 : ${nd?.exact_match ? "是" : "否"}
映射     : ${nd?.mapping || "—"}`}
            </div>
            <SourceFooter meta={agents.meta} />
          </Panel>

          <Panel title="审计日志（真实）" sub={agents.data?.audit_log?.length + " 条"} right={<StatusPill label="BLOCKED" />}>
            <StateView q={agents} emptyText="无审计记录" />
            {agents.data?.audit_log?.map((e: any, i: number) => (
              <div className="jev-row" key={i} style={{ justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 12 }}>
                <span style={{ textAlign: "left" }}>{e.event}<div style={{ color: "var(--muted)", fontSize: 11 }}>{e.evidence}</div></span>
                <StatusPill label={e.verdict} />
              </div>
            ))}
            <SourceFooter meta={agents.meta} />
          </Panel>
        </div>

        <Panel title="Provider 配置状态" sub="BLOCKED" right={<StatusPill label="BLOCKED" />}>
          <StateView q={prov} emptyText="无 Provider 数据" />
          {prov.data ? (
            <>
              <div className="jev-code">{prov.data.report_excerpt || "—"}</div>
              <div style={{ marginTop: 10 }}>
                {prov.data.providers?.map((p: any) => (
                  <div className="jev-row" key={p.id} style={{ justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                    <span>{p.id}</span>
                    <span className="jev-down">未配置</span>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 11 }}>
                凭据写入既有 DPAPI SecretStore；浏览器不保存任何 key，前端不新建第二套凭据系统。
              </div>
            </>
          ) : null}
          <SourceFooter meta={prov.meta} />
        </Panel>
      </div>
    </>
  );
}
