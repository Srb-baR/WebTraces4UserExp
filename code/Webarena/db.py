import hashlib
import hmac
import base64
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from settings import DB_PATH, config, intent
from survey import ITEMS, VERSION, validate

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()

def init():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
          prolific_pid TEXT NOT NULL, study_id TEXT, prolific_session_id TEXT,
          phase TEXT NOT NULL, created_at REAL NOT NULL, consent_at REAL NOT NULL,
          consent_version TEXT NOT NULL, completed_at REAL, config_json TEXT NOT NULL,
          intent TEXT NOT NULL, condition TEXT NOT NULL, user_agent TEXT);
        CREATE TABLE IF NOT EXISTS tasks (
          session_id TEXT REFERENCES sessions(id), task INTEGER CHECK(task IN (1,2)),
          name TEXT, started_at REAL, ended_at REAL, duration_ms REAL, outcome TEXT,
          PRIMARY KEY(session_id,task));
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
          task INTEGER, site TEXT, kind TEXT, client_at REAL, received_at REAL NOT NULL,
          page_id TEXT, seq INTEGER, elapsed_ms REAL, since_previous_event_ms REAL,
          since_previous_click_ms REAL, payload_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_session_time ON events(session_id,received_at);
        CREATE TABLE IF NOT EXISTS submissions (
          id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
          kind TEXT, created_at REAL, payload_json TEXT, correct INTEGER);
        CREATE TABLE IF NOT EXISTS artifacts (
          id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
          kind TEXT, path TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS surveys (
          session_id TEXT PRIMARY KEY REFERENCES sessions(id), version TEXT,
          submitted_at REAL, answers_json TEXT, instrument_json TEXT, comments TEXT);
        ''')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS study_runs(id TEXT PRIMARY KEY,prolific_pid TEXT NOT NULL,created_at REAL NOT NULL,completed_at REAL,protocol TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_rounds(run_id TEXT NOT NULL REFERENCES study_runs(id),full_task_no INTEGER NOT NULL CHECK(full_task_no IN (1,2)),session_id TEXT UNIQUE NOT NULL REFERENCES sessions(id),PRIMARY KEY(run_id,full_task_no));
        CREATE VIEW IF NOT EXISTS round_events AS SELECT r.run_id,r.full_task_no,e.* FROM study_rounds r JOIN events e ON e.session_id=r.session_id;
        CREATE VIEW IF NOT EXISTS round_surveys AS SELECT r.run_id,r.full_task_no,s.* FROM study_rounds r JOIN surveys s ON s.session_id=r.session_id;
        CREATE VIEW IF NOT EXISTS round_submissions AS SELECT r.run_id,r.full_task_no,s.* FROM study_rounds r JOIN submissions s ON s.session_id=r.session_id;
        CREATE VIEW IF NOT EXISTS round_visual_archives AS SELECT r.run_id,r.full_task_no,v.* FROM study_rounds r JOIN visual_archives v ON v.session_id=r.session_id;
        CREATE VIEW IF NOT EXISTS round_stages AS SELECT r.run_id,r.full_task_no,t.* FROM study_rounds r JOIN tasks t ON t.session_id=r.session_id;
        ''')

def round_config(cfg,number):
    result={**cfg,**cfg['full_tasks'][number-1],'full_task_no':number,'full_task_count':len(cfg['full_tasks'])}
    result.pop('official_task',None);result.pop('task_id',None)
    result['intent']=(f"Gather the titles of {result['product']} reviews with {result['rating']} rating from OneStopShop, and post them in {result['forum_path']} under the title \"real user feedback on {result['product']}\"")
    return result

def following_token(token,sid):
    return base64.urlsafe_b64encode(hmac.new(token.encode(),('next-full-task:'+sid).encode(),hashlib.sha256).digest()).decode().rstrip('=')

