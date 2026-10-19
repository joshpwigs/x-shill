from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import os
import tomllib


@dataclass
class Settings:
    mode: str = "demo"
    database: str = "data/x-shill.sqlite3"
    host: str = "127.0.0.1"
    port: int = 8787
    public_origin: str = ""
    timezone: str = "America/Chicago"
    daily_post_limit: int = 3
    minimum_gap_minutes: int = 180
    quiet_start: int = 22
    quiet_end: int = 8
    max_attempts: int = 4
    lease_seconds: int = 120
    worker_interval_seconds: int = 5
    max_schedule_days: int = 90
    model: dict = field(default_factory=lambda: {"provider": "template", "daily_budget_usd": 0})
    browser: dict = field(default_factory=dict)

    def validate(self):
        if self.mode not in {"demo", "manual", "browser"}:
            raise ValueError("Mode must be demo, manual, or browser")
        ZoneInfo(self.timezone)
        if not (1 <= self.daily_post_limit <= 20 and 1 <= self.minimum_gap_minutes <= 1440):
            raise ValueError("Use 1–20 posts/day and a 1–1440 minute minimum gap")
        if not (0 <= self.quiet_start <= 23 and 0 <= self.quiet_end <= 23):
            raise ValueError("Quiet hours must be 0–23; equal values disable them")
        if not (1 <= self.max_attempts <= 8 and 30 <= self.lease_seconds <= 600):
            raise ValueError("Invalid retry or lease configuration")
        if not (1 <= self.worker_interval_seconds <= 60 and 1 <= self.max_schedule_days <= 365):
            raise ValueError("Invalid scheduler configuration")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            if len(os.environ.get("DASHBOARD_PASSWORD", "")) < 16:
                raise ValueError("Remote binding requires DASHBOARD_PASSWORD (16+ characters)")
            if urlsplit(self.public_origin).scheme != "https":
                raise ValueError("Remote binding requires an HTTPS public_origin and TLS reverse proxy")
        if self.public_origin:
            u = urlsplit(self.public_origin)
            if u.scheme != "https" or not u.hostname or u.username or u.query or u.path not in {"", "/"}:
                raise ValueError("public_origin must be an HTTPS origin without credentials or path")
        if self.model.get("provider", "template") not in {"template", "openai-compatible"}:
            raise ValueError("Unknown model provider")
        if not isinstance(self.browser, dict):
            raise ValueError("browser must be a TOML table")
        queries = self.browser.get("search_queries", [])
        if not isinstance(queries, list) or len(queries) > 30 or not all(isinstance(v, str) and 0 < len(v) <= 120 for v in queries):
            raise ValueError("browser.search_queries must contain at most 30 short strings")
        accounts = self.browser.get("watch_accounts", [])
        if not isinstance(accounts, list) or len(accounts) > 50 or not all(isinstance(v, str) and v.lstrip("@").replace("_", "").isalnum() and len(v.lstrip("@")) <= 15 for v in accounts):
            raise ValueError("browser.watch_accounts contains an invalid X account")
        poll = int(self.browser.get("poll_minutes", 20))
        minimum_score = int(self.browser.get("minimum_score", 35))
        if not (5 <= poll <= 1440 and 0 <= minimum_score <= 100):
            raise ValueError("Invalid browser poll interval or minimum score")
        base_url = self.browser.get("base_url", "https://x.com")
        if base_url != "https://x.com" and not self.browser.get("allow_test_site", False):
            raise ValueError("browser.base_url must be https://x.com")
        return self


def load_settings(path=None):
    path = Path(path or os.environ.get("SHILL_CONFIG", "config.toml"))
    values = tomllib.loads(path.read_text()) if path.exists() else {}
    return Settings(**values).validate()
