"""Page visits and evidence-based narration, independent of raw event volume."""
import hashlib
from urllib.parse import urlsplit,parse_qs

def site_path(url,site):
 u=urlsplit(url);path=u.path
 prefix='/'+site
 if path==prefix:path='/'
 elif path.startswith(prefix+'/'):path=path[len(prefix):]
 return path

def visual_key(url,site):
 u=urlsplit(url);path=site_path(url,site);return hashlib.sha256((site+'|'+path+'?'+u.query+'#'+u.fragment).encode()).hexdigest()[:24]
def describe(e):
 p=e['payload'];kind=e['kind'];field=p.get('name') or p.get('id') or 'the field';text=' '.join(str(p.get('text') or '').split())[:160];value=p.get('value')
 if kind=='click':
  if text=='Post' and '/f/' in p.get('url','') and '/submit/' not in p.get('url',''):return 'Clicked “Post” in the comment form on the published-post page (no new comment is confirmed by this click alone).'
  if text=='No comments Edit Delete':return 'Clicked the post’s comment/edit/delete control area; the specific control was not recorded.'
  if p.get('id')=='search' or p.get('name')=='q':return 'Clicked the shop’s search box.'
  if text.isdigit() and '/catalogsearch/' in p.get('url',''):return f'Clicked the page {text} control to browse another page of search results.'
  if 'review' in text.lower():return f'Clicked “{text}” to inspect product reviews.'
  if text=='Search':return 'Submitted the product search to find matching items in the shop.'
  if text:return f'Clicked “{text}”.'
  return 'Clicked a page control (its label was not recorded).'
 if kind in ('input','change'):
  if value is None or value=='[redacted]':return None
  if field in ('q','search'):return f'Entered “{value}” in the search box.'
  if 'sort' in field.lower():return f'Changed the sorting control to “{value}”.'
  return f'Entered “{value}” in {field}.'
 if kind=='copy':
  selected=p.get('selection','')
  if not selected:return 'Used Copy; the selected text was not captured.'
  if e.get('site')=='shopping' and ('#reviews' in p.get('url','') or 'review' in field.lower()):return f'Copied the review text “{selected}” from the product’s reviews to the clipboard.'
  return f'Copied “{selected}” to the clipboard.'
 if kind=='paste':
  target='the product search box' if field in ('q','search') else 'the Reddit post title' if field=='submission[title]' else 'the Reddit post body' if field=='submission[body]' else 'the selected field'
  return f'Pasted “{p.get("pasted_text", "") }” into {target}.'
 if kind=='submit':return 'Submitted the search form to display matching products.' if e.get('site')=='shopping' and '/catalogsearch/' in p.get('url','') else 'Submitted the form; the next recorded page shows the resulting state.'
 return None

def chronological_trace(trace):
 """Use browser occurrence time for queued events; retain receipt time for audit."""
 if trace.get('_chronological'):return trace
 result=dict(trace);events=[]
 for original in trace['events']:
  e=dict(original);received=e['received_at'];client=e.get('client_at')
  # Native recorder uses epoch milliseconds. Reject malformed or implausible clocks.
  if isinstance(client,(int,float)) and client>100000000000:
   occurred=client/1000
   if abs(occurred-received)<86400:
    e['server_received_at']=received;e['received_at']=occurred
  events.append(e)
 result['events']=sorted(events,key=lambda e:e['received_at'])
 result['_chronological']=True
 return result

