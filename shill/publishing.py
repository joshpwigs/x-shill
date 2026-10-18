"""Publisher boundary. No live X calls are implemented until official access is verified."""
import time
import uuid
from .db import audit


class KnownRejected(Exception):
    def __init__(self, message="Explicit API rejection", retryable=False, retry_after=None):
        self.retryable, self.retry_after = retryable, retry_after
        super().__init__(message)


class UncertainDelivery(Exception):
    pass


class DemoPublisher:
    def __init__(self,db):
        self.db=db

    def send(self,body,request_key):
        # Simulated remote is durable and separate from the scheduler's delivery transaction.
        # This is not a promise that X supports idempotency keys.
        with self.db.connect(write=True) as c:
            existing=c.execute("SELECT post_id FROM mock_remote WHERE request_key=?",(request_key,)).fetchone()
            if existing:
                return existing[0]
            receipt="demo-"+uuid.uuid4().hex[:16]
            c.execute("INSERT INTO mock_remote VALUES(?,?,?,?)",(request_key,receipt,body["text"],time.time()))
            return receipt


class UnavailableXPublisher:
    def send(self,body,request_key):
        raise KnownRejected("Live X access, current rules, prices, and endpoint entitlements have not been verified.")


def reconcile_demo(db,did):
    from .service import Conflict, get_draft
    with db.connect(write=True) as c:
        d=get_draft(c,did)
        j=c.execute("SELECT * FROM jobs WHERE draft_id=?",(did,)).fetchone()
        if not j or j["state"]!="uncertain":
            raise Conflict("Only uncertain simulations can be reconciled.")
        receipt=c.execute("SELECT post_id FROM mock_remote WHERE request_key=?",(j["id"],)).fetchone()
        if not receipt:
            raise Conflict("No confirmed receipt. Kept uncertain; do not automatically resend.")
        c.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)",(d["content_hash"],did,receipt[0],"demo",time.time()))
        c.execute("UPDATE drafts SET status='simulated' WHERE id=?",(did,))
        c.execute("UPDATE jobs SET state='sent',receipt=?,lease_until=NULL WHERE draft_id=?",(receipt[0],did))
        audit(c,"delivery.reconciled",{"draft_id":did,"receipt":receipt[0],"mode":"demo"})
