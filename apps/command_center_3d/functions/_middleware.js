// Cloudflare Pages STAGING ONLY. Authentication is required before serving any asset.
// Never publish an unprotected trading preview or expose secrets to the browser.
const encoder = new TextEncoder();
const harden = { 'cache-control':'private, no-store', 'x-content-type-options':'nosniff',
  'x-frame-options':'DENY', 'referrer-policy':'no-referrer' };
function response(status,code,extra={}) {
  return new Response(JSON.stringify({ ok:false, code, meta:{ label:'BLOCKED', channel:'CLOUDFLARE_STAGING' } }),{
    status, headers:{ 'content-type':'application/json; charset=utf-8', ...harden, ...extra }
  });
}
async function matches(header,user,password) {
  if(!header || !header.startsWith('Basic ') || header.length>2048) return false;
  try {
    const bytes=Uint8Array.from(atob(header.slice(6)),x=>x.charCodeAt(0));
    const target=encoder.encode(user+':'+password);
    const [a,b]=await Promise.all([crypto.subtle.digest('SHA-256',bytes),crypto.subtle.digest('SHA-256',target)]);
    const av=new Uint8Array(a),bv=new Uint8Array(b);
    let delta=0;
    for(let i=0;i<av.length;i++) delta|=(av[i]^bv[i]);
    return delta===0;
  }catch{return false;}
}
export async function onRequest(context) {
  const user=context.env?.JEV_PREVIEW_USER, password=context.env?.JEV_PREVIEW_PASSWORD;
  if(typeof user!=='string'||!user||typeof password!=='string'||password.length<20)
    return response(503,'PREVIEW_AUTH_NOT_CONFIGURED');
  if(!await matches(context.request.headers.get('authorization'),user,password))
    return response(401,'PREVIEW_AUTH_REQUIRED',{'www-authenticate':'Basic realm="JEV Cloudflare Staging", charset="UTF-8"'});
  const upstream=await context.next();
  const headers=new Headers(upstream.headers);
  for(const [k,v] of Object.entries(harden))headers.set(k,v);
  return new Response(upstream.body,{status:upstream.status,statusText:upstream.statusText,headers});
}
