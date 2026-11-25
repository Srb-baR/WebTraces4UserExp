import sqlite3,json,time,secrets,hashlib,os,hmac
from pathlib import Path
from contextlib import contextmanager,nullcontext
from survey import ITEMS,VERSION,validate
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('RECONSTRUCTED_DB',str(ROOT/'data/prolific_observers.sqlite')))
@contextmanager
def connect():
 DB.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row
 try:
  with c:yield c
 finally:c.close()
def init():
 with connect() as c:
  c.executescript('''PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS observers(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE,participant_id TEXT,study_id TEXT,prolific_session_id TEXT,source_id TEXT,created_at REAL,completed_at REAL,phase TEXT,trace_json TEXT);
CREATE TABLE IF NOT EXISTS interactions(id INTEGER PRIMARY KEY,observer_id TEXT,at REAL,kind TEXT,payload_json TEXT);
CREATE TABLE IF NOT EXISTS observer_pairs(id TEXT PRIMARY KEY,source_run_id TEXT NOT NULL,first_id TEXT UNIQUE NOT NULL,second_id TEXT UNIQUE NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS responses(observer_id TEXT PRIMARY KEY,at REAL,version TEXT,answers_json TEXT,instrument_json TEXT,comments TEXT);
''')
  columns={r[1] for r in c.execute('PRAGMA table_info(observers)')}
  for name,kind in [('source_run_id','TEXT'),('full_task_no','INTEGER')]:
   if name not in columns:c.execute(f'ALTER TABLE observers ADD COLUMN {name} {kind}')
