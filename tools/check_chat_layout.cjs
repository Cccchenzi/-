'use strict';

// All HTML, CSS and JavaScript are served from source files via Playwright routing.
// Every API response is synthetic. No live server, account, document or model is used.
// Run: node tools/check_chat_layout.cjs [--output <screenshot-directory>]
// Uses installed playwright and Edge. Optional PLAYWRIGHT_MODULE / EDGE_EXECUTABLE
// select existing installations; ZHIWEI_LAYOUT_PROJECT overrides the repository root.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const project = path.resolve(process.env.ZHIWEI_LAYOUT_PROJECT || path.join(__dirname,'..'));
const origin = 'http://zhiwei-layout.test';
const outputArg = process.argv.indexOf('--output');
if(outputArg>=0&&!process.argv[outputArg+1])throw new Error('--output requires a directory');
const outputDir = path.resolve(outputArg>=0?process.argv[outputArg+1]:path.join(os.tmpdir(),'zhiwei-chat-layout-'+new Date().toISOString().replace(/[:.]/g,'-')));
const report = {status:'running', checks:[], measurements:[], screenshots:[], browser_errors:[], blocked_requests:[], scope:'Real headless Edge; current repository HTML/CSS/JS with synthetic API fixtures only; no live server, user data or model API access.'};
const fixedDate = '2026-10-16T08:00:00Z';
const citation = {index:1,chunk_id:1,document_id:1,document_name:'长文本布局测试.md',page:null,start_line:1,end_line:90,score:null,
  content:Array.from({length:90},(_,i)=>`第 ${i+1} 行：这是一段用于检查引用预览滚动的合成原文，确保引用内容不会把主页面撑高。`).join('\n')};
