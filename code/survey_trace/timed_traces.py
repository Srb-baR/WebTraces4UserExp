"""Add recorded timing to v2 evidence without changing its non-timing content."""
import copy
import re
from . import traces

VERSION='full-trace-role-context-time-visible-v1'


def timing_only(value):
    """Extract timing fields recursively; never recover private/survey content."""
    if isinstance(value,dict):
        result={}
        for key,raw in value.items():
            if key.lower() in traces.PRIVATE or key.lower() in traces.SURVEY_KEYS or key.lower().startswith('survey_'):continue
            item=traces.decode(raw) if key.endswith('_json') else raw
            if traces.TIME_KEY.search(key):
                # Timing values should be scalars or timing structures; no survey payloads.
                result[key]=item
            elif isinstance(item,(dict,list)):
                child=timing_only(item)
                if child:result[key]=child
        return result
    if isinstance(value,list):
        # Preserve occurrence alignment, even where some elements have no timings.
        result=[timing_only(v) for v in value]
        return result if any(v for v in result) else []
    return {}


def merge_timing(original,raw):
    addition=timing_only(raw)
    result=copy.deepcopy(original)
    def merge(target,extra):
        if isinstance(target,dict) and isinstance(extra,dict):
            for key,value in extra.items():
                if key in target and isinstance(target[key],(dict,list)):merge(target[key],value)
                else:target[key]=value
        elif isinstance(target,list) and isinstance(extra,list):
            for a,b in zip(target,extra):merge(a,b)
    merge(result,addition)
    return result


def completed_webarena_ids(source):
    with traces.database(source) as c:
        rows=traces.rows(c,"""SELECT r.session_id,r.run_id,r.full_task_no,u.prolific_pid
            FROM study_rounds r JOIN study_runs u ON u.id=r.run_id
            WHERE r.run_id IN (SELECT r.run_id FROM study_rounds r JOIN surveys v USING(session_id)
            JOIN study_runs u ON u.id=r.run_id JOIN sessions s ON s.id=r.session_id
            WHERE u.completed_at IS NOT NULL AND s.phase='done' AND s.completed_at IS NOT NULL
            AND (SELECT count(*) FROM tasks t WHERE t.session_id=r.session_id)=2
            AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.session_id=r.session_id AND t.ended_at IS NULL)
            GROUP BY r.run_id HAVING count(*)=2 AND count(DISTINCT r.full_task_no)=2)
            ORDER BY r.run_id,r.full_task_no""")
    if len(rows)!=106 or len({r['run_id'] for r in rows})!=53 or len({r['prolific_pid'] for r in rows})!=53:
        raise ValueError('Expected the 53 original actors completing both tasks and both surveys (106 activity sessions)')
    return [r['session_id'] for r in rows]


def testreco(source,sid):
    base,audit=traces.testreco(source,sid)
    with traces.database(source) as c:
        session=traces.rows(c,'SELECT * FROM study_sessions WHERE id=?',(sid,))[0]
        attempts=traces.rows(c,'SELECT * FROM attempts WHERE session_id=? ORDER BY id',(sid,))
        steps=traces.rows(c,'SELECT * FROM learning_steps WHERE session_id=? ORDER BY step_index',(sid,))
        calls=traces.rows(c,'SELECT * FROM orchestrator_calls WHERE session_id=? ORDER BY step_index,id',(sid,))
    by_attempt={r['id']:r for r in attempts};by_step={r['step_index']:r for r in steps}
    base['session']=merge_timing(base['session'],session)
    def add_question(q):
        if q['attempt']:
            q['attempt']=merge_timing(q['attempt'],by_attempt[q['attempt']['id']])
    for q in base['pretest']:add_question(q)
    for step in base['learning_steps']:
        index=step['step']['step_index'];step['step']=merge_timing(step['step'],by_step[index])
        own=[r for r in calls if r['step_index']==index]
        for strategy,call in zip(step['recommendation_strategies'],own):
            strategy.update(timing_only(call))
        for q in step['questions']:add_question(q)
    audit.update(timing_visible=True,timed_attempts=len(attempts),timed_steps=len(steps),timed_recommendation_calls=len(calls))
    return base,audit


def quizcomp(source,sid):
    base,audit=traces.quizcomp(source,sid)
    with traces.database(source) as c:
        events=traces.rows(c,'SELECT * FROM event_logs WHERE session_id=? ORDER BY ts,id',(sid,))
        attempts=traces.rows(c,'SELECT * FROM compose_attempts WHERE session_id=? ORDER BY ts,id',(sid,))
        session=traces.rows(c,'SELECT * FROM study_sessions WHERE id=?',(sid,))[0]
    by_kind={'event':{r['id']:r for r in events},'composition':{r['id']:r for r in attempts}}
    for occurrence in base['timeline']+base['final_actions']:
        occurrence['record']=merge_timing(occurrence['record'],by_kind[occurrence['kind']][occurrence['record']['id']])
    base['recorded_session_timing']=timing_only(session)
    # Add timing-only occurrences for recorder lifecycle events excluded by v2;
    # never include the survey event or its payload.
    included={r['record']['id'] for r in base['timeline']+base['final_actions'] if r['kind']=='event'}
    extra=[]
    for r in events:
        if r['event_type']=='survey' or r['name'] in ('study_session_created','study_session_resumed') and not timing_only(r):continue
        if r['id'] not in included:
            extra.append(dict(record_id=r['id'],event_type=r['event_type'],name=r['name'],timing=timing_only(r)))
    base['other_recorded_event_timing']=extra
    audit.update(timing_visible=True,timed_event_log_records=len(included)+len(extra),excluded_survey_event_records=sum(r['event_type']=='survey' for r in events),timed_composition_attempts=len(attempts),additional_timing_only_events=len(extra))
    return base,audit


