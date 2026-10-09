# BuildScope

**Construction Opportunity Intelligence**

*See the work before the work begins.*

Commercial HVAC and mechanical contractors spend hours hunting public permit records for
projects that may be worth bidding. BuildScope does that hunt for you: it collects public
construction and permit records across a market, assembles them into real projects, checks what
the records actually evidence, and surfaces the opportunities that match your trade and territory
— with the source behind every claim.

BuildScope is built for estimating and business-development teams at commercial mechanical
contractors. It is currently active for the Dallas–Fort Worth market.

---

## Core capabilities

- **Opportunity discovery** — a filterable, searchable directory of commercial projects with
  documented mechanical/HVAC activity, scoped to a market, city and project type.
- **Evidence-backed intelligence** — every material field carries its source, record identifier,
  source date and supporting excerpt. A field no source publishes stays empty and reads
  "Not verified" rather than being guessed.
- **Search and filtering** — keyword search with synonym and abbreviation expansion, plus filters
  for signal strength, city, project type, procurement status, value and freshness.
- **Monitoring and change alerts** — watch a project and get an alert when a tracked field or its
  permit set actually changes. No change means no alert.
- **Account pipeline** — save and watch opportunities, and track your own workflow stage (New,
  Reviewing, Watching, Target, Contacted, Pursuing, Closed out) separately from the project's
  procurement status.
- **Reports and market insight** — published customer briefs, plus market, city and project-type
  breakdowns drawn from stored records.

Every capability above is implemented and covered by the test suite. Planned work is labelled as
such in the documentation, not presented as live.

## Evidence and trust

BuildScope does not invent information. If a source does not publish a value, the value is
missing and is shown as such; nothing is imputed to fill a gap. Contradictions between sources
are preserved and flagged, not silently resolved.

Permit evidence is evidence of a permit, not proof of a sales opportunity. A permit does not by
itself mean a project is accepting bids, or that an HVAC package is available. Because no
configured source in this market publishes bid status, BuildScope never claims a bid is open: it
states the procurement status it can actually evidence, and no more. Completed projects are
excluded from discovery but remain reachable by direct link, where their status is stated
plainly. See [evidence and classification](docs/evidence-and-classification.md).

## How it works

1. **Collect** public records from city and county sources.
2. **Normalize and assemble** permits at one address into the underlying project.
3. **Verify and classify** each project against documented evidence, attaching provenance to
   every field.
4. **Present** the result through the web application and JSON API — from the same service the
   reports use, so a page and a report can never disagree.

## Quick start

```bash
git clone https://github.com/daveok16-arch/Buildscope.git && cd Buildscope
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export PYTHONPATH=src
export SECRET_KEY=dev-only-not-for-production

python -m oppintel.cli initdb                          # intelligence schema
flask --app oppintel.app.wsgi init-app                 # application tables
python -m oppintel.app.wsgi                            # http://127.0.0.1:5000
```

A fresh database is empty, so the site shows a real empty state rather than placeholder content.
Run an ingestion to populate it — see [getting started](docs/getting-started.md).

## Documentation

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Install, populate a database, run the app, run the tests |
| [Architecture](docs/architecture.md) | Layers, the service boundary, module map, design records |
| [Data sources](docs/data-sources.md) | Sources, coverage limits, the Dallas request mechanism |
| [Evidence and classification](docs/evidence-and-classification.md) | The evidence standard, classification, procurement |
| [API](docs/api.md) | The JSON API contract |
| [Deployment](docs/deployment.md) | Production configuration, Render, scheduled ingestion |
| [Operations](docs/operations.md) | The refresh daemon, logs, one-off refresh |
| [Security](docs/security.md) | Auth, authorization, CSRF, accounts and entitlements |
| [Testing](docs/testing.md) | How to run the suite and what it covers |

## Project status

BuildScope is in active development. The intelligence pipeline and the customer web application
are implemented and tested for the DFW market; coverage is deliberately partial and stated as
such. Payment, email delivery, and additional markets are not yet implemented and are reported as
unimplemented in the product surfaces rather than mocked. Repo-wide counts change as ingestion
runs, so current figures are read from the coverage and validation reports rather than hardcoded.

- Repository: <https://github.com/daveok16-arch/Buildscope>
- Project notes for contributors and agents: [`AGENTS.md`](AGENTS.md)

## Compliance

All data is collected from public, no-authentication government sources. Records may be
incomplete or superseded. Confirm all figures with the issuing jurisdiction before commercial
reliance.
