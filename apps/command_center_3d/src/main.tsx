import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// The single design-system stylesheet. Imported once, here, so Tailwind's
// preflight and the design tokens load before any component renders.
import "./index.css";

const el = document.getElementById("root");
if (!el) throw new Error("#root not found");

createRoot(el).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