def pack_timing_events(events):
    """Lossless column/row encoding; every timing occurrence remains present."""
    metadata=['event_id','kind','task','site','page_id','seq']
    clocks=list(dict.fromkeys(key for event in events for key in event['timing']))
    columns=metadata+clocks+['payload_timing']
    rows=[[event[key] for key in metadata]+[event['timing'].get(key) for key in clocks]+[event['payload_timing']] for event in events]
    return dict(encoding='Each row is one recorded event. Map values positionally to columns; timing_columns belong to the original event timing record. No events were sampled or dropped.',
                columns=columns,timing_columns=clocks,rows=rows)


def is_survey(event,payload):
    if not isinstance(payload,dict):payload={}
    phase=str(payload.get('phase',''));url=str(payload.get('url',''))
    text=str(payload.get('visible_text','')) if event['kind']=='study_screen' else ''
    return (event['kind']=='survey_submitted' or phase in ('survey','done') or url.startswith(('study:survey','study:done'))
            or any(s in text for s in ('How successful do you feel','Post-task questionnaire','Post-study survey')))


def webarena(source,sid):
    base,audit=traces.webarena(source,sid)
    index=0;timing_events=[]
    with traces.database(source) as c:
        session=traces.rows(c,'SELECT * FROM sessions WHERE id=?',(sid,))[0]
        tasks=traces.rows(c,'SELECT * FROM tasks WHERE session_id=? ORDER BY task',(sid,))
        submissions=traces.rows(c,'SELECT * FROM submissions WHERE session_id=? ORDER BY created_at,id',(sid,))
        for raw in c.execute('SELECT * FROM events WHERE session_id=? ORDER BY received_at,rowid',(sid,)):
            event=dict(raw);payload=traces.decode(event['payload_json'])
            if is_survey(event,payload):break
            kind=event['kind']
            outer={k:v for k,v in event.items() if k!='payload_json'}
            if kind in traces.WEB_PASSIVE_KINDS or re.search(r'(screenshot|video|visual_archive)',kind,re.I):
                # No page text, media, new action payloads or pointer positions:
                # only recording timestamps/durations and occurrence identifiers.
                timing_events.append(dict(event_id=event['id'],kind=kind,task=event['task'],site=event['site'],page_id=event['page_id'],seq=event['seq'],
                                          timing=timing_only(outer),payload_timing=traces.action_payload(timing_only(payload))))
                continue
            if index>=len(base['events']):raise ValueError('Timing/action coverage mismatch')
            action=base['events'][index]
            if (action['kind'],action['task'],action['site'])!=(kind,event['task'],event['site']):raise ValueError('Timing/action order mismatch')
            expected=traces.action_payload(traces.clean(payload))
            if action['payload']!=expected:raise ValueError('Timing would alter the non-timing payload')
            action.update(timing_only(outer))
            action['payload']=traces.action_payload(merge_timing(action['payload'],payload))
            index+=1
    if index!=len(base['events']):raise ValueError('Missing action timings')
    for i,task in enumerate(base['tasks']):base['tasks'][i]=merge_timing(task,tasks[i])
    safe_submissions=[r for r in submissions if not re.search(r'survey|questionnaire',str(r['kind']),re.I)]
    for i,r in enumerate(safe_submissions):
        base['submissions'][i]=traces.action_payload(merge_timing(base['submissions'][i],r))
    base['recorded_session_timing']=timing_only(session)
    base['other_recorded_event_timing']=pack_timing_events(timing_events)
    audit.update(timing_visible=True,timed_actions=index,additional_timing_only_events=len(timing_events),
                 representation=audit['representation']+' All pre-survey event timing is included: omitted page/passive/media events have timing-only entries, without their contents.')
    return base,audit


def build(app,source,sid):
    trace,audit=globals()[app](source,sid)
    trace['timing_interpretation']=dict(source='Recorded database values; missing/null timings stay missing.',
        units='Fields ending _ms use milliseconds; _s or _seconds use seconds. WebArena client_at/received_at numeric clocks are Unix seconds; ISO/SQL datetime strings retain their original timezone convention (unspecified if absent).',
        scope='Session creation/completion can include consent or survey phases; task/attempt/step timing defines the activity where available. Duration is evidence, not proof of felt time pressure or a deadline. No unrecorded clicks, page visits, times or limits are invented.')
    audit['timing_protocol']=VERSION
    return trace,audit
