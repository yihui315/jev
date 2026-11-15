/**
 * QA-U1..U5 - 1000SATS unit_multiplier=1000 / base_asset="1000SATS",
 * inverse round-trip, 1000x sentinel, never parallel with SATSUSDT,
 * wrong-multiplier rejection, and BLOCKED business codes.
 */
import {
  SYMBOL_CONTRACTS, EXCHANGE_GLOBAL, STATUS_BLOCKED,
  toPerBaseUnit, fromPerBaseUnit, buildMarkContracts, contractFor,
} from "../../src/market-contract.mjs";
import { validateUnitContracts, detectSatsSeriesConflict, ERROR_CODES } from "../../src/market-guard.mjs";
import { v16Recorder } from "./lib.mjs";

const R = v16Recorder("v16_u1_u5_units", { coverage: "QA-U1..U5" });
const c = SYMBOL_CONTRACTS["1000SATSUSDT"];

// U1 - contract identity.
R.check("U1_1000sats_unit_multiplier_equals_1000", c.unit_multiplier === 1000, { unit_multiplier: c.unit_multiplier });
R.check("U1_1000sats_base_asset_is_1000SATS", c.base_asset === "1000SATS", { base_asset: c.base_asset });
R.check("U1_1000sats_quote_asset_is_USDT", c.quote_asset === "USDT", { quote_asset: c.quote_asset });
R.check("U1_1000sats_price_unit_explicit", c.price_unit === "USDT per 1 (1000SATS) unit = 1000 SATS", { price_unit: c.price_unit });
R.check("U1_1000sats_global_identification_only", c.exchange === EXCHANGE_GLOBAL, { exchange: c.exchange });

// U5 - BLOCKED business codes.
R.check("U5_1000sats_status_blocked", c.status === STATUS_BLOCKED && c.reason === "GLOBAL_ONLY_IDENTIFIER_NEVER_QUOTED", { status: c.status, reason: c.reason });
R.check("U5_ordi_blocked_not_listed_on_us",
  SYMBOL_CONTRACTS.ORDIUSDT.status === STATUS_BLOCKED && SYMBOL_CONTRACTS.ORDIUSDT.reason === "NOT_LISTED_ON_BINANCE_US",
  { status: SYMBOL_CONTRACTS.ORDIUSDT.status, reason: SYMBOL_CONTRACTS.ORDIUSDT.reason });

// U2 - inverse unit transform round-trips (1000x sentinel).
const quote = 0.00000800;                    // USDT per 1 (1000SATS) unit
const perSats = toPerBaseUnit(quote, 1000);  // USDT per 1 SATS
const back = fromPerBaseUnit(perSats, 1000);
R.check("U2_inverse_round_trip_restores_quote",
  Math.abs(back - quote) < 1e-15 && Math.abs(perSats - quote / 1000) < 1e-20,
  { quote, perSats, back });
R.check("U2_1000x_sentinel",
  Math.abs(fromPerBaseUnit(perSats, 1000) - perSats * 1000) < 1e-15 && Math.abs(perSats * 1000 - quote) < 1e-15,
  { perSats, times1000: perSats * 1000, quote });

// U3 - 1000SATS and SATSUSDT must never share one series.
const series = [contractFor("1000SATSUSDT"), contractFor("SATSUSDT")];
R.check("U3_sats_series_conflict_detected", detectSatsSeriesConflict(series).conflict === true,
  detectSatsSeriesConflict(series));
const u3 = validateUnitContracts(series);
R.check("U3_parallel_sats_rejected",
  u3.pass === false && u3.code === ERROR_CODES.UNIT_CONTRACT_MISMATCH, { code: u3.code });

// U4 - a tampered / wrong unit_multiplier for 1000SATS is rejected.
const tampered = { ...contractFor("1000SATSUSDT"), unit_multiplier: 1 };
const u4 = validateUnitContracts([tampered]);
R.check("U4_wrong_unit_multiplier_rejected",
  u4.pass === false && u4.code === ERROR_CODES.UNIT_CONTRACT_MISMATCH, { code: u4.code });

// U5 - any BLOCKED symbol inside a VERIFIED series is rejected.
const u5 = validateUnitContracts([contractFor("BTCUSDT"), contractFor("1000SATSUSDT")]);
R.check("U5_blocked_symbol_in_series_rejected",
  u5.pass === false && u5.code === ERROR_CODES.UNIT_CONTRACT_MISMATCH, { code: u5.code });

// M2 - buildMarkContracts carries unit_multiplier=1000 into the stored record.
const contracts = buildMarkContracts({
  market: {
    priceTsSource: "exchange_serverTime", sourceHost: "api.binance.us",
    sourceUrl: "https://api.binance.us/x", priceTs: "t", fetchedAt: "t",
    freshnessMs: 0, freshnessVerdict: "FRESH", payloadSha256: "p", dataQuality: "UNKNOWN",
  },
  marks: { "1000SATSUSDT": 1n },
  networkContext: "local_test_shim",
});
R.check("U1_buildMarkContracts_carries_unit_1000",
  contracts && typeof contracts === "object" && !Array.isArray(contracts) &&
  Object.keys(contracts).length === 1 &&
  contracts["1000SATSUSDT"] &&
  contracts["1000SATSUSDT"].unit_multiplier === 1000 &&
  contracts["1000SATSUSDT"].base_asset === "1000SATS",
  contracts);

R.finish({ quote, perSats, back });
