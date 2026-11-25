import json,sqlite3,os
from pathlib import Path
from contextlib import contextmanager
SOURCE=Path(os.environ.get('WEBARENA_REPLAY_SOURCE',str(Path(__file__).resolve().parent.parent/'report/data/actors/webarena/study.sqlite')))
EXCLUDE={'survey_submitted','heartbeat','control_heartbeat','pointermove','pointerdown','pointerup','keydown','keyup','focusin','focusout','control_focusin','control_focusout','response','console'}
@contextmanager
def connect():
 c=sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:yield c
 finally:c.close()
def available():
 with connect() as c:
  has_rounds=c.execute("SELECT 1 FROM sqlite_master WHERE name='study_rounds'").fetchone()
  if has_rounds:
   rows=[dict(r) for r in c.execute("SELECT s.id,s.intent,s.created_at,r.run_id,r.full_task_no FROM sessions s JOIN study_rounds r ON r.session_id=s.id WHERE s.phase='done' AND s.prolific_pid NOT LIKE 'TEST%' ORDER BY s.created_at")]
   if rows:return rows
  return [dict(r) for r in c.execute("SELECT id,intent,created_at FROM sessions WHERE phase='done' AND prolific_pid NOT LIKE 'TEST%' ORDER BY created_at")]
def load(sid):
 with connect() as c:
  c.execute('BEGIN')
  s=c.execute("SELECT id,intent,created_at,completed_at,config_json FROM sessions WHERE id=? AND phase='done' AND prolific_pid NOT LIKE 'TEST%'",(sid,)).fetchone()
  if not s:raise ValueError('Completed trace not found.')
  trace=dict(s)
  cfg=json.loads(trace.pop("config_json"));trace["forum_path"]=cfg.get("forum_path","/f/gaming");trace["product"]=cfg["product"]
  trace['tasks']=[dict(r) for r in c.execute('SELECT task,name,started_at,ended_at,duration_ms,outcome FROM tasks WHERE session_id=? ORDER BY task',(sid,))]
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'study_rounds' in tables:
   row=c.execute('SELECT run_id,full_task_no FROM study_rounds WHERE session_id=?',(sid,)).fetchone()
   if row:trace.update(dict(row))
  trace['visual_archives']=[dict(r) for r in c.execute("SELECT id,page_id,received_at,metadata_json,render_status FROM visual_archives WHERE session_id=? ORDER BY received_at",(sid,))] if 'visual_archives' in tables else []
  trace['events']=[]
  for r in c.execute("SELECT id,task,site,kind,client_at,received_at,page_id,seq,payload_json FROM events WHERE session_id=? AND kind NOT LIKE '%survey%' ORDER BY received_at,rowid",(sid,)):
   e=dict(r);e['payload']=json.loads(e.pop('payload_json'));trace['events'].append(e)
  trace['submissions']=[{'kind':r[0],'created_at':r[1],'payload':json.loads(r[2])} for r in c.execute("SELECT kind,created_at,payload_json FROM submissions WHERE session_id=? AND kind IN ('gather','live_post','post') ORDER BY created_at",(sid,))]
 return trace
def steps(trace):
 result=[e for e in trace['events'] if e['kind'] not in EXCLUDE and e['kind'] not in ('dom_snapshot','page_snapshot')]
 for sub in trace['submissions']:
  result.append({'kind':'submitted_'+sub['kind'],'task':1 if sub['kind']=='gather' else 2,'site':'study','received_at':sub['created_at'],'payload':sub['payload'],'page_id':None})
 return sorted(result,key=lambda e:e['received_at'])
def snapshot(trace,event):
 candidates=[e for e in trace['events'] if e['kind'] in ('dom_snapshot','page_snapshot') and e['received_at']<=event['received_at'] and e['page_id']==event.get('page_id')]
 return candidates[-1]['payload'] if candidates else None

def archived_visual(trace,chapter):
 import zlib
 records=[a for a in trace.get('visual_archives',[]) if a['page_id']==chapter['identity'][0] and a['render_status']!='historical_dom_no_original_assets']
 if chapter.get('study_evidence'):
  records=[a for a in records if a['id']==chapter['identity'][1]]
 else:
  records=[a for a in records if a['received_at']<=chapter['end']+2]
 if not records:return None
 with connect() as c:
  row=c.execute('SELECT html_zlib,png,metadata_json FROM visual_archives WHERE id=? AND session_id=?',(records[-1]['id'],trace['id'])).fetchone()
 if row:return {'html':zlib.decompress(row['html_zlib']).decode(),'png':row['png'],'meta':json.loads(row['metadata_json'])}

def reference_visual(key):
 import zlib
 with connect() as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='visual_references'").fetchone():return None
  row=c.execute('SELECT html_zlib FROM visual_references WHERE reference_key=?',(key,)).fetchone()
 return zlib.decompress(row[0]).decode() if row and row[0] else None

def available_pairs():
 """Only complete two-task runs; never pair unrelated participants."""
 with connect() as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='study_rounds'").fetchone():return []
  rows=c.execute("SELECT r.run_id,r.full_task_no,s.id FROM study_rounds r JOIN sessions s ON s.id=r.session_id WHERE s.phase='done' AND s.prolific_pid NOT LIKE 'TEST%' AND EXISTS (SELECT 1 FROM surveys q WHERE q.session_id=s.id) ORDER BY s.created_at").fetchall()
 groups={}
 for row in rows:groups.setdefault(row['run_id'],{})[row['full_task_no']]=row['id']
 manifest=SOURCE.parent/'manifest.json'
 allowed=set(json.loads(manifest.read_text())['eligible_run_ids']) if manifest.exists() else None
 return [{'run_id':run,'session_ids':[tasks[1],tasks[2]]} for run,tasks in groups.items() if set(tasks)=={1,2} and (allowed is None or run in allowed)]
