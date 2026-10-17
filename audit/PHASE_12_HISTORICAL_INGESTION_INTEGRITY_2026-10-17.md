# Phase 12 — Historical Ingestion Integrity and Change-Event Safety

**Date:** 2026-10-17
**Phase:** BuildScope 2.0 Phase 12 (controlled implementation, tests required)
**Mode:** Audit + minimal fix. No redesign of the intelligence engine.
**Status:** Complete. Full suite green; disabled sources still disabled; production data untouched.

---

## 1. Baseline

| Item | Value | How verified |
|---|---|---|
| Repository | `/workspace/project/Buildscope` | `pwd` |
| Branch | `main` | `git rev-parse --abbrev-ref HEAD` |
| Commit | `4344ace` | `git rev-parse HEAD` |
| Test baseline | **1215 passed, 1 skipped** (141.15s) | `pytest tests/ -q` before any change |
| Skipped test | `tests/test_accessibility.py:29` — Playwright is a developer-only tool | `pytest -rs` |

**Initial working tree** (all preserved; nothing reset, stashed, cleaned or discarded):

```
 M AGENTS.md                                        (Phase 9)
 M src/oppintel/app/templates/companies/detail.html (Phase 9)
 M src/oppintel/companies.py                        (Phase 9)
 M src/oppintel/db.py                               (Phase 9)
 M tests/test_ai_analyst_app.py                     (Phase 9)
 M tests/test_companies.py                          (Phase 9)
?? audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md
?? audit/CURRENT_PARTICIPANT_SOURCE_DISCOVERY_2026-10-16.md
?? audit/PHASE_11_PARTICIPANT_SOURCE_STRATEGY_2026-10-17.md
?? audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md
```

**Environment note (verified, not a repository defect):** the sandbox Python environment was
reset again at the start of this phase (`pytest`/`flask`/`yaml` missing; the documented
`vendor/python` tree absent). Dependencies were reinstalled from the repository's own
`requirements.txt` plus `pytest`. No repository file was changed by this.

**Phase tracker:** no dedicated phase-tracker file exists. `docs/engineering/BUILDSCOPE_PLATFORM_AUDIT.md`
is the closest standing document but predates Phases 9–12 and numbers its phases differently, so
writing a "Phase 12" row into it would create a competing roadmap. Per the instruction not to
create a competing roadmap, this report is the Phase 12 record and the platform audit is left
untouched. This is flagged as an open question in §10.

---

## 2. Root cause for each defect

### Defect A — historical date filtering

**A1. `dallas_gis_permits` ignored `since` entirely.** `DallasGisPermitsConnector.fetch_raw`
accepted `since` and never read it: the ArcGIS query was hard-coded `"where": "1=1"`
(`connectors/dallas_permits.py:47-53` before the fix). A bounded crawl therefore downloaded the
entire layer and relied on nothing to trim it. **Root cause: the bound was never translated into
the source's own filter.**

**A2. `dallas_permits_socrata`'s filter was unreachable and off by one.** It built
`$where = issued_date > '{since}'` only when `since is not None` (`:139-140`), but the pipeline
resolves `since` from `SourceConfig.resolved_since()`, and this source declares no `since` or
`since_months` — so the pipeline always passed `None` and the predicate was dead code. The strict
`>` also dropped any row dated exactly on the boundary, and the raw `since` was interpolated
without coercion, so a `date` object would have been formatted inconsistently with the ISO string
Socrata expects. **Root cause: dead branch + exclusive comparison + no coercion.**

### Defect B — historical records and event timestamps

`changes.diff_project` emitted `NEW_PERMIT` for **every** permit key absent from the previous
snapshot, with no reference to the permit's own date. `NEW_PERMIT` is in `NOTIFIABLE_KINDS` and in
`app/alerts.py:CHANGE_TO_ALERT`, so a 2019 permit ingested for the first time today raised a
**"New permit filed"** alert. **Root cause: the added-permit branch keyed on observation (the key
is new to the snapshot) instead of on the permit's source date.**

Note the *first* assembly pass is already safe: with no prior snapshot, `diff_project` returns a
single `NEW_PROJECT`, which is not alertable. The defect is specifically the *later* pass on which
an old permit first appears.

### Defect C — attribution-only updates

Investigated rather than assumed. Findings:

* Project identity is `project_key = sha1(trade|address_key)` (`assemble.py:project_key_for`), and
  `upsert_project` keys on it with `ON CONFLICT(project_key) DO UPDATE`. Enriching a permit at a
  known address therefore **updates the same project in place** — it cannot fork a second project
  or re-announce `new_project`.
