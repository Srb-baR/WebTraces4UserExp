"""Read-only recorded interaction evidence, retaining timing and hiding survey/identity data."""
import json
import re
import sqlite3
from pathlib import Path

PRIVATE={'user_id','username','prolific_pid','prolific_participant_id','prolific_study_id','prolific_session_id','study_id','token_hash','password_hash','completion_code','email','ip','ip_address','notes'}
SURVEY=re.compile(r'survey|questionnaire|answers_json|instrument_json|q[1-9]_')
TABLES={
 'testreco':[('study_sessions','id'),('attempts','session_id'),('learning_steps','session_id'),('orchestrator_calls','session_id'),('simulation_questions','session_id')],
 'quizcomp':[('study_sessions','id'),('event_logs','session_id'),('compose_attempts','session_id')],
 'webarena':[('sessions','id'),('tasks','session_id'),('events','session_id'),('submissions','session_id')],
}


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k.lower() not in PRIVATE and not SURVEY.search(k.lower())}
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,str):
        try:decoded=json.loads(value)
        except (ValueError,TypeError):return value
        if isinstance(decoded,(dict,list)):return clean(decoded)
    return value


def is_survey(row):
    if any(SURVEY.search(str(row.get(k,'')).lower()) for k in ('event_type','name','kind')):return True
    payload=row.get('payload_json',{})
    if isinstance(payload,str):
        try:payload=json.loads(payload)
        except ValueError:payload={}
    if not isinstance(payload,dict):return False
    return (payload.get('phase') in ('survey','done') or str(payload.get('url','')).startswith(('study:survey','study:done'))
            or any(t in str(payload.get('visible_text','')) for t in ('How successful do you feel','Post-task questionnaire','Post-study survey')))



def records(app,source,sid):
    result={}
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table,key in TABLES[app]:
            if table not in tables:continue
            rows=[]
            for r in con.execute('SELECT * FROM "'+table+'" WHERE "'+key+'"=? ORDER BY rowid',(sid,)):
                row=dict(r)
                if is_survey(row):
                    if table in ('events','event_logs'):break
                    continue
                rows.append(clean(row))
            result[table]=rows
    return result


def event_time(row):
    for key in ('ts','received_at','answered_at','started_at','created_at'):
        if row.get(key) is not None:return str(row[key])
    return ''


def format_duration(seconds):
    import math
    if seconds is None:return 'Not recorded'
    seconds=float(seconds)
    if not math.isfinite(seconds) or seconds<0:return 'Not recorded'
    minutes,seconds=divmod(int(seconds+0.5),60)
    return f'{minutes} min {seconds:02d} sec'


def duration(row):
    from datetime import datetime
    for key,scale in [('duration_ms',1000),('response_time_ms',1000),('total_time_s',1)]:
        if row.get(key) is not None:return float(row[key])/scale
    def stamp(value):
        if isinstance(value,(int,float)):return float(value)
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    start=row.get('started_at',row.get('created_at'));end=row.get('ended_at',row.get('completed_at'))
    if start is None or end is None:return None
    try:return stamp(end)-stamp(start)
    except (ValueError,TypeError):return None


def render(app,source,sid):
    import streamlit as st
    data=records(app,source,sid)
    if app=='webarena':
        for row in data.get('tasks',[]):
            label='Shopping' if row.get('task')==1 else 'Reddit'
            st.caption(label+' time: '+format_duration(duration(row)))
    else:
        sessions=data.get('study_sessions',[])
        if sessions:st.caption('Recorded session time: '+format_duration(duration(sessions[0])))
