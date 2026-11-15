/**
 * TradingDesk — P7 "no fabricated state" metrics surface.
 *
 * Every card is EXACTLY ONE of two shapes (control_ui v1_1.py:5-15):
 *   { available: true,  source: <named>, data: <number>, provenance }
 *   { available: false, source: <named>, reason: "NOT_MEASURED" }
 * There is NO third "available with a placeholder" shape: a missing value is
 * never 0, never "—", never a fabricated figure.
 *
 * Account-measured figures (equity / realizedPnl / unrealizedPnl / apiFeeUsd /
 * tradeCount) are kept in a SEPARATE section from Shadow decisions / simulated
 * fills; the two are never merged and no hardcoded reconciliation verdict
 * (e.g. MATCH) is emitted. This component generates NO data.
 *
 * B.4: delivered and exported, NOT wired into a route (see TRADING_DESK_MOUNT).
 */
import {
  ProvenanceBadge,
  resolveProvenanceState,
  providerNeedsAction,
  type Provenance,
  type ProviderVerdict,
  type ProviderStatus,
  type TestProviderStatus,
} from "./ProvenanceBadge";

/** P7 / B.4: component is declared + exported, not yet mounted on a route. */
export const TRADING_DESK_MOUNT = "DECLARED_NOT_WIRED" as const;

/** Discriminated union — exactly two shapes, no placeholder variant. */
export type DeskCard =
  | { available: true; source: string; data: number; provenance: Provenance }
  | { available: false; source: string; reason: string };

export interface TradingDeskMetrics {
  /** 账户净值（账户实测口径） */
  equity: DeskCard;
  /** 已实现利润（账户实测口径） */
  realizedPnl: DeskCard;
  /** 未实现利润（账户实测口径） */
  unrealizedPnl: DeskCard;
  /** 实际 API 费用 USD（账户实测口径） */
  apiFeeUsd: DeskCard;
  /** 真实成交笔数（账户实测口径） */
  tradeCount: DeskCard;
  /** Shadow 决策数：独立口径，不计入上方实测值 */
  shadowDecisionCount?: DeskCard;
  /** 模拟成交数：独立口径，不计入上方实测值 */
  simulatedFillCount?: DeskCard;
}

/** Three distinct provider fields are kept separate (never conflated). */
export interface ProviderStateFields {
  /** GET /providers list: BLOCKED | UNVERIFIED only */
  verdict: ProviderVerdict;
  /** credential fact, NOT connectivity */
  status: ProviderStatus;
  /** POST /providers/test, evidence-only */
  testStatus: TestProviderStatus;
  cardVerified: boolean;
}

const NOT_MEASURED: DeskCard = {
  available: false,
  source: "no named source / NOT_MEASURED",
  reason: "NOT_MEASURED",
};

/** All-unavailable defaults: every card is honestly NOT_MEASURED. */
export function emptyTradingDeskMetrics(): TradingDeskMetrics {
  return {
    equity: NOT_MEASURED,
    realizedPnl: NOT_MEASURED,
    unrealizedPnl: NOT_MEASURED,
    apiFeeUsd: NOT_MEASURED,
    tradeCount: NOT_MEASURED,
    shadowDecisionCount: NOT_MEASURED,
    simulatedFillCount: NOT_MEASURED,
  };
}

function MetricValue({ card }: { card: DeskCard }) {
  // available:false -> render the reason, never 0 or a placeholder.
  if (!card.available) {
    return (
      <span className="font-mono text-muted-foreground" data-measured="false">
        {card.reason}
      </span>
    );
  }
  // available:true but provenance not measured -> still NOT_MEASURED.
  const state = resolveProvenanceState(card.provenance);
  if (state === "NOT_MEASURED") {
    return (
      <span className="font-mono text-muted-foreground" data-measured="false">
        NOT_MEASURED
      </span>
    );
  }
  return (
    <span className="font-mono tabular-nums text-foreground" data-measured="true">
      {String(card.data)}
    </span>
  );
}

function MetricRow({ label, note, card }: { label: string; note: string; card: DeskCard }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2">
      <div className="min-w-0">
        <div className="t-body text-foreground">{label}</div>
        <div className="t-meta text-muted-foreground">{note}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <MetricValue card={card} />
        <ProvenanceBadge provenance={card.available ? card.provenance : null} />
      </div>
    </div>
  );
}

export function TradingDesk({
  metrics,
  provider,
}: {
  metrics?: TradingDeskMetrics;
  provider?: ProviderStateFields;
}) {
  const m = metrics ?? emptyTradingDeskMetrics();
  const shadow = m.shadowDecisionCount ?? NOT_MEASURED;
  const simulated = m.simulatedFillCount ?? NOT_MEASURED;
  return (
    <section className="rounded-lg border border-border bg-card p-6" aria-label="Trading desk metrics">
      <h2 className="t-section text-foreground">交易台 · 账户实测口径</h2>
      <p className="mt-1.5 t-meta text-muted-foreground">
        每张卡只能是「已实测（含具名来源与 provenance）」或「未测得（NOT_MEASURED）」两种形态之一；绝不显示 0 或占位假值。
      </p>

      {/* real-looking provider + unverified card => alarm, never a green state */}
      {provider && providerNeedsAction(provider) ? (
        <div role="alert" className="mt-3 rounded border border-status-warn/40 bg-status-warn/10 px-3 py-2 t-meta text-status-warn">
          告警：Provider 显示为真实态（{provider.status} / {provider.testStatus}），但卡片未验证。verdict={provider.verdict}；CONFIGURED 只是凭据事实，不代表已连通。
        </div>
      ) : null}

      <div className="mt-4">
        <MetricRow label="账户净值 equity" note="账户实测口径" card={m.equity} />
        <MetricRow label="已实现利润 realizedPnl" note="账户实测口径 · 已平仓实现" card={m.realizedPnl} />
        <MetricRow label="未实现利润 unrealizedPnl" note="账户实测口径 · 持仓浮动，未实现" card={m.unrealizedPnl} />
        <MetricRow label="实际 API 费用 apiFeeUsd (USD)" note="账户实测口径 · 真实计费，非模拟" card={m.apiFeeUsd} />
        <MetricRow label="真实成交笔数 tradeCount" note="账户实测口径 · 真实成交，不含 shadow / 模拟" card={m.tradeCount} />
      </div>

      {/* 显式区分：Shadow 决策 / 模拟成交是独立口径，绝不并入上方实测值。 */}
      <div className="mt-6 rounded-lg border border-dashed border-border p-4">
        <h3 className="t-body font-medium text-foreground">Shadow 决策 / 模拟成交（独立口径）</h3>
        <p className="mt-1 t-meta text-muted-foreground">
          与上方「已实现利润 / 未实现利润 / 实际 API 费用 / 真实成交」严格分开：Shadow 决策与模拟成交不产生真实费用、不产生真实成交、不计入账户实测口径。
        </p>
        <div className="mt-3">
          <MetricRow label="Shadow 决策数" note="仅研究/影子决策，非真实成交" card={shadow} />
          <MetricRow label="模拟成交数" note="离线模拟，非真实成交" card={simulated} />
        </div>
      </div>

      <p className="mt-4 t-meta text-muted-foreground">
        本视图不输出任何写死的对账结论；bar 数是样本基础、不是策略绩效，绝不显示为收益/胜率/回撤。对账状态未由服务端 provenance 提供时一律 NOT_MEASURED。
      </p>
    </section>
  );
}
