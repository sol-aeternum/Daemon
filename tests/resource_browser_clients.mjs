// Real isolated browser sessions and actual bearer HTTP; no auth or catalog mocks.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
let input='';
for await (const chunk of process.stdin) input+=chunk;
const {base,headers,taskId}=JSON.parse(input);
const browser=await chromium.launch({headless:true});
try {
  const contexts=await Promise.all(headers.map(extraHTTPHeaders=>browser.newContext({extraHTTPHeaders})));
  const pages=await Promise.all(contexts.map(context=>context.newPage()));
  for(const page of pages)await page.goto(base+'/resources');
  const catalog=async page=>page.evaluate(async()=>{const r=await fetch('/resources',{cache:'no-store'});return {status:r.status,data:await r.json()};});
  const a=await catalog(pages[0]);const a2=await catalog(pages[1]);const b=await catalog(pages[2]);
  assert.equal(a.status,200);assert.deepEqual(a.data,a2.data);assert.deepEqual(b.data.items,[]);
  const bytes=async(page,path)=>page.evaluate(async path=>{const r=await fetch(path,{cache:'no-store'});return {status:r.status,body:[...new Uint8Array(await r.arrayBuffer())],etag:r.headers.get('etag')};},path);
  const pins=[];
  for(const item of a.data.items){
    const path=`/resources/${item.resource_id}/versions/${item.version_id}`;
    for(const suffix of ['', '/preview','/content'])assert.equal((await bytes(pages[2],path+suffix)).status,404);
    const content=await bytes(pages[0],path+'/content');
    if(item.representation==='metadata_only'){assert.equal(content.status,409);continue;}
    assert.equal(content.status,200);assert.deepEqual(content,await bytes(pages[1],path+'/content'));pins.push([path,content]);
  }
  assert.equal((await bytes(pages[2],`/tasks/${taskId}/inputs`)).status,404);
  await contexts[0].close();
  for(const [path,content] of pins)assert.deepEqual(await bytes(pages[1],path+'/content'),content);
  // A new B identity cannot read an A validator or content even using identical IDs.
  await contexts[1].setExtraHTTPHeaders(headers[2]);
  for(const [path] of pins)assert.equal((await bytes(pages[1],path+'/content')).status,404);
  console.log(JSON.stringify({contexts:3,byte_versions:pins.length,closed_A_replayed:true,foreign_denied:true}));
} finally {await browser.close();}