def create(pid,study,psession,trace):
 token=secrets.token_urlsafe(32);oid=secrets.token_hex(16)
 with connect() as c:c.execute('INSERT INTO observers(id,token_hash,participant_id,study_id,prolific_session_id,source_id,created_at,completed_at,phase,trace_json,source_run_id,full_task_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(oid,hashlib.sha256(token.encode()).hexdigest(),pid,study,psession,trace['id'],time.time(),None,'review',json.dumps(trace),trace.get('run_id'),trace.get('full_task_no')))
 return token
def session(token):
 with connect() as c:r=c.execute('SELECT * FROM observers WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
 if not r:raise ValueError('Session not found.')
 return dict(r)
def log(oid,kind,payload):
 with connect() as c:c.execute('INSERT INTO interactions(observer_id,at,kind,payload_json) VALUES(?,?,?,?)',(oid,time.time(),kind,json.dumps(payload)))
def finish(oid,answers,comments):
 validate(answers)
 with connect() as c:
  row=c.execute('SELECT phase FROM observers WHERE id=?',(oid,)).fetchone()
  if not row:raise ValueError('Observer not found.')
  if row['phase']=='done':return
  pair=c.execute('SELECT first_id FROM observer_pairs WHERE second_id=?',(oid,)).fetchone()
  if pair and c.execute('SELECT phase FROM observers WHERE id=?',(pair['first_id'],)).fetchone()[0]!='done':raise ValueError('Complete the first questionnaire first.')
  c.execute('INSERT OR IGNORE INTO responses VALUES(?,?,?,?,?,?)',(oid,time.time(),VERSION,json.dumps(answers),json.dumps(ITEMS),comments))
  c.execute("UPDATE observers SET phase='done',completed_at=? WHERE id=?",(time.time(),oid))
  c.execute("INSERT INTO interactions(observer_id,at,kind,payload_json) VALUES(?,?,?,?)",(oid,time.time(),"questionnaire_submitted",json.dumps({"version":VERSION})))


def second_token(token,oid):
 return hmac.new(token.encode(),('observer-task-2:'+oid).encode(),hashlib.sha256).hexdigest()

def create_pair(pid,study,psession,traces,token=None,connection=None):
 if len(traces)!=2 or [t.get('full_task_no') for t in traces]!=[1,2] or not traces[0].get('run_id') or traces[0]['run_id']!=traces[1].get('run_id'):
  raise ValueError('Two ordered tasks from the same original run are required.')
 token=token or secrets.token_urlsafe(32);ids=[secrets.token_hex(16),secrets.token_hex(16)];tokens=[token,second_token(token,ids[1])];now=time.time()
 with (nullcontext(connection) if connection is not None else connect()) as c:
  if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
  if c.execute('SELECT 1 FROM observers WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone():return token
  for i,trace in enumerate(traces):
   c.execute('INSERT INTO observers(id,token_hash,participant_id,study_id,prolific_session_id,source_id,created_at,completed_at,phase,trace_json,source_run_id,full_task_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ids[i],hashlib.sha256(tokens[i].encode()).hexdigest(),pid,study,psession,trace['id'],now,None,'review' if i==0 else 'ready',json.dumps(trace),trace['run_id'],i+1))
  c.execute('INSERT INTO observer_pairs VALUES(?,?,?,?,?)',(secrets.token_hex(16),traces[0]['run_id'],*ids,now))
 return token

def next_task(token):
 current=session(token)
 if current['phase']!='done':return None
 with connect() as c:row=c.execute('SELECT second_id FROM observer_pairs WHERE first_id=?',(current['id'],)).fetchone()
 return second_token(token,row['second_id']) if row else None

def begin_second(token):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  row=c.execute('SELECT o.id,o.phase,p.first_id FROM observers o JOIN observer_pairs p ON p.second_id=o.id WHERE o.token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
  if not row or c.execute('SELECT phase FROM observers WHERE id=?',(row['first_id'],)).fetchone()[0]!='done':raise ValueError('Complete task 1 first.')
  if row['phase']=='ready':c.execute("UPDATE observers SET phase='review' WHERE id=?",(row['id'],))

CONSENT_VERSION='observer-recording-v1'
def record_consent(ids):
 with connect() as c:
  for oid in ids:
   c.execute('INSERT INTO interactions(observer_id,at,kind,payload_json) VALUES(?,?,?,?)',
             (oid,time.time(),'consent_accepted',json.dumps({'version':CONSENT_VERSION,'scope':'navigation, clicks, scrolls, focus, viewing time, questionnaire edits and submissions','introduction_seen':True})))

def browser_events(oid,events):
 with connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS browser_events(event_id TEXT PRIMARY KEY,observer_id TEXT,received_at REAL,client_at REAL,kind TEXT,payload_json TEXT)')
  for e in events[:500]:
   if not isinstance(e,dict) or not isinstance(e.get('id'),str):continue
   c.execute('INSERT OR IGNORE INTO browser_events VALUES(?,?,?,?,?,?)',(oid+':'+e['id'],oid,time.time(),e.get('at'),e.get('kind','unknown'),json.dumps(e.get('payload',{}))))

def viewed_indices(oid):
 with connect() as c:
  rows=c.execute("SELECT payload_json FROM interactions WHERE observer_id=? AND kind='view_chapter'",(oid,)).fetchall()
 return sorted({json.loads(r[0])['index'] for r in rows})

def open_questionnaire(oid,total):
 viewed=[i for i in viewed_indices(oid) if 0<=i<total]
 payload={'total_pages':total,'viewed_indices':viewed,'unviewed_indices':[i for i in range(total) if i not in viewed],'skipped_pages':len(viewed)<total}
 with connect() as c:
  c.execute('INSERT INTO interactions(observer_id,at,kind,payload_json) VALUES(?,?,?,?)',(oid,time.time(),'questionnaire_opened',json.dumps(payload)))
  c.execute("UPDATE observers SET phase='survey' WHERE id=?",(oid,))


def pair_completed(oid):
 with connect() as c:
  row=c.execute("SELECT a.phase,b.phase FROM observer_pairs p JOIN observers a ON a.id=p.first_id JOIN observers b ON b.id=p.second_id WHERE p.second_id=?",(oid,)).fetchone()
 return bool(row and row[0]=='done' and row[1]=='done')

def assign_pair(pid,study,psession,token):
 """Reserve one unused complete source pair atomically; never recycle a source."""
 import replay
 choices=replay.available_pairs()
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  if c.execute('SELECT 1 FROM observers WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone():return token
  used={r[0] for r in c.execute('SELECT source_run_id FROM observer_pairs')}
  chosen=next((p for p in choices if p['run_id'] not in used),None)
  if not chosen:raise ValueError('All available recorded experiences have been assigned. Please contact the researcher.')
  traces=[replay.load(sid) for sid in chosen['session_ids']]
  return create_pair(pid,study,psession,traces,token=token,connection=c)
