import concurrent.futures
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from shill.scheduler import Scheduler
from shill.service import Service, Conflict
from shill.db import Database
from shill.publishing import KnownRejected, DemoPublisher, reconcile_demo
from conftest import approved, scheduled


class FakePublisher:
    def __init__(self,error=None): self.calls=0; self.error=error
    def send(self,body,key):
        self.calls+=1
        if self.error:raise self.error
        return 'test-receipt-'+key


def test_pending_job_survives_restart(service):
    d=scheduled(service)
    restarted=Service(Database(service.db.path),service.settings)
    p=FakePublisher()
    assert Scheduler(restarted,p).tick()=='sent'
    assert restarted.draft(d['id'])['status']=='simulated' and p.calls==1
    assert Scheduler(restarted,p).tick()=='idle' and p.calls==1


def test_concurrent_workers_send_once(service):
    scheduled(service);publisher=FakePublisher()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:Scheduler(service,publisher).tick(),range(2)))
    assert results.count('sent')==1 and publisher.calls==1


def test_ambiguous_result_never_auto_retries(service):
    d=scheduled(service);p=FakePublisher(TimeoutError())
    assert Scheduler(service,p).tick()=='uncertain'
    assert Scheduler(service,p).tick(time.time()+86400)=='idle'
    assert p.calls==1 and service.draft(d['id'])['status']=='uncertain'
    with pytest.raises(Conflict):service.cancel(d['id'])
    with pytest.raises(Conflict):service.edit(d['id'],d['body'],1)


def test_missing_receipt_is_uncertain(service):
    d=scheduled(service)
    p=FakePublisher();p.send=lambda *args:None
    assert Scheduler(service,p).tick()=='uncertain'
    assert service.draft(d['id'])['job']['receipt'] is None


def test_expired_inflight_lease_is_quarantined_after_restart(service):
    d=scheduled(service);now=time.time()
    with service.db.connect(write=True) as c:
        c.execute("UPDATE jobs SET state='inflight',lease_until=? WHERE draft_id=?",(now-1,d['id']))
        c.execute("UPDATE drafts SET status='inflight' WHERE id=?",(d['id'],))
    p=FakePublisher()
    Scheduler(Service(Database(service.db.path),service.settings),p).tick(now)
    assert p.calls==0 and service.draft(d['id'])['status']=='uncertain'
    with pytest.raises(Conflict):reconcile_demo(service.db,d['id'])


def test_crash_after_remote_acceptance_can_reconcile_without_resending(service):
    d=scheduled(service);remote=DemoPublisher(service.db)
    with service.db.connect(write=True) as c:
        c.execute("UPDATE jobs SET state='inflight',lease_until=? WHERE draft_id=?",(time.time()-1,d['id']))
        c.execute("UPDATE drafts SET status='inflight' WHERE id=?",(d['id'],))
    receipt=remote.send(d['body'],d['job']['id'])
    Scheduler(service).tick()
    reconcile_demo(service.db,d['id'])
    assert service.draft(d['id'])['job']['receipt']==receipt
    with service.db.connect() as c:assert c.execute('SELECT count(*) FROM mock_remote').fetchone()[0]==1


def test_explicit_rate_rejection_uses_retry_after_and_bounded_attempts(service):
    d=scheduled(service);p=FakePublisher(KnownRejected(retryable=True,retry_after=90));s=Scheduler(service,p)
    now=time.time()
    assert s.tick(now)=='retry'
    job=service.draft(d['id'])['job']
    assert job['due_at']>=now+90 and job['attempts']==1
    assert s.tick(now+10)=='idle'
    service.settings.max_attempts=2
    assert s.tick(now+91)=='failed'
    assert p.calls==2
    assert s.tick(now+5000)=='idle'


def test_permanent_rejection_does_not_retry(service):
    d=scheduled(service);p=FakePublisher(KnownRejected())
    assert Scheduler(service,p).tick()=='failed'
    assert Scheduler(service,p).tick(time.time()+4000)=='idle'
    assert p.calls==1 and service.draft(d['id'])['status']=='failed'


def test_rate_limits_and_minimum_gap_are_durable(service):
    service.settings.daily_post_limit=1
    first=scheduled(service);s=Scheduler(service);now=time.time()
    assert s.tick(now)=='sent'
    second=scheduled(service,text='A readable glossary is a kind way to welcome curious builders.')
    assert Scheduler(Service(Database(service.db.path),service.settings)).tick(now+1)=='limited'
    assert service.draft(second['id'])['job']['due_at']>now+86400


def test_quiet_hours_cross_midnight_with_timezone(service):
    service.settings.quiet_start=22;service.settings.quiet_end=8
    s=Scheduler(service)
    now=datetime(2026,10,18,23,0,tzinfo=ZoneInfo('America/Chicago')).timestamp()
    assert s.quiet(now)
    assert s.quiet(datetime(2026,10,19,7,59,tzinfo=ZoneInfo('America/Chicago')).timestamp())
    with service.db.connect() as c:allowed=s.next_allowed(c,now)
    local=datetime.fromtimestamp(allowed,ZoneInfo('America/Chicago'))
    assert local.hour==8 and local.day==19


def test_emergency_stop_survives_restart(service):
    d=scheduled(service);service.stop(True);p=FakePublisher()
    assert Scheduler(Service(Database(service.db.path),service.settings),p).tick()=='stopped'
    assert p.calls==0
    service.stop(False)
    assert Scheduler(service,p).tick()=='sent'


def test_manual_scheduling_never_calls_publisher(service):
    service.settings.mode='manual';profile=service.profile();profile['profile']['fictional']=False
    service.save_profile(profile['profile'],profile['version'])
    d=scheduled(service);p=FakePublisher()
    assert Scheduler(service,p).tick()=='ready' and p.calls==0
    assert service.export(d['id'])['label'].startswith('Individually')


def test_manual_mode_refuses_fictional_profile(service):
    service.settings.mode='manual'
    with pytest.raises(Conflict):approved(service)
