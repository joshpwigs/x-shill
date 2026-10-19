import json
import time
import uuid
from . import demo
from .db import audit, encode
from .policy import approval_fingerprint, content_hash, inspect_draft, rank_opportunity
from .schemas import DraftBody, Profile


class Conflict(ValueError):
    pass


def get_profile(c):
    row = c.execute("SELECT version,body FROM profile WHERE id=1").fetchone()
    return json.loads(row["body"]), row["version"]


def get_draft(c, draft_id):
    row = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        raise KeyError("Draft not found")
    return {**dict(row), "body":json.loads(row["body"])}


class Service:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def initialize(self):
        with self.db.connect(write=True) as c:
            if not c.execute("SELECT 1 FROM profile").fetchone():
                p = demo.profile() if self.settings.mode == "demo" else Profile(name="Your project",account="YourProject",disclosure="Project team",fictional=True).model_dump()
                c.execute("INSERT INTO profile VALUES(1,1,?)", (encode(p),))
                audit(c,"workspace.initialized",{"mode":self.settings.mode})
                if self.settings.mode == "demo":
                    for o in demo.opportunities():
                        c.execute("INSERT INTO opportunities VALUES(?,?,?)", (o["id"],encode(o),time.time()))

    def profile(self):
        with self.db.connect() as c:
            p, version = get_profile(c)
            return {"profile":p,"version":version}

    def save_profile(self, profile, version):
        with self.db.connect(write=True) as c:
            _, current = get_profile(c)
            if current != version:
                raise Conflict("Profile changed in another session; reload before saving.")
            if c.execute("SELECT 1 FROM jobs WHERE state='inflight'").fetchone():
                raise Conflict("A delivery is in flight. Stop and reconcile it before editing the project.")
            c.execute("UPDATE profile SET body=?,version=version+1 WHERE id=1", (encode(profile),))
            c.execute("UPDATE drafts SET status='draft',approval_hash=NULL,profile_version=NULL,revision=revision+1,updated_at=? WHERE status IN ('approved','scheduled','ready','failed')", (time.time(),))
            c.execute("DELETE FROM jobs WHERE state IN ('pending','ready','failed')")
            c.execute("DELETE FROM browser_queue WHERE state IN ('queued','previewed','failed','dismissed')")
            audit(c,"profile.updated",{"version":version+1,"approvals_invalidated":True})
        return self.profile()

    def create_draft(self, body):
        body = DraftBody(**body).model_dump()
        now, did = time.time(),uuid.uuid4().hex
        with self.db.connect(write=True) as c:
            p, _ = get_profile(c)
            body["fictional"] = p["fictional"]
            c.execute("INSERT INTO drafts(id,body,status,content_hash,created_at,updated_at) VALUES(?,?,'draft',?,?,?)", (did,encode(body),content_hash(body),now,now))
            audit(c,"draft.created",{"draft_id":did,"kind":body["kind"]})
        return self.draft(did)

    def draft(self, did):
        with self.db.connect() as c:
            d = get_draft(c,did)
            p, v = get_profile(c)
            d["review"] = inspect_draft(c,d["body"],p,did)
            d["fingerprint"] = approval_fingerprint(d["body"],p,v)
            job = c.execute("SELECT * FROM jobs WHERE draft_id=?", (did,)).fetchone()
            d["job"] = dict(job) if job else None
            return d

    def drafts(self):
        with self.db.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM drafts ORDER BY created_at DESC LIMIT 500")]
        return [self.draft(did) for did in ids]

    def edit(self, did, body, revision):
        with self.db.connect(write=True) as c:
            d = get_draft(c,did)
            if d["revision"] != revision:
                raise Conflict("Draft changed; reload before editing.")
            if d["status"] in {"inflight","uncertain","simulated","posted","discarded"}:
                raise Conflict("Delivered, uncertain, in-flight, or discarded drafts cannot be edited.")
            p,_ = get_profile(c)
            body["fictional"] = p["fictional"]
            c.execute("UPDATE drafts SET body=?,revision=revision+1,status='draft',approval_hash=NULL,profile_version=NULL,content_hash=?,updated_at=? WHERE id=?", (encode(body),content_hash(body),time.time(),did))
            c.execute("DELETE FROM jobs WHERE draft_id=?",(did,))
            audit(c,"draft.edited",{"draft_id":did,"approval_invalidated":True})
        return self.draft(did)

    def approve(self, did, revision, fingerprint, facts_reviewed, context_reviewed):
        with self.db.connect(write=True) as c:
            d=get_draft(c,did)
            p,v=get_profile(c)
            expected=approval_fingerprint(d["body"],p,v)
            if revision!=d["revision"] or fingerprint!=expected:
                raise Conflict("Content or project knowledge changed; review the current version.")
            if d["status"] not in {"draft","approved"}:
                raise Conflict("This draft is not awaiting approval.")
            if not facts_reviewed or not context_reviewed:
                raise Conflict("Review all factual claims, sources, affiliation, and conversation context first.")
            review=inspect_draft(c,d["body"],p,did)
            if review["blockers"]:
                raise Conflict("Approval blocked: " + "; ".join(review["blockers"]))
            if self.settings.mode in {"manual", "browser"} and (p["fictional"] or d["body"]["fictional"]):
                raise Conflict("Replace the fictional project profile and regenerate before real manual use.")
            if self.settings.mode in {"manual", "browser"} and d['body'].get('opportunity_id'):
                context=c.execute("SELECT body FROM opportunities WHERE id=?",(d['body']['opportunity_id'],)).fetchone()
                if context and json.loads(context[0])['fictional']:
                    raise Conflict("A real reply/quote cannot use fictional conversation context.")
            c.execute("UPDATE drafts SET status='approved',approval_hash=?,profile_version=?,updated_at=? WHERE id=?", (expected,v,time.time(),did))
            audit(c,"draft.approved",{"draft_id":did,"revision":revision,"fingerprint":expected,"facts_reviewed":True,"context_reviewed":True})
        return self.draft(did)

    def validate_approval(self,c,d,now=None):
        p,v=get_profile(c)
        if d["approval_hash"]!=approval_fingerprint(d["body"],p,v) or d["profile_version"]!=v:
            raise Conflict("Approval is missing or stale.")
        review=inspect_draft(c,d["body"],p,d["id"],now)
        if review["blockers"]:
            raise Conflict("Review no longer passes: "+"; ".join(review["blockers"]))
        if self.settings.mode in {"manual", "browser"} and (p["fictional"] or d["body"]["fictional"]):
            raise Conflict("Fictional content cannot enter the real manual workflow.")
        if self.settings.mode in {"manual", "browser"} and d['body'].get('opportunity_id'):
            context=c.execute("SELECT body FROM opportunities WHERE id=?",(d['body']['opportunity_id'],)).fetchone()
            if context and json.loads(context[0])['fictional']:
                raise Conflict("Fictional context cannot enter the real manual workflow.")
        return review

    def schedule(self,did,revision,due_at):
        now=time.time()
        if not now-5 <= due_at <= now+self.settings.max_schedule_days*86400:
            raise Conflict("Choose a future time within the configured scheduling horizon.")
        with self.db.connect(write=True) as c:
            d=get_draft(c,did)
            if d["revision"]!=revision or d["status"]!="approved":
                raise Conflict("Only the current individually approved version can be scheduled.")
            if c.execute("SELECT stopped FROM controls").fetchone()[0]:
                raise Conflict("Emergency stop is active.")
            review=self.validate_approval(c,d)
            if not review["schedule_eligible"]:
                raise Conflict("Replies, quotes, threads, and image briefs require individual manual export.")
            c.execute("INSERT INTO jobs(id,draft_id,state,due_at) VALUES(?,?,'pending',?)",(uuid.uuid4().hex,did,due_at))
            c.execute("UPDATE drafts SET status='scheduled' WHERE id=?",(did,))
            audit(c,"draft.scheduled",{"draft_id":did,"due_at":due_at,"mode":self.settings.mode})
        return self.draft(did)

    def cancel(self,did):
        with self.db.connect(write=True) as c:
            d=get_draft(c,did)
            if d["status"] in {"inflight","uncertain","simulated","posted"}:
                raise Conflict("An in-flight or completed/uncertain delivery cannot be discarded.")
            c.execute("DELETE FROM jobs WHERE draft_id=?",(did,))
            c.execute("UPDATE drafts SET status='discarded',approval_hash=NULL WHERE id=?",(did,))
            audit(c,"draft.discarded",{"draft_id":did})

    def export(self,did):
        with self.db.connect(write=True) as c:
            d=get_draft(c,did)
            if d["status"] not in {"approved","ready"}:
                raise Conflict("Export requires current individual approval. Scheduled originals become available when due.")
            self.validate_approval(c,d)
            audit(c,"draft.exported",{"draft_id":did,"fingerprint":d["approval_hash"]})
            source_map={f["id"]:f for f in get_profile(c)[0]["facts"]}
            return {"label":"FICTIONAL DEMO — DO NOT PUBLISH" if d["body"]["fictional"] else "Individually reviewed manual export; not sent to X",
                    "content":d["body"],"sources":[source_map[f] for f in d["body"]["source_ids"]],
                    "approval_fingerprint":d["approval_hash"],"exported_at":time.time()}

    def opportunities(self):
        with self.db.connect() as c:
            p,_=get_profile(c)
            result=[{**json.loads(r[0])} for r in c.execute("SELECT body FROM opportunities")]
            for o in result:
                o["ranking"]=rank_opportunity(c,o,p)
        return sorted(result,key=lambda o:o["ranking"]["score"],reverse=True)

    def import_opportunities(self, items):
        now=time.time()
        with self.db.connect(write=True) as c:
            for o in items:
                if o["posted_at"]>now+60:
                    raise Conflict("Conversation timestamp is in the future.")
                old=c.execute("SELECT body FROM opportunities WHERE id=?",(o["id"],)).fetchone()
                if old and json.loads(old[0])!=o:
                    raise Conflict("Existing conversation IDs are immutable; import a new ID to preserve reviewed context.")
                c.execute("INSERT OR IGNORE INTO opportunities VALUES(?,?,?)",(o["id"],encode(o),now))
            audit(c,"research.imported",{"count":len(items),"source":"manual"})
        return self.opportunities()

    def stop(self,stopped):
        with self.db.connect(write=True) as c:
            c.execute("UPDATE controls SET stopped=?",(int(stopped),))
            audit(c,"emergency_stop.changed",{"stopped":stopped})

    def record_manual(self,did,url):
        from urllib.parse import urlsplit
        with self.db.connect(write=True) as c:
            d=get_draft(c,did)
            p,_=get_profile(c)
            if self.settings.mode!="manual" or d["status"] not in {"approved","ready","uncertain"}:
                raise Conflict("Only real manual deliveries or uncertain outcomes can be recorded here.")
            if urlsplit(url).path.split('/')[1].casefold()!=p["account"].casefold():
                raise Conflict("Receipt must belong to the configured project account.")
            if d["status"]!="uncertain":
                self.validate_approval(c,d)
            post_id=urlsplit(url).path.split('/')[-1]
            c.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)",(d["content_hash"],did,post_id,"manual",time.time()))
            c.execute("UPDATE drafts SET status='posted' WHERE id=?",(did,))
            c.execute("UPDATE jobs SET state='sent',receipt=?,lease_until=NULL WHERE draft_id=?",(post_id,did))
            audit(c,"delivery.manually_recorded",{"draft_id":did,"post_id":post_id,"api_verified":False})
        return self.draft(did)
