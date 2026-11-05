/**
 * trading/components/CandlestickChart — K-line renderer.
 *
 * PRIMARY PATH: `lightweight-charts` (declared in package.json devDependencies).
 * The import is DYNAMIC and wrapped in try/catch, because the package is NOT
 * installed in this offline environment. When the import rejects (package
 * missing) OR the environment cannot create a canvas, we fall back to a pure
 * SVG candlestick renderer so the page still renders and type-checks.
 *
 * The SVG fallback is the honest offline rendering; when `lightweight-charts`
 * is actually installed, the dynamic import succeeds and the richer chart is
 * used instead.
 */
import { useEffect, useRef, useState } from "react";
import type { Candle } from "../services/types";

type Mode = "loading" | "chart" | "svg";

export function CandlestickChart({
  candles,
  height = 320,
}: {
  candles: Candle[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>("loading");

  useEffect(() => {
    if (candles.length === 0) {
      setMode("svg");
      return;
    }
    let disposed = false;
    let chart: { remove?: () => void } | null = null;

    async function mount() {
      try {
        // Guarded dynamic import: rejects when the package is absent (offline).
        const lc = (await import("lightweight-charts")) as any;
        if (disposed || !ref.current) return;
        const created = lc.createChart(ref.current, {
          height,
          width: ref.current.clientWidth || 640,
          layout: { background: { color: "transparent" }, textColor: "#9fb3c8" },
          grid: { vertLines: { color: "#1a1f27" }, horzLines: { color: "#1a1f27" } },
          rightPriceScale: { borderColor: "#2a313c" },
          timeScale: { borderColor: "#2a313c" },
        });
        chart = created;
        const series =
          created.addCandlestickSeries?.({
            upColor: "#4ade80",
            downColor: "#f87171",
            borderUpColor: "#4ade80",
            borderDownColor: "#f87171",
            wickUpColor: "#4ade80",
            wickDownColor: "#f87171",
          }) ?? created.addSeries?.(lc.CandlestickSeries, {});
        if (!series) throw new Error("no candlestick series");
        series.setData(
          candles.map((c) => ({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          })),
        );
        created.timeScale().fitContent();
        if (!disposed) setMode("chart");
      } catch {
        // Package missing or canvas unavailable: render the SVG fallback.
        if (!disposed) setMode("svg");
      }
    }

    void mount();
    return () => {
      disposed = true;
      try {
        chart?.remove?.();
      } catch {
        /* ignore */
      }
    };
  }, [candles, height]);

  if (mode === "chart") {
    return <div ref={ref} style={{ height }} data-testid="candle-chart" />;
  }

  // SVG fallback (also the loading placeholder, since it paints instantly).
  return <SvgCandles candles={candles} height={height} />;
}

function SvgCandles({ candles, height }: { candles: Candle[]; height: number }) {
  if (candles.length === 0) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground"
      >
        暂无 K 线数据
      </div>
    );
  }
  const W = 720;
  const H = height;
  const pad = 8;
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const max = Math.max(...highs);
  const min = Math.min(...lows);
  const range = max - min || 1;
  const stepX = (W - pad * 2) / candles.length;
  const bodyW = Math.max(2, stepX * 0.6);
  const y = (v: number) => pad + (1 - (v - min) / range) * (H - pad * 2);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", height }}
      className="rounded-lg border border-border bg-card"
      data-testid="candle-svg-fallback"
    >
      {candles.map((c, i) => {
        const cx = pad + i * stepX + stepX / 2;
        const up = c.close >= c.open;
        const color = up ? "#4ade80" : "#f87171";
        return (
          <g key={c.time}>
            <line x1={cx} x2={cx} y1={y(c.high)} y2={y(c.low)} stroke={color} strokeWidth={1} />
            <rect
              x={cx - bodyW / 2}
              y={y(Math.max(c.open, c.close))}
              width={bodyW}
              height={Math.max(1, Math.abs(y(c.open) - y(c.close)))}
              fill={color}
            />
          </g>
        );
      })}
    </svg>
  );
}
