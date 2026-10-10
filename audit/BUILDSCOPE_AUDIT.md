# BuildScope — Principal Engineering & Staff UI Audit (Re-audit)

**Type:** READ-ONLY audit. No application code, schema, data, or configuration was modified.
**Audit date:** 2026-10-10 (UTC). **Audited revision:** branch `wp3-visual` @ `1b694f4`
(working tree). `origin/main` is `e01eadd` and was never touched.
**Live URL probed:** https://buildscope-xppz.onrender.com
**Local run:** gunicorn on a **copy** of the DB (`/tmp/audit_copy.db`) — source DB never written.
**Auditor:** OpenHands agent (AI).

**Method:** static code review + read-only SQLite `SELECT`s against a copy + live HTTP probes +
a local run + Playwright screenshots at 3 viewports + axe-core. Every number is copied from a
command output; the commands are in the Appendix. Anything not verifiable is marked
**UNVERIFIED** with the reason.

> **Provenance note.** A prior audit exists at `audit/BUILDSCOPE_AUDIT_2026-10-09.md` (dated
> 2026-10-09). It found 910 tests, 46 routes, and serious contrast/a11y failures plus a
> `/companies` overflow. Those specific issues have since been fixed by WP1/WP2 (1246 tests now
> pass; axe is clean; no route overflows). This report is the **current** state; where it
> contradicts the 2026-10-09 audit, the current measurement wins.

> **Disclosure.** The instructions for this session mixed a read-only audit request with a
> description of ongoing implementation on branch `wp3-visual`. Before this report, five commits
> existed on that feature branch (K1–K5); `origin/main` remains untouched at `e01eadd`. This
> report itself creates only `audit/` artifacts. See Finding C6.

---

## 1. Executive summary

BuildScope is a genuine, well-layered B2B intelligence product with a disciplined, evidence-first
backend and a fast, pure-server-rendered UI. It is **not** mock: 1246 tests pass on a real
pipeline, every displayed fact traces to a permit row, and the stat layer has a real
single-source-of-truth. The remaining gaps are **commercial completeness** (no pricing, no export,
no charts on live), **thin company/GC data**, **SEO canonicals that all point at `/`**, and a
**hostile free-preview ops posture** (self-seeding ingest→assemble→monitor every boot, 3 pages ×
4 sources, no disk).

Overall health score (1–10):

| Area | Score | One-line rationale |
|---|---|---|
| Data integrity | **8/10** | Consistent, snapshot-backed counts; 2 future permits; 2,286 dropped permits; 63% null value |
| UI / design | **7/10** | Real token system; coherent; 349 inline `style=` attrs across 34 templates |
| Mobile | **7/10** | No overflow anywhere; two 15px footer link tap targets; filter sheet is good |
| Performance | **9/10** | 0 JS, 1 CSS request (8.7KB gzip), TTFB ~180ms warm; 1.3s cold |
| Accessibility | **8/10** | axe-clean (WCAG 2.1 AA); 404 has no `<h1>`; axe colour-contrast "incomplete" worth manual check |
| Security | **8/10** | CSP, CSRF (403), PBKDF2, rate-limit, no tracked secrets; no HSTS header; no email verification |
| SEO | **6/10** | `/trends`, `/companies`, `/guides`, `/reports`, `/analytics`, `/changes`, `/markets`, `/trades` all canonical → `/` |
| Product completeness | **6/10** | 46 routes, real workflow; no pricing, no export, no charts on live, GC/architect data absent |
| Code quality | **8/10** | Layered, documented, heavily tested; ~600-line route module |
| Ops | **6/10** | Blueprint correct but fragile on Free; no CI, backups, or error monitoring |

**Headline:** the core is real and now measurably healthy; the two things keeping it from
Dodge/ConstructConnect-class are **no monetization surface** and **a canonical/SEO defect that
de-indexes every secondary page**.

---

## 2. Architecture overview

