# Data sources and coverage

BuildScope ingests public, no-authentication government records. Coverage is deliberately partial
and is reported honestly rather than implied.

## Configured sources

| City | Source | Live? | Notes |
|---|---|---|---|
| Dallas | DallasNOW / Accela | Current | Publishes `Commercial Mechanical Permit` as a first-class record type. No declared value or floor area |
| Fort Worth | City Development Permits (ArcGIS) | Current | Publishes a `Mechanical` permit type |
| Collin County | Collin CAD (Socrata) | Current | Plano, Frisco, McKinney and others. Publishes no trade permits, so no Tier-1 evidence |

Sources are declared in `config/sources.yaml`; each connector lives in
`src/oppintel/connectors/`. Run `python -m oppintel.cli sources` to list what is configured and its
verified coverage.

## Coverage is not complete for DFW

Three cities are ingested; the rest of the metroplex is not. Two limits are worth stating plainly:

- **Pagination caps.** Three Dallas record types stop at a 300-page pagination safety cap, so
  their record counts are lower bounds. The coverage report marks these explicitly.
- **Missing fields.** Dallas publishes no declared project value or floor area, which limits how
  many Dallas projects can establish significance. `architect` and `developer` are published by no
  free source in this market and remain "Not verified" on effectively every record.

Historical Dallas feeds (Socrata, FY2023-24) are configured but disabled: their rows are too old to
represent sales opportunities. They are retained for historical pattern research only and must not
be presented as newly issued opportunities.

A **lower bound** in the coverage report means the crawl stopped at a safety cap, so the true count
is at least the figure shown and may be higher. No total should be read as complete for a
jurisdiction whose source is capped.

## The Dallas request mechanism

The Dallas Accela endpoint has a non-obvious contract — a windowed pagination behaviour and an
`Origin`/`Referer` requirement — which was determined by direct reconnaissance and is documented in
full in the [Dallas source record](engineering/dallas_source.md). The connector
(`connectors/dallas_accela_permits.py`) and `config/sources.yaml` both point at that record.

## Adding a source

Add a connector in `src/oppintel/connectors/`, register it in `connectors/__init__.py`, and add its
entry to `config/sources.yaml`. The connector is responsible for fetch and normalize only;
assembly, evidence and classification are shared across all sources.
