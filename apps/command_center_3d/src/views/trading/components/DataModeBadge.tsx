/**
 * trading/components/DataModeBadge — honesty guard.
 *
 * Surfaces the data mode everywhere in the trading subsystem so a mock session
 * can NEVER be mistaken for the real Paper API. When USE_MOCK is true the badge
 * is an explicit amber "模拟数据 · 非真实交易" warning. When bound to the real
 * API it is a quiet cyan label naming the live market-data source.
 */
import { MARKET_DATA_SOURCE, USE_MOCK } from "../services/paperApi";

export function DataModeBadge({ className = "" }: { className?: string }) {
  if (USE_MOCK) {
    return (
      <span
        className={
          "inline-flex items-center gap-1 rounded border border-status-warn/40 bg-status-warn/10 px-2 py-0.5 t-meta font-medium text-status-warn " +
          className
        }
        title="当前为离线模拟数据，账户/订单/成交/行情均为模拟，非真实交易"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-status-warn" />
        模拟数据 · 非真实交易
      </span>
    );
  }
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded border border-primary/40 bg-primary/10 px-2 py-0.5 t-meta font-medium text-primary " +
        className
      }
      title={"已接入真实接口；行情来源 " + MARKET_DATA_SOURCE}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-primary" />
      实时 · {MARKET_DATA_SOURCE.replace(/^https?:\/\//, "")}
    </span>
  );
}