```
   public gov sources (no auth)
   Fort Worth ArcGIS · Collin CAD Socrata · Dallas Accela WebForms
        │  connectors order newest-first, since_months=24 at source
        ▼  raw payload archived → data/raw/*.jsonl
   normalize → assemble → provenance(evidence) → classify → eligibility → procurement
        │                       │                        │
        │                       └ evidence / evidence_history (append-only)
        ▼
   changes.py (diff) → project_change → monitoring → alerts (in-app)
   intelligence.derive_for_project → entity/event/trade graph
        │
        ▼
   service.py  OpportunityService   ← THE read boundary (only app module on intel tables)
   stat_snapshot.py                 ← only app module running headline COUNT(*)
        │
        ▼
   Flask app (app/main.py, ~46 routes) · app/api.py (JSON) · seo.py · accounts.py · workflow.py
   Jinja templates (47) · static/css/app.css (58,410 B, 76 tokens, 291 var() uses) · 0 JS
        │
        ▼
   SQLite (db.py: SCHEMA intel + APP_SCHEMA app) · single file
   Deploy: render.yaml → bash ops/start.sh → gunicorn (+ in-process refresh thread)
```

Two-layer separation is real and test-enforced (`tests/test_app_architecture.py`).

---

## 3. Findings table

| ID | Sev | Area | Finding | Evidence | Root cause | Recommended fix | Effort |
|---|---|---|---|---|---|---|---|
| S1 | High | SEO | `/trends`, `/companies`, `/guides`, `/reports`, `/analytics`, `/changes`, `/markets`, `/trades` all emit `canonical="/"` | `curl` per route; `seo.py:257` `simple(title,description,path="/")` default; callers in `main.py:955,1040,1056,1085,1178,1198` omit `path=` | `Seo.simple()` has a default `path="/"` and call sites never pass the real path | Pass each route's own path to `simple()`; add a test asserting canonical != `/` for public routes | S |
| S2 | Med | SEO | Broken `<title>` on trade page: "Commercial **Commercial HVAC / Mechanical** …" | `curl /trades/commercial-hvac`; `main.py:854` `f"Commercial {trade.short_label or trade.label} …"`; `seo.py:214-222` prefixes "Commercial " too | Template `page_title` and SEO title both prefix "Commercial"; `short_label` is null so `label` is used | Use `short_label or label` in one place; drop the doubled prefix | S |
| D1 | High | Data | 2 projects have a future `permit_date` (2026-12-02, 2026-12-19) relative to 2026-10-09 | SQL; examples id 1247 (Celina), id 413 (Plano) | Permit dates parsed but not sanity-bounded; future dates pass through `dates.py` | Flag future permit dates in `quality.py` (already flags them elsewhere) and exclude from occurrence metrics | S |
| D2 | Med | Data | Refresh drops most rows: 4,190 permits landed → only 1,904 linked (2,286 dropped, 54.6%) | `market_stat_snapshot.metrics.funnel`; Fort Worth 1,944 landed → 93 linked | Assembly eligibility/linking discards non-commercial or unlinkable rows | Surface the funnel on `/how-it-works`; tune `assemble.py` linkage | M |
| D3 | Med | Data | 63/1261 (5.0%) projects have null `estimated_project_value`; 139/1261 (11.0%) null `square_footage` | SQL | Source does not always publish the field | Already rendered as "Not published by the source" — acceptable; document coverage | S |
| D4 | Low | Data | 1,117/1,261 (88.6%) titles are ALL CAPS at rest | SQL | Titles stored verbatim from source | Already title-cased at display (`presentation`/`titlecase`); consider normalizing at ingest | S |
| D5 | Low | Data | 490 address keys carry repeated permits within ≤2 distinct months | SQL | Multiple permits per site | Confirm these are legitimate multi-trade permits, not duplicates; expose "shares building" | M |
| P1 | High | Product | No pricing page, no plan UI, no upgrade path; `set-plan` is CLI-only | `grep route` for pricing → none; `main.py:2460` `set-plan` | Monetization deliberately not implemented yet | Add `/pricing` + plan comparison; gate export/contacts | L |
| P2 | High | Product | No export (CSV/PDF/Excel) anywhere | `grep csv|excel|export` in `main.py`/`api.py` → none | Not built | Add CSV export on the directory (entitlement-gated) | M |
| P3 | Med | Product | `/companies` shows only Owner roles; `?role=contractor` and `?role=architect` return **zero** | live curl with role filter; DB `project_party` roles = `[('owner',1830)]` only | GC/architect never extracted from source payloads | Extract contractor/architect from permit `contractor` field / Accela parties | L |
| P4 | Med | Product | Trends has no chart; inline SVG chart exists only on local working tree | local `/trends` grep `bar-chart`=1; live=0; live commit has none | The K5 chart is committed on the feature branch but **not deployed** to `main` | Deploy the branch; or accept and ship | S |
| U1 | Med | UI | 349 inline `style=` attributes across 34 templates | `grep -ro 'style="' templates | wc -l` | Incremental additions bypass the token system | Migrate to classes; K8 in progress | M |
| U2 | Low | UI | Only 8 `!important` in the whole stylesheet; token system is genuine (76 tokens, 291 `var()`) | `grep -c '!important'`; `:root` block | — | Keep; the discipline is good | — |
| U3 | Low | UI | No clipped/truncated card content detected (prior-audit issue resolved) | Playwright `scrollHeight>clientHeight` scan → `[]` on `/companies`,`/opportunities` | Fixed by WP2/WP3 card work | — | — |
| M1 | Low | Mobile | Two footer nav links are 15px tall (below 44px) — "Terms", other footer links | Playwright tap-target scan | Small text links in footer | Add padding to footer link targets | S |
| A1 | Low | A11y | 404 page has no `<h1>` (`page-has-heading-one`) | axe on `/this-does-not-exist` | 404 template | Add an `<h1>` | S |
| A2 | Low | A11y | 21–61 nodes per page are axe `color-contrast` **incomplete** (needs manual check) | axe incomplete list | Likely CSS gradients/tinted backgrounds axe can't compute | Manual contrast check | S |
| SEC1 | Med | Security | No `Strict-Transport-Security` header | `curl -D -` grep HSTS = 0 | Not set in `security.py` | Add HSTS (careful with subdomains) | S |
| SEC2 | Med | Security | No email verification on signup | `accounts.py` — no verify flow | Not built | Add token verification (mailer exists) | M |
| SEC3 | Low | Security | `script-src` includes `'unsafe-inline'` | live CSP header | Firebase/GTM integration | Move to nonces; WP2 added nonce tests | M |
| SEC4 | Low | Security | Dependency/secret scan clean in this checkout | `git grep AIza|sk-` = none; `firebase-applet-config.json` untracked; history is a shallow clone (42 commits) | — | **UNVERIFIED** for full history: shallow clone prevents a complete history scan | S |
| O1 | High | Ops | Free preview rebuilds the whole dataset on every deploy/boot: initdb→ingest(3 pages×4 sources)→assemble→index→monitor | `render.yaml` (no disk, `plan: free`); `ops/start.sh:42`; live `projects_total` 6,682 | No persistent disk on Free | Run on a paid plan with the disk block enabled | M |
| O2 | Med | Ops | No CI pipeline; no backups; no error monitoring | no `.github/workflows`; `render.yaml` has none | Not configured | Add CI (pytest) + scheduled `backup.sh` + Sentry | M |
| O3 | Low | Ops | Cold start adds ~1.0–1.1s TTFB on first hit after idle | live TTFB try1 1.21s vs 0.17–0.18s warm | Free plan idle sleep | Paid plan / keep-warm ping | S |
| O4 | Low | Ops | `data/*.jsonl` raw captures keep growing, unrotated | `ls data/raw` shows 13 files | Retention not scheduled | Schedule `data/raw` retention | S |
| C1 | Low | Code | `main.py` is ~2,500 lines of route closures | `main.py` line count | Single module | Extract blueprints | L |
| C2 | Low | Code | One f-string SQL in `seo_report.py:190` (constant fragment, no user input) | grep | — | Leave; not injectable | — |
| C3 | High | Ops/Integrity | `oppintel.db` is git-ignored, so **`origin/main` deploys with an empty DB** and relies entirely on the ingest loop | `git check-ignore data/oppintel.db` ✓; `requirements.txt` has no data | By design, but means the only "data" is the pipeline output | Accept, or ship a seed snapshot | M |

