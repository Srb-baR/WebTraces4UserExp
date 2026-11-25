"""Separate full-detail variants of the existing evaluator applications."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[2]


def streamlit_app(app,output):
    import streamlit as st
    if not hasattr(st,'cache'):st.cache=st.cache_data
    from simulation_full_trace.evidence import render,format_duration
    original=ROOT/'code'/('TestReco' if app=='testreco' else 'QuizComp')/'Simulation_app/app.py'
    spec=importlib.util.spec_from_file_location('original_'+app,original)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    module.SURVEY_DB_FILE=Path(os.environ.get('REVIEWER_OUTPUT_DIR', str(output)))/'simulation_survey_responses.db'
    old=module.render_replay_page
    if app=='testreco':
        def replay(meta,detail):
            render(app,module.RESULTS_DB_FILE,meta['replay_session_id']);old(meta,detail)
    else:
        def replay(persona):
            render(app,module.SOURCE_DB_FILE,persona['replay_session_id']);old(persona)
    if app=='testreco':
        old_question=module.render_question_review
        def question(review,key):
            ms=review.get('response_time_ms')
            st.caption('Answer time: '+format_duration(ms/1000 if ms else None))
            old_question(review,key)
        module.render_question_review=question
        def pretest(meta,detail):
            module.render_hero('Replay page 1: prequalification test',
                'Review every recorded prequalification question, answer and answer time below.','')
            for idx,attempt in enumerate(detail['pretest_attempts'],start=1):
                st.markdown(f'### Prequalification question {idx}')
                module.render_question_review(attempt,f'pretest_{idx}')
                st.divider()
        module.render_pretest_page=pretest

    if app=='quizcomp':
        old_result=module.render_result_panel
        def result(attempt):
            st.caption('Quiz generation time: '+format_duration(attempt.get('api_time_s')))
            old_result(attempt)
        module.render_result_panel=result
    module.render_replay_page=replay
    module.main()


def webarena_app(output):
    original=ROOT/'code/reconstructed_Webarena'
    source=ROOT/'code/report/data/actors/webarena/study.sqlite'
    os.environ['WEBARENA_REPLAY_SOURCE']=str(source)
    os.environ['RECONSTRUCTED_DB']=str(Path(os.environ.get('REVIEWER_OUTPUT_DIR', str(output)))/'data/full_trace_observers.sqlite')
    os.environ['RECONSTRUCTED_CONFIG']=str(Path(output)/'config.local.json')
    sys.path.insert(0,str(original))
    import replay,journey
    import importlib
    importlib.reload(replay);importlib.reload(journey)
    from survey_trace.timed_traces import completed_webarena_ids
    from simulation_full_trace.evidence import render
    allowed=set(completed_webarena_ids(source))
    original_pairs=replay.available_pairs
    def pairs():return [p for p in original_pairs() if set(p['session_ids'])<=allowed]
    replay.available_pairs=pairs
    original_available=replay.available
    replay.available=lambda:[r for r in original_available() if r['id'] in allowed]
    original_load=replay.load
    def load(sid):
        if sid not in allowed:raise ValueError('Only the 53 complete two-task actors can be replayed.')
        trace=original_load(sid)
        from simulation_full_trace.evidence import is_survey
        activity=[]
        for event in trace['events']:
            if is_survey(dict(event,payload_json=event.get('payload',{}))):break
            activity.append(event)
        trace['events']=activity
        return trace
    replay.load=load
    original_chapters=journey.chapters
    def chapters(trace):
        render('webarena',source,trace['id'])
        return original_chapters(trace)
    journey.chapters=chapters
    runpy.run_path(str(original/'app.py'),run_name='__main__')
