# Validation record — 2026-10-18

Validated in this cloud workspace on Linux with Python 3.12.14 and Chromium. These results apply to the code and current instance, not a published environment or a real X integration.

| Check | Outcome |
| --- | --- |
| `python -m pytest -q` | **80 passed**; no skipped tests. One dependency deprecation warning: Starlette currently supports the HTTPX TestClient used here but recommends HTTPX2 for future versions |
| Browser smoke test | Passed discovery, generation, exact-content approval, manual export, scheduling, edit invalidation, every dashboard view, analytics fixtures, emergency stop/resume, XSS escaping and mobile overflow checks |
| Mobile system navigation | Passed at a 390×844 viewport |
| JavaScript syntax | `node --check shill/static/app.js` passed |
| Dependency consistency | `pip check` passed |
| Installation | `sh scripts/install.sh` succeeded and succeeded again while preserving the existing config/database |
| Startup/restart | `sh scripts/run.sh` served the dashboard; health reported a running worker and healthy database after restart; all nine current demo drafts/history records were retained |
| CLI backup | Consistent backup created; restored database opened with `PRAGMA integrity_check = ok` and retained its drafts. Unit tests also verified pending jobs survive backup |
| Actual network side effects | No X API requests, paid model requests, public posts or purchases |
| Environment configuration | `install_script` and `start_skill` saved successfully. Custom domain additions: `docs.x.com`, `help.x.com`, `api.x.com`. Save does not apply runtime networking or publish |

Browser runs used a separate disposable demo database. Screenshots were inspected for desktop analytics/discovery and mobile layout. The initial default demo database also contains fictional validation drafts and explicit analytics fixtures; it contains no real project details or credentials.

The suite tests service and HTTP boundaries for real manual-mode approval, scheduling to export-ready, account-matching receipt recording and nullable metrics. Model success/failure/budget tests use mocked HTTP clients. Crash-after-acceptance tests reconcile a durable simulated receipt without resending. Ambiguous sends and expired leases remain uncertain instead of retrying.

**Not verified:** current official X documentation/rules/prices/entitlements (egress proxy denied access), X authentication or endpoints, real model-provider compatibility/prices, physical Steam Deck operation, VPS deployment/TLS, publication, or snapshot restoration in a new cloud task. See [X verification record](x-api-verification.md) and [operations instructions](../README.md).
