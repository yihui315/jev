// Page 3 — 3D 市场宇宙. A Three.js scene where each asset is a cube whose height
// and colour encode its 24h change (real Binance Vision REST snapshot). This is a
// situational-awareness view ONLY. It is explicitly NOT a money-flow diagram and
// is labelled as REST_POLLING, never WebSocket LIVE.
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import * as THREE from "three";
import { useJevQuery } from "../api/useJev";
import { Panel, Banner, StatusPill, SourceFooter, StateView } from "../components/common";
import { TopBar } from "../components/Shell";
import { deriveStatusLabel } from "../../views/trading/components/PageContainer";

const ALL = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT", "BNBUSDT", "SOLUSDT"];
const COLLECTOR_ONLY = new Set(["BNBUSDT", "SOLUSDT"]);

function Controls() {
  const { camera, gl } = useThree();
  const ref = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enableDamping = true; c.dampingFactor = 0.08; c.autoRotate = true; c.autoRotateSpeed = 0.7;
    c.minDistance = 6; c.maxDistance = 40;
    ref.current = c;
    return () => c.dispose();
  }, [camera, gl]);
  useFrame(() => ref.current?.update());
  return null;
}

function Cubes({ data, onPick }: { data: Record<string, any>; onPick: (s: string) => void }) {
  const cols = 3;
  const positions = useMemo(() => {
    const out: [number, number, number][] = [];
    ALL.forEach((_, i) => {
      const x = (i % cols - (cols - 1) / 2) * 3.2;
      const z = (Math.floor(i / cols) - 0.5) * 3.2;
      out.push([x, 0, z]);
    });
    return out;
  }, []);

  return (
    <group>
      {ALL.map((sym, i) => {
        const t = data[sym];
        const chg = t ? Number(t.priceChangePercent) : 0;
        const h = 0.4 + Math.min(6, Math.abs(chg) / 2);
        const color = chg >= 0 ? "#22D3A6" : "#FF5A6A";
        const [x, , z] = positions[i];
        return (
          <mesh key={sym} position={[x, h / 2, z]} onClick={() => onPick(sym)}>
            <boxGeometry args={[2, h, 2]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.18} metalness={0.2} roughness={0.6} />
          </mesh>
        );
      })}
    </group>
  );
}

function MarketScene({ data, onPick }: { data: Record<string, any>; onPick: (s: string) => void }) {
  return (
    <Canvas camera={{ position: [0, 9, 16], fov: 45 }} style={{ width: "100%", height: "100%" }}>
      <color attach="background" args={["#060a11"]} />
      <ambientLight intensity={0.6} />
      <directionalLight position={[5, 12, 8]} intensity={0.9} />
      <gridHelper args={[30, 30, "#1B2A3B", "#0f1825"]} position={[0, 0, 0]} />
      <Cubes data={data} onPick={onPick} />
      <Controls />
    </Canvas>
  );
}

export function MarketUniverse3D({ topbar }: { topbar: React.ReactNode }) {
  const tq = useJevQuery<any[]>(`market/ticker?symbols=${encodeURIComponent(JSON.stringify(ALL))}`);
  const [pick, setPick] = useState<string | null>(null);
  const data = useMemo(() => {
    const m: Record<string, any> = {};
    (tq.data || []).forEach((t) => { m[t.symbol] = t; });
    return m;
  }, [tq.data]);

  const sel = pick ? data[pick] : null;

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="info">
          3D 视图为 <b>态势感知</b>，立方体高度/颜色编码各资产 24h 涨跌幅（真实 REST 快照）。
          <b>这不是资金流向图</b>：本终端 <b>禁止真实资金</b>，也不展示任何未经核验的资金迁移。
          通道 <b>REST_POLLING</b>，非 WebSocket。BNB/SOL 仅有采集层数据，标记为「仅采集、不可交易」。
        </Banner>

        <div className="jev-grid cols-4">
          {ALL.map((sym) => {
            const t = data[sym];
            const chg = t ? Number(t.priceChangePercent) : 0;
            const collector = COLLECTOR_ONLY.has(sym);
            return (
              <Panel key={sym} title={sym} sub={collector ? "仅采集" : "可交易"} right={<StatusPill label={collector ? "BACKFILL" : deriveStatusLabel(tq)} />}>
                <div className="jev-metric"><span className="label">最新价</span><span className="value sm">{t ? Number(t.lastPrice).toLocaleString("en-US", { maximumFractionDigits: 2 }) : "—"}</span></div>
                <div className="jev-metric"><span className="label">24h 涨跌</span><span className={`value sm ${chg >= 0 ? "jev-up" : "jev-down"}`}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span></div>
              </Panel>
            );
          })}
        </div>

        <div className="jev-3d-wrap" style={{ marginTop: 14 }}>
          <MarketScene data={data} onPick={setPick} />
          <div className="jev-3d-tip">拖拽旋转 · 滚轮缩放 · 点击资产查看详情</div>
          <div className="jev-3d-legend">
            绿=上涨 红=下跌 · 高度=涨跌幅绝对值<br />数据通道 REST_POLLING（非 WS LIVE）
          </div>
          {sel ? (
            <div className="jev-3d-detail">
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{pick}</div>
              <div>最新价：{Number(sel.lastPrice).toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
              <div className={Number(sel.priceChangePercent) >= 0 ? "jev-up" : "jev-down"}>24h：{Number(sel.priceChangePercent).toFixed(2)}%</div>
              <div>最高：{Number(sel.highPrice).toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
              <div>最低：{Number(sel.lowPrice).toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
              <div>成交量：{Number(sel.volume).toFixed(2)}</div>
              {pick && COLLECTOR_ONLY.has(pick) ? <div style={{ color: "var(--amber)", marginTop: 6 }}>仅采集层数据 · 冻结引擎白名单不支持交易</div> : null}
            </div>
          ) : <div className="jev-3d-detail">点击任意立方体查看该资产详情</div>}
        </div>
        <div style={{ marginTop: 10 }}><SourceFooter meta={tq.meta} /></div>
        <StateView q={tq} emptyText="暂无 3D 行情数据" />
      </div>
    </>
  );
}
