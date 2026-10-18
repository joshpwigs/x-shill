"""Explicit, repeatable fictional analytics fixtures, never an API operation."""
import time
from .db import audit, encode
from .policy import content_hash
from .schemas import DraftBody, Metrics


def seed_metrics(db):
    now=time.time()
    with db.connect(write=True) as c:
        for i in range(6):
            did=f"demo-history-{i}"
            if c.execute("SELECT 1 FROM drafts WHERE id=?",(did,)).fetchone():
                continue
            body=DraftBody(text=["What makes a guide useful? A fictional community question.","The imaginary roadmap needs fewer mystery boxes.","Our imaginary fern wants a token mechanics glossary.","A fictional demo question: where do new community members get stuck?", "Opinion from the fictional garden: examples help.","Fictional weekly office hours: bring one question."][i]+"\n\nFictional demo · project team",topic=['onboarding','roadmap','education'][i%3],hook=['question','contrast'][i%2],kind='question' if i%2==0 else 'opinion',cta='conversation',fictional=True).model_dump()
            posted=now-(i+2)*86400
            c.execute("INSERT INTO drafts(id,body,status,content_hash,created_at,updated_at) VALUES(?,?,'simulated',?,?,?)",(did,encode(body),content_hash(body),posted,posted))
            c.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)",(content_hash(body),did,f"demo-history-receipt-{i}",'demo',posted))
            m=Metrics(captured_at=posted+26*3600,impressions=180+i*37,likes=5+i,replies=3+i,meaningful_replies=i%4,profile_visits=None if i%2 else 8+i,clicks=2+i,qualified_clicks=None,source="FICTIONAL demo measurements; no real audience or attribution").model_dump()
            c.execute("INSERT INTO metrics(draft_id,captured_at,body) VALUES(?,?,?)",(did,m['captured_at'],encode(m)))
        audit(c,'demo.analytics_loaded',{'fictional':True})
