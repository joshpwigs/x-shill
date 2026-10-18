# X integration verification record

**Attempted on 2026-10-18. Status: blocked, not verified.**

The development environment's HTTPS egress proxy returned `403 Forbidden` during CONNECT for both `docs.x.com` and `help.x.com`. No official documentation content was retrieved. No X credentials were present. This is a network-policy result, not evidence of invalid X credentials or an X account ban.

These are the official destinations requested for verification; their current content and page availability remain unverified:

| Official source / attempted URL | Required verification |
| --- | --- |
| [X API overview](https://docs.x.com/x-api/introduction) | Current product, access model and available API capabilities |
| [Pricing](https://docs.x.com/x-api/getting-started/pricing) | Current access tiers, prices, purchase requirements, per-endpoint costs and spending controls |
| [Create a post](https://docs.x.com/x-api/posts/create-post) | Current write endpoint, user authorization, scopes, supported fields, errors, response receipts, rate limits and whether any documented idempotency facility exists |
| [Recent search](https://docs.x.com/x-api/posts/recent-search) | Search entitlement, query syntax, pagination, time window, rate limits, public-account monitoring and read charges |
| [Automation rules](https://help.x.com/en/rules-and-policies/x-automation) | Current rules for automated posts, consent, replies, mentions, spam and affiliated account disclosure |
| [Documentation index](https://docs.x.com/llms.txt) | Find current official URLs rather than assuming these paths are still canonical |

No Free/Basic/Pro/Enterprise tier, dollar price, endpoint entitlement, or rate-limit number is assumed. **Verified live X costs: unavailable.** Local template/demo/manual-export operations make no X or model requests and incur **$0 external API charges**. Hardware/VPS costs are separate. Do not fund an X account based on this document.

## Deliberate live gate

There is no live X research or publishing implementation. `UnavailableXPublisher` cannot send requests; config accepts only `demo` or `manual`. Adding a token does not unlock it. No authentication flow, token storage, automatic monitoring, X metrics collector, or post-write HTTP endpoint has been guessed or fabricated. The dashboard's X health status explicitly says unavailable.

Research currently ranks **manually imported permitted public data** using the configured keywords/account list. It does not scrape, open a browser to collect X data, or poll X. Replies and quote suggestions require individual review and manual export. Originals can be scheduled for a fictional receipt in demo mode or an export-ready state in manual mode.

## Before a future live adapter is implemented

1. Allow `docs.x.com` and `help.x.com` in environment settings, save those settings, and re-fetch official documentation. This task saved those domains plus `api.x.com` to the configuration draft; saving the draft alone does not change runtime access. Publication is a separate user action.
2. Record dated excerpts of current rules, actual official endpoint URLs, required scopes, token refresh/revocation behavior, rate limits, access tiers and exact account-specific prices. Follow the current docs if URLs or API hosts have changed.
3. Verify read-only account identity and endpoint entitlement using the authorized project account. A dashboard login, app bearer credential, or successful search does not prove write authorization. Store secrets server-side only, using the current supported authorization flow.
4. Implement a separate adapter against the existing publisher interface and add protocol-level tests. Explicit 429/known nonacceptance may be retried with Retry-After and bounded backoff. Timeouts, disconnects, unknown server errors, malformed successful responses, and process crashes are **uncertain**, never auto-retried. Do not assume the demo adapter's request-key idempotency exists on X.
5. Add a real user authorization and explicit publishing configuration gate. Bind that authorization to the configured account ID. Keep per-content approval, stop controls, source checks, deduplication, limits, quiet hours, and conservative reconciliation.
6. With the user's separate authorization, validate a read-only connection first. This implementation task authorizes **no service purchases and no public posts**.

## Model providers

The default template engine is fully offline. An optional OpenAI-compatible JSON chat adapter exists and was tested with mocks only. It requires explicit `mode = "manual"`, a configured model/HTTPS endpoint, `MODEL_API_KEY`, a positive rolling-24-hour budget, and **user-verified** input/output prices. No provider/model was purchased or called. Real availability, credentials, schema compatibility, rate limits and pricing remain unverified.

The adapter reserves a conservative byte-based input token bound plus maximum output cost before calling; it makes no automatic retries, retains reservations for failed/uncertain calls, and uses returned usage when available. The spending guard depends on accurate configured prices and a provider honoring the configured token cap. Provider billing remains authoritative. It is not a substitute for the provider's own hard spending limit.
