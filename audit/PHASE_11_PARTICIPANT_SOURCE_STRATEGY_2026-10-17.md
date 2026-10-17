# Phase 11 — Current Participant Source Strategy

**Date:** 2026-10-17
**Phase:** BuildScope 2.0 Phase 11 (source research and qualification only)
**Repository state:** branch `main`, commit `4344ace`. Working tree carries the uncommitted Phase 9
company-layer changes (preserved, untouched).
**Prior reports read:** `audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md`,
`audit/CURRENT_PARTICIPANT_SOURCE_DISCOVERY_2026-10-16.md`.

**Decision: C — no source qualifies for immediate controlled integration.** The most valuable next
investigation is a two-track one: (1) an operator-authorised authenticated Accela check, and
(2) a DFW municipal survey using Chicago's role-labelled field pattern as the template.
**No connector was implemented, enabled, or changed. No schema, production data, or source was
touched.**

---

## 1. Executive summary

The objective was to find a **current, authorised** source that establishes project-to-company
relationships with **explicit participant roles**, and to qualify it on a scorecard.

Eleven candidate sources were examined. Four findings decide the outcome:

1. **A Texas municipal source does publish a role-labelled contractor field — but it is out of
   market.** The City of Austin's *Issued Construction Permits* dataset (`3syk-w9eu`) carries
   `contractor_trade` (`General Contractor`, `Electrical Contractor`, `Mechanical Contractor`,
   `Plumbing Contractor`) **and** `contractor_company_name`, is current to 2026-10-16, and holds
   2.38 M rows. This is the first source in three phases with an explicit role label attached to a
   company. It proves the field type exists in Texas municipal open data.
2. **Austin's "contractor" is the applicant's self-declared company, not an independent
   contractor.** Measured across the whole dataset: `contractor_company_name` equals
   `applicant_org` in **78,575 of 78,575** commercial building permits (0 divergent). The
   publisher's own field description says "Company name of the primary contractor associated with
   the permit", but the value is the applicant's declared organisation. It is a *declared* role,
   not a *corroborated* one — and Austin is not in the configured DFW market.
3. **The best role-labelled model is Chicago, and it is out of market.** Chicago's Building
   Permits carries 15 `contact_N_type` + `contact_N_name` pairs with explicit role vocabularies —
   `CONTRACTOR-GENERAL CONTRACTOR` (85,901), `CONTRACTOR-ELECTRICAL` (29,847), `ARCHITECT`
   (35,196), `STRUCTURAL ENGINEER` (10,845), `OWNER` (136,799). This is the pattern BuildScope
   needs; no DFW equivalent was found.
4. **The one in-market source with an explicit participant role is small and planning-stage.**
   The City of Allen's *Current Development Projects* layer has a literal `Developer` field, and
   Allen is a configured DFW city. But the layer holds **83 records total**, only **22** with a
   developer, and it is a development-application tracker, not a permit feed.

No DFW permit source — enabled or candidate — publishes a role-labelled participant column today.
Fort Worth exposes only `Owner_Full_Name`; Collin CAD's `permitbuildername` mixes builder brand
codes and is largely residential; Irving publishes no participant fields; Dallas's current sources
are historical/stale and disabled; Frisco's layer was not reachable; Houston publishes no permit
feed.

**Recommendation: C.** Do not integrate a participant source yet. The highest-value next
investigation is the **authenticated Accela view** (the only *unverified* avenue, requiring
operator credentials this environment does not hold) **plus** a targeted DFW municipal survey
modelled on Chicago's field pattern.

---

## 2. Baseline (verified)

| Item | Value | How verified |
|---|---|---|
| Branch | `main` | `git rev-parse --abbrev-ref HEAD` |
| Commit | `4344ace` | `git rev-parse --short HEAD` |
| Working tree | 6 modified + 3 untracked audit docs (preserved) | `git status --short` |
| Enabled sources | `fort_worth_permits`, `collin_cad_permits`, `dallas_accela_permits` | `load_sources()` |
| Disabled sources | `dallas_gis_permits`, `dallas_permits_socrata` (still `enabled: false`) | `load_sources()` |
| Accela credentials | `ACCELA_USER`, `ACCELA_USERNAME`, `ACCELA_PASSWORD`, `DALLAS_ACCELA_USER` — **all unset** | presence check only |
| Socrata app token | `Socrata_App_Token`, `SOCRATA_APP_TOKEN` — **unset** | presence check only |
| Test suite | `1215 passed, 1 skipped in 142.46s` | `pytest tests/ -q` |