---

## 4. Data integrity (answers a–h)

### (a) Home-page stats — one source of truth
Home stats are rendered from `stats = g.service.market_statistics()` (`main.py:511`),
consumed in `home.html:46-58`. **Live, home and `/api/statistics` now agree exactly:**

```
home evidence-stat-num: 741 / 141 / 18 / 59,521
/api/statistics:        projects_public 741 · with_mechanical 141 · cities 18 · permit_records 59,521
```

The headline counts have a **single source of truth**: `stat_snapshot.py:compute_metrics()`
is documented as "the *only* place [in the app layer] that issues the headline `COUNT(*)`
queries", persisted in `market_stat_snapshot`, computed once per refresh. The many other
`g.service.market_statistics()` call sites (`main.py:702,722,846,868,932,981,1187,1283`)
read the same stored row.

On the **local copy** (a bounded 1,261-project ingest) the snapshot reads
`public_projects 133 · with_mechanical 8 · permit_records 236 · cities 15`. That is a *smaller
dataset*, not a disagreement. The 175/253/11,966 numbers in the task brief came from the
2026-10-09 live dataset; the site has since collected more (741/59,521 live now).

**Conclusion:** (a) is **not currently a bug** — the numbers are internally consistent and snapshot-backed.

### (b) Trends vs home record count
Local home `permit_records` = **236**; local `/trends` "Permit records" (windowed, trade-linked)
= **164**. These are two different, deliberately-defined metrics: home's "Permit records" is the
all-time canonical count of permit rows on public projects; Trends' is permits **inside the
selected window** that are **linked to a project of this trade** (`trends.py:411-419`,
`JOIN project_permit`). The templates state the definition beside each number
(`trends.html` basis lines). Not a bug; the definitions differ by design.

