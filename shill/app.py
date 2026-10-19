from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlencode, parse_qsl, urlunsplit
from typing import Literal
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field, field_validator
from .analytics import report
from .browserbot import BrowserBlocked, BrowserCampaign, BrowserSupervisor
from .campaign_settings import get as get_campaign_settings, initialize as initialize_campaign_settings, load as load_campaign_settings, save as save_campaign_settings
from .config import load_settings
from .db import Database, audit, encode
from .generation import generate, GenerationError
from .policy import fact_status
from .publishing import reconcile_demo
from .scheduler import Scheduler
from .schemas import Strict, Profile, Opportunity, DraftBody, Generate, EditDraft, Approve, Schedule, Metrics, Reconcile
from .service import Service, Conflict, get_draft, get_profile


class ProfileUpdate(Strict):
    profile: Profile
    version: int


class Import(Strict):
    opportunities: list[Opportunity] = Field(max_length=100)


class Stop(Strict):
    stopped: bool


class TaggedLink(Strict):
    url: str = Field(max_length=2000)
    campaign: str = Field(min_length=1,max_length=100)
    content: str = Field(min_length=1,max_length=100)


class BrowserControl(Strict):
    action: Literal["start", "stop-worker", "pause", "resume", "run-once"]


class BrowserItemEdit(Strict):
    text: str = Field(min_length=1, max_length=1000)


class BrowserItemAction(Strict):
    action: Literal["dismiss", "requeue"]


class BrowserFeedback(Strict):
    value: Literal[-1, 1]
    note: str = Field(default="", max_length=500)


class BatchPreview(Strict):
    limit: int = Field(default=10, ge=1, le=20)


class EditableBrowserSettings(Strict):
    dry_run: bool = True
    search_queries: list[str] = Field(default_factory=list, max_length=30)
    watch_accounts: list[str] = Field(default_factory=list, max_length=50)
    blocked_accounts: list[str] = Field(default_factory=list, max_length=100)
    minimum_score: int = Field(default=35, ge=0, le=100)
    require_keyword_match: bool = True
    author_cooldown_days: int = Field(default=14, ge=1, le=365)
    discovery_limit: int = Field(default=30, ge=1, le=100)
    per_source_limit: int = Field(default=10, ge=1, le=30)
    discovery_scrolls: int = Field(default=1, ge=0, le=4)
    poll_minutes: int = Field(default=20, ge=5, le=1440)
    similarity_limit: float = Field(default=.82, ge=.65, le=.95)
    reply_provider: Literal["template", "ollama"] = "template"
    template_fallback: bool = True
    ollama_url: str = Field(default="http://127.0.0.1:11434", max_length=200)
    ollama_model: str = Field(default="", max_length=100)
    ollama_timeout_seconds: int = Field(default=60, ge=5, le=300)
    autostart: bool = False
    preview_batch_size: int = Field(default=10, ge=1, le=20)

    @field_validator("search_queries")
    @classmethod
    def queries(cls, values):
        if not all(isinstance(value,str) and 0 < len(value) <= 120 for value in values): raise ValueError("Search queries must be 1–120 characters")
        return values

    @field_validator("watch_accounts", "blocked_accounts")
    @classmethod
    def accounts(cls, values):
        if not all(value.lstrip('@').replace('_','').isalnum() and len(value.lstrip('@')) <= 15 for value in values): raise ValueError("Invalid X account")
        return [value.lstrip('@') for value in values]


class EditableLimits(Strict):
    daily_post_limit: int = Field(default=10, ge=1, le=20)
    minimum_gap_minutes: int = Field(default=60, ge=1, le=1440)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    timezone: str = Field(default="America/Chicago", max_length=100)


class CampaignSettingsUpdate(Strict):
    version: int = Field(ge=1)
    browser: EditableBrowserSettings
    limits: EditableLimits
    onboarding_complete: bool = True


STATIC=Path(__file__).parent/"static"


