"""Native HTTP website gateways reachable through VS Code port forwarding.
Pages render in the participant's browser; this does not stream screenshots.
"""
import asyncio
from contextlib import asynccontextmanager
import gzip
from http.client import HTTPConnection, HTTPSConnection
import os
import re
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Response
from settings import config

HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','content-length'}

def js_escape_url(value):
    # Magento Escaper::escapeJs leaves alphanumerics and comma/dot/underscore.
    return ''.join(c if c.isalnum() or c in ',._' else '\\u%04X' % ord(c) for c in value)

def rewrite(text,mapping):
    for source,target in mapping.items():
        text=text.replace(source,target).replace(source.replace('/','\\/'),target.replace('/','\\/'))
        text=re.sub(re.escape(js_escape_url(source)),lambda _:js_escape_url(target),text,flags=re.I)
        # Magento also configures the JS cookie domain separately from its URLs.
        old_host=urlsplit(source).hostname
        new_host=urlsplit(target).hostname
        if old_host and new_host:
            pattern=r'("domain"\s*:\s*")'+re.escape(old_host)+r'(")'
            text=re.sub(pattern,lambda m:m.group(1)+new_host+m.group(2),text)
    return text

def make_app(site):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.middleware('http')
    async def study_recorder_cors(request,call_next):
        origin=request.headers.get('origin','')
        configured=os.environ.get('WEBARENA_STUDY_URL','http://127.0.0.1:8505').rstrip('/')
        parsed=urlsplit(origin);expected=urlsplit(configured)
        allowed=(parsed.scheme==expected.scheme and parsed.netloc==expected.netloc and bool(parsed.netloc)) or (expected.hostname in ('127.0.0.1','localhost') and parsed.hostname in ('127.0.0.1','localhost') and parsed.scheme=='http')
        recording=request.url.path=='/__study__/events'
        if request.method=='OPTIONS' and recording:
            response=Response(status_code=204 if allowed else 403)
        else:response=await call_next(request)
        if recording and allowed:
            response.headers['Access-Control-Allow-Origin']=origin
            response.headers['Access-Control-Allow-Methods']='POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers']='Authorization, Content-Type'
            response.headers['Vary']='Origin'
        return response
    @app.api_route('/{path:path}',methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])
    async def proxy(path:str,request:Request):
        cfg=config()
        upstream=cfg[site+'_url'].rstrip('/')
        # Backend-to-browser origins can be configured for deployment or port remapping.
        mapping={cfg[s+'_url'].rstrip('/'):cfg.get(s+'_browser_url',cfg[s+'_url']).rstrip('/') for s in ('shopping','reddit')}
        mapping[upstream]=cfg.get(site+'_browser_url', str(request.base_url).rstrip('/')).rstrip('/')
        is_recording=request.url.path.startswith('/__study__/')
        target=urlsplit(os.environ.get('WEBARENA_WORKSPACE_URL','http://127.0.0.1:'+os.environ.get('WORKSPACE_PORT','8766'))) if is_recording else urlsplit(upstream)
        headers={k:v for k,v in request.headers.items() if k.lower() not in HOP|{'host','accept-encoding','origin','referer','if-none-match','if-modified-since'}}
        headers['Host']=target.netloc
        headers['Accept-Encoding']='identity'
        # Keep website CSRF checks consistent with the original host.
        inverse={v:k for k,v in mapping.items()}
        for name in ('origin','referer'):
            if name in request.headers:headers[name]=rewrite(request.headers[name],inverse)
        raw=await request.body()
        if len(raw)>2_000_000:return Response('Request too large',status_code=413)
        route=('/native/'+request.url.path[len('/__study__/'):] if is_recording else request.url.path)+('?' + request.url.query if request.url.query else '')
        def send():
            cls=HTTPSConnection if target.scheme=='https' else HTTPConnection
            con=cls(target.hostname,target.port,timeout=30)
            try:
                con.request(request.method,route,body=raw or None,headers=headers)
                res=con.getresponse()
                return res.status,res.getheaders(),res.read()
            finally:con.close()
        try:status,out_headers,body=await asyncio.to_thread(send)
        except OSError:return Response('The research website could not be reached. Please retry.',status_code=502)
        content_type=next((v for k,v in out_headers if k.lower()=='content-type'),'')
        encoding=next((v for k,v in out_headers if k.lower()=='content-encoding'),'')
        textual=any(t in content_type for t in ('text/','javascript','json','xml'))
        modified=False
        if textual and encoding in ('','identity','gzip'):
            if encoding=='gzip':body=gzip.decompress(body)
            body=rewrite(body.decode('utf-8',errors='replace'),mapping).encode('utf-8')
            if 'text/html' in content_type and not is_recording and b'/__study__/tracker.js' not in body:
                body=body.replace(b'</head>',b'<script defer src="/__study__/tracker.js"></script></head>',1)
            modified=True
        result=Response(content=body,status_code=status)
        # Preserve separate Set-Cookie headers and never share browser cookies between requests.
        result.raw_headers=[]
        for name,value in out_headers:
            lower=name.lower()
            if lower in HOP or (modified and lower in ('content-encoding','etag','last-modified')):continue
            if lower=='location':value=rewrite(value,mapping)
            if lower=='set-cookie':value=re.sub(r';\s*Domain=[^;]+','',value,flags=re.I)
            if lower in ('content-security-policy','content-security-policy-report-only'):value=rewrite(value,mapping)
            result.raw_headers.append((name.lower().encode('latin-1'),value.encode('latin-1')))
        result.raw_headers.append((b'content-length',str(len(body)).encode()))
        return result
    return app

shopping_app=make_app('shopping')
reddit_app=make_app('reddit')