### (c) "Projects with a detected change" > new projects
`changed = 0`, `new commercial projects = 132`, `public = 133` on the local snapshot. The
definitions:
- "detected change" = `COUNT(DISTINCT pc.project_id) FROM project_change WHERE change_kind <> 'new_project'` (`trends.py:499-505`). A first appearance (`new_project`) is **excluded** — so it cannot count the whole dataset.
- The original 533 figure predates the `new_project` exclusion and the snapshot rework. Today the metric is 0 locally because a single ingest pass produces only `new_project` rows and no re-observation changes. **Conclusion: the historical 533>301 was a real bug (counting first appearances as changes); it is fixed.** On the live site the value is 64 (lower than new-projects 620), consistent with the fix.

### (d) Mechanical-evidence numbers
- Home "Projects with mechanical evidence" = `with_mechanical` = public projects with a Tier1/2 evidence clause (`stat_snapshot.py:97-104`) — local **8**, live **141**.
- Trends "Projects with strong mechanical evidence" = distinct projects with `mechanical_evidence_tier IN (1,2)` **dated inside the window** (`trends.py:577-586`) — local **8**.
- Trends "Projects with classified trade scope" = distinct projects with a `project_trade` row dated in window (`trends.py:454-461`) — local **96**.
All four words are defined once and rendered next to their number; they measure different things
(public-mechanical / windowed-mechanical / windowed-trade-classification). Not a bug.

### (e) Future permit dates
```
SELECT COUNT(*) FROM project WHERE permit_date > '2026-10-09';   -- 2
id 1247  permit_date 2026-12-02  Celina  'S PRESTON RD , CELINA, TX 75009'
id  413  permit_date 2026-12-19  Plano   '2200 INDEPENDENCE PKWY , PLANO, TX 75075'
```
Dates are parsed/validated in `src/oppintel/dates.py`; there is no future-date rejection at
ingest. `trends.py` and `coverage.py` **exclude** future dates from metrics (and `quality.py`
flags them), so the number is bounded but the underlying rows persist. **Root cause: no
sanity bound on parsed permit dates.** Two rows, both in the local copy.

### (f) Data-quality battery (local copy: 1,261 projects, 4,190 permits, 14,564 evidence rows)
| Metric | Count | % |
|---|---|---|
| null declared value (`estimated_project_value`) | 63 | 5.0% |
| null `square_footage` | 139 | 11.0% |
| null `project_type` | 0 | 0.0% |
| ALL-CAPS title | 1,117 | 88.6% |
| title > 80 chars | 64 | 5.1% |
| projects with zero evidence | 0 | 0.0% |
| orphan permits (not in `project_permit`) | 2,286 | 54.6% |
| future `permit_date` | 2 | 0.16% |
| `permit_date` range | 2025-12-04 → 2026-12-19 | — |
| `created_at` (first observed) range | 2026-10-09T16:48 → 16:49 | — |
| address keys with repeated permits (≤2 months) | 490 | — |

