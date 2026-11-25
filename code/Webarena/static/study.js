/* Instrumentation is confined to this consented study workspace. */
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const storeKey = `wa101-events-${token}`;
const draftKey = `wa101-draft-${token}`;
const pageId = crypto.randomUUID();
let queue = [], draft = {}, state, task=1, site='shopping', seq=0, lastEvent=performance.now(), lastClick=null;
let active = true, flushing = null, page='catalog', productName='', query='', rating='', reviewPage=0;
try { queue=JSON.parse(localStorage.getItem(storeKey)||'[]'); draft=JSON.parse(localStorage.getItem(draftKey)||'{}'); } catch(e) {}
const $ = id=>document.getElementById(id);
const esc = value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function persist(){try{localStorage.setItem(storeKey,JSON.stringify(queue));localStorage.setItem(draftKey,JSON.stringify(draft));}catch(e){$('status').textContent='Browser storage unavailable. Keep this page open while saving.';}}
function log(kind,payload={}){
 if(!active)return;
 const now=performance.now();
 const event={id:crypto.randomUUID(),task,site,kind,page_id:pageId,seq:++seq,client_at:Date.now(),elapsed_ms:now,
 since_previous_event_ms:now-lastEvent,since_previous_click_ms:kind==='click'&&lastClick!==null?now-lastClick:null,
 payload:{...payload,page,visibility:document.visibilityState,viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY}}};
 lastEvent=now;if(kind==='click')lastClick=now;queue.push(event);persist();
}
async function api(path,data){
 const response=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
 if(!response.ok){let message='Unable to save';try{message=(await response.json()).detail;}catch(e){}throw Error(message);}
 return response.json();
}
async function flush(){
 if(flushing){await flushing;if(queue.length)return flush();return;}
 flushing=(async()=>{while(queue.length){const batch=queue.slice(0,200);await api('events',{events:batch});const ids=new Set(batch.map(e=>e.id));queue=queue.filter(e=>!ids.has(e.id));persist();}$('status').textContent='Study recording connected · All queued interactions saved';})();
 try{await flushing;}catch(e){$('status').textContent='Saving interrupted — interactions queued in this browser. Reconnecting…';throw e;}finally{flushing=null;}
}
function target(el){if(!(el instanceof Element))return {};return {tag:el.tagName,id:el.id,name:el.getAttribute('name'),type:el.getAttribute('type'),text:(el.textContent||'').trim().slice(0,250),value:'value'in el&&el.type!=='password'?el.value:undefined};}
for(const type of ['click','dblclick','pointerdown','pointerup','input','change','focusin','focusout','submit','copy','cut','paste','keydown','keyup']){
 document.addEventListener(type,e=>{
  const p={target:target(e.target)};
  if(type.startsWith('key')){p.key=e.key;p.code=e.code;p.ctrl=e.ctrlKey;p.meta=e.metaKey;p.shift=e.shiftKey;p.alt=e.altKey;p.repeat=e.repeat;}
  if('clientX'in e){p.x=e.clientX;p.y=e.clientY;p.button=e.button;}
  if(type==='copy'||type==='cut')p.selection=getSelection().toString();
  if(type==='paste')p.text=e.clipboardData?.getData('text/plain');
  if(type==='input'&&e.target.id){draft[e.target.id]=e.target.value;persist();}
  log(type,p);
 },true);
}
let scrollPending=false;
document.addEventListener('scroll',()=>{if(!scrollPending){scrollPending=true;setTimeout(()=>{log('scroll');scrollPending=false;},200);}},true);
let movePending=false;
document.addEventListener('pointermove',e=>{if(!movePending){movePending=true;setTimeout(()=>{log('pointermove',{x:e.clientX,y:e.clientY});movePending=false;},100);}});
for(const type of ['focus','blur','resize','online','offline'])window.addEventListener(type,()=>log(type));
document.addEventListener('visibilitychange',()=>{log('visibilitychange');flush().catch(()=>{});});
window.addEventListener('pagehide',()=>{log('pagehide');persist();fetch('/api/events',{method:'POST',headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({events:queue.slice(0,40)}),keepalive:true}).catch(()=>{});});
window.addEventListener('error',e=>log('javascript_error',{message:e.message}));
setInterval(()=>{if(active)log('heartbeat',{focused:document.hasFocus()});flush().catch(()=>{});},5000);
function taskBanner(){return `<div class="task"><b>Task ${task} of 2</b><br>${esc(state.intent)}</div>`;}
function footer(){return '<footer>Local research replica · Synthetic review data · Posts are visible only in your study session.</footer>';}
function render(){
 if(state.phase==='shopping'){task=1;site='shopping';renderShop();}
 else if(state.phase==='reddit'){task=2;site='reddit';renderReddit();}
 else{$('app').innerHTML='<div class="card"><h2>Website tasks finished</h2><p>Return to the study page and select <b>Continue / refresh progress</b> to answer the questionnaire.</p></div>';active=false;}
 if(active)log('page_view',{title:document.title});
}
function renderShop(){
 const products=state.config.products;
 let content='';
 if(page==='product'){
 const product=products.find(p=>p.name===productName);
 const reviews=product.reviews.filter(r=>!rating||r.rating===Number(rating));
 const shown=reviews.slice(reviewPage*2,reviewPage*2+2);
 content=`<button id="back" class="secondary">← All products</button><article class="card"><span class="pill">${esc(product.category)}</span><h2>${esc(product.name)}</h2><p class="price">${esc(product.price)}</p><h3>Customer reviews (${product.reviews.length})</h3><label for="rating">Filter by rating</label><select id="rating"><option value="">All ratings</option>${[5,4,3,2,1].map(n=>`<option value="${n}" ${rating==n?'selected':''}>${n} stars</option>`).join('')}</select>${shown.map(r=>`<section class="review"><span class="stars">${'★'.repeat(r.rating)}${'☆'.repeat(5-r.rating)} · ${r.rating}/5</span><h3>${esc(r.title)}</h3><p>${esc(r.body)}</p></section>`).join('')||'<p>No matching reviews.</p>'}<div class="row"><button id="prev" class="secondary" ${reviewPage===0?'disabled':''}>Previous reviews</button><span>Page ${reviewPage+1} of ${Math.max(1,Math.ceil(reviews.length/2))}</span><button id="next" ${((reviewPage+1)*2>=reviews.length)?'disabled':''}>Next reviews</button></div></article>`;
 }else{
 content=`<form id="searchForm" class="row"><label for="search">Search products</label><input id="search" value="${esc(query)}" placeholder="Product name"><button>Search</button></form><div class="grid">${products.filter(p=>p.name.toLowerCase().includes(query.toLowerCase())).map(p=>`<article class="card"><div class="product-art" aria-hidden="true">${p.category==='Controllers'?'🎮':'🎧'}</div><p class="hint">${esc(p.category)}</p><h2>${esc(p.name)}</h2><p class="price">${esc(p.price)}</p><button data-product="${esc(p.name)}">View product & reviews</button></article>`).join('')||'<p>No products found. Try another search.</p>'}</div>`;
 }
 $('app').innerHTML=`<header><h1>OneStopShop</h1><span class="pill">Customer reviews</span></header>${taskBanner()}${content}<section class="card"><h2>Your gathered review titles</h2><p>Enter the matching review titles, one per line. You can copy and paste from the reviews.</p><form id="gatherForm"><label for="titles">Review titles</label><textarea id="titles" required>${esc(draft.titles||'')}</textarea><p id="message" class="error" role="alert"></p><button id="gather">Finish task 1 · Go to Reddit</button></form></section><button id="unable" class="secondary">I cannot complete this task</button>${footer()}`;
 if($('searchForm'))$('searchForm').onsubmit=e=>{e.preventDefault();query=$('search').value;render();};
 document.querySelectorAll('[data-product]').forEach(b=>b.onclick=()=>{page='product';productName=b.dataset.product;rating='';reviewPage=0;render();});
 if($('back'))$('back').onclick=()=>{page='catalog';render();};
 if($('rating'))$('rating').onchange=()=>{rating=$('rating').value;reviewPage=0;render();};
 if($('prev'))$('prev').onclick=()=>{reviewPage--;render();};
 if($('next'))$('next').onclick=()=>{reviewPage++;render();};
 $('gatherForm').onsubmit=e=>{e.preventDefault();transition('gather',{titles:$('titles').value});};
 $('unable').onclick=()=>transition('unable',{reason:'participant_reported_unable'});
}
function renderReddit(){
 const gathered=state.submissions.find(s=>s.kind==='gather');
 $('app').innerHTML=`<header><h1 style="color:#bd3d13">reddit <small>· study edition</small></h1><span class="pill">Study participant</span></header>${taskBanner()}<div class="card"><h2>Create a post</h2><p>Post the review titles you gathered in the requested community.</p><details><summary>Your notes from OneStopShop</summary><pre style="white-space:pre-wrap">${esc(gathered?JSON.parse(gathered.payload_json).titles:'No review titles submitted.')}</pre></details><form id="postForm"><label for="subreddit">Community</label><select id="subreddit" required><option value="">Choose a community</option>${['games','gaming','technology'].map(s=>`<option value="${s}" ${draft.subreddit===s?'selected':''}>r/${s}</option>`).join('')}</select><label for="postTitle">Title</label><input id="postTitle" style="width:100%" maxlength="300" required value="${esc(draft.postTitle||'')}"><label for="postBody">Text · one review title per line</label><textarea id="postBody" required>${esc(draft.postBody||'')}</textarea><p id="message" class="error" role="alert"></p><button class="orange" id="post">Post to community · Finish task 2</button></form></div><button id="unable" class="secondary">I cannot complete this task</button>${footer()}`;
 $('postForm').onsubmit=e=>{e.preventDefault();transition('post',{subreddit:$('subreddit').value,title:$('postTitle').value,body:$('postBody').value});};
 $('unable').onclick=()=>transition('unable',{reason:'participant_reported_unable'});
}
async function transition(action,payload){
 document.querySelectorAll('button').forEach(b=>b.disabled=true);
 try{log('task_submit',{action});await flush();await api('action',{action,payload});state=await api('state');page=state.phase;lastClick=null;
 if(state.phase==='survey'&&action==='post'){
 active=false;
 $('app').innerHTML=`<header><h1>reddit · r/${esc(payload.subreddit)}</h1></header><article class="card"><p class="hint">Posted by study participant · just now</p><h2>${esc(payload.title)}</h2><p style="white-space:pre-wrap">${esc(payload.body)}</p></article><div class="task">Your post has been saved. Return to the study page and select <b>Continue / refresh progress</b> to answer the questionnaire.</div>${footer()}`;
 }else render();
 }catch(e){if($('message'))$('message').textContent=e.message;else $('status').textContent=e.message;document.querySelectorAll('button').forEach(b=>b.disabled=false);}
}
(async()=>{try{state=await api('state');active=['shopping','reddit'].includes(state.phase);task=state.phase==='reddit'?2:1;site=state.phase==='reddit'?'reddit':'shopping';log('session_open',{user_agent:navigator.userAgent,language:navigator.language,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,screen:{width:screen.width,height:screen.height}});await flush();render();}catch(e){$('status').textContent=e.message;}})();
