"""Prolific URL identity and completion configuration (no API credentials required)."""
import re

def identity(params):
 values=[]
 for key in ('PROLIFIC_PID','STUDY_ID','SESSION_ID'):
  if hasattr(params,'get_all') and len(params.get_all(key))!=1:
   raise ValueError('Open the study using your Prolific study link. Required participant identifiers are missing or duplicated.')
  value=params.get(key,'')
  if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
   raise ValueError('Open the study using your Prolific study link. Required participant identifiers are missing or invalid.')
  values.append(value)
 return tuple(values)

def completion_code(value):
 if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{4,64}',value):
  raise ValueError('Set WEBARENA_COMPLETION_CODE to your Prolific completion code before launching.')
 return value
