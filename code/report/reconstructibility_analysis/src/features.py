"""Trace features from recorded behavior; timing excluded from visible-cue analyses."""
import json
import numpy as np
import pandas as pd
from data import fetch,sid

def elapsed(start,end):
 if start is None or end is None:return np.nan
 try:return float(end)-float(start)
 except (TypeError,ValueError):return (pd.to_datetime(end,utc=True)-pd.to_datetime(start,utc=True)).total_seconds()

def extract(originals):
 results=[];definitions={}
 def add(o,name,value,definition,visible=True):
  try:value=float(value)
  except (TypeError,ValueError):value=np.nan
  results.append(dict(application=o.application,task=o.task,actor_id=o.actor_id,actor_session_id=o.actor_session_id,feature=name,value=value,visible_trace=visible,visibility='Derived from recorded replay actions/results' if visible else 'Recorded timing withheld in time-hidden condition',definition=definition))
  definitions[o.application,name]=dict(application=o.application,feature=name,definition=definition,visible_to_time_hidden_llm=visible,visible_to_human_replay='Replay-derived behavioral summary; exposure inferred from recorded replay schema, not attention or actual use' if visible else 'Excluded; timestamps alone do not prove visibility')
 for o in originals.itertuples(index=False):
  p=o.source_database;s=o.actor_session_id
  if o.application=='testreco':
   a=fetch(p,'SELECT * FROM attempts WHERE session_id=?',(s,));steps=fetch(p,'SELECT * FROM learning_steps WHERE session_id=? ORDER BY step_index',(s,));session=fetch(p,'SELECT * FROM study_sessions WHERE id=?',(s,))[0]
   learning=[r for r in a if r['phase']=='learning'];pre=[r for r in a if r['phase']=='pretest']
   add(o,'learning_accuracy',np.mean([r['is_correct'] for r in learning]) if learning else None,'Mean recorded is_correct over learning attempts')
   add(o,'pretest_accuracy',np.mean([r['is_correct'] for r in pre]) if pre else None,'Mean recorded is_correct over pretest attempts')
   add(o,'completed_questions',len(learning),'Number of recorded learning question attempts (not recommendations)')
   add(o,'mastery_progression',steps[-1]['mastery_after']-steps[0]['mastery_before'] if steps else None,'Last learning mastery_after minus first mastery_before; system estimate, not percentile')
   add(o,'session_duration',elapsed(session['started_at'],session['ended_at']),'Session ended_at minus started_at, seconds; not active solving time',False)
  elif o.application=='quizcomp':
   a=fetch(p,'SELECT * FROM compose_attempts WHERE session_id=? ORDER BY ts,id',(s,));events=fetch(p,"SELECT * FROM event_logs WHERE session_id=? AND event_type!='survey' AND name NOT IN ('submitted','ended','study_session_created','study_session_resumed')",(s,));session=fetch(p,'SELECT * FROM study_sessions WHERE id=?',(s,))[0]
   add(o,'composition_attempts',len(a),'Number of recorded compose_attempts rows')
   add(o,'improvement_actions',sum(r['name']=='satisfied_no' for r in events),'Recorded dissatisfied/improve button actions, name=satisfied_no')
   add(o,'composition_failures',sum(not r['succeeded'] for r in a),'Count of compose_attempts with succeeded false')
   add(o,'final_target_match',a[-1]['target_match'] if a else None,'Final recorded target_match, not assumed subjective satisfaction')
   add(o,'final_quiz_size',a[-1]['num_mcqs'] if a else None,'Final recorded num_mcqs')
   add(o,'session_duration',session.get('total_time_s'),'Recorded total_time_s, seconds; not active interaction time',False)
  else:
   # Exclude post-survey study screens and background browser/page snapshot events.
   events=fetch(p,"SELECT kind FROM events WHERE session_id=? AND received_at <= (SELECT submitted_at FROM surveys WHERE session_id=?)",(s,s))
   kinds=['click','input','change','submit','keydown','paste','copy','navigation']
   add(o,'action_count',sum(r['kind'] in kinds for r in events),'Count of pre-survey recorded click/input/change/submit/keydown/paste/copy/navigation actions; excludes page snapshots and background events')
   add(o,'click_count',sum(r['kind']=='click' for r in events),'Count of pre-survey recorded click actions')
   add(o,'submission_count',fetch(p,"SELECT count(*) n FROM submissions WHERE session_id=? AND kind NOT LIKE '%survey%'",(s,))[0]['n'],'Number of non-survey task submissions')
   ts=fetch(p,'SELECT * FROM tasks WHERE session_id=?',(s,));success=[]
   add(o,'unable_subtasks',sum(str(t.get('outcome') or '').lower()=='unable' for t in ts),'Number of subtasks explicitly recorded as unable; submitted/submitted_unverified are not assumed successful')
   for t in ts:
    val=str(t.get('outcome') or '').lower()
    if val in ('success','succeeded','correct','true','1'):success.append(1)
    elif val in ('failure','failed','incorrect','false','0'):success.append(0)
   add(o,'recorded_task_success',np.mean(success) if success else None,'Mean explicitly coded success/failure subtask outcomes; unrecognized outcome text remains missing')
   add(o,'session_duration',sum(t['duration_ms'] for t in ts if t['duration_ms'] is not None)/1000 if any(t['duration_ms'] is not None for t in ts) else None,'Sum of recorded subtask durations in seconds; unavailable as a visible time-hidden cue',False)
 return pd.DataFrame(results),list(definitions.values())


def additional_webarena_features(originals):
    """Count recorded pre-survey navigation and browser input events."""
    rows = []
    for o in originals[originals.application == 'webarena'].itertuples(index=False):
        events = fetch(o.source_database, '''
            SELECT kind, CASE WHEN kind='page_view' THEN json_extract(payload_json, '$.url') END AS url FROM events
            WHERE session_id=? AND received_at <= (
                SELECT submitted_at FROM surveys WHERE session_id=?
            ) ORDER BY received_at, rowid
        ''', (o.actor_session_id, o.actor_session_id))
        pages = [e['url'].split('#', 1)[0] for e in events if e['url']]
        visits = list(dict.fromkeys(pages))
        counts = {
            'keystrokes': sum(e['kind'] == 'keydown' for e in events),
            'pages_visited': len(visits),
            'scrolling': sum(e['kind'] == 'scroll' for e in events),
            'backtracking': sum(a != b and b in pages[:i] for i, (a, b) in enumerate(zip(pages, pages[1:]))),
        }
        for feature, value in counts.items():
            rows.append(dict(application=o.application, task=o.task, actor_id=o.actor_id,
                             actor_session_id=o.actor_session_id, feature=feature, value=value,
                             visible_trace=True, additional=True))
    return pd.DataFrame(rows)
