# Current-Source Participant Discovery — Phase 10

**Date:** 2026-10-16
**Phase:** BuildScope 2.0 Phase 10 (source research and controlled validation)
**Repository state:** branch `main`, commit `4344ace` (shallow/grafted). Working tree carries the
uncommitted Phase 9 company-layer changes (unchanged; see §10).
**Prior phase:** `audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md` (NO-GO on the two
disabled Dallas connectors).

**Decision: C — NO-GO on operator-accessible Accela participant data, with a qualified
partial for TDLR licence corroboration.** No connector was activated. No production data was
written. No event or alert was emitted.

---

## 1. Executive summary

The objective was to find a reliable, authorised source of project participants, starting with
operator-accessible Accela detail pages, and — only if justified — propose a minimal
implementation.

Four things were established by direct measurement:

1. **The anonymous Accela detail page exposes no participant.** Across 15 sampled pages, 0
   contained an applicant, contractor, licensed professional, architect, engineer, licence
   number, or any contact block. The string "Additional Licensed Professionals" is present in
   the page's JavaScript, but the section is **not rendered** for anonymous users and has no
   corresponding table. The detail page's tab strip contains record detail, fee, inspections,
   attachments, education, examination, custom component, related records, trust account and
   right-of-way — **no contacts tab**.
2. **One participant-ish field exists, and it is not a contractor.** The Application
   Information section carries `Business Name:`, the applicant's self-declared business. It is
   populated on **3/40 pages (7.5%)** — the only populated participant field found — and its
   values (`Riverfront Residential II LP`, `Danik Holdings LLC`, `DISD VICTOR HEXTOR ES`) name
   the *owner / applicant entity*, not a contractor. It carries no role label and no licence.
3. **Operator access is unavailable, so the authenticated path is unverified — not disproven.**
   No Accela credentials are configured in this environment (checked by presence only:
   `ACCELA_USER`, `ACCELA_USERNAME`, `ACCELA_PASSWORD`, `DALLAS_ACCELA_USER`,
   `DALLAS_ACCELA_PASSWORD` — all unset), and the repository contains no Accela login code.
   Whether an authenticated view exposes a labelled contractor is therefore **not
   determinable here**. Per the task instruction, this is stated explicitly rather than
   claimed as "the source contains nothing".
4. **TDLR is a real, authorised corroboration source — for identity, not for project linkage.**
   The Texas Department of Licensing and Regulation publishes a public "Air Conditioning
   Contractors" registry (20,460 records, queryable by name, with licence number, status,
   type and dates). A controlled experiment corroborated **1/15** sampled Dallas contractor
   names, and that single hit was a **surname-token false positive** — so the honest measured
   identity-match rate is **0/15**. Critically, **a registry never names a project**, so it
   cannot establish a project-specific relationship at all.

**Recommendation: NO-GO on the Accela operator path as currently accessible.** The next
defensible step is an operator-authorised check of the *authenticated* Accela detail view
(requires credentials this environment does not hold), or a current source that publishes a
role-labelled participant column. TDLR should be kept as an identity-corroboration input only,
never as a project relationship.

---

## 2. Baseline (verified)

| Item | Value |
|---|---|
| Branch | `main` |
| Commit | `4344ace` (shallow clone, `is-shallow-repository=true`) |
| Working tree | 6 modified files + 2 untracked audit docs — preserved, not touched |
| Prior report | `audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md` — read |
| Disabled connectors | `dallas_gis_permits`, `dallas_permits_socrata` — **still `enabled: false`** |

**Environment note (verified, not a repository defect):** partway through this phase the
sandbox's Python environment was reset — `site-packages` lost PyYAML/Flask/pytest and the
documented `vendor/python` directory was absent. Dependencies were reinstalled from the
repository's own `requirements.txt` (`requests`, `PyYAML`, `Flask`, `gunicorn`) plus `pytest`,
per the AGENTS.md "Build" instruction. No repository file was changed by this.

---

## 3. Accela detail-page investigation

**Source and access method.** `dallas_accela_permits` (`config/sources.yaml:137+`), `enabled:
true`, `market_coverage: current`, `since_months: 24` → `resolved_since=2024-10-16`. Access is
the connector's own documented flow: `GET` the search form, `POST` the search
(`ctl00$PlaceHolderMain$btnNewSearch`), then `GET` each `CapDetail.aspx` URL from
`detail_path`. **No authentication was bypassed**; no CAPTCHA, rate limit or access control was
circumvented. All requests used the connector's own 1-second rate limiter.

