"""Private session-scoped study websites and event collector."""
import json
import asyncio
import contextlib
import live
import native
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from settings import ROOT, config
import db

@asynccontextmanager
async def lifespan(app):
    db.init()
    import archive
    archive.init()
    cleaner=asyncio.create_task(live.cleanup())
    yield
    cleaner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleaner
    for browser in list(live.BROWSERS.values()):
        await browser.close()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(native.router)
app.mount("/assets", StaticFiles(directory=ROOT / "static"), name="assets")

@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

@app.get("/health")
def health():
    return {"status":"ok","mode":config()["mode"]}

@app.get("/workspace")
def workspace():
    return FileResponse(ROOT / "static/index.html")

def auth(request):
    token=request.headers.get("Authorization","").removeprefix("Bearer ")
    if not token:
        raise HTTPException(401,"Missing study session")
    try:
        db.session(token)
    except ValueError:
        raise HTTPException(401,"Invalid study session")
    return token

@app.get("/api/state")
def state(request:Request):
    return db.state(auth(request))

async def body(request):
    raw=await request.body()
    if len(raw)>1_000_000:
        raise HTTPException(413,"Request too large")
    try:
        data=json.loads(raw)
        if not isinstance(data,dict):
            raise ValueError()
        return data
    except (ValueError,TypeError):
        raise HTTPException(400,"Invalid JSON object")

@app.post("/api/events")
async def events(request:Request):
    token=auth(request)
    data=await body(request)
    try:
        db.save_events(token,data.get("events"))
    except (ValueError,TypeError,KeyError) as e:
        raise HTTPException(400,str(e))
    return {"saved":True}

@app.post("/api/action")
async def action(request:Request):
    token=auth(request)
    data=await body(request)
    try:
        if data.get("action") == "live_finish":
            raise ValueError("Use the live browser finish endpoint.")
        return {"phase":db.change(token,data.get("action"),data.get("payload"))}
    except ValueError as e:
        raise HTTPException(409,str(e))

@app.get("/live")
def live_workspace():
    return FileResponse(ROOT / "static/live.html")

@app.get("/api/live/frame")
async def live_frame(request:Request):
    try:
        browser=await live.get_browser(auth(request))
        async with browser.lock:
            return await browser.frame()
    except ValueError as e:
        raise HTTPException(409,str(e))
    except Exception:
        raise HTTPException(503,"Live browser unavailable. Check website connectivity and Chromium installation.")

@app.post("/api/live/action")
async def live_action(request:Request):
    data=await body(request)
    try:
        browser=await live.get_browser(auth(request))
        async with browser.lock:
            return await browser.action(data)
    except ValueError as e:
        raise HTTPException(400,str(e))

@app.post("/api/live/finish")
async def live_finish(request:Request):
    token=auth(request)
    browser=await live.get_browser(token)
    async with browser.lock:
        evidence=await browser.evidence()
        try:
            return {"phase":db.change(token,"live_finish",evidence)}
        except ValueError as e:
            raise HTTPException(409,str(e))
