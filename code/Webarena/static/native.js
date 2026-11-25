/* Runs only in native study tabs with an explicitly bootstrapped, consented session. */
(async()=>{
 if(location.pathname.startsWith('/__study__/'))return;
 if(window.__wa101Native)return;
 window.__wa101Native=true;
 let session;try{session=JSON.parse(sessionStorage.getItem('wa101-native')||'null');}catch(e){}
 if(!session)return;
 const uuid=()=>Date.now().toString(36)+'-'+Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16)).join('-');
 const pageId=uuid(), key='wa101-native-queue';
 let queue=[];try{queue=JSON.parse(sessionStorage.getItem(key)||'[]');}catch(e){}
 let phase=null,seq=0,last=performance.now(),lastClick=null,flushing=null,stopped=false;
 const banner=document.createElement('div');banner.id='wa-study-banner';
 banner.style.cssText='position:fixed;bottom:12px;right:12px;z-index:2147483647;background:#102a43;color:white;padding:12px 18px;border-radius:10px;box-shadow:0 3px 12px #0004;font:14px/1.5 system-ui;max-width:340px';
 const label=document.createElement('span');label.textContent='Connecting study recording…';banner.append(label);
 const link=document.createElement('a');link.textContent='Return to study ↗';link.style.cssText='display:block;color:#a7ddff;margin-top:5px';
 if(/^https?:\/\//.test(session.returnUrl)){link.href=session.returnUrl;link.target='wa101-study';banner.append(link);}
 document.body.append(banner);
 const persist=()=>{try{sessionStorage.setItem(key,JSON.stringify(queue));}catch(e){label.textContent='Recording storage full — return to your study tab.';}};
 async function api(path,data){const r=await fetch('/__study__/'+path,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+session.token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});const result=await r.json();if(!r.ok)throw Error(result.detail||'Recording disconnected');return result;}
 function setPhase(value){phase=value;if(!['shopping','reddit'].includes(phase)){stopped=true;label.textContent='Task recording finished. Return to the study.';sessionStorage.removeItem('wa101-native');}}
 function log(kind,payload={}){
  if(stopped||!phase)return;
  const now=performance.now();queue.push({id:uuid(),task:phase==='shopping'?1:2,site:session.site,kind,page_id:pageId,seq:++seq,client_at:Date.now(),elapsed_ms:now,since_previous_event_ms:now-last,since_previous_click_ms:kind==='click'&&lastClick!==null?now-lastClick:null,payload:{...payload,url:location.href,title:payload.title||document.title,scroll:{x:scrollX,y:scrollY},viewport:{width:innerWidth,height:innerHeight},visibility:document.visibilityState}});
  last=now;if(kind==='click')lastClick=now;persist();
 }
 async function flush(){
  if(flushing)return flushing;
  flushing=(async()=>{while(queue.length){const batch=[];let bytes=0;for(const event of queue){const size=new TextEncoder().encode(JSON.stringify(event)).length;if(batch.length>=100||(batch.length&&bytes+size>800000))break;batch.push(event);bytes+=size;}const result=await api('events',{events:batch});const ids=new Set(batch.map(e=>e.id));queue=queue.filter(e=>!ids.has(e.id));persist();setPhase(result.phase);}if(!stopped)label.textContent='Study recording connected · Use this website normally';})();
  try{await flushing;}catch(e){label.textContent='Recording reconnecting… Your actions are queued.';throw e;}finally{flushing=null;}
 }
 try{setPhase((await api('state')).phase);}catch(e){label.textContent='Recording unavailable. Return to the study and reopen this tab.';return;}
 if(stopped)return;
 for(const kind of ['click','dblclick','pointerdown','pointerup','input','change','keydown','keyup','focusin','focusout','submit','copy','cut','paste'])document.addEventListener(kind,e=>{
  if(banner.contains(e.target))return;
  const t=e.target,secret=t?.type==='password';
  const payload={tag:t?.tagName,id:t?.id,name:t?.name,type:t?.type,text:t?.textContent?.trim().slice(0,200),value:secret?'[redacted]':t?.value,x:e.clientX,y:e.clientY,key:secret?'[redacted]':e.key,ctrl:e.ctrlKey,shift:e.shiftKey,alt:e.altKey,meta:e.metaKey};
  payload.href=t?.closest?.('a')?.href;payload.form_action=t?.form?.action;payload.selector=t?.id?'#'+t.id:undefined;
  if(kind==='paste'&&!secret)payload.pasted_text=e.clipboardData?.getData('text/plain');
  if(kind==='copy'||kind==='cut')payload.selection=getSelection().toString();
  log(kind,payload);
 },true);

 // Preserve composer contents independently of individual input events or navigation.
 let formTimer=null,lastPost='';
 function recordRedditForm(reason,form){
  if(session.site!=='reddit'||stopped)return;
  const fields=[];let chars=0;
  (form||document).querySelectorAll('input,textarea,select,[contenteditable="true"]').forEach(el=>{
   if(['password','hidden','file','submit','button'].includes(el.type)||(!el.name&&!el.id&&!el.isContentEditable))return;
   const value=String(el.value??el.innerText??'').slice(0,20000);
   if(chars+value.length>60000)return;chars+=value.length;
   fields.push({name:el.name||'',id:el.id||'',type:el.type||el.tagName,value});
  });
  if(fields.length){log('reddit_form_state',{reason,fields});flush().catch(()=>{});}
 }
 document.addEventListener('submit',e=>recordRedditForm('submit',e.target),true);
 document.addEventListener('input',()=>{
  if(session.site!=='reddit')return;
  clearTimeout(formTimer);formTimer=setTimeout(()=>recordRedditForm('editing'),700);
 },true);
 document.addEventListener('click',e=>{
  const button=e.target.closest?.('button,input[type="submit"]');
  if(button&&button.form)recordRedditForm('submit_control_click',button.form);
 },true);
 window.addEventListener('pagehide',()=>recordRedditForm('leaving_page'),true);
 function recordPublishedPost(){
  if(session.site!=='reddit'||!/^\/f\/[^/]+\/\d+(?:\/|$)/.test(location.pathname)||stopped)return;
  const title=document.querySelector('.submission__title')?.textContent?.trim();
  const body=document.querySelector('.submission__body')?.textContent?.trim();
  if(!title||!body)return;
  const signature=JSON.stringify([location.pathname,title,body]);if(signature===lastPost)return;lastPost=signature;
  log('native_post_seen',{title,body,evidence:'rendered_post'});
  flush().catch(()=>{});
 }
 setInterval(recordPublishedPost,2000);
 recordPublishedPost();
 // Durable, self-contained visual archive. IndexedDB survives tab navigation.
 let archiveBusy=false,archiveLast=0,archiveDirty=true;
 const archiveDB=new Promise((resolve,reject)=>{const request=indexedDB.open('wa101-visual-archive',1);request.onupgradeneeded=()=>request.result.createObjectStore('pending',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
 async function archiveStore(mode,action){const database=await archiveDB;return new Promise((resolve,reject)=>{const tx=database.transaction('pending',mode);const req=action(tx.objectStore('pending'));tx.oncomplete=()=>resolve(req?.result);tx.onerror=()=>reject(tx.error);});}
 async function sendArchives(){
  if(archiveBusy)return;archiveBusy=true;
  try{
   for(const item of await archiveStore('readonly',s=>s.getAll())){
    if(item.token!==session.token)continue;
    const total=Math.ceil(item.html.length/150000);
    for(let part=0;part<total;part++)await api('archive',{id:item.id,part,total,content:item.html.slice(part*150000,(part+1)*150000),meta:item.meta});
    await archiveStore('readwrite',s=>s.delete(item.id));
    log('visual_archive_saved',{archive_id:item.id});
   }
  }catch(e){log('visual_archive_error',{message:String(e).slice(0,200)});}finally{archiveBusy=false;}
 }
 function captureVisual(reason){
  if(stopped||!phase)return;
  const root=document.documentElement.cloneNode(true),missing=[];
  const originals=[...document.querySelectorAll('input,textarea,select')];
  root.querySelectorAll('input,textarea,select').forEach((el,i)=>{const original=originals[i];if(el.type==='password'){el.value='';el.setAttribute('value','[redacted]');}else if(el.tagName==='TEXTAREA')el.textContent=original.value;else if(el.tagName==='SELECT'){[...el.options].forEach((o,j)=>{if(original.options[j]?.selected)o.setAttribute('selected','');else o.removeAttribute('selected');});}else{el.setAttribute('value',original.value||'');if(original.checked)el.setAttribute('checked','');else el.removeAttribute('checked');}});
  let css='';for(const sheet of document.styleSheets){try{css+=[...sheet.cssRules].map(r=>r.cssText).join('\n');}catch(e){missing.push('stylesheet:'+sheet.href);}}
  css=css.replace(/url\(([^)]+)\)/g,(match,url)=>{if(/^['"]?data:/i.test(url))return match;missing.push('css-resource:'+url.slice(0,200));return 'url(about:blank)';});
  const originalsImages=[...document.images];root.querySelectorAll('img').forEach((img,i)=>{const source=originalsImages[i];try{const canvas=document.createElement('canvas');canvas.width=source.naturalWidth;canvas.height=source.naturalHeight;if(!canvas.width||!canvas.height)throw Error('unloaded');canvas.getContext('2d').drawImage(source,0,0);img.setAttribute('src',canvas.toDataURL('image/png'));}catch(e){missing.push('image:'+source.src);img.removeAttribute('src');}img.removeAttribute('srcset');});
  root.querySelectorAll('script,noscript,link,meta,base,iframe,object,embed,input[type=hidden],#wa-study-banner').forEach(n=>n.remove());
  root.querySelectorAll('*').forEach(n=>{for(const a of [...n.attributes])if(/^on/i.test(a.name)||['href','action','formaction','nonce'].includes(a.name))n.removeAttribute(a.name);});
  const style=document.createElement('style');style.textContent=css;root.querySelector('head')?.append(style);
  const html='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; form-action \'none\'; base-uri \'none\'">'+root.outerHTML;
  if(html.length>6000000){log('visual_archive_error',{reason,message:'Visual archive exceeds 6 million characters; text events retained.'});return;}
  const item={id:uuid(),token:session.token,html,meta:{page_id:pageId,task:phase==='shopping'?1:2,site:session.site,client_at:Date.now(),reason,url:location.href,viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY},missing_resources:missing,format:'self-contained-dom-v1',screenshot_kind:'rendered_from_archived_dom'}};
  archiveLast=Date.now();archiveDirty=false;
  archiveStore('readwrite',s=>s.put(item)).then(sendArchives).catch(e=>log('visual_archive_error',{message:String(e)}));
 }
 for(const kind of ['click','input','change','scroll'])document.addEventListener(kind,()=>{archiveDirty=true;},true);
 document.addEventListener('submit',()=>captureVisual('submit'),true);
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&archiveDirty)captureVisual('hidden');});
 setInterval(()=>{if(archiveDirty&&Date.now()-archiveLast>8000)captureVisual('interaction');sendArchives();},3000);
 setTimeout(()=>captureVisual('initial'),1000);

 let move=false,scroll=false;
 document.addEventListener('pointermove',e=>{if(!move){move=true;setTimeout(()=>{log('pointermove',{x:e.clientX,y:e.clientY});move=false},100)}});
 document.addEventListener('scroll',()=>{if(!scroll){scroll=true;setTimeout(()=>{log('scroll');scroll=false},200)}},true);
 for(const kind of ['focus','blur','resize','online','offline'])window.addEventListener(kind,()=>log(kind));
 document.addEventListener('visibilitychange',()=>{log('visibilitychange');flush().catch(()=>{});});
 window.addEventListener('pagehide',()=>{log('pagehide');persist();const batch=queue.slice(0,20);if(JSON.stringify(batch).length<60000)fetch('/__study__/events',{method:'POST',headers:{Authorization:'Bearer '+session.token,'Content-Type':'application/json'},body:JSON.stringify({events:batch}),keepalive:true}).catch(()=>{});});

 // Sanitized structural snapshots, not video. Capture only consented study pages.
 let snapshotTimer=null,lastSnapshot='';
 function domSnapshot(reason){
  if(stopped||!phase)return;
  const root=document.documentElement.cloneNode(true);
  root.querySelectorAll('script,iframe,object,embed,link,meta,base,#wa-study-banner,input[type="hidden"]').forEach(n=>n.remove());
  const original=document.querySelectorAll('input,textarea,select');
  const cloned=root.querySelectorAll('input,textarea,select');
  // Match controls by id/name instead of index because hidden fields were removed.
  cloned.forEach(n=>{
   const source=Array.from(original).find(o=>(n.id&&o.id===n.id)||(!n.id&&n.name&&o.name===n.name));
   if(n.type==='password'){n.setAttribute('value','[redacted]');return;}
   if(source){if(n.tagName==='TEXTAREA')n.textContent=source.value;else n.setAttribute('value',source.value||'');}
  });
  root.querySelectorAll('*').forEach(n=>{
   for(const a of [...n.attributes])if(/^on/i.test(a.name)||['src','srcset','href','action','formaction','nonce','integrity'].includes(a.name))n.removeAttribute(a.name);
  });
  let html=root.outerHTML;
  if(new TextEncoder().encode(html).length>650000)html='';
  if(html===lastSnapshot&&reason==='mutation')return;
  lastSnapshot=html;
  const frames=[];
  document.querySelectorAll('iframe').forEach((f,i)=>{try{if(f.contentDocument)frames.push({id:f.id||f.name||String(i),text:f.contentDocument.body?.innerText.slice(0,20000)});}catch(e){}});
  log('dom_snapshot',{reason,html:html.length<=220000?html:null,html_omitted:!html||html.length>220000,original_html_chars:html.length,visible_text:document.body.innerText.slice(0,30000),frames,format:'sanitized-dom-v1'});
 }
 function scheduleSnapshot(reason){if(snapshotTimer)return;snapshotTimer=setTimeout(()=>{snapshotTimer=null;domSnapshot(reason);},1500);}
 const observer=new MutationObserver(changes=>{if(changes.some(c=>!banner.contains(c.target)))scheduleSnapshot('mutation');});
 observer.observe(document.body,{childList:true,subtree:true,attributes:true,characterData:true});
 for(const event of ['click','change','input'])document.addEventListener(event,()=>scheduleSnapshot(event),true);
 domSnapshot('initial');

 log('page_view',{user_agent:navigator.userAgent,language:navigator.language});
 log('page_snapshot',{visible_text:document.body.innerText.slice(0,50000)});
 flush().catch(()=>{});
 const timer=setInterval(async()=>{if(stopped){clearInterval(timer);return;}log('heartbeat',{focused:document.hasFocus()});try{await flush();setPhase((await api('state')).phase);}catch(e){}},3000);
})();
