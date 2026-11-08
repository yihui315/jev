// Cloudflare Pages UI-only preview intentionally has NO Paper Trading backend.
// Do not fake fills, AI decisions, accounts, or market provenance.
export function onRequest() {
  return new Response(JSON.stringify({
    schema:'jev.api.v1', ok:false, code:'CLOUDFLARE_PREVIEW_UI_ONLY',
    error:'Cloudflare Pages preview only. Paper Trading server, realtime market adapter and AI provider are not deployed.',
    meta:{ label:'BLOCKED', source:'cloudflare-pages-ui-preview', channel:'NO_BACKEND' }
  }),{status:503,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store'}});
}
