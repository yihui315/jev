import assert from 'node:assert/strict';
import { onRequest } from '../../functions/_middleware.js';
import { onRequest as apiRequest } from '../../functions/api/jev/[[path]].js';

// JEV Cloudflare staging — REAL Paper Trading backend on D1.
//  - Preview auth is enforced at the middleware layer (fail-closed).
//  - The API gateway must be the REAL backend, and must refuse to run unless
//    ENABLE_REAL_TRADING === 'false' (red line). It is NOT the UI-only stub.
const env = { JEV_PREVIEW_USER: 'ci', JEV_PREVIEW_PASSWORD: 'a-fake-preview-password-with-32-characters' };
const next = async () => new Response('ok', { status: 200 });
function context(header, variables = env) {
  const headers = new Headers();
  if (header) headers.set('authorization', header);
  return { request: new Request('https://preview.pages.dev/trading.html', { headers }), env: variables, next };
}

// --- middleware: preview auth fail-closed ---
let r = await onRequest(context(null, {})); assert.equal(r.status, 503); // creds not configured
r = await onRequest(context(null)); assert.equal(r.status, 401);          // anonymous denied
r = await onRequest(context('Basic ' + btoa('ci:wrongpassword'))); assert.equal(r.status, 401); // wrong pw
r = await onRequest(context('Basic ' + btoa('ci:' + env.JEV_PREVIEW_PASSWORD))); assert.equal(r.status, 200); // ok
assert.equal(r.headers.get('cache-control'), 'private, no-store');

// --- api gateway: real backend, fail-closed unless real trading disabled ---
const apiCtx = {
  request: new Request('https://preview.pages.dev/api/jev/health', { headers: new Headers() }),
  env: { ENABLE_REAL_TRADING: 'true' },
  next,
};
r = await apiRequest(apiCtx);
assert.equal(r.status, 500);
const body = await r.json();
assert.equal(body.ok, false);
assert.equal(body.error, 'ENABLE_REAL_TRADING_MUST_BE_FALSE');
assert.equal(body.code, 'RED_LINE');

console.log('Cloudflare preview auth + real-backend gateway fail-closed: 6/6 PASS');
