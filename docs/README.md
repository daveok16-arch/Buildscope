# BuildScope documentation

BuildScope is a construction opportunity intelligence platform for commercial HVAC and
mechanical contractors. It discovers relevant commercial projects from documented public
construction records and presents them with verifiable evidence.

Start with the [root README](../README.md) for the product overview. The guides below hold the
technical detail.

## Guides

| Guide | Read it when you need to… |
|---|---|
| [Getting started](getting-started.md) | install, populate a database, run the app, run the tests |
| [Architecture](architecture.md) | understand the layers, the service boundary, and the module map |
| [Data sources](data-sources.md) | know what is ingested, how complete coverage is, and how each source is fetched |
| [Evidence and classification](evidence-and-classification.md) | understand the evidence standard, classification, and procurement rules |
| [API](api.md) | call the JSON API |
| [Deployment](deployment.md) | deploy to production, and configure ingestion on a schedule |
| [Operations](operations.md) | run and automate the refresh daemon |
| [Security](security.md) | review authentication, authorization, accounts and entitlements |
| [Testing](testing.md) | run the suite and understand what it covers |

## Engineering records

Background design and audit documents are kept under [`engineering/`](engineering/) so they do
not crowd the repository root. They are historical records of how the platform was designed and
audited, not current product documentation.

## One authoritative document per subject

Each subject has exactly one home: this index points to the guide that owns it. If you find the
same explanation in two places, the guide named above wins.
