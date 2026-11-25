import json,os,re,time
import streamlit as st
import streamlit.components.v1 as components
from html import escape
import replay,storage,tracking,prolific
from journey import chapters,clean_replay_html,evidence_card
from pathlib import Path
import base64
from survey import ITEMS
st.set_page_config(page_title='Web interaction Experience Reconstructed',layout='wide')
st.title('Web interaction Experience Reconstructed')
storage.init();token=st.query_params.get('observer','')
try:prolific_settings=prolific.settings()
except ValueError as error:st.error(str(error));st.stop()
if not token:
 if st.session_state.get('entry_page','introduction')=='introduction':
  st.subheader('Observe someone else’s web experience')
  st.write('You will observe two recorded tasks completed by the same original participant. You will not carry out the shopping tasks yourself, browse the live shops, or publish a Reddit post.')
  st.markdown("""The original participant was asked to:
1. Find **Minecraft - Bedrock Edition PS4** on OneStopShop, collect review titles with the requested star rating, and publish those titles in the assigned Reddit community.
2. Repeat the shopping-and-posting task for **Macool Girls Summer Clothes Cute Unicorns Mermaid Kids Toddler Stripes Short Sleeves**.

Each replay shows the exact product, requested rating, and community for that task. The goal was to collect the short **review titles**, not the longer review bodies.""")
  st.write('You will see the original study instructions, descriptions of what the participant clicked, searched for, typed, copied and submitted, and available screenshots or reconstructed views of the websites they visited. Each view explains whether it is archived evidence or a later reconstruction. Some details were not captured. The original participant’s questionnaire answers are not shown.')
  st.write('First observe task 1 and answer its questionnaire, then observe task 2 and answer the same questions. Imagine how you would have felt performing the recorded actions: for example, how satisfied or frustrated you would have been. Rate that experience, rather than this replay website.')
  st.write('Use Previous, Next, or the page list to explore the journey. You may go to the questionnaire at any time, even without viewing every page. We record which pages you viewed and whether you skipped ahead.')
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
 st.write('Participation is voluntary. After you consent, we record your page navigation, clicks, scrolling, copied text, form changes, viewing and focus times, questionnaire edits and submitted answers. These records are linked to your participant ID and the two assigned recordings for research analysis. Do not enter unrelated personal information.')
 st.write('You may stop by closing the page. Skipping to a questionnaire is allowed and is recorded. Nothing you enter here changes the original participant’s recording.')
 with st.form('consent'):
  st.caption('Your participant details have been received automatically from Prolific.')
  consent=st.checkbox('I have read the study information and consent to participation and the interaction recording described above.')
  go=st.form_submit_button('Begin observation')
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
 st.write('This replay now records navigation, clicks, scrolling, copied text, form and questionnaire edits, focus and viewing times, submitted answers, and whether you skip pages. Participation is voluntary; you can stop by closing the page.')
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
st.caption(f"Recorded task {s.get('full_task_no') or 1} · Shopping and Reddit")
if s['phase']=='review':
 trace=json.loads(s['trace_json']);timeline=chapters(trace)
 st.markdown('**Your task in this recorded experience**')
 product=trace['product']
 rating_match=re.search(r'reviews with (.+?) rating',trace['intent'],flags=re.I)
 rating=rating_match.group(1) if rating_match else 'the star rating specified below'
 st.write('The original participant was asked to complete the following steps. You will observe their recorded actions:')
 st.markdown(f"""1. **Find the product on OneStopShop**, the shopping website: **{product}**.
2. **Open its customer reviews** and find the individual reviews rated **{rating}**. Use each review’s own star rating, not the product’s overall average.
3. **Collect only the titles of those matching reviews.** A review title is the short heading above the longer review text. Do **not** copy the review body (the detailed comment), reviewer’s name, or reviews with a different star rating. Save one review title per line in the study form.
4. **Go to Reddit and create a post in {trace.get('forum_path','/f/gaming')}.** Use the post title shown below. Put the collected review titles in the post’s body, then submit the post.""")
 st.write('**Required Reddit post title:**')
 st.text('real user feedback on '+product)
 if not rating_match:st.write('Original task instruction: '+trace['intent'])
 st.caption('You are reviewing a recorded experience; you do not need to visit the live websites or create a new post. After this task’s replay, answer the questionnaire as if you had performed the recorded actions yourself.')
 criteria=json.loads((Path(__file__).parent/'success_criteria.json').read_text())
 accepted=next((titles for name,titles in criteria['products'].items() if product.strip()==name or product.strip().startswith(name+' ')),None)
 if accepted:
  criteria_key='show_success_'+s['id']
  def toggle_success_criteria():
   st.session_state[criteria_key]=not st.session_state.get(criteria_key,False)
   storage.log(s['id'],'success_criteria_toggled',{'visible':st.session_state[criteria_key],'version':criteria['version'],'product':product,'accepted_titles':accepted})
  st.markdown('<style>[class*="st-key-success_button_"] button {background-color:#d92535!important;border-color:#d92535!important;color:white!important}</style>',unsafe_allow_html=True)
  st.button('Hide success criteria' if st.session_state.get(criteria_key) else 'What counts as a successful task?',key='success_button_'+s['id'],type='primary',on_click=toggle_success_criteria)
  if st.session_state.get(criteria_key):
   with st.container(border=True):
    st.markdown('**Successful shopping task**')
    st.write(f'Find {product} and save all of the accepted review titles below, one per line. Collect only these titles, not the longer review bodies or titles from other products or ratings.')
    st.code('\n'.join(accepted),language=None)
    st.markdown('**Successful Reddit post**')
    st.write(f'Publish a post in {trace.get("forum_path","/f/gaming")} with a title starting with “real user feedback on” followed by the product name. Use the exact title shown below. Its body should contain these same accepted review titles. Filling in the form alone does not complete the task; the post must be submitted successfully.')
    st.code('real user feedback on '+product,language=None)
    st.caption('These are the researcher-confirmed expected results. Compare them with the recorded participant’s actions; the participant may not have achieved them. You do not need to create a post yourself.')
 if not timeline:st.error('No recorded page visits are available.');st.stop()
 key='chapter_v5_'+s['id'];st.session_state.setdefault(key,0)
 nav,main=st.columns([1,3])
 with nav:
  st.subheader('The participant’s journey')
  def move(delta):
   old=st.session_state[key];new=max(0,min(len(timeline)-1,old+delta))
   storage.log(s['id'],'navigation',{'control':'next' if delta>0 else 'previous','from':old,'to':new})
   st.session_state[key]=new
  def select_visit():
   storage.log(s['id'],'navigation',{'control':'page_list','to':st.session_state[key]})
  back,forward=st.columns(2)
  back.button('Previous',on_click=move,args=(-1,),disabled=st.session_state[key]==0)
  forward.button('Next',on_click=move,args=(1,),disabled=st.session_state[key]==len(timeline)-1)
  index=st.radio('Page visits',range(len(timeline)),format_func=lambda i:f"{i+1}. {timeline[i]['title'][:65]}",key=key,on_change=select_visit)

 seen='seen_chapters_v5_'+s['id'];st.session_state.setdefault(seen,set(storage.viewed_indices(s['id']))).add(index)
 if st.session_state.get('last_chapter')!=(s['id'],index):
  storage.log(s['id'],'view_chapter',{'index':index,'url':timeline[index]['url']});st.session_state['last_chapter']=(s['id'],index)
 c=timeline[index]
 with main:
  st.subheader(c['title'])
  st.caption(f"Visit {index+1} of {len(timeline)} · Stage {c.get('task') or 'transition'}")
  st.markdown('### '+('Actions leading to this view' if c.get('action_source_url') else 'What the participant did'))
  if c.get('action_source_url'):st.caption('These actions occurred on the preceding page; this view shows the observed result.')
  if c.get('submission'):
   for action in c['actions']:st.write('• '+action['sentence'])
   if 'titles' in c['submission']:
    st.write('The participant returned to the study and saved these review titles for the Reddit post. This marks the end of the shopping stage. The saved text is shown below; clipboard-copy actions, when recorded, appear on the preceding product page.')
   else:st.write('The study recorded the published Reddit post below, completing this task’s posting stage.')
   for label in ('titles','title','body'):
    if c['submission'].get(label):st.text(c['submission'][label])
  elif c['actions']:
   for action in c['actions']:st.write('• '+action['sentence'])
  elif c.get('form_values'):st.write('The final saved post is shown in the actual submission-form layout below. The original drafting sequence was not captured for this view.')
  else:st.write('Viewed this page. Navigation actions are presented with their resulting view in the following steps.')
  image=Path(__file__).parent/'data/prolific_source/visuals'/(c['key']+'.png')
  htmlfile=image.with_suffix('.html')
  post=c.get('archived_post')
  archived=replay.archived_visual(trace,c)
  reference=replay.reference_visual(c['key'])
  if reference and c.get('composer'):
   form_actions=c['actions']+[{'payload':{'name':'submission['+key+']','value':value}} for key,value in c.get('form_values',{}).items() if key in ('title','body')]
   for action in form_actions:
    field=action['payload'].get('name','');value=action['payload'].get('value')
    if field in ('submission[title]','submission[body]') and value is not None:
     pattern=r'(<textarea\b[^>]*name=[\"\']'+re.escape(field)+r'[\"\'][^>]*>).*?(</textarea>)'
     reference=re.sub(pattern,lambda m:m[1]+escape(str(value))+m[2],reference,flags=re.S|re.I)
 
  import hashlib
  evidence_key=hashlib.sha256((trace['id']+'|'+str(c['identity'])+'|'+str(c['start'])).encode()).hexdigest()[:32]
  evidence_image=Path(__file__).parent/'data/prolific_source/study_visuals'/(evidence_key+'.png')
  evidence_meta=evidence_image.with_suffix('.json')
  if archived:
   st.caption('Page data archived during the original session, including available images and styles. PNG preview is rendered from that archived DOM.')
   components.html('<iframe sandbox="" style="width:100%;height:650px;border:1px solid #ccc" srcdoc="'+escape(clean_replay_html(archived['html']),quote=True)+'"></iframe>',height=670)
   if archived['png']:
    with st.expander('Archived visual preview'):st.image(archived['png'])
   if archived['meta'].get('missing_resources'):st.caption(f"Archive reports {len(archived['meta']['missing_resources'])} unavailable resources.")
  elif evidence_image.exists():
   meta=json.loads(evidence_meta.read_text())
   st.caption('Recorded page content rendered with Playwright; '+('original saved study layout.' if meta['kind']=='rendered-historical-study-dom' else 'later reference styling where available. Missing historical images are not recovered.' if meta['kind']=='rendered-recorded-page' else 'reconstructed from recorded actions and text, not an original screenshot.'))
   st.image(str(evidence_image))
  elif post and (c.get('snapshot') or {}).get('html'):
   st.caption('Recorded published-post page and controls, styled with a Reddit website reference. This is the same page on which the following clicks occurred; the live historical post is unavailable.')
   from journey import visual_key
   layout=replay.reference_visual(visual_key(trace.get('forum_path','/f/gaming'),'reddit')) or ''
   styles=''.join(re.findall(r'<style\b[^>]*>.*?</style>',layout,flags=re.S|re.I))
   content='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; form-action \'none\'">'+clean_replay_html(c['snapshot']['html'])+styles+'<style>body{background:white!important;color:#202936!important}img:not([src]){display:none}button[type=submit]{outline:2px solid orange}</style>'
   components.html('<iframe sandbox="" style="width:100%;height:650px;border:1px solid #ccc" srcdoc="'+escape(content,quote=True)+'"></iframe>',height=670)
  elif post:
   st.caption('Archived post reconstructed from the original database. This preserves the recorded title and body; it is not an original screenshot. The live post may no longer exist.')
   title=escape(post.get('title',''));body=escape(post.get('body',''))
   card='<html><body style="margin:0;background:#f4f6f8;color:#202936;font:16px system-ui"><header style="background:#fff;padding:20px;border-bottom:1px solid #ddd;font-weight:700">Postmill · '+escape(trace.get('forum_path','/f/gaming'))+'</header><main style="margin:24px;padding:28px;background:#fff;border:1px solid #dbe1e8;border-radius:8px"><h2>'+title+'</h2><div style="white-space:pre-wrap;line-height:1.7">'+body+'</div></main></body></html>'
   components.html('<iframe sandbox="" style="width:100%;height:380px;border:0" srcdoc="'+escape(card,quote=True)+'"></iframe>',height=400)
  elif c.get('submission'):
   components.html(evidence_card(c),height=400,scrolling=True)
  elif c['site'] in ('shopping','reddit') and image.exists() and not c.get('composer'):
   st.caption('Screenshot of this recorded URL captured later from the live website. It is a visual reference; the participant’s original actions are described above.')
   uri=base64.b64encode(image.read_bytes()).decode()
   components.html('<div style="height:650px;overflow:auto;border:1px solid #ccc"><img style="width:100%" src="data:image/png;base64,'+uri+'"></div>',height=675)
  elif c['site'] in ('shopping','reddit') and (reference or htmlfile.exists()):
   st.caption('Reconstructed website view using a current-site reference, not a historical screenshot. The explanations above describe recorded actions. This view is read-only; scroll to explore it.')
   ids={a['payload'].get('id') for a in c['actions'] if a['kind']=='click'}
   selectors=[f'#{i}' for i in ids if i and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',i)]
   highlight='<style>'+(','.join(selectors)+'{outline:3px solid #ed9400!important;outline-offset:3px}' if selectors else '')+'</style>'
   content='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; form-action \'none\'; base-uri \'none\'">'+(reference or htmlfile.read_text())+highlight
   if selectors:st.caption('Orange outlines mark matching recorded click-target IDs in this reference page.')
   components.html('<iframe sandbox="" style="width:100%;height:650px;border:1px solid #ccc" srcdoc="'+escape(content,quote=True)+'"></iframe>',height=670)
  elif c['site'] in ('shopping','reddit') and image.exists():
   st.caption('Website appearance reference captured from the current WebArena site. The actions above are historical records; this image is not an original-session screenshot. Scroll inside the website view to inspect the page.')
   uri=base64.b64encode(image.read_bytes()).decode()
   components.html('<div style="height:650px;overflow:auto;border:1px solid #ccc;border-radius:8px"><img style="width:100%;display:block" src="data:image/png;base64,'+uri+'"></div>',height=675)
  elif c['snapshot'] and c['snapshot'].get('html'):
   st.caption('Recorded page structure; original images and styles were not archived.')
   content='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; form-action \'none\'; base-uri \'none\'">'+'<style>html,body{background:white!important;color:#202936!important;font:16px system-ui}img:not([src]),img[src=""]{display:none}body{padding:18px}</style>'+clean_replay_html(c['snapshot']['html'])
   components.html('<iframe sandbox="" style="width:100%;height:600px;border:1px solid #ccc" srcdoc="'+escape(content,quote=True)+'"></iframe>',height=620)
  else:components.html(evidence_card(c),height=600,scrolling=True)
  if c.get('snapshot'):
   with st.expander('Text captured during this original visit'):st.text(c['snapshot'].get('visible_text',''))
  st.progress(len(st.session_state[seen])/len(timeline))
 if st.button('Continue to questionnaire',type='primary'):
  storage.open_questionnaire(s['id'],len(timeline))
  st.rerun()
 st.stop()
if st.button('Back to replay'):
 storage.log(s['id'],'back_to_replay',{})
 with storage.connect() as con:con.execute("UPDATE observers SET phase='review' WHERE id=?",(s['id'],))
 st.rerun()
st.subheader('Answer as if you performed the recorded interaction')
st.write('Imagine you were the person taking these actions. Rate that experience, not the usability of this replay viewer.')
with st.form('survey_'+s['id']):
 reviewed=st.checkbox('I understand that I should answer as if I had performed the recorded actions I observed.',key='reviewed_'+s['id'])
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
