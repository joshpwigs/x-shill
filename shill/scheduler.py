from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import random
import threading
import time
from .db import audit
from .service import Conflict, get_draft
from .publishing import DemoPublisher, KnownRejected, UnavailableXPublisher

log=logging.getLogger(__name__)


class Scheduler:
    def __init__(self, service, publisher=None):
        self.service,self.db,self.settings=service,service.db,service.settings
        self.publisher=publisher or (DemoPublisher(self.db) if self.settings.mode=="demo" else UnavailableXPublisher())
        self.halt=threading.Event()
        self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.run,name="durable-scheduler",daemon=True)
        self.thread.start()

    def close(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run(self):
        while not self.halt.is_set():
            try:
                self.tick()
            except Exception:
                # Never include provider bodies, tokens, or generated content in logs.
                log.error("Scheduler cycle failed; durable state retained. Inspect the audit log/database.")
            self.halt.wait(self.settings.worker_interval_seconds)

    def quiet(self,now):
        h=datetime.fromtimestamp(now,ZoneInfo(self.settings.timezone)).hour
        a,b=self.settings.quiet_start,self.settings.quiet_end
        return a<=h<b if a<b else (h>=a or h<b) if a!=b else False

    def next_allowed(self,c,now):
        delay=now
        if self.quiet(now):
            delay=(int(now)//60+1)*60
            while self.quiet(delay):
                delay+=60
        attempts=c.execute("SELECT at FROM attempts WHERE at>? ORDER BY at DESC",(now-86400,)).fetchall()
        if attempts:
            delay=max(delay,attempts[0][0]+self.settings.minimum_gap_minutes*60)
        if len(attempts)>=self.settings.daily_post_limit:
            delay=max(delay,attempts[self.settings.daily_post_limit-1][0]+86400+1)
        return delay

    def tick(self,now=None):
        now=now or time.time()
        with self.db.connect(write=True) as c:
            # A crash between send and receipt persistence is ambiguous, not retryable.
            expired=c.execute("SELECT id,draft_id FROM jobs WHERE state='inflight' AND lease_until<=?",(now,)).fetchall()
            for row in expired:
                c.execute("UPDATE jobs SET state='uncertain',error='Worker lease expired; reconcile before any further action' WHERE id=?",(row['id'],))
                c.execute("UPDATE drafts SET status='uncertain' WHERE id=?",(row['draft_id'],))
                audit(c,"delivery.recovery_uncertain",{"draft_id":row['draft_id']},now)
            if c.execute("SELECT stopped FROM controls").fetchone()[0]:
                return "stopped"
            # Only one delivery may be in flight; transactions serialize multiple local workers.
            if c.execute("SELECT 1 FROM jobs WHERE state='inflight'").fetchone():
                return "busy"
            j=c.execute("SELECT * FROM jobs WHERE state='pending' AND due_at<=? ORDER BY due_at,id LIMIT 1",(now,)).fetchone()
            if not j:
                return "idle"
            d=get_draft(c,j["draft_id"])
            try:
                if d["status"]!="scheduled":
                    raise Conflict("Draft is not scheduled")
                review=self.service.validate_approval(c,d,now)
                if not review["schedule_eligible"]:
                    raise Conflict("Content type requires manual review/export")
            except Conflict as e:
                c.execute("UPDATE jobs SET state='failed',error=? WHERE id=?",(str(e),j["id"]))
                c.execute("UPDATE drafts SET status='failed' WHERE id=?",(d["id"],))
                audit(c,"delivery.blocked",{"draft_id":d["id"],"reason":str(e)},now)
                return "blocked"
            allowed=self.next_allowed(c,now)
            if allowed>now:
                c.execute("UPDATE jobs SET due_at=? WHERE id=?",(allowed,j["id"]))
                return "limited"
            if self.settings.mode=="manual":
                c.execute("UPDATE jobs SET state='ready' WHERE id=?",(j["id"],))
                c.execute("UPDATE drafts SET status='ready' WHERE id=?",(d["id"],))
                c.execute("INSERT INTO attempts(job_id,at,outcome) VALUES(?,?,'manual_ready')",(j["id"],now))
                audit(c,"manual.export_ready",{"draft_id":d["id"]},now)
                return "ready"
            c.execute("UPDATE jobs SET state='inflight',lease_until=?,last_attempt=?,attempts=attempts+1 WHERE id=?",(now+self.settings.lease_seconds,now,j["id"]))
            c.execute("UPDATE drafts SET status='inflight' WHERE id=?",(d["id"],))
            attempt=c.execute("INSERT INTO attempts(job_id,at,outcome) VALUES(?,?,'inflight')",(j["id"],now)).lastrowid
            audit(c,"delivery.attempt",{"draft_id":d["id"],"mode":"demo","attempt":j["attempts"]+1},now)
        # A stop set after claiming but before the request cancels the claim. A request already
        # in flight cannot be unsent; it is always recorded/reconciled.
        with self.db.connect(write=True) as c:
            if c.execute("SELECT stopped FROM controls").fetchone()[0]:
                c.execute("UPDATE jobs SET state='pending',lease_until=NULL WHERE id=?",(j['id'],))
                c.execute("UPDATE drafts SET status='scheduled' WHERE id=?",(d['id'],))
                c.execute("UPDATE attempts SET outcome='stopped_before_send' WHERE id=?",(attempt,))
                return "stopped"
        try:
            receipt=self.publisher.send(d["body"],j["id"])
            if not isinstance(receipt,str) or not receipt.strip():
                raise RuntimeError("Missing delivery receipt")
        except KnownRejected as e:
            retry=e.retryable and j["attempts"]+1<self.settings.max_attempts
            backoff=max(min(3600,30*2**j["attempts"]+random.uniform(0,5)),float(e.retry_after or 0))
            with self.db.connect(write=True) as c:
                c.execute("UPDATE jobs SET state=?,due_at=?,lease_until=NULL,error=? WHERE id=?",("pending" if retry else "failed",now+backoff,"Explicit rejection; retry scheduled" if retry else "Explicit rejection; manual correction required",j["id"]))
                c.execute("UPDATE drafts SET status=? WHERE id=?",("scheduled" if retry else "failed",d["id"]))
                c.execute("UPDATE attempts SET outcome='rejected' WHERE id=?",(attempt,))
                audit(c,"delivery.rejected",{"draft_id":d["id"],"retry":retry},now)
            return "retry" if retry else "failed"
        except Exception:
            with self.db.connect(write=True) as c:
                c.execute("UPDATE jobs SET state='uncertain',error='Ambiguous result; no automatic retry',lease_until=NULL WHERE id=?",(j["id"],))
                c.execute("UPDATE drafts SET status='uncertain' WHERE id=?",(d["id"],))
                c.execute("UPDATE attempts SET outcome='uncertain' WHERE id=?",(attempt,))
                audit(c,"delivery.uncertain",{"draft_id":d["id"]},now)
            return "uncertain"
        with self.db.connect(write=True) as c:
            c.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)",(d["content_hash"],d["id"],receipt,"demo",now))
            c.execute("UPDATE jobs SET state='sent',receipt=?,lease_until=NULL,error=NULL WHERE id=?",(receipt,j["id"]))
            c.execute("UPDATE drafts SET status='simulated' WHERE id=?",(d["id"],))
            c.execute("UPDATE attempts SET outcome='sent' WHERE id=?",(attempt,))
            audit(c,"delivery.simulated",{"draft_id":d["id"],"receipt":receipt},now)
        return "sent"
