"""Read-only database discovery, explicit schema mappings and actor linkage."""
from pathlib import Path
import sqlite3,json,re,hashlib
from contextlib import contextmanager
import numpy as np
import pandas as pd

DIMS=['Acc','Eff','MD','Cont','TD','Sat']
KEYS={'testreco':['accomplishment','effort_required','mental_demand','perceived_controllability','temporal_demand','trust'],
'quizcomp':['q1_accomplishment','q2_effort','q3_mental_demand','q4_controllability','q5_temporal_demand','q6_satisfaction_trust'],
'webarena':['accomplishment','effort_required','mental_demand','perceived_controllability','temporal_demand','satisfaction']}
HUMAN_TEST=['q1_accomplishment','q2_effort_required','q3_mental_demand','q4_perceived_controllability','q5_temporal_demand','q7_trust']
HUMAN_QUIZ=['q1_accomplishment','q2_effort','q3_mental_demand','q4_controllability','q5_temporal_demand','q6_satisfaction']
@contextmanager
def db(path):
 c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:yield c
 finally:c.close()
def fetch(path,sql,params=()):
 with db(path) as c:return [dict(r) for r in c.execute(sql,params)]
def tables(path):return [r['name'] for r in fetch(path,"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
def safeid(value):return hashlib.sha256(str(value).encode()).hexdigest()[:16]
def sid(value):
 s=str(value);return str(int(s)) if s.isdecimal() else s

def sources(root,data,use_observed=False):
 records=[]
 observed=data/'observed'
 if use_observed and not observed.is_dir():raise FileNotFoundError(observed)
 evaluator_root=observed if use_observed else data
 balanced=observed.is_dir() and not use_observed
 for folder,kind in [('actors','actor'),('human evaluators','human'),('LLM evaluators','llm')]:
  base=(data if kind=='actor' else evaluator_root)/folder
  for p in sorted(base.glob('*/*')):
   if p.suffix not in ('.sqlite','.db'):continue
   condition='actor' if kind=='actor' else ('no_trace' if p.name=='no_trace.db' or p.stem.endswith('__no_trace') else 'trace')
   records.append(dict(path=str(p.resolve()),application=p.parent.name,kind=kind,condition=condition,mapping='balanced' if balanced and kind!='actor' else 'detected'))
 for app,folder in [('testreco','testreco'),('quizcomp','QuizComp'),('webarena','WebArenaShopping')]:
  for p in sorted((root/'code/metrics/crowd2LLMs'/folder/'llm_dbs/survey_only_openrouter_no_trace').rglob('survey_predictions.sqlite')):
   packaged=evaluator_root/'LLM evaluators'/app/(p.parent.parent.name+'__no_trace.sqlite')
   if not packaged.is_file():records.append(dict(path=str(p.resolve()),application=app,kind='llm',condition='no_trace',mapping='description_only_predictions'))
 human=[('testreco','code/TestReco/Simulation_app_no_trace/no_trace_simulation_results.db'),('quizcomp','code/QuizComp/Simulation_app_no_trace/no_trace_survey_responses.db'),('webarena','code/metrics/crowd2crowd/webarena/dbs/webarena_no_trace_merged.db')]
 for app,p in human:
  if (root/p).is_file() and not (evaluator_root/'human evaluators'/app/'no_trace.db').is_file():records.append(dict(path=str((root/p).resolve()),application=app,kind='human',condition='no_trace',mapping='independent_control'))
 return records

def inventory(records,out):
 inv=[];schema=[]
 for meta in records:
  path=Path(meta['path']);entry=dict(meta,bytes=path.stat().st_size,tables=[])
  with db(path) as c:
   for t in tables(path):
    columns=[dict(r) for r in c.execute(f'PRAGMA table_info("{t}")')]
    count=c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    # Redact participant identifiers, free text, credentials and large trace payloads.
    samples=[]
    for row in c.execute(f'SELECT * FROM "{t}" LIMIT 2'):
     sample={}
     for k,v in dict(row).items():
      if v is None or isinstance(v,(int,float)):sample[k]=v
      elif re.search('score|q[1-9]|phase|status|model|protocol|task|condition|education|mode|event_type|kind',k):sample[k]=str(v)[:150]
      else:sample[k]='[redacted; length '+str(len(str(v)))+']'
     samples.append(sample)
    entry['tables'].append(dict(name=t,n_rows=count,columns=columns,foreign_keys=[dict(r) for r in c.execute(f'PRAGMA foreign_key_list("{t}")')],representative_records=samples))
    schema.append(dict(**meta,table=t,n_rows=count,columns=json.dumps([r['name'] for r in columns]),primary_keys=json.dumps([r['name'] for r in columns if r['pk']]),foreign_keys=json.dumps([dict(r) for r in c.execute(f'PRAGMA foreign_key_list("{t}")')])))
  inv.append(entry)
 (out/'database_inventory.json').write_text(json.dumps(inv,indent=2)+'\n')
 pd.DataFrame(schema).to_csv(out/'database_schema.csv',index=False)
 return inv

def normalize(records):
 originals={};orig=[];evaluations=[];excluded=[];audits=[];source_cache={};actor_paths={m['application']:m['path'] for m in records if m['kind']=='actor'}
 for meta in records:
  if meta['kind']!='actor':continue
  app=meta['application'];p=meta['path']
  if app=='webarena':
   rows=fetch(p,'SELECT v.*,r.run_id,r.full_task_no FROM surveys v JOIN study_rounds r USING(session_id)')
  else:
   rows=fetch(p,'SELECT v.*,s.user_id FROM survey_responses v JOIN study_sessions s ON s.id=v.session_id')
  for r in rows:
   session=sid(r['session_id']);actor=safeid(app+':'+str(r['run_id'] if app=='webarena' else r['user_id']));task='T'+str(r['full_task_no']) if app=='webarena' else 'main'
   a=json.loads(r['answers_json']) if app=='webarena' else r
   values=[a.get(k) for k in KEYS[app]]
   rec=dict(application=app,task=task,actor_id=actor,actor_session_id=session,original_trace_id=session,source_database=p,**dict(zip(DIMS,values)))
   if (app,session) in originals:raise ValueError('Duplicate original survey '+session)
   originals[app,session]=rec;orig.append(rec)
 for meta in records:
  if meta['kind']=='actor':continue
  app=meta['application'];p=meta['path'];kind=meta['kind'];condition=meta['condition'];round_id=Path(p).stem;raw=[];mapping='';total=0
  balanced=meta['mapping']=='balanced'
  if balanced:
   raw=fetch(p,'SELECT * FROM ratings ORDER BY id')
   mapping='Balanced evaluator export; padded rows retain their source response IDs'
   round_id=raw[0]['evaluation_round']
   for r in raw:
    r.update(_answers=dict(zip(KEYS[app], (r[d] for d in DIMS))),
             _sid=r['actor_session_id'], _task=r['task'], _run=r['run_id'],
             _evaluator=r['evaluator_id'], _protocol=r['protocol'],
             _model=round_id if kind=='llm' else '')
  elif kind=='llm':
   raw=fetch(p,'SELECT * FROM predictions ORDER BY source_session_id,run');mapping='predictions.source_session_id -> actor survey session_id; no_trace IDs explicitly unpaired'
   for r in raw:
    r['_answers']=json.loads(r['answers_json']);r['_sid']=None if condition=='no_trace' else sid(r['source_session_id']);r['_model']=r['model'];r['_evaluator']=r['model'];r['_run']=str(r['run']);r['_task']=None
    if app=='quizcomp':r['_answers']={k:r['_answers'].get(v) for k,v in zip(KEYS[app],['accomplishment','effort','mental_demand','controllability','temporal_demand','satisfaction'])}
    r['_protocol']=r['protocol']
    if condition=='trace' and r['protocol']!='full-trace-role-context-time-blind-v2':raise ValueError('Unexpected protocol '+p)
    if condition=='no_trace' and (r['source_session_id']!='description-only' or r['source_db']!='fixed-study-description'):raise ValueError('Unexpected no-trace linkage')
   round_id=raw[0]['model'] if raw else round_id
  elif condition=='no_trace':
   mapping='Independent population controls; administrative source/persona assignments are not actor matches'
   if app=='webarena':
    raw=fetch(p,'SELECT r.*,o.participant_id,o.full_task_no FROM responses r JOIN observers o ON o.id=r.observer_id')
    for r in raw:r.update(_answers=json.loads(r['answers_json']),_task='T'+str(r['full_task_no']),_evaluator=safeid(r['participant_id']),_run='1',_sid=None)
   else:
    raw=fetch(p,'SELECT * FROM survey_responses')
    for r in raw:
     columns=['q1_accomplishment','q2_effort_required','q3_mental_demand','q4_controllability','q5_temporal_demand','q7_trust' if app=='testreco' else 'q6_satisfaction']
     r.update(_answers=dict(zip(KEYS[app],[r.get(k) for k in columns])),_task='main',_evaluator=safeid(r.get('prolific_pid') or r.get('participant_key') or r['review_id']),_run='1',_sid=None)
   round_id='human_no_trace'
  elif app=='testreco':
   ts=tables(p)
   if 'simulation_feedback' in ts:
    raw=fetch(p,'SELECT * FROM simulation_feedback')
    assignments=fetch(p,'SELECT * FROM participant_assignments') if 'participant_assignments' in ts else []
    assign={r.get('completed_review_id'):r for r in assignments if r.get('completed_review_id')}
    for r in raw:
     a=assign.get(r.get('review_id'),{});r.update(_answers=dict(zip(KEYS[app],[r.get(k) for k in HUMAN_TEST])),_sid=sid(r['source_session_id']),_evaluator=safeid(a.get('prolific_pid') or a.get('participant_key') or str(Path(p).name)+':'+str(r['id'])),_run='1',_task='main')
    mapping='simulation_feedback.source_session_id; evaluator via participant_assignments.completed_review_id when available'
   else:
    raw=fetch(p,'SELECT v.*,s.simulated_session_id,s.user_id FROM survey_responses v JOIN study_sessions s ON s.id=v.session_id WHERE s.simulated_session_id IS NOT NULL')
    for r in raw:r.update(_answers=r.copy(),_sid=sid(r['simulated_session_id']),_evaluator=safeid('testreco-round1-user:'+str(r['user_id'])),_run='1',_task='main')
    mapping='survey_responses.session_id -> study_sessions.simulated_session_id (explicit original session)'
  elif app=='quizcomp':
   raw=fetch(p,'SELECT v.*,s.source_session_id,s.observer_prolific_pid,s.participant_key,s.run_number FROM llm_survey_responses v JOIN simulations s ON s.id=v.simulation_id')
   for r in raw:r.update(_answers=dict(zip(KEYS[app],[r.get(k) for k in HUMAN_QUIZ])),_sid=sid(r['source_session_id']),_evaluator=safeid(r.get('observer_prolific_pid') or r.get('participant_key') or str(Path(p).name)+':'+str(r['simulation_id'])),_run=str(r['run_number']),_task='main')
   mapping='llm_survey_responses.simulation_id -> simulations.source_session_id; table name is historical, these are human observer data'
  else:
   raw=fetch(p,'SELECT r.*,o.source_id,o.source_run_id,o.full_task_no,o.participant_id FROM responses r JOIN observers o ON o.id=r.observer_id')
   parts={t:{r['participant_id'] for r in raw if r['full_task_no']==t} for t in (1,2)};complete=parts[1]&parts[2]
   for r in raw:
    r.update(_answers=json.loads(r['answers_json']),_sid=sid(r['source_id']),_task='T'+str(r['full_task_no']),_evaluator=safeid(r['participant_id']),_run='1')
    if r['participant_id'] not in complete:r['_exclude']='observer did not submit both task surveys'
   mapping='responses.observer_id -> observers.source_id; source_run_id and full_task_no validated against original'
  total=len(raw);matched=unmatched=missing=0;seen=set();used=[]
  for n,r in enumerate(raw):
   why=r.get('_exclude');original=originals.get((app,r['_sid'])) if r['_sid'] is not None else None
   if condition=='trace' and original is None:why=why or 'original session outside included actor cohort'
   if balanced and original and (original['actor_id']!=r['actor_id'] or original['task']!=r['_task']):
    raise ValueError('Balanced row actor metadata mismatch')
   if not balanced and app=='webarena' and kind=='human' and condition=='trace' and original:
    # The actor hash uses the true study run, not participant row order.
    if original['actor_id']!=safeid(app+':'+str(r['source_run_id'])) or original['task']!=r['_task']:raise ValueError('WebArena source metadata mismatch')
   if original and kind=='llm' and not balanced:
    sourcepath=actor_paths[app] if r['source_db']==f'actors/{app}' else r['source_db'];cachekey=(app,sourcepath)
    if cachekey not in source_cache:
     if not Path(sourcepath).is_file():raise FileNotFoundError('Cannot validate prediction trace source: '+sourcepath)
     source_rows=fetch(sourcepath,'SELECT * FROM '+('surveys' if app=='webarena' else 'survey_responses'))
     source_cache[cachekey]={sid(v['session_id']):v for v in source_rows}
    source_row=source_cache[cachekey].get(r['_sid'])
    if source_row is None:raise ValueError('Prediction source survey missing in its declared database')
    answer=json.loads(source_row['answers_json']) if app=='webarena' else source_row
    if any(answer.get(k)!=original[d] for d,k in zip(DIMS,KEYS[app])):raise ValueError('Packaged original differs from declared LLM trace source')
   if why:
    unmatched+=1;excluded.append(dict(source_database=p,row=n,reason=why,source_session_id=r.get('_sid')));continue
   tasks=[original['task']] if original else ([r['_task']] if r.get('_task') else (['T1','T2'] if app=='webarena' else ['main']))
   identity=(r.get('_sid'),r['_evaluator'],round_id,r['_run'],r.get('_task')) if condition=='trace' else (r['_evaluator'],r['_run'],r.get('_task'))
   if balanced and r['is_duplicate']:identity+=('padding',r['id'])
   if identity in seen:
    excluded.append(dict(source_database=p,row=n,reason='duplicate evaluator/round/run/source key; explicit duplicate excluded'));unmatched+=1;continue
   seen.add(identity)
   vals=[r['_answers'].get(k) for k in KEYS[app]];missing+=sum(v not in (1,2,3,4,5) for v in vals)
   for task in tasks:
    for d,v in zip(DIMS,vals):
     x=original[d] if original else np.nan
     evaluations.append(dict(application=app,task=task,actor_id=original['actor_id'] if original else None,actor_session_id=r['_sid'],evaluator_type=kind,evaluator_id=r['_evaluator'],model_name=r.get('_model',''),evaluation_round=round_id,run_id=r['_run'],experimental_condition=condition,dimension=d,original_actor_score=x,evaluator_score=v if v in (1,2,3,4,5) else np.nan,original_trace_id=r['_sid'],response_id=r['source_response_id'] if balanced else safeid(p+':'+str(n)),source_database=p,is_duplicate=bool(r.get('is_duplicate',False)),protocol=r.get('_protocol','human_trace_replay' if condition=='trace' else 'human_general_description'),pairing='verified_actor' if original else 'independent_population'))
   matched+=1;used.append((r.get('_sid'),r['_evaluator']))
  counts=pd.Series([v[0] for v in used if v[0] is not None]).value_counts()
  audits.append(dict(**meta,schema_mapping=mapping,n_source_responses=total,n_padding_rows=sum(r.get('is_duplicate',0) for r in raw),n_accepted_responses=matched,n_unmatched_or_excluded=unmatched,n_missing_questionnaire_values=missing,n_evaluators=len(set(v[1] for v in used)),n_evaluated_actor_sessions=len(set(v[0] for v in used if v[0] is not None)),n_rounds=1,n_runs=len(set(r.get('_run') for r in raw)),model_names=';'.join(sorted(set(r.get('_model','') for r in raw))),responses_per_actor_min=counts.min() if len(counts) else np.nan,responses_per_actor_max=counts.max() if len(counts) else np.nan,pairing='verified_actor' if condition=='trace' else 'independent_population'))
 return pd.DataFrame(orig),pd.DataFrame(evaluations),pd.DataFrame(audits),pd.DataFrame(excluded)