**Environment note (verified, not a repository defect):** the sandbox's Python environment was
reset again at the start of this phase (PyYAML/Flask/pytest missing; documented `vendor/python`
absent). Dependencies were reinstalled from the repository's own `requirements.txt` plus `pytest`.
No repository file was changed by this. The suite was re-run clean afterwards.

---

## 3. Authenticated Accela path

**Status: UNVERIFIED.** No operator credentials are configured in this environment (checked by
presence only — no value was read, printed, or stored). No guessed credentials, undocumented paths,
or access-control bypasses were attempted.

The repository contains **no Accela login code**: a search of
`src/oppintel/connectors/dallas_accela_permits.py` for `login`, `authenticate`, `password`,
`session_cookie`, `LoginName` returns nothing. The connector is an anonymous-citizen-access client
only. Therefore the system today cannot *document* an authenticated participant capability, and
cannot exercise one.

`docs/engineering/dallas_source.md` documents the anonymous mechanism in detail (session-scoped
`__VIEWSTATE`, `Origin`/`Referer` requirement, windowed pager) and lists the commercially relevant
permit types, including `Commercial Mechanical Permit` = `Building/Commercial/Mechanical/NA`. It
does not document an authenticated participant view.

**What an operator could legitimately provide (a request, not an action taken here):**
- confirmation of whether the agency's authenticated view exposes a labelled contractor/applicant
  section, with a redacted screenshot or field list; or
- a licensed Accela API credential (Accela Construct/API), which is a commercial agreement.

This is left UNVERIFIED per the task instruction and does **not** block the wider effort.

---

## 4. Source scorecard

Legend: **V** = VERIFIED, **P** = PARTIALLY VERIFIED, **U** = UNVERIFIED, **R** = REJECTED.
All counts below were measured live during this phase; none is a vendor claim.

### 4.1 Austin — Issued Construction Permits (`3syk-w9eu`) — strongest candidate, out of market

| Criterion | Rating | Evidence |
|---|---|---|
| Official source & access | **V** | Socrata; `https://data.austintexas.gov/resource/3syk-w9eu.json`; public, no auth |
| Currentness & cadence | **V** | max `issue_date` = 2026-10-16; `rowsUpdatedAt` = 2026-10-17; daily |
| Geographic coverage | **V** | City of Austin, TX — **outside the configured DFW market** |
| Participant fields | **V** | `contractor_trade`, `contractor_company_name`, `contractor_full_name`, `applicant_org`, `applicant_full_name` |
| Role semantics | **P** | `contractor_trade` is permit-type-relative (BP→General Contractor; EP→Electrical; MP→Mechanical; PP→Plumbing). But `contractor_company_name` == `applicant_org` in **78,575/78,575** commercial BPs → declared, not corroborated |
| Project identifiers & join | **P** | `permit_number` unique per row; `project_id` is **1:1 with the row** (2,380,707 distinct of 2,380,707) so it is not a group key; `masterpermitnum` **does** group (up to 97 permits) — the real join key |
| Record dates & history | **P** | `applieddate`, `issue_date`, `completed_date`, `statusdate`. **Defect:** 11,668 rows have `issue_date` in 2026 with `applieddate` before 2026 — `issue_date` is overwritten by a re-issue/refresh date on some records |
| Provenance & reproducibility | **V** | Stable resource id; deterministic query API |
| Access terms & cost | **V** | `Public Domain U.S. Government` (`licenseId: USGOV_WORKS`), attribution "City of Austin, Texas"; free |
| Implementation effort | **P** | A Socrata connector exists (`collin_cad_permits` is one) → ~1–2 days, plus market config |
| False-match risk | **P** | `applicant_org` duplication inflates any "contractor" claim; out-of-market |

**Verdict: REJECTED for now — out of market, and its role field is self-declared.** It is the
reference implementation for field shape, not an integration target.

### 4.2 Chicago — Building Permits (`ydr8-5enu`) — best role-labelled model, out of market

