# Testing

## Run the suite

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

No mocks are used. The intelligence tests run against real captured source payloads; the
application tests run against a real database and a real Flask app.

## What the suite covers

| Suite | Covers |
|---|---|
| `test_app_directory.py` | Listing, filters, search, sorting, pagination, empty states |
| `test_app_detail.py` | Provenance, missing fields, procurement transparency, grouping, private-data separation |
| `test_app_accounts.py` | Signup, signin, password hashing, enumeration resistance, open redirect, saved opportunities |
| `test_app_seo.py` | Metadata, canonical URLs, noindex rules, sitemap, robots, structured data |
| `test_app_api.py` | API contract, null encoding, statistics, no internal leakage |
| `test_app_admin.py` | `/admin/data` access: anonymous, non-operator, operator, escalation, exposure |
| `test_app_architecture.py` | Market/trade are configuration; no intelligence logic duplicated |
| `test_auth_security.py` | Google/Firebase token verification, account takeover resistance, password reset |
| `test_firebase_config.py` | Firebase web config is injected, never committed; injection precedence |
| `test_changes.py` | Change detection: real differences recorded, no-op passes silent, before/after values |
| `test_workflow.py` | Watching, pipeline, notes, tags, assignment, per-account isolation, stage/procurement separation |
| `test_alerts.py` | Event-driven alerts, watch scoping, no-change-means-no-alert, alert integrity |
| `test_security.py` | CSRF, rate limiting, headers, privilege escalation, IDOR, open redirect, operator area |
| `test_entitlements.py` | Plan/subscription/entitlement separation, no self-service upgrade, lapsed access |
| `test_deployment.py` | Health-check contract, empty-vs-unusable database, environment-configured database path |
| `test_acceptance.py` | The full lifecycle from source to report against real records |
| `test_seo_keywords.py` | Keyword map coherence, indexation contract, quality gate, internal linking, content, analytics funnel, SEO report |
| `test_search_intelligence.py`, `test_search_vocabulary.py` | Search expansion and match explanations |
| `test_trends.py`, `test_linkedin.py` | Trend metrics and evidence-backed draft generation |
| `test_classify.py`, `test_normalize.py`, `test_provenance.py`, … | The intelligence engine |

## Search visibility governance

The public site is built so a contractor searching for commercial construction leads in DFW can
find the product, understand it, and reach the directory. The SEO layer sits on top of the product;
it does not replace it. Two rules are enforced by `tests/test_seo_keywords.py`:

- **One primary destination per keyword.** `config/keywords.yaml` maps every targeted keyword to its
  intent and its single canonical page. Two pages competing for one primary keyword dilute each
  other, so a duplicate claim fails the suite.
- **A keyword is only targeted where the data can satisfy it.** Phrases this product cannot
  evidence — `construction bid opportunities` and similar — are recorded as `deferred` with a
  reason rather than pointed at a page that would have to overclaim.

**Programmatic-SEO quality gate.** A page below `min_projects` (or a trade page below
`min_mechanical`) is not offered to crawlers. It still renders, still shows real data, and states
plainly that it is not being offered to search engines yet; it is marked `noindex` and withheld
from the sitemap, so the two signals always agree. Curated pages under a market's `landing_pages`
are a human editorial decision and stay indexable.

**Indexation rules.**

| Page | Rule |
|---|---|
| Home, core category, directory, markets, trades, project types, reports, guides | index |
| Curated market and city landing pages | index |
| Market × trade | index (curated) |
| City × trade | index only when it clears the quality gate, otherwise noindex |
| Filtered or paginated directory views | noindex, canonical to the clean directory |
| Individual opportunity pages | index; closed or unverified records are not discoverable |
| Account area, dashboard, watching, pipeline, alerts, saved, preferences, admin | noindex and disallowed in `robots.txt` |
| Error pages | noindex |

**Analytics funnel.** Landing views are recorded as a page *kind* in the existing `analytics_event`
table. No URL, query string, IP address, user agent or account link is stored, so the table cannot
become a record of who searched for what. The funnel stages — directory landing → opportunity
viewed → account created → saved → watched → pipeline action — are read from existing events.

**Audit:**

```bash
PYTHONPATH=src flask --app oppintel.app.wsgi seo-report          # human-readable
PYTHONPATH=src flask --app oppintel.app.wsgi seo-report --json   # machine-readable
```

The report lists keyword coverage, the gate decision and observed counts for every programmatic
page, content gaps, and the funnel. It reports **no ranking position**, because ranking has not
been measured and asserting it would be the same class of error as inventing a project value.
