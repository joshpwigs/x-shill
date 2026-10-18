# Signal Garden

A local-first X promotion assistant for **one clearly identified project account**. Research relevant conversations, draft useful/funny content, review exact content and sources, plan originals, and learn from meaningful engagement. Runs with Python and SQLite; no Node build or external API needed.

**Working modes:** fictional offline demo and real manual export. **Live X research/publishing is unavailable.** Official X documentation was blocked by the environment's egress proxy on 2026-10-18, so no tiers, prices, endpoints, or account entitlements were assumed. See the [verification record](docs/x-api-verification.md). No services were purchased and no public posts were sent.

## Start

Requires Python **3.12+** with `venv`, a local writable disk, and a browser.

```sh
cd /workspace/x-shill  # or your local checkout
sh scripts/install.sh
sh scripts/run.sh
```

On your own machine, open the dashboard at loopback port **8787**. The cloud onboarding UI does not expose a web preview. `config.toml` is created only if absent; it is ignored by Git. The default database is `data/x-shill.sqlite3`. Stop the process with Ctrl+C. Restart with the same command; jobs and approvals persist.

On **Steam Deck desktop mode**, keep the checkout under your home directory, use the same commands, and check `python3 --version` first. If Python/venv is unavailable, use an existing Distrobox installation with an Ubuntu 24.04 container and install `python3 python3-venv` inside it. Run the commands inside that container; publish/forward the loopback port if your container setup requires it. Do not disable SteamOS's read-only system to install this app. Linux/Python 3.12 was tested here; physical Steam Deck hardware was not available.

## Try the complete demo

1. Open **Opportunities**: fictional conversations are ranked; sensitive/unrelated ones are excluded. Read the original-context warning before suggesting a reply.
2. In **Content studio**, choose a format, tone and topic, then generate three offline options. The editable project is fictional Moon Garden; all sample facts/links are fictional.
3. Open a draft. Edit it, save, examine blockers/sources, and check both review attestations. Approve the exact version.
4. Export it, or schedule an approved original. Replies, quotes, threads and image briefs are manual-export only. The scheduler respects configured quiet hours and spacing. Demo delivery is a **simulation**, never an X request.
5. In **System & audit log**, load fictional analytics examples. **Analytics** shows missing metrics, observation windows, group sizes, and a downloadable weekly report. Emergency stop persists across restarts.

## First real post — shortest path

1. Stop the server. Set `mode = "manual"` and use a **new database path**, e.g. `database = "data/real-project.sqlite3"`, in `config.toml`. This keeps fictional work separate. Keep the template provider for a no-cost workflow. Restart.
2. In **Project knowledge**, enter the actual account/name, official links, disclosure, audience and voice. Add facts with supporting HTTPS sources and verification dates. Leave the contract address blank unless verified. Mark rumors as unverified; clear the fictional checkbox only after replacing the example identity/details.
3. Generate or write one original. Review/edit it, attach source IDs for factual statements, and approve the exact text. Schedule a time in your browser timezone. Quiet hours use the configured project timezone.
4. When it becomes **ready**, export it, review once more and publish **yourself** from the authorized project account in X. Use **Record manual post** to save its URL. The app does not purchase API access or send it for you.
5. Record available metrics later; leave unavailable fields blank. Tagged-link creation is limited to your official-link hosts. Do not infer purchases/price effects from clicks.