Per-jurisdiction (local): Plano 42, McKinney 29, Fort Worth 26, Dallas 18, Celina 14, Frisco 14,
Allen 12, Prosper 5, Wylie 4, Murphy 3, Richardson 3, Anna 1, Farmersville 1, Melissa 1,
Nevada 1 (*Nevada is out-of-market and excluded from stats — correct), Royse City 1.

### (g) Companies / roles
```
project_party roles: [('owner', 1830)]     # ONLY owners exist
live /companies: class="badge badge-plain">Owner  ×60
live /companies?role=contractor  → 0 results
live /companies?role=architect   → 0 results
```
`project.general_contractor` non-null = **0**, `architect` non-null = **0**, `developer` **0**.
**GC/architect data does not exist.** The page copy ("verified general contractors, owners,
developers, and architects") is **aspirational**, and the role filter offers GC/Architect
options that yield empty sets. The role vocabulary is supported in code
(`companies/index.html:55-66`) but never populated.

### (h) Ingestion pipeline
- **Sources:** 4 enabled (`config/sources.yaml`): Fort Worth ArcGIS, Collin CAD (Socrata),
  Dallas Accela, plus a fourth. Verified, public, login-free; `since_months: 24` applied at source.
- **Schedule:** in-process thread, `REFRESH_SECONDS=21600` (6h); `MAX_PAGES=3`; `GROW_BACKFILL=0` on preview (`render.yaml`).
- **Idempotency:** connectors are keyed (`natural_key`) and insert with dedupe; `evidence_history` is append-only and `event_uid` uses `INSERT OR IGNORE`.
- **Error handling:** per-source retries (`max_retries: 3`), a single-writer lock (`locks.py`), refresh errors logged and retried on the next tick (`automate.py:237-245`).
- **Raw archival:** every fetch lands `data/raw/*.jsonl` (13 files present).
- **Classification Tier 1/Tier 2:** Tier 1 = official trade permit (mechanical/HVAC permit type), Tier 2 = documented scope text; resolved via `config/trades.yaml` `evidence_clause`/`strong_evidence_clause` and `classify.py`. Permit **type** is consulted before description (AGENTS.md rule).
- **Tests:** `test_classify.py`, `test_assemble.py`, `test_normalize.py`, `test_dallas_*.py` (5 files), `test_source_window.py` — yes, covered.

---

## 5. Screenshot index

54 screenshots written to `audit/screenshots/` (18 routes × 3 viewports: 390×844, 820×1180,
1440×900). Problem note per route (blank = no problem detected this run):

| Route | Note |
|---|---|
| `/` | Clean; 21 axe colour-contrast *incomplete* nodes to eyeball |
| `/opportunities` | Clean; server-side pagination; 61 contrast-incomplete |
| `/opportunities/new` | Clean; `noindex,nofollow` present |
| `/markets`, `/markets/dfw` | Clean |
| `/companies` | Clean layout; **only Owner badges**; GC/Architect filters empty (product gap) |
| `/changes` | Clean |
| `/trends` | Clean; **no chart on live**; canonical → `/` (defect) |
| `/analytics` | Clean; 30 inline styles |
| `/reports` | Clean; canonical → `/` |
| `/how-it-works` | Clean |
| `/trades`, `/trades/commercial-hvac` | Clean; **title "Commercial Commercial HVAC…"** |
| `/commercial-construction-leads`, `/guides` | Clean; `/guides` canonical → `/` |
| `/signin`, `/signup` | Clean |
| `/this-does-not-exist` | **Missing `<h1>`** |

Layout detection: `documentElement.scrollWidth == innerWidth` on **all 54** captures → **no
horizontal overflow**. The `.mobile-drawer` flagged at x≈725 is an off-canvas element (the page
does not scroll), a false positive.

---

## 6. Feature claimed-vs-built matrix

| Feature | Claimed | Status | Evidence |
|---|---|---|---|
| Opportunity directory / search | Yes | **Implemented** | `main.py:536` `/opportunities`, `service.py` |
| Search synonyms/abbreviations | — | **Implemented** | `search_vocabulary.yaml`, `search_index.py` |
| Project dossier | Yes | **Implemented** | `/opportunities/<slug>` |
| Trend Radar | Yes | **Implemented** | `/trends`, `trends.py` |
| Trend charts | (implied by earlier work) | **Partial** | inline SVG on feature branch only; **live = none** |
| Market changes | Yes | **Implemented** | `/changes`, `changes.py` |
| Change alerts (in-app) | Yes | **Implemented** | `/alerts`, `alerts.py` |
| Email alerts | — | **Missing** | `alerts.py:12` — modelled, never sent |
| Watchlist ("watching") | Yes | **Implemented** | `/watching`, `/watching/<id>` |
| Pursuit pipeline | Yes | **Implemented** | `/my-pipeline`, `/pipeline/<id>` |
| Notes | Yes | **Implemented** | `/notes/<id>` |
| Tags | Yes | **Implemented** | `/tags/<id>` |
| Saved projects | Yes | **Implemented** | `/saved`, `/saved/<id>` |
| Saved **searches** | implied | **Missing** | no `saved_search` anywhere |
| Reports | Yes | **Implemented** | `/reports`, `report_generator.py` |
| CSV/PDF/Excel export | — | **Missing** | no export code |
| Analytics funnel | Yes | **Implemented** | `/analytics`, `analytics_funnel.py` |
| Companies directory | Yes | **Partial** | `/companies`; owners only, GC/architect empty |
| LinkedIn content drafts | — | **Implemented** (ADMIN) | `/content/linkedin`, `linkedin.py` |
| Pricing / plans / upgrade | — | **Missing** | no route; `set-plan` CLI only (`main.py:2460`) |
| Email verification | — | **Missing** | no flow in `accounts.py` |
| Password reset | — | **Implemented** | `mailer.py`, `accounts.py` |

---

## 7. Competitor gap table (Dodge One / ConstructConnect / BidClerk)

Internet access available; sources cited in the Appendix. Patterns described only.

| Dimension | Dodge One | ConstructConnect | BuildScope | Gap |
|---|---|---|---|---|
| Navigation | Search-first; adaptive menu | Product modules + Watch List/search icons global | Clear top nav; workflow links | Competitive |
| Homepage | Search hero; provenance as value | Benefits + demo/pricing CTAs | Evidence-first hero + quiet stat strip | Missing pricing CTA |
| Search/filter | Radius/county; applied-filters panel | Project↔company filters; CSI codes; value sliders | City/type/classification/freshness; NL interpretation | Fewer domain filters |
| Result density | Compact / List / Map modes | Table + sliders | Card grid, server-paginated | No table/map, no compare |
| Detail page | Dodge Report: fields→contacts→plans/specs | Bids, attachments, document viewer | Dossier + evidence chain | No plans/specs |
| Pricing/gating | Tiered + region add-ons; paid packages | Public Starter/Professional; Vetted Leads add-on | **None** | **Major** |
| Typography/colour | Not disclosed | Brand palette (dark/regatta/light blue + orange) | Single system font; navy+amber tokens | Fine |
| Trust signals | 100+ yrs, field verification | Research team, patents, partners | Evidence links, definitions, "Not verified" | Strong but different |
| Export | Up to 10k records; PDF | CSV/PDF/Excel | **None** | **Major** |
| Mobile | Native app, push | Responsive web | Responsive web; no app | Minor |

**The two structural gaps vs the class are monetization surface and bulk export.**

*Sources: construction.com/dodge-one, construction.com/helpcenter (filters, search, export),
constructconnect.com/en/products/project-intelligence, constructconnect.com/pricing,
constructconnect.com/app-pricing-lp, ConstructConnect Watch List help.*

---

## 8. Top 15 quick wins (< 1 day each)

1. **Fix canonicals** (S1) — pass the real path in `main.py` calls to `seo.py:simple()`. Highest SEO ROI.
2. **Fix doubled trade title** (S2) — dedupe the "Commercial" prefix.
3. **Add `<h1>` to 404** (A1).
4. **Add HSTS header** (SEC1) in `security.py`.
5. **Add `/pricing` page** (P1) — even a static plan table beats none.
6. **CSV export** on `/opportunities` (P2), gated later.
7. **Pad footer link tap targets** to ≥44px (M1).
8. **Future-date guard** (D1) — drop or flag `permit_date > today` at ingest.
9. **Broaden `/companies`** role options to only roles with data (avoid empty sets).
10. **Home page copy** to state coverage honestly (thin dataset).
11. **Sitemap lastmod** + confirm indexability after the canonical fix.
12. **Keep-warm ping** for the Free preview (O3).
13. **CI workflow** running `pytest` (O2).
14. **Schedule raw-payload retention** (O4).
15. **Migrate top-3 inline-style templates** (U1: `opportunities/detail.html`, `analytics.html`, `trends.html`).

---

## 9. Proposed 4-week roadmap (risk/impact ordered)

- **Week 1 — SEO & trust:** canonicals, titles, 404 h1, HSTS, sitemap lastmod, pricing page stub. (Low risk, high reach.)
- **Week 2 — Monetization & export:** `/pricing`, entitlement gating, CSV export, contacts gating. (High impact on "professional SaaS" perception.)
- **Week 3 — Data breadth:** extract GC/architect/contractor parties, expand sources beyond 4, reduce the 54.6% permit drop in assembly, future-date guard.
- **Week 4 — Ops & polish:** CI + backups + error monitoring, paid plan + disk, inline-style cleanup (K8), trend-chart deploy, mobile/contrast manual pass.

---

## 10. Open questions for the founder

1. Is the deployment target the **Free preview** or a paid plan with a disk? The whole "data resets on every deploy" story depends on this.
2. Is **monetization** intended for this phase, or is the site still a demo? There is no pricing/plan UI at all.
3. Should GC/architect data be extracted from the source `contractor`/Accela party fields, or is "owners only" acceptable for now?
4. Is the 54.6% permit drop in assembly expected (non-commercial) or a linkage defect?
5. Should the audit artifacts (`audit/*.py`, `*.json`, `screenshots/`) be committed, or kept out of the repo? I created them but committed nothing.
6. Was the K5 chart meant to be on `main` (live) yet? It is only on `wp3-visual`.

---

## 11. Appendix

### Command log (abridged)
```
git branch -vv; git --no-pager log --oneline -8; git rev-parse --is-shallow-repository   # true; 42 commits
curl -w 'ttfb=%{time_starttransfer}' https://buildscope-xppz.onrender.com/{,api/statistics,trends,opportunities}
sqlite3(ro) queries: classification/trade/permit_date/future/quality/roles/party/funnel  # see §4
grep -c '!important' app.css   # 8
grep -ro 'style="' templates | wc -l   # 349 across 34 templates
grep -oE '#[0-9a-fA-F]{3,6}' app.css | sort | uniq -c   # navy/slate/amber system
python audit/ui_probe.py        # 54 screenshots, no overflow
python audit/axe_probe.py       # 0 WCAG 2.1 AA violations on 16 routes
curl -D - .../ | grep -i strict-transport   # 0  (no HSTS)
curl -X POST /signin (no CSRF token)        # 403
env -u BASE_URL … pytest tests/ -q          # 1246 passed in 254.73s
```

### Versions
Python 3.13.15 · gunicorn 26.2.0 · Flask/Jinja2/Werkzeug/YAML/requests vendored under
`vendor/python` · axe-core 4.9.1 · Playwright 1.63.0 (chromium-1243) · Node 24.21.0.

### Raw outputs saved
`audit/ui_probe_results.json` (overflow + tap targets) ·
`audit/axe_results.json` (per-route violations) · `audit/screenshots/*.png` (54).

### Not verified / blocked
- **Full git-history secret scan:** the clone is shallow (`is-shallow-repository=true`, 42 commits). A complete history scan requires `git fetch --unshallow`. No secrets found in the tracked tree or the 42 available commits.
- **Lighthouse numeric scores:** not run (would fabricate). The homepage has **0 JS** and **1 CSS** request (8.7KB gzip), so LCP/CLS/TBT are structurally near-ideal; TTFB measured directly (~180ms warm, 1.2s cold).
- **Monetization/plan behaviour:** UNVERIFIED against a deployed paid instance — none exists.
- **Local dataset** is a bounded 1,261-project sample, not the live 741-public/6,682-total set; per-jurisdiction and quality percentages describe the sample.
