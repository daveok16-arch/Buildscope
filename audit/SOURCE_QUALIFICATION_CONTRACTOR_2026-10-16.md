# Contractor Evidence Source Qualification — Dallas

**Date:** 2026-10-16
**Scope:** Qualify (and, only if justified, integrate) a source of evidence-backed
contractor / general-contractor relationships for existing BuildScope projects.
**Repository state:** branch `main`, commit `4344ace`, working tree carrying the uncommitted
Phase 9 company-layer changes (see *Working tree* below).
**Decision: NO-GO.** Do not enable `dallas_gis_permits` or `dallas_permits_socrata` as
ordinary sources. The evidence is stated in this report; no production behaviour was changed.

---

## 1. Executive summary

The task named a source `dallas_permits`. **No source with that id exists.** `dallas_permits.py`
is a *module* holding two connectors whose real ids are `dallas_gis_permits` (ArcGIS,
FY2023-24) and `dallas_permits_socrata` (Socrata, ends 2019). Both are `enabled: false` in
`config/sources.yaml` and both are `market_coverage: historical`.

Qualification found three independent blockers, any one of which is sufficient:

1. **Semantic mislabel.** The field the connector maps to `contractor` is the source's
   *contractor of record* — and Dallas's own data proves that is not a general contractor.
   In a 40-name sample it includes architects (`180 DEGREE ARCHITECTS LLC`,
   `CUMMINGS & ASSOCIATES ARCHITECTS INC`), landscaping (`COMPLETE LANDSCULPTURE OF TEXAS LP`),
   roofing (`KPOST ROOFING, LLC`), seating vendors (`SPECIAL EVENT SEATING , INC.`), and
   individuals (`AASIM TEIR`, `ABDEL HUSSEIN`). The pipeline maps `Permit.contractor` to
   `ROLE_GENERAL_CONTRACTOR` and writes the `general_contractor` field
   (`assemble.py:176-192`), so enabling it would publish **unsupported GC attribution** —
   violating acceptance criteria 1 and 3.
2. **False current-activity signal.** A historical permit that joins an *existing* project
   emits an **alertable** `new_permit` change stamped at observation time ("now"). Reproduced
   in an isolated database: adding one GIS permit to an Accela project produced
   `new_permit @ 2026-10-16T21:31:37` with summary `New permit recorded: HIST-1`, and
   `new_permit` is in `CHANGE_TO_ALERT` (`app/alerts.py:37`). This is a current-activity claim
   about a 2023 permit — violating acceptance criterion 4.
3. **No safe containment primitive.** `market_coverage: historical` gates only *reporting*
   (`reporting.py:108` → `PARTIALLY VERIFIED`). It does **not** gate classification, change
   detection, event generation, or alerting. There is no "attribution-only" ingestion mode
   anywhere in the codebase (grep for `attribution`: no hits). Integrating without one would
   require inventing a new pipeline mode, which exceeds a bounded, reversible change.

A **fourth, positive** finding changes the recommendation: the **enabled** Dallas source
(`dallas_accela_permits`, current) publishes no contractor data in the anonymous grid
(10 columns, none a participant) and **no participant data on the public detail page** either.
The Accela detail page has an "Additional Licensed Professionals" section, but on the six
records probed it contains no applicant, contractor, licence number, or name. So the
highest-value, lowest-risk path is not either disabled historical source at all.

**Recommendation:** NO-GO on both disabled sources. The next defensible step is a *source
discovery* task for a **current** source that publishes a participant field with an explicit
role, or an operator-authenticated Accela detail fetch — not enabling a 2019/2023 feed.

---

## 2. What was verified, and how

All figures below were measured live on 2026-10-16 against the public, unauthenticated APIs,
plus bounded runs on **isolated temp databases**. No production data or external service was
modified. Method: `git` state; `curl` against the source APIs; `Pipeline.ingest_source` +
`assemble_and_classify` on a `tempfile` database with `DATA_DIR` redirected to a temp dir.

### 2.1 The source, and what it provides

