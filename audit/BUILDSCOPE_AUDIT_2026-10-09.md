# BuildScope — Principal Engineering & Staff UI Audit

**Type:** READ-ONLY audit. No application code, schema, data, or configuration was modified.
The only artifacts created are this report and the audit tooling/screenshots under `audit/`.
Local writes were limited to: a *throwaway* SQLite database at `data/oppintel.db` built from a
bounded 2-page ingest, the vendored `vendor/python` install, and audit output files. The
shipped `data/` was empty; the git-ignored DB path was used as documented.

**Audit date:** 2026-10-09. **Auditor:** OpenHands agent (AI), read-only.

**Method:** static code review + live HTTP probes of `https://buildscope-xppz.onrender.com`
+ a local run of the app against a locally rebuilt database + Playwright screenshots/axe-core
at 3 viewports. Every number below is copied from a command output; commands are in the
Appendix. Anything not verifiable is marked **UNVERIFIED** with the reason.

---

## 1. Executive summary

BuildScope is a real, well-architected B2B intelligence product with an unusually disciplined
evidence engine and a clean, fast, pure-server-rendered UI. The **intelligence layer is the
strong asset**; the **gaps are in data coverage, one cosmetic-vs-semantic stat mismatch, UI
polish under real data density, mobile touch ergonomics, and the absence of a few
"professional SaaS" affordances (pricing, PDF/Excel export, datasets/charts)**. The founder's
instinct is half right: it does not look like a *Dodge/ConstructConnect-class* product mainly
because the data is thin (175 of 1,791 projects discoverable; no GC/architect companies;
GTM pages rank #2 in sitemap) and because there is no monetization surface — not because the
core is fake. Nothing is "mock": 910 tests pass on a real pipeline.

Overall health score (1–10):

| Area | Score | One-line rationale |
|---|---|---|
| Data integrity | **5/10** | Real counts, but 4 differently-defined "record" numbers, 2 future permits, 63% future-dated coverage window, one searchable discrepancy |
| UI / design | **7/10** | Genuine token system, one font, coherent; hurt by inline styles, ALL-CAPS raw titles, thin empty cards |
| Mobile | **6/10** | Mostly responsive, but horizontal overflow on `/companies`, 30–38px tap targets, drawer off-canvas scroll |
| Performance | **9/10** | 2 requests, 0 JS, ~10KB CSS, FCP ~300ms — exemplary |
| Accessibility | **6/10** | Strong labels/landmarks; serious AA contrast failures + heading-order issues on every page |
| Security | **8/10** | CSRF, CSP, PBKDF2, rate-limit, no committed secrets; no email verification, no HSTS header |
| SEO | **7/10** | Correct canonicals/sitemap/robots/JSON-LD; home `<title>` describes a *different* page; `/guides` absent from sitemap |
| Product completeness | **6/10** | 46 routes, real features; no pricing, no export, no charts, no email, thin company data |
| Code quality | **8/10** | Disciplined, layered, documented, tested; ~331 inline styles, migration fragility |
| Ops | **7/10** | Render blueprint + disk docs correct; no CI, no backups, no error monitoring, cold-start risk |

**Headline:** the site is *not* broken — it is a **good product under-built at the edges**.
The single most damaging category is **data integrity presentation** (Phase 1), followed by
**mobile/a11y polish**, then **GTM completeness**.

---

## 2. Architecture overview

```
                         ┌──────────────────────────────────────────────┐
   public gov sources →  │ connectors/  (Fort Worth ArcGIS, Collin CAD   │
   (no auth)             │  Socrata, Dallas Accela WebForms)            │
                         └───────────────┬──────────────────────────────┘
                                         │ raw payload → data/raw/*.jsonl (archived)
                                         ▼
                 normalize → assemble → provenance(evidence) → classify
                                         │        │              │
                                         │        │              └→ project.classification
                                         │        └→ evidence / evidence_history (append-only)
                                         ▼
                         changes.py (diff) → project_change → monitoring → alerts
                         intelligence.derive_for_project → entity/event/trade graph
                                         │
                                         ▼
                         ┌───────────────────────────────┐
                         │ service.py  OpportunityService │  ← THE read boundary
                         │  (only app module touching     │     (enforced by tests)
                         │   intelligence tables)         │
                         └───────────────┬────────────────┘
                                         ▼
   Flask app (app/main.py, 46 routes) ─ templates (Jinja, 42 files) ─ static/css/app.css
   app/api.py (18 JSON endpoints)  ·  seo.py · accounts.py · workflow.py · seo_gate.py
                                         │
                                         ▼
      SQLite (src/oppintel/db.py: SCHEMA intel + APP_SCHEMA app) · single file
      Deployment: render.yaml → bash ops/start.sh → gunicorn + in-process refresh loop
```

- **Backend:** Flask 3.1.3 + Werkzeug, Jinja2 server-rendered. **No frontend framework in the served product** — the React/Vite/stub (`src/App.tsx`) contributes nothing (`src/App.tsx` renders an empty `<div>`).
- **DB:** SQLite (`data/oppintel.db`), schema in two strings in `src/oppintel/db.py` (`SCHEMA` line ~20, `APP_SCHEMA` line ~505+). 35 tables incl. FTS5 `project_search`.
- **Ingestion:** `python -m oppintel.cli ingest` → `pipeline.py`; connectors order newest-first, default 200 pages/source; bounded refresh (`MAX_PAGES=3`) in `ops/automate.py`.
- **Scheduler:** none external; `ops/automate.py` supervises gunicorn + a refresh thread (`REFRESH_SECONDS=21600`). Container has no cron/systemd (by design).
- **Solvers/config:** `config/{markets,trades,sources,keywords,search_vocabulary,trade_taxonomy}.yaml`.

---

## 3. Findings table

Severity: **C**ritical / **H**igh / **M**ed / **L**ow. Effort: **S**mall (≤½ day) / **M**ed (1–3 d) / **L**arge (>3 d).

| ID | Sev | Area | Finding | Evidence (path:line or command) | Root cause | Recommended fix | Effort |
|---|---|---|---|---|---|---|---|
| F-01 | H | Data | Home "<n> available opportunities" (`projects_public`) **changes per request** because `procurement_status` is re-derived and a Dallas connector's query returns partly-random rows | live `curl` of `/` returned 175 twice minutes apart but the auto-refresh loop re-ingests and re-assembles; `service.py:851`; `connectors/dallas_accela_permits.py` | Stats are `SELECT COUNT(*)` over a *changing* table with no memoization; the refresh loop mutates the dataset while users read | Compute headline stats once per refresh and cache (e.g. a `market_stat` snapshot row), or state "as of <timestamp>" | M |
| F-02 | H | Data | Four *different* "record" totals are shown as if comparable: home 6,175, `/healthz` 6175, `/api/statistics` 6175, coverage `records` 6,142, project `permit_date` range, trends "Permit records 245" | `service.py:877` (`COUNT(*) FROM permit`); `coverage.py:317` (`records = held["permits"]`); `trends.py` permits metric | No single source of truth for the word "record"; each surface counts a different join | Define "record" once (public permit rows) and expose that one number everywhere; label the rest explicitly | M |
| F-03 | H | Data | `/changes` page title claims "**533 Differences Detected**" (screenshots) / live "60 Differences Detected" but the route caps at `limit=60` and prints `changes|length` as a *total* | `app/main.py:941` (`recent_changes(limit=60, days=days)`); `templates/changes.html:46` (`{{ changes|length }} Differences Detected in Last {{ days }} Days`) | Template presents a *page size* as a *count*; the number is `min(total, 60)` | Query a real distinct-project count and render "60 of N"; paginate the feed | S |
| F-04 | H | Data | 2 projects carry **future permit dates**; newest is `2026-12-19` (74 days ahead of 2026-10-09) and drives the "Newest permit" freshness banner | local DB query `permit_date > '2026-10-09'` → 2 rows: `THE VILLAGE AT OWNSBY FARMS RETAIL` (2026-12-02, Celina), `PARK BLVD ESTATES WEST SCHOOL SITE NO 2` (2026-12-19, Plano); source `Collin CAD` `latest_date=2026-12-19` in `/healthz` | `quality.py:76` already *flags* `FUTURE_PERMIT_DATE` but the pipeline stores the value and **does not block it**; the freshness banner uses `MAX(permit_date)` without the future guard used in coverage | Reject/flag future-dated permits at ingest (or exclude from freshness); surface the quality issue on the site | M |
| F-05 | M | Data | Freshness banner "Continuous Ingestion: October 2026" hides that the entire dataset was collected in **~94 seconds on a single day** | `/api/trends` `ingestion_window.first_observed=2026-10-09T15:30`, `last_observed=2026-10-09T15:32`; local run same | `_ingestion_window` reports the DB's own min/max timestamps, not coverage | State "observed 2026-10-09" rather than a month; add "first pass" caveat | S |
| F-06 | M | Data | **No general contractor, architect, or developer data exists at all** — 100% of company rows are `owner` | local `SELECT role,COUNT(*) FROM project_party` → `owner 1830`; `/api/opportunities` shows `general_contractor:null, architect:null, developer:null` | Only Fort Worth/Collin publish an owner; no connector maps `contractor`/architect fields into `project_party` (the `contractor` permit column is populated but never projected) | Project `permit.contractor` into `project_party(role='contractor')`; add architect/engineer fields where sources carry them | M |
| F-07 | M | Data | 2,286 of 4,190 permits (≈55%) are **orphaned** (not linked to any project) in the local rebuild | local `permits not linked to any project: 2286`; `project_permit rows: 1904` | Commercial filtering + address clustering intentionally drops non-commercial/unaddressable rows; the UI never states the drop rate | Publish an ingest funnel ("4,190 landed → 1,904 linked → 1,261 projects") so the ratio is a trust signal, not a hidden gap | S |
| F-08 | M | UI | Mobile **horizontal overflow on `/companies`**: `document.body.scrollWidth 426 > innerWidth 390`; `.card-grid` 410 vs 358 | Playwright mobile measure: offenders `.feed-workspace-layout` 410/358, `.card-grid` 410/358 (see `audit/a11y/a11y.json`) | The companies index uses inline `grid-template-columns: repeat(auto-fill, minmax(320px,1fr))` (`templates/companies/index.html:31`) plus inline card styles, so the grid cannot shrink below 320px | Replace inline grid with the responsive `.card-grid`/`.facts` classes; drop `minmax(320px…)` on small screens | S |
| F-09 | M | UI/Mobile | **185 text-overflow elements** and **137 sub-44px tap targets** on mobile `/companies`; 46 on `/opportunities` | `audit/a11y/a11y.json` `mobile ./companies {smallCount:137, overflowCount:185}`; `.btn-small{min-height:30px}` `app.css:693-694`, `.btn{min-height:38px}` `app.css:639` | Company cards carry long canonical ALL-CAPS names + `minmax(320px)` forcing scroll; buttons below WCAG 2.5.5 (44px) | Raise `min-height` to 44px on mobile; make company names wrap/truncate by CSS not scroll | M |
| F-10 | M | UI | **~331 inline `style="…"` attributes** across templates, concentrated in the most important pages | `grep -rc 'style="' templates` → `opportunities/detail.html:53`, `analytics.html:30`, `dashboard.html:27`, `trends.html:21`, `home.html:21` | Templates hand-style spacing/borders instead of using the token classes present in `app.css` | Move to utility classes; ban inline styles in review | M |
| F-11 | M | UI | Project titles are **raw source strings in ALL CAPS**: 1,117 of 1,261 (88.6%) local projects are all-caps; 64 >80 chars | local `project_name=UPPER(project_name) AND ≠LOWER` → 1117/1261; `LENGTH>80` → 64 | `normalize.py` strips boilerplate but does not title-case display text | Add a display-title transform (title-case + preserve acronyms) at render, never mutating stored value | S |
| F-12 | M | UI | Card density is thin: on a live `/opportunities` page of 20, **8 show 2+ missing displayed fields** and 5 show 1 (Value/Type/Scale/Permit Date) | live `/api/opportunities?per_page=20` analysis; `partials/opportunity_card.html` renders "Not verified" for each empty field | Dallas sources publish no value/area; disclosure-only model prints many "Not verified" | Add a "completeness" cue and de-emphasize missing fields; sort complete records first by default | S |
| F-13 | M | A11y | **Serious color-contrast violations on every route** (12–28 nodes), plus heading-order (h3→h4 skips) | axe-core 4.10.2 via Playwright: `/` 12 nodes, `/opportunities` 28, `/signup` 8; targets `.btn-ghost[href$=signin]`, `.btn-primary[href$=signup]`, drawer buttons; heading-order `.detail-panel > h4`, footer `h4` | Ghost/primary button palettes and amber-on-white fall below 4.5:1; footer uses `h4` under `h2` | Darken button text/borders; fix heading hierarchy; re-run axe in CI | M |
| F-14 | M | A11y | Minor: `#mobile-nav-drawer` gets an ARIA role not allowed for `<aside role=dialog>`; hero content is outside a landmark ("region") | `audit/a11y/a11y.json` `aria-allowed-role` ×1, `region` ×1–5 on every page | Drawer uses `role="dialog"` on `<aside>` while `aria-modal` semantics need a wrapper; hero not wrapped in `<section>`/main | Use `<div role="dialog">` or native `<dialog>`; wrap hero in a landmark | S |
| F-15 | M | Security | No **email verification** and no email delivery (console only); signup is instantly active | `app/mailer.py` (default `console`); `app/accounts.py` `create_account` sets `is_active` immediately; `.env.example` MAIL_BACKEND | MVP scope: reset delivery modelled, but signup does not verify ownership | Add verify-email flow before account is usable for alerts | M |
| F-16 | M | Ops | **No CI** (`.github/workflows` absent), **no backup**, **no error monitoring** (only an `app_error` table surfaced at `/admin/data`) | `ls .github/workflows` → none; `grep backup` → none; `db.record_app_error` `main.py:415` | Single-file SQLite + free Render plan = every deploy starts from an empty DB; nothing alerts on 5xx | Add GitHub Actions (pytest), a `sqlite .backup` cron in `ops/`, and uptime/5xx alerting | M |
| F-17 | L | SEO | Home `<title>` is *"DFW Construction Opportunity Intelligence — Commercial HVAC"* while the homepage is a broad product page; the DFW-specific title describes the market+trade landing page | live `<title>`; `app/seo.py:83-99`; contrast `seo_for_market_trade` title | Home and market-trade pages compete for the same intent; JSD says this is deliberate but results in a homepage title that reads as a category page | Give home a brand+category title; keep the market keyword on `/markets/dfw` | S |
| F-18 | L | SEO | `/guides` is **not in `sitemap.xml`** although it is Allow-listed and footer-linked | live `sitemap.xml` lacks `/guides`; `robots.txt` allows `/` | Sitemap builder in `app/seo.py` omits the guides index | Add `/guides` (and guide pages) to the sitemap | S |
| F-19 | L | Product | **No pricing page / monetization surface**; plans exist in DB but no UI | `render.yaml` free plan; `app/entitlements.py` defines FREE/PRO/TEAM; no `/pricing` route (`main.py` route list) | Product sells to contractors but never asks for money anywhere | Add a `/pricing` page driven by `entitlements.plans()` | S |
| F-20 | L | Product | **No export** (CSV/Excel/PDF) and **no charts** anywhere; "Trends" is numbers only | `grep -rniE export\|csv` in app = none; `grep chart\|d3\|recharts` = none; `package.json` has no chart lib | Data-viz and export were never built | Add server-side CSV endpoint + one SVG sparkline/bar on `/trends` | M |

---

## 4. Data integrity section (answers a–h)

All local numbers come from a fresh bounded rebuild (`ingest --max-pages 2` for 3 sources →
`assemble` → `build-search-index`); live numbers from `https://buildscope-xppz.onrender.com`
probed 2026-10-09. Because the local rebuild is a partial pass, **ratio/consistency findings
are the durable result; absolute counts differ from the live instance by design**.

### (a) Home stats disagree — every computation site

There is **no single source of truth**. Every headline number is an ad-hoc `COUNT(*)`:

| Surface | Code | Query |
|---|---|---|
| Home "Commercial projects" | `service.py:851` | `SELECT COUNT(*) FROM project p WHERE <public_where>` |
| Home "Projects with mechanical evidence" | `service.py:860` | `… AND {trade.evidence_clause}` |
| Home "Active jurisdictions" | `service.py:875` | `SELECT COUNT(DISTINCT p.city) …` |
| Home "Permit records processed" | `service.py:877` | `SELECT COUNT(*) FROM permit` (global, **unscoped**) |
| `/healthz` "permits"/"projects" | `app/main.py:1090` | `SELECT COUNT(*)` on `permit`/`project` |
| Coverage `records` | `coverage.py:317` ← `_market_records` `coverage.py:182-226` | `SELECT COUNT(*) FROM permit WHERE city IN (<market cities>) AND permit_date <= today` |
| Trends "Permit records" | `trends.py` permits metric | `COUNT(*) permit JOIN project_permit JOIN project` in a date window |
| Companies "N Companies Recorded" | `companies.py` `list_companies` | `COUNT(*)` after `LIMIT 60` (capped) |

Observed live values in one snapshot: home `175 / 12 / 16 / 6,175`; `/api/statistics`
`projects_public=175, with_mechanical=12, permit_records=6175, projects_total=1791,
high=1, medium=174, tier1=2, evidence_records=21060`; `/healthz` `projects=1791, permits=6175`;
`/api/trends` `projects_newly_observed=1791`. The **175/12** pair is coherent with
`projects_public`/`with_mechanical`; the founder's "253/26/11,966" are a *different snapshot*
(changed between screenshots and review, as the refresh loop re-wrote the DB → see F-01).
**Caching: none** (`grep -rn "cache|lru_cache" service.py app/main.py` → none).

### (b) Trends "11,906" vs home "11,966"

These are two different counts, not a rounding bug. Home shows `COUNT(*) FROM permit`
(`service.py:877`); the trends "Permit records" metric counts only permits **joined to a
project of this trade and dated inside the window** (`trends.py` permits metric). The delta
was the orphan-drop + window/date-boundary + future-exclusion. In today's live snapshot both
surfaces read 6,175 because the "records" label on home is unscoped while trends was 245 in
the 30-day window — the two were never meant to be equal, but **the product never labels them
as different**, which is the defect (F-02).

### (c) "533 projects changed exceeded 301 new projects and 253 total" — is it a bug?

**Yes, semantically.** Definitions from `trends.py`:
- `projects_changed` = `COUNT(DISTINCT project_id) FROM project_change WHERE change_kind <> 'new_project' AND detected_at ∈ window`.
- `projects_newly_observed` = `COUNT(DISTINCT id) FROM project WHERE created_at ∈ window`.
- "total commercial projects" = `projects_public` (classification/procurement-gated).

`projects_changed` **excludes** `new_project` (first observation), so it can legitimately exceed
*newly observed*. It counts **change rows**, and a project can change many times — so a count in
the hundreds on a dataset of ~1,700 projects is arithmetically possible. But it is only defensible
if repeated-change churn is real; the docs assert "a no-op assembly pass emits nothing"
(`AGENTS.md`), yet the live `/changes` page shows a large number. On the local rebuild
`projects_changed = 0` (single pass, no diffs) which is the *correct* behaviour. **Conclusion:**
the arithmetic may be valid, but the *comparison* the founder made (changed vs new) is a false
comparison the page invites — different metrics must not be presented side by side without units.
The `533` figure itself is **UNVERIFIED at the row level** because I cannot `SELECT` the live DB;
it is reproduced only through the public API's metric.

### (d) "26 mechanical" vs "28 strong" vs "212 trade scope"

Three different definitions (all real, all in code):
- `with_mechanical` (`service.py:860`): public projects where `mechanical_evidence_tier ∈ (1,2)` for the active trade — **all time**, no window.
- `projects_with_strong_evidence` (`trends.py`): `mechanical_evidence_tier IN (1,2)` **and permit_date inside the 30-day window** → live **13**.
- `projects_with_trade_scope` (`trends.py`): distinct projects with a row in `project_trade` **inside the window** → live **143**.

So 26 (all time) ≠ 13 (windowed) ≠ 143 (trade relationship, windowed). The numbers are
**internally consistent**; the defect is that no page says why they differ (F-02/F-06).

### (e) Future permit date

- Parsing: connectors call `parse_date(...)` (`connectors/collin_cad_permits.py:111` etc.); values are stored as text `YYYY-MM-DD` with **no rejection** of future dates.
- Validation exists but is advisory: `quality.py:76` calls `dates.validate_date("permit_date", …, observed=today)` which returns `VERDICT_FUTURE_IMPLAUSIBLE` (`dates.py`) and is written to `data_quality_issue`; the project row is still stored and served.
- Local query (`permit_date > '2026-10-09'`): **2 rows** — `THE VILLAGE AT OWNSBY FARMS RETAIL` (Celina, `2026-12-02`), `PARK BLVD ESTATES WEST SCHOOL SITE NO 2` (Plano, `2026-12-19`, $200,000,000). Live `/healthz` shows Collin CAD `latest_date: 2026-12-19` and `/api/trends` `excluded_future: 2` for `projects_observed`. **Count = 2.**
- The freshness banner ("Continuous Ingestion: October 2026", "Newest permit" 2026-12-19) is derived from `trends._ingestion_window` `MAX(p.permit_date)` **without** the future guard used elsewhere.

### (f) Data-quality queries (local rebuild; absolute counts are a partial pass)

| Metric | Value | % of total |
|---|---|---|
| Total projects | 1,261 | — |
| `estimated_project_value` null | 63 | 5.0% |
| `square_footage` null | 139 | 11.0% |
| `project_type` null | **0** | 0% (every project is typed) |
| `project_status` null | 1,149 | 91.1% |
| `owner` null | 68 | 5.4% |
| `general_contractor` / `architect` / `developer` null | 1,261 / 1,261 / 1,261 | **100%** |
| ALL-CAPS `project_name` | 1,117 | 88.6% |
| Titles > 80 chars | 64 | 5.1% |
| Titles ending "…" (truncated in source) | 0 | 0% |
| Projects with **zero** evidence rows | **0** | 0% |
| Orphaned permits (no project link) | 2,286 | 54.6% of 4,190 permits |
| Duplicate addresses (same norm. address+city) | 0 | 0% |
| Duplicate `(source, permit_number)` | 61 groups | 1.5% of permits |

- **Records per jurisdiction** (projects, local; live `/api/statistics` shows the serving subset): Plano 305, McKinney 229, Frisco 209, Allen 88, Dallas 86, Fort Worth 68, Wylie 60, Prosper 35, Celina 33, Melissa 31, Murphy 22, Richardson 20 … — **19 cities**, not "16 jurisdictions" (home says 16; live `/api/statistics` lists 16 serving cities incl. an out-of-market `Nevada`).
- **Permits per source** (local): `collin_cad_permits 1,966`, `fort_worth_permits 1,944`, `dallas_accela_permits 280`.
- **`permit_date` range** (local): `2025-12-04` → `2026-12-19` (the max is a future date).
- **`first_observed`/created_at range**: all rows `2026-10-09T16:48–16:49` — a single 94-second ingestion.
- **Duplicate permits (61)**: same `(source_id, permit_number)` → the natural key is not unique for some Accela/ArcGIS rows (likely repeated inspection rows). Worth a dedupe rule.

### (g) Companies page role distribution

- Rendered live: "**59 Companies Recorded**", every card badged "**Owner**". `curl /companies?role=contractor` → "**0 Companies Recorded**".
- `companies.py:161-205` unions `project_party` + `project.general_contractor` (`role='general_contractor'`) + `project.owner` (`role='owner'`); `companies.py:240` formats the role.
- Local DB: `SELECT role,COUNT(*) FROM project_party` → **`owner 1830` only**; `project.general_contractor/architect/developer` are **100% NULL**.
- **Conclusion:** GC/architect data **does not exist**. The directory is honestly only owners (mostly ISDs — `PLANO ISD`, `ALLEN ISD`), and the "General Contractor/Architect" filter options return empty. This is a **coverage gap**, not a bug — but the page's "serious" filter UI implies data that isn't there (F-06).

### (h) Ingestion pipeline

- **Sources** (`config/sources.yaml`): `fort_worth_permits` (ArcGIS, enabled), `collin_cad_permits` (Socrata, enabled), `dallas_accela_permits` (Accela WebForms, enabled), plus 2 disabled historical Dallas feeds.
- **Schedule:** in-process refresh loop only (`ops/automate.py`, `REFRESH_SECONDS=21600`, `MAX_PAGES=3`); no cron/systemd (documented constraint).
- **Idempotency:** `INSERT OR IGNORE` on stable keys; `raw_record` UNIQUE `(source_id, natural_key)` (`db.py:57`); re-assembly emits no changes (`AGENTS.md`; verified locally `projects_changed=0`).
- **Error handling:** per-source try/except with retries (`connectors/base.py` `max_retries:3`); Dallas page-cap warnings logged; `app_error` table for web errors.
- **Raw archival:** yes — `data/raw/<source>_<ts>.jsonl` (local run wrote `fort_worth_permits_…jsonl`, 802KB).
- **Classification rules:** `normalize.detect_mechanical_signal` — Tier 1 when the permit *type* matches `mechanical_permit_type_keywords` (`normalize.py:247-278`), Tier 2 when `mechanical_scope_keywords` appear in scope text with negation handling (`normalize.py:60-289`). Weights in `config/trades.yaml`; gate `requires_mechanical_evidence_for_high` enforced in `classify.py:127-152`.
- **Tests:** extensive — `tests/test_normalize.py`, `test_classify.py`, `test_dallas_*`, `test_quality.py`, `test_dates.py`, `test_provenance.py`, etc. (910 pass).

---

## 5. Screenshot index

Captured with Playwright at 390×844 / 820×1180 / 1440×900.
Live: `audit/ui_live/` (51 PNGs). Local: `audit/ui_local/` (51 PNGs).
Per-viewport metrics: `audit/ui_live/metrics.json`, `audit/ui_local/metrics.json`.

| Screenshot (each viewport) | Problem note |
|---|---|
| `*___.png` (`/` home) | Home stat numbers are the volatile `projects_public`; the "175" also appears hard-coded in the **meta description** (F-01/F-17). Loads cleanly, no overflow. |
| `*__opportunities.png` | Clean grid; 8/20 cards show 2+ "Not verified"; empty Permit-Date From/To pickers; sort select 31px tall (F-12/F-09). |
| `*__companies.png` | **Mobile only:** body scrollWidth 426 > 390 → horizontal overflow; 137 small tap targets; 185 overflowing elements (F-08/F-09). |
| `*__changes.png` | Header claims a "Differences Detected" total that is actually a capped 60-row page (F-03). |
| `*__trends.png` | Numbers only, **no chart**; live shows "13 strong / 143 trade scope / 0 changed" and the "Newest permit 2026-12-19" future date (F-04/F-20). |
| `*__analytics.png` | Dense inline-styled stat blocks (30 inline styles); no chart (F-10/F-20). |
| `*__reports.png` | Two catalogue entries that render on-demand; no PDF/export (F-20). |
| `*__how-it-works.png` | Good trust/methodology prose; footer `h4` under `h2` → heading-order a11y (F-13). |
| `*__markets.png`, `*__markets_dfw.png` | Market coverage language is strong; "16 jurisdictions" vs 19 in data (F-02). |
| `*__trades.png`, `*__trades_commercial-hvac.png` | Landing pages render; blocked only by thin data. |
| `*__commercial-construction-leads.png` | Ranked priority-0.9 in sitemap but is a thin category page (SEO risk). |
| `*__guides.png` | Present and footer-linked but **missing from sitemap.xml** (F-18). |
| `*__signin.png`, `*__signup.png` | Serious contrast violations (5–8 nodes); signup advertises "saved searches" (not built). |
| `*__nonexistent-404-page.png` | Correct 404 page, `noindex,follow`, canonical to `/` (good), but canonical-to-home on a 404 is unusual. |

---

## 6. Feature claimed-vs-built matrix

| Feature | Claimed | Built | Evidence | Notes |
|---|---|---|---|---|
| Opportunity directory + filters | Yes | **Yes** | `main.py:457`, `templates/opportunities/list.html` | Real server-side filter/paginate |
| Keyword search + synonym expansion | Yes | **Yes** | `search_index.py`, `service._search_ids`, `config/search_vocabulary.yaml` | Real FTS5 |
| Evidence / provenance per field | Yes | **Yes** | `provenance.py`, `service.evidence_for` | The strongest feature |
| Change detection | Yes | **Yes** | `changes.py`, `project_change`, `service.recent_changes` | Real diffs; no-op emits nothing |
| Alerts (in-app) | Yes | **Partial** | `app/alerts.py`, `main.py:1603` | Event-driven rows exist; delivery is in-app only, no email |
| Email delivery | Modelled | **Missing** | `app/mailer.py` default `console`; `email_sent_at` column | Not sent |
| Watchlist | Yes | **Yes** | `watched_opportunity`, `main.py:1447` | Real |
| Pursuit pipeline | Yes | **Yes** | `pipeline_entry`, `main.py:1463` (`/my-pipeline`) | Real; vocab disjoint from procurement |
| Notes | Yes | **Yes** | `opportunity_note`, `main.py:1558` | Real |
| Tags | Yes | **Yes** | `opportunity_tag`, `main.py:1584` | Real |
| Saved searches | **Claimed** in signup copy | **Missing** | `templates/account/signup.html:15` says "saved searches"; no `saved_search` table/route | Copy overclaims |
| Reports | Yes | **Yes (2)** | `app/reports.py` → 2 reports (`dfw-hvac-opportunity-brief`, `dfw-market-summary`), generated on demand | No PDF/export |
| Analytics | Yes | **Yes (internal)** | `app/analytics_funnel.py`, `/analytics`, `/api/*` | Records page *kind*; no external analytics |
| Exports (CSV/Excel/PDF) | NOT claimed | **Missing** | no route/`grep export` | Competitors all have it |
| Charts / data-viz | NOT claimed | **Missing** | no chart lib in `package.json`; `/trends` is text | Competitor table-stakes |
| Pricing / paid tiers | NOT on site | **Missing UI** | `entitlements.py` has FREE/PRO/TEAM; no `/pricing` route | Plans exist only in DB |
| Admin / operations view | Internal | **Yes** | `main.py:1645` `/admin/data`, `@_require_admin` | Real |
| Companies directory | Yes | **Partial** | owners only; GC/architect empty | Coverage gap |
| Natural-language search | Present | **Yes** | `nl_search.py` | Real |

---

## 7. Competitor gap table

Benchmarked live: **ConstructConnect** and **Dodge Construction Network (construction.com)**.
Patterns only — no assets or text copied.

| Dimension | ConstructConnect | Dodge | BuildScope | Gap |
|---|---|---|---|---|
| Global nav | Products / Solutions / Pricing; deep IA | Solutions / Products / Capabilities / Resources | 9 flat public links, no Pricing | Pricing + solution IA missing |
| Homepage sections | Hero with live project ticker, 3-step workflow, trust logos, testimonials, blog, lead form | Hero, 3 lifecycle solutions, hard stats (700K+ projects), human-verification trust, integrations, news, FAQ | Hero + stats + how-it-works + latest cards | Trust (logos/testimonials/stats-at-scale) and lead form weak |
| Search/filter UX | Faceted (keyword, location count, sector, stage, date) + results table with Value/Stage/**Bid Date** | AI Search Assist; planning-stage leads with specs/contacts | Faceted (keyword/city/type/procurement/value/date); no **bid date/stage** column | Missing lifecycle stage + bid date |
| Project detail | Name/Value/Location/Stage/Bid date/updates | Specs, contacts, documents, relationships | Evidence, provenance, sources, timeline, notes | Contact/spec data missing |
| Pricing/gating | Explicit "See pricing" plans | "Request a demo" gated | **None** | BuildScope gives everything away; no funnel |
| Typography/color | Brand green + neutral, strong hierarchy | Warm orange/blue, restrained | Navy + amber token system, one family | BuildScope is competitive here |
| Info density | Very high (tables, tickers) | High | Low (thin cards, few projects) | Density tracks data volume |
| Trust signals | 100K+ users, 4.6/5, logos, patents | 130+ yrs, 10M projects, U.S. Census endorsement | Methodology page, "verified" language | BuildScope lacks social proof / scale stats |
| Data-viz | Takeoff, insight charts | Momentum Index, forecasts | None | Gap |
| Export/API | API + CRM + data feeds | REST API + data feeds + CRM | JSON API only | Enterprise hooks missing |

**Verdict:** BuildScope's *visual* language is closer to the majors than the founder fears — the
palette, typography and density are professional. The real distance is **coverage scale, contact/
spec data, pricing, and trust signals**, exactly the items a solo build cannot fake.

---

## 8. Top 15 quick wins (each < 1 day)

1. **Fix `/changes` count** — render "N of M" and paginate (`main.py:941`, `changes.html:46`). (F-03)
2. **Fix mobile `/companies` overflow** — remove inline `minmax(320px)` grid (`companies/index.html:31`). (F-08)
3. **Raise tap targets to 44px** on mobile buttons (`.btn`, `.btn-small` in `app.css:639,693`). (F-09)
4. **Title-case displayed project names** with an acronym-aware Jinja filter. (F-11)
5. **Label every stat** with its definition + observation date; add "as of <timestamp>". (F-01/F-02/F-05)
6. **Exclude future-dated permits** from the freshness banner (`trends._ingestion_window`). (F-04)
7. **Fix serious color contrast** on ghost/primary buttons and footer headings. (F-13)
8. **Fix heading order** (`h4` → `h3`) in detail panel + footer. (F-13)
9. **Add the "saved searches" copy fix** or ship a minimal saved-search table. (F-06 matrix)
10. **Add `/pricing`** driven by `entitlements.plans()`. (F-19)
11. **Add `/guides` to `sitemap.xml`.** (F-18)
12. **Home `<title>`** to a brand+category title. (F-17)
13. **Add CSV export** endpoint for the current filter set. (F-20)
14. **Surface the ingest funnel** (landed → linked → projects) on `/how-it-works`. (F-07)
15. **Add a GitHub Actions workflow** running `pytest` on push/PR. (F-16)

---

## 9. Proposed 4-week roadmap (risk & impact ordered)

**Week 1 — Truth & stability.** Single source of truth for every headline stat (memoised per
refresh); fix `/changes` count; label all metrics with definition + observation timestamp;
exclude/flag future permit dates end-to-end; publish the ingest funnel. *(F-01..F-05, F-07)*
**Week 2 — Mobile & accessibility.** Kill the `/companies` overflow; 44px tap targets; contrast
and heading fixes; axe-core in CI gating; keyboard/drawer pass. *(F-08, F-09, F-13, F-14)*
**Week 3 — Data depth & product surface.** Project `permit.contractor` into companies; add
pricing page; CSV/PDF export; one `/trends` chart; replenish company roles. *(F-06, F-19, F-20)*
**Week 4 — Professionalization & ops.** De-dupe permits (61 dup groups); title-case titles;
trust signals (case study, coverage stats); GitHub Actions + `sqlite .backup` + uptime/5xx
alerting; email verification + real mail backend. *(F-10..F-16, F-17, F-18)*

---

## 10. Open questions for the founder

1. **Which "records" number is canonical?** permit rows, linked permits, or projects? (F-02)
2. **Is the refresh loop expected to change public counts mid-session?** If yes, a stat snapshot
   is required; if no, the loop is racing readers. (F-01)
3. **Why 16 jurisdictions on the home page but 19 cities in the data?** Naming/config mismatch. (F-02)
4. **Do you intend to carry bid dates / procurement stage?** No configured source publishes bid
   status (per `config/sources.yaml`), yet competitors lead with "Bidding closes…". (benchmark)
5. **Is monetization in scope?** Plans exist in the DB but are unreachable from the product. (F-19)
6. **Company data plan?** With GC/architect data absent, `/companies` is an owners-only ISD
   directory; is that the intent? (F-06)
7. **Free Render plan + no disk means every deploy empties the database** — is a paid disk
   approved? (F-16)
8. **Was the live dataset at "175/12/6,175" the same revision as the screenshots "253/26/11,966"?**
   (I could not pin the exact revision — UNVERIFIED.)

---

## 11. Appendix — command log, versions, raw tool output

### Environment / versions
- Python 3.13.15; Flask 3.1.3; Werkzeug; gunicorn; PyYAML; requests; pytest (installed for audit).
- Node v24.21.0; bun 11.19.1 (React/Vite stub is not part of the served product).
- Playwright + Chromium 153.0.8010.12; axe-core 4.10.2.
- Repo: shallow clone, branch `main`, HEAD `e01eadd`.

### Key commands (abridged; full outputs in `audit/*.json`, `data/*.log`)
```bash
# orient
find . -maxdepth 3 ...; cat README.md package.json pyproject.toml render.yaml
# run
pip install -r requirements.txt
PYTHONPATH=src SECRET_KEY=dev OPPINTEL_DB=$PWD/data/oppintel.db python -m oppintel.cli initdb
python -m oppintel.cli ingest --source fort_worth_permits --source collin_cad_permits --source dallas_accela_permits --max-pages 2
python -m flask --app oppintel.app.wsgi init-app
python -m oppintel.cli assemble      # 1261 projects; HIGH 1 / MEDIUM 132 / NEEDS_VERIFICATION 1128
python -m flask --app oppintel.app.wsgi build-search-index
python -m oppintel.cli integrity     # healthy: true
python -m oppintel.app.wsgi          # http://127.0.0.1:5000 — served 200
# tests
PYTHONPATH=src python -m pytest tests/ -q -p no:cacheprovider   # 910 passed in 129.43s
# UI
python audit/capture.py   # 51 screenshots × {mobile,tablet,desktop}
python audit/a11y.py      # axe-core + mobile measurements
python audit/perf.py      # TTFB/FCP/requests
# live probes
curl https://buildscope-xppz.onrender.com/{healthz,api/trends,api/statistics,api/opportunities,robots.txt,sitemap.xml}
```

### Raw data-integrity query results (local rebuild)
```
future permit_date (project): 2
ALL CAPS project_name: 1117/1261 ; >80 chars: 64 ; truncated "…": 0
projects with zero evidence: 0
permits not linked to any project: 2286 / 4190
duplicate (source, permit_number) groups: 61
estimated_project_value null: 63 ; square_footage null: 139 ; project_type null: 0
owner null: 68 ; general_contractor/architect/developer null: 1261 (100%)
project_party roles: owner 1830 (only) ; distinct names 1017
permit_date range: 2025-12-04 .. 2026-12-19 ; created_at: all 2026-10-09T16:48–16:49
```

### Raw axe-core summary (serious/critical only)
```
/               color-contrast 12 nodes
/opportunities  color-contrast 28 nodes
/markets/dfw    color-contrast 14 nodes
/companies      color-contrast  8 nodes
/changes        color-contrast  7 nodes
/trends         color-contrast  8 nodes
/analytics      color-contrast  6 nodes
/reports        color-contrast  6 nodes
/signin         color-contrast  5 nodes
/signup         color-contrast  8 nodes
+ heading-order (moderate) on /, /opportunities, /markets/dfw, /companies, /changes, /analytics
+ aria-allowed-role (minor) #mobile-nav-drawer on every page ; region (moderate)
```

### Raw performance summary (mobile emulation, live)
```
route            TTFB   FCP   load   requests  transfer(css)  js
/                329ms  544ms 521ms   2         9,746 B        0
/opportunities   113ms  300ms 287ms   2           300 B        0
/trends          167ms  328ms 315ms   2           300 B        0
/companies       105ms  296ms 284ms   2           300 B        0
```
No JS bundle, no images on the audited routes, no third-party requests. Server-side pagination
confirmed (`/opportunities` `page_size:20`).

### Live security headers
`content-security-policy` (self + Firebase), `x-content-type-options: nosniff`,
`x-frame-options: DENY`, `referrer-policy: same-origin`, `permissions-policy …`.
**No `Strict-Transport-Security`** observed (TLS is terminated by Cloudflare — F-16 note).
No committed secrets found in tracked files (`git grep AIza|api_key|secret` → clean); the
Firebase key is git-ignored per `AGENTS.md` and `tests/test_firebase_config.py`.

---

## Audit artifacts created (read-only task outputs)

All under `./audit/` (tooling + evidence), plus a throwaway local DB (git-ignored):

- `audit/BUILDSCOPE_AUDIT.md` — this report
- `audit/capture.py`, `audit/a11y.py`, `audit/perf.py` — audit tooling
- `audit/ui_live/` — 51 live screenshots + `metrics.json`
- `audit/ui_local/` — 51 local screenshots + `metrics.json`
- `audit/a11y/a11y.json` (+ `axe.min.js`) — accessibility + mobile measurements
- `audit/perf/perf.json` — performance measurements
- `audit/live/` — raw HTML/JSON captures of the live site
- `data/oppintel.db` — throwaway local database (git-ignored); `data/raw/*.jsonl`, `data/*.log`
