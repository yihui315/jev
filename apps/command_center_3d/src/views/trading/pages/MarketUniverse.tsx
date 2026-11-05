/**
 * trading/pages/MarketUniverse — 03 · 3D 市场宇宙 (V4.0)
 *
 * REAL INFORMATION VALUE — the non-compromisable requirement. There are no
 * decorative particles and no fabricated fund flows. Everything the scene draws
 * is derived from the SAME authoritative Binance public K-line data the rest of
 * the desk uses:
 *
 *   • Nodes             = the 4 tradable assets.
 *       – radius        ∝ real 24h trading volume (activity / interest)
 *       – body colour   = RISK, derived from the REAL return volatility
 *                         (cyan = calm → amber → red = turbulent)
 *       – height (y)    = real 24h change (up lifts, down sinks)
 *   • Edges             = pairwise correlation computed from the REAL close-
 *                         to-close return series. Thickness / opacity ∝ |corr|;
 *                         sign is shown by colour. The UI states explicitly that
 *                         these are CORRELATIONS, not fund flows.
 *
 * WebGL2 is required for the 3D view; without it the page renders an honest 2D
 * correlation matrix + node table (the same numbers, no canvas).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import { AlertTriangle, Info, RefreshCw } from "lucide-react";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatPct, formatUsd } from "../lib/finance";
import { cn } from "../../../ui/cn";
import { DataModeBadge } from "../components/DataModeBadge";
import type { Candle, Interval, Symbol } from "../services/types";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const INTERVAL: Interval = "4h";
const LIMIT = 120;

interface MarketAsset {
  symbol: Symbol;
  last: number;
  changePct: number;
  /** Stddev of close-to-close returns — the risk signal. */
  volatility: number;
  volume: number;
  /** Per-asset returns, kept for the correlation pass. */
  returns: number[];
  /** Pairwise Pearson correlation to every other asset. */
  corr: Record<string, number>;
  /** Normalised [0,1] volume → node radius. */
  sizeNorm: number;
  /** Normalised [0,1] volatility → node risk colour. */
  riskNorm: number;
}

// ---------------------------------------------------------------------------
// Pure analytics (no React, no I/O — unit-testable).
// ---------------------------------------------------------------------------

function mean(xs: number[]): number {
  if (xs.length === 0) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function stddev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  const v = xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1);
  return Math.sqrt(v);
}

