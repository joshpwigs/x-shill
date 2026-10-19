from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from shill.browserbot import BrowserSession
from shill.config import Settings


HTML = b"""<!doctype html><html><body>
<article data-testid="tweet">
  <a href="/Alice/status/123"><time datetime="2026-10-19T12:00:00Z">now</time></a>
  <div data-testid="tweetText">What should Cronos wallets explain to newcomers?</div>
  <button data-testid="reply" aria-label="4 replies" onclick="openReply()">Reply</button>
</article>
<script>
function openReply() {
  const box=document.createElement('div'); box.dataset.testid='tweetTextarea_0'; box.contentEditable='true'; document.body.appendChild(box);
  const button=document.createElement('button'); button.dataset.testid='tweetButton'; button.textContent='Post';
  button.onclick=()=>{ const toast=document.createElement('div'); toast.dataset.testid='toast'; toast.textContent='Your reply was sent'; document.body.appendChild(toast); };
  document.body.appendChild(button);
}
</script></body></html>"""

UNCERTAIN_HTML = HTML.replace(b"/Alice/status/123", b"/Alice/status/124").replace(
    b"button.onclick=()=>{ const toast=document.createElement('div'); toast.dataset.testid='toast'; toast.textContent='Your reply was sent'; document.body.appendChild(toast); };",
    b"button.onclick=()=>{};")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = UNCERTAIN_HTML if "/status/124" in self.path else HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_x():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def test_real_browser_discovery_and_reply_confirmation(tmp_path, fake_x):
    executable = next((p for p in ("/usr/bin/chromium", "/usr/bin/google-chrome") if Path(p).exists()), None)
    if not executable:
        pytest.skip("No system Chromium")
    settings = Settings(mode="browser", database=str(tmp_path / "db.sqlite3"), browser={
        "base_url": fake_x, "allow_test_site": True, "profile_dir": str(tmp_path / "profile"),
        "executable_path": executable, "browser_args": ["--no-sandbox"], "headless": True,
        "search_queries": ["Cronos"], "watch_accounts": [], "discovery_limit": 5,
    }).validate()
    browser = BrowserSession(settings)
    posts = browser.discover()
    assert posts[0]["author"] == "Alice"
    assert posts[0]["replies"] == 4
    result = browser.reply(fake_x + "/Alice/status/123", "A useful Cronos reply", dry_run=False)
    assert result["outcome"] == "sent"
    assert result["receipt"].startswith("ui:123:")


def test_ambiguous_browser_click_saves_local_screenshot(tmp_path, fake_x):
    executable = next((p for p in ("/usr/bin/chromium", "/usr/bin/google-chrome") if Path(p).exists()), None)
    if not executable:
        pytest.skip("No system Chromium")
    settings = Settings(mode="browser", database=str(tmp_path / "db.sqlite3"), browser={
        "base_url": fake_x, "allow_test_site": True, "profile_dir": str(tmp_path / "profile"),
        "diagnostics_dir": str(tmp_path / "diagnostics"), "confirmation_timeout_ms": 300,
        "executable_path": executable, "browser_args": ["--no-sandbox"], "headless": True,
        "search_queries": ["Cronos"], "watch_accounts": [],
    }).validate()
    from shill.browserbot import BrowserUncertain
    with pytest.raises(BrowserUncertain, match="diagnostic"):
        BrowserSession(settings).reply(fake_x + "/Alice/status/124", "A useful Cronos reply", dry_run=False)
    assert len(list((tmp_path / "diagnostics").glob("*.png"))) == 1
