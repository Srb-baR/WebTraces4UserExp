"""Session-scoped visual evidence stored as compressed HTML and PNG in SQLite."""
import asyncio,hashlib,json,time,zlib
import db

def init():
 with db.connect() as c:c.executescript('''
 CREATE TABLE IF NOT EXISTS visual_archives(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,page_id TEXT,task INTEGER,site TEXT,client_at REAL,received_at REAL,reason TEXT,url TEXT,metadata_json TEXT,html_zlib BLOB NOT NULL,png BLOB,render_status TEXT NOT NULL DEFAULT 'pending');
 CREATE INDEX IF NOT EXISTS visual_archives_session ON visual_archives(session_id,received_at);
 CREATE TABLE IF NOT EXISTS visual_archive_chunks(session_id TEXT,archive_id TEXT,part INTEGER,total INTEGER,content TEXT,metadata_json TEXT,created_at REAL,PRIMARY KEY(session_id,archive_id,part));
 ''')
def receive(token,payload):
 s=db.session(token)
 if s['phase'] not in ('shopping','reddit'):raise ValueError('Recording is not active.')
 aid=payload['id'];part=payload['part'];total=payload['total'];content=payload['content'];meta=payload['meta']
 if not isinstance(aid,str) or len(aid)>100 or type(part)!=int or type(total)!=int or not 0<=part<total<=40 or not isinstance(content,str) or len(content)>160000:raise ValueError('Invalid archive chunk')
 if meta.get('site') not in ('shopping','reddit','study') or meta.get('task') not in (1,2):raise ValueError('Invalid archive scope')
 with db.connect() as c:
  existing=c.execute('SELECT session_id FROM visual_archives WHERE id=?',(aid,)).fetchone()
  if existing:
   if existing['session_id']!=s['id']:raise ValueError('Archive ID conflict')
   return False
  c.execute('DELETE FROM visual_archive_chunks WHERE created_at<?',(time.time()-86400,))
  c.execute('INSERT OR REPLACE INTO visual_archive_chunks VALUES(?,?,?,?,?,?,?)',(s['id'],aid,part,total,content,json.dumps(meta),time.time()))
  rows=c.execute('SELECT content FROM visual_archive_chunks WHERE session_id=? AND archive_id=? ORDER BY part',(s['id'],aid)).fetchall()
  if len(rows)!=total:return False
  html=''.join(r['content'] for r in rows)
  c.execute('INSERT INTO visual_archives(id,session_id,page_id,task,site,client_at,received_at,reason,url,metadata_json,html_zlib) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(aid,s['id'],meta.get('page_id'),meta['task'],meta['site'],meta.get('client_at'),time.time(),meta.get('reason'),meta.get('url'),json.dumps(meta),zlib.compress(html.encode(),6)))
  c.execute('DELETE FROM visual_archive_chunks WHERE session_id=? AND archive_id=?',(s['id'],aid))
 return True

_lock=asyncio.Semaphore(1)
async def render(aid):
 """PNG rendered from the archived DOM, never mislabelled as a browser screen capture."""
 async with _lock:
  try:
   with db.connect() as c:row=c.execute('SELECT html_zlib,metadata_json FROM visual_archives WHERE id=?',(aid,)).fetchone()
   if not row:return
   meta=json.loads(row['metadata_json']);viewport=meta.get('viewport',{})
   from playwright.async_api import async_playwright
   async with async_playwright() as p:
    browser=await p.chromium.launch()
    try:
     page=await browser.new_page(viewport={'width':max(320,min(int(viewport.get('width',1280)),2560)),'height':max(240,min(int(viewport.get('height',900)),1600))},java_script_enabled=False)
     await page.route('**/*',lambda route:route.abort())
     await page.set_content(zlib.decompress(row['html_zlib']).decode(),wait_until='load',timeout=15000)
     await page.evaluate('(p)=>window.scrollTo(p.x||0,p.y||0)',meta.get('scroll',{}))
     png=await page.screenshot(timeout=15000)
    finally:await browser.close()
   with db.connect() as c:c.execute("UPDATE visual_archives SET png=?,render_status='rendered_from_archived_dom' WHERE id=?",(png,aid))
  except Exception as e:
   with db.connect() as c:c.execute('UPDATE visual_archives SET render_status=? WHERE id=?',(type(e).__name__,aid))