**Fields verified on the anonymous detail page** (the task's target list):

| Target field | Present anonymously? | Evidence |
|---|---|---|
| Applicant / permit holder | **No** | 0/15 pages contained "Applicant" |
| Contractor / general contractor | **No** | 0/15 pages contained "Contractor" |
| Architect / engineer | **No** | 0/15 pages contained "Architect"/"Engineer" |
| Trade / specialist contractor | **No** | — |
| Licence / registration number | **No** | 0/15 pages had a licence-number field |
| Company contact / registry ref | **No** | — |
| Explicit role labels | **No** | only `Business Name:` (unlabelled by role) |
| Source timestamps | Yes (permit dates) | `ISSUE_DATE`/status only; not participant timestamps |

**What the page *does* expose:** Work Location, Project Description, Job Value, Housing Units,
Number of Buildings, Public Owned, Construction Type, Application Information (Type of Work,
phased-project flag, `Business Name`, areas, costs, land use), Parcel Information, Plan Review,
Related Records, and a Right-Of-Way Management panel. None of these is a participant.

**The "Additional Licensed Professionals" red herring.** The phrase appears only inside an
inline JavaScript block (`showALP`/`hideALP` toggles that operate on `<tr tips="...">` rows).
The probe found **zero** rows carrying a `tips` attribute on the sampled pages, and no table
with a licence-professional id. So the toggle has nothing to reveal for an anonymous visitor.

**Sample methodology.** The connector's result counter saturates at the literal "100+"
(`dallas_accela_permits.py:68`), so records were sampled across five month windows
(Jan/Feb/Apr/Jul/Oct 2024) and across permit types (Commercial New Construction, Alteration
Addition, Mechanical, Electrical, Plumbing, Roofing, Accessory, Demolition, SolarPV, PoolSpa).
15 pages were used for the label sweep, 40 for the `Business Name` fill rate, and 6 mechanical
pages specifically (mechanical permits are where a trade contractor would most plausibly be
the applicant) — mechanical pages also showed no participant.

**Reproducibility.** The flow is deterministic and unauthenticated; the findings above
reproduce on any run. **Consistency:** uniformly absent across all sampled types and months.

---

## 4. Registry corroboration (TDLR)

**Source and access method.** Texas Department of Licensing and Regulation, *Active License
Search* — `https://www.tdlr.texas.gov/LicenseSearch/licenseSearch.asp`. Public, unauthenticated,
no CAPTCHA encountered. Form fields read from the page: `tdlr_status` (license type),
`pht_oth_name` (name), `phy_city`, `phy_cnty`, `phy_zip`; results at
`SearchResultsListBrowse.asp`; detail at `SearchResultDetail.asp?1=<type><seq>`.

**Coverage (measured).** The registry offers construction-relevant types including
`AIRREF` (Air Conditioning Contractors), `ACTECH` (A/C Technicians), `ELCTRC` (Electricians),
`ELECTR` (Elevator Contractors), `MRCNTR` (Mold Remediation Contractor). The `AIRREF` type
reported **20,460 records** on the probed query (the page retrieves the first 10,000 and says
so). **It does not cover general contractors** — Texas does not license GCs; and it does not
cover plumbing (plumbing is licensed by the Texas State Board of Plumbing Examiners, a
different agency not probed here).

**Attributes per licence (verified).** `SearchResultDetail.asp` returns License #, Expiration
Date, Type, Status, and Original License Issue Date. The list view returns License#, Exp Date,
Name, City, Zip, County, Phone. The name field frequently carries a person *and* a company in
parentheses, e.g. `GRAVES, LAWRENCE CLINTON (GRACO MECHANICAL INC)`.

**Claim separation (as required):**

- A TDLR row can corroborate **company identity** and a **licence** for a named HVAC
  contractor. ✅ available.
- A TDLR row **cannot** establish that a company works on a specific project. ❌ no project
  reference exists in the registry.
- A TDLR row **cannot** establish a general-contractor role. ❌ the role it evidences is
  "licensed A/C contractor", not GC.

---

## 5. Controlled matching experiment

**Isolation.** The experiment read the live GIS API and the live TDLR site only. It used **no
database at all** (not even a temp one) — no production write, no raw capture, no event, no
alert. It is a pure read-and-compare.

**Method.** 60 live `COMMERCIAL='Y'` GIS records were sampled (60 had a `CONTRACTOR_NAME`; 39
distinct names). Each distinct name was queried against the TDLR `AIRREF` registry using its
last significant token, and a hit was accepted as corroboration only if that token appeared in
the TDLR name field.

**Measured results:**

| Measure | Result |
|---|---|
| Records examined (GIS) | 60 |
| Records with a participant name | 60 (100% — but see role caveat) |
| Records with an **explicit role label** | **0** — the column is `CONTRACTOR_NAME`, no role |
| Records with a **licence identifier** | **0** — the layer has no licence column |
| Distinct names tested against TDLR | 15 |
| Registry **identity matches** (token-verified) | **0/15** |
| Raw token hits (before verification) | several — all surname collisions |
| **Project matches supported by project-level evidence** | **0** (a registry names no project) |
| Ambiguous matches | all non-zero hits (e.g. `JAMES R THOMPSON, INC` → `THOMPSON A/C & HEATING INC` is a different company) |
| Rejected matches | 15/15 |

**Illustrative false positive.** `JAMES R THOMPSON, INC` returned
`TACLB00002178C — THOMPSON, STEVEN RAY (THOMPSON A/C & HEATING INC DBA)`. The shared token is
`THOMPSON`; the companies are different. This is exactly the "do not turn inferred matches into
verified relationships" hazard, reproduced.

**Conclusion.** TDLR cannot supply project-level participant evidence, and its identity-match
yield against Dallas permit names is ~0 at this precision. It is not a path to project
relationships.

---

## 6. Historical-ingestion defects (recorded separately, not repaired)

These are independently verified and are recorded for a **separate** bounded fix. Nothing in
the event pipeline was modified during this phase.

1. **`since` is silently ignored by `dallas_gis_permits`.** `fetch_raw(self, since=None, ...)`
   never references `since`; the ArcGIS query is hard-coded `"where": "1=1"`
   (`connectors/dallas_permits.py:47-53`). A caller passing a bound gets the whole layer.
   `dallas_permits_socrata` *does* build `$where` when `since is not None`
   (`:139-140`), but the pipeline never passes it because the source has no `since_months`
   (verified: `resolved_since=None`). So both are effectively unbounded.
2. **No connector-specific tests.** No test module instantiates `DallasGisPermitsConnector` or
   `DallasPermitsSocrataConnector`; `tests/test_dallas_*.py` cover the Accela connector and
   classification only. Their normalizers are unverified.
3. **Historical rows can raise current-time `new_permit` alerts.** Reproduced in Phase 9's
   probe and recorded in the prior report: attaching a historical permit to an existing project
   emits an alertable `new_permit` change stamped at observation time (`new_permit` is in
   `CHANGE_TO_ALERT`, `app/alerts.py:37`).
4. **No attribution-only containment primitive exists.** `market_coverage` gates reporting
   only (`reporting.py:108`); nothing gates classification, changes, events or alerts by
   coverage. There is no ingestion mode that contributes attribution while suppressing
   projects/changes/alerts.

**Recommended separate fix (bounded, with acceptance criteria):**

- *Scope:* implement `since` in `DallasGisPermitsConnector.fetch_raw` (ArcGIS `where` on
  `ISSUE_DATE`) and confirm the Socrata `$where` is reachable via `since_months`; add connector
  tests over captured payloads; add a per-source flag that suppresses project creation, change
  rows, events and alerts while still landing permits and parties.
- *Acceptance:* (a) `fetch_raw(since=...)` returns only rows at/after the bound, proven by a
  test; (b) an attribution-only source produces 0 `project_change` rows, 0 events and 0 alerts
  on a database that already holds a matching project; (c) the full suite stays green;
  (d) `integrity_report()` stays healthy and `alerts_without_event()` stays empty.
- *This is a separate approval; it is not part of Phase 10.*

---

## 7. Decision gate

**C — NO-GO** for the operator-accessible Accela participant path, with a **partial** note for
TDLR.

| Question | Answer | Strength |
|---|---|---|
| Does the anonymous Accela detail page expose participants? | No — 0/15 pages | Verified |
| Does the authenticated view expose participants? | **Unverified** — no credentials available | Not determinable here |
| Is there any project-level participant evidence on the public page? | No | Verified |
| Can TDLR establish a project relationship? | No — a registry names no project | Verified (by source design) |
| Can TDLR corroborate a company identity / licence? | Yes, in principle | Verified (registry exists, fields readable) |
| Does TDLR cover general contractors? | No — Texas does not license GCs | Verified |

Because the only *unverified* avenue is the authenticated Accela view, and that requires
credentials this environment does not hold, the correct outcome is NO-GO **with an explicit
statement that access was unavailable**, not a claim that the source contains no participant
data.

### Next defensible source-discovery targets (priority order)

1. **Operator-authorised authenticated Accela check.** If an operator can supply credentials
   (or export an authenticated detail page), re-run §3 against the logged-in view. This is the
   single highest-value unknown. It must be authorised by the operator and must not bypass any
   access control.
2. **A current jurisdiction that publishes a role-labelled participant column.** The search
   criterion is a column *named for a role* (e.g. "Contractor", "Applicant", "Builder") in a
   **current** feed — not a bare name field.
3. **Trade-licence registries as an identity layer only** (TDLR for HVAC/electrical; TSBPE for
   plumbing). Useful to *verify* a company already linked to a project by project-level
   evidence — never to *create* the link.

---

## 8. Coverage estimates (with denominators)

| Estimate | Value | Denominator |
|---|---|---|
| Accela detail pages with any participant label | 0 | 15 |
| Accela detail pages with a licence-number field | 0 | 15 |
| Accela detail pages with populated `Business Name` | 3 (7.5%) | 40 |
| Mechanical-permit pages with a participant | 0 | 6 |
| GIS records with a name | 60 (100%) | 60 |
| GIS records with an explicit role label | 0 | 60 |
| GIS records with a licence id | 0 | 60 |
| TDLR identity matches against Dallas names | 0 | 15 |
| TDLR `AIRREF` registry size | 20,460 | 1 query |

**Evidence strength:** high for the "absent on anonymous pages" findings (uniform across types
and months, reproducible). **Low** for any inference about the authenticated view — that is
explicitly unverified.

---

## 9. Limitations

- **No operator credentials**, so the authenticated Accela view could not be inspected. This is
  the principal limitation.
- The Accela result counter saturates at "100+", so samples are windows, not exhaustive.
- Only the `AIRREF` TDLR type was queried; other construction types were listed but not
  exercised. The Texas plumbing board was not probed.
- TDLR's list view truncates at 10,000 rows for a broad query; name-filtered queries are exact
  enough for the experiment performed.
- Sample sizes are small (15–60); they are sufficient to establish *absence of a rendered
  participant section*, not to estimate a population fill rate to high precision.

---

## 10. Files inspected / changed / working tree

**Inspected (read-only):** `config/sources.yaml`,
`src/oppintel/connectors/dallas_accela_permits.py`, `connectors/dallas_permits.py`,
`connectors/base.py`, `config.py`, `app/alerts.py`, `reporting.py`,
`audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md`.

**Changed:** none in product code. This report is the only new artefact.

**Scratch files:** seven throwaway probe scripts were created and **deleted**;
`git status` is unchanged from the start of the phase.

```
 M AGENTS.md
 M src/oppintel/app/templates/companies/detail.html
 M src/oppintel/companies.py
 M src/oppintel/db.py
 M tests/test_ai_analyst_app.py
 M tests/test_companies.py
?? audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md
?? audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md
```

**Non-writes verified:** production `data/oppintel.db` still has 0 rows in `permit`, `project`,
`project_change`, `evidence`; no `data/raw/*.jsonl` capture was written; no connector was
enabled; no credential value was printed or recorded.

---

## 11. Test / runtime baseline

```
$ PYTHONPATH=src python -m pytest tests/ -q
1215 passed, 1 skipped in 140.69s (0:02:20)
```

No failures, no errors. The 1 skip is pre-existing.

## 12. Constraints honoured

- No connector activation; both disabled sources remain `enabled: false`.
- No production data writes, no production deployment.
- No fabricated participant roles; no inferred match was promoted to a verified relationship.
- No architecture change; no unrelated UI, pricing, export or auth work.
- No secrets exposed; only credential *presence* was reported.
- All probing was authorised, read-only, rate-limited, and did not bypass any access control.

**Awaiting approval before any implementation or connector change.**
