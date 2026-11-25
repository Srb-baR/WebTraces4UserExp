"""Same-origin APIs for instrumented native WebArena tabs."""
import json
import os
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import db
from settings import ROOT

router=APIRouter(prefix='/native')

def token_for(request):
    token=request.headers.get('Authorization','').removeprefix('Bearer ')
    try:db.session(token)
    except ValueError:raise HTTPException(401,'Open the website using the button in your study tab.')
    return token

@router.get('/tracker.js')
def tracker():return FileResponse(ROOT/'static/native.js',media_type='application/javascript')

@router.get('/boot.js')
def boot():return FileResponse(ROOT/'static/native_boot.js',media_type='application/javascript')

@router.get('/start/{site}')
def start(site:str):
    if site not in ('shopping','reddit'):
        raise HTTPException(404)

    return HTMLResponse(f'''<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Opening study website</title>
</head>
<body style="font:18px system-ui;padding:40px">
    <h1>Opening your study website…</h1>
    <p id="status">Connecting your study session.</p>
    <script src="/{site}/__study__/boot.js"></script>
</body>
</html>''')

@router.post('/bootstrap/{site}')
def bootstrap(site:str,request:Request):
    token=token_for(request)
    s=db.session(token)
    if json.loads(s['config_json'])['mode'] != 'live':raise HTTPException(409,'This session does not use the live websites.')
    if s['phase'] not in ('shopping','reddit'):raise HTTPException(409,'Start your task in the study tab first.')
    if site not in ('shopping','reddit'):raise HTTPException(404)
    cfg=json.loads(s['config_json'])
    response=JSONResponse({'path':'/' if site=='shopping' else cfg.get('forum_path','/f/gaming')})
    if site=='reddit':
        path=Path(os.environ.get('WEBARENA_STORAGE_STATE',str(ROOT/'data/auth.json')))
        if not path.exists():raise HTTPException(503,'Research account login needs to be refreshed.')
        cookies=json.loads(path.read_text()).get('cookies',[])
        # Copy only the benchmark Reddit authentication cookies, never unrelated cookies.
        for cookie in cookies:
            if cookie['name'] in ('PHPSESSID','REMEMBERME','_cookie_check'):
                response.set_cookie(cookie['name'],cookie['value'],path=cookie.get('path','/'),httponly=True,samesite='lax')
    with db.connect() as c:db.system_event(c,s['id'],'native_tab_opened',1 if s['phase']=='shopping' else 2,{'site':site})
    return response

@router.get('/state')
def state(request:Request):
    s=db.session(token_for(request))
    cfg=json.loads(s['config_json'])
    return {'phase':s['phase'],'intent':s['intent'],'product':cfg['product'],'rating':cfg['rating']}

@router.post('/events')
async def events(request:Request,background_tasks:__import__("fastapi").BackgroundTasks):
    token=token_for(request)
    raw=await request.body()
    if len(raw)>1_000_000:raise HTTPException(413,'Event batch too large')
    try:
        data=json.loads(raw)
        db.save_events(token,data['events'])
        import archive
        for event in data['events']:
            payload=event.get('payload',{})
            if event.get('site')!='study' or event.get('kind')!='study_screen' or not payload.get('html'):continue
            html=payload['html'];total=(len(html)+149999)//150000
            meta={'site':'study','task':event['task'],'page_id':event.get('page_id'),'client_at':event.get('client_at'),'url':payload.get('url'),'reason':payload.get('reason'),'viewport':payload.get('viewport'),'format':'study-dom-v1','screenshot_kind':'rendered_from_archived_dom'}
            try:
                for part in range(total):
                    complete=archive.receive(token,{'id':event['id'],'part':part,'total':total,'content':html[part*150000:(part+1)*150000],'meta':meta})
                if complete:background_tasks.add_task(archive.render,event['id'])
            except ValueError:pass # The event remains saved if its task just ended.

    except (ValueError,TypeError,KeyError):raise HTTPException(400,'Invalid event batch')
    return {'saved':True,'phase':db.session(token)['phase']}

def latest_post(token):
    s=db.session(token)
    with db.connect() as c:
        rows=c.execute("SELECT payload_json FROM events WHERE session_id=? AND kind='native_post_seen' ORDER BY received_at DESC LIMIT 20",(s['id'],)).fetchall()
    for row in rows:
        p=json.loads(row['payload_json'])
        if re.search(r'/f/[^/]+/\d+',p.get('url','')) and p.get('title') and p.get('body') and p.get('title')=='real user feedback on '+json.loads(s['config_json'])['product'] and p.get('url','').split('/f/',1)[-1].split('/',1)[0]==json.loads(s['config_json']).get('forum_path','/f/gaming').split('/')[-1]:
            return p
    return None

@router.post('/archive')
async def archive_chunk(request:Request,background_tasks:__import__('fastapi').BackgroundTasks):
    import archive
    raw=await request.body()
    if len(raw)>900000:raise HTTPException(413,'Archive chunk too large')
    try:
        payload=json.loads(raw)
        completed=archive.receive(token_for(request),payload)
    except (ValueError,KeyError,TypeError):raise HTTPException(400,'Invalid visual archive')
    if completed:background_tasks.add_task(archive.render,payload['id'])
    return {'saved':True,'complete':completed}
