"""Per-participant real browser. One uvicorn worker; records DOM events and traces.
Only the two configured WebArena origins can be visited. Each context has isolated
cookies; account/data isolation still requires distinct accounts or server instances.
"""
import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
import db
from settings import ROOT

BROWSERS = {}
MANAGER = None
LAUNCH_LOCK = asyncio.Lock()

INSTRUMENT = r'''(() => {
const uuid=()=>typeof crypto.randomUUID==='function'?crypto.randomUUID():Date.now().toString(36)+'-'+Array.from(crypto.getRandomValues(new Uint32Array(4)),v=>v.toString(16)).join('-');
let seq=0,last=performance.now(),click=null;const page=uuid();
function send(kind,e){const now=performance.now(),t=e?.target;
const secret=t?.type==='password';
const payload={url:location.href,title:document.title,tag:t?.tagName,id:t?.id,
 name:t?.name,role:t?.getAttribute?.('role'),text:t?.textContent?.slice(0,250),
 value:secret?'[redacted]':t?.value,x:e?.clientX,y:e?.clientY,
 key:secret?'[redacted]':e?.key,scroll_x:scrollX,scroll_y:scrollY,visibility:document.visibilityState};
if(kind==='paste'&&!secret)payload.pasted_text=e.clipboardData?.getData('text/plain');
if(kind==='copy')payload.selection=getSelection().toString();
window.studyEvent({id:uuid(),kind,page_id:page,seq:++seq,client_at:Date.now(),
 elapsed_ms:now,since_previous_event_ms:now-last,since_previous_click_ms:kind==='click'&&click!==null?now-click:null,payload}).catch(()=>{});
last=now;if(kind==='click')click=now;}
for(const k of ['click','dblclick','pointerdown','pointerup','input','change','keydown','keyup','focusin','focusout','submit','copy','cut','paste','visibilitychange'])document.addEventListener(k,e=>send(k,e),true);
let pending=false;document.addEventListener('scroll',e=>{if(!pending){pending=true;setTimeout(()=>{send('scroll',e);pending=false},200)}},true);
window.addEventListener('DOMContentLoaded',()=>send('page_view'));
})();'''

def origin(url):
    p=urlsplit(url)
    return f"{p.scheme}://{p.netloc}"

