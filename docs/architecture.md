# Architecture and boundaries

Single-process Python 3.12+ / FastAPI service; standard HTML, CSS and JavaScript; SQLite with WAL, foreign keys and FULL synchronous commits. No Redis, Docker, Node build, GPU, or external service is required. SQLite should live on a local disk, not an NFS/network mount.

| Module | Responsibility |
| --- | --- |
| `schemas.py`, `service.py`, `policy.py` | Project facts, immutable research context, drafts, exact-content approval, review checks and manual exports |
| `generation.py` | Three offline alternatives per format/tone; optional guarded model provider |
| `publishing.py` | Durable fictional remote and deliberately unavailable X publisher |
| `scheduler.py` | Durable due jobs, serialized claims, quiet hours, rate/spacing limits, rejection backoff, uncertain-outcome quarantine |
| `analytics.py` | Metric availability, 24–48-hour snapshot comparisons, descriptive weekly reports |
| `app.py`, `static/` | Authenticated API when configured, same-origin dashboard, escaped untrusted data |
| `db.py` | Schema, transaction boundaries, backups and audit events |

## Approval and state

Approval fingerprints include the entire draft body and complete project profile/version, including sources, dates, disclosure, image brief, category and tone. The API requires the displayed revision and fingerprint plus explicit claim/context review. Editing a draft revokes its approval and removes its pending schedule. Changing project knowledge revokes pending approvals/schedules. Imported conversation IDs cannot change in place. There is no approve-all endpoint.

```
draft -> approved -> scheduled -> inflight -> simulated   (demo only)
                      |             |  |
                      |             |  +-> uncertain -> reconcile confirmed receipt
                      |             +-> explicit rejection -> bounded retry / failed
                      +-> ready -> manually recorded posted             (manual)
approved -> individually reviewed export                       (either mode)
```

Only original `post`, `update`, `opinion`, and `question` formats can be scheduled. Replies, quotes, educational threads and image briefs are exported individually. No DMs, engagement exchanges, purchased engagement, account rotation, or automated unsolicited replies exist.

Draft-only is the starting state: there are no default jobs and no implicit approvals. Scheduling is itself an explicit individual action. Demo scheduling creates fictional delivery receipts only; manual scheduling creates export-ready items only. Loading analytics fixtures is an explicit demo-only action and is labeled fictional.

## Durable delivery and recovery

Claiming a pending job uses `BEGIN IMMEDIATE`; at most one job is in flight at a time. The job/attempt is committed **before** calling the adapter. Successful receipt persistence is a separate transaction. A crash in between cannot be presumed to mean failure. Expired in-flight leases become uncertain, never pending. A restarted worker waits for an unexpired lease, then quarantines it.

Unique content hashes and receipt IDs prevent duplicate recorded deliveries. Exact normalized duplicates and near-identical drafts are blocked; wording similarity strips the affiliation boilerplate. This is an additional content safeguard, not a network exactly-once guarantee. Uncertain content remains immutable and is not automatically resent.

Only explicit known rejections are retryable, with exponential backoff, jitter, max attempts and Retry-After. Account limits count attempts conservatively, including failed and ambiguous ones, over a rolling 24-hour window. Quiet hours use an IANA timezone and are checked on every attempt. A delay can move a job to another quiet period; the next cycle re-evaluates it. Emergency stop persists in the database and prevents new claims. It cannot undo a request already in flight.

The demo's simulated remote stores a durable receipt keyed by job ID. Reconciliation checks that store without sending again. A real manual receipt requires an X URL belonging to the configured account and is explicitly **user-confirmed, not API-verified**. There is intentionally no “retry uncertain” button.

Run one server process. Transactions protect against duplicate claims by overlapping local workers, but multi-host operation, distributed leases, and a public multi-tenant service are not supported. A VPS process supervisor may restart the one process. Do not run two copies with independent databases for the same account.

## Truth and context

Source verification is a human attestation; the app does not authenticate a source URL. Only current, dated, sourced verified facts enter generated factual context. Opinions/unverified/stale facts remain editable and visible but block use as factual sources. Certain high-risk phrases require an attached current fact quoted verbatim; price promises and project-forbidden claims are blocked. A contract address needs a matching verification source.

These conservative checks are **not a semantic fact checker**. They cannot detect every false claim, inappropriate joke, sensitive situation, synthetic engagement count, or prompt-injection attempt. Human claim/context review is required. Imported posts are displayed as escaped text and passed to a model as data; no retrieval instruction can call tools, modify permissions, approve, schedule, or publish. The model has no tool access. Sensitive/unrelated conversations are excluded using transparent heuristics, not certainty about intent.

X length is a conservative weighted estimate, with HTTPS links treated as 23 characters and many Unicode characters weighted as two. Complex emoji may be overcounted. Threads are manual exports and require per-segment review in X; there is no unverified premium long-post or image-upload assumption.

## Analytics

Metric fields are nullable. Unavailable values are never replaced with zero. Meaningful replies are manually classified; a count of all replies is not substituted. Qualified clicks require an explicit qualification rule. Comparisons use the latest snapshot only when captured 24–48 hours after posting, reducing exposure-age differences. Group sizes and paired metric coverage are shown; sparse data never produces a “winner.” Reports are descriptive, not causal or statistically significant claims. Frequency and permissions never change automatically.

## Security and operational scope

Loopback is the default. Remote binding requires a 16+ character `DASHBOARD_PASSWORD` and an HTTPS public origin. HTTP Basic authentication applies to HTML, static assets and APIs. Terminate TLS with a reverse proxy; never expose the app's plain HTTP port publicly. Origin/Fetch-Metadata checks, required JSON, body limits, trusted hosts, a restrictive CSP, and escaped rendering protect the local dashboard from common cross-site attacks. The browser receives no provider key. Auth failure limiting is process-local; use a firewall/reverse-proxy rate limit on a VPS.

Configuration and SQLite contain project content, not provider credentials. Credentials come only from process environment. Exception responses and audit events omit provider responses and credentials. The audit log is durable, but not tamper-evident against a user who can edit the SQLite file. Protect/encrypt backups and restrict filesystem permissions. Deleting a workspace or backup requires explicit operator action; no retention purge runs silently.

This is a tested local/manual application, not a verified live X deployment or an independently security-audited service. Future schema changes require migrations and fresh backup/recovery tests; current schema version is 1.
