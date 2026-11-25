const token=new URLSearchParams(location.hash.slice(1)).get('token'),$=id=>document.getElementById(id);
let state,stopped=false,busy=false,chain=Promise.resolve(),pointerDown=false;
async function api(path,data){const r=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});if(!r.ok){let d=await r.json();throw Error(d.detail||'Connection failed');}return r.json();}
function error(e){$('status').textContent=e.message;}
function command(data){chain=chain.then(()=>api('live/action',data)).catch(error);return chain;}
async function refresh(){if(stopped||busy)return;busy=true;try{await chain;const f=await api('live/frame');$('screen').src='data:image/jpeg;base64,'+f.image;$('url').textContent=f.url;$('status').textContent='Connected · Website interactions are being recorded';}catch(e){error(e);}finally{busy=false;}}
function point(e){const r=$('screen').getBoundingClientRect();return {x:(e.clientX-r.left)*1280/r.width,y:(e.clientY-r.top)*800/r.height};}
$('screen').onpointerdown=e=>{e.preventDefault();$('screen').focus();pointerDown=true;$('screen').setPointerCapture(e.pointerId);command({kind:'down',...point(e)});};
$('screen').onpointerup=e=>{if(pointerDown){pointerDown=false;command({kind:'up',...point(e)});}};
let movedAt=0;$('screen').onpointermove=e=>{if(pointerDown&&Date.now()-movedAt>80){movedAt=Date.now();command({kind:'move',...point(e)});}};
$('screen').onwheel=e=>{e.preventDefault();command({kind:'scroll',dx:e.deltaX,dy:e.deltaY});};
$('screen').onkeydown=e=>{if(['Control','Alt','Shift','Meta'].includes(e.key))return;e.preventDefault();let key=e.key===' '?'Space':e.key;const mods=[];if(e.ctrlKey)mods.push('Control');if(e.metaKey)mods.push('Meta');if(e.altKey)mods.push('Alt');if(e.shiftKey&&key.length>1)mods.push('Shift');command({kind:'key',key:[...mods,key].join('+')});};
$('screen').onpaste=e=>{e.preventDefault();command({kind:'text',text:e.clipboardData.getData('text/plain')});};
for(const k of ['back','reload'])$(k).onclick=()=>command({kind:k});
document.querySelectorAll('[data-site]').forEach(b=>b.onclick=()=>command({kind:'site',site:b.dataset.site}));
$('copy').onclick=async()=>{try{await chain;const r=await api('live/action',{kind:'selection'});$('notes').value+=($('notes').value?'\n':'')+r.selection;saveNotes();}catch(e){error(e);}};
$('paste').onclick=()=>command({kind:'text',text:$('notes').value});
const key='wa101-live-notes-'+token;
function saveNotes(){sessionStorage.setItem(key,$('notes').value);}
$('notes').oninput=saveNotes;$('notes').value=sessionStorage.getItem(key)||'';
async function finish(unable=false){
 $('finish').disabled=true;$('unable').disabled=true;
 try{await chain;const action=unable?'unable':state.phase==='shopping'?'gather':'live_finish';
 await flushControls();
 await api(action==='live_finish'?'live/finish':'action',action==='live_finish'?{}:{action,payload:{titles:$('notes').value}});
 state=await api('state');update();}catch(e){error(e);}finally{if($('finish'))$('finish').disabled=false;if($('unable'))$('unable').disabled=false;}
}
$('finish').onclick=()=>finish();$('unable').onclick=()=>finish(true);
function update(){
 $('assignment').textContent=state.intent+(state.config.forum_path==='\/f\/gaming'?' On this WebArena server, the games community is labelled /f/gaming.':'');
 if(state.phase==='reddit'){$('finish').textContent='I have posted on Reddit · Finish task 2';}
 if(!['shopping','reddit'].includes(state.phase)){stopped=true;document.querySelector('main').innerHTML='<div class="card"><h2>Website tasks finished</h2><p>Return to the study page and select Continue / refresh progress to answer the questionnaire.</p></div>';}
}
(async()=>{try{state=await api('state');update();await refresh();setInterval(refresh,700);}catch(e){error(e);}})();

// Record the participant's local controls and attention independently of remote DOM events.
const eventKey='wa101-live-events-'+token, controlPage=crypto.randomUUID();
let controlEvents=JSON.parse(localStorage.getItem(eventKey)||'[]'),controlSeq=0,lastControl=performance.now(),lastControlClick=null,controlFlush=null;
function controlEvent(kind,e){
 if(stopped||!state||!['shopping','reddit'].includes(state.phase))return;
 const now=performance.now(),t=e?.target;
 controlEvents.push({id:crypto.randomUUID(),task:state.phase==='shopping'?1:2,site:state.phase==='shopping'?'shopping':'reddit',kind:'control_'+kind,page_id:controlPage,seq:++controlSeq,client_at:Date.now(),elapsed_ms:now,since_previous_event_ms:now-lastControl,since_previous_click_ms:kind==='click'&&lastControlClick!==null?now-lastControlClick:null,payload:{id:t?.id,value:t?.id==='notes'?t.value:undefined,x:e?.clientX,y:e?.clientY,visibility:document.visibilityState,focused:document.hasFocus()}});
 lastControl=now;if(kind==='click')lastControlClick=now;
 localStorage.setItem(eventKey,JSON.stringify(controlEvents));
}
async function flushControls(){
 if(controlFlush){await controlFlush;if(controlEvents.length)return flushControls();return;}
 controlFlush=(async()=>{while(controlEvents.length){const batch=controlEvents.slice(0,200);await api('events',{events:batch});const ids=new Set(batch.map(e=>e.id));controlEvents=controlEvents.filter(e=>!ids.has(e.id));localStorage.setItem(eventKey,JSON.stringify(controlEvents));}})();
 try{await controlFlush;}finally{controlFlush=null;}
}
for(const kind of ['click','input','change','focusin','focusout','visibilitychange'])document.addEventListener(kind,e=>controlEvent(kind,e),true);
setInterval(()=>{controlEvent('heartbeat');flushControls().catch(error);},5000);
window.addEventListener('pagehide',()=>{controlEvent('pagehide');fetch('/api/events',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({events:controlEvents.slice(0,40)}),keepalive:true}).catch(()=>{});});
