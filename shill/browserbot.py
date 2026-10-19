"""Conservative browser UI worker for one locally authenticated X account.

It never reads or exports cookies, bypasses login challenges, or retries an
ambiguous click. Browser selectors are intentionally isolated in BrowserSession.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import re
import shutil
import time

from .db import audit, encode
from .policy import PROMISES, SENSITIVE, fact_status, rank_opportunity, weighted_length
from .schemas import Opportunity
from .service import Service, get_profile


class BrowserBlocked(RuntimeError):
    pass


class BrowserUncertain(RuntimeError):
    pass


def _post_id(url):
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else ""


class BrowserSession:
    def __init__(self, settings):
        self.settings = settings
        self.cfg = settings.browser
        self.base = self.cfg.get("base_url", "https://x.com").rstrip("/")

    @contextmanager
    def open(self, headed=False):
        if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            local_browsers = Path(__file__).resolve().parents[1] / "data" / "ms-playwright"
            if local_browsers.exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserBlocked("Playwright is missing. Run sh scripts/install.sh and sh scripts/install-browser.sh") from exc
        profile = Path(self.cfg.get("profile_dir", "data/x-browser-profile")).resolve()
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(profile, 0o700)
        executable = self.cfg.get("executable_path") or next((shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable") if shutil.which(name)), None)
        with sync_playwright() as pw:
            options = {
                "user_data_dir": str(profile),
                "headless": False if headed else bool(self.cfg.get("headless", True)),
                "viewport": None if headed else {"width": 1280, "height": 900},
                "locale": "en-US",
            }
            if executable:
                options["executable_path"] = executable
            if self.cfg.get("browser_args"):
                options["args"] = list(self.cfg["browser_args"])
            context = pw.chromium.launch_persistent_context(**options)
            try:
                yield context
            finally:
                context.close()

    def _challenge(self, page):
        url = page.url.casefold()
        if any(part in url for part in ("/i/flow/login", "/account/access", "/i/flow/challenge")):
            return True
        try:
            body = page.locator("body").inner_text(timeout=1500).casefold()
        except Exception:
            return False
        return any(v in body for v in ("verify your identity", "unusual activity", "complete the captcha", "authenticate your account"))

    def login(self):
        with self.open(headed=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.base + "/home", wait_until="domcontentloaded", timeout=60000)
            print("Log in to X in the opened browser. Complete any challenge yourself, then return here.")
            input("Press Enter after the Home timeline is visible: ")
            page.reload(wait_until="domcontentloaded", timeout=60000)
            if self._challenge(page):
                raise BrowserBlocked("Login is incomplete or X requires a challenge. No automation was attempted.")
            print("Browser profile is ready. Close this command and run browser-once.")

    def _extract_posts(self, page):
        results = []
        for article in page.locator("article[data-testid='tweet']").all():
            try:
                text = article.locator("[data-testid='tweetText']").first.inner_text(timeout=2000).strip()
                links = article.locator("a[href*='/status/']").all()
                href = next((v.get_attribute("href") for v in links if v.get_attribute("href") and _post_id(v.get_attribute("href"))), None)
                if not text or not href:
                    continue
                path = urlsplit(href).path
                bits = path.strip("/").split("/")
                if len(bits) < 3 or bits[1] != "status":
                    continue
                author, pid = bits[0], bits[2]
                if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", author) or not pid.isdigit():
                    continue
                stamp = article.locator("time").first.get_attribute("datetime") if article.locator("time").count() else None
                posted = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else time.time()
                reply_label = article.locator("[data-testid='reply']").first.get_attribute("aria-label") or ""
                reply_match = re.search(r"([\d,]+)\s+repl", reply_label, re.I)
                results.append({
                    "id": "x_" + pid,
                    "author": author,
                    "text": text,
                    "url": "https://x.com/" + author + "/status/" + pid,
                    "posted_at": posted,
                    "thread_id": pid,
                    "context": "Discovered from a configured public X search/account page.",
                    "inbound": False,
                    "replies": int(reply_match.group(1).replace(",", "")) if reply_match else 0,
                    "likes": 0,
                    "fictional": False,
                })
            except Exception:
                continue
        return results

    def discover(self):
        limit = max(1, min(100, int(self.cfg.get("discovery_limit", 30))))
        found = {}
        paths = [f"/search?q={quote(q)}&src=typed_query&f=live" for q in self.cfg.get("search_queries", [])]
        paths += ["/" + a.lstrip("@") for a in self.cfg.get("watch_accounts", [])]
        with self.open() as context:
            page = context.pages[0] if context.pages else context.new_page()
            for path in paths:
                page.goto(urljoin(self.base + "/", path.lstrip("/")), wait_until="domcontentloaded", timeout=60000)
                if self._challenge(page):
                    raise BrowserBlocked("X needs an interactive login or account challenge. Run browser-login; no posting was attempted.")
                try:
                    page.locator("article[data-testid='tweet']").first.wait_for(timeout=15000)
                except Exception:
                    continue
                for item in self._extract_posts(page):
                    found[item["id"]] = item
                    if len(found) >= limit:
                        return list(found.values())
        return list(found.values())

    def reply(self, target_url, text, dry_run=True):
        clicked = False
        with self.open() as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            if self._challenge(page):
                raise BrowserBlocked("X requires an interactive login or account challenge. No reply was clicked.")
            pid = _post_id(target_url)
            link = page.locator(f"article[data-testid='tweet'] a[href*='/status/{pid}']").first
            article = link.locator("xpath=ancestor::article")
            article.wait_for(timeout=15000)
            article.locator("[data-testid='reply']").first.click()
            editor = page.locator("[data-testid='tweetTextarea_0']").last
            editor.wait_for(timeout=10000)
            editor.fill(text)
            if dry_run:
                return {"outcome": "dry_run", "target": target_url, "text": text}
            try:
                clicked = True
                page.locator("[data-testid='tweetButton']").last.click()
                toast = page.locator("[data-testid='toast']")
                toast.wait_for(timeout=15000)
                message = toast.inner_text().casefold()
                if not any(word in message for word in ("sent", "posted", "reply")):
                    raise RuntimeError("Unexpected confirmation")
                return {"outcome": "sent", "receipt": f"ui:{_post_id(target_url)}:{int(time.time())}"}
            except Exception as exc:
                if clicked:
                    raise BrowserUncertain("The Post button was clicked but no clear confirmation appeared. Automation has stopped; inspect your timeline before continuing.") from exc
                raise BrowserBlocked("The reply editor could not submit. No confirmed click occurred.") from exc


def compose_reply(profile, opportunity, variant=0):
    now = time.time()
    facts = [f for f in profile["facts"] if fact_status(f, now) == "verified"]
    if profile.get("fictional"):
        raise BrowserBlocked("Replace the fictional project profile before browser posting.")
    if not facts or not profile.get("official_links"):
        raise BrowserBlocked("Browser posting needs at least one current verified fact and one official link in Project knowledge.")
    haystack = opportunity["text"].casefold()
    hits = [k for k in profile.get("keywords", []) if k.strip() and k.casefold() in haystack]
    topic = hits[0] if hits else (profile.get("chain") or "crypto")
    link = profile["official_links"][0]
    templates = [
        "The {topic} angle is worth adding here. {name}: {fact} {link}\n\n{disclosure}",
        "For anyone comparing {topic} projects, {name} is one to research: {fact} {link}\n\n{disclosure}",
        "A relevant {topic} example is {name}. {fact} Details: {link}\n\n{disclosure}",
        "One concrete data point for this {topic} discussion: {fact} {link}\n\n{disclosure}",
    ]
    for offset in range(len(facts) * len(templates)):
        fact = facts[(variant + offset) % len(facts)]
        template = templates[(variant + offset) % len(templates)]
        text = template.format(topic=topic, name=profile["name"], fact=fact["text"], link=link, disclosure=profile["disclosure"])
        if weighted_length(text) <= 280 and not PROMISES.search(text) and not SENSITIVE.search(opportunity["text"]):
            return text
    raise BrowserBlocked("No verified fact fits in a 280-character reply with the required disclosure and link.")


class BrowserCampaign:
    def __init__(self, db, settings, session=None):
        self.db, self.settings = db, settings
        self.session = session or BrowserSession(settings)
        Service(db, settings).initialize()

    def login(self):
        if self.settings.mode != "browser":
            raise BrowserBlocked("Set mode = \"browser\" in config.toml first.")
        self.session.login()

    def _recover(self):
        with self.db.connect(write=True) as c:
            rows = c.execute("SELECT id FROM browser_queue WHERE state='inflight'").fetchall()
            if rows:
                c.execute("UPDATE browser_queue SET state='uncertain',error='Process ended during a browser send; inspect X before resuming' WHERE state='inflight'")
                c.execute("UPDATE controls SET stopped=1 WHERE id=1")
                audit(c, "browser.recovered_uncertain", {"count": len(rows)})

    def _quiet(self, now):
        hour = datetime.fromtimestamp(now, ZoneInfo(self.settings.timezone)).hour
        start, end = self.settings.quiet_start, self.settings.quiet_end
        return False if start == end else (start <= hour < end if start < end else hour >= start or hour < end)

    def _import(self, items):
        added = 0
        with self.db.connect(write=True) as c:
            profile, _ = get_profile(c)
            for raw in items:
                try:
                    opp = Opportunity(**raw).model_dump()
                except Exception:
                    continue
                ranking = rank_opportunity(c, opp, profile)
                prior = c.execute("SELECT 1 FROM browser_queue WHERE author=? OR thread_id=?", (opp["author"], opp["thread_id"])).fetchone()
                c.execute("INSERT OR IGNORE INTO opportunities VALUES(?,?,?)", (opp["id"], encode(opp), time.time()))
                if ranking["appropriate"] and ranking["score"] >= int(self.settings.browser.get("minimum_score", 35)) and not prior:
                    cursor = c.execute("INSERT OR IGNORE INTO browser_queue(id,author,url,thread_id,body,score,state,discovered_at) VALUES(?,?,?,?,?,?,'queued',?)",
                                       (opp["id"], opp["author"], opp["url"], opp["thread_id"], encode(opp), ranking["score"], time.time()))
                    added += cursor.rowcount
            audit(c, "browser.discovery", {"found": len(items), "queued": added})
        return added

    def run_once(self):
        if self.settings.mode != "browser":
            raise BrowserBlocked("Set mode = \"browser\" in config.toml first.")
        self._recover()
        with self.db.connect() as c:
            if c.execute("SELECT stopped FROM controls WHERE id=1").fetchone()[0]:
                return {"state": "stopped", "message": "Emergency stop is active."}
        items = self.session.discover()
        added = self._import(items)
        now = time.time()
        if self._quiet(now):
            return {"state": "quiet_hours", "discovered": len(items), "queued": added}
        with self.db.connect() as c:
            attempts = c.execute("SELECT COUNT(*) FROM attempts WHERE at>=?", (now - 86400,)).fetchone()[0]
            last = c.execute("SELECT MAX(at) FROM attempts").fetchone()[0]
            row = c.execute("SELECT * FROM browser_queue WHERE state IN ('queued','previewed') ORDER BY score DESC,discovered_at LIMIT 1").fetchone()
            profile, _ = get_profile(c)
        if attempts >= self.settings.daily_post_limit:
            return {"state": "daily_limit", "attempts": attempts, "queued": added}
        if last and now - last < self.settings.minimum_gap_minutes * 60:
            return {"state": "spacing", "next_at": last + self.settings.minimum_gap_minutes * 60, "queued": added}
        if not row:
            return {"state": "idle", "discovered": len(items), "queued": added}
        opportunity = json.loads(row["body"])
        variant = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16)
        text = compose_reply(profile, opportunity, variant)
        dry_run = bool(self.settings.browser.get("dry_run", True))
        if dry_run:
            result = self.session.reply(row["url"], text, dry_run=True)
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='previewed',draft_text=?,error=NULL WHERE id=?", (text, row["id"]))
                audit(c, "browser.previewed", {"item_id": row["id"], "weighted_length": weighted_length(text)})
            return {"state": "previewed", "target": row["url"], "text": text, "queued": added, **result}
        with self.db.connect(write=True) as c:
            claimed = c.execute("UPDATE browser_queue SET state='inflight',draft_text=?,attempted_at=? WHERE id=? AND state IN ('queued','previewed')", (text, now, row["id"]))
            if claimed.rowcount != 1:
                return {"state": "busy", "message": "Another local worker claimed this item."}
            c.execute("INSERT INTO attempts(job_id,at,outcome) VALUES(?,?,'started')", ("browser:" + row["id"], now))
            audit(c, "browser.send_started", {"item_id": row["id"], "target": row["url"]})
        try:
            result = self.session.reply(row["url"], text, dry_run=False)
        except BrowserUncertain as exc:
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='uncertain',error=? WHERE id=?", (str(exc), row["id"]))
                c.execute("UPDATE attempts SET outcome='uncertain' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
                c.execute("UPDATE controls SET stopped=1 WHERE id=1")
                audit(c, "browser.send_uncertain", {"item_id": row["id"], "stopped": True})
            raise
        except BrowserBlocked as exc:
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='failed',error=? WHERE id=?", (str(exc), row["id"]))
                c.execute("UPDATE attempts SET outcome='rejected' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
            raise
        with self.db.connect(write=True) as c:
            c.execute("UPDATE browser_queue SET state='sent',receipt=?,error=NULL WHERE id=?", (result["receipt"], row["id"]))
            c.execute("UPDATE attempts SET outcome='sent' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
            audit(c, "browser.sent", {"item_id": row["id"], "receipt": result["receipt"]})
        return {"state": "sent", "target": row["url"], "receipt": result["receipt"], "queued": added}

    def status(self):
        with self.db.connect() as c:
            counts = {r["state"]: r["n"] for r in c.execute("SELECT state,COUNT(*) n FROM browser_queue GROUP BY state")}
            stopped = bool(c.execute("SELECT stopped FROM controls WHERE id=1").fetchone()[0])
            recent = [dict(r) for r in c.execute("SELECT id,author,url,score,state,draft_text,attempted_at,receipt,error FROM browser_queue ORDER BY discovered_at DESC LIMIT 10")]
        return {"mode": self.settings.mode, "dry_run": bool(self.settings.browser.get("dry_run", True)), "stopped": stopped, "counts": counts, "recent": recent}

    def run_forever(self):
        interval = int(self.settings.browser.get("poll_minutes", 20)) * 60
        print("Browser campaign running. Ctrl+C stops it; status is stored in SQLite.")
        while True:
            try:
                print(json.dumps(self.run_once(), ensure_ascii=False))
            except BrowserBlocked as exc:
                print(json.dumps({"state": "blocked", "message": str(exc)}))
            except BrowserUncertain as exc:
                print(json.dumps({"state": "uncertain", "message": str(exc)}))
                return
            time.sleep(interval)