* Every asserted field flows through `provenance.assert_field`, which refuses a value with no
  identified source and records an evidence row; `assemble_project` records an owner/contractor
  party **only when the permit carries that value**, and explicitly creates **no** mechanical
  contractor party because no source publishes one.
* `upsert_project` **rebuilds** the evidence set and party rows from the permits on every pass and
  appends to the immutable `evidence_history`, so repeated enrichment converges instead of
  accumulating.

**Conclusion:** within the permit model, attribution-only enrichment is already safe and
idempotent; it was verified and locked down with tests (§4), not re-implemented. The genuine
**blocker** is a *non-permit* attribution side channel: because evidence and parties are rebuilt
from permits each pass, an externally attached evidence/party row that no permit backs would be
wiped by the next assembly. A durable attribution-only path for a non-permit source (e.g. a
registry overlay) would require either an assembly-level merge — a broad redesign of the rebuild
model — or a schema change to mark rows as externally sourced. That is documented as blocked in
§7 with the smallest justified alternative; no such change was made.

### Defect D — idempotency

The mechanisms already exist and were verified, not rebuilt: `raw_record` is
`UNIQUE(source_id, natural_key, payload_hash)` + `INSERT OR IGNORE`; `permit` is
`UNIQUE(source_id, natural_key)` + upsert; `project_event.event_uid` is `UNIQUE` + `INSERT OR
IGNORE`; `alert_event` uses `INSERT OR IGNORE` with `watched_opportunity.last_seen_change_id`
tracking. Focused regression tests were added for each.

---

## 3. Files changed and why

| File | Change | Defect |
|---|---|---|
| `src/oppintel/connectors/base.py` | Added `coerce_since()` and `within_since()` — one shared implementation of "is this date inside the bound", used by both Dallas connectors. | A |
| `src/oppintel/connectors/dallas_permits.py` | `DallasGisPermitsConnector.fetch_raw` now puts the bound in the ArcGIS `where` and keeps a client-side backstop. `DallasPermitsSocrataConnector.fetch_raw` coerces the bound, uses inclusive `>=`, and adds the same backstop. | A |
| `src/oppintel/changes.py` | Added `HISTORICAL_PERMIT` (non-alertable), `classify_added_permit()`, and a `recent_days`/`today` parameter to `diff_project`; the added-permit branch now classifies by the permit's own source date. | B |
| `src/oppintel/pipeline.py` | `_monitor_project` passes the trade profile's `recent_permit_days` into `diff_project`, so "recent" is configured once. | B |

No change to `db.py`, no schema change, no new dependency, no change to the app layer, no change to
any source's `enabled` flag, and no change to the Phase 11 decision.

**Design choice — one date policy, no new threshold.** The freshness window is read from
`trade.scoring.recent_permit_days` (already `180` in `config/trades.yaml`), the same value
`classify.py` uses for its recency points, with a module-level `DEFAULT_RECENT_PERMIT_DAYS = 180`
fallback. No new config key was introduced, so the two places cannot disagree.

---

## 4. Tests added and actual results

Three new modules, **36 tests**, all passing:

| File | Tests | Covers |
|---|---|---|
| `tests/test_connector_date_filtering.py` | 13 | Defect A: server-side bound for both connectors, inclusive `>=`, client backstop drops a provably-old row, undated row kept, absent/malformed bound widens, pagination preserved. HTTP mocked — **no external service contacted.** |
| `tests/test_historical_ingestion.py` | 16 | Defect B/D: old permit → `historical_permit`, fresh permit → `new_permit`, undated → historical, historical not alertable, backfill raises no alert, genuine new permit still alerts, first pass = `new_project` only, re-ingest idempotent, repeated assembly/alert idempotent, event `occurred_at` vs `recorded_at`. |
| `tests/test_attribution_only.py` | 7 | Defect C: enrichment keeps one project, raises no `new_project`, records evidence with source, role recorded only when stated, repeated enrichment does not duplicate parties/evidence/entity links. |

Actual results:

```
tests/test_connector_date_filtering.py   13 passed
tests/test_historical_ingestion.py       16 passed
tests/test_attribution_only.py            7 passed
focused affected set (changes/alerts/monitoring/acceptance/events/
  source_window/trends/reporting)         98 passed
```

