import time
import pytest
from shill.service import Conflict
from shill.scheduler import Scheduler
from shill.db import encode
from conftest import new_draft, approved, scheduled


def test_unapproved_cannot_schedule_or_export(service):
    d=new_draft(service)
    with pytest.raises(Conflict): service.schedule(d['id'],d['revision'],time.time())
    with pytest.raises(Conflict): service.export(d['id'])


def test_approval_requires_exact_fingerprint_and_review(service):
    d=new_draft(service)
    with pytest.raises(Conflict):service.approve(d['id'],1,'wrong',True,True)
    with pytest.raises(Conflict):service.approve(d['id'],1,d['fingerprint'],False,True)
    service.approve(d['id'],1,d['fingerprint'],True,True)


def test_edit_invalidates_approval_and_cancels_schedule(service):
    d=scheduled(service)
    changed={**d['body'],'text':'A different thought about community.\n\n'+service.profile()['profile']['disclosure']}
    d=service.edit(d['id'],changed,d['revision'])
    assert d['status']=='draft' and d['approval_hash'] is None and d['job'] is None
    with pytest.raises(Conflict):service.schedule(d['id'],d['revision'],time.time())
    assert Scheduler(service).tick()=='idle'


def test_stale_browser_cannot_overwrite(service):
    d=new_draft(service)
    service.edit(d['id'],d['body'],1)
    with pytest.raises(Conflict):service.edit(d['id'],d['body'],1)


def test_profile_change_invalidates_all_approvals(service):
    d=scheduled(service)
    p=service.profile()
    p['profile']['voice']='Straightforward and specific'
    service.save_profile(p['profile'],p['version'])
    assert service.draft(d['id'])['status']=='draft'
    assert service.draft(d['id'])['job'] is None


@pytest.mark.parametrize('kind',['reply','quote','education','meme'])
def test_only_originals_can_schedule(service,kind):
    kwargs={'kind':kind}
    if kind in {'reply','quote'}:kwargs['opportunity_id']='demo-garden'
    d=approved(service,**kwargs)
    with pytest.raises(Conflict):service.schedule(d['id'],d['revision'],time.time())
    assert service.export(d['id'])['label'].startswith('FICTIONAL')


def test_stale_sources_block_even_after_approval(service):
    p=service.profile();f=p['profile']['facts'][0];f['verified_at']=time.time()-86398;f['stale_after_days']=1
    service.save_profile(p['profile'],p['version'])
    d=scheduled(service,source_ids=[f['id']])
    assert Scheduler(service).tick(time.time()+5)=='blocked'
    assert service.draft(d['id'])['status']=='failed'


@pytest.mark.parametrize('text,sources',[('Guaranteed returns for everyone.',[]),('We partnered with a major exchange.',[]),('The demo workshop launched last month.',['demo-stale']),('A fictional exchange listing is rumored.',['demo-rumor'])])
def test_claims_are_blocked_with_explanations(service,text,sources):
    d=new_draft(service,text,source_ids=sources)
    assert d['review']['blockers']
    with pytest.raises(Conflict):service.approve(d['id'],1,d['fingerprint'],True,True)


def test_verbatim_current_verified_claim_can_be_reviewed(service):
    p=service.profile();p['profile']['facts'].append({'id':'progress','text':'The fictional demo shipped a guide.','classification':'verified','source':'https://example.com/guide','verified_at':time.time(),'stale_after_days':10})
    service.save_profile(p['profile'],p['version'])
    d=approved(service,'The fictional demo shipped a guide.',source_ids=['progress'])
    assert d['status']=='approved'


def test_deduplication_across_drafts_and_deliveries(service):
    d=scheduled(service)
    assert Scheduler(service).tick()=='sent'
    other=new_draft(service,'What would make a newcomer feel welcome on day one!!!')
    assert any('duplicate' in x for x in other['review']['blockers'])
    with pytest.raises(Conflict):service.approve(other['id'],1,other['fingerprint'],True,True)


def test_storage_tampering_does_not_retain_approval(service):
    d=scheduled(service)
    with service.db.connect(write=True) as c:
        c.execute('UPDATE drafts SET body=? WHERE id=?',(encode({**d['body'],'text':'Swapped text'}),d['id']))
    assert Scheduler(service).tick()=='blocked'


def test_sensitive_and_unrelated_research_is_excluded(service):
    opp={o['id']:o for o in service.opportunities()}
    assert opp['demo-garden']['ranking']['appropriate']
    assert not opp['demo-personal']['ranking']['appropriate']
    assert not opp['demo-unrelated']['ranking']['appropriate']


def test_imported_context_cannot_mutate_under_approval(service):
    o={k:v for k,v in service.opportunities()[0].items() if k!='ranking'}
    o['text']='Changed context'
    with pytest.raises(Conflict):service.import_opportunities([o])


def test_real_manual_reply_cannot_target_fictional_conversation(service):
    service.settings.mode='manual'
    p=service.profile();p['profile']['fictional']=False
    service.save_profile(p['profile'],p['version'])
    d=new_draft(service,kind='reply',opportunity_id='demo-garden')
    with pytest.raises(Conflict,match='fictional conversation'):
        service.approve(d['id'],1,d['fingerprint'],True,True)
