// V1.4 / T03 — market status classifier unit test.
// IMPORTANT: inputs here are SYNTHETIC. This test verifies the classification
// logic only; it does NOT generate, cache or claim any real market data.
// Real-行情 evidence lives in the HTTPS endpoints (/api/jev/market/status,
// /api/jev/market/diagnose) and in reports/V14_MARKET_PROVENANCE.md.
import assert from 'node:assert/strict';
import { classifyFreshness } from '../../functions/api/jev/[[path]].js';

const cases = [
  { name: 'fresh 200 -> VERIFIED',        input: { ok: true,  httpStatus: 200, skewMs: 41 },        want: 'VERIFIED' },
  { name: 'exactly at threshold -> VERIFIED', input: { ok: true, httpStatus: 200, skewMs: 60000 },  want: 'VERIFIED' },
  { name: 'past threshold -> STALE',      input: { ok: true,  httpStatus: 200, skewMs: 60001 },     want: 'STALE' },
  { name: 'very old data -> STALE',       input: { ok: true,  httpStatus: 200, skewMs: 900000 },    want: 'STALE' },
  { name: 'upstream 403 -> UNAVAILABLE',  input: { ok: false, httpStatus: 403, skewMs: 0 },         want: 'UNAVAILABLE' },
  { name: 'upstream 451 -> UNAVAILABLE',  input: { ok: false, httpStatus: 451, skewMs: 0 },         want: 'UNAVAILABLE' },
  { name: 'upstream 530 -> UNAVAILABLE',  input: { ok: false, httpStatus: 530, skewMs: 0 },         want: 'UNAVAILABLE' },
  { name: 'ok=false wins over fresh skew',input: { ok: false, httpStatus: 200, skewMs: 1 },         want: 'UNAVAILABLE' },
  { name: 'null skew stays VERIFIED',     input: { ok: true,  httpStatus: 200, skewMs: null },      want: 'VERIFIED' },
  { name: 'custom threshold honored',     input: { ok: true,  httpStatus: 200, skewMs: 200, staleThresholdMs: 100 }, want: 'STALE' },
];

for (const c of cases) {
  assert.equal(classifyFreshness(c.input), c.want, c.name);
}
console.log(`Market freshness classifier (SYNTHETIC inputs, no market data): ${cases.length}/${cases.length} PASS`);
