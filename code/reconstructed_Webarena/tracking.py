from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
import storage
_component=components.declare_component('observer_activity',path=str(Path(__file__).parent/'observer_tracker'))
@st.fragment
def mount(observer,phase,chapter=None):
 key='tracker_ack_'+observer['id']
 result=_component(observer=observer['id'],phase=phase,chapter=chapter,task=observer.get('full_task_no'),ack=st.session_state.get(key,[]),key='observer_activity_'+observer['id'],default=None)
 if result and result.get('events'):
  storage.browser_events(observer['id'],result['events'])
  st.session_state[key]=[e['id'] for e in result['events']]