**Full suite after the change: `1251 passed, 1 skipped in 141.13s`.** The skip is the pre-existing
`test_accessibility.py:29` (Playwright not installed). 1251 − 1215 baseline = 36 new tests; no
pre-existing test changed status. No failures, no errors, no new skips.

---

## 5. Event and alert behaviour: before vs after

| Scenario | Before | After |
|---|---|---|
| First assembly pass over a whole backfill | `new_project` only (not alertable) | unchanged — `new_project` only |
| Old permit (pre-window) first ingested on a later pass | `new_permit` → **"New permit filed" alert** | `historical_permit` → timeline entry, **no alert** |
| Permit with no usable date, first ingested later | `new_permit` → alert | `historical_permit` → **no alert** |
| Permit dated inside the window | `new_permit` → alert | unchanged — `new_permit` → alert |
| Re-ingest of an unchanged record | no change | unchanged — no change |
| Attribution-only enrichment of a known permit | one project, no `new_project` | unchanged (verified, now tested) |

`occurred_at` (the permit's own date) and `recorded_at` (observation) were already separate in
`project_event`; the tests now pin that a `PERMIT_RECORDED` event is dated by the source and that a
`PROJECT_DISCOVERED` event is dated by observation. Ingestion time is never substituted for the
source date, and no issuance, commencement or participation is inferred from ingestion time.

---

## 6. Date-filter behaviour and known limitations

* **GIS (ArcGIS):** the bound is placed in `ISSUE_DATE >= timestamp '… 00:00:00'` and cuts the
  download. A client-side backstop drops only rows provably before the bound.
* **Socrata:** the bound is placed in `issued_date >= '…T00:00:00.000'` (inclusive) and cuts the
  download. Same backstop.
* **Undated rows are kept, not dropped.** A record the source left undated (or dated in a format
  `parse_date` does not recognise) cannot be shown to be out of window, so it is retained. This is
  deliberate: silently discarding it would be a quiet data loss, which the task forbids ("never
  silently claim filtering succeeded if it did not"). The consequence is that a bounded crawl of a
  source with many undated rows may keep more than the bound strictly implies — visible in the
  record count, not hidden.
* **A malformed `since` widens the request.** `coerce_since` returns `None` rather than guessing,
  so a bad CLI `--since` fetches everything instead of filtering on a wrong date.
* **Neither connector was enabled or run against a live service.** Verification is by mocked
  fixtures, per the task. The ArcGIS `where` syntax and the Socrata `$where` syntax match the
  forms the other connectors already use (`fort_worth_permits` uses the same `timestamp '…'`
  form), but a live confirmation of these two disabled sources was **not** performed and is not
  claimed.

---

## 7. Attribution-only ingestion: implemented or deferred?

**Partially implemented, partly deferred — deliberately.**

* **Safe today and now tested:** enriching a permit the system already holds with additional
  source attribution (owner, contractor, source link) updates the same project in place, raises no
  false `new_project`, records provenance, and converges under repetition. This is the mechanism
  the task asked to *reuse*, and it was verified rather than rebuilt.
* **Deferred (documented blocker):** a durable attribution-only path for a **non-permit** source —
  e.g. attaching registry or directory information that no permit row backs. `upsert_project`
  rebuilds `evidence` and `project_party` from the permits on every pass, so such a row would be
  wiped by the next assembly. Making it durable requires either (a) an assembly-level merge that
  preserves externally sourced rows, which is a broad redesign of the documented
  "rebuild every pass" model, or (b) a schema change marking a row's origin so the rebuild can
  spare it.

**Smallest justified alternative (proposed, not built):** route attribution through the existing
connector/Permit path so it lands as a permit-backed fact and inherits the rebuild, provenance and
idempotency guarantees unchanged. That keeps the intelligence model intact and needs no schema
change. If a future phase needs true non-permit overlays, option (b) — one `origin` column on
`evidence`/`project_party` plus a rebuild that spares `origin='external'` rows — is the minimal
schema change; it is **not** made here because the task requires a separately justified schema
change and no current source needs it.

No participant relationship or role was invented anywhere in this phase, and no registry lookup or
name match was allowed to establish a project relationship.

---

## 8. Confirmation: production data, source configuration, schema

* **Production data:** untouched. `data/oppintel.db` still reads `permit 0, project 0,
  project_change 0, evidence 0, project_event 0, alert_event 0`.
* **Source configuration:** unchanged. `dallas_gis_permits` and `dallas_permits_socrata` are still
  `enabled: false`; the three current sources are still `enabled: true`. No `config/*.yaml` file
  was modified.
* **Schema:** unchanged. `db.py` is not in this phase's diff (its working-tree modification is the
  pre-existing Phase 9 company work). No `CREATE`/`ALTER` was added; no migration was run.
* **Secrets:** none read, printed or stored. No external provider contacted; no third-party
  authentication attempted.
* **Phase 11 decision:** unchanged.

---

## 9. Remaining risks and blocked acceptance criteria

1. **Live confirmation of the two disabled connectors is blocked** by design: enabling them is
   forbidden this phase and they were not run. The date-filter fix is verified by fixtures only.
   Residual risk: the exact `where` syntax against the live ArcGIS/Socrata endpoints is
   unconfirmed, though it matches the patterns the enabled connectors already use.
2. **Undated rows are retained** under a bounded crawl (see §6). This is intentional but means a
   bound is not an absolute cap on stored rows for a source with poor dates. `quality.py` already
   flags malformed/future dates; no new handling was added.
3. **The freshness window is a heuristic**, not a source fact. 180 days matches the classifier's
   recency points, but a permit issued 179 days ago and one issued yesterday are both `new_permit`.
   This is the same trade-off the classifier already makes and is not new.
4. **Non-permit attribution-only remains blocked** (§7) pending a decision on the smallest
   justified schema change or the connector-path alternative.
5. **The `changes`/`alerts` UI labels were not touched.** `HISTORICAL_PERMIT` renders through the
   existing `change_kind.replace("_", " ").title()` paths as "Historical Permit", which is
   accurate; no template change was needed or made.

---

## 10. Open questions

* **Is there a maintained phase tracker?** None was found. `docs/engineering/BUILDSCOPE_PLATFORM_AUDIT.md`
  numbers phases differently and predates Phases 9–12, so it was not edited (editing it would
  create a competing roadmap). If a tracker is expected, its location should be named.
* **Should `historical_permit` also be excluded from the `/changes` headline breakdown?** It is
  already excluded from the "detected differences" count by the existing
  `change_kind <> 'new_project'` filter, which counts it as a difference. Whether an old import
  should read as a "difference" on the public feed is a product question, not a correctness one;
  left as-is to avoid an unrequested UI change.
* **Does any future source need non-permit attribution?** If yes, §7's option (b) is the minimal
  schema change; if no, the connector-path alternative suffices and no schema change is ever needed.

---

## 11. Final state

| Item | Value |
|---|---|
| Branch / commit | `main` @ `4344ace` (unchanged; not committed this phase) |
| Files changed by Phase 12 | `connectors/base.py`, `connectors/dallas_permits.py`, `changes.py`, `pipeline.py` |
| Tests added | `tests/test_connector_date_filtering.py`, `tests/test_historical_ingestion.py`, `tests/test_attribution_only.py` (36 tests) |
| Full suite | **1251 passed, 1 skipped** (141.13s) |
| Disabled sources | still `enabled: false` |
| Production data / schema / source config | unchanged |

**Working tree after Phase 12** (Phase 9 changes and all four prior audit docs preserved):

```
 M AGENTS.md
 M src/oppintel/app/templates/companies/detail.html
 M src/oppintel/changes.py                 <-- Phase 12
 M src/oppintel/companies.py
 M src/oppintel/connectors/base.py         <-- Phase 12
 M src/oppintel/connectors/dallas_permits.py  <-- Phase 12
 M src/oppintel/db.py
 M src/oppintel/pipeline.py                <-- Phase 12
 M tests/test_ai_analyst_app.py
 M tests/test_companies.py
?? audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md
?? audit/CURRENT_PARTICIPANT_SOURCE_DISCOVERY_2026-10-16.md
?? audit/PHASE_11_PARTICIPANT_SOURCE_STRATEGY_2026-10-17.md
?? audit/PHASE_12_HISTORICAL_INGESTION_INTEGRITY_2026-10-17.md   <-- this report
?? audit/SOURCE_QUALIFICATION_CONTRACTOR_2026-10-16.md
?? tests/test_attribution_only.py          <-- Phase 12
?? tests/test_connector_date_filtering.py  <-- Phase 12
?? tests/test_historical_ingestion.py      <-- Phase 12
```

No commit was made and no PR was opened; the task did not request either, and the working tree
carries pre-existing Phase 9 changes that should not be swept into an unrequested commit.

**Phase 12 complete. Stopping here; awaiting approval before any further phase.**
