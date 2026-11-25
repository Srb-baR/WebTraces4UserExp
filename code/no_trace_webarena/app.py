import json,time
import streamlit as st
import streamlit.components.v1 as components
import storage,tracking,prolific
from no_trace import task_pages
from pathlib import Path
import base64
from survey import ITEMS
st.set_page_config(page_title='Web Interaction Experience Study',layout='wide')
st.title('Web Interaction Experience Study')
storage.init();token=st.query_params.get('observer','')
try:prolific_settings=prolific.settings()
except ValueError as error:st.error(str(error));st.stop()
if not token:
 if st.session_state.get('entry_page','introduction')=='introduction':
  st.subheader('About this study')
  st.write('The objective of this study is to understand how people would experience using a web system to complete a task, including how easy, useful, and satisfying they would find the experience.')
  st.write('You will be introduced to the system through task descriptions and general screenshots of its websites and study platform. Your role is only to observe these materials and imagine yourself using the system. You will not carry out the tasks, interact with the live websites, search for products, copy review titles, or publish any posts.')
  st.write('After viewing the materials for each task, you will be asked to give feedback in a questionnaire. Answer as if you were the person who had performed the described interaction, based on how you think you would have felt during that experience. There are no right or wrong answers; we are interested in your own impressions.')
  st.subheader('Imagine a shopping and community website experience')
  st.write('OneStopShop is a shopping website where you can search for products and read customer reviews. Reddit is a community website where you can create posts in a dedicated community.')
  st.write('Imagine searching for a specified product on OneStopShop and finding reviews with a requested star rating. You may find the product on your first search, or you may need to scroll through more results or browse additional pages. Read the matching reviews and copy their titles to the study platform, one title per line. Then open Reddit in the dedicated community and submit a post containing those titles.')
  st.write('You will see the task instructions and general screenshots of the websites and the original study platform. Use Previous, Next, or the page list to explore them. Answer the questionnaire as if you had performed the interaction yourself, based on how you think you would have felt.')
  st.write('Your participation consists of viewing the study materials and answering the questionnaires. We record your navigation and questionnaire answers. You may go to the questionnaire at any time.')
  if st.button('Continue to consent',type='primary'):
   st.session_state['entry_page']='consent';st.rerun()
  st.stop()
 try:launch_ids=prolific.identity(st.query_params)
 except ValueError as error:st.error(str(error));st.stop()
 resume_token=prolific.launch_token(launch_ids,storage.DB)
 try:existing=storage.session(resume_token)
 except ValueError:existing=None
 if existing:
  st.query_params['observer']=resume_token;st.rerun()
 st.subheader('Consent to participate')
 st.write('Participation is voluntary. After you consent, we record your page navigation, clicks, scrolling, copied text, form changes, viewing and focus times, questionnaire edits and submitted answers. These records are linked to your participant ID and the assigned task descriptions for research analysis. Do not enter unrelated personal information.')
 st.write('You may stop by closing the page. Skipping to a questionnaire is allowed and is recorded. Nothing you enter here changes the task description.')
 with st.form('consent'):
  st.caption('Your participant details have been received automatically from Prolific.')
  consent=st.checkbox('I have read the study information and consent to participation and the interaction recording described above.')
  go=st.form_submit_button('Begin study')
 if go:
  if not consent:st.error('Please confirm consent to begin.')
  else:
   try:
    new_token=storage.assign_pair(*launch_ids,token=resume_token)
   except ValueError as error:st.error(str(error))
   else:
    first=storage.session(new_token)
    with storage.connect() as c:pair=c.execute('SELECT first_id,second_id FROM observer_pairs WHERE first_id=?',(first['id'],)).fetchone()
    storage.record_consent(list(pair))
    st.query_params['observer']=new_token;st.rerun()

 if st.button('Back to introduction'):
  st.session_state['entry_page']='introduction';st.rerun()
 st.stop()

try:s=storage.session(token)
except ValueError as e:st.error(str(e));st.stop()
with storage.connect() as con:
 consent_record=con.execute("SELECT 1 FROM interactions WHERE observer_id=? AND kind='consent_accepted'",(s['id'],)).fetchone()
if not consent_record:
 st.subheader('Consent to interaction recording')
 st.write('This study records navigation, clicks, scrolling, copied text, form and questionnaire edits, focus and viewing times, submitted answers, and whether you skip pages. Participation is voluntary; you can stop by closing the page.')
 agreed=st.checkbox('I consent to participation and the interaction recording described above.')
 if st.button('Continue with recording',disabled=not agreed):
  storage.record_consent([s['id']]);st.rerun()
 st.stop()
screen=(s['id'],s['phase'],st.session_state.get('chapter_v5_'+s['id']) if s['phase']=='review' else None)
previous_screen=st.session_state.get('recorded_screen')
if not previous_screen or previous_screen['screen']!=screen:
 if previous_screen:
  storage.log(previous_screen['screen'][0],'screen_departure',{'phase':previous_screen['screen'][1],'chapter':previous_screen['screen'][2],'elapsed_wall_seconds':max(0,time.time()-previous_screen['at'])})
 storage.log(s['id'],'screen_view',{'phase':s['phase'],'chapter':screen[2]})
 st.session_state['recorded_screen']={'screen':screen,'at':time.time()}
