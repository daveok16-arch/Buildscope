# AI analyst audit — grounding an LLM in BuildScope's evidence layer

Written before any change, by reading the working tree at `2c9c76b` (`main`) and running the
suite. It records what exists, what the constraints are, and the strategy the AI analyst follows.

## A. Baseline (actually executed)

```
pip install -r requirements.txt pytest      # container had no dependencies installed
PYTHONPATH="src" python -m pytest tests/ -q
1132 passed, 1 skipped in 133.46s
```

Python 3.13.15. No `vendor/python` in this checkout (it is git-ignored), so dependencies were
installed from `requirements.txt`.

## B. The Gemini gap

`metadata.json` declares `majorCapabilities: ["MAJOR_CAPABILITY_SERVER_SIDE_GEMINI_API"]` and
`.env.example` documents `GEMINI_API_KEY`, but there is **no AI code anywhere** in the running
app. `docs/engineering/BUILD_SCOPE_REPOSITORY_AUDIT.md` already flagged this as an unimplemented
capability. The analyst fills that gap without weakening any honesty rule.

## C. What the analyst must never do

These are enforced by the existing suite (`tests/test_app_architecture.py`, `test_copy_honesty.py`,
`test_app_seo.py`) and are the design constraints for this work.

1. **Never invent a value.** A missing field is `NULL` in the database, rendered `Not verified.`
   A model must never turn absence into a claim.
2. **Relevance is not procurement.** `PERMIT_EVIDENCE_NOTICE` — permit evidence does not confirm a
   package is available for bid. No output may assert bid status.
3. **No intelligence logic in the app layer.** Scoring, classification and procurement wording
   live in the intelligence layer. The analyst therefore lives in `src/oppintel/`, not `app/`.
4. **The service boundary.** Only `service.py` queries intelligence tables from the app layer.
   The analyst reads through the same `Database` helpers, and the route reads through
   `OpportunityService`.
5. **No route grants privilege.** New surfaces are ADMIN-gated via `_require_admin` (CLI-only
   grant) and marked `noindex` like `/content/linkedin` and `/admin/data`.

## D. Available grounding (the assets the analyst stands on)

| Source | What it gives |
|---|---|
| `reporting.field_verdict` | Per-field verdict `CONFIRMED` / `PARTIALLY VERIFIED` / `NOT VERIFIED` — the field's support strength |
| `db.evidence_history_for` | Append-only observation archive (every value ever seen) |
| `service.permits_for` | Contributing permits with type, date, value, area, text |
| `service.sources_for` | Source name, record key, fields supported, URL |
| `db.changes_for_project` | Recorded changes with before/after values |
| `db.entities_for_project` | Resolved owner/GC/architect/mechanical entities |
| `discrepancy` | Cross-source contradictions, preserved not resolved |
| `procurement_explanation` | The shared procurement wording |
| `PERMIT_EVIDENCE_NOTICE` | The fixed permit-evidence caveat |

## E. Advanced intelligence strategy (the design)

The failure mode of "an LLM over a database" is fluent invention. The strategy inverts the usual
flow — the model is never the source of a fact, only of prose about facts that already exist.

1. **Evidence-first retrieval.** `collect_facts` builds an immutable `Fact` list. A fact is emitted
   only for a field whose value is supported by a stored evidence row (`field_verdict`). An
   unsupported field is emitted as an explicit **unknown**, never a value.
2. **Dossier, not prompt-tricks.** `assemble_dossier` serialises the facts into a structured,
   numbered dossier. The model is asked to write prose *about the dossier*, not to recall anything.
3. **Structured output.** Gemini's `responseSchema` (`responseMimeType: application/json`) forces
   a fixed shape (`summary`, `why_it_matters`, `field_assessments`, `questions_to_resolve`,
   `confidence`, `limitations`), so parsing is deterministic and a hallucinated extra section is
   impossible.
4. **Verification, then publication.** `validate_brief` re-derives support from the fact list and
   flags any fact-shaped token in the model's free text that is not among the supported values:
   a number that is not a stored figure, an ISO date that is not a stored date, a restated
   `Not verified` marker, or bid language the notice forbids. A violation demotes the brief to
   `UNVERIFIED` and replaces the narrative with a deterministic, evidence-only summary.
5. **No fabricated judgment.** The model may not re-grade a field: `field_assessments[].verdict`
   must equal the dossier's verdict for that field. `confidence` is constrained to
   `high`/`medium`/`low`.
6. **Graceful absence.** With no `GEMINI_API_KEY`, or on any API error, the analyst returns an
   `UNAVAILABLE` brief built entirely from the evidence — it never blocks a page and never
   pretends an analysis happened.
7. **Deterministic testability.** The evidence pipeline is tested with no network; the model layer
   is a `Protocol` (one in-process fake for the transport), and `GeminiClient` is exercised
   against a real HTTP boundary in manual verification.

## F. Surface plan

* HTML `/ai-analyst` (ADMIN, `noindex`, robots-disallowed) — evidence, verification result, the
  unsupported fields, and the caveat, so an operator sees what the model was given and whether
  what it said survived.
* JSON `/api/project/<id>/analyst` (ADMIN: `401`/`403`/`404`) — `status`, `verified`,
  `unsupported_fields`, `verification_issues`, `caveat`.
* CLI `flask ... analyst-project ID [--json] [--no-model]` — offline-safe.

## G. Result (verified, this checkout)

* Live model: `gemini-2.5-flash`. On a real ingested Dallas mechanical permit the brief came back
  `AVAILABLE`, `verified: true`, `confidence: high`, with all five unstated fields correctly
  reported as `NOT VERIFIED` and no verification issues. The narrative quoted only stored figures
  (`2026-10-14`, permit `COM-MEC-26-002656`, `2540 ELM ST`) and carried the permit-evidence notice.
* Offline: no key → `UNAVAILABLE`, evidence intact, deterministic summary; the page and CLI both
  render, and no surface fails.
* A fabricated payload (an unsupported `$9,999,999` and "out for bid") is demoted to `UNVERIFIED`
  with the deterministic summary, exactly as designed.
* Tests: `tests/test_ai_analyst.py` (31) and `tests/test_ai_analyst_app.py` (16). Full suite:
  1179 passed, 1 skipped. No test reaches the public endpoint — the model is reached through the
  `AnalystClient` Protocol, with a real local HTTP stub for the transport itself.
* Live stability, five consecutive real projects: three `AVAILABLE`/`verified`, one demoted to
  `UNVERIFIED` because the model slipped "not verified" into a limitation (the guard caught it and
  the operator sees the evidence-only summary), and one `UNAVAILABLE` when the free-tier quota was
  reached mid-run. The `UNAVAILABLE` limitation reads as one clean sentence with no URL.
* Hardening from the live runs: a demotion now strips *all* model prose (not just the narrative),
  so an offending limitation cannot survive in the JSON; the bid-language check is clause-scoped
  and negation-aware, so restating the caveat is not read as a claim while a real claim beside a
  negation still is; and an HTTP error is condensed to one sentence rather than pasting Gemini's
  JSON body (with its documentation URL) into a limitation.

| Surface | Gate | Why |
|---|---|---|
| `/ai-analyst` (HTML) | ADMIN | The analysis is for operators; a draft-grade narrative is not published |
| `/api/project/<id>/analyst` (JSON) | ADMIN | Machine-readable brief for internal tooling |
| `flask ... analyst-project` | CLI | Offline generation, consistent with `report-quality` |

`/ai-analyst` joins `/admin` in the robots disallow list and is `noindex` — it is an operational
surface, not content.