| Criterion | Rating | Evidence |
|---|---|---|
| Official source & access | **V** | Socrata; `https://data.cityofchicago.org/resource/ydr8-5enu.json`; public |
| Currentness | **V** | `rowsUpdatedAt` = 2026-10-17; records issued 2026-10-16 |
| Geographic coverage | **V** | City of Chicago, IL — out of market |
| Participant fields | **V** | 15 `contact_N_type` + `contact_N_name` pairs |
| Role semantics | **V** | Explicit vocabulary: `CONTRACTOR-GENERAL CONTRACTOR` 85,901; `ELECTRICAL CONTRACTOR` 261,787; `ARCHITECT` 35,196; `STRUCTURAL ENGINEER` 10,845; `OWNER` 136,799 |
| Join quality | **P** | `permit_` is the join key; no multi-permit project id surfaced |
| Access terms | **P** | `license: See Terms of Use`; attribution "City of Chicago" |
| False-match risk | **P** | `OWNER AS GENERAL CONTRACTOR` (59,497) is a self-performed owner, not a GC firm |
| Effort | **P** | Socrata connector, ~1–2 days |

**Verdict: KEEP AS TEMPLATE (out of market).** Chicago demonstrates the exact role-labelled shape
BuildScope should require of a DFW source. It is not an integration target because it is not DFW.

### 4.3 Allen — Current Development Projects (ArcGIS FeatureServer) — in-market, explicit role, tiny

| Criterion | Rating | Evidence |
|---|---|---|
| Official source & access | **V** | `https://gismaps.cityofallen.org/arcgis/rest/services/CommunityDevelopment/Current_Development_Projects/FeatureServer/0`; public |
| Geographic coverage | **V** | Allen, TX — **a configured DFW city** (`config/markets.yaml`) |
| Participant fields | **V** | `Developer`, plus `ProjectManager`, `ProjectManagerEmail/Phone`, `DeveloperWebsite` |
| Role semantics | **P** | `Developer` is an explicit role, but planning-stage (developer), not a construction contractor |
| Volume | **R** | **83 records total; 22 with `Developer`** — far too small for a market layer |
| Join quality | **P** | `OBJECTID`/`GlobalID`; `ProjectLocation` is free text — no permit join |
| Record dates | **V** | `created_date`, `last_edited_date` |
| Access terms | **U** | No explicit licence text on the layer |
| Effort | **P** | ArcGIS connector, ~1 day |

**Verdict: INVESTIGATE (bounded).** Real, in-market, and role-labelled, but planning-stage and
tiny. Useful later as a *developer-relationship* signal, not as the participant backbone.

### 4.4 Collin CAD Building Permits (`82ee-gbj5`) — enabled, weak role field

| Criterion | Rating | Evidence |
|---|---|---|
| Participant fields | **P** | `permitbuildername` present in 103,977/114,931 rows |
| Role semantics | **R** | Values are builder **brand/model codes**: `LANDON #E4176-D`, `NORMANDY #BELLA-B II`; mostly `Residential` |
| Record dates | **R** | Defects: `2206-08-19`, and future dates like `2026-12-29` |
| Current state | **V** | Connector already sets `contractor=None` with an explicit comment: "The dataset records the permit applicant/builder, not a general contractor" |

**Verdict: REFACTOR (later).** The existing decision to leave `contractor=None` is correct. The
`permitbuildername` field is not a usable GC role.

### 4.5 Other candidates

| Source | Access | Participant role field? | Currentness | Verdict |
|---|---|---|---|---|
| Fort Worth Permits (enabled) | ArcGIS | **No** — only `Owner_Full_Name` (34 fields inspected) | current | **KEEP** (owner-only; no participant to add) |
| Irving Commercial Permits | ArcGIS | **No** — 12 fields, no participant | Feb 2022–present | **REJECTED** |
| Dallas GIS `T_BU_Permits_FY2023_24` | ArcGIS | `CONTRACTOR_NAME` but no role; no licence column | historical (2023-12-29) | **REJECTED** (disabled) |
| Dallas Socrata `e7gq-4sah` | Socrata | none usable | stale (2019-12-31) | **REJECTED** (disabled) |
| Dallas "Active Contractors Map" `ivm2-a2dn` | Socrata | contractor list, no project link | **2017-03-31** | **REJECTED** (stale) |
| Frisco Active Building Permits | ArcGIS | unknown — layer did not return metadata | unknown | **UNVERIFIED** (not reachable) |
| Houston permits | — | none found (no permit feed) | — | **REJECTED** |
| TDLR A/C Contractors registry | web form | licence identity only, **no project link** | current | **KEEP as identity layer only** |

---

## 5. Matching and role safeguards (binding on any future integration)

These are carried forward from the identity/evidence rules and are non-negotiable:

1. **Relationship evidence ≠ identity ≠ licence ≠ role.** A permit row naming a company in a role
   field is *relationship evidence*. A TDLR/registry row is *licence corroboration*. Neither alone
   establishes the other. Each is stored as its own evidence row with its own `source_id`,
   `source_url`, `source_record_key` and excerpt.
2. **A licence record alone does not prove a company worked on a project.** Phase 10 measured
   **0/15** TDLR corroborations against Dallas names, and a registry names no project. Registry
   data may *annotate* a relationship already established by project-level evidence; it may never
   *create* one.
3. **Name similarity is not identity.** Phase 10's `JAMES R THOMPSON, INC` →
   `THOMPSON A/C & HEATING INC` collision is the cautionary case. `identity.py` resolves entities
   deterministically (legal-form folding, `&`=`and`, no fuzzy matching); a false merge is worse
   than a duplicate.
4. **A role label must come from the source, not be inferred.** Austin's `contractor_trade` is
   publisher-supplied; Dallas GIS's `CONTRACTOR_NAME` is not a role and must not be promoted to
   `general_contractor`. The existing `assemble.py` rule — no party row without a sourced role —
   stands.
5. **Ambiguous relationships stay unresolved.** Where the source does not state the role, the
   value is recorded as an explicit unknown (`None` → "Not verified"), never defaulted.
6. **Declared vs corroborated is recorded.** Austin's `contractor_company_name == applicant_org`
   finding means an "applicant-declared company" must not be rendered as a verified GC.

---

## 6. Historical-ingestion defects (carried forward, not repaired)

Recorded from Phase 9/10 for a **separate** bounded reliability phase. Nothing was changed here.

1. **`since` is ignored by `dallas_gis_permits`.** `fetch_raw(self, since=None, ...)` never
   references `since`; the ArcGIS query is hard-coded `"where": "1=1"`
   (`connectors/dallas_permits.py:47-53`). `dallas_permits_socrata` builds `$where` only when
   `since is not None` (`:139-140`), and the pipeline never passes it because the source has no
   `since_months` (`resolved_since=None`, verified).
2. **No connector-specific tests** for either disabled connector; their normalizers are unverified.
3. **Historical rows can raise current-time alertable `new_permit` events** (`new_permit` is in
   `CHANGE_TO_ALERT`, `app/alerts.py:37`).
4. **No attribution-only ingestion mode.** `market_coverage` gates reporting only
   (`reporting.py:108`); nothing gates classification, changes, events or alerts by coverage.

**Recommended separate fix (acceptance criteria):**
- *Scope:* implement `since` in `DallasGisPermitsConnector.fetch_raw` (ArcGIS `where` on
  `ISSUE_DATE`); make the Socrata `$where` reachable via `since_months`; add connector tests over
  captured payloads; add a per-source flag that suppresses project creation, change rows, events
  and alerts while still landing permits.
- *Acceptance:* (a) `fetch_raw(since=...)` returns only rows at/after the bound, proven by a test;
  (b) an attribution-only source yields 0 `project_change`, 0 events, 0 alerts on a DB already
  holding a matching project; (c) the suite stays green; (d) `alerts_without_event()` stays empty.

---

## 7. Decision gate

**Selected: C — no source qualifies for immediate controlled integration.**

| Option | Outcome | Why |
|---|---|---|
| A. Authenticated Accela view available | **Not selectable** | Credentials unavailable → UNVERIFIED, not disproven |
| B. Alternative current source qualifies | **Not selectable** | The role-labelled current sources (Austin, Chicago) are out of market; the in-market role source (Allen) is 83 records and planning-stage |
| **C. No source qualifies** | **SELECTED** | All in-market current permit sources lack a role-labelled participant column |

### Most valuable next investigation, and the evidence needed

**Track 1 — Operator-authorised authenticated Accela check (highest value).**
Evidence needed: a redacted field list or screenshot of the authenticated detail view showing
whether a labelled contractor/applicant section exists. This is the only *unverified* avenue; every
anonymous avenue has now been measured and found empty.

**Track 2 — DFW municipal survey on the Chicago pattern.**
Evidence needed: identify a current DFW jurisdiction whose permit dataset exposes a **role-named**
column (a field literally named for a role — `contractor_type`, `applicant_role`,
`general_contractor`) with a documented vocabulary. Chicago proves the pattern is publishable;
the question is which DFW city publishes it. Candidates to check that were not reachable or not
exhausted in this phase: Frisco (layer metadata did not return), Plano, McKinney, Garland,
Arlington, and the Dallas "Commercial Permits Activity Dashboard" behind `ync5-xnfn`.