const citations = Array.from({length:15},(_,i)=>({...citation,index:i+1,chunk_id:i+1,document_name:`合成测试资料-${i+1}.md`}));
const longText = Array.from({length:80},(_,i)=>`${i+1}. 这是布局测试用的长段落。原文摘录会保留换行与引用，输入框应始终位于视口内，长回答必须在消息区域单独滚动。[1]`).join('\n\n') + '\n' + 'LongUnbrokenSyntheticToken'.repeat(60);
// Persisted metadata is flattened by the backend's message_json into these fields.
const failureNotices = {generation:'合成场景：模型生成请求未成功，已展示可用原文摘录。',validation:'合成场景：模型回复未通过原文证据核验，已展示可用原文摘录。'};
const answer = (id,content=longText,phase='validation',retrieval='overview')=>({id,role:'assistant',content,mode:'extractive-fallback',retrieval_mode:retrieval,generation_status:'fallback',failure_stage:phase,failure_code:phase==='generation'?'generation_failed':'evidence_validation_failed',notice:failureNotices[phase],citations,created_at:fixedDate});
const sessions = [{id:11,title:'单条长摘录（合成）',created_at:fixedDate},{id:12,title:'多轮长会话（合成）',created_at:fixedDate}];
const sessionMessages = new Map([
  [11,[{id:1,role:'user',content:'请给出完整长摘录。',created_at:fixedDate},answer(2)]],
  [12,Array.from({length:12},(_,i)=>[{id:100+i*2,role:'user',content:`第 ${i+1} 轮合成提问，请继续说明。`,created_at:fixedDate},answer(101+i*2,`第 ${i+1} 轮合成回答。\n`+longText.slice(0,1300),i%2?'generation':'validation',['dense','lexical','overview'][i%3])]).flat()]
]);
const library = {id:1,name:'布局测试（全为合成数据）',description:'仅测试渲染，不读取任何用户数据。',document_count:1,chunk_count:15,session_count:2,embedded_chunk_count:15,pending_chunk_count:0};
const config = {mode:'llm',model:'deepseek-flash',embedding:'qwen3-embedding:0.6b',provider_configured:true,rag:{provider:'deepseek',chat_model:'deepseek-flash',embedding_provider:'ollama',embedding_model:'qwen3-embedding:0.6b',configured:true,checked:true,connected:true,chat_ready:true,embedding_ready:true,message:'布局测试的模拟模型状态；不会发起模型调用。'}};
let browser, page, context;
function pass(name){report.checks.push(name);console.log('PASS '+name);}
async function screenshot(name){const dest=path.join(outputDir,name+'.png');await page.screenshot({path:dest,fullPage:true,animations:'disabled'});report.screenshots.push(dest);}
async function measure(label){
  // Edge may report new innerWidth/innerHeight before media queries and dvh recalculate.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const data=await page.evaluate(()=>{
    const rect=id=>{const r=document.querySelector(id).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height};};
    const hit=id=>{const node=document.querySelector(id),r=node.getBoundingClientRect(),target=document.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2);return !!target&&(target===node||node.contains(target));};
    const chat=document.getElementById('chat-scroll');
    return {viewport:{width:innerWidth,height:innerHeight},document:{height:document.documentElement.scrollHeight,width:document.documentElement.scrollWidth},body:{height:document.body.scrollHeight,width:document.body.scrollWidth},workspace:rect('.workspace'),composer:rect('.composer-zone'),textarea:rect('#question'),send:rect('#send-button'),textareaHit:hit('#question'),sendHit:hit('#send-button'),chat:{...rect('#chat-scroll'),clientHeight:chat.clientHeight,scrollHeight:chat.scrollHeight,scrollTop:chat.scrollTop}};
  });
  report.measurements.push({label,...data});
  return data;
}
function assertLayout(data,label){
  assert.ok(data.composer.bottom<=data.viewport.height+1,label+': composer below viewport');
  assert.ok(data.composer.top>=0&&data.textarea.height>0,label+': composer has no visible input');
  assert.ok(data.send.bottom<=data.viewport.height+1,label+': send button below viewport');
  assert.ok(data.textareaHit&&data.sendHit,label+': input or send button is obscured by an overlay/toast');
  assert.ok(Math.max(data.document.height,data.body.height)<=data.viewport.height+1,label+': document grows beyond viewport');
  assert.ok(Math.max(data.document.width,data.body.width)<=data.viewport.width+1,label+': horizontal overflow');
  assert.ok(data.chat.clientHeight>30&&data.chat.scrollHeight>data.chat.clientHeight,label+': message pane does not scroll independently');
  pass(label+': visible composer, bounded page, independent message scroll');
}
async function checkScroll(label){
  const result=await page.evaluate(async()=>{
    const chat=document.getElementById('chat-scroll');chat.style.scrollBehavior='auto';chat.scrollTop=0;
    const before=document.getElementById('question-form').getBoundingClientRect().top;
    chat.scrollTop=chat.scrollHeight;
    await new Promise(resolve=>requestAnimationFrame(resolve));
    return {top:chat.scrollTop,max:chat.scrollHeight-chat.clientHeight,before,after:document.getElementById('question-form').getBoundingClientRect().top,windowY:scrollY};
  });
  assert.ok(result.max>0&&Math.abs(result.top-result.max)<=1,label+': cannot reach end of message pane');
  assert.equal(result.before,result.after,label+': composer moved with messages');
  assert.equal(result.windowY,0,label+': window scrolled');
  pass(label+': scroll reaches latest answer without moving composer');
}
async function selectSession(title){
  if(await page.locator('#menu-button').isVisible())await page.locator('#menu-button').click();
  await page.locator('.session-item').filter({hasText:title}).click();
  await page.locator('.message.assistant').first().waitFor();
  await page.waitForFunction(()=>!document.getElementById('question').disabled);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function checkCitation(label){
  await page.locator('.message-source-chip').last().click();
  await page.locator('#preview-modal').waitFor({state:'visible'});
  const preview=await page.evaluate(()=>{const modal=document.getElementById('preview-modal'),body=document.getElementById('preview-body'),r=modal.getBoundingClientRect();body.scrollTop=body.scrollHeight;return {top:r.top,bottom:r.bottom,right:r.right,scrollTop:body.scrollTop,height:innerHeight,width:innerWidth};});
  assert.ok(preview.top>=0&&preview.bottom<=preview.height+1&&preview.right<=preview.width+1,label+': citation preview outside viewport');
  assert.ok(preview.scrollTop>0,label+': citation preview does not scroll');
  await screenshot(label+'-citation-preview');
  await page.locator('#preview-close').click();
  await page.locator('#toggle-sources').click();
  // On desktop this button hides the panel first; open it again for panel verification.
  if(!await page.locator('#source-panel').isVisible())await page.locator('#toggle-sources').click();
  await page.locator('#citations-tab').click();
  await page.locator('.citation-card').last().scrollIntoViewIfNeeded();
  const panel=await page.evaluate(()=>{const r=document.getElementById('source-panel').getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:innerHeight};});
  assert.ok(panel.top>=-1&&panel.bottom<=panel.height+1,label+': citations panel outside viewport');
  await screenshot(label+'-citations-panel');
  if(await page.locator('#sidebar-shade').isVisible())await page.locator('#sidebar-shade').click({position:{x:5,y:5}});
  assertLayout(await measure(label+'-after-citation'),label+' after citation');
  pass(label+': citation preview and source panel remain bounded');
}
(async()=>{
  await fs.mkdir(outputDir,{recursive:true});
  const html=await fs.readFile(path.join(project,'frontend/index.html'),'utf8');
  const css=await fs.readFile(path.join(project,'frontend/styles.css'),'utf8');
  const app=await fs.readFile(path.join(project,'frontend/app.js'),'utf8');
  browser=await chromium.launch({headless:true,...(process.env.EDGE_EXECUTABLE?{executablePath:process.env.EDGE_EXECUTABLE}:{channel:'msedge'}),args:['--disable-background-networking','--disable-component-update','--no-first-run']});
  context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce',serviceWorkers:'block'});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),pathname=url.pathname;
    if(url.origin!==origin){report.blocked_requests.push(request.method()+' '+url.origin);return route.abort();}
    const send=data=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    if(request.method()==='GET'){
      if(pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:html});
      if(pathname==='/styles.css')return route.fulfill({status:200,contentType:'text/css',body:css});
      if(pathname==='/app.js')return route.fulfill({status:200,contentType:'text/javascript',body:app});
      if(pathname==='/favicon.ico')return route.fulfill({status:204});
      if(pathname==='/api/auth/me')return send({user:{id:9001,username:'layout-fixture'}});
      if(pathname==='/api/config')return send(config);
      if(pathname==='/api/libraries')return send({libraries:[library]});
      if(pathname==='/api/libraries/1/documents')return send({documents:[{id:1,name:'长文本布局测试.md',size:12000,chunk_count:15,embedded_chunk_count:15,index_status:'ready',created_at:fixedDate}]});
      if(pathname==='/api/libraries/1/sessions')return send({sessions});
      const match=pathname.match(/^\/api\/sessions\/(11|12)$/);
      if(match)return send({session:sessions.find(s=>s.id===+match[1]),messages:sessionMessages.get(+match[1])});
    }
    const postMatch=pathname.match(/^\/api\/sessions\/(11|12)\/messages$/);
    if(request.method()==='POST'&&postMatch){
      const question=request.postDataJSON().question,id=+postMatch[1],messages=sessionMessages.get(id);
      const user_message={id:10000+messages.length,role:'user',content:question,created_at:fixedDate},assistant_message=answer(user_message.id+1,'这是合成追加回答，用于验证发送后输入框仍可使用。\n'+longText.slice(0,1000),'generation');
      messages.push(user_message,assistant_message);return send({user_message,assistant_message,notice:assistant_message.notice});
    }
    report.blocked_requests.push(request.method()+' '+pathname);return route.abort();
  });
  page=await context.newPage();page.on('pageerror',error=>report.browser_errors.push(error.message));
  await page.goto(origin);await page.locator('.session-item').first().waitFor();
  await selectSession('单条长摘录');
  assert.equal(await page.locator('.message.assistant .retrieval-mode').first().textContent(),'文档概览');
  assert.equal(await page.locator('.message.assistant .message-notice').first().textContent(),failureNotices.validation);
  pass('History restores overview label and safe evidence-validation failure notice');
  const first=await measure('desktop-single');
  await screenshot('desktop-single');
    assertLayout(first,'desktop single long fallback');await checkScroll('desktop single');
    await page.locator('#question').fill('继续说明（合成追问）');await page.locator('#send-button').click();
    await page.waitForFunction(()=>document.querySelectorAll('.message.assistant').length===2);
    assert.equal(await page.locator('.message.assistant .message-notice').last().textContent(),failureNotices.generation);
    pass('New fallback answer preserves safe generation-failure notice');
    assertLayout(await measure('desktop-followup'),'desktop follow-up');
    await selectSession('多轮长会话');
    assert.deepEqual(await page.locator('.message.assistant .retrieval-mode').allTextContents(),Array.from({length:12},(_,i)=>['语义向量检索','词法检索','文档概览'][i%3]));
    assert.equal(await page.locator('.message.assistant .message-notice').last().textContent(),failureNotices.generation);
    pass('History preserves generation failure and dense/lexical/overview labels');
    assertLayout(await measure('desktop-multiturn'),'desktop 12 turns');await checkScroll('desktop multiple turns');
    await screenshot('desktop-multiturn');await checkCitation('desktop');
    await page.locator('#toggle-sources').click();assertLayout(await measure('desktop-no-sources'),'desktop without sources');
    await page.setViewportSize({width:1920,height:1080});assertLayout(await measure('desktop-1920'),'desktop 1920x1080');await screenshot('desktop-1920');
    await page.setViewportSize({width:1280,height:720});assertLayout(await measure('desktop-1280'),'desktop 1280x720');await screenshot('desktop-1280');
    await page.setViewportSize({width:1440,height:560});assertLayout(await measure('desktop-short'),'desktop 560px height');
    await page.setViewportSize({width:390,height:844});await selectSession('单条长摘录');
    assertLayout(await measure('mobile-single'),'mobile single long fallback');await checkScroll('mobile single');await screenshot('mobile-single');
    await selectSession('多轮长会话');assertLayout(await measure('mobile-multiturn'),'mobile 12 turns');await checkScroll('mobile multiple turns');
    await screenshot('mobile-multiturn');await checkCitation('mobile');
    // Create a real UI toast through the mocked send path, then hit-test the controls.
    await page.locator('#question').fill('触发手机合成失败提示');await page.locator('#send-button').click();
    await page.waitForFunction(()=>document.querySelectorAll('.message.assistant').length===13);
    await page.locator('#toast-stack .toast.error').last().waitFor({state:'visible'});
    await page.locator('#question').fill(Array.from({length:12},(_,i)=>`第${i+1}行追问`).join('\n'));
    assertLayout(await measure('mobile-expanded-composer'),'mobile multiline composer');await screenshot('mobile-expanded-composer');
    await page.locator('#question').fill('短屏追问');await page.setViewportSize({width:390,height:400});
    assertLayout(await measure('mobile-short'),'mobile 400px height');await screenshot('mobile-short');
    await page.locator('#question').fill(Array.from({length:12},(_,i)=>`短屏第${i+1}行追问`).join('\n'));
    assertLayout(await measure('mobile-short-multiline'),'mobile short multiline composer');await screenshot('mobile-short-multiline');
    await page.setViewportSize({width:390,height:320});
    assertLayout(await measure('mobile-shortest-multiline'),'mobile 320px height multiline composer');await screenshot('mobile-shortest-multiline');
    assert.ok(await page.locator('#toast-stack .toast.error').count()>0,'The short-screen hit test must include an active error toast');
    pass('Active error toasts do not block mobile input or send button');
    assert.deepEqual(report.browser_errors,[],'Browser JavaScript errors');assert.deepEqual(report.blocked_requests,[],'Unexpected/external requests');
    pass('No browser errors or external/unexpected requests');report.status='passed';
})().catch(error=>{report.status='failed';report.error=String(error.stack||error);console.error(String(error.message||error));process.exitCode=1;}).finally(async()=>{
  await context?.close();await browser?.close();await fs.mkdir(outputDir,{recursive:true});await fs.writeFile(path.join(outputDir,'summary.json'),JSON.stringify(report,null,2));console.log('Report: '+path.join(outputDir,'summary.json'));
});
