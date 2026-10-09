# BuildScope Platform Audit

Scope: the repository at `main` (commit `29c00d9`) and the deployed service at
`https://buildscope-xppz.onrender.com`. Written before any change, from reading the code and
probing the live service. Baseline: **716 tests passing** (`PYTHONPATH="vendor/python:src"
python -m pytest tests/ -q`).

Production state at audit time: `{"permits":6165,"projects":1779,"status":"ok"}`.

## 1. What already works

| Area | Implementation | Notes |
|---|---|---|
| Framework | Flask 3 + gunicorn, `ops/start.sh`, `render.yaml` | Single process runs server + refresh loop |
| Config-driven geography | `config/markets.yaml`, `config/markets.py` loader | DFW active; Houston/Austin present but inactive |
| Config-driven trade | `config/trades.yaml`, `TradeConfig` | Discovery scoped by `evidence_field`/`evidence_values`, not a column literal |
| Schema | `src/oppintel/db.py`, 30+ tables | `raw_record` retains source payloads verbatim |
| Provenance | `provenance.py`, `evidence` table | Every material field has evidence rows; NULL means missing, never a placeholder |
| Normalization | `normalize.py` | Negation-aware keyword matching; residential exclusion |
| Assembly | `assemble.py`, `grouping.py` | Address-key clustering, sibling detection |
| Classification | `classify.py` | Mechanical-evidence gate; scale floor; reasons recorded per point |
| Search | FTS5 `project_search`, `quote_for_fts` | Prefix matching, bound parameters, malformed query degrades to empty |
| Change monitoring | `changes.py`, `events.py` | Snapshot diff; no change ⇒ no event ⇒ no alert |
| Alerts | `alerts.py` | Every alert links to a `project_change` or a first-match |
| Companies | `companies.py` | `project_party` roles kept distinct |
| Reports | `report_generator.py`, `reporting.py` | Generated from live data, not cached |
| SEO | `seo.py`, `seo_gate.py`, sitemap, robots | Canonical gating on filtered views |
| Auth | `accounts.py`, `security.py`, CSRF, reset tokens | Phase 1A remediation already applied |
| Analytics | `analytics_event` + `analytics_funnel.py` | Landing→signup→save funnel |
| Health | `/healthz` | Empty DB is 200, unreachable DB is 503 |

## 2. What partially works

1. **Search relevance.** Prefix matching only. No synonym/abbreviation/punctuation handling, no
   relevance test set, no zero-result diagnostics. Equipment terms ("AHU", "RTU") do not expand.
2. **Date validation.** `models.parse_date` accepts any parseable date, including far-future.
   `quality.py` flags a permit only beyond `+365 days` and treats all date fields identically —
   it never distinguishes an issue date from a planned or expiration date.
3. **Analytics depth.** `analytics_event` stores only `event_name`, `project_id`, `market_id`,
   `trade_id`. The query text, result count, filters, session and campaign parameters are not
   stored, so "what do users search for" and "which searches return nothing" cannot be answered.
4. **Company intelligence.** Roles are read from `project_party`; a derived specialisation is
   labelled but there is no explicit evidence-strength separation for company claims.
5. **Report scoping.** `report_generator.py` defaults `market="Dallas–Fort Worth, TX"` and titles
   the brief "DFW Commercial HVAC Opportunity Brief" — a hardcoded geography in the generator.

## 3. What is broken

1. **Future-dated permits are counted as recent filings.** `classify.py` awards
   "Permit filed within the last N days" whenever `permit_date >= today - window`. A permit dated
   in the future satisfies this, so the product asserts a historical filing that has not
   happened. This is the concrete Phase 3 defect.
2. **No semantic date model.** A future date is never classified as plausible (planned/expiration)
   versus implausible (issue/observation), so the quality report cannot say *why* a date is odd.
3. **Coverage conflates configuration with data.** Nothing distinguishes a market that is merely
   configured from one with an active connector, from one with ingested records, from one with
   verified recent coverage. The mission forbids equating the two.

## 4. What is missing

- **Multi-market coverage intelligence** (Phase 2): the six coverage states are not computed.
- **Search synonym/abbreviation vocabulary** (Phase 5): no config-driven expansion.
- **First-party query analytics** (Phase 6): query, result count, filters, session, campaign.
- **Trend intelligence / keyword radar** (Phase 10): no module, no storage, no scoring.
- **LinkedIn content engine** (Phase 11): `content.py` holds static guides only.
- **Campaign/UTM attribution** (Phase 12): no link generator, no attribution storage.

## 5. Changes that are necessary

Implement, in the existing architecture, without removing working behaviour:

1. A semantic date-validation module; fix the future-dated-recent-filing defect; regression tests.
2. A coverage-intelligence module distinguishing the six states, surfaced honestly.
3. Analytics enrichment (query, result count, filters, session, campaign) with privacy limits.
4. A config-driven search vocabulary and a reproducible relevance test set.
5. Trend intelligence with transparent scoring and explicit "preliminary" labelling.
6. A content engine producing reviewable drafts from verified records, plus a UTM link builder.
7. De-hardcode the report generator's geography.

## 6. Behaviour that must be preserved

- The mechanical-evidence gate (a project cannot be HIGH without trade evidence).
- Missing information stays NULL and renders as "Not verified".
- No change ⇒ no event ⇒ no alert.
- Closed projects remain reachable by direct URL but excluded from discovery.
- Public pages never claim a bid is open without authoritative evidence.
- Config-driven markets/trades; no market-specific business logic duplicated in code.
- All 716 existing tests continue to pass.