tracking.mount(s,s['phase'],st.session_state.get('chapter_v5_'+s['id']))
if s['phase']=='done':
 next_token=storage.next_task(token)
 if next_token:
  st.success('Your task 1 questionnaire is saved.')
  if st.button('Continue to task 2',type='primary'):
   storage.log(s['id'],'continue_to_task_2',{});storage.begin_second(next_token);st.query_params['observer']=next_token;st.rerun()
 elif storage.pair_completed(s['id']):
  st.success('Thank you. You have completed both questionnaires.')
  st.write('Return to Prolific to complete your submission, or copy the completion code below.')
  st.code(prolific_settings['completion_code'],language=None)
  st.link_button('Complete study on Prolific',prolific.completion_url(prolific_settings['completion_code']),type='primary')
 else:st.info('Please complete both assigned questionnaires before returning to Prolific.')
 st.stop()
if s['phase']=='ready':
 st.info('Complete the first task questionnaire before starting task 2.');st.stop()
st.caption(f"Task {s.get('full_task_no') or 1} · Shopping and Reddit")
if s['phase']=='review':
 task=json.loads(s['trace_json']);timeline=task_pages(task)
 key='chapter_v5_'+s['id'];st.session_state.setdefault(key,0)
 st.session_state[key]=max(0,min(len(timeline)-1,st.session_state[key]))
 nav,main=st.columns([1,3])
 with nav:
  st.subheader('Task overview')
  def move(delta):
   old=st.session_state[key];new=max(0,min(len(timeline)-1,old+delta))
   storage.log(s['id'],'navigation',{'control':'next' if delta>0 else 'previous','from':old,'to':new})
   st.session_state[key]=new
  def select_visit():
   storage.log(s['id'],'navigation',{'control':'page_list','to':st.session_state[key]})
  back,forward=st.columns(2)
  back.button('Previous',on_click=move,args=(-1,),disabled=st.session_state[key]==0)
  forward.button('Next',on_click=move,args=(1,),disabled=st.session_state[key]==len(timeline)-1)
  index=st.radio('Pages',range(len(timeline)),format_func=lambda i:f"{i+1}. {timeline[i]['title']}",key=key,on_change=select_visit)
 seen='seen_chapters_v5_'+s['id'];st.session_state.setdefault(seen,set(storage.viewed_indices(s['id']))).add(index)
 if st.session_state.get('last_chapter')!=(s['id'],index):
  storage.log(s['id'],'view_chapter',{'index':index,'condition':'no_trace'});st.session_state['last_chapter']=(s['id'],index)
 page=timeline[index]
 with main:
  st.subheader(page['title'])
  st.caption(f'Page {index+1} of {len(timeline)}')
  st.markdown(page['description'])
  if page.get('image'):
   st.caption('General website screenshot for context. Imagine using this website to complete the task described above.')
   uri=base64.b64encode(page['image'].read_bytes()).decode()
   components.html('<div style="height:650px;overflow:auto;border:1px solid #ccc"><img alt="General website screenshot" style="width:100%" src="data:image/png;base64,'+uri+'"></div>',height=675)
  st.progress(len(st.session_state[seen] & set(range(len(timeline))))/len(timeline))
 if st.button('Continue to questionnaire',type='primary'):
  storage.open_questionnaire(s['id'],len(timeline));st.rerun()
 st.stop()
if st.button('Back to task overview'):
 storage.log(s['id'],'back_to_replay',{})
 with storage.connect() as con:con.execute("UPDATE observers SET phase='review' WHERE id=?",(s['id'],))
 st.rerun()
st.subheader('Answer as if you performed the interaction')
st.write('Imagine you completed the task described on the shopping and Reddit websites yourself. Answer every question based on that imagined experience.')
with st.form('survey_'+s['id']):
 reviewed=st.checkbox('I understand that I should answer as if I had completed the described task myself.',key='reviewed_'+s['id'])
 answers={}
 for key,question,low,high in ITEMS:
  st.write(question)
  if key=='would_use_again':
   value=st.radio(question,['No','Yes'],index=None,horizontal=True,label_visibility='collapsed',key=s['id']+key);answers[key]=None if value is None else int(value=='Yes')
  else:
   st.caption(f'1 = {low} · 5 = {high}');answers[key]=st.radio(question,[1,2,3,4,5],index=None,horizontal=True,label_visibility='collapsed',key=s['id']+key)
 comments=st.text_area('Comments (optional)',max_chars=10000,key='comments_'+s['id'])
 send=st.form_submit_button('Submit my answers')
error_key='survey_error_'+s['id']
if send:
 st.session_state.pop(error_key,None)
 storage.log(s['id'],'questionnaire_submit_attempt',{'answers':answers,'comments':comments,'acknowledged':reviewed})
 try:
  if not reviewed:raise ValueError('Confirm how you will answer the questionnaire.')
  storage.finish(s['id'],answers,comments)
  next_token=storage.next_task(token)
  if next_token:
   storage.log(s['id'],'continue_to_task_2',{'trigger':'questionnaire_submission'})
   storage.begin_second(next_token);st.query_params['observer']=next_token
  st.rerun()
 except ValueError as error:
  storage.log(s['id'],'questionnaire_validation_error',{'error':str(error)});st.session_state[error_key]=str(error)
if st.session_state.get(error_key):st.error(st.session_state[error_key])