def next_task_token(token):
    with connect() as c:
        current=session(token,c)
        if current['phase']!='done':return None
        row=c.execute('SELECT n.session_id FROM study_rounds r JOIN study_rounds n ON n.run_id=r.run_id AND n.full_task_no=r.full_task_no+1 WHERE r.session_id=?',(current['id'],)).fetchone()
        return following_token(token,row['session_id']) if row else None

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def create(pid, study_id="", prolific_session_id=""):
    if not pid.strip() or len(pid) > 128:
        raise ValueError("A valid participant ID is required.")
    cfg = config()
    if cfg.get('prolific_required'):
        from prolific import identity
        identity({'PROLIFIC_PID':pid,'STUDY_ID':study_id,'SESSION_ID':prolific_session_id})
    if cfg.get('full_tasks'):cfg=round_config(cfg,1)
    token, sid, now = secrets.token_urlsafe(32), str(uuid.uuid4()), time.time()
    with connect() as c:
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, digest(token), pid.strip(), study_id[:128], prolific_session_id[:128],
             "ready", now, now, "consent-two-rounds-v2" if cfg.get("full_tasks") else "consent-v1", None, json.dumps(cfg), intent(cfg), cfg["mode"], None))
        if cfg.get('full_tasks'):
            run_id=str(uuid.uuid4())
            c.execute('INSERT INTO study_runs VALUES(?,?,?,?,?)',(run_id,pid.strip(),now,None,'two-complete-tasks-v2'))
            c.execute('INSERT INTO study_rounds VALUES(?,?,?)',(run_id,1,sid))
        for n, name in [(1,"gather_review_titles"),(2,"post_to_community")]:
            c.execute("INSERT INTO tasks(session_id,task,name) VALUES (?,?,?)", (sid,n,name))
    return token

def session(token, c=None):
    if c is None:
        with connect() as conn:
            return session(token, conn)
    row = c.execute("SELECT * FROM sessions WHERE token_hash=?", (digest(token),)).fetchone()
    if row is None:
        raise ValueError("Unknown study session.")
    return dict(row)

def system_event(c, sid, kind, task=None, payload=None):
    c.execute("INSERT INTO events(id,session_id,task,site,kind,received_at,payload_json) VALUES (?,?,?,?,?,?,?)",
              (str(uuid.uuid4()),sid,task,"study",kind,time.time(),json.dumps(payload or {})))

def change(token, action, payload=None):
    payload = payload or {}
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        s = session(token,c)
        cfg = json.loads(s["config_json"])
        sid, now, phase = s["id"], time.time(), s["phase"]
        if action == "start" and phase == "ready":
            c.execute("UPDATE tasks SET started_at=? WHERE session_id=? AND task=1",(now,sid))
            next_phase = "shopping"
        elif action == "gather" and phase == "shopping":
            titles = payload.get("titles", "")
            if not isinstance(titles,str) or not titles.strip() or len(titles)>20000:
                raise ValueError("Enter the review titles you gathered, one per line.")
            expected = [r["title"] for p in cfg.get("products",[]) if p["name"]==cfg["product"] for r in p["reviews"] if r["rating"]==cfg["rating"]]
            correct = (sorted(t.strip() for t in titles.splitlines() if t.strip()) == sorted(expected)) if cfg["mode"] == "local_replica" else None
            c.execute("INSERT INTO submissions(session_id,kind,created_at,payload_json,correct) VALUES (?,?,?,?,?)",
                      (sid,"gather",now,json.dumps(payload),int(correct) if correct is not None else None))
            finish_task(c,sid,1,now,"submitted")
            c.execute("UPDATE tasks SET started_at=? WHERE session_id=? AND task=2",(now,sid))
            next_phase = "reddit"
        elif action == "post" and phase == "reddit":
            for key in ("title","body","subreddit"):
                if not isinstance(payload.get(key),str) or not payload[key].strip() or len(payload[key])>20000:
                    raise ValueError("Choose a community and enter a title and body.")
            expected = [r["title"] for p in cfg.get("products",[]) if p["name"]==cfg["product"] for r in p["reviews"] if r["rating"]==cfg["rating"]]
            lines = [t.strip().removeprefix("- ").removeprefix("• ") for t in payload["body"].splitlines() if t.strip()]
            correct = (payload["title"] == f'real user feedback on {cfg["product"]}' and payload["subreddit"] == "games" and sorted(lines)==sorted(expected))
            c.execute("INSERT INTO submissions(session_id,kind,created_at,payload_json,correct) VALUES (?,?,?,?,?)",
                      (sid,"post",now,json.dumps(payload),int(correct) if correct is not None else None))
            finish_task(c,sid,2,now,"submitted")
            next_phase = "survey"
        elif action == "live_finish" and phase == "reddit" and cfg["mode"] == "live":
            if not payload.get("url") or not payload.get("title") or not payload.get("body"):
                raise ValueError("Open your published post before finishing. Its title and body could not be found on this page.")
            c.execute("INSERT INTO submissions(session_id,kind,created_at,payload_json,correct) VALUES (?,?,?,?,?)",
                      (sid,"live_post",now,json.dumps(payload),None))
            finish_task(c,sid,2,now,"submitted_unverified")
            next_phase = "survey"
        elif action == "unable" and phase in ("shopping","reddit"):
            n = 1 if phase == "shopping" else 2
            finish_task(c,sid,n,now,"unable")
            if n == 1:
                c.execute("UPDATE tasks SET started_at=? WHERE session_id=? AND task=2",(now,sid))
            next_phase = "reddit" if n == 1 else "survey"
        elif action == "withdraw" and phase not in ("done","withdrawn"):
            for n in (1,2):
                finish_task(c,sid,n,now,"withdrawn")
            next_phase = "withdrawn"
        else:
            raise ValueError("This action is not available in the current phase. Refresh the study.")
        c.execute("UPDATE sessions SET phase=? WHERE id=?",(next_phase,sid))
        system_event(c,sid,action,1 if phase=="shopping" else 2 if phase=="reddit" else None,payload)
    return next_phase

