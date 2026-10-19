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
import math
import os
import re
import shutil
import threading
import time

from .browser_generation import CompositionError, compose, template_reply, validate_reply
from .db import audit, encode
from .policy import SENSITIVE, weighted_length
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

    def _diagnostic(self, page, label):
        directory = Path(self.cfg.get("diagnostics_dir", "data/browser-diagnostics")).resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe = re.sub(r"[^a-z0-9_-]", "-", label.casefold())[:40]
        path = directory / f"{int(time.time())}-{safe}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            os.chmod(path, 0o600)
            return str(path)
        except Exception:
            return ""

    @staticmethod
    def _first(root, selectors):
        for selector in selectors:
            locator = root.locator(selector)
            if locator.count():
                return locator.first
        return root.locator(selectors[0]).first

    @staticmethod
    def _last(root, selectors):
        for selector in selectors:
            locator = root.locator(selector)
            if locator.count():
                return locator.last
        return root.locator(selectors[0]).last

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

    def health(self):
        with self.open() as context:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(self.base + "/home", wait_until="domcontentloaded", timeout=60000)
                challenged = self._challenge(page)
                return {"ok": not challenged, "state": "challenge" if challenged else "ready", "url": page.url}
            except Exception:
                diagnostic = self._diagnostic(page, "health-failure")
                return {"ok": False, "state": "unreachable", "diagnostic": diagnostic}

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
        articles = page.locator("article[data-testid='tweet']")
        if not articles.count():
            articles = page.locator("article[role='article']")
        for article in articles.all():
            try:
                text = self._first(article, ["[data-testid='tweetText']", "[lang]"]).inner_text(timeout=2000).strip()
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
                def count(testid, word):
                    locator = article.locator(f"[data-testid='{testid}']").first
                    label = locator.get_attribute("aria-label") if locator.count() else ""
                    match = re.search(rf"([\d,.]+[KMB]?)\s+{word}", label or "", re.I)
                    if not match:
                        return 0
                    value = match.group(1).replace(",", "").upper()
                    factor = 1
                    if value[-1:] in {"K", "M", "B"}:
                        factor = {"K": 1000, "M": 1000000, "B": 1000000000}[value[-1]]
                        value = value[:-1]
                    return int(float(value) * factor)
                results.append({
                    "id": "x_" + pid,
                    "author": author,
                    "text": text,
                    "url": "https://x.com/" + author + "/status/" + pid,
                    "posted_at": posted,
                    "thread_id": pid,
                    "context": "Discovered from a configured public X search/account page.",
                    "inbound": False,
                    "replies": count("reply", "repl(?:y|ies)"),
                    "likes": count("like", "likes?"),
                    "fictional": False,
                })
            except Exception:
                continue
        return results

    def discover(self):
        limit = max(1, min(100, int(self.cfg.get("discovery_limit", 30))))
        per_source = max(1, min(30, int(self.cfg.get("per_source_limit", 10))))
        scrolls = max(0, min(4, int(self.cfg.get("discovery_scrolls", 1))))
        found = {}
        paths = [f"/search?q={quote(q)}&src=typed_query&f=live" for q in self.cfg.get("search_queries", [])]
        paths += ["/" + a.lstrip("@") for a in self.cfg.get("watch_accounts", [])]
        with self.open() as context:
            page = context.pages[0] if context.pages else context.new_page()
            for path in paths:
                try:
                    page.goto(urljoin(self.base + "/", path.lstrip("/")), wait_until="domcontentloaded", timeout=60000)
                    if self._challenge(page):
                        diagnostic = self._diagnostic(page, "login-challenge")
                        raise BrowserBlocked(f"X needs an interactive login or account challenge. Run browser-login; diagnostic: {diagnostic or 'unavailable'}")
                    self._first(page, ["article[data-testid='tweet']", "article[role='article']"]).wait_for(timeout=15000)
                except Exception:
                    if self._challenge(page):
                        raise
                    continue
                source_items = {}
                for _ in range(scrolls + 1):
                    for item in self._extract_posts(page):
                        source_items[item["id"]] = item
                    if len(source_items) >= per_source:
                        break
                    page.mouse.wheel(0, 1400)
                    page.wait_for_timeout(500)
                for item in sorted(source_items.values(), key=lambda value: value["posted_at"], reverse=True)[:per_source]:
                    found[item["id"]] = item
        return sorted(found.values(), key=lambda value: value["posted_at"], reverse=True)[:limit]

    def reply(self, target_url, text, dry_run=True):
        clicked = False
        with self.open() as context:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                if self._challenge(page):
                    diagnostic = self._diagnostic(page, "reply-challenge")
                    raise BrowserBlocked(f"X requires an interactive login or account challenge. Diagnostic: {diagnostic or 'unavailable'}")
                pid = _post_id(target_url)
                link = page.locator(f"article a[href*='/status/{pid}']").first
                article = link.locator("xpath=ancestor::article")
                article.wait_for(timeout=15000)
                self._first(article, ["[data-testid='reply']", "button[aria-label*='Reply']"]).click()
                editor = self._last(page, ["[data-testid='tweetTextarea_0']", "[role='textbox'][contenteditable='true']"])
                editor.wait_for(timeout=10000)
                editor.fill(text)
                if dry_run:
                    return {"outcome": "dry_run", "target": target_url, "text": text}
                clicked = True
                self._last(page, ["[data-testid='tweetButton']", "[data-testid='tweetButtonInline']", "button:has-text('Post')"]).click()
                toast = self._first(page, ["[data-testid='toast']", "[role='alert']"])
                toast.wait_for(timeout=int(self.cfg.get("confirmation_timeout_ms", 15000)))
                message = toast.inner_text().casefold()
                if not any(word in message for word in ("sent", "posted", "reply")):
                    raise RuntimeError("Unexpected confirmation")
                return {"outcome": "sent", "receipt": f"ui:{_post_id(target_url)}:{int(time.time())}"}
            except Exception as exc:
                diagnostic = self._diagnostic(page, "send-uncertain" if clicked else "reply-failure")
                if clicked:
                    raise BrowserUncertain(f"The Post button was clicked but no clear confirmation appeared. Automation stopped; diagnostic: {diagnostic or 'unavailable'}") from exc
                if isinstance(exc, BrowserBlocked):
                    raise
                raise BrowserBlocked(f"The reply editor could not submit. No confirmed click occurred. Diagnostic: {diagnostic or 'unavailable'}") from exc