| Property | `dallas_gis_permits` | `dallas_permits_socrata` |
|---|---|---|
| Kind | ArcGIS FeatureServer | Socrata |
| `enabled` | `false` | `false` |
| `market_coverage` | `historical` | `stale` |
| Verified max date | `ISSUE_DATE` max **2023-12-29** | `issued_date` max **2019-12-31** |
| Total records | **14,336** (3,630 `COMMERCIAL='Y'`) | **126,840** |
| Contractor fill | **3,199 / 3,630 commercial (88.1%)** | 100% of sampled rows (concatenated) |
| Permit types present | **Building (BU) only** (0 EL/PL/ME/FI/SG/RF) | all trades |
| Contractor field | `CONTRACTOR_NAME` | `contractor` (name + address + phone) |
| Geography | City of Dallas | City of Dallas |

Config evidence: `config/sources.yaml:92-129`. Connector evidence:
`src/oppintel/connectors/dallas_permits.py:112` (`CONTRACTOR_NAME`),
`:198` (`contractor`).

### 2.2 Contractor semantics — the source's actual meaning

`CONTRACTOR_NAME` is the **contractor of record on the permit**. Dallas does not label it as a
general contractor, and the value proves it is not one. Measured distinct-name sample:

```
180 DEGREE ARCHITECTS LLC          <- architect
CUMMINGS & ASSOCIATES ARCHITECTS INC <- architect
COMPLETE LANDSCULPTURE OF TEXAS LP <- landscape contractor
KPOST ROOFING, LLC                 <- roofing
SPECIAL EVENT SEATING , INC.       <- vendor
LANKFORD CONSTRUCTION COMPANY      <- GC (Illinois address)
FRY CONSTRUCTION CO INC            <- GC
AASIM TEIR / ABDEL HUSSEIN         <- individuals
```

Because the GIS layer contains **Building (BU) permits only**, the value is the *building*
permit's contractor — a mix of GCs, subs, architects, vendors and individuals. The Socrata
`contractor` is a different thing again: a single concatenated string
(`"MATT AND PAUL, LLC 6135 VICKERY BLVD, DALLAS, TX 75214 (214) 226-1186"`) holding the
**trade** contractor of record (a plumber for a plumbing permit, etc.), and it also contains
degenerate values such as `", ,   () -"`.

**Conclusion:** neither field is a general contractor. Mapping either to
`ROLE_GENERAL_CONTRACTOR` would be an invented claim. The existing role vocabulary already
contains `ROLE_MECHANICAL_CONTRACTOR` (`constants.py:65`) and `identity.py` already classifies
it as a company (`intelligence.py:50`), so a mechanically-honest role exists — but the source
does not distinguish roles, so even that mapping would be a guess for a mixed-value field.

### 2.3 How the source is disabled, and why

`enabled: false` in `config/sources.yaml`; `SourceConfig.is_current` is
`market_coverage == "current"` (`config.py:55`). `Pipeline.run` ingests only sources whose
config `enabled` is true (`pipeline.py:330`), and `Pipeline.ingest_source` does **not** consult
`enabled` when called directly. The recorded reasons are historical-only coverage
(`sources.yaml:99-102`, `:119-121`), and they are accurate — the date bounds above confirm them.

### 2.4 False-signal analysis (measured)

**Does a historical backfill create false new-opportunity signals?**

- On an **empty** database, ingesting 1 page (1,000 rows) of `dallas_gis_permits` and
  assembling produced **145 projects**, **145 `new_project`** change rows, and 28 projects
  landing at `MEDIUM` (117 `NEEDS_VERIFICATION`). `new_project` is *not* in `CHANGE_TO_ALERT`,
  so it generates no alert — but the projects themselves enter the directory as if newly
  observed.
- On a database that **already holds** a current-source project, adding one historical permit
  at the same address produced an **alertable** `new_permit` stamped at observation time:

  ```
  after pass 2 (historical permit added):
    projects: 1
    permits on the project: 2
      change: new_project      @ 2026-10-16T21:31:37  (pass 1)
      change: evidence_updated @ 2026-10-16T21:31:37  Project name changed...
      change: evidence_updated @ 2026-10-16T21:31:37  Permit number changed...
      change: evidence_updated @ 2026-10-16T21:31:37  Permit date changed...
      change: new_permit       @ 2026-10-16T21:31:37  New permit recorded: HIST-1.
    new_permit alertable? True -> new_permit
  ```

  This is a "new permit filed" alert about a 2023 permit. It reaches any user *watching* that
  project (`app/alerts.py:87-88` joins `watched_opportunity`). That is a false current-activity
  claim — acceptance criterion 4 fails.

