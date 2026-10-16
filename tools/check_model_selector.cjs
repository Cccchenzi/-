'use strict';
// Real Edge rendering; all API responses are synthetic and all external traffic is blocked.
// node tools/check_model_selector.cjs [--output <directory>]
// Optional: PLAYWRIGHT_MODULE, EDGE_EXECUTABLE, ZHIWEI_LAYOUT_PROJECT, ZHIWEI_FRONTEND_DIR.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const project=path.resolve(process.env.ZHIWEI_LAYOUT_PROJECT||path.join(__dirname,'..'));
const frontend=path.resolve(process.env.ZHIWEI_FRONTEND_DIR||path.join(project,'frontend'));
const arg=process.argv.indexOf('--output');
if(arg>=0&&!process.argv[arg+1])throw Error('--output requires a directory');
const output=path.resolve(arg>=0?process.argv[arg+1]:path.join(os.tmpdir(),'zhiwei-model-selector-'+new Date().toISOString().replace(/[:.]/g,'-')));
const origin='http://zhiwei-model-selector.test';
const FLASH='deepseek-flash',PRO='deepseek-v4-pro',NEW='catalog-new-model';
const catalog=[{id:FLASH,display_name:'Flash'},{id:PRO,display_name:'Pro'},{id:NEW,display_name:'目录新模型 <img src=x>'}];
const users={alpha:{id:101,username:'alpha'},beta:{id:202,username:'beta'}};
const preferences={alpha:FLASH,beta:FLASH};
const checked={alpha:true,beta:true};
const server={active:'alpha',catalog,configured:true,embeddingConfigured:true,catalogError:'',saveError:'',catalogGate:null,saveGate:null,messageGate:null,dropSaveResponse:false,failConfigOnce:false,dropAndFailConfig:false};
const requests=[],gates=[];
const report={status:'running',checks:[],screenshots:[],browser_errors:[],blocked_requests:[],scope:'Current frontend in real headless Edge; synthetic accounts/catalog/config/messages only, no real server, user documents or model calls.'};
const date='2026-10-16T08:00:00Z';
const history=[{id:1,role:'user',content:'历史问题',created_at:date},{id:2,role:'assistant',mode:'llm',content:'历史成功回答',generation_model:'history-model-x',citations:[],created_at:date},{id:3,role:'assistant',mode:'llm',content:'没有模型字段的旧记录',citations:[],created_at:date},{id:4,role:'assistant',mode:'extractive-fallback',content:'旧摘录',notice:'合成失败提示',citations:[],created_at:date}];
let browser,context,page;
function config(user=server.active){const model=preferences[user]||FLASH,configured=server.configured&&server.embeddingConfigured,ready=!!checked[user]&&configured;return {mode:'llm',model,provider_configured:server.configured,embedding:'qwen3-embedding:0.6b',rag:{provider:'deepseek',chat_model:model,embedding_provider:'ollama',embedding_model:'qwen3-embedding:0.6b',configured,checked:ready,connected:ready,chat_ready:ready,embedding_ready:ready,message:ready?'合成检查通过':server.configured?'已选择模型，尚未检查。':'请配置 DEEPSEEK_API_KEY。'}};}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function gate(){const started=deferred(),release=deferred();const item={started,release};gates.push(item);return item;}
async function started(item){let timer;try{await Promise.race([item.started.promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('Deferred API request did not start')),10000))]);}finally{clearTimeout(timer);}}
function pass(name){report.checks.push(name);console.log('PASS '+name);}
async function settle(){await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));}
async function shot(name){const file=path.join(output,name+'.png');await page.screenshot({path:file,fullPage:true,animations:'disabled'});report.screenshots.push(file);}
async function openModels(wait=true){await page.locator('#mode-button').click();await page.locator('#deepseek-model').waitFor();if(wait)await page.waitForFunction(()=>!document.getElementById('deepseek-model-refresh').disabled);}
async function refreshCatalog(){await page.locator('#deepseek-model-refresh').click();await page.waitForFunction(()=>!document.getElementById('deepseek-model-refresh').disabled);}
async function closeModal(){if(await page.locator('#modal').isVisible())await page.locator('#modal-close').click();}
async function logout(){await closeModal();await page.locator('#user-button').click();await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.locator('#auth-screen').waitFor({state:'visible'});}
async function login(username){await page.locator('#auth-username').fill(username);await page.locator('#auth-password').fill('SyntheticOnly123!');await page.locator('#auth-submit').click();await page.locator('#app').waitFor({state:'visible'});await page.waitForFunction(name=>document.getElementById('username').textContent===name,username);await page.waitForFunction(()=>!document.getElementById('question').disabled);}
async function ready(){await page.locator('#app').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('question').disabled);}
async function session(){await closeModal();if(await page.locator('#menu-button').isVisible())await page.locator('#menu-button').click();await page.locator('.session-item').first().click();await page.locator('.message.assistant').first().waitFor();}
async function apply(model){await page.locator('#deepseek-model').selectOption(model);await page.locator('#deepseek-model-apply').click();await page.waitForFunction(()=>!document.getElementById('deepseek-model-refresh').disabled);}
async function reload(){await page.reload();await ready();}
async function routeRequest(route){
  const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method(),user=server.active;
  if(url.origin!==origin){report.blocked_requests.push(method+' '+url.origin);return route.abort();}
  const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  requests.push({method,path:p,user});
  if(method==='GET'){
    if(p==='/')return route.fulfill({status:200,contentType:'text/html',body:await fs.readFile(path.join(project,'frontend/index.html'),'utf8')});
    if(p==='/app.js'||p==='/styles.css')return route.fulfill({status:200,contentType:p.endsWith('.js')?'text/javascript':'text/css',body:await fs.readFile(path.join(frontend,p.slice(1)),'utf8')});
    if(p==='/favicon.ico')return route.fulfill({status:204});
    if(p==='/api/auth/me')return json({user:users[user]||null});
    if(p==='/api/config'){if(server.failConfigOnce){server.failConfigOnce=false;return json({error:'合成配置读取失败'},503);}return json(config(user));}
    if(p==='/api/deepseek/models'){
      const response={models:server.catalog.map(model=>({...model})),selected_model:preferences[user]};
      if(server.catalogGate){const hold=server.catalogGate;server.catalogGate=null;hold.started.resolve();await hold.release.promise;if(hold.status)return json({error:'合成过期登录响应'},hold.status);if(hold.models)response.models=hold.models;}
      return server.catalogError?json({error:server.catalogError},503):json(response);
    }
    if(p==='/api/libraries')return json({libraries:[{id:1,name:'模型选择合成测试',description:'所有内容为测试夹具',document_count:1,chunk_count:1,embedded_chunk_count:1,pending_chunk_count:0,session_count:1}]});
    if(p==='/api/libraries/1/documents')return json({documents:[{id:1,name:'合成资料.md',size:100,chunk_count:1,embedded_chunk_count:1,index_status:'ready'}]});
    if(p==='/api/libraries/1/sessions')return json({sessions:[{id:1,title:'合成历史',created_at:date}]});
    if(p==='/api/sessions/1')return json({session:{id:1,title:'合成历史'},messages:history});
  }
  if(method==='POST'){
    if(p==='/api/auth/logout'){server.active=null;return json({ok:true});}
    if(p==='/api/auth/login'){server.active=request.postDataJSON().username;return json({user:users[server.active]});}
    if(p==='/api/models/check'){checked[user]=true;return json(config(user));}
    if(p==='/api/deepseek/model'){
      const model=request.postDataJSON().model;
      if(server.saveGate){const hold=server.saveGate;server.saveGate=null;hold.started.resolve();await hold.release.promise;}
      if(server.saveError)return json({error:server.saveError},409);
      preferences[user]=model;checked[user]=false;
      if(server.dropSaveResponse||server.dropAndFailConfig){server.dropSaveResponse=false;if(server.dropAndFailConfig){server.dropAndFailConfig=false;server.failConfigOnce=true;}return route.abort('connectionreset');}
      return json(config(user));
    }
    if(p==='/api/sessions/1/messages'){
      const model=preferences[user],question=request.postDataJSON().question;
      if(server.messageGate){const hold=server.messageGate;server.messageGate=null;hold.started.resolve();await hold.release.promise;}
      const id=100+history.length,user_message={id,role:'user',content:question,created_at:date},assistant_message={id:id+1,role:'assistant',mode:'llm',content:'这是合成成功回答。',generation_model:model,citations:[],created_at:date};history.push(user_message,assistant_message);return json({user_message,assistant_message});
    }
  }
  report.blocked_requests.push(method+' '+p);return route.abort();
}
(async()=>{
  await fs.mkdir(output,{recursive:true});
  browser=await chromium.launch({headless:true,...(process.env.EDGE_EXECUTABLE?{executablePath:process.env.EDGE_EXECUTABLE}:{channel:'msedge'}),args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce',serviceWorkers:'block'});await context.route('**/*',routeRequest);
  page=await context.newPage();page.on('pageerror',error=>report.browser_errors.push(error.message));await page.goto(origin);await ready();
  await openModels();assert.equal(await page.locator('#deepseek-model').inputValue(),FLASH);assert.equal(await page.locator('#deepseek-model-apply').isDisabled(),true);
  assert.match(await page.locator(`#deepseek-model option[value="${FLASH}"]`).textContent(),/默认.*低费用/);assert.ok((await page.locator('#deepseek-model option').allTextContents()).some(text=>text.includes(NEW)&&text.includes('<img')));assert.equal(await page.locator('#modal-body img').count(),0);
  assert.equal(requests.filter(r=>r.path==='/api/deepseek/model').length,0);pass('Default Flash, dynamic catalog IDs, safe text, no automatic model switch');await shot('desktop-default');
  await apply(PRO);assert.equal(preferences.alpha,PRO);assert.equal(await page.locator('#mode-label').textContent(),'模型待检查');assert.match(await page.locator('#deepseek-model-status').textContent(),/已保存.*检查模型连接/);assert.equal(requests.filter(r=>r.path==='/api/models/check').length,0);pass('Explicit apply saves preference and remains unchecked without inference');
  await session();assert.deepEqual(await page.locator('.generation-model').allTextContents(),['模型：history-model-x']);
  await page.locator('#question').fill('使用新模型回答');await page.locator('#send-button').click();await page.waitForFunction(()=>document.querySelectorAll('.generation-model').length===2);assert.deepEqual(await page.locator('.generation-model').allTextContents(),['模型：history-model-x','模型：'+PRO]);pass('Recorded model stays with each answer; old records and fallback are not backfilled');
  await reload();await openModels();assert.equal(await page.locator('#deepseek-model').inputValue(),PRO);pass('Preference reloads from the account config');
  server.catalogError='合成目录网络失败';await refreshCatalog();assert.match(await page.locator('#deepseek-model-status').textContent(),/读取失败/);assert.equal(await page.locator('#deepseek-model').isDisabled(),true);assert.equal(preferences.alpha,PRO);pass('Catalog failure preserves active model and disables stale choices');
  server.catalogError='';server.catalog=[];await refreshCatalog();assert.match(await page.locator('#deepseek-model-status').textContent(),/列表为空/);assert.equal(await page.locator('#deepseek-model-apply').isDisabled(),true);pass('Empty catalog has an honest empty state');
  server.catalog=[catalog[0]];await refreshCatalog();assert.match(await page.locator('#deepseek-model-status').textContent(),/未出现在/);assert.equal(await page.locator(`#deepseek-model option[value="${PRO}"]`).isDisabled(),true);pass('Missing current model remains visible without becoming a fake selectable option');
  server.catalog=catalog;await refreshCatalog();const loading=gate();server.catalogGate=loading;await page.locator('#deepseek-model-refresh').click();await started(loading);assert.equal(await page.locator('#deepseek-model').isDisabled(),true);assert.match(await page.locator('#deepseek-model-status').textContent(),/正在从/);loading.release.resolve();await page.waitForFunction(()=>!document.getElementById('deepseek-model-refresh').disabled);pass('Catalog loading locks controls and recovers');
  server.saveError='合成模型不可选';await apply(FLASH);assert.equal(preferences.alpha,PRO);assert.match(await page.locator('#deepseek-model-status').textContent(),/当前读取到的模型/);assert.equal(await page.locator('#deepseek-model').inputValue(),PRO);pass('Rejected selection re-reads actual config and preserves the confirmed model');
  server.saveError='';await refreshCatalog();server.dropSaveResponse=true;await apply(FLASH);assert.equal(preferences.alpha,FLASH);assert.equal(await page.locator('#deepseek-model').inputValue(),FLASH);assert.match(await page.locator('#deepseek-model-status').textContent(),/已确认当前模型/);assert.equal(await page.locator('#mode-label').textContent(),'模型待检查');pass('Lost save response is reconciled with server config instead of falsely claiming no change');
  server.dropAndFailConfig=true;await apply(PRO);assert.match(await page.locator('#deepseek-model-status').textContent(),/未能确认切换结果/);assert.equal(await page.locator('#mode-label').textContent(),'模型状态未知');pass('Unknown save plus failed verification is explicitly unconfirmed');await reload();await openModels();
  server.embeddingConfigured=false;const chatOnlyCount=requests.filter(r=>r.path==='/api/deepseek/models').length;await closeModal();await openModels();assert.ok(requests.filter(r=>r.path==='/api/deepseek/models').length>chatOnlyCount);assert.equal(await page.locator('#deepseek-model').isDisabled(),false);assert.equal(await page.locator('#mode-label').textContent(),'模型配置未完成');assert.doesNotMatch(await page.locator('#deepseek-model-status').textContent(),/配置 API Key/);pass('Configured chat service can select models while embedding setup is incomplete');server.embeddingConfigured=true;
  server.configured=false;const before=requests.filter(r=>r.path==='/api/deepseek/models').length;await closeModal();await openModels(false);await page.waitForFunction(()=>document.getElementById('mode-label').textContent==='DeepSeek 待配置');assert.equal(await page.locator('#deepseek-model-refresh').isDisabled(),true);assert.equal(await page.locator('#deepseek-model-apply').isDisabled(),true);assert.match(await page.locator('#deepseek-model-status').textContent(),/API Key/);assert.equal(requests.filter(r=>r.path==='/api/deepseek/models').length,before);assert.equal(await page.locator('#modal input[type="password"]').count(),0);pass('Missing key neither loads catalog nor collects credentials');server.configured=true;await closeModal();await openModels();
  await session();const pending=gate();server.messageGate=pending;await page.locator('#question').fill('延迟回答');await page.locator('#send-button').click();await started(pending);await openModels();assert.equal(await page.locator('#deepseek-model').isDisabled(),true);assert.equal(await page.locator('#deepseek-model-apply').isDisabled(),true);assert.match(await page.locator('#deepseek-model-status').textContent(),/回答正在生成/);pending.release.resolve();await page.waitForFunction(()=>!document.getElementById('deepseek-model').disabled);pass('In-flight answer prevents switching and completion unlocks selection');
  const saving=gate();server.saveGate=saving;await page.locator('#deepseek-model').selectOption(FLASH);await page.locator('#deepseek-model-apply').click();await started(saving);assert.equal(await page.locator('#deepseek-model-apply').isDisabled(),true);await closeModal();assert.equal(await page.locator('#question').isDisabled(),true);saving.release.resolve();await page.waitForFunction(()=>!document.getElementById('question').disabled);pass('Saving cannot re-enter and temporarily locks new questions');
  const stale401=gate();stale401.status=401;server.catalogGate=stale401;await openModels(false);await started(stale401);await logout();await login('beta');const oldResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/deepseek/models'&&r.status()===401);stale401.release.resolve();await oldResponse;await settle();assert.equal(await page.locator('#username').textContent(),'beta');assert.equal(await page.locator('#app').isVisible(),true);pass('Old account catalog 401 cannot log out the new account');
  const staleSame=gate();staleSame.models=[{id:'stale-only-model',display_name:'过期目录'}];server.catalogGate=staleSame;await openModels(false);await started(staleSame);await logout();await login('beta');await openModels();const later=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/deepseek/models');staleSame.release.resolve();await later;await settle();assert.equal(await page.locator('#deepseek-model option[value="stale-only-model"]').count(),0);pass('Same-account re-login rejects the previous authentication session catalog');
  const staleSave=gate();server.saveGate=staleSave;await page.locator('#deepseek-model').selectOption(PRO);await page.locator('#deepseek-model-apply').click();await started(staleSave);await logout();await login('alpha');await openModels();const oldSave=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/deepseek/model');staleSave.release.resolve();await oldSave;await settle();assert.equal(await page.locator('#deepseek-model').inputValue(),preferences.alpha);assert.equal(await page.locator('#username').textContent(),'alpha');pass('Late save result never replaces another account config');
  await shot('desktop-selected');await page.setViewportSize({width:390,height:844});await settle();await page.locator('#deepseek-model-apply').scrollIntoViewIfNeeded();
  const geometry=await page.evaluate(()=>{const r=document.getElementById('modal').getBoundingClientRect();return {top:r.top,bottom:r.bottom,right:r.right,w:innerWidth,h:innerHeight,body:document.documentElement.scrollWidth};});assert.ok(geometry.top>=0&&geometry.bottom<=geometry.h+1&&geometry.right<=geometry.w+1&&geometry.body<=geometry.w+1);await shot('mobile-selector');pass('390px dialog and model actions remain within the viewport');
  assert.deepEqual(report.browser_errors,[]);assert.deepEqual(report.blocked_requests,[]);pass('No browser errors or external/unexpected requests');report.status='passed';
})().catch(error=>{report.status='failed';report.error=String(error.stack||error);console.error(String(error.message||error));process.exitCode=1;}).finally(async()=>{for(const item of gates)item.release.resolve();await context?.close();await browser?.close();await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log('Report: '+path.join(output,'summary.json'));});
