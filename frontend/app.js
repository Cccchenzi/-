"use strict";

const ICONS = {
  plus:'<path d="M12 5v14M5 12h14"/>',
  library:'<path d="M4 4v16M8 4v16M12 4v16M16 5l4 14"/>',
  message:'<path d="M20 11.5a8.5 8.5 0 0 1-8.5 8.5 9 9 0 0 1-4-.9L3 21l1.9-4.5a9 9 0 0 1-.9-4A8.5 8.5 0 0 1 12.5 4h.5a8.5 8.5 0 0 1 7 7v.5Z"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1 .7-1.5 1-1.5 2.5M12 17h.01"/>',
  'arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>',
  'arrow-right':'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-up':'<path d="M12 20V4m-6 6 6-6 6 6"/>',
  'chevron-down':'<path d="m6 9 6 6 6-6"/>',
  more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  shield:'<path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Z"/><path d="m8 12 3 3 5-6"/>',
  quote:'<path d="M10 7H4v6h6V7Zm10 0h-6v6h6V7ZM10 13c0 3-2 5-5 5m15-5c0 3-2 5-5 5"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  file:'<path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v6h5M8 13h8M8 17h5"/>',
  book:'<path d="M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1Zm0 0v15"/>',
  sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  lightbulb:'<path d="M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0c-1 .7-1 2-1 2H9s0-1.3-1-2Z"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  alert:'<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
  copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  logout:'<path d="M9 3H3v18h6m5-16 7 7-7 7m7-7H8"/>',
  link:'<path d="m9 15 6-6m-7 9-2 2a4.2 4.2 0 0 1-6-6l4-4a4.2 4.2 0 0 1 6 0m4-4 2-2a4.2 4.2 0 0 1 6 6l-4 4a4.2 4.2 0 0 1-6 0" transform="translate(1 0) scale(.92)"/>'
};
const $ = id => document.getElementById(id);
const state = {user:null,health:null,libraries:[],libraryId:null,documents:[],sessions:[],sessionId:null,messages:[],citations:[],nav:0,pending:new Map(),creatingSession:false,uploading:false,register:false,authBusy:false,previewRequest:0};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name) {
  const node = el('span');
  // The SVG strings are fixed application assets, never document or user content.
  node.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.file}</svg>`;
  return node;
}
function iconButton(name, title, callback, className='') {
  const button = el('button',`icon-button ${className}`);
  button.type='button'; button.title=title; button.setAttribute('aria-label',title);
  button.append(icon(name)); button.addEventListener('click',callback);
  return button;
}
function button(text, callback, className='button') {
  const node=el('button',className,text); node.type='button'; node.addEventListener('click',callback); return node;
}
function spinner() { const node=el('span','spinner'); node.setAttribute('aria-label','正在处理'); return node; }
function library() { return state.libraries.find(item=>item.id===state.libraryId); }
function sameId(a,b) { return String(a)===String(b); }
function dateText(value) { if(!value)return ''; const d=new Date(value); return Number.isNaN(d.getTime())?'':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
function sizeText(bytes) { const n=Number(bytes)||0; return n>=1048576?`${(n/1048576).toFixed(1)} MB`:n>=1024?`${Math.round(n/1024)} KB`:`${n} B`; }
function modeText(mode) { return mode==='llm'?'AI 问答':'本地原文检索'; }
function locationText(chunk) { const parts=[]; if(chunk.page)parts.push(`第 ${chunk.page} 页`); if(chunk.start_line)parts.push(chunk.end_line && chunk.end_line!==chunk.start_line?`第 ${chunk.start_line}–${chunk.end_line} 行`:`第 ${chunk.start_line} 行`); if(!parts.length && chunk.ordinal!==undefined)parts.push(`片段 ${chunk.ordinal}`); return parts.join(' · ')||'原文片段'; }
function downloadUrl(id) { return `/api/documents/${encodeURIComponent(id)}/download`; }
function toast(message,type='success') {
  const node=el('div',`toast ${type}`); node.setAttribute('role',type==='error'?'alert':'status');
  node.append(icon(type==='error'?'alert':'check'),el('span','',message));
  const close=iconButton('close','关闭提示',()=>node.remove(),'toast-close');node.append(close);$('toast-stack').append(node);
  setTimeout(()=>node.remove(),type==='error'?8500:5000);
}
async function api(path,options={}) {
  const init={credentials:'same-origin',...options,headers:{...options.headers}};
  if(init.body && !(init.body instanceof FormData)){init.headers['Content-Type']='application/json';init.body=JSON.stringify(init.body);}
  let response;
  try{response=await fetch(path,init);}catch{throw new Error('连接失败，请确认本地服务已启动，再重试。');}
  const data=await response.json().catch(()=>({error:`服务返回异常（${response.status}）` }));
  if(!response.ok){
    const details=Array.isArray(data.errors)?data.errors.map(item=>`${item.name}：${item.error}`).join('；'):'';
    const error=new Error(typeof data.error==='string'?data.error:(data.error?.message || data.message || details || `请求失败（${response.status}）`));error.status=response.status;
    if(response.status===401 && state.user){showAuth();toast('登录已失效，请重新登录。','error');}
    throw error;
  }
  return data;
}
function setAuthBusy(busy) {$('auth-submit').disabled=busy;$('auth-toggle').disabled=busy;state.authBusy=busy;}
function showAuth(){state.user=null;state.nav++;state.previewRequest++;state.libraryId=null;state.sessionId=null;state.libraries=[];state.documents=[];state.sessions=[];state.messages=[];state.citations=[];state.pending.clear();$('app').hidden=true;$('auth-screen').hidden=false;$('loading-screen').hidden=true;closeMobilePanels();if($('modal').open)$('modal').close();if($('preview-modal').open)$('preview-modal').close();}
function updateAuthForm(){
  $('auth-title').textContent=state.register?'创建你的知识空间':'欢迎回来';
  $('auth-description').textContent=state.register?'从第一份资料开始，让知识随时可用。':'登录知微，继续连接你的知识。';
  $('auth-submit').replaceChildren(document.createTextNode(state.register?'创建账户':'登录'),icon('arrow-right'));
  $('auth-toggle').textContent=state.register?'前往登录':'创建账户';
  $('auth-switch-description').textContent=state.register?'已经有账户？':'第一次使用？';
  $('auth-password').autocomplete=state.register?'new-password':'current-password';
  $('auth-error').textContent='';
}
async function authenticate(event){
  event.preventDefault();if(state.authBusy)return;setAuthBusy(true);$('auth-error').textContent='';
  try{const data=await api(`/api/auth/${state.register?'register':'login'}`,{method:'POST',body:{username:$('auth-username').value.trim(),password:$('auth-password').value}});state.user=data.user;$('auth-password').value='';await loadWorkspace();}
  catch(error){$('auth-error').textContent=error.message;}
  finally{setAuthBusy(false);}
}
async function loadWorkspace(){
  const userId=state.user.id;
  $('username').textContent=state.user.username;$('user-avatar').textContent=state.user.username.slice(0,1).toUpperCase();
  $('auth-screen').hidden=true;$('app').hidden=false;$('loading-screen').hidden=true;
  try{const data=await api('/api/libraries');if(state.user?.id!==userId)return;state.libraries=data.libraries||[];renderLibraries();
    let last;try{last=localStorage.getItem(`zhiwei-library:${state.user.username}`);}catch{}
    const selected=state.libraries.find(item=>sameId(item.id,last))||state.libraries[0];
    if(selected)await selectLibrary(selected.id);else renderNoLibrary();
  }catch(error){toast(error.message,'error');renderNoLibrary();}
}
function renderLibraries(){
  const list=$('library-list');list.replaceChildren();
  for(const item of state.libraries){const row=button('',()=>selectLibrary(item.id),'library-item');row.classList.toggle('active',item.id===state.libraryId);row.setAttribute('aria-pressed',String(item.id===state.libraryId));row.title=item.name;row.append(icon('library'),el('span','lib-name',item.name),el('span','library-count',item.document_count||0));list.append(row);}
  if(!state.libraries.length){const create=button('',showCreateLibrary,'empty-library-action');create.append(icon('plus'),el('span','','创建第一个知识库'));list.append(create);}
}
function renderNoLibrary(){state.nav++;state.libraryId=null;state.sessionId=null;state.documents=[];state.sessions=[];state.messages=[];state.citations=[];renderLibraries();renderLibraryHeader();renderDocuments();renderSessions();renderMessages();}
function renderLibraryHeader(){
  const current=library();$('current-library-name').textContent=current?.name||'我的知识空间';$('composer-library-name').textContent=current?.name||'请先创建知识库';$('library-description').textContent=current?.description||'收集资料，连接你的每一次思考。';
  $('welcome-description').replaceChildren(document.createTextNode(current?'上传你的资料，用自然语言提问。':'先创建一个知识库，开始收集资料。'),el('br'),document.createTextNode('从答案出发，随时回到原文。'));
  $('library-settings').disabled=!current;$('upload-zone').disabled=!current||state.uploading;$('import-samples').disabled=state.uploading;
  $('stat-documents').textContent=state.documents.length;$('stat-chunks').textContent=state.documents.reduce((sum,item)=>sum+(item.chunk_count||0),0);$('stat-sessions').textContent=state.sessions.length;
  updateComposer();
}
async function selectLibrary(id){
  if(state.libraryId===id){closeMobilePanels();return;}
  const token=++state.nav;state.libraryId=id;state.sessionId=null;state.messages=[];state.citations=[];state.documents=[];state.sessions=[];
  try{localStorage.setItem(`zhiwei-library:${state.user.username}`,String(id));}catch{}
  renderLibraries();renderLibraryHeader();renderMessages();renderDocuments();renderSessions();closeMobilePanels();
  $('session-list').replaceChildren(el('div','loading-label','正在加载对话…'));$('document-list').replaceChildren(el('div','loading-label','正在加载文档…'));
  const results=await Promise.allSettled([api(`/api/libraries/${id}/documents`),api(`/api/libraries/${id}/sessions`)]);
  if(token!==state.nav)return;
  if(results[0].status==='fulfilled')state.documents=results[0].value.documents||[];else toast(results[0].reason.message,'error');
  if(results[1].status==='fulfilled')state.sessions=results[1].value.sessions||[];else toast(results[1].reason.message,'error');
  renderDocuments();renderSessions();renderLibraryHeader();
}
async function refreshLibraries(){const userId=state.user?.id;const result=await api('/api/libraries');if(!state.user||state.user.id!==userId)return;state.libraries=result.libraries||[];renderLibraries();renderLibraryHeader();}
async function refreshLibraryFiles(id){
  const result=await api(`/api/libraries/${id}/documents`);if(state.libraryId===id){state.documents=result.documents||[];renderDocuments();renderLibraryHeader();}
  await refreshLibraries();
}
async function refreshSessions(id){const result=await api(`/api/libraries/${id}/sessions`);if(state.libraryId===id){state.sessions=result.sessions||[];renderSessions();renderLibraryHeader();}}
function renderSessions(){
  const list=$('session-list');list.replaceChildren();$('history-count').textContent=state.sessions.length;
  if(!state.sessions.length){list.append(el('p','sidebar-empty',state.libraryId?'你的第一段对话，从这里开始。':'创建知识库后，即可开始对话。'));return;}
  for(const session of state.sessions){const row=el('div','session-row');row.classList.toggle('active',session.id===state.sessionId);const select=button('',()=>selectSession(session.id),'session-item');select.title=session.title||'新对话';select.append(icon('message'),el('span','',session.title||'新对话'));if(session.id===state.sessionId)select.setAttribute('aria-current','true');row.append(select,iconButton('trash','删除对话',()=>deleteSession(session),'session-delete'));list.append(row);}
}
async function selectSession(id){
  const token=++state.nav;state.sessionId=id;state.messages=[];state.citations=[];renderSessions();renderMessages();closeMobilePanels();
  $('welcome').hidden=true;$('messages').append(el('p','loading-label','正在加载对话…'));
  try{const data=await api(`/api/sessions/${id}`);if(token!==state.nav)return;state.messages=data.messages||[];if(data.session){const index=state.sessions.findIndex(item=>item.id===id);if(index>=0)state.sessions[index]=data.session;}selectLatestCitations();renderSessions();renderMessages();}
  catch(error){if(token!==state.nav)return;toast(error.message,'error');state.sessionId=null;renderMessages();}
}
function newConversation(){if(!state.libraryId){showCreateLibrary();return;}state.nav++;state.sessionId=null;state.messages=[];state.citations=[];renderSessions();renderMessages();closeMobilePanels();$('question').value='';resizeComposer();$('question').focus();}
function selectLatestCitations(){const last=[...state.messages].reverse().find(item=>item.role==='assistant');state.citations=last?.citations||[];}
function renderMessages(){
  const list=$('messages');list.replaceChildren();$('welcome').hidden=state.messages.length>0||!!state.pending.get(state.sessionId);
  const current=state.sessions.find(item=>item.id===state.sessionId);$('conversation-title').textContent=state.sessionId?(current?.title||'新的对话'):'知识问答';
  for(const message of state.messages)list.append(renderMessage(message));
  const pending=state.pending.get(state.sessionId);
  if(pending){if(!state.messages.some(item=>item.id===pending.tempId||item.role==='user'&&item.content===pending.question&&item===state.messages[state.messages.length-1]))list.append(renderMessage({id:pending.tempId,role:'user',content:pending.question}));const row=el('article','message assistant');const avatar=el('div','message-avatar');avatar.append(icon('sparkles'));const body=el('div','message-main');const heading=el('div','message-heading');heading.append(el('strong','','知微'),el('span','message-mode',state.health?.mode==='llm'?'正在检索与生成':'正在查找相关原文'));const dots=el('div','thinking-dots');dots.setAttribute('aria-label','正在处理问题');dots.append(el('span'),el('span'),el('span'));body.append(heading,dots);row.append(avatar,body);list.append(row);}
  renderCitations();updateComposer();scrollBottom();
}
function renderMessage(message){
  const row=el('article',`message ${message.role==='user'?'user':'assistant'}`);const avatar=el('div','message-avatar');
  if(message.role==='user')avatar.textContent=state.user?.username.slice(0,1).toUpperCase()||'我';else avatar.append(icon('sparkles'));
  const main=el('div','message-main');const heading=el('div','message-heading');heading.append(el('strong','',message.role==='user'?'你':'知微'));if(message.role!=='user')heading.append(el('span','message-mode',modeText(message.mode||state.health?.mode)));
  const content=el('div','message-content');const citations=Array.isArray(message.citations)?message.citations:[];appendCitedText(content,message.content||'',citations);main.append(heading,content);
  if(citations.length){const chips=el('div','message-sources');for(const source of citations){const chip=button('',()=>showCitation(source,citations),'message-source-chip');chip.append(icon('file'),el('span','',`[${source.index}] ${source.document_name}`));chips.append(chip);}main.append(chips);}
  if(message.role!=='user'){const footer=el('div','message-footer');footer.append(el('span','',dateText(message.created_at)));const copy=button('',()=>copyText(message.content||''),'');copy.append(icon('copy'),el('span','','复制回答'));footer.append(copy);if(citations.length){const view=button('',()=>{state.citations=citations;renderCitations();showPanel('citations');},'');view.append(icon('quote'),el('span','',`${citations.length} 处引用`));footer.append(view);}main.append(footer);}
  row.append(avatar,main);return row;
}
function appendCitedText(target,text,citations){
  const regex=/\[(\d+)\]/g;let position=0;let match;
  while((match=regex.exec(String(text)))){target.append(document.createTextNode(String(text).slice(position,match.index)));const citation=citations.find(item=>Number(item.index)===Number(match[1]));if(citation){const cite=button(match[0],()=>showCitation(citation,citations),'inline-citation');cite.title=`查看引用：${citation.document_name}`;target.append(cite);}else target.append(document.createTextNode(match[0]));position=regex.lastIndex;}
  target.append(document.createTextNode(String(text).slice(position)));
}
async function copyText(text){try{await navigator.clipboard.writeText(text);toast('回答已复制。');}catch{toast('浏览器未允许复制，请选中回答文字后复制。','error');}}
function scrollBottom(){requestAnimationFrame(()=>{$('chat-scroll').scrollTop=$('chat-scroll').scrollHeight;});}
function resizeComposer(){const input=$('question');input.style.height='auto';input.style.height=`${Math.min(input.scrollHeight,150)}px`;updateComposer();}
function updateComposer(){const busy=state.creatingSession||state.pending.has(state.sessionId);$('send-button').disabled=!state.libraryId||!$('question').value.trim()||busy;$('question').disabled=!state.libraryId;$('send-button').replaceChildren(busy?spinner():icon('arrow-up'));$('send-button').setAttribute('aria-label',busy?'正在生成回答':'发送问题');}
async function sendQuestion(event){
  event.preventDefault();const question=$('question').value.trim();if(!question||!state.libraryId||state.creatingSession||state.pending.has(state.sessionId))return;
  const libraryId=state.libraryId;const token=state.nav;let sessionId=state.sessionId;const userId=state.user.id;const tempId=`pending-${Date.now()}`;
  $('question').value='';resizeComposer();
  try{
    if(!sessionId){state.creatingSession=true;updateComposer();const data=await api(`/api/libraries/${libraryId}/sessions`,{method:'POST',body:{}});sessionId=data.session.id;if(state.libraryId===libraryId){state.sessions.unshift(data.session);if(state.nav===token)state.sessionId=sessionId;renderSessions();}}
    state.creatingSession=false;
    if(!state.user||state.user.id!==userId)return;
    state.pending.set(sessionId,{question,tempId});if(state.sessionId===sessionId)renderMessages();else updateComposer();
    const data=await api(`/api/sessions/${sessionId}/messages`,{method:'POST',body:{question}});
    state.pending.delete(sessionId);if(!state.user||state.user.id!==userId)return;
    if(state.sessionId===sessionId){state.nav++;if(data.user_message && !state.messages.some(item=>item.id===data.user_message.id))state.messages.push(data.user_message);else if(!data.user_message)state.messages.push({id:tempId,role:'user',content:question});if(!state.messages.some(item=>item.id===data.assistant_message.id))state.messages.push(data.assistant_message);selectLatestCitations();renderMessages();if(data.assistant_message.citations?.length)showPanel('citations',false);}
    else toast('一条对话已完成，可在最近对话中查看。');
    if(data.notice)toast(data.notice);
    await refreshSessions(libraryId);
    if(state.sessionId===sessionId)$('conversation-title').textContent=state.sessions.find(item=>item.id===sessionId)?.title||'新的对话';
  }catch(error){state.pending.delete(sessionId);if(state.user?.id!==userId)return;toast(error.message,'error');if(state.sessionId===sessionId||state.nav===token){if(!$('question').value)$('question').value=question;resizeComposer();renderMessages();const errorNode=el('div','chat-error');errorNode.append(icon('alert'),el('span','',`这次提问未完成：${error.message} 你可以重试。`));$('messages').append(errorNode);scrollBottom();}}
  finally{state.creatingSession=false;updateComposer();}
}
function renderDocuments(){
  const list=$('document-list');list.replaceChildren();$('document-count').textContent=state.documents.length;$('document-total-size').textContent=state.documents.length?sizeText(state.documents.reduce((sum,item)=>sum+(Number(item.size)||0),0)):'';
  if(!state.documents.length){const empty=el('div','empty-documents');empty.append(icon('book'),el('strong','','这里，等待你的第一份资料'),el('span','',state.libraryId?'上传文档后，即可开始检索和提问。':'先创建知识库，再上传你的资料。'));list.append(empty);}
  for(const document of state.documents){const row=el('div','document-item');const extension=(document.name.split('.').pop()||'FILE').toLowerCase();const badge=el('span',`file-badge ${['pdf','md'].includes(extension)?extension:''}`,extension.toUpperCase().slice(0,4));const info=el('div','document-info');const name=button(document.name,()=>previewDocument(document),'document-name');name.title=document.name;const meta=el('div','document-meta');meta.append(el('span','',sizeText(document.size)),el('span','','·'),el('span','',`${document.chunk_count||0} 个片段`));info.append(name,meta);const actions=el('div','document-actions');const download=el('a','icon-button');download.href=downloadUrl(document.id);download.title='下载原文';download.setAttribute('aria-label',`下载 ${document.name}`);download.append(icon('download'));actions.append(download,iconButton('trash',`删除 ${document.name}`,()=>deleteDocument(document)));row.append(badge,info,actions);list.append(row);}
  renderLibraryHeader();
}
function renderCitations(){
  $('citation-count').textContent=state.citations.length;const list=$('citation-list');list.replaceChildren();
  if(!state.citations.length){const empty=el('div','citation-empty');empty.append(icon('quote'),el('strong','','让答案有据可查'),el('span','','提问后，相关原文会显示在这里。点击回答中的引用编号，即可查看出处。'));list.append(empty);return;}
  for(const citation of state.citations){const card=button('',()=>showCitation(citation,state.citations),'citation-card');card.dataset.index=String(citation.index);const header=el('div','citation-card-header');header.append(el('span','citation-number',citation.index),el('span','citation-card-name',citation.document_name));const footer=el('div','citation-location');footer.append(el('span','',citation.available===false?'原文已删除 · 引用快照':locationText(citation)),icon('arrow-up-right'));card.append(header,el('p','citation-excerpt',citation.content),footer);list.append(card);}
}
function setPanelTab(tab){const documents=tab==='documents';$('documents-view').hidden=!documents;$('citations-view').hidden=documents;$('documents-tab').classList.toggle('active',documents);$('citations-tab').classList.toggle('active',!documents);$('documents-tab').setAttribute('aria-selected',String(documents));$('citations-tab').setAttribute('aria-selected',String(!documents));}
function showPanel(tab,force=true){setPanelTab(tab);if(force){$('app').classList.remove('sources-hidden');if(window.innerWidth<=1000){$('app').classList.add('sources-open');$('sidebar-shade').hidden=false;}}}
function closeMobilePanels(){$('app').classList.remove('sidebar-open','sources-open');$('sidebar-shade').hidden=true;}
function showCitation(citation,citations){
  state.citations=citations;renderCitations();setPanelTab('citations');state.previewRequest++;
  $('preview-title').textContent=citation.document_name;const body=$('preview-body');body.replaceChildren();const meta=el('div','preview-meta');meta.append(el('span','',`引用 [${citation.index}] · ${locationText(citation)}`));if(citation.available!==false){const download=el('a','','下载原文');download.href=downloadUrl(citation.document_id);meta.append(download);}body.append(meta);if(citation.available===false)body.append(el('p','preview-empty','原文已删除，以下为提问时保存的引用快照。'));body.append(el('div','preview-content',citation.content));openPreview();
}
async function previewDocument(document){
  const request=++state.previewRequest;$('preview-title').textContent=document.name;$('preview-body').replaceChildren(el('div','loading-label','正在读取文档片段…'));openPreview();
  try{const data=await api(`/api/documents/${document.id}/chunks`);if(request!==state.previewRequest)return;const body=$('preview-body');body.replaceChildren();const meta=el('div','preview-meta');meta.append(el('span','',`${data.chunks?.length||0} 个知识片段 · ${sizeText(document.size)}`));const download=el('a','','下载原文');download.href=downloadUrl(document.id);meta.append(download);body.append(meta);for(const [index,chunk] of (data.chunks||[]).entries()){const section=el('section','chunk-section');section.append(el('span','chunk-label',`片段 ${index+1} · ${locationText(chunk)}`),el('div','preview-content',chunk.content));body.append(section);}if(!data.chunks?.length)body.append(el('p','preview-empty','此文档没有可预览的文本片段。'));}
  catch(error){if(request===state.previewRequest)$('preview-body').replaceChildren(el('p','modal-error',error.message));}
}
function openPreview(){if(!$('preview-modal').open)$('preview-modal').showModal();}
async function uploadFiles(files){
  if(!state.libraryId||state.uploading||!files.length)return;const libraryId=state.libraryId;const selected=Array.from(files);if(selected.length>10){toast('每批最多上传 10 个文件，请分批上传。','error');return;}const supported=/\.(pdf|txt|md)$/i;const accepted=[];
  for(const file of selected){if(!supported.test(file.name))toast(`${file.name}：不支持此格式。`,'error');else if(file.size>20*1024*1024)toast(`${file.name}：文件超过 20 MB。`,'error');else accepted.push(file);}
  if(!accepted.length)return;state.uploading=true;updateUploadState(accepted.length);
  const form=new FormData();accepted.forEach(file=>form.append('files',file));
  try{const data=await api(`/api/libraries/${libraryId}/documents`,{method:'POST',body:form});if(data.documents?.length)toast(`已处理 ${data.documents.length} 份文档，可以开始提问了。`);for(const error of (data.errors||[]))toast(`${error.name}：${error.error}`,'error');await refreshLibraryFiles(libraryId);}
  catch(error){toast(error.message,'error');}
  finally{state.uploading=false;updateUploadState();$('file-input').value='';}
}
function updateUploadState(count=0){const zone=$('upload-zone');zone.classList.toggle('uploading',state.uploading);zone.disabled=!state.libraryId||state.uploading;const strong=zone.querySelector('strong');strong.textContent=state.uploading?`正在处理 ${count||''} 份文档…`:'点击或拖拽上传文件';zone.querySelector('.upload-icon').replaceChildren(state.uploading?spinner():icon('upload'));$('import-samples').disabled=state.uploading;}
async function importSamples(){
  if(state.uploading)return;if(!state.libraryId){showCreateLibrary();return;}const libraryId=state.libraryId;state.uploading=true;updateUploadState();$('import-samples').querySelector('span:nth-child(2)').textContent='正在导入示例资料…';
  try{const data=await api(`/api/libraries/${libraryId}/seed`,{method:'POST',body:{}});if(data.imported)toast(`已导入 ${data.imported} 份示例资料。`);else if(data.skipped)toast('示例资料已经在知识库中，可以直接提问。');for(const item of data.errors||[])toast(`${item.name}：${item.error}`,'error');await refreshLibraryFiles(libraryId);if(state.libraryId===libraryId&&(data.imported||data.skipped)){showPanel('documents',false);$('question').value='需求工程的五步是什么？';resizeComposer();$('question').focus();}}
  catch(error){toast(error.message,'error');}
  finally{state.uploading=false;updateUploadState();$('import-samples').querySelector('span:nth-child(2)').textContent='还没有资料？导入示例，体验一次知识问答';}
}
function modal(title){$('modal-title').textContent=title;$('modal-body').replaceChildren();if(!$('modal').open)$('modal').showModal();return $('modal-body');}
function modalActions(body,confirmText,onConfirm,danger=false){const actions=el('div','modal-actions');actions.append(button('取消',()=>$('modal').close()));const confirm=button(confirmText,onConfirm,`button ${danger?'danger':'primary'}`);actions.append(confirm);body.append(actions);return confirm;}
function showCreateLibrary(){
  const body=modal('新建知识库');const form=el('form');const label=el('label','','知识库名称');label.htmlFor='library-name-input';const name=el('input','field-input');name.id='library-name-input';name.required=true;name.maxLength=80;name.placeholder='例如：课程学习、项目资料、读书笔记';const descriptionLabel=el('label','','描述（选填）');descriptionLabel.htmlFor='library-description-input';const description=el('textarea','field-input');description.id='library-description-input';description.rows=3;description.maxLength=500;description.placeholder='这个知识库将收集哪些资料？';const error=el('p','modal-error');error.setAttribute('role','alert');form.append(label,name,descriptionLabel,description,error);body.append(form);const actions=el('div','modal-actions');actions.append(button('取消',()=>$('modal').close()));const submit=el('button','button primary','创建知识库');submit.type='submit';actions.append(submit);form.append(actions);
  form.addEventListener('submit',async event=>{event.preventDefault();if(submit.disabled)return;const clean=name.value.trim();if(!clean){error.textContent='请输入知识库名称。';name.focus();return;}submit.disabled=true;error.textContent='';try{const data=await api('/api/libraries',{method:'POST',body:{name:clean,description:description.value.trim()}});state.libraries.unshift(data.library);$('modal').close();await selectLibrary(data.library.id);toast('知识库已创建，现在可以上传资料了。');}catch(err){error.textContent=err.message;}finally{submit.disabled=false;}});requestAnimationFrame(()=>name.focus());
}
function showLibrarySettings(){
  const current=library();if(!current)return;const body=modal(current.name);body.append(el('p','',current.description||'这个知识库还没有填写描述。'));for(const [key,value] of [['文档',`${state.documents.length} 份`],['知识片段',`${state.documents.reduce((sum,d)=>sum+(d.chunk_count||0),0)} 个`],['历史对话',`${state.sessions.length} 段`]]){const row=el('div','info-row');row.append(el('span','',key),el('strong','',value));body.append(row);}const actions=el('div','modal-actions');actions.append(button('删除知识库',()=>confirmDeleteLibrary(current),'button danger'));actions.append(button('完成',()=>$('modal').close(),'button primary'));body.append(actions);
}
function confirmDeleteLibrary(current){
  const body=modal('删除知识库');body.append(el('p','',`确定删除「${current.name}」吗？其中的文档、知识片段和历史对话也会一并删除。此操作无法撤销。`));const error=el('p','modal-error');body.append(error);const confirm=modalActions(body,'确认删除',async()=>{confirm.disabled=true;try{await api(`/api/libraries/${current.id}`,{method:'DELETE'});$('modal').close();state.libraries=state.libraries.filter(item=>item.id!==current.id);if(state.libraryId===current.id){state.libraryId=null;if(state.libraries.length)await selectLibrary(state.libraries[0].id);else renderNoLibrary();}else renderLibraries();toast('知识库已删除。');}catch(err){error.textContent=err.message;}finally{confirm.disabled=false;}},true);
}
function deleteDocument(document){
  const libraryId=state.libraryId;const body=modal('删除文档');body.append(el('p','',`确定删除「${document.name}」吗？删除后，该文档不会再参与新的检索。`));const error=el('p','modal-error');body.append(error);const confirm=modalActions(body,'删除文档',async()=>{confirm.disabled=true;try{await api(`/api/documents/${document.id}`,{method:'DELETE'});$('modal').close();for(const message of state.messages)for(const citation of message.citations||[])if(citation.document_id===document.id)citation.available=false;for(const citation of state.citations)if(citation.document_id===document.id)citation.available=false;renderCitations();await refreshLibraryFiles(libraryId);toast('文档已删除。');}catch(err){error.textContent=err.message;}finally{confirm.disabled=false;}},true);
}
function deleteSession(session){
  if(state.pending.has(session.id)){toast('这段对话仍在生成回答，请完成后再删除。','error');return;}
  const libraryId=state.libraryId;const body=modal('删除对话');body.append(el('p','',`确定删除「${session.title||'新对话'}」及全部消息吗？此操作无法撤销。`));const error=el('p','modal-error');body.append(error);const confirm=modalActions(body,'删除对话',async()=>{confirm.disabled=true;try{await api(`/api/sessions/${session.id}`,{method:'DELETE'});$('modal').close();if(state.sessionId===session.id)newConversation();await refreshSessions(libraryId);toast('对话已删除。');}catch(err){error.textContent=err.message;}finally{confirm.disabled=false;}},true);
}
function showMode(){
  const body=modal('当前问答模式');const mode=state.health?.mode;body.append(el('p','',mode==='llm'?'已连接语言模型。知微会先从当前知识库检索资料，再将相关片段交给模型生成带引用的回答。':'当前使用本地原文检索：根据问题查找相关资料，并展示原文片段与出处。此模式不调用外部语言模型，不生成超出原文的总结。'));
  for(const [key,value] of [['当前模式',modeText(mode)],['回答模型',state.health?.model||'未配置'],['检索方式',state.health?.embedding||'本地文本检索']]){const row=el('div','info-row');row.append(el('span','',key),el('strong','',value));body.append(row);}
  body.append(el('div','modal-note',mode==='llm'?'使用外部模型时，问题和相关原文片段会发送给已配置的模型服务。重要结论请核对引用。':'如需 AI 总结与综合问答，可按照项目 README 配置模型服务并重启应用。未配置时，原文检索功能仍可正常使用。'));const actions=el('div','modal-actions');actions.append(button('了解了',()=>$('modal').close(),'button primary'));body.append(actions);
}
function showHelp(){
  const body=modal('从资料到答案，只需三步');for(const [i,title,description] of [[1,'创建主题知识库','按课程、项目或兴趣建立知识库，将相关资料放在一起。'],[2,'上传并检查资料','支持 PDF、TXT 和 Markdown（.md）。点击文档名称可查看提取出的文本片段；扫描 PDF 需先转为可复制的文字。'],[3,'提问，回到原文','用具体的问题查找资料。点击回答中的引用编号，核对原文和位置；历史对话会自动保存。']]){const row=el('div','help-step');const text=el('div');text.append(el('strong','',title),el('p','',description));row.append(el('span','',i),text);body.append(row);}body.append(el('div','modal-note',state.health?.mode==='llm'?'当前已启用 AI 问答。模型会结合检索到的片段作答。':'当前为「本地原文检索」模式，适合查找资料和定位出处。接入模型后可使用 AI 问答。'));const actions=el('div','modal-actions');actions.append(button('开始使用',()=>$('modal').close(),'button primary'));body.append(actions);
}
function showUser(){
  const body=modal('个人账户');const row=el('div','info-row');row.append(el('span','','当前账户'),el('strong','',state.user.username));body.append(row,el('div','modal-note','资料与对话按账户隔离。退出登录后，下次使用同一账户即可继续。'));const actions=el('div','modal-actions');const logout=button('退出登录',async()=>{logout.disabled=true;try{await api('/api/auth/logout',{method:'POST',body:{}});showAuth();toast('已退出登录。');}catch(error){toast(error.message,'error');}finally{logout.disabled=false;}},'button');actions.append(logout,button('返回',()=>$('modal').close(),'button primary'));body.append(actions);
}
function renderSuggestions(){
  const suggestions=[['search','查找具体信息','从资料中定位你需要的答案','青竹-27 的里程碑日期是什么？'],['list','梳理关键要点','沿着原文，找到重要内容','需求工程的五步是什么？'],['link','连接相关知识','从不同资料中寻找关联','知微默认检索基线使用什么？']];
  const list=$('suggestion-grid');for(const [image,title,description,question] of suggestions){const card=button('',()=>{if(!state.libraryId){showCreateLibrary();return;}$('question').value=question;resizeComposer();$('question').focus();},'suggestion-card');const mark=icon(image);mark.className='suggestion-icon';const arrow=icon('arrow-up-right');arrow.className='suggestion-arrow';card.append(mark,el('strong','',title),el('p','',description),arrow);list.append(card);}
}
function bindEvents(){
  document.querySelectorAll('[data-icon]').forEach(node=>node.append(icon(node.dataset.icon)));
  $('auth-form').addEventListener('submit',authenticate);$('auth-toggle').addEventListener('click',()=>{state.register=!state.register;updateAuthForm();});$('toggle-password').addEventListener('click',()=>{const input=$('auth-password');input.type=input.type==='password'?'text':'password';$('toggle-password').setAttribute('aria-label',input.type==='password'?'显示密码':'隐藏密码');});
  $('add-library').addEventListener('click',showCreateLibrary);$('new-chat').addEventListener('click',newConversation);$('clear-conversation').addEventListener('click',newConversation);$('library-settings').addEventListener('click',showLibrarySettings);$('question-form').addEventListener('submit',sendQuestion);$('question').addEventListener('input',resizeComposer);$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send-button').disabled)$('question-form').requestSubmit();}});
  $('upload-zone').addEventListener('click',()=>$('file-input').click());$('file-input').addEventListener('change',event=>uploadFiles(event.target.files));const zone=$('upload-zone');['dragenter','dragover'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();if(!state.uploading)zone.classList.add('dragover');}));['dragleave','drop'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();zone.classList.remove('dragover');}));zone.addEventListener('drop',event=>uploadFiles(event.dataTransfer.files));
  window.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault();});window.addEventListener('drop',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault();});
  $('import-samples').addEventListener('click',importSamples);$('documents-tab').addEventListener('click',()=>setPanelTab('documents'));$('citations-tab').addEventListener('click',()=>setPanelTab('citations'));$('mode-button').addEventListener('click',showMode);$('help-button').addEventListener('click',showHelp);$('user-button').addEventListener('click',showUser);
  for(const tab of [$('documents-tab'),$('citations-tab')])tab.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();const target=tab.id==='documents-tab'?'citations':'documents';setPanelTab(target);$(`${target}-tab`).focus();}});
  $('modal-close').addEventListener('click',()=>$('modal').close());$('preview-close').addEventListener('click',()=>{$('preview-modal').close();state.previewRequest++;});for(const dialog of [$('modal'),$('preview-modal')])dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  $('menu-button').addEventListener('click',()=>{$('app').classList.toggle('sidebar-open');$('sidebar-shade').hidden=!$('app').classList.contains('sidebar-open');});$('sidebar-shade').addEventListener('click',closeMobilePanels);$('toggle-sources').addEventListener('click',()=>{if(window.innerWidth<=1000){$('app').classList.toggle('sources-open');$('sidebar-shade').hidden=!$('app').classList.contains('sources-open');}else $('app').classList.toggle('sources-hidden');});
  window.addEventListener('resize',()=>{if(window.innerWidth>1000)closeMobilePanels();});document.addEventListener('keydown',event=>{if(event.key==='Escape')closeMobilePanels();if(event.key==='n'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&state.user&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)&&!$('modal').open&&!$('preview-modal').open)newConversation();});
}
async function bootstrap(){
  bindEvents();renderSuggestions();const results=await Promise.allSettled([api('/api/auth/me'),api('/api/health')]);
  if(results[1].status==='fulfilled'){state.health=results[1].value;$('mode-label').textContent=modeText(state.health.mode);$('storage-label').textContent=state.health.mode==='llm'?'资料在本机 · 模型已连接':'资料在本机 · 原文检索';}else{$('mode-label').textContent='服务连接异常';}
  if(results[0].status==='fulfilled'&&results[0].value.user){state.user=results[0].value.user;await loadWorkspace();}else{showAuth();if(results[0].status==='rejected'&&results[0].reason.status!==401)$('auth-error').textContent=results[0].reason.message;}
}
bootstrap();
