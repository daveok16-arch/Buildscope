# Architecture

BuildScope separates the work of *deciding what the data means* from the work of *showing it*.
Nothing that reads the data is allowed to change it.

## Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│ Application layer                                                   │
│   Flask routes ── OpportunityService ── templates ── JSON API       │
│   accounts · SEO · sitemap · reports                                │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ reads only; no intelligence logic
┌───────────────────────────────▼─────────────────────────────────────┐
│ Intelligence layer                                                  │
│   connectors → raw landing → normalize → assemble → evidence        │
│   → classify → eligibility → procurement → reports                  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                    config/markets.yaml · config/trades.yaml
                    config/sources.yaml
```

**Intelligence layer.** Collects public permit records, normalises them, assembles individual
permits into the underlying project, attaches per-field evidence, and classifies each project
against documented evidence. It never invents a value: a field a source does not publish stays
empty in the database and is rendered as "Not verified". The full design rationale — schema, the
evidence chain, collection strategy, and classification scoring — is in the
[design record](engineering/DESIGN.md).

**Application layer.** A web application that reads the intelligence data. It does not
re-implement any classification, eligibility or scoring rule; it calls the same modules the report
generator uses, so a page and a report can never disagree about a project.

## The service boundary

`src/oppintel/service.py` (`OpportunityService`) is the only module in the application layer that
queries the intelligence tables. No route, template or API handler writes SQL against `project`.
This is enforced, not merely documented: tests in `tests/test_app_architecture.py` fail if
classification logic, a hardcoded market name, or a literal evidence column appears in the
application layer.

## Module map

| Module | Responsibility |
|---|---|
| `connectors/` | Per-source fetch and normalize. DallasNOW/Accela, Fort Worth ArcGIS, Collin CAD |
| `pipeline.py` | Orchestrates collect → land → normalize → assemble → classify |
| `normalize.py` | Commercial filtering, boilerplate stripping, mechanical evidence detection |
| `assemble.py` | Clusters permits at one address into a project |
| `provenance.py` | The never-invent rule; the only path that sets a project field |
| `classify.py` | HIGH / MEDIUM / NEEDS_VERIFICATION with recorded reasons and four gates |
| `eligibility.py` | The seven customer-brief criteria |
| `grouping.py` | Building identity, sibling detection, duplicate statistics |
| `procurement.py` | The four procurement states |
| `discrepancy.py` | Cross-source contradiction detection |
| `report_generator.py` | Customer brief and internal research report |
| `service.py` | **The boundary.** Every web read goes through here |
| `changes.py` | Diffs a project against its last snapshot; records only real changes |
| `quality.py` | Observes data-quality problems without repairing or imputing them |
| `trends.py` | Market trend metrics, each with its own stated definition |
| `search_index.py` | FTS5 index, query quoting, and match explanations |
| `linkedin.py` | Evidence-backed draft generation for the operator surface |
| `app/` | Flask application: routes, templates, static assets, SEO, accounts, API |
| `app/workflow.py` | The account's own record: watching, pipeline, notes, tags, activity |
| `app/alerts.py` | Turns recorded changes into per-user alerts. An alert cannot exist without an event |
| `app/entitlements.py` | Plan, subscription and entitlement, kept as three separate things |
| `app/matching.py` | Why an opportunity matches an account, as checkable facts |
| `app/security.py` | CSRF, rate limiting and response headers |
| `app/content.py` | Owned educational prose for the guide pages |

## The web application

| Page | Purpose |
|---|---|
| `/` | Homepage: what the product does, with real counts from the database |
| `/dashboard` | The account's working centre: new matches, recently updated, watching, saved, pipeline, review |
| `/opportunities` | The discovery directory, with filters, search and pagination |
| `/opportunities/<slug>` | One opportunity: evidence, provenance, procurement status, sources, timeline, notes |
| `/markets`, `/markets/dfw`, `/markets/dfw/dallas` | Market and city landing pages |
| `/trades`, `/trades/commercial-hvac` | Trade landing pages |
| `/markets/dfw/commercial-hvac` | Market crossed with trade — the primary SEO page |
| `/project-types`, `/project-types/<type>` | Project-type landing pages, one per type the data holds |
| `/guides`, `/guides/<slug>` | Educational content on reading permit evidence |
| `/how-it-works` | The pipeline and what the product refuses to do |
| `/reports` | Published customer reports |
| `/trends` | Market trend radar; each metric states its definition |
| `/watching` | Opportunities the account is monitoring for change |
| `/my-pipeline` | The account's own workflow stages over opportunities |
| `/alerts` | Event-driven alerts, each traceable to a recorded change |
| `/saved`, `/preferences` | Saved opportunities and personalization |
| `/signin`, `/signup` | Free account |
| `/content/linkedin` | Operator-only draft generation (**ADMIN**) |
| `/sitemap.xml`, `/robots.txt` | Crawler control |
| `/api/*` | Read-mostly JSON API |
| `/admin/data` | Internal operations view. **Operator accounts only** |

`/my-pipeline` is deliberately not `/pipeline`: the reserved-path test that keeps ingestion off
the web surface reserves `/pipeline`, and the account's workflow route must not shadow it.

## The account workspace

The dashboard answers the five questions the product is built around — what is happening, why it
matters, what the evidence is, what changed, and what the customer can do next.

- **Save vs. watch.** A save bookmarks a project. A watch asks the system to re-check the record
  for meaningful change and drives alerts. Separate actions, separate tables, separate intents.
- **Pipeline.** A user workflow stage (New, Reviewing, Watching, Target, Contacted, Pursuing,
  Closed out) is the account's own label for where it is with a project. It is *not* the project's
  procurement status; the vocabularies are kept disjoint on purpose, and a test asserts they never
  overlap.
- **Alerts are event-driven.** Every alert points at a recorded change (with a previous value, a
  current value and a source) or at the moment a project first matched the account's criteria.
  There is no scheduler that invents notifications.
- **Change detection.** Each assembly pass diffs a project against the previous snapshot and
  records only real differences. A re-run with identical data produces nothing.
- **Personalization.** Match reasons are checkable facts produced by one shared module, so a card,
  a detail page and the API cannot disagree.

## Schema order

The intelligence schema and the application schema are applied in a fixed order, and the
application schema references intelligence tables. `tests/test_app_architecture.py` and the
migration tests assert the order so a partial initialisation cannot silently succeed.

## Adding a market or trade

A market is a `config/markets.yaml` entry plus whatever connectors its sources need. Nothing in
the application code references DFW. A trade is a `config/trades.yaml` entry that declares its own
evidence column and values, so discovery, statistics and reports scope themselves from the
declaration without code changes. Houston and Austin are present as inactive placeholders, which
is why the architecture tests can prove a second market loads without code changes. Worked
examples are in the [design record](engineering/DESIGN.md).
