// Single import entry for the Trading Cockpit.
// NOTE: contracts.ts lives at src/trading/metrics/, so from this cockpit/
// folder the correct relative path is ../../metrics/contracts (not ../).
export * from "../../metrics/contracts";
export { MetricStrip } from "./MetricStrip";
export { ProviderComparison } from "./ProviderComparison";
export { CostAttribution } from "./CostAttribution";