class StudyBrowser:
    def __init__(self,token,cfg):
        self.token=token
        self.cfg=cfg
        self.lock=asyncio.Lock()
        self.context=None
        self.browser=None
        self.page=None
        self.used=time.monotonic()
        self.last_click=None
        self.directory=ROOT/'data'/'recordings'/db.session(token)['id']
        self.directory.mkdir(parents=True,exist_ok=True)
        self.allowed={origin(cfg['shopping_url']),origin(cfg['reddit_url'])}

    def event(self,kind,payload,raw=None):
        s=db.session(self.token)
        if s['phase'] not in ('shopping','reddit'):
            return
        n=1 if s['phase']=='shopping' else 2
        e=raw or {'id':str(uuid.uuid4()),'kind':kind,'client_at':time.time()*1000,'payload':payload}
        e.update(task=n,site='reddit' if origin(e.get('payload',{}).get('url',''))==origin(self.cfg['reddit_url']) else 'shopping')
        db.save_events(self.token,[e])

    async def start(self):
        global MANAGER
        if MANAGER is None:
            MANAGER=await async_playwright().start()
        self.browser=await MANAGER.chromium.launch(headless=True)
        opts={'viewport':{'width':1280,'height':800},'record_video_dir':str(self.directory)}
        storage=os.environ.get('WEBARENA_STORAGE_STATE')
        if storage:
            opts['storage_state']=storage
        self.context=await self.browser.new_context(**opts)
        await self.context.tracing.start(screenshots=True,snapshots=True,sources=False)
        async def receive(source,e):
            self.event(e['kind'],e.get('payload',{}),e)
        await self.context.expose_binding('studyEvent',receive)
        await self.context.add_init_script(INSTRUMENT)
        async def restrict(route):
            url=route.request.url
            if url.startswith(('data:','blob:')) or origin(url) in self.allowed:
                await route.continue_()
            else:
                self.event('blocked_request',{'url':url})
                await route.abort()
        await self.context.route('**/*',restrict)
        self.context.on('page',self.attach)
        self.page=await self.context.new_page()
        await self.page.goto(self.cfg['shopping_url'],wait_until='domcontentloaded',timeout=45000)

    def attach(self,page):
        self.page=page
        page.on('requestfailed',lambda r:self.event('request_failed',{'url':r.url,'method':r.method,'failure':r.failure}))
        page.on('response',lambda r:self.event('response',{'url':r.url,'status':r.status}))
        page.on('console',lambda msg:self.event('console',{'type':msg.type,'text':msg.text[:1000]}))
        page.on('dialog',lambda dialog:dialog.dismiss())

    async def frame(self):
        self.used=time.monotonic()
        image=await self.page.screenshot(type='jpeg',quality=75,timeout=15000)
        return {'image':base64.b64encode(image).decode(),'url':self.page.url,'title':await self.page.title()}

    async def action(self,data):
        kind=data.get('kind')
        self.used=time.monotonic()
        # Password values/keys are redacted by the in-page recorder. Do not duplicate
        # raw keyboard text in transport events.
        self.event('browser_command',{'kind':kind,'url':self.page.url})
        if kind in ('down','up','move'):
            x=max(0,min(1279,float(data['x'])))
            y=max(0,min(799,float(data['y'])))
            await self.page.mouse.move(x,y)
            if kind=='down':await self.page.mouse.down(button='left')
            if kind=='up':await self.page.mouse.up(button='left')
        elif kind=='key':
            key=data.get('key','')
            if len(key)>80:raise ValueError('Invalid key')
            await self.page.keyboard.press(key)
        elif kind=='text':
            await self.page.keyboard.insert_text(str(data.get('text',''))[:20000])
        elif kind=='scroll':
            await self.page.mouse.wheel(float(data.get('dx',0)),float(data.get('dy',0)))
        elif kind=='back':await self.page.go_back(wait_until='domcontentloaded')
        elif kind=='reload':await self.page.reload(wait_until='domcontentloaded')
        elif kind=='site':
            site=data.get('site')
            if site not in ('shopping','reddit'):raise ValueError('Invalid site')
            await self.page.goto(self.cfg[site+'_url'],wait_until='domcontentloaded')
        elif kind=='selection':
            text=await self.page.evaluate('getSelection().toString()')
            self.event('copy_selection',{'selection':text,'url':self.page.url})
            return {'selection':text}
        else:raise ValueError('Unknown browser action')
        return {'ok':True}

    async def evidence(self):
        path=self.directory/f'final-{int(time.time())}.png'
        await self.page.screenshot(path=str(path),full_page=True)
        title=self.page.locator('.submission__title').first
        body=self.page.locator('.submission__body').first
        return {'url':self.page.url,'screenshot':str(path),
                'title':await title.inner_text() if await title.count() else '',
                'body':await body.inner_text() if await body.count() else ''}

    async def close(self):
        if self.context:
            await self.context.tracing.stop(path=str(self.directory/f'trace-{int(time.time())}.zip'))
            await self.context.close()
            with db.connect() as c:
                sid=db.session(self.token,c)['id']
                for path in self.directory.iterdir():
                    c.execute('INSERT INTO artifacts(session_id,kind,path,created_at) VALUES (?,?,?,?)',
                              (sid,path.suffix.lstrip('.'),str(path),time.time()))
        if self.browser:
            await self.browser.close()

async def get_browser(token):
    s=db.session(token)
    cfg=json.loads(s['config_json'])
    if cfg['mode']!='live' or s['phase'] not in ('shopping','reddit'):
        raise ValueError('Live browser is only available during live tasks.')
    async with LAUNCH_LOCK:
        if token not in BROWSERS:
            if len(BROWSERS)>=int(os.environ.get('WEBARENA_MAX_BROWSERS','6')):
                raise ValueError('All study browsers are busy. Please try again shortly.')
            b=StudyBrowser(token,cfg)
            try:await b.start()
            except Exception:
                await b.close()
                raise
            BROWSERS[token]=b
    return BROWSERS[token]

async def cleanup():
    while True:
        await asyncio.sleep(30)
        for token,b in list(BROWSERS.items()):
            if db.session(token)['phase'] not in ('shopping','reddit') or time.monotonic()-b.used>1800:
                async with b.lock:
                    await b.close()
                    BROWSERS.pop(token,None)
