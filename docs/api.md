# API

The JSON API is the machine-readable form of the same `OpportunityService` the pages use, so a
page and an API response can never disagree about a project. A missing value is `null`, never the
string "Not verified" — the machine-readable document must not assert that a party named
"Not verified" exists.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/opportunities` | GET | List/search. Same filters as the directory, plus `min_value`, `max_value`, `mechanical_only`, `freshness_days` |
| `/api/opportunities/<slug>` | GET | One opportunity, with sources, per-field verdicts, recorded changes and match reasons |
| `/api/markets`, `/api/trades` | GET | Configured markets and trades |
| `/api/statistics` | GET | Market, city and project-type counts. Every figure is a count of stored rows |
| `/api/trends` | GET | Market trend metrics, each carrying its own definition |
| `/api/saved` | GET | The account's saved opportunities. `401` when unauthenticated, never an empty list |
| `/api/saved/<id>` | POST/DELETE | Save or unsave |
| `/api/watching`, `/api/watching/<id>` | GET, POST/DELETE | The account's watched opportunities |
| `/api/pipeline`, `/api/pipeline/<id>` | GET, POST/DELETE | The account's workflow entries |
| `/api/notes/<id>` | GET/POST | The account's private notes on an opportunity |
| `/api/alerts`, `/api/alerts/<id>/read` | GET, POST | Event-driven alerts, with the change that caused each one |
| `/api/me` | GET | Identity and the resolved entitlement. Reports only what the stored subscription grants |

## Authorization

Account-scoped endpoints return `401` when there is no session, and every write is filtered by the
authenticated user id, so an id from another account resolves to nothing.

## Machine-readable honesty

- A missing value is `null`, not a placeholder string.
- `/api/statistics` reports counts of stored rows, not estimates.
- `/api/trends` returns each metric with the definition that produced it, so a number is never
  shown without its meaning.
- `/api/me` reports exactly what the stored subscription grants; an account with no subscription
  holds no gated feature.
