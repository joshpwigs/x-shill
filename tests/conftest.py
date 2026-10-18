import time
import pytest
from shill.config import Settings
from shill.db import Database
from shill.service import Service
from shill.schemas import DraftBody


@pytest.fixture
def service(tmp_path):
    settings=Settings(database=str(tmp_path/'test.sqlite3'),quiet_start=0,quiet_end=0,minimum_gap_minutes=1,daily_post_limit=3)
    s=Service(Database(settings.database),settings)
    s.initialize()
    return s


def new_draft(s,text="What would make a newcomer feel welcome on day one?",**kwargs):
    return s.create_draft(DraftBody(text=text+'\n\n'+s.profile()['profile']['disclosure'],**kwargs).model_dump())


def approved(s,text="What would make a newcomer feel welcome on day one?",**kwargs):
    d=new_draft(s,text,**kwargs)
    return s.approve(d['id'],d['revision'],d['fingerprint'],True,True)


def scheduled(s,**kwargs):
    d=approved(s,**kwargs)
    return s.schedule(d['id'],d['revision'],time.time())
