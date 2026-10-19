from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import time


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def audit(c, action, details, now=None):
    c.execute("INSERT INTO audit(at,action,details) VALUES(?,?,?)", (now or time.time(), action, encode(details)))


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER, body TEXT);
            CREATE TABLE IF NOT EXISTS opportunities(id TEXT PRIMARY KEY, body TEXT NOT NULL, seen_at REAL);
            CREATE TABLE IF NOT EXISTS drafts(
                id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL, approval_hash TEXT, profile_version INTEGER,
                content_hash TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL REFERENCES drafts(id),
                state TEXT NOT NULL, due_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                lease_until REAL, last_attempt REAL, error TEXT, receipt TEXT);
            CREATE TABLE IF NOT EXISTS attempts(
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, at REAL NOT NULL, outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(
                content_hash TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL,
                post_id TEXT UNIQUE NOT NULL, mode TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS mock_remote(
                request_key TEXT PRIMARY KEY, post_id TEXT UNIQUE NOT NULL, text TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS metrics(
                id INTEGER PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES drafts(id),
                captured_at REAL NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS controls(id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL DEFAULT 0);
            INSERT OR IGNORE INTO controls VALUES(1,0);
            CREATE TABLE IF NOT EXISTS spending(
                id TEXT PRIMARY KEY, at REAL NOT NULL, reserved_usd REAL NOT NULL, actual_usd REAL,
                status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS browser_queue(
                id TEXT PRIMARY KEY, author TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                thread_id TEXT NOT NULL, body TEXT NOT NULL, score INTEGER NOT NULL,
                state TEXT NOT NULL, draft_text TEXT, discovered_at REAL NOT NULL,
                attempted_at REAL, receipt TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS browser_feedback(
                item_id TEXT PRIMARY KEY REFERENCES browser_queue(id),
                value INTEGER NOT NULL CHECK(value IN (-1,1)), note TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS campaign_config(
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, body TEXT NOT NULL,
                onboarding_complete INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,due_at);
            CREATE INDEX IF NOT EXISTS metrics_draft ON metrics(draft_id,captured_at);
            CREATE INDEX IF NOT EXISTS browser_queue_state ON browser_queue(state,score,discovered_at);
            INSERT OR IGNORE INTO schema_version VALUES(2);
            """)
            columns = {row[1] for row in c.execute("PRAGMA table_info(browser_queue)")}
            for name, definition in {
                "ranking": "TEXT", "generator": "TEXT", "style": "TEXT",
                "diagnostic": "TEXT", "updated_at": "REAL"
            }.items():
                if name not in columns:
                    c.execute(f"ALTER TABLE browser_queue ADD COLUMN {name} {definition}")
            c.execute("INSERT OR IGNORE INTO schema_version VALUES(3)")
            c.execute("INSERT OR IGNORE INTO schema_version VALUES(4)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self, write=False):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            if write:
                c.commit()
        except BaseException:
            if write:
                c.rollback()
            raise
        finally:
            c.close()

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Backup target already exists")
        with self.connect() as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        os.chmod(target, 0o600)