def visited_chapters(trace):
 trace=chronological_trace(trace)
 result=[];current=None;snapshots={};visited=set()
 composer_url="/submit/"+trace.get("forum_path","/f/gaming").rstrip("/").split("/")[-1]
 for e in trace['events']:
  p=e['payload'];url=p.get('url','');site=e.get('site');kind=e['kind']
  if site not in ('shopping','reddit'):continue
  if not url:continue
  identity=(e.get('page_id') or url,site,e.get('task'))
  is_snapshot=kind in ('dom_snapshot','page_snapshot')
  if is_snapshot:snapshots[identity]=p
  first_snapshot=is_snapshot and identity not in visited and p.get('visibility')!='hidden'
  meaningful=first_snapshot or describe(e) is not None or kind=='page_view' or (kind in ('focus','visibilitychange') and p.get('visibility')=='visible')
  if not meaningful:
   # Background heartbeats/snapshots never constitute a navigation or tab return.
   if current is not None and current['identity']==identity:
    current['end']=max(current['end'],e['received_at']);current['raw_count']+=1
    if is_snapshot:current['snapshot']=p
   continue
  if kind=='page_view' and p.get('visibility')=='hidden':continue
  if current is None or current['identity']!=identity:
   current={'identity':identity,'url':url,'site':site,'task':e.get('task'),'start':e['received_at'],'end':e['received_at'],'actions':[],'snapshot':None,'raw_count':0,'title':p.get('title') or ('Shopping website' if site=='shopping' else 'Community website'),'key':visual_key(url,site)}
   current['snapshot']=snapshots.get(identity)
   result.append(current);visited.add(identity)
  current['end']=e['received_at'];current['raw_count']+=1
  if kind in ('dom_snapshot','page_snapshot'):current['snapshot']=p
  sentence=describe(e)
  if sentence:
   action={'sentence':sentence,'kind':kind,'field':p.get('name') or p.get('id'),'payload':p,'at':e['received_at']}
   previous=current['actions'][-1] if current['actions'] else None
   if previous and previous['sentence']==sentence:continue
   if previous and kind in ('input','change') and previous['kind'] in ('input','change') and previous['field']==action['field']:current['actions'][-1]=action
   else:current['actions'].append(action)
 for sub in trace['submissions']:
  result.append({'identity':('submission',sub['kind']), 'site':'study','task':1 if sub['kind']=='gather' else 2,'start':sub['created_at'],'end':sub['created_at'],'title':'Collected review titles' if sub['kind']=='gather' else 'Submitted community post','actions':[],'submission':sub['payload'],'snapshot':None,'raw_count':1,'url':'','key':''})
 # Attach the exact archived post to matching page visits, even after the live post disappears.
 for chapter in result:
  if chapter['site']!='reddit':continue
  snapshot=chapter.get('snapshot') or {}
  chapter['composer']=('/submit/' in chapter['url'] or 'Create submission' in snapshot.get('visible_text','') or any('submission[' in str(a['payload'].get('name','')) for a in chapter['actions']))
  if chapter['composer']:chapter['key']=visual_key(composer_url,'reddit')
  for sub in trace['submissions']:
   payload=sub['payload']
   if sub['kind'] in ('live_post','post') and urlsplit(payload.get('url','')).path==urlsplit(chapter['url']).path:
    chapter['archived_post']=payload
  for event in trace['events']:
   if event['kind']=='native_post_seen' and urlsplit(event['payload'].get('url','')).path==urlsplit(chapter['url']).path:
    chapter['archived_post']=event['payload']
 if not any(c.get('composer') for c in result):
  for sub in trace['submissions']:
   if sub['kind'] not in ('live_post','post'):continue
   first_post=min((c['start'] for c in result if c.get('archived_post')),default=sub['created_at'])
   result.append({'identity':('reconstructed-composer',sub['kind']),'site':'reddit','task':2,'start':first_post-.001,'end':first_post-.001,'title':'Submission form · reconstructed from saved post','actions':[],'snapshot':None,'raw_count':0,'url':composer_url,'key':visual_key(composer_url,'reddit'),'composer':True,'form_values':sub['payload']})
 result.sort(key=lambda x:x['start'])
 filtered=[c for c in result if c['actions'] or c['snapshot'] or c.get('submission') or c.get('composer')]
 merged=[]
 for c in filtered:
  if merged and c['site'] in ('shopping','reddit') and not c.get('composer') and not merged[-1].get('composer') and c['site']==merged[-1]['site'] and c['task']==merged[-1]['task'] and urlsplit(c['url']).path==urlsplit(merged[-1]['url']).path and urlsplit(c['url']).query==urlsplit(merged[-1]['url']).query:
   previous=merged[-1];previous['end']=max(previous['end'],c['end']);previous['raw_count']+=c['raw_count'];previous['actions']+=c['actions']
   if c.get('snapshot'):previous['snapshot']=c['snapshot']
   if c.get('archived_post'):previous['archived_post']=c['archived_post']
  else:merged.append(c)
 return merged


def clean_replay_html(content):
 """Remove JS-disabled notices introduced by replay's script-free sandbox."""
 import re
 return re.sub(r'<noscript\b[^>]*>.*?</noscript>', '', content, flags=re.S|re.I)