def compose_reply(profile, opportunity, variant=0):
    """Compatibility wrapper for the offline template composer."""
    try:
        return template_reply(profile, opportunity, variant)["text"]
    except CompositionError as exc:
        raise BrowserBlocked(str(exc)) from exc


def rank_browser_opportunity(c, opportunity, profile, cfg, now=None):
    now = now or time.time()
    text = (opportunity["text"] + " " + opportunity.get("context", "")).casefold()
    keywords = [key.casefold() for key in profile.get("keywords", []) if key.strip()]
    hits = [key for key in keywords if key in text]
    watched = opportunity["author"].casefold() in {a.lstrip("@").casefold() for a in cfg.get("watch_accounts", []) + profile.get("monitored_accounts", [])}
    blocked = opportunity["author"].casefold() in {a.lstrip("@").casefold() for a in cfg.get("blocked_accounts", [])}
    own = opportunity["author"].casefold() == profile.get("account", "").casefold()
    sensitive = bool(SENSITIVE.search(text))
    age_hours = max(0, (now - opportunity["posted_at"]) / 3600)
    useful = "?" in text or bool(re.search(r"\b(how|why|which|explain|help|feedback|compare|learn)\b", text))
    engagement = opportunity.get("replies", 0) + opportunity.get("likes", 0)
    velocity = engagement / max(1, age_hours)
    feedback = c.execute("SELECT AVG(f.value) FROM browser_feedback f JOIN browser_queue q ON q.id=f.item_id WHERE q.author=?", (opportunity["author"],)).fetchone()[0] or 0
    require_keyword = bool(cfg.get("require_keyword_match", True))
    relevant = bool(hits) if require_keyword else bool(hits or watched or opportunity.get("inbound"))
    appropriate = relevant and not sensitive and not blocked and not own and age_hours <= 72
    score = 25 * min(2, len(hits)) + 10 * watched + 12 * useful + max(0, 18 - age_hours / 2)
    score += min(15, math.log2(velocity + 1) * 5) + feedback * 8
    score = max(0, min(100, round(score))) if appropriate else 0
    reasons = [f"Keyword match: {', '.join(hits)}" if hits else "No project keyword match",
               f"{age_hours:.1f} hours old", f"Engagement velocity: {velocity:.1f}/hour"]
    if watched: reasons.append("Priority account")
    if useful: reasons.append("Question or useful discussion prompt")
    if feedback: reasons.append(f"Prior author feedback: {feedback:+.1f}")
    if sensitive: reasons.append("Sensitive context excluded")
    if blocked or own: reasons.append("Blocked or project-owned account")
    return {"score": score, "appropriate": appropriate, "keyword_hits": hits, "velocity": round(velocity, 2), "reasons": reasons}


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
                ranking = rank_browser_opportunity(c, opp, profile, self.settings.browser)
                cooldown = max(1, min(365, int(self.settings.browser.get("author_cooldown_days", 14)))) * 86400
                prior = c.execute("SELECT 1 FROM browser_queue WHERE thread_id=? OR (author=? AND discovered_at>=?)", (opp["thread_id"], opp["author"], time.time()-cooldown)).fetchone()
                c.execute("INSERT OR IGNORE INTO opportunities VALUES(?,?,?)", (opp["id"], encode(opp), time.time()))
                if ranking["appropriate"] and ranking["score"] >= int(self.settings.browser.get("minimum_score", 35)) and not prior:
                    cursor = c.execute("INSERT OR IGNORE INTO browser_queue(id,author,url,thread_id,body,score,state,discovered_at,ranking,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                                       (opp["id"], opp["author"], opp["url"], opp["thread_id"], encode(opp), ranking["score"], time.time(), encode(ranking), time.time()))
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
            states = "('queued')" if bool(self.settings.browser.get("dry_run", True)) else "('queued','previewed')"
            row = c.execute(f"SELECT * FROM browser_queue WHERE state IN {states} ORDER BY score DESC,discovered_at LIMIT 1").fetchone()
            profile, _ = get_profile(c)
            recent = [r[0] for r in c.execute("SELECT draft_text FROM browser_queue WHERE id!=? AND draft_text IS NOT NULL AND state IN ('previewed','sent') ORDER BY COALESCE(updated_at,discovered_at) DESC LIMIT 20", (row["id"],))] if row else []
        if attempts >= self.settings.daily_post_limit:
            return {"state": "daily_limit", "attempts": attempts, "queued": added}
        if last and now - last < self.settings.minimum_gap_minutes * 60:
            return {"state": "spacing", "next_at": last + self.settings.minimum_gap_minutes * 60, "queued": added}
        if not row:
            return {"state": "idle", "discovered": len(items), "queued": added}
        opportunity = json.loads(row["body"])
        variant = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16)
        try:
            if row["generator"] == "manual" and row["draft_text"]:
                text = validate_reply(row["draft_text"], profile, opportunity, recent, float(self.settings.browser.get("similarity_limit", 0.82)))["text"]
                generated = {"text": text, "provider": "manual", "style": "edited"}
            else:
                generated = compose(self.settings, profile, opportunity, variant, recent)
                text = generated["text"]
        except CompositionError as exc:
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='failed',error=?,updated_at=? WHERE id=?", (str(exc), time.time(), row["id"]))
            raise BrowserBlocked(str(exc)) from exc
        dry_run = bool(self.settings.browser.get("dry_run", True))
        if dry_run:
            result = self.session.reply(row["url"], text, dry_run=True)
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='previewed',draft_text=?,generator=?,style=?,error=NULL,updated_at=? WHERE id=?", (text, generated["provider"], generated["style"], time.time(), row["id"]))
                audit(c, "browser.previewed", {"item_id": row["id"], "weighted_length": weighted_length(text)})
            return {"state": "previewed", "target": row["url"], "text": text, "queued": added, **result}
        with self.db.connect(write=True) as c:
            claimed = c.execute("UPDATE browser_queue SET state='inflight',draft_text=?,generator=?,style=?,attempted_at=?,updated_at=? WHERE id=? AND state IN ('queued','previewed')", (text, generated["provider"], generated["style"], now, now, row["id"]))
            if claimed.rowcount != 1:
                return {"state": "busy", "message": "Another local worker claimed this item."}
            c.execute("INSERT INTO attempts(job_id,at,outcome) VALUES(?,?,'started')", ("browser:" + row["id"], now))
            audit(c, "browser.send_started", {"item_id": row["id"], "target": row["url"]})
        try:
            result = self.session.reply(row["url"], text, dry_run=False)
        except BrowserUncertain as exc:
            diagnostic = str(exc).rsplit("diagnostic: ", 1)[-1] if "diagnostic: " in str(exc) else ""
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='uncertain',error=?,diagnostic=?,updated_at=? WHERE id=?", (str(exc), diagnostic, time.time(), row["id"]))
                c.execute("UPDATE attempts SET outcome='uncertain' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
                c.execute("UPDATE controls SET stopped=1 WHERE id=1")
                audit(c, "browser.send_uncertain", {"item_id": row["id"], "stopped": True})
            raise
        except BrowserBlocked as exc:
            diagnostic = str(exc).rsplit("diagnostic: ", 1)[-1] if "diagnostic: " in str(exc) else ""
            with self.db.connect(write=True) as c:
                c.execute("UPDATE browser_queue SET state='failed',error=?,diagnostic=?,updated_at=? WHERE id=?", (str(exc), diagnostic, time.time(), row["id"]))
                c.execute("UPDATE attempts SET outcome='rejected' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
            raise
        with self.db.connect(write=True) as c:
            c.execute("UPDATE browser_queue SET state='sent',receipt=?,error=NULL,updated_at=? WHERE id=?", (result["receipt"], time.time(), row["id"]))
            c.execute("UPDATE attempts SET outcome='sent' WHERE job_id=? AND at=?", ("browser:" + row["id"], now))
            audit(c, "browser.sent", {"item_id": row["id"], "receipt": result["receipt"]})
        return {"state": "sent", "target": row["url"], "receipt": result["receipt"], "queued": added}

    def status(self):
        with self.db.connect() as c:
            counts = {r["state"]: r["n"] for r in c.execute("SELECT state,COUNT(*) n FROM browser_queue GROUP BY state")}
            stopped = bool(c.execute("SELECT stopped FROM controls WHERE id=1").fetchone()[0])
            rows = c.execute("SELECT q.*,f.value feedback FROM browser_queue q LEFT JOIN browser_feedback f ON f.item_id=q.id ORDER BY q.discovered_at DESC LIMIT 100").fetchall()
            recent = []
            for row in rows:
                item = dict(row)
                item["post"] = json.loads(item.pop("body"))
                item["ranking"] = json.loads(item["ranking"]) if item.get("ranking") else {"score": item["score"], "reasons": []}
                if item.get("diagnostic"):
                    item["diagnostic"] = Path(item["diagnostic"]).name
                recent.append(item)
        return {"mode": self.settings.mode, "dry_run": bool(self.settings.browser.get("dry_run", True)),
                "provider": self.settings.browser.get("reply_provider", "template"), "stopped": stopped,
                "counts": counts, "recent": recent}

    def edit_item(self, item_id, text):
        with self.db.connect(write=True) as c:
            row = c.execute("SELECT * FROM browser_queue WHERE id=?", (item_id,)).fetchone()
            if not row or row["state"] not in {"queued", "previewed", "failed"}:
                raise BrowserBlocked("Only queued, previewed, or failed items can be edited.")
            profile, _ = get_profile(c)
            opportunity = json.loads(row["body"])
            recent = [r[0] for r in c.execute("SELECT draft_text FROM browser_queue WHERE id!=? AND draft_text IS NOT NULL AND state IN ('previewed','sent')", (item_id,))]
            checked = validate_reply(text, profile, opportunity, recent, float(self.settings.browser.get("similarity_limit", 0.82)))
            c.execute("UPDATE browser_queue SET draft_text=?,generator='manual',style='edited',state='previewed',error=NULL,updated_at=? WHERE id=?", (checked["text"], time.time(), item_id))
            audit(c, "browser.item_edited", {"item_id": item_id, "weighted_length": weighted_length(checked["text"])})
        return self.status()

    def item_action(self, item_id, action):
        with self.db.connect(write=True) as c:
            row = c.execute("SELECT state FROM browser_queue WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError("Browser item not found")
            if action == "dismiss" and row["state"] in {"queued", "previewed", "failed"}:
                state = "dismissed"
            elif action == "requeue" and row["state"] in {"dismissed", "failed"}:
                state = "queued"
            else:
                raise BrowserBlocked("That queue transition is not allowed.")
            c.execute("UPDATE browser_queue SET state=?,error=NULL,updated_at=? WHERE id=?", (state, time.time(), item_id))
            audit(c, "browser.item_" + action, {"item_id": item_id})
        return self.status()

    def feedback(self, item_id, value, note=""):
        with self.db.connect(write=True) as c:
            row = c.execute("SELECT state FROM browser_queue WHERE id=?", (item_id,)).fetchone()
            if not row or row["state"] != "sent":
                raise BrowserBlocked("Feedback can be recorded only for sent replies.")
            c.execute("INSERT INTO browser_feedback(item_id,value,note,at) VALUES(?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET value=excluded.value,note=excluded.note,at=excluded.at", (item_id, value, note[:500], time.time()))
            audit(c, "browser.feedback", {"item_id": item_id, "value": value})
        return self.status()

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


class BrowserSupervisor:
    """Owns at most one background campaign loop for the dashboard process."""
    def __init__(self, campaign):
        self.campaign = campaign
        self.thread = None
        self.halt = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.last_result = None
        self.last_error = ""
        self.last_run = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        with self.lock:
            if self.running:
                self.wake.set()
                return
            self.halt = threading.Event()
            self.wake = threading.Event()
            self.thread = threading.Thread(target=self._loop, name="browser-campaign", daemon=True)
            self.thread.start()

    def trigger(self):
        with self.lock:
            if self.running:
                self.wake.set()
                return
            self.thread = threading.Thread(target=self._execute, name="browser-campaign-once", daemon=True)
            self.thread.start()

    def stop(self):
        self.halt.set()
        self.wake.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def _loop(self):
        interval = int(self.campaign.settings.browser.get("poll_minutes", 20)) * 60
        while not self.halt.is_set():
            self.wake.clear()
            self._execute()
            if isinstance(self.last_result, dict) and self.last_result.get("state") == "uncertain":
                return
            self.wake.wait(interval)

    def _execute(self):
        try:
            self.last_result = self.campaign.run_once()
            self.last_error = ""
        except (BrowserBlocked, BrowserUncertain) as exc:
            self.last_result = {"state": "blocked" if isinstance(exc, BrowserBlocked) else "uncertain"}
            self.last_error = str(exc)
        except Exception:
            self.last_result = {"state": "error"}
            self.last_error = "Unexpected campaign error; durable queue state was retained."
        self.last_run = time.time()

    def status(self):
        return {"running": self.running, "last_result": self.last_result, "last_error": self.last_error, "last_run": self.last_run}
