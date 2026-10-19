import time
import pytest

from shill.browserbot import BrowserCampaign, BrowserUncertain, compose_reply
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