def evidence_card(chapter):
 """An explicitly reconstructed view, never presented as a screenshot."""
 from html import escape
 p=chapter.get('submission') or chapter.get('form_values') or {}
 lines=[]
 for key,label in [('titles','Review titles saved in the study'),('title','Post title'),('body','Post body')]:
  if p.get(key):lines.append('<h3>'+label+'</h3><pre style="white-space:pre-wrap">'+escape(str(p[key]))+'</pre>')
 if not lines:
  text=(chapter.get('snapshot') or {}).get('visible_text','')
  lines=['<p>'+escape(a['sentence'])+'</p>' for a in chapter.get('actions',[])]
  if text:lines.append('<hr><pre style="white-space:pre-wrap">'+escape(text[:20000])+'</pre>')
 return '<html><body style="background:white;color:#243040;font:16px system-ui;padding:24px"><h2>'+escape(chapter['title'])+'</h2><p>Reconstructed from recorded text and actions — not an original screenshot.</p>'+''.join(lines)+'</body></html>'


def chapters(trace):
 """Present actions with their observed outcome, retaining their original source URL."""
 trace=chronological_trace(trace)
 visits=visited_chapters(trace)
 for c in visits:c['performed_actions']=c['actions'];c['actions']=[]
 for i,c in enumerate(visits):
  outgoing=c['performed_actions']
  if not outgoing:continue
  next_visit=visits[i+1] if i+1<len(visits) else None
  if next_visit and (next_visit['site']==c['site'] or next_visit.get('submission')):
   next_visit['actions'].extend(outgoing);next_visit['action_source_url']=c['url']
  else:
   # No observed destination: retain actions on their recorded page.
   c['actions'].extend(outgoing)
 for e in trace['events']:
  if e['site']!='study' or e['kind'] not in ('start','native_tab_opened','study_screen','unable'):continue
  p=e['payload'];site=p.get('site');kind=e['kind']
  if kind=='native_tab_opened':
   destination=next((v for v in visits if v['site']==site and v['start']>=e['received_at']),None)
   if destination:
    destination['actions'].insert(0,{'sentence':'Opened '+('OneStopShop' if site=='shopping' else 'Reddit')+' from the original Streamlit study app.','kind':kind,'field':None,'payload':p,'at':e['received_at']})
    destination['action_source_url']='Original Streamlit study app'
    continue
  title='Study · Unable to complete this stage' if kind=='unable' else 'Study · Start shopping task' if kind=='start' else ('Study · Open '+('OneStopShop' if site=='shopping' else 'Reddit')) if kind=='native_tab_opened' else 'Study · '+p.get('phase','Task instructions')
  sentence='The participant reported being unable to complete this stage and continued the study.' if kind=='unable' else 'Started the shopping stage in the original study app.' if kind=='start' else 'Opened the website from the study app; the next website view shows where the participant arrived.' if kind=='native_tab_opened' else 'Viewed the original study instructions and controls.'
  visits.append({'identity':(e.get('page_id') or 'study-event',e['id']),'site':'study','task':e.get('task'),'start':e['received_at'],'end':e['received_at'],'title':title,'actions':[{'sentence':sentence,'kind':kind,'field':None,'payload':p,'at':e['received_at']}],'snapshot':p if p.get('html') else None,'raw_count':1,'url':'','key':'','study_evidence':True})
 for c in visits:
  if not c.get('study_evidence') or not c.get('snapshot'):continue
  previous=max((v['start'] for v in visits if v.get('study_evidence') and v['start']<c['start']),default=0)
  for event in trace['events']:
   if event['site']=='study' and previous<event['received_at']<=c['start'] and event['kind'] in ('click','input','change','copy','paste'):
    sentence=describe(event)
    if sentence:c['actions'].append({'sentence':sentence,'kind':event['kind'],'field':event['payload'].get('name'),'payload':event['payload'],'at':event['received_at']})
 for c in visits:
  if not c.get('submission') or 'titles' not in c['submission']:continue
  saved=c['submission']['titles']
  prior=[e for e in trace['events'] if e['site']=='study' and e['kind'] in ('input','change') and e['received_at']<=c['start'] and e['payload'].get('name')=='Review titles' and e['payload'].get('value')]
  if prior and prior[-1]['payload']['value'].strip()!=saved.strip():
   c['actions'].append({'sentence':'The saved review titles differ from the earlier draft shown in the study form. The submitted titles below are the final recorded value; intermediate edits were not fully captured.','kind':'saved_review_titles','field':None,'payload':{},'at':c['start']})
 visits.sort(key=lambda c:c['start'])
 return visits