Live automatic X scheduling requires a future adapter after [official documentation and account authorization are verified](docs/x-api-verification.md#before-a-future-live-adapter-is-implemented). Tokens alone cannot unlock it.

## Configuration and credentials

See [`config.example.toml`](config.example.toml). Limits are bounded and explicit: three attempts per rolling 24 hours, three-hour spacing, 22:00–08:00 America/Chicago quiet hours by default. Equal quiet-start/end disables quiet hours. Retry attempts also count conservatively against limits. No learning process changes these settings.

| Workflow | Credential / verified external API cost |
| --- | --- |
| Demo, templates, manual imports/exports | None; **$0 API charges** |
| Dashboard beyond loopback | `DASHBOARD_PASSWORD` (16+ characters), HTTPS reverse proxy; no credential in repository/browser bundle |
| Optional model | `MODEL_API_KEY`; manual mode, HTTPS provider URL/model, positive budget and current user-verified per-token prices. Actual prices/access unverified; provider was not called |
| Future live X | Current project-account authorization/scopes and paid access if required by the current official docs. Exact credential types, entitlement and costs remain unverified; no X token is requested or used now |

Use a private process environment or a mode-0600 service environment file. `.env` files are **not automatically loaded**. Never paste credentials into project knowledge, source URLs, configuration values, or chat. Changing a key requires restarting the process. HTTPS/provider verification is never disabled. Model calls reserve estimated cost before requests; ambiguous charges retain their reservation. See the [model spending limits](docs/x-api-verification.md#model-providers).

## Tests

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m shill check
```

Tests cover approval enforcement and invalidation, source freshness, deduplication, concurrency, restart recovery, ambiguous outcomes, reconciliation, retry/backoff, quiet hours, rate limits, manual delivery, spending reservations, auth, CSRF/host checks, untrusted content, unavailable metrics, and backup consistency. No paid or X calls run in tests.

Optional browser test (mutates **demo data only**, run against a disposable demo instance): install Playwright 1.62.1 in an external tools directory, expose it through `NODE_PATH`, and set `BROWSER_PATH` to Chromium. With the demo server running:

```sh
node tests/browser-smoke.cjs
```

`SHILL_TEST_URL` defaults to loopback port 8787. Screenshots are written outside the checkout under `/tmp/signal-garden-screenshots`. The browser test checks discovery, generation, approval/export/scheduling, edit invalidation, every dashboard screen, analytics, stop/resume, XSS escaping, and mobile overflow. It requires a fresh disposable demo database.

## Backup, restore and update

Use SQLite's backup API rather than copying only the main file while WAL writes may be active:

```sh
.venv/bin/python -m shill backup --output /path/to/private-backups/garden-2026-10-18.sqlite3
```

Back up `config.toml` separately and protect credential storage separately. The app refuses to overwrite an existing backup. To restore, stop the service, retain the current database/WAL/SHM files as an archive, place the backup at a **new** path, point `database` there, run `check`, then restart. Pending jobs resume; expired in-flight sends become uncertain. Confirm uncertain results before taking any further action. Test a restore periodically and restrict backup access.

Before updating: stop the service, create a backup, preserve local changes, inspect the incoming diff/release notes, then update the checkout and run `sh scripts/install.sh` plus the tests. Do not reset a modified checkout. Configuration is never overwritten by installation. Current schema is version 1; future schema changes must provide explicit migrations. Deployment is not performed by this task.

## Small VPS

Run one app process under a dedicated user, store SQLite on local disk, and use a process supervisor. See the [systemd example](deploy/signal-garden.service) and [Caddy example](deploy/Caddyfile). Set a strong dashboard password even when proxying to loopback. Set `public_origin` to your HTTPS hostname; do not expose port 8787 publicly. Keep TLS, host validation, auth and the firewall enabled. A VPS and domain have costs unrelated to this app's $0 offline mode.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Draft cannot be approved | Read the blockers: missing disclosure, stale/unverified sources, unsupported claims, near duplicates, overlength or inappropriate context. Edit/save, then review again. Discard unused near-duplicates |
| Scheduled original has not run | Check emergency stop, quiet hours, due time/timezone, daily attempt limit, spacing, and the audit log. Manual mode marks it ready instead of publishing |
| Job is uncertain | Do not resend. Demo: reconcile against its stored receipt. Real manual workflow: check the account timeline and record a confirmed URL. No receipt means it stays unresolved |
| Model request failed | No automatic retry; its cost reservation remains for 24 hours. Check provider billing, valid model/schema/prices and credentials outside the dashboard. Default templates continue working |
| X shows unavailable | Expected. This release has no live X adapter; review the verification record |
| Remote start refused / login required | Configure a 16+ character `DASHBOARD_PASSWORD`, HTTPS public origin and reverse proxy. Username is `admin` |
| Database locked / wrong permissions | Run one process on a local disk; check directory ownership and avoid network filesystems. Don't delete SQLite WAL files to “fix” a live database |
| Python or dependency installation fails | Use Python 3.12+ and venv; preserve TLS/signature verification. Check package-manager network access to PyPI |

See [architecture and operational limits](docs/architecture.md). Source verification is a human responsibility; text heuristics are not comprehensive fact checking. This is a tested local/manual workflow, not an independently audited or live-X-validated deployment.
