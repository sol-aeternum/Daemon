// Owned dynamic loopback fixture. Real production components, auth/cache modules
// and resource proxy; actual PG/POSIX backend, never route interception or mocks.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import { build } from '../frontend/node_modules/esbuild/lib/main.js';
import { createServer } from 'node:http';
import { pipeFixtureResponse } from './resource_fixture_stream.mjs';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
const input=createInterface({input:process.stdin});const frames=input[Symbol.asyncIterator]();
const {base,headers,conversation,pin1,pin2}=JSON.parse((await frames.next()).value);
const frontend=resolve('frontend');
// Next's normal webpack build replaces its process.env compile constants. This
// in-memory fixture compiler must do the same; never inject host environment.
const bundled=await build({absWorkingDir:frontend,entryPoints:['e2e/resource-native-harness.tsx'],bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',tsconfig:'tsconfig.json',define:{'process.env':'{}','process.env.NODE_ENV':'"production"','process.env.NEXT_PUBLIC_API_URL':'""'}});
const proxyBuild=await build({absWorkingDir:frontend,entryPoints:['lib/resourceProxy.ts'],bundle:true,write:false,format:'esm',platform:'node',define:{'process.env.DAEMON_INTERNAL_API_URL':JSON.stringify(base)}});
const {resourceProxy}=await import('data:text/javascript;base64,'+Buffer.from(proxyBuild.outputFiles[0].text).toString('base64'));
let holdNextPreview=false, previewBlocked, releasePreview;
const streamFailures=[],streamCancellations=[];
const blocked=new Promise(r=>{previewBlocked=r;});
const released=new Promise(r=>{releasePreview=r;});
const server=createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://fixture.test');
    if(url.pathname==='/') {res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});res.end('<div id="root"></div><script type="module" src="/bundle.js"></script>');return;}
    if(url.pathname==='/bundle.js'){res.writeHead(200,{'Content-Type':'text/javascript','Cache-Control':'no-store'});res.end(bundled.outputFiles[0].text);return;}
    if(req.method==='POST'&&url.pathname==='/api/v1/auth/cache/clear'){
      // Actual backend cache endpoint validates this browser's same-origin signal.
      const reply=await fetch(base+'/v1/auth/cache/clear',{method:'POST',headers:{Origin:req.headers.origin??'',Referer:req.headers.referer??'','Sec-Fetch-Site':req.headers['sec-fetch-site']??''},redirect:'error'});
      res.writeHead(reply.status,Object.fromEntries(reply.headers));res.end();return;
    }
    if(req.method!=='GET'){res.writeHead(405,{'Cache-Control':'private, no-store'});res.end();return;}
    const target=url.pathname.replace(/^\/api/,'');
    const lifetime=new AbortController();res.on('close',()=>lifetime.abort());
    const request=new Request('http://fixture.test'+url.pathname+url.search,{headers:req.headers,signal:lifetime.signal});
    let reply;
    if(/^\/conversations\/[0-9a-f-]{36}$/.test(target)){
      reply=await fetch(base+target+url.search,{headers:{Authorization:req.headers.authorization??''},cache:'no-store',redirect:'error'});
    }else reply=await resourceProxy(request,target);
    if(holdNextPreview&&target.endsWith('/preview')){holdNextPreview=false;previewBlocked();await released;}
    if(res.destroyed){await reply.body?.cancel();return;}
    res.writeHead(reply.status,Object.fromEntries(reply.headers));
    if(reply.body)pipeFixtureResponse(reply.body,res,lifetime.signal,streamFailures,streamCancellations);else res.end();
  }catch {res.writeHead(503,{'Cache-Control':'private, no-store'});res.end();}
});
await new Promise((r,j)=>{server.once('error',j);server.listen(0,'127.0.0.1',r);});
const ui='http://127.0.0.1:'+server.address().port;
console.log(JSON.stringify({ui}));
if((await frames.next()).value!=='armed')throw new Error('Fixture not armed');
input.close();
const browser=await chromium.launch({headless:true});
try{
  const contexts=await Promise.all(headers.map(()=>browser.newContext()));
  const pages=await Promise.all(contexts.map(c=>c.newPage()));
  for(const p of pages)p.on('pageerror',e=>console.error('native fixture page error:',e.message));
  const calls=[];for(const p of pages){p.on('request',r=>{if(r.url().includes('/api/'))calls.push([r.method(),new URL(r.url()).pathname]);});p.on('response',async r=>{if(r.url().includes('/api/')&&r.status()>=400)console.error('native API refusal',r.status(),new URL(r.url()).pathname);});}
  for(const p of pages){await p.goto(ui);await p.waitForFunction(()=>typeof window.qualifyResourceUI==='function');}
  for(let i=0;i<2;i++){
    // Preserve the deliberately tiny shared admission limit: qualify each real
    // client before starting the next, rather than assuming four simultaneous
    // gallery/input responses are admitted by a two-response account budget.
    await pages[i].evaluate(({token,conversation})=>window.qualifyResourceUI(token,conversation),{token:headers[i].Authorization.slice(7),conversation});
    const saved=pages[i].getByRole('region',{name:'Saved chat turns'});
    try{await saved.getByText('Version '+pin1).waitFor({timeout:10000});}
    catch(e){console.error('synthetic native fixture UI:',await pages[i].locator('body').innerText());throw e;}
    assert.equal(await saved.getByText('Version '+pin2).count(),0);
    await saved.locator('article').filter({hasText:pin1}).getByRole('button',{name:'Preview'}).click();
    await saved.locator('pre').first().waitFor();
  }
  const original=await pages[1].getByRole('region',{name:'Saved chat turns'}).locator('pre').first().textContent();
  await contexts[0].close();
  await pages[1].reload();await pages[1].waitForFunction(()=>typeof window.qualifyResourceUI==='function');
  await pages[1].evaluate(({token,conversation})=>window.qualifyResourceUI(token,conversation),{token:headers[1].Authorization.slice(7),conversation});
  const saved=pages[1].getByRole('region',{name:'Saved chat turns'});
  await saved.getByText('Version '+pin1).waitFor();await saved.locator('article').filter({hasText:pin1}).getByRole('button',{name:'Preview'}).click();await saved.locator('pre').first().waitFor();
  assert.equal(await saved.locator('pre').first().textContent(),original);
  // Begin a genuine resource request with A; switching auth must abort/discard it.
  holdNextPreview=true;
  await saved.locator('article').filter({hasText:pin1}).getByRole('button',{name:'Preview'}).click();
  await blocked;
  await pages[1].evaluate(async({token,conversation})=>{try{await window.qualifyResourceUI(token,conversation);}catch{/* B cannot read A history */}}, {token:headers[2].Authorization.slice(7),conversation});
  releasePreview();
  await pages[1].getByText('Version '+pin1).waitFor({state:'detached'});
  assert.equal(await pages[1].locator('pre').count(),0);
  const denied=await pages[1].evaluate(async({token,version})=>{const r=await fetch('/api/resources?task_id='+version,{headers:{Authorization:'Bearer '+token},cache:'no-store'});return await r.json();},{token:headers[2].Authorization.slice(7),version:conversation});
  assert.deepEqual(denied.items,[]);
   assert.equal(calls.filter(([method,path])=>method!=='GET'&&path!=='/api/v1/auth/cache/clear').length,0);
   assert.deepEqual(streamFailures,[]);
   console.error('native fixture stream cancellations:',JSON.stringify(streamCancellations));
   console.log(JSON.stringify({native_components:true,production_auth_cache:true,actual_resource_proxy:true,pinned_history_reloaded:true,two_sessions:true,account_switch_cleared:true,local_generation_calls:0}));
}finally{releasePreview();await browser.close();await new Promise((r,j)=>server.close(e=>e?j(e):r()));assert.deepEqual(streamFailures,[]);}