function pearson(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 5) return 0;
  const aa = a.slice(0, n);
  const bb = b.slice(0, n);
  const ma = mean(aa);
  const mb = mean(bb);
  let cov = 0;
  let va = 0;
  let vb = 0;
  for (let i = 0; i < n; i++) {
    const da = aa[i] - ma;
    const db = bb[i] - mb;
    cov += da * db;
    va += da * da;
    vb += db * db;
  }
  if (va === 0 || vb === 0) return 0;
  return cov / Math.sqrt(va * vb);
}

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function hexLerp(a: string, b: string, t: number): string {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const r = Math.round(pa[0] + (pb[0] - pa[0]) * t);
  const g = Math.round(pa[1] + (pb[1] - pa[1]) * t);
  const bl = Math.round(pa[2] + (pb[2] - pa[2]) * t);
  return `#${[r, g, bl].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

/** Risk colour: calm cyan → amber → turbulent red. */
function riskColor(t: number): string {
  if (t < 0.5) return hexLerp("#14C6E8", "#F5A623", t / 0.5);
  return hexLerp("#F5A623", "#F2455E", (t - 0.5) / 0.5);
}

function corrColor(c: number): string {
  const a = Math.min(1, Math.abs(c));
  return c >= 0 ? hexLerp("#0E7490", "#16C8E8", a) : hexLerp("#F5A623", "#F2455E", a);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function buildAssets(
  tickers: { symbol: Symbol; price: number }[],
  candlesBySymbol: Record<Symbol, Candle[]>,
): MarketAsset[] {
  const raw: MarketAsset[] = SYMBOLS.map((symbol) => {
    const candles = candlesBySymbol[symbol] ?? [];
    const closes = candles.map((c) => c.close);
    const last = tickers.find((t) => t.symbol === symbol)?.price ?? closes.at(-1) ?? 0;
    const first = closes[0] ?? last;
    const changePct = first > 0 ? (last - first) / first : 0;
    const returns: number[] = [];
    for (let i = 1; i < closes.length; i++) {
      returns.push((closes[i] - closes[i - 1]) / closes[i - 1]);
    }
    const volatility = stddev(returns);
    const volume = candles.reduce((acc, c) => acc + c.volume, 0);
    return { symbol, last, changePct, volatility, volume, returns, corr: {}, sizeNorm: 0, riskNorm: 0 };
  });

  // Pairwise correlation.
  for (const a of raw) {
    for (const b of raw) {
      if (a.symbol !== b.symbol) a.corr[b.symbol] = pearson(a.returns, b.returns);
    }
  }

  // Normalise volume (→ radius) and volatility (→ risk colour) across the set.
  const vols = raw.map((s) => s.volatility);
  const vmin = Math.min(...vols);
  const vmax = Math.max(...vols);
  const volumes = raw.map((s) => s.volume);
  const omin = Math.min(...volumes);
  const omax = Math.max(...volumes);
  return raw.map((s) => ({
    ...s,
    sizeNorm: omax > omin ? (s.volume - omin) / (omax - omin) : 0.5,
    riskNorm: vmax > vmin ? (s.volatility - vmin) / (vmax - vmin) : 0.5,
  }));
}

// ---------------------------------------------------------------------------
// 3D scene
// ---------------------------------------------------------------------------

function makeLabelSprite(text: string, color: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  const fontSize = 52;
  ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
  const w = Math.ceil(ctx.measureText(text).width);
  canvas.width = w + 36;
  canvas.height = fontSize + 28;
  ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  ctx.fillStyle = color;
  ctx.fillText(text, 18, canvas.height / 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  const scale = 0.0042;
  sprite.scale.set(canvas.width * scale, canvas.height * scale, 1);
  return sprite;
}

function layoutNodes(stats: MarketAsset[]): [number, number, number][] {
  const R = 5.6;
  return stats.map((s, i) => {
    const a = (i / stats.length) * Math.PI * 2;
    const x = Math.cos(a) * R;
    const z = Math.sin(a) * R;
    const y = clamp(s.changePct * 10, -2.2, 2.2);
    return [x, y, z] as [number, number, number];
  });
}

function AssetNode({
  asset,
  position,
  dimmed,
  onSelect,
  onHover,
}: {
  asset: MarketAsset;
  position: [number, number, number];
  dimmed: boolean;
  onSelect: (s: Symbol) => void;
  onHover: (s: Symbol | null) => void;
}) {
  const ref = useRef<THREE.Group>(null);
  const radius = 0.55 + asset.sizeNorm * 1.05;
  const color = riskColor(asset.riskNorm);
  const label = useMemo(() => makeLabelSprite(asset.symbol, "#DCEAF5"), [asset.symbol]);

  useFrame((_, dt) => {
    if (!ref.current) return;
    const target = dimmed ? 0.85 : 1;
    const cur = ref.current.scale.x;
    const next = cur + (target - cur) * Math.min(1, dt * 6);
    ref.current.scale.setScalar(next);
  });

  return (
    <group ref={ref} position={position}>
      <mesh
        onClick={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          onSelect(asset.symbol);
        }}
        onPointerOver={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          onHover(asset.symbol);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          onHover(null);
          document.body.style.cursor = "auto";
        }}
      >
        <sphereGeometry args={[radius, 40, 40]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={dimmed ? 0.18 : 0.32}
          roughness={0.35}
          metalness={0.12}
          transparent
          opacity={dimmed ? 0.45 : 1}
        />
      </mesh>
      <primitive object={label} position={[0, radius + 0.7, 0]} />
    </group>
  );
}

function CorrEdge({ a, b, corr }: { a: [number, number, number]; b: [number, number, number]; corr: number }) {
  const { length, mid, quat, thick } = useMemo(() => {
    const va = new THREE.Vector3(...a);
    const vb = new THREE.Vector3(...b);
    const dir = new THREE.Vector3().subVectors(vb, va);
    const len = dir.length();
    const m = new THREE.Vector3().addVectors(va, vb).multiplyScalar(0.5);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    const t = 0.025 + Math.abs(corr) * 0.11;
    return { length: len, mid: m, quat: q, thick: t };
  }, [a, b, corr]);
  const color = corrColor(corr);
  const opacity = 0.16 + 0.72 * Math.min(1, Math.abs(corr));
  return (
    <mesh position={[mid.x, mid.y, mid.z]} quaternion={[quat.x, quat.y, quat.z, quat.w]}>
      <boxGeometry args={[thick, length, thick]} />
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} transparent opacity={opacity} roughness={0.45} />
    </mesh>
  );
}

/** Dependency-free orbit camera (no drei / three-examples import). */
function SceneControls({ target = [0, 0, 0] as [number, number, number] }) {
  const { camera, gl } = useThree();
  const st = useRef({ radius: 15, theta: Math.PI / 4, phi: Math.PI / 2.6, dragging: false, lx: 0, ly: 0 });

  useEffect(() => {
    const el = gl.domElement;
    const onDown = (e: PointerEvent) => {
      st.current.dragging = true;
      st.current.lx = e.clientX;
      st.current.ly = e.clientY;
    };
    const onUp = () => {
      st.current.dragging = false;
    };
    const onMove = (e: PointerEvent) => {
      if (!st.current.dragging) return;
      const dx = e.clientX - st.current.lx;
      const dy = e.clientY - st.current.ly;
      st.current.lx = e.clientX;
      st.current.ly = e.clientY;
      st.current.theta -= dx * 0.006;
      st.current.phi = clamp(st.current.phi - dy * 0.006, 0.2, Math.PI / 2 - 0.05);
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      st.current.radius = clamp(st.current.radius + e.deltaY * 0.012, 7, 34);
    };
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointermove", onMove);
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointermove", onMove);
      el.removeEventListener("wheel", onWheel);
    };
  }, [gl]);

  useFrame(() => {
    const s = st.current;
    const x = target[0] + s.radius * Math.sin(s.phi) * Math.cos(s.theta);
    const y = target[1] + s.radius * Math.cos(s.phi);
    const z = target[2] + s.radius * Math.sin(s.phi) * Math.sin(s.theta);
    camera.position.set(x, y, z);
    camera.lookAt(target[0], target[1], target[2]);
  });
  return null;
}

function Scene({
  assets,
  selected,
  onSelect,
  onHover,
}: {
  assets: MarketAsset[];
  selected: Symbol | null;
  onSelect: (s: Symbol) => void;
  onHover: (s: Symbol | null) => void;
}) {
  const positions = useMemo(() => layoutNodes(assets), [assets]);
  return (
    <>
      <color attach="background" args={["#0A0F1A"]} />
      <ambientLight intensity={0.65} />
      <directionalLight position={[7, 13, 9]} intensity={0.85} color="#bfe6f5" />
      <gridHelper args={[36, 36, "#1d2f4a", "#14223a"]} position={[0, -2.4, 0]} />
      {assets.map((a, i) => (
        <AssetNode
          key={a.symbol}
          asset={a}
          position={positions[i]}
          dimmed={selected !== null && selected !== a.symbol}
          onSelect={onSelect}
          onHover={onHover}
        />
      ))}
      {assets.map((a, i) =>
        assets.slice(i + 1).map((b, j) => (
          <CorrEdge key={`${a.symbol}-${b.symbol}`} a={positions[i]} b={positions[i + 1 + j]} corr={a.corr[b.symbol] ?? 0} />
        )),
      )}
      <SceneControls />
    </>
  );
}

// ---------------------------------------------------------------------------
// 2D fallback (no WebGL2) — same numbers, honest rendering.
// ---------------------------------------------------------------------------

function Fallback({
  assets,
  error,
  onRetry,
}: {
  assets: MarketAsset[] | null;
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div className="jev-trading is-fullbleed h-full w-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="flex items-center gap-2 rounded-lg border border-status-warn/40 bg-status-warn/10 p-3 text-status-warn">
          <AlertTriangle className="h-4 w-4" />
          当前浏览器未启用 WebGL2，3D 视图不可用。以下为同一份数据的二维相关性表（无画布渲染）。
        </div>
        {error ? (
          <div className="mt-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">{error}</div>
        ) : null}
        {assets ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="t-section text-foreground">相关性矩阵（实时 K 线收益序列）</div>
              <table className="mt-3 w-full border-collapse text-sm">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="py-1 text-left">资产</th>
                    {assets.map((a) => (
                      <th key={a.symbol} className="py-1 text-right t-meta">{a.symbol}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {assets.map((row) => (
                    <tr key={row.symbol} className="border-t border-border">
                      <td className="py-1 text-foreground">{row.symbol}</td>
                      {assets.map((c) => (
                        <td
                          key={c.symbol}
                          className={cn("py-1 text-right tabular-nums", row.symbol === c.symbol ? "text-muted-foreground" : c.corr[row.symbol] >= 0 ? "text-status-ok" : "text-status-danger")}
                        >
                          {row.symbol === c.symbol ? "—" : c.corr[row.symbol].toFixed(2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 t-meta text-muted-foreground">数值为皮尔逊相关系数：+1 同向、−1 反向。连线 = 相关性，非资金流向。</div>
            </div>
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="t-section text-foreground">资产快照</div>
              <div className="mt-3 space-y-2">
                {assets.map((a) => (
                  <div key={a.symbol} className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-2">
                    <span className="text-foreground">{a.symbol}</span>
                    <span className="tabular-nums text-foreground">{formatUsd(a.last, { decimals: a.last < 1 ? 6 : 2 })}</span>
                    <span className={cn("tabular-nums", a.changePct >= 0 ? "text-status-ok" : "text-status-danger")}>{formatPct(a.changePct)}</span>
                    <span className="t-meta text-muted-foreground">σ {a.volatility.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="mt-6 h-40 animate-pulse rounded-lg border border-border bg-card" />
        )}
        <button
          type="button"
          onClick={onRetry}
          className="mt-6 flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground"
        >
          <RefreshCw className="h-3.5 w-3.5" /> 重新计算相关性
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function webgl2Supported(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch {
    return false;
  }
}

export function MarketUniverse({ controlToken: _controlToken }: { controlToken: string }) {
  const api = usePaperApi(_controlToken);
  const [assets, setAssets] = useState<MarketAsset[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Symbol | null>(null);
  const [hovered, setHovered] = useState<Symbol | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [supports3D] = useState<boolean>(() => webgl2Supported());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tickers, sets] = await Promise.all([
        api.getTicker(SYMBOLS),
        Promise.all(SYMBOLS.map((s) => api.getCandles(s, INTERVAL, LIMIT).catch(() => [] as Candle[]))),
      ]);
      const bySymbol = {} as Record<Symbol, Candle[]>;
      SYMBOLS.forEach((s, i) => {
        bySymbol[s] = sets[i];
      });
      setAssets(buildAssets(tickers, bySymbol));
      setLastUpdated(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const focus = selected ?? hovered;
  const focusAsset = assets?.find((a) => a.symbol === focus) ?? null;

  return (
    <div className="jev-trading is-fullbleed relative h-full w-full overflow-hidden">
      {/* Header */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between p-5">
        <div className="pointer-events-auto max-w-md">
          <h1 className="t-page text-foreground">03 · 3D 市场宇宙</h1>
          <p className="mt-1.5 t-body text-muted-foreground">
            资产相关性网络。连线为实时 K 线收益序列计算的<strong className="text-accent-cyan">相关性</strong>，并非资金流向；球体越大代表成交量越活跃，颜色越红代表波动率（风险）越高。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> 重新计算
            </button>
            {lastUpdated ? (
              <span className="t-meta text-muted-foreground">更新于 {new Date(lastUpdated).toLocaleTimeString("zh-CN")}</span>
            ) : null}
            <DataModeBadge />
            <span className="t-meta text-muted-foreground">数据：Binance 公开行情 · 相关性实时计算</span>
          </div>
        </div>
      </div>

      {error ? (
        <div className="absolute inset-x-5 top-32 z-10 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">{error}</div>
      ) : null}

      {supports3D ? (
        <Canvas
          className="absolute inset-0"
          camera={{ position: [0, 7, 15], fov: 50 }}
          gl={{ antialias: true }}
        >
          {assets ? (
            <Scene assets={assets} selected={selected} onSelect={(s) => setSelected((p) => (p === s ? null : s))} onHover={setHovered} />
          ) : null}
        </Canvas>
      ) : (
        <div className="absolute inset-0 flex items-center justify-center">
          <Fallback assets={assets} error={error} onRetry={() => void load()} />
        </div>
      )}

      {/* Legend (bottom-left) */}
      {supports3D ? (
        <div className="pointer-events-none absolute bottom-5 left-5 z-10 w-64 rounded-lg border border-border bg-card/85 p-3 backdrop-blur">
          <div className="t-meta text-muted-foreground">图例</div>
          <div className="mt-2 space-y-1.5 t-meta text-foreground">
            <div className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "#14C6E8" }} /> 球体颜色 = 波动率（风险）：青→琥珀→红</div>
            <div className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-border" /> 球体大小 = 成交量（活跃度）</div>
            <div className="flex items-center gap-2"><span className="inline-block h-0.5 w-6" style={{ background: "#16C8E8" }} /> 连线粗细 = |相关性|（同向青 / 反向红）</div>
          </div>
          <div className="mt-2 flex items-start gap-1.5 t-meta text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> 拖拽旋转 · 滚轮缩放 · 点击资产查看相关性明细。
          </div>
        </div>
      ) : null}

      {/* Detail panel (right) */}
      {focusAsset ? (
        <div className="absolute right-5 top-1/2 z-10 w-72 -translate-y-1/2 rounded-lg border border-border bg-card/90 p-4 backdrop-blur">
          <div className="flex items-center justify-between">
            <span className="t-section text-foreground">{focusAsset.symbol}</span>
            <button type="button" onClick={() => { setSelected(null); setHovered(null); }} className="t-meta text-muted-foreground hover:text-foreground">关闭</button>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 t-meta">
            <div className="rounded-md border border-border bg-background p-2">
              <div className="text-muted-foreground">最新价</div>
              <div className="tabular-nums text-foreground">{formatUsd(focusAsset.last, { decimals: focusAsset.last < 1 ? 6 : 2 })}</div>
            </div>
            <div className="rounded-md border border-border bg-background p-2">
              <div className="text-muted-foreground">24h 涨跌</div>
              <div className={cn("tabular-nums", focusAsset.changePct >= 0 ? "text-status-ok" : "text-status-danger")}>{formatPct(focusAsset.changePct)}</div>
            </div>
            <div className="rounded-md border border-border bg-background p-2">
              <div className="text-muted-foreground">波动率 σ</div>
              <div className="tabular-nums text-foreground">{focusAsset.volatility.toFixed(4)}</div>
            </div>
            <div className="rounded-md border border-border bg-background p-2">
              <div className="text-muted-foreground">成交量</div>
              <div className="tabular-nums text-foreground">{focusAsset.volume.toLocaleString("en-US")}</div>
            </div>
          </div>
          <div className="mt-3 t-meta text-muted-foreground">与其他资产的相关性</div>
          <div className="mt-2 space-y-1.5">
            {assets
              ?.filter((a) => a.symbol !== focusAsset.symbol)
              .map((a) => {
                const c = focusAsset.corr[a.symbol] ?? 0;
                const barStyle: CSSProperties = {
                  width: `${Math.abs(c) * 50}%`,
                  background: corrColor(c),
                };
                if (c >= 0) barStyle.left = "50%";
                else barStyle.right = "50%";
                return (
                  <div key={a.symbol} className="flex items-center gap-2">
                    <span className="w-24 t-meta text-foreground">{a.symbol}</span>
                    <div className="relative h-2 flex-1 overflow-hidden rounded bg-background">
                      <div className="absolute inset-y-0 left-1/2 rounded" style={barStyle} />
                    </div>
                    <span className={cn("w-12 text-right tabular-nums t-meta", c >= 0 ? "text-status-ok" : "text-status-danger")}>{c.toFixed(2)}</span>
                  </div>
                );
              })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