def create_app(settings=None,run_worker=True,testing=False):
    settings=(settings or load_settings()).validate()
    db=Database(settings.database)
    load_campaign_settings(db,settings)
    service=Service(db,settings)
    service.initialize()
    initialize_campaign_settings(db,settings,service.profile()['profile'])
    scheduler=Scheduler(service)
    browser_campaign=BrowserCampaign(db,settings) if settings.mode=='browser' else None
    browser_supervisor=BrowserSupervisor(browser_campaign) if browser_campaign else None

    @asynccontextmanager
    async def lifespan(app):
        if run_worker: scheduler.start()
        if run_worker and browser_supervisor and settings.browser.get('autostart',False): browser_supervisor.start()
        yield
        if browser_supervisor: browser_supervisor.stop()
        scheduler.close()

    app=FastAPI(title="Signal Garden",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.db,app.state.service,app.state.scheduler=db,service,scheduler
    app.state.browser_campaign,app.state.browser_supervisor=browser_campaign,browser_supervisor
    hosts=["localhost","127.0.0.1","[::1]"]
    if settings.public_origin: hosts.append(urlsplit(settings.public_origin).hostname)
    if testing: hosts.append("testserver")
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=hosts)
    password=os.environ.get("DASHBOARD_PASSWORD","")
    if password and len(password)<16: raise ValueError("DASHBOARD_PASSWORD must have at least 16 characters")
    auth_failures={}

    @app.middleware("http")
    async def guard(request,call_next):
        # Host validation also prevents DNS rebinding against an unauthenticated localhost dashboard.
        if password:
            client=request.client.host if request.client else "unknown"
            recent=[t for t in auth_failures.get(client,[]) if t>time.time()-60]
            if len(recent)>=15:
                return JSONResponse({"detail":"Too many authentication failures; wait a minute."},429,headers={"Retry-After":"60"})
            supplied=""
            try:
                scheme,value=request.headers.get("authorization","").split(" ",1)
                if scheme.lower()=="basic":
                    username,supplied=base64.b64decode(value,validate=True).decode().split(":",1)
                    if username!="admin": supplied=""
            except (ValueError,UnicodeError):
                pass
            if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(password.encode()).digest()):
                auth_failures[client]=recent+[time.time()]
                return JSONResponse({"detail":"Dashboard authentication required."},401,headers={"WWW-Authenticate":'Basic realm="Signal Garden", charset="UTF-8"'})
            auth_failures.pop(client,None)
        if request.method not in {"GET","HEAD","OPTIONS"}:
            expected=settings.public_origin.rstrip('/') or str(request.base_url).rstrip('/')
            origin=request.headers.get("origin")
            if (origin and origin!=expected) or request.headers.get("sec-fetch-site") in {"cross-site","same-site"}:
                return JSONResponse({"detail":"Cross-origin writes are forbidden."},403)
            if request.headers.get("content-type","").split(';')[0]!="application/json":
                return JSONResponse({"detail":"JSON request required."},415)
            # Bounded read works even without Content-Length (chunked requests).
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>262144:
                    return JSONResponse({"detail":"Request too large (256 KiB maximum)."},413)
            request._body=bytes(data)
        response=await call_next(request)
        response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer",
                                 "Cache-Control":"no-store","Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.exception_handler(Conflict)
    async def conflict(request,exc): return JSONResponse({"detail":str(exc)},409)
    @app.exception_handler(GenerationError)
    async def generation_error(request,exc): return JSONResponse({"detail":str(exc)},400)
    @app.exception_handler(BrowserBlocked)
    async def browser_blocked(request,exc): return JSONResponse({"detail":str(exc)},409)
    @app.exception_handler(KeyError)
    async def missing(request,exc): return JSONResponse({"detail":"Record not found"},404)
    @app.exception_handler(sqlite3.IntegrityError)
    async def duplicate(request,exc): return JSONResponse({"detail":"Duplicate record or conflicting delivery. Reload and reconcile; do not resend."},409)
    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        return JSONResponse({"detail":"Invalid input", "errors":[{"field":".".join(str(p) for p in e['loc']),"message":e['msg']} for e in exc.errors()]},422)

    @app.get("/")
    def home(): return FileResponse(STATIC/"index.html")

    @app.get("/api/health")
    def health():
        with db.connect() as c:
            c.execute("SELECT 1").fetchone()
            stopped=bool(c.execute("SELECT stopped FROM controls").fetchone()[0])
            spend=c.execute("SELECT COALESCE(SUM(COALESCE(actual_usd,reserved_usd)),0) FROM spending WHERE at>=?",(time.time()-86400,)).fetchone()[0]
        browser_mode=settings.mode=='browser'
        return {"ok":True,"mode":settings.mode,"stopped":stopped,"timezone":settings.timezone,
                "x":{"status":"browser" if browser_mode else "unavailable","detail":"Local browser campaign is configured separately; no X API key is used." if browser_mode else "No X API requests are made."},
                "model":{"provider":settings.model.get('provider','template'),"key_present":bool(os.environ.get('MODEL_API_KEY')),"reserved_or_spent_usd":spend,"daily_budget_usd":settings.model.get('daily_budget_usd',0)},
                "limits":{"daily":settings.daily_post_limit,"gap_minutes":settings.minimum_gap_minutes,"quiet_hours":f"{settings.quiet_start:02}:00–{settings.quiet_end:02}:00"},
                "worker_running":bool(scheduler.thread and scheduler.thread.is_alive()),"browser_running":bool(browser_supervisor and browser_supervisor.running),
                "publishing":"simulation only" if settings.mode=='demo' else "local browser campaign + manual originals" if browser_mode else "manual export only"}

    @app.get("/api/profile")
    def profile():
        result=service.profile()
        result["fact_statuses"]={f['id']:fact_status(f,time.time()) for f in result['profile']['facts']}
        return result

    @app.put("/api/profile")
    def save_profile(body:ProfileUpdate): return service.save_profile(body.profile.model_dump(),body.version)

    @app.get("/api/opportunities")
    def opportunities(): return service.opportunities()

    @app.post("/api/opportunities/import")
    def import_opportunities(body:Import): return service.import_opportunities([o.model_dump() for o in body.opportunities])

    @app.post("/api/generate")
    def create_generated(body:Generate):
        p=service.profile()['profile']
        opportunity=None
        if body.kind in {'reply','quote'} and not body.opportunity_id:
            raise Conflict("Select an appropriate conversation before generating replies or quotes.")
        if body.opportunity_id:
            opportunity=next((o for o in service.opportunities() if o['id']==body.opportunity_id),None)
            if not opportunity or not opportunity['ranking']['appropriate']:
                raise Conflict("This conversation is unsuitable or missing.")
        drafts=generate(db,settings,body.model_dump(),p,opportunity)
        return [service.create_draft(d) for d in drafts]

    @app.get("/api/drafts")
    def drafts(): return service.drafts()

    @app.post("/api/drafts")
    def create_draft(body:DraftBody): return service.create_draft(body.model_dump())

    @app.get("/api/drafts/{did}")
    def draft(did:str): return service.draft(did)

    @app.put("/api/drafts/{did}")
    def edit(did:str,body:EditDraft): return service.edit(did,body.body.model_dump(),body.revision)

    @app.post("/api/drafts/{did}/approve")
    def approve(did:str,body:Approve): return service.approve(did,**body.model_dump())

    @app.post("/api/drafts/{did}/schedule")
    def schedule(did:str,body:Schedule): return service.schedule(did,**body.model_dump())

    @app.post("/api/drafts/{did}/discard")
    def discard(did:str): service.cancel(did); return {"ok":True}

    @app.post("/api/drafts/{did}/export")
    def export(did:str): return service.export(did)

    @app.post("/api/drafts/{did}/record-manual")
    def manual(did:str,body:Reconcile): return service.record_manual(did,body.post_url)

    @app.post("/api/drafts/{did}/reconcile-demo")
    def reconcile(did:str):
        if settings.mode!='demo': raise Conflict("Demo reconciliation is unavailable outside demo mode.")
        reconcile_demo(db,did)
        return service.draft(did)

    @app.get("/api/calendar")
    def calendar():
        with db.connect() as c:
            return [dict(r) for r in c.execute("SELECT j.*,d.body,d.status FROM jobs j JOIN drafts d ON d.id=j.draft_id ORDER BY due_at")]

    @app.post("/api/stop")
    def stop(body:Stop): service.stop(body.stopped); return {"stopped":body.stopped}

    @app.get("/api/campaign-settings")
    def campaign_settings(): return get_campaign_settings(db,settings)

    @app.put("/api/campaign-settings")
    def campaign_settings_update(body:CampaignSettingsUpdate):
        try:
            return save_campaign_settings(db,settings,{"browser":body.browser.model_dump(),"limits":body.limits.model_dump()},body.version,body.onboarding_complete)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    @app.get("/api/browser")
    def browser_status():
        if not browser_campaign:
            return {"configured":False,"mode":settings.mode,"running":False,"recent":[],"counts":{}}
        return {"configured":True,**browser_campaign.status(),**browser_supervisor.status()}

    @app.post("/api/browser/control")
    def browser_control(body:BrowserControl):
        if not browser_campaign: raise Conflict("Set mode = \"browser\" and restart the dashboard first.")
        if body.action=='start': browser_supervisor.start()
        elif body.action=='stop-worker': browser_supervisor.stop()
        elif body.action=='pause': service.stop(True)
        elif body.action=='resume': service.stop(False)
        else: browser_supervisor.trigger()
        return {**browser_campaign.status(),**browser_supervisor.status()}

    @app.get("/api/browser/session-health")
    def browser_session_health():
        if not browser_campaign: raise Conflict("Browser mode is not configured.")
        if browser_supervisor.running: raise Conflict("Stop the browser worker before checking the same browser profile.")
        return browser_campaign.session.health()

    @app.post("/api/browser/preview-batch")
    def browser_preview_batch(body:BatchPreview):
        if not browser_campaign: raise Conflict("Browser mode is not configured.")
        browser_supervisor.trigger_batch(body.limit)
        return {**browser_campaign.status(),**browser_supervisor.status()}

    @app.put("/api/browser/items/{item_id}")
    def browser_item_edit(item_id:str,body:BrowserItemEdit):
        if not browser_campaign: raise Conflict("Browser mode is not configured.")
        return browser_campaign.edit_item(item_id,body.text)

    @app.post("/api/browser/items/{item_id}/action")
    def browser_item_action(item_id:str,body:BrowserItemAction):
        if not browser_campaign: raise Conflict("Browser mode is not configured.")
        return browser_campaign.item_action(item_id,body.action)

    @app.post("/api/browser/items/{item_id}/feedback")
    def browser_item_feedback(item_id:str,body:BrowserFeedback):
        if not browser_campaign: raise Conflict("Browser mode is not configured.")
        return browser_campaign.feedback(item_id,body.value,body.note)

    @app.get("/api/browser/diagnostics/{name}")
    def browser_diagnostic(name:str):
        if not browser_campaign or Path(name).name!=name: raise KeyError("Diagnostic not found")
        directory=Path(settings.browser.get('diagnostics_dir','data/browser-diagnostics')).resolve()
        path=(directory/name).resolve()
        if path.parent!=directory or not path.is_file(): raise KeyError("Diagnostic not found")
        return FileResponse(path,media_type='image/png')

    @app.get("/api/audit")
    def audit_log():
        with db.connect() as c:
            return [{**dict(r),"details":json.loads(r['details'])} for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300")]

    @app.get("/api/analytics")
    def analytics(): return report(db,settings)

    @app.post("/api/demo/analytics")
    def demo_analytics():
        if settings.mode!='demo': raise Conflict("Fictional analytics can only be loaded in demo mode.")
        from .demo_metrics import seed_metrics
        seed_metrics(db)
        return {"ok":True,"fictional":True}

    @app.post("/api/drafts/{did}/metrics")
    def metrics(did:str,body:Metrics):
        with db.connect(write=True) as c:
            delivery=c.execute("SELECT at FROM deliveries WHERE draft_id=?",(did,)).fetchone()
            if not delivery or not delivery['at']<=body.captured_at<=time.time()+60:
                raise Conflict("Metrics need a delivered post and a capture time between delivery and now.")
            m=body.model_dump()
            for small,big in [('meaningful_replies','replies'),('qualified_clicks','clicks')]:
                if m[small] is not None and m[big] is not None and m[small]>m[big]:
                    raise Conflict(f"{small} cannot exceed {big}")
            c.execute("INSERT INTO metrics(draft_id,captured_at,body) VALUES(?,?,?)",(did,body.captured_at,encode(m)))
            audit(c,"metrics.recorded",{"draft_id":did,"source":body.source})
        return {"ok":True}

    @app.post("/api/tag-link")
    def tag_link(body:TaggedLink):
        u=urlsplit(body.url)
        if u.scheme!='https' or not u.hostname or u.username:
            raise Conflict("Use a public HTTPS URL without credentials.")
        p=service.profile()['profile']
        allowed={urlsplit(link).hostname for link in p['official_links']}
        if u.hostname not in allowed:
            raise Conflict("Link host must be included in the project's official links.")
        query=dict(parse_qsl(u.query,keep_blank_values=True))
        query.update(utm_source='x',utm_medium='organic_social',utm_campaign=body.campaign,utm_content=body.content)
        return {"url":urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),u.fragment))}

    app.mount("/static",StaticFiles(directory=STATIC),name="static")
    return app
