import assert from 'node:assert/strict';
import { onRequest } from '../../functions/_middleware.js';
import { onRequest as apiRequest } from '../../functions/api/jev/[[path]].js';
const env={ JEV_PREVIEW_USER:'ci', JEV_PREVIEW_PASSWORD:'a-fake-preview-password-with-32-characters' };
const next=async()=>new Response('ok',{status:200});
function context(header,variables=env){
  const headers=new Headers(); if(header)headers.set('authorization',header);
  return {request:new Request('https://preview.pages.dev/trading.html',{headers}),env:variables,next};
}
let r=await onRequest(context(null,{}));assert.equal(r.status,503);
r=await onRequest(context(null));assert.equal(r.status,401);
r=await onRequest(context('Basic '+btoa('ci:wrongpassword')));assert.equal(r.status,401);
r=await onRequest(context('Basic '+btoa('ci:'+env.JEV_PREVIEW_PASSWORD)));assert.equal(r.status,200);
assert.equal(r.headers.get('cache-control'),'private, no-store');
r=apiRequest();assert.equal(r.status,503);const data=await r.json();
assert.equal(data.ok,false);assert.equal(data.meta.label,'BLOCKED');
console.log('Cloudflare preview auth + API fail-closed: 5/5 PASS');