**Containment check:** `market_coverage` is read only in reporting/coverage
(`reporting.py:69,399`, `service.py:830,1081`, `db.py:1536,1884`), never in
`classify.py`, `changes.py`, `intel_events.py`, or `alerts.py`. No `attribution`-only mode
exists. So there is no existing switch that would let a historical source contribute
attribution without also contributing projects, changes and alerts.

### 2.5 Idempotency and integrity (measured)

Two identical ingest+assemble passes on an isolated database were byte-stable:

```
[pass 1] permits=1000 projects=145 changes=145 evidence=1586 parties=183
[pass 2] permits=1000 projects=145 changes=145 evidence=1586 parties=183
second pass added changes: 0
```

`integrity_report()` returned `healthy: True, findings: []` in every probe. So
**re-ingestion is already idempotent** (acceptance criterion 5 is met by the existing
architecture), and the integrity machinery is sound. The blockers above are about *meaning*
and *signals*, not about duplication.

### 2.6 Connector reliability / maintainability

Both disabled connectors are small and readable, but they carry defects that matter for a
production integration:

- **`since` is ignored.** Both `fetch_raw` signatures accept `since` but neither uses it
  (`dallas_permits.py:43`, `:129`). Measured: `uses it? False` for both. The AGENTS.md
  invariant "the bound reaches the source's own date filter" does **not** hold for these two.
  The ArcGIS connector hard-codes `where: "1=1"` (`:49`); the Socrata connector builds a
  `$where` only when `since is not None` (`:139-140`), which the pipeline never passes
  because `resolved_since()` is `None` (no `since_months` configured). A full GIS pull is
  14,336 records; a full Socrata pull is 126,840 — neither bounded.
- **No connector tests.** No test module references `DallasGisPermitsConnector` or
  `DallasPermitsSocrataConnector`. `tests/test_dallas_*` cover the *Accela* connector and
  classification, not these two. Their normalizers are therefore **unverified**.
- **Socrata `contractor` is unparsed** — stored as the whole name+address+phone blob, so
  identity resolution would treat the address as part of the company name.
- The config note `"Carries ... the permit contractor of record"` (`sources.yaml:109`) is
  literally accurate and is exactly why it must not be read as a GC.

---

## 3. Go / No-Go

| Criterion | Verdict | Evidence |
|---|---|---|
| Traceable source + evidence per relationship | Would pass | `assert_field` records source/url/excerpt |
| Roles preserve the source's meaning | **FAIL** | field is "contractor of record", a mixed bag (§2.2) |
| No unsupported GC attribution | **FAIL** | maps to `ROLE_GENERAL_CONTRACTOR` (`assemble.py:179`) |
| Historical attribution ≠ new/current opportunity | **FAIL** | alertable `new_permit` at "now" (§2.4) |
| Re-ingestion idempotent | Pass | §2.5 |
| Existing tests pass | Pass | 1215 passed, 1 skipped |
| `integrity_report()` healthy, `alerts_without_event()` empty | Pass | §2.5, and suite green |
| No production/external change | Pass | temp DBs only; `data/oppintel.db` unchanged |

**Decision: NO-GO.** Per the task's own instruction — "If the source does not qualify, stop
and request approval before trying a different source" — work stops here and no integration
was implemented.

---

## 4. What would have to be true to qualify

For a Dallas historical feed to be safely usable for attribution, all of the following would
be needed, and none exists today:

1. A **role-honest target**: store the name under a role the source actually supports. Since
   the source does not distinguish roles, the honest label is "contractor of record", not
   `general_contractor`. This needs either a new role constant or a per-source role mapping —
   a schema/role-vocabulary decision, not a config toggle.
2. An **attribution-only ingestion mode** that lands and normalises records, contributes
   `project_party`/evidence, and **suppresses** project creation, `project_change` rows,
   events and alerts for that source. This is the missing containment primitive.
3. A **`since` implementation** on both connectors so a backfill is bounded.
4. **Connector tests** over captured payloads, as the other three connectors have.

That is a multi-part change to the pipeline's meaning. It should not be smuggled in as
"enable a config flag".

---

## 5. Recommendation — the next defensible step

Do **not** enable either disabled source. The next step is **source discovery for a
participant field with an explicit role**, in priority order:

