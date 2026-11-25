"""Prolific launch parameters and server-controlled completion settings."""
import os,json,re,hmac,hashlib,secrets
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def settings():
 path=Path(os.environ.get('RECONSTRUCTED_CONFIG',str(ROOT/'config.prolific.json')))
 cfg=json.loads(path.read_text()) if path.exists() else {}
 cfg['completion_code']=os.environ.get('PROLIFIC_COMPLETION_CODE',cfg.get('completion_code','')).strip()
 cfg['study_id']=os.environ.get('PROLIFIC_STUDY_ID',cfg.get('study_id','')).strip()
 if not re.fullmatch(r'[A-Za-z0-9]+',cfg['completion_code']):
  raise ValueError('The study completion code is not configured correctly. Please contact the researcher.')
 return cfg

def identity(params):
 result=[]
 for key in ('PROLIFIC_PID','STUDY_ID','SESSION_ID'):
  value=str(params.get(key,'')).strip()
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
   raise ValueError('Please open this study using your Prolific study link. Participant details are supplied automatically; no manual ID entry is needed.')
  result.append(value)
 expected=settings().get('study_id')
 if expected and result[1]!=expected:raise ValueError('This link belongs to a different Prolific study. Please return to Prolific and open the correct study link.')
 return tuple(result)

def launch_token(ids,db):
 # A server secret makes URL-based resumption stable without exposing tokens.
 path=Path(str(db)+'.launch-secret')
 path.parent.mkdir(parents=True,exist_ok=True)
 import fcntl
 fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
 with os.fdopen(fd,'r+') as f:
  fcntl.flock(f,fcntl.LOCK_EX)
  secret=f.read().strip()
  if not secret:
   secret=secrets.token_hex(32);f.write(secret);f.flush()
  fcntl.flock(f,fcntl.LOCK_UN)

 return hmac.new(secret.encode(),json.dumps(ids).encode(),hashlib.sha256).hexdigest()

def completion_url(code):
 return 'https://app.prolific.com/submissions/complete?cc='+code
