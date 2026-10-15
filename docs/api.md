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
| `/api/project/<id>/analyst` | GET | The grounded AI analyst brief for one project. **Operator (ADMIN) only**: `401` unauthenticated, `403` for a signed-in non-operator |
| `/api/me` | GET | Identity and the resolved entitlement. Reports only what the stored subscription grants |

## Authorization

Account-scoped endpoints return `401` when there is no session, and every write is filtered by the
authenticated user id, so an id from another account resolves to nothing.

The operator endpoints (`/api/project/<id>/analyst`) return JSON status codes rather than an HTML
redirect, so a client is never sent to a sign-in form it cannot render.

## The analyst endpoint

`/api/project/<id>/analyst` returns a model-written narrative that was grounded in the stored
evidence and then re-checked against it. The payload is honest by construction:

- `status` is `AVAILABLE` (the narrative passed every check), `UNVERIFIED` (the model answered but
  said something the evidence does not license, so `summary` is the deterministic evidence-only
  fallback) or `UNAVAILABLE` (no model is configured, or the model could not be reached).
- `verified` is `true` only when `status` is `AVAILABLE` and there are no `verification_issues`.
- `unsupported_fields` names every field the record does not state, so a consumer never has to
  infer absence from a `null`.
- `caveat` always carries the permit-evidence notice; the endpoint never asserts a bid is open.

The narrative may be written by either configured provider — Gemini or xAI's Grok. `model` records
which model produced the prose. The provider is a transport choice only: both are constrained to
the same JSON schema and both pass through the same verification, so the provider does not change
what the analyst is permitted to claim.

## Machine-readable honesty

- A missing value is `null`, not a placeholder string.
- `/api/statistics` reports counts of stored rows, not estimates.
- `/api/trends` returns each metric with the definition that produced it, so a number is never
  shown without its meaning.
- `/api/me` reports exactly what the stored subscription grants; an account with no subscription
  holds no gated feature.
