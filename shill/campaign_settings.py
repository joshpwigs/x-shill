"""Persist user-editable campaign settings separately from advanced TOML options."""
import copy
import json
import time

from .db import audit, encode


TARGET_PACK = {
    "search_queries": ["Cronos crypto", '"Cronos chain"', "#CROFam", "$CRO", "Cronos DeFi", "Cronos gaming"],
    "groups": [
        {"id": "core", "label": "Cronos core", "accounts": ["cronos_chain", "cryptocom"]},
        {"id": "ecosystem", "label": "Cronos ecosystem", "accounts": ["VVS_finance", "TectonicFi", "ferroprotocol", "ebisusbay"]},
        {"id": "exchanges", "label": "Large exchanges", "accounts": ["binance", "coinbase", "krakenfx", "kucoincom"]},
        {"id": "media", "label": "Crypto media", "accounts": ["CoinDesk", "Cointelegraph", "DecryptMedia"]},
    ],
}


EDITABLE_BROWSER_KEYS = {
    "dry_run", "search_queries", "watch_accounts", "blocked_accounts", "minimum_score",
    "require_keyword_match", "author_cooldown_days", "discovery_limit", "per_source_limit",
    "discovery_scrolls", "poll_minutes", "similarity_limit", "reply_provider",
    "template_fallback", "ollama_url", "ollama_model", "ollama_timeout_seconds", "autostart",
    "preview_batch_size",
}


def effective(settings):
    return {
        "browser": {key: settings.browser.get(key) for key in EDITABLE_BROWSER_KEYS if key in settings.browser},
        "limits": {"daily_post_limit": settings.daily_post_limit, "minimum_gap_minutes": settings.minimum_gap_minutes,
                   "quiet_start": settings.quiet_start, "quiet_end": settings.quiet_end, "timezone": settings.timezone},
    }


def apply(settings, body):
    for key, value in body.get("browser", {}).items():
        if key in EDITABLE_BROWSER_KEYS:
            settings.browser[key] = value
    limits = body.get("limits", {})
    for key in ("daily_post_limit", "minimum_gap_minutes", "quiet_start", "quiet_end", "timezone"):
        if key in limits:
            setattr(settings, key, limits[key])
    settings.validate()
    return settings


def load(db, settings):
    with db.connect() as c:
        row = c.execute("SELECT body FROM campaign_config WHERE id=1").fetchone()
    if row:
        apply(settings, json.loads(row["body"]))
    return settings


def initialize(db, settings, profile):
    with db.connect(write=True) as c:
        if not c.execute("SELECT 1 FROM campaign_config WHERE id=1").fetchone():
            complete = int(not profile.get("fictional") and profile.get("name") != "Your project")
            c.execute("INSERT INTO campaign_config(id,version,body,onboarding_complete,updated_at) VALUES(1,1,?,?,?)",
                      (encode(effective(settings)), complete, time.time()))
            audit(c, "campaign.settings_initialized", {"onboarding_complete": bool(complete)})


def get(db, settings):
    with db.connect() as c:
        row = c.execute("SELECT version,body,onboarding_complete,updated_at FROM campaign_config WHERE id=1").fetchone()
    return {"version": row["version"], "onboarding_complete": bool(row["onboarding_complete"]),
            "updated_at": row["updated_at"], **effective(settings), "target_pack": TARGET_PACK}


def save(db, settings, body, version, complete=True):
    staged = copy.deepcopy(settings)
    apply(staged, body)
    stored = effective(staged)
    with db.connect(write=True) as c:
        row = c.execute("SELECT version FROM campaign_config WHERE id=1").fetchone()
        if not row or row["version"] != version:
            raise ValueError("Campaign settings changed in another session; reload and try again.")
        c.execute("UPDATE campaign_config SET version=version+1,body=?,onboarding_complete=?,updated_at=? WHERE id=1",
                  (encode(stored), int(complete), time.time()))
        invalidated = c.execute("DELETE FROM browser_queue WHERE state IN ('queued','previewed','failed','dismissed')").rowcount
        audit(c, "campaign.settings_updated", {"version": version + 1, "dry_run": staged.browser.get("dry_run", True), "queue_invalidated": invalidated})
    apply(settings, stored)
    return get(db, settings)
