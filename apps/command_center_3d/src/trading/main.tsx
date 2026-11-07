// JEV Trading Desk V1.0 entry. Mounts the trading app into #trading-root and
// loads the dedicated design stylesheet. The legacy command center keeps its own
// entry (index.html); this one is fully independent.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TradingApp } from "./App";
import "./styles.css";

const el = document.getElementById("trading-root");
if (el) {
  createRoot(el).render(
    <StrictMode>
      <TradingApp />
    </StrictMode>,
  );
}
