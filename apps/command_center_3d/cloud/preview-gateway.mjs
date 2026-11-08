// JEV staging-only same-origin gateway. No production-trading support.
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { createHash, timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { URL } from 'node:url';

const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff', '.wasm': 'application/wasm', '.json': 'application/json; charset=utf-8' };
const noForward = new Set(['authorization','connection','transfer-encoding','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','upgrade','host','origin','x-forwarded-host','x-forwarded-proto','x-forwarded-for']);
const secureHeaders = { 'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer', 'x-frame-options': 'DENY', 'cache-control': 'no-store' };
function send(res, status, body, type='application/json; charset=utf-8', more={}) {
  if (res.headersSent) return;
  res.writeHead(status, {...secureHeaders, 'content-type':type,...more}); res.end(typeof body === 'string' ? body : JSON.stringify(body));
}
function safeAuth(header, user, password) {
  if (typeof header !== 'string' || !header.startsWith('Basic ') || header.length > 2048) return false;
  let decoded;
  try { decoded=Buffer.from(header.slice(6),'base64').toString('utf8'); } catch { return false; }
  const ix=decoded.indexOf(':'); if(ix<=0) return false;
  // Compare equal-length cryptographic digests in constant time.
  const incoming=createHash('sha256').update(decoded).digest();
  const expected=createHash('sha256').update(`${user}:${password}`).digest();
  return timingSafeEqual(incoming,expected);
}
function validOrigin(req) {
  const origin=req.headers.origin;
  if (typeof origin!=='string') return false;
  const host=req.headers.host;
  if (!host || /[\r\n]/.test(host)) return false;
  const proto= req.headers['x-forwarded-proto']==='https'?'https':
    (host.startsWith('localhost:')||host.startsWith('127.0.0.1:')?'http':'https');
  return origin===`${proto}://${host}`;
}
function gatewayUrl(requestUrl) {
  try {return new URL(requestUrl,'http://localhost');} catch {return null;}
}
async function proxyToAdapter(req,res,upstream) {
  const headers={};
  for(const [k,v] of Object.entries(req.headers)) if (!noForward.has(k) && v!==undefined) headers[k]=v;
  // Adapter's own origin check must see its internal same-origin URL after
  // the public gateway has checked the real browser Origin.
  if(!['GET','HEAD','OPTIONS'].includes(req.method)) headers.origin=upstream.origin;
  headers.host=upstream.host;
  const proxy=http.request(new URL(req.url,upstream), {method:req.method,headers, timeout:15000}, upstreamRes=>{
    if(res.headersSent) return;
    const answerHeaders={...secureHeaders};
    for(const [k,v] of Object.entries(upstreamRes.headers)) {
      if(!['transfer-encoding','connection','content-length','content-security-policy'].includes(k) && v!==undefined) answerHeaders[k]=v;
    }
    answerHeaders['cache-control']='no-store';
    res.writeHead(upstreamRes.statusCode||502,answerHeaders);
    upstreamRes.pipe(res);
  });
  proxy.on('timeout',()=>proxy.destroy(new Error('timeout')));
  proxy.on('error',()=>{if(!res.headersSent)send(res,502,{error:'UPSTREAM_UNAVAILABLE'});else res.destroy();});
  req.pipe(proxy);
}
async function checkAdapter(upstream, healthPath) {
  return new Promise(resolve=>{
    const r=http.get(new URL(healthPath,upstream),{timeout:2000},resp=>{resp.resume();resolve(resp.statusCode===200);});
    r.on('timeout',()=>r.destroy()); r.on('error',()=>resolve(false));
  });
}
export function createGateway({staticDir, upstreamUrl='http://127.0.0.1:4791', previewUser, previewPassword, healthPath='/health'}) {
  if (!previewUser || !previewPassword || previewPassword.length<20) throw new Error('PREVIEW_CREDENTIALS_REQUIRED');
  const root=path.resolve(staticDir), upstream=new URL(upstreamUrl);
  if (upstream.protocol!=='http:' || !['127.0.0.1','localhost'].includes(upstream.hostname)) throw new Error('LOOPBACK_UPSTREAM_ONLY');
  return http.createServer(async(req,res)=>{
    const u=gatewayUrl(req.url);
    if(!u)return send(res,400,{error:'BAD_URL'});
    if(u.pathname==='/healthz'){
      const ok=await checkAdapter(upstream,healthPath);
      return send(res,ok?200:503,{status:ok?'ready':'unavailable'});
    }
    if(!safeAuth(req.headers.authorization,previewUser,previewPassword)) return send(res,401,{error:'PREVIEW_AUTH_REQUIRED'},'application/json; charset=utf-8',{'www-authenticate':'Basic realm="JEV Staging"'});
    if(u.pathname.startsWith('/api/')){
      if(!u.pathname.startsWith('/api/jev/')) return send(res,404,{error:'API_NOT_EXPOSED'});
      if(!['GET','HEAD','OPTIONS'].includes(req.method) && !validOrigin(req)) return send(res,403,{error:'ORIGIN_REJECTED'});
      return proxyToAdapter(req,res,upstream);
    }
    if(!['GET','HEAD'].includes(req.method)) return send(res,405,{error:'METHOD_NOT_ALLOWED'});
    let p;
    try { p=decodeURIComponent(u.pathname); }catch{return send(res,400,{error:'BAD_PATH'});}
    if(p==='/' )p='/trading.html';
    if(p.includes('\\') || p.split('/').some(seg=>seg==='..'||seg.startsWith('.')) || /\0/.test(p)) return send(res,404,{error:'NOT_FOUND'});
    const ext=path.extname(p);
    if(!types[ext]||ext==='.json' && !p.startsWith('/assets/')) return send(res,404,{error:'NOT_FOUND'});
    const dest=path.resolve(root,`.${p}`);
    if(!dest.startsWith(root+path.sep))return send(res,404,{error:'NOT_FOUND'});
    try {
      const details=await stat(dest); if(!details.isFile()||details.size>25*1024*1024)throw new Error('file invalid');
      const data=await readFile(dest);
      res.writeHead(200,{...secureHeaders,'content-type':types[ext]});
      res.end(req.method==='HEAD'?undefined:data);
    }catch{send(res,404,{error:'NOT_FOUND'});}
  });
}