1. **Operator-authenticated Accela detail fetch (highest value).** The enabled, *current*
   Dallas source already reaches `CapDetail.aspx`; its "Additional Licensed Professionals"
   section is present but empty for anonymous requests. If an authenticated view exposes a
   *labelled* participant (applicant / contractor / licensed professional), that is a
   **current** source — no false-recency problem — and it reuses the existing connector.
   This should be probed before writing any integration code. **Note:** it may require
   credentials and must be authorised by the operator.
2. **A current source that publishes an explicit role column** (e.g. a jurisdiction that
   distinguishes "applicant" from "contractor" from "owner"). Fort Worth already publishes
   `Owner_Full_Name` (`fort_worth_permits.py:160`) and Collin publishes `propownername`
   (`collin_cad_permits.py:122`); the missing participant is specifically the GC.
3. **A licensed-contractor registry join.** Texas TDLR / city contractor registries publish
   licensed contractors by trade. A registry gives a *role* (trade) and a *license*, which is
   stronger evidence than a bare name, and it is a different data shape than permits.

Separately, and independent of contractor data: the two disabled connectors' ignored `since`
parameter is a real defect. Fixing it is safe, bounded, and worth doing on its own — but it
does not by itself make the source suitable for attribution.

---

## 6. Files inspected / changed

**Inspected (read-only):**
`config/sources.yaml`, `config/markets.yaml`, `src/oppintel/connectors/dallas_permits.py`,
`connectors/dallas_accela_permits.py`, `connectors/fort_worth_permits.py`,
`connectors/collin_cad_permits.py`, `connectors/base.py`, `connectors/__init__.py`,
`pipeline.py`, `assemble.py`, `provenance.py`, `classify.py`, `changes.py`,
`intel_events.py`, `intelligence.py`, `identity.py`, `reporting.py`, `monitoring.py`,
`coverage.py`, `config.py`, `dates.py`, `constants.py`, `db.py`, `app/alerts.py`,
`app/main.py`, `docs/engineering/dallas_source.md`, `tests/test_dallas_*.py`.

**Changed:** none in product code. This report is the only new artefact.

**Scratch files:** five throwaway probe scripts were created and **deleted**; `git status` is
unchanged from the start of the task (six modified files + the untracked Phase 9 audit doc).

---

## 7. Test / runtime baseline

```
$ PYTHONPATH=vendor/python:src python -m pytest tests/ -q
1215 passed, 1 skipped in 142.09s (0:02:22)
```

- No failures, no errors. The 1 skip is pre-existing.
- Production `data/oppintel.db` verified unchanged: `permit`, `project`, `evidence`,
  `project_party`, `project_change` all still 0 rows (schema-only checkout).
- No `data/raw/*.jsonl` capture was written (probes redirected `DATA_DIR` to temp).

## 8. Working tree

```
 M AGENTS.md
 M src/oppintel/app/templates/companies/detail.html
 M src/oppintel/companies.py
 M src/oppintel/db.py
 M tests/test_ai_analyst_app.py
 M tests/test_companies.py
?? audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md
```

These are the Phase 9 company-layer changes described in
`audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md`. They were **preserved**, not touched.

---

## 9. Open questions / could not verify

- **Accela authenticated detail view.** Anonymous detail pages expose no participant. Whether
  an authenticated view exposes a labelled contractor/applicant is **unverified** — it needs
  operator authorisation and credentials.
- **Socrata terms of use.** The dataset is public and unauthenticated; specific redistribution
  terms were not reviewed in this task.
- **`new_permit` reach.** The false signal fires for users *watching* the affected project
  (alert join is on `watched_opportunity`); the exact blast radius depends on watch counts,
  which cannot be measured on the schema-only checkout.
- **GIS/Socrata overlap with Accela.** Not measured. The date ranges do not overlap
  (2019, 2023 vs. current), so overlap is likely low, but this was not confirmed record by
  record.

## 10. Constraints honoured

- Audit/qualification only; no product implementation, no schema migration, no UI change.
- The disabled connectors were **not** enabled; no production data or external service
  modified.
- No secrets printed; only presence of configuration was noted.
- All probe runs used isolated temp databases and a redirected `DATA_DIR`.
- No metrics, coverage or success claims are asserted beyond the measurements quoted above.

**Awaiting approval before pursuing any of the sources in §5.**