def finish_task(c,sid,n,now,outcome):
    c.execute("UPDATE tasks SET ended_at=?,duration_ms=(?-started_at)*1000,outcome=? WHERE session_id=? AND task=? AND started_at IS NOT NULL AND ended_at IS NULL",(now,now,outcome,sid,n))

def save_events(token, events):
    if not isinstance(events,list) or len(events)>250:
        raise ValueError("Invalid event batch")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        s = session(token,c)
        if s["phase"] in ("done","withdrawn"):
            return
        for e in events:
            if e.get("task") not in (1,2) or e.get("site") not in ("shopping","reddit","study"):
                raise ValueError("Invalid event scope")
            if not isinstance(e.get("id"),str) or not isinstance(e.get("kind"),str):
                raise ValueError("Invalid event")
            c.execute('''INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (e["id"][:100],s["id"],e["task"],e["site"],e["kind"][:100],e.get("client_at"),time.time(),
                e.get("page_id"),e.get("seq"),e.get("elapsed_ms"),e.get("since_previous_event_ms"),
                e.get("since_previous_click_ms"),json.dumps(e.get("payload",{}))))

def save_survey(token, answers, comments):
    validate(answers)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        s = session(token,c)
        if s["phase"]=="done":
            return
        if s["phase"]!="survey":
            raise ValueError("Complete shopping and Reddit for this task before answering the survey.")
        now = time.time()
        c.execute("INSERT INTO surveys VALUES (?,?,?,?,?,?)",(s["id"],VERSION,now,json.dumps(answers),json.dumps(ITEMS),comments[:10000]))
        c.execute("UPDATE sessions SET phase='done',completed_at=? WHERE id=?",(now,s["id"]))
        system_event(c,s["id"],"survey_submitted")
        cfg=json.loads(s['config_json'])
        link=c.execute('SELECT * FROM study_rounds WHERE session_id=?',(s['id'],)).fetchone()
        if link and link['full_task_no']<len(cfg.get('full_tasks',[])):
            number=link['full_task_no']+1;sid=str(uuid.uuid4());new_cfg=round_config(cfg,number)
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,digest(following_token(token,sid)),s['prolific_pid'],s['study_id'],s['prolific_session_id'],'ready',now,s['consent_at'],s['consent_version'],None,json.dumps(new_cfg),intent(new_cfg),s['condition'],None))
            c.execute('INSERT INTO study_rounds VALUES(?,?,?)',(link['run_id'],number,sid))
            for n,name in [(1,'gather_review_titles'),(2,'post_to_community')]:c.execute('INSERT INTO tasks(session_id,task,name) VALUES(?,?,?)',(sid,n,name))
        elif link:
            c.execute('UPDATE study_runs SET completed_at=? WHERE id=?',(now,link['run_id']))

def state(token):
    with connect() as c:
        s=session(token,c)
        return {"phase":s["phase"],"config":json.loads(s["config_json"]),"intent":s["intent"],
                "tasks":[dict(r) for r in c.execute("SELECT * FROM tasks WHERE session_id=? ORDER BY task",(s["id"],))],
                "submissions":[dict(r) for r in c.execute("SELECT kind,payload_json FROM submissions WHERE session_id=? ORDER BY id",(s["id"],))]}
