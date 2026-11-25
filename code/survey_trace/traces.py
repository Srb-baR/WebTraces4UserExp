"""Original DB trace readers. Survey answers are never selected into trace payloads."""
import csv
import json
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'full-trace-role-context-time-blind-v2'
TIME_KEY = re.compile(r'(^|_)(time|timestamp|duration|latency|elapsed|ts|timer|deadline|timeout)(_|$)|(_at$)|^since_previous|^client_at$|^received_at$', re.I)
PRIVATE = {'prolific_pid', 'prolific_participant_id', 'prolific_study_id', 'prolific_session_id',
           'token_hash', 'password_hash', 'completion_code'}
SURVEY_KEYS = {'answers_json', 'instrument_json', 'survey_responses', 'surveys', 'survey_answers', 'survey'}


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def clean(value):
    """Remove recording timing recursively, including nested JSON fields."""
    if isinstance(value, dict):
        return {k: clean(decode(v) if k.endswith('_json') else v) for k, v in value.items()
                if not TIME_KEY.search(k) and k.lower() not in PRIVATE
                and k.lower() not in SURVEY_KEYS and not k.lower().startswith('survey_')}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


@contextmanager
def database(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def exists(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def session_ids(app, source):
    with database(source) as c:
        if app == 'testreco':
            result = c.execute("""SELECT s.id FROM study_sessions s WHERE s.simulated_session_id IS NULL
                AND s.status='completed' AND EXISTS (SELECT 1 FROM survey_responses r WHERE r.session_id=s.id)
                AND (SELECT COUNT(*) FROM learning_steps l WHERE l.session_id=s.id)=10 ORDER BY s.id""")
        elif app == 'quizcomp':
            result = c.execute("""SELECT s.id FROM study_sessions s WHERE s.status='completed'
                AND EXISTS (SELECT 1 FROM survey_responses r WHERE r.session_id=s.id)
                AND EXISTS (SELECT 1 FROM compose_attempts a WHERE a.session_id=s.id) ORDER BY s.id""")
        else:
            if exists(c,'study_rounds'):
                result = c.execute("""SELECT s.id FROM sessions s LEFT JOIN study_rounds r ON r.session_id=s.id
                    WHERE EXISTS (SELECT 1 FROM surveys v WHERE v.session_id=s.id)
                    ORDER BY COALESCE(r.run_id,s.id),r.full_task_no,s.id""")
            else:
                result = c.execute("SELECT s.id FROM sessions s WHERE EXISTS (SELECT 1 FROM surveys r WHERE r.session_id=s.id) ORDER BY s.id")
        return [r[0] for r in result]


def testreco(source, session_id):
    app = ROOT / 'code/TestReco'
    with (app / 'Simulation_app/QA.json').open(encoding='utf-8') as f:
        bank = json.load(f)
    bank = [q for q in bank['questions'] if q.get('topic') == 'Fundamental Mathematics']
    by_id = {str(q['id']): q for q in bank}
    with database(source) as c:
        session = rows(c, 'SELECT * FROM study_sessions WHERE id=?', (session_id,))[0]
        attempts = rows(c, 'SELECT * FROM attempts WHERE session_id=? ORDER BY id', (session_id,))
        steps = rows(c, 'SELECT * FROM learning_steps WHERE session_id=? ORDER BY step_index', (session_id,))
        calls = rows(c, 'SELECT * FROM orchestrator_calls WHERE session_id=? ORDER BY step_index,id', (session_id,))
        education = rows(c, '''SELECT pd.education_level FROM users u JOIN prolific_demographics pd
            ON pd.prolific_participant_id=u.username WHERE u.id=?''', (session['user_id'],))
        stored = rows(c, 'SELECT * FROM simulation_questions WHERE session_id=?', (session_id,)) if exists(c, 'simulation_questions') else []
    persisted = {(r['phase'], r['step_index'], r['question_index']): r for r in stored}
    def question(phase, step, index, attempt=None):
        saved = persisted.get((phase, step, index))
        if saved:
            q = dict(question=saved['question_text'], options=json.loads(saved['options_json']),
                     correct_answer=saved['correct_option_index'], topic=saved.get('topic'), subtopic=saved.get('subtopic'))
            origin = 'simulation_questions'
        else:
            qid = str(attempt['dataset_question_id']) if attempt else str(bank[index]['id'])
            q = by_id.get(qid)
            if q is None:
                raise ValueError('Missing original question content: ' + qid)
            origin = 'Simulation_app/QA.json (same fallback as replay app)'
        options = q.get('options', [])
        correct = int(attempt['correct_option_index'] if attempt else q['correct_answer'])
        chosen = int(attempt['chosen_option_index']) if attempt else None
        if not q.get('question') or not options or not 0 <= correct < len(options):
            raise ValueError('Incomplete question or invalid correct answer at index ' + str(index))
        if chosen is not None and not 0 <= chosen < len(options):
            raise ValueError('Invalid recorded chosen option at index ' + str(index))
        return dict(question_index=index, content_source=origin, question=q, options_indexing='zero-based',
                    correct_option_index=correct, correct_answer=options[correct],
                    chosen_option_index=chosen, chosen_answer=options[chosen] if chosen is not None else None,
                    outcome=('correct' if chosen == correct else 'incorrect') if chosen is not None else 'not answered',
                    attempt=clean(attempt or {}))
    pretest = [question('pretest', None, a['question_index'], a) for a in attempts if a['phase'] == 'pretest']
    learning = []
    for step in steps:
        recorded = [a for a in attempts if a['phase'] == 'learning' and a['step_index'] == step['step_index']]
        selected = decode(step['selected_question_indices_json']) or [a['question_index'] for a in recorded]
        # Preserve every attempt, including repeated answers; also show unanswered recommendations.
        qs = [question('learning', step['step_index'], a['question_index'], a) for a in recorded]
        qs += [question('learning', step['step_index'], i) for i in selected if not any(a['question_index'] == i for a in recorded)]
        strategies = [{k: r.get(k) for k in ('selected_strategy', 'action')} for r in calls if r['step_index'] == step['step_index']]
        if not strategies: strategies=[dict(action=step['action'],selected_strategy={0:'Performance',1:'Gap',2:'Aptitude'}.get(step['action']))]
        learning.append(dict(step=clean(step), recommendation_strategies=strategies, questions=qs))
    if len(pretest) != session['pretest_num_questions']:
        raise ValueError('Recorded pretest count differs from session metadata')
    if sum(len([q for q in s['questions'] if q['chosen_option_index'] is not None]) for s in learning) != len([a for a in attempts if a['phase'] == 'learning']):
        raise ValueError('Learning attempt coverage mismatch')
    return dict(application='TestReco', session={k: session[k] for k in ('topic','status','pretest_num_questions','pretest_correct','pretest_mastery_init','final_mastery','final_accuracy','total_steps_planned','questions_per_step')},
                education_level=education[0]['education_level'] if education else None,
                pretest=pretest, learning_steps=learning), dict(pretest_attempts=len(pretest), learning_steps=len(steps),
                learning_attempts=len([a for a in attempts if a['phase']=='learning']), question_bank=str(app/'Simulation_app/QA.json'))


def csv_bank(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        sample = f.read(4096); f.seek(0)
        dialect = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=';,|\t,')
        result = {}
        for index, r in enumerate(csv.DictReader(f, delimiter=dialect.delimiter)):
            # The original generator renames dataset id to mcq_id, then uses df.index as id.
            qid = int(r['id']) if 'mcq_id' in r else index
            options = [r.get(a) or r.get(b) for a,b in [('option_a','correct_answer'),('option_b','answer2'),('option_c','answer3'),('option_d','answer4')]]
            result[qid] = dict(id=qid, dataset_question_id=r.get('mcq_id') or r.get('id'), question=r['question'], options=[v for v in options if v],
                               correct_answer=r.get('correct_option') or r.get('correct_answer'),
                               topic=r.get('topic_name'), difficulty=r.get('difficulty') or r.get('difficulty_level'))
        return result


def quizcomp(source, session_id):
    app = ROOT / 'code/QuizComp'
    with database(source) as c:
        attempts = rows(c, 'SELECT * FROM compose_attempts WHERE session_id=? ORDER BY ts,id', (session_id,))
        events = rows(c, 'SELECT * FROM event_logs WHERE session_id=? ORDER BY ts,id', (session_id,))
    timeline = [(a['ts'], 0, a['id'], 'composition', a) for a in attempts]
    timeline += [(e['ts'], 1, e['id'], 'event', e) for e in events if e['event_type'] != 'survey' and e['name'] not in ('proceed_to_survey','study_session_created','study_session_resumed','ended')]
    result = []; banks = {}; references = {}; paths = set(); fallback_attempts=0
    for _, _, _, kind, item in sorted(timeline):
        if kind == 'composition':
            uuid = item.get('data_uuid')
            candidates = [app/'Simulation_app/data'/str(uuid)/('mcqs_'+str(uuid)+'.csv'), app/'data'/str(uuid)/('mcqs_'+str(uuid)+'.csv'), app/'Simulation_app/data/math.csv',app/'data/math.csv']
            path = next((p for p in candidates if p.is_file()), None)
            if path is None:
                raise ValueError('QuizComp question bank missing')
            if path.name=='math.csv': fallback_attempts+=1
            if path not in banks:
                banks[path] = csv_bank(path)
            paths.add(str(path))
            ids = decode(item['mcq_ids_json']) or []
            content = []
            for qid in ids:
                if int(qid) not in banks[path]:
                    raise ValueError('Missing QuizComp question ' + str(qid))
                key = str(path) + ':' + str(qid)
                if key not in references:
                    references[key] = banks[path][int(qid)]
                    if not references[key]['question'] or not references[key]['options'] or not references[key]['correct_answer']:
                        raise ValueError('Incomplete QuizComp question content: '+str(qid))
                content.append(key)
            result.append(dict(kind=kind, record=clean(item), quiz_question_refs=content))
        else:
            result.append(dict(kind=kind, record=clean(item)))
    # Keep the recorded stop/validate/download actions; omit the survey form and its responses.
    final = [dict(kind='event',record=clean(e)) for e in events if e['name']=='proceed_to_survey']
    return dict(application='QuizComp',topic_order=['Differentiation','Integration','Fundamental Mathematics','Complex Numbers','Optimization','Differential Equations','Probability','Linear Algebra','Numerical Methods','Discrete Mathematics'],
                difficulty_order=['Very Easy (L1)','Easy (L2)','Medium (L3)','Medium (L4)','Hard (L5)','Very Hard (L6)'],
                number_of_attempts=len(attempts), question_dictionary=references, timeline=result, final_actions=final), dict(composition_attempts=len(attempts), events=len(result)-len(attempts)+len(final), question_occurrences=sum(len(decode(a['mcq_ids_json']) or []) for a in attempts), question_banks=sorted(paths),base_csv_fallback_attempts=fallback_attempts,question_id_mapping='Original generator id=df.index, dataset id retained as mcq_id')


class PageText(HTMLParser):
    """Lossless text/control extraction; skip scripts/styles and explicitly hidden nodes."""
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack=[]; self.parts=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        hidden = (self.stack[-1][1] if self.stack else False) or tag in ('script','style','head','noscript') or 'hidden' in attrs or attrs.get('aria-hidden')=='true' or 'display:none' in attrs.get('style','').replace(' ','') or attrs.get('type')=='hidden' or tag=='time' or bool(re.search(r'(^|[-_ ])(timer|elapsed|countdown|duration|timestamp)([-_ ]|$)',attrs.get('id','')+' '+attrs.get('class',''),re.I))
        if tag not in ('input','img','br','hr','meta','link','source','wbr','area','base','embed','param','track','col'):
            self.stack.append((tag,hidden))
        if not hidden:
            for k in ('alt','aria-label','placeholder','title','value'):
                if attrs.get(k): self.parts.append(attrs[k])
    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:]
                break
    def handle_data(self, data):
        if not (self.stack[-1][1] if self.stack else False) and data.strip(): self.parts.append(data.strip())


def webarena_legacy_pages(source, session_id):
    with database(source) as c:
        session = rows(c,'SELECT intent,config_json FROM sessions WHERE id=?',(session_id,))[0]
        events = rows(c,'SELECT rowid AS recording_order,* FROM events WHERE session_id=? ORDER BY received_at,rowid',(session_id,))
        submissions = rows(c,'SELECT kind,payload_json FROM submissions WHERE session_id=? ORDER BY created_at,id',(session_id,))
        tasks = rows(c,'SELECT task,name,outcome FROM tasks WHERE session_id=? ORDER BY task',(session_id,))
        round_link=rows(c,'SELECT run_id,full_task_no FROM study_rounds WHERE session_id=?',(session_id,)) if exists(c,'study_rounds') else []
    snapshots={}; snapshot_ids={}; timeline=[]; excluded=Counter(); kinds=Counter(); limitations=Counter()
    for event in events:
        payload=decode(event['payload_json'])
        phase=str(payload.get('phase','')) if isinstance(payload,dict) else ''
        url=str(payload.get('url','')) if isinstance(payload,dict) else ''
        # Cut at the first survey screen: captured form values must never enter a prompt.
        if event['kind']=='survey_submitted' or phase in ('survey','done') or url.startswith(('study:survey','study:done')):
            excluded['survey_and_later_events']=len(events)-sum(kinds.values()); break
        if isinstance(payload,dict) and event['kind']=='study_screen':
            text=payload.get('visible_text','')
            if ('How successful do you feel' in text or 'Post-task questionnaire' in text or 'Post-study survey' in text):
                excluded['survey_and_later_events']=len(events)-sum(kinds.values()); break
        safe=clean(payload)
        if isinstance(payload,dict):
            if payload.get('html_omitted'): limitations['html_omitted_by_original_recorder']+=1
            visible=payload.get('visible_text','')
            if len(visible)>=30000 and not payload.get('html'):
                limitations['possibly_truncated_original_page_text']+=1
            if 'visible_text' in safe:
                safe['visible_text']=re.sub(r'(?im)^.*(?:elapsed time|time spent|time remaining|total time|response time|generation time)\s*[:=].*$', '[interaction timing withheld]',safe['visible_text'])
        if isinstance(safe,dict) and 'html' in safe:
            parser=PageText();parser.feed(safe.pop('html') or '')
            safe['document_text_and_controls']='\n'.join(parser.parts)
        if event['kind'] in ('dom_snapshot','page_snapshot','study_screen'):
            # Exact structural deduplication only: every occurrence remains in the timeline.
            key=json.dumps(safe,ensure_ascii=False,sort_keys=True)
            import hashlib
            digest=hashlib.sha256(key.encode()).hexdigest()
            if digest not in snapshot_ids:
                snapshot_ids[digest]='page-'+str(len(snapshot_ids)+1)
                snapshots[snapshot_ids[digest]]=safe
            safe={'snapshot_ref':snapshot_ids[digest]}
        timeline.append(dict(order=len(timeline)+1,task=event['task'],site=event['site'],kind=event['kind'],page_id=event['page_id'],seq=event['seq'],payload=safe))
        kinds[event['kind']]+=1
    config=decode(session['config_json'])
    # Only participant-facing task parameters, never credentials or server configuration.
    config={k:v for k,v in config.items() if k in ('product','rating','subreddit','forum_path','intent','full_tasks','full_task_no','interface')}
    return dict(application='WebArena',intent=session['intent'],task_configuration=clean(config),tasks=tasks,
                page_dictionary=snapshots,events=timeline,submissions=clean(submissions)), dict(study_run_id=round_link[0]['run_id'] if round_link else None,full_task_no=round_link[0]['full_task_no'] if round_link else decode(session['config_json']).get('full_task_no'),unit='One complete shopping + Reddit activity followed by its survey; task 1/2 within events means shopping/Reddit stages, not full-activity number.',recorded_events=len(events),included_events=len(timeline),excluded=dict(excluded),event_kinds=dict(kinds),original_recording_limitations=dict(limitations),unique_page_snapshots=len(snapshots),submissions=len(submissions),representation='Full available recorded page text/controls and actions; no screenshots, scripts, CSS or hidden DOM. Identical snapshots referenced without dropping occurrences.')


# Passive recorder events do not describe an intentional website action.
WEB_PASSIVE_KINDS = {
    'dom_snapshot', 'page_snapshot', 'pointermove', 'mousemove', 'heartbeat',
    'visual_archive', 'visual_archive_error', 'screenshot', 'video',
}
WEB_OMITTED_FIELDS = {
    'html', 'visible_text', 'document_text_and_controls', 'frames', 'image', 'images', 'media',
    'viewport', 'user_agent', 'language', 'visibility', 'original_html_chars',
    'html_omitted', 'format',
}
WEB_MEDIA_KEY = re.compile(r'(screenshot|video|image_data|image_url|image_base64|base64|canvas|blob|data_url|media_url|recording_url)', re.I)


def action_payload(value):
    """Text action evidence only, including values, selectors and submitted text."""
    if isinstance(value, dict):
        return {k: action_payload(v) for k,v in value.items()
                if k.lower() not in WEB_OMITTED_FIELDS and not WEB_MEDIA_KEY.search(k)
                and not (isinstance(v,str) and re.match(r'^(data:|blob:)',v,re.I))}
    if isinstance(value, list):
        return [action_payload(v) for v in value
                if not (isinstance(v,str) and re.match(r'^(data:|blob:)',v,re.I))]
    return value


def webarena(source, session_id):
    """All available intentional actions for one shopping + community activity.

    Snapshot/telemetry exclusions are explicit and counted. No action sequence is
    sampled or clipped, and original survey answers are never included.
    """
    with database(source) as c:
        session=rows(c,'SELECT intent,config_json FROM sessions WHERE id=?',(session_id,))[0]
        # Stream the records instead of materializing every page snapshot in memory.
        events=c.execute('SELECT rowid AS recording_order,* FROM events WHERE session_id=? ORDER BY received_at,rowid',(session_id,))
        submissions=rows(c,'SELECT kind,payload_json FROM submissions WHERE session_id=? ORDER BY created_at,id',(session_id,))
        tasks=rows(c,'SELECT task,name,outcome FROM tasks WHERE session_id=? ORDER BY task',(session_id,))
        round_link=rows(c,'SELECT run_id,full_task_no FROM study_rounds WHERE session_id=?',(session_id,)) if exists(c,'study_rounds') else []
        timeline=[];excluded=Counter();kinds=Counter();recorded=0;survey_started=False
        for event in events:
            recorded+=1
            if survey_started:
                excluded['survey_and_later_events']+=1;continue
            payload=decode(event['payload_json'])
            phase=str(payload.get('phase','')) if isinstance(payload,dict) else ''
            url=str(payload.get('url','')) if isinstance(payload,dict) else ''
            screen=str(payload.get('visible_text','')) if isinstance(payload,dict) and event['kind']=='study_screen' else ''
            if (event['kind']=='survey_submitted' or phase in ('survey','done')
                    or url.startswith(('study:survey','study:done'))
                    or any(s in screen for s in ('How successful do you feel','Post-task questionnaire','Post-study survey'))):
                survey_started=True;excluded['survey_and_later_events']+=1;continue
            kind=event['kind']
            if kind in WEB_PASSIVE_KINDS or re.search(r'(screenshot|video|visual_archive)',kind,re.I):
                excluded[kind]+=1;continue
            safe=action_payload(clean(payload))
            timeline.append(dict(order=len(timeline)+1,task=event['task'],site=event['site'],kind=kind,payload=safe))
            kinds[kind]+=1
    config=decode(session['config_json'])
    config={k:v for k,v in config.items() if k in ('product','rating','subreddit','forum_path','intent','full_tasks','full_task_no','interface')}
    # Only task submissions, never a survey stored in the submissions table.
    safe_submissions=action_payload(clean([s for s in submissions if not re.search(r'survey|questionnaire',str(s['kind']),re.I)]))
    trace=dict(application='WebArena',intent=session['intent'],task_configuration=action_payload(clean(config)),
               tasks=clean(tasks),events=timeline,submissions=safe_submissions)
    audit=dict(study_run_id=round_link[0]['run_id'] if round_link else None,
               full_task_no=round_link[0]['full_task_no'] if round_link else config.get('full_task_no'),
               unit='One complete shopping + Reddit activity followed by its survey; event task 1/2 denotes shopping/Reddit stages.',
               recorded_events=recorded,included_events=len(timeline),excluded=dict(excluded),event_kinds=dict(kinds),
               submissions=len(safe_submissions),media_included=False,page_snapshots_included=False,
               representation='Text-only end-to-end action log: navigation, clicks, key/input/change events, copy/paste, form states, task feedback and submissions. No page snapshots, images, videos, pointer movements or heartbeats. No action sampling or truncation.')
    return trace,audit


def pack_repeated_text(value):
    """Reversible dictionary encoding, without summarizing, clipping or sampling text."""
    counts=Counter()
    def pieces(text):
        return text.splitlines(keepends=True) if len(text)>512 else [text]
    def count(v):
        if isinstance(v,dict):
            for item in v.values():count(item)
        elif isinstance(v,list):
            for item in v:count(item)
        elif isinstance(v,str):
            counts.update(p for p in pieces(v) if len(p)>=80)
    count(value)
    lookup={s:'text-'+str(i+1) for i,s in enumerate(s for s,n in counts.items() if n>1)}
    def encode(v):
        if isinstance(v,dict):return {k:encode(item) for k,item in v.items()}
        if isinstance(v,list):return [encode(item) for item in v]
        if isinstance(v,str):
            parts=pieces(v)
            encoded=[{'text_ref':lookup[p]} if p in lookup else p for p in parts]
            if len(parts)==1:return encoded[0]
            if any(isinstance(p,dict) for p in encoded):return {'concatenate_text_segments':encoded}
        return v
    return dict(encoding='Reversible text dictionary. Replace text_ref with the dictionary string; concatenate_text_segments means concatenate all segments in order, with no extra characters.',
                text_dictionary={reference:text for text,reference in lookup.items()},trace=encode(value))