**What would flip the decision to B:** a current, in-market source whose role field (i) is
publisher-supplied, (ii) carries a documented vocabulary, and (iii) is *not* merely a copy of the
applicant field. Austin fails (iii) and market; Chicago fails market; Allen fails volume.

---

## 8. Minimal implementation plan (contingent — not approved, not started)

This is a *proposal* for whichever source passes §7. It is written now so approval is cheap, but
**nothing here is implemented.**

**Files likely affected**
- `config/sources.yaml` — one new source block with `since_months`, `market_coverage: current`.
- `src/oppintel/connectors/<source>.py` — one new connector (Socrata or ArcGIS).
- `config/markets.yaml` — add the market only if it is not DFW.
- `src/oppintel/assemble.py` — map the source's role field to `ROLE_*` constants; **no new
  inference**.
- `tests/test_<source>.py` — new connector tests over captured payloads.
- `src/oppintel/constants.py` — only if a new role constant is genuinely required.

**Acceptance criteria**
1. The connector yields `RawPermit` rows with a `contractor` value **only** where the source
   supplies a role-labelled participant; otherwise `None`.
2. A role maps to an existing `ROLE_*` constant; no fuzzy or inferred role assignment.
3. A test asserts that a row whose role field equals the applicant field is **not** recorded as a
   verified general contractor.
4. A test asserts provenance: every party row has `source_id`, `source_url`, `source_record_key`
   and an excerpt.
5. `fetch_raw(since=...)` bounds the download (the Phase 9 defect is not repeated in new code).
6. The full suite stays green; `integrity_report()` healthy; `alerts_without_event()` empty.
7. No new alert is emitted from a backfill; a new-source backfill does not produce current-time
   `new_permit` events.

**Regression protection for the existing intelligence core**
- No change to `service.py`'s boundary, `identity.py`, `evidence_history`, or the change/alert
  pipeline.
- The connector is additive; existing connectors and their `contractor=None` decisions are
  unchanged.
- The source ships `enabled: false` until the acceptance criteria pass.

---

## 9. Files inspected / changed / working tree

**Inspected (read-only):** `config/sources.yaml`, `config/markets.yaml`, `config/trades.yaml`,
`src/oppintel/connectors/{fort_worth_permits,collin_cad_permits,dallas_permits,dallas_accela_permits}.py`,
`src/oppintel/{assemble,constants,db,provenance,reporting}.py`, `src/oppintel/app/alerts.py`,
`docs/engineering/dallas_source.md`, and both prior audit reports.

**Changed:** none in product code. This report is the only new artefact.

**Working tree (unchanged by this phase):**
```
 M AGENTS.md
 M src/oppintel/app/templates/companies/detail.html
 M src/oppintel/companies.py
 M src/oppintel/db.py
 M tests/test_ai_analyst_app.py
 M tests/test_companies.py
?? audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md
?? audit/CURRENT_PARTICIPANT_SOURCE_DISCOVERY_2026-10-16.md
?? audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md
```

**Non-writes verified:** production `data/oppintel.db` untouched; no `data/raw/*.jsonl` capture
written; no source enabled; no credential value read, printed or stored; no schema change.

---

## 10. Limitations

- **No Accela credentials**, so the authenticated view is UNVERIFIED. This is the principal
  limitation and the reason option A cannot be selected.
- Frisco's ArcGIS layer did not return metadata over HTTP or HTTPS, so its fields are UNVERIFIED.
  Plano, McKinney, Garland and Arlington were not exhaustively surveyed.
- Austin's `contractor_company_name == applicant_org` finding is measured over commercial
  building permits (78,575 rows) and spot-checked on EP/MP/PP; a per-record exception may exist.
- Coverage counts are live measurements at the time of the query, not guarantees; datasets change.
- Only TDLR's `AIRREF` type and the sources listed were probed; other trade registries and
  licensed commercial data providers were not evaluated for cost.
- No licensed commercial provider was contacted; their economics are therefore UNVERIFIED.

## 11. Constraints honoured

- No connector implemented, enabled, or changed; no schema change; no production data write; no
  deployment.
- No authentication bypass, guessed credentials, or undocumented access path.
- No fabricated roles, coverage figures, or vendor claims presented as verified.
- Historical-ingestion defects kept separate; not repaired here.
- No secrets exposed; only credential presence reported.
- All probing was authorised, read-only, and rate-limited.

**Awaiting approval before any implementation.**
