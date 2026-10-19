import time
import pytest

from shill.browserbot import BrowserCampaign, BrowserUncertain, compose_reply, rank_browser_opportunity
from shill.browser_generation import ollama_reply, template_reply
from shill.config import Settings
from shill.db import Database
from shill.service import Service


class FakeBrowser:
    def __init__(self, items, outcome="sent"):
        self.items = items
        self.outcome = outcome
        self.calls = []

    def discover(self):
        return self.items

    def reply(self, url, text, dry_run=True):
        self.calls.append((url, text, dry_run))
        if self.outcome == "uncertain":
            raise BrowserUncertain("clicked without confirmation")
        return {"outcome": "dry_run"} if dry_run else {"outcome": "sent", "receipt": "ui:100:1"}


def real_profile(service):
    item = service.profile()
    item["profile"].update({
        "name": "Cro Test",
        "ticker": "CROTEST",
        "chain": "Cronos",
        "account": "CroTest",
        "official_links": ["https://example.com/project"],
        "facts": [{"id": "utility", "text": "Cro Test publishes an open project guide.", "classification": "verified",
                   "source": "https://example.com/source", "verified_at": time.time(), "stale_after_days": 30}],
        "keywords": ["Cronos", "wallet"],
        "monitored_accounts": ["CryptoWriter"],
        "disclosure": "Project team",
        "fictional": False,
    })
    service.save_profile(item["profile"], item["version"])
    return item["profile"]


def opportunity(pid="100", author="CryptoWriter"):
    return {"id": "x_" + pid, "author": author, "text": "What should a new Cronos wallet explain?", "url": f"https://x.com/{author}/status/{pid}",
            "posted_at": time.time(), "thread_id": pid, "context": "public post", "inbound": False,
            "replies": 3, "likes": 0, "fictional": False}


def campaign(tmp_path, dry_run=True, outcome="sent"):
    settings = Settings(mode="browser", database=str(tmp_path / "browser.sqlite3"), quiet_start=0, quiet_end=0,
                        minimum_gap_minutes=1, daily_post_limit=20,
                        browser={"dry_run": dry_run, "search_queries": ["Cronos"], "watch_accounts": [], "minimum_score": 20}).validate()
    db = Database(settings.database)
    service = Service(db, settings)
    service.initialize()
    real_profile(service)
    fake = FakeBrowser([opportunity()], outcome)
    return BrowserCampaign(db, settings, fake), fake


def test_compose_reply_uses_verified_fact_and_disclosure(tmp_path):
    bot, _ = campaign(tmp_path)
    with bot.db.connect() as c:
        from shill.service import get_profile
        profile, _ = get_profile(c)
    text = compose_reply(profile, opportunity())
    assert "Cro Test publishes an open project guide." in text
    assert "https://example.com/project" in text
    assert text.endswith("Project team")


def test_dry_run_previews_without_counting_attempt(tmp_path):
    bot, fake = campaign(tmp_path, dry_run=True)
    result = bot.run_once()
    assert result["state"] == "previewed"
    assert fake.calls[0][2] is True
    with bot.db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_live_run_sends_once_and_deduplicates_author(tmp_path):
    bot, fake = campaign(tmp_path, dry_run=False)
    assert bot.run_once()["state"] == "sent"
    assert fake.calls[0][2] is False
    bot.settings.minimum_gap_minutes = 1
    with bot.db.connect(write=True) as c:
        c.execute("UPDATE attempts SET at=?", (time.time() - 120,))
    fake.items = [opportunity("101")]
    assert bot.run_once()["state"] == "idle"
    assert len(fake.calls) == 1


def test_uncertain_click_stops_campaign(tmp_path):
    bot, _ = campaign(tmp_path, dry_run=False, outcome="uncertain")
    with pytest.raises(BrowserUncertain):
        bot.run_once()
    status = bot.status()
    assert status["stopped"] is True
    assert status["counts"]["uncertain"] == 1


def test_contextual_templates_avoid_recent_wording(tmp_path):
    bot, _ = campaign(tmp_path)
    with bot.db.connect() as c:
        from shill.service import get_profile
        profile, _ = get_profile(c)
    first = template_reply(profile, opportunity(), variant=0)
    second = template_reply(profile, opportunity(), variant=0, recent=[first["text"]])
    assert second["text"] != first["text"]
    assert second["source_id"] == "utility"


def test_browser_ranking_requires_project_keyword_and_uses_feedback(tmp_path):
    bot, _ = campaign(tmp_path)
    with bot.db.connect(write=True) as c:
        from shill.service import get_profile
        profile, _ = get_profile(c)
        unrelated = opportunity("200", "AnotherWriter")
        unrelated["text"] = "Which chart setup looks best today?"
        assert rank_browser_opportunity(c, unrelated, profile, bot.settings.browser)["appropriate"] is False
        relevant = opportunity("201", "AnotherWriter")
        assert rank_browser_opportunity(c, relevant, profile, bot.settings.browser)["appropriate"] is True
        c.execute("INSERT INTO browser_queue(id,author,url,thread_id,body,score,state,discovered_at) VALUES('old','FeedbackWriter','https://x.com/FeedbackWriter/status/1','1','{}',50,'sent',?)", (time.time()-20*86400,))
        c.execute("INSERT INTO browser_feedback(item_id,value,note,at) VALUES('old',1,'useful',?)", (time.time(),))
        rewarded = rank_browser_opportunity(c, opportunity("202", "FeedbackWriter"), profile, bot.settings.browser)
        neutral = rank_browser_opportunity(c, opportunity("203", "NeutralWriter"), profile, bot.settings.browser)
        assert rewarded["score"] > neutral["score"]


def test_ollama_reply_is_validated_against_verified_facts(tmp_path, monkeypatch):
    bot, _ = campaign(tmp_path)
    bot.settings.browser.update(reply_provider="ollama", ollama_model="local-test", ollama_url="http://127.0.0.1:11434")
    with bot.db.connect() as c:
        from shill.service import get_profile
        profile, _ = get_profile(c)
    text = "For the Cronos question: Cro Test publishes an open project guide. https://example.com/project\n\nProject team"
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": __import__('json').dumps({"text": text, "source_id": "utility", "style": "answer"})}}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr("shill.browser_generation.httpx.Client", Client)
    result = ollama_reply(bot.settings, profile, opportunity())
    assert result["provider"] == "ollama"
    assert result["source_id"] == "utility"
