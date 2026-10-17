# Security

## Controls

| Control | Implementation |
|---|---|
| CSRF | Session-bound token, constant-time compare, required on every unsafe method. A parameterised test asserts every POST form in the templates carries one |
| Rate limiting | Per-client sliding window; the tightest allowance is on `/signin` and `/signup` |
| Session | `HttpOnly`, `SameSite=Lax`, `Secure` outside debug; the cookie holds only the user id |
| Headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and a same-origin CSP |
| Authorization | Anonymous ≠ FREE ≠ PRO/TEAM ≠ ADMIN. ADMIN is granted only by CLI |
| IDOR | Every account-scoped read and write is filtered by the authenticated user id; tests cover saves, notes and alerts across two accounts |
| Open redirect | `next` is accepted only when it is a same-site path |
| Password | PBKDF2-SHA256 with a per-password salt; never logged or echoed |
| Enumeration | Identical error for an unknown address and a wrong password |
| Error exposure | A 500 renders a generic page and records only the exception class, alongside the request path — no traceback, user id or body |

The rate limiter is per-process, so it is a defence in depth rather than the only one. The
documented [production deployment](deployment.md) puts a real limiter in front of the app.

## Sign-in surface

Email and password is the only sign-in method. The Google/Firebase integration was removed: the
`/auth/google` and `/auth/firebase-verify` routes, the `_verify_firebase_id_token` verifier, the
Firebase JS SDK, and the Firebase config loader are all gone, and the Content-Security-Policy no
longer allowlists any Google or Firebase origin.

The removal is why the `/auth/google` account-takeover bypass cannot recur: the route that accepted
an email address from a query parameter is not registered, rather than merely refusing to act.

A Firebase web `apiKey` (`AIza...`) still appears in this repository's git history from the earlier
integration. Removing the code does not un-expose a historical value, so if that project is still
in use, rotate the key in Firebase Console → Project settings → General → Web API key. No Firebase
service-account private key ever existed in the repository.

## Accounts, plans and entitlement

Plans exist so commercial tiers do not require a redesign later. Three things are kept apart:

- A **plan** is a catalogue entry in `plan` (`FREE`, `PRO`, `TEAM`), seeded from `entitlements.py`.
  It holds no customer data.
- A **subscription** is an account's recorded relationship to a plan, with a status. Only an active
  or trialing status grants the plan's features, so a lapsed subscription loses access
  automatically rather than waiting for someone to revoke it.
- An **entitlement** is the resolved answer to "may this account use this feature", computed from
  the stored subscription plus the access level.

**Payment is not implemented, and nothing here pretends otherwise.** There is no checkout, no
invoice and no way to buy a plan. What is implemented is the property that keeps the product
honest: no web request can grant a plan. The only mechanism is the operator command, which is what
an external billing integration would call once a subscription is real:

```bash
PYTHONPATH=src flask --app oppintel.app.wsgi set-plan estimator@example.com PRO
PYTHONPATH=src flask --app oppintel.app.wsgi set-plan estimator@example.com TEAM --status TRIALING
```

An account with no subscription is on `FREE` and holds no gated feature, and `/api/me` reports
exactly that. ADMIN is an internal operator level, not a paid tier: it grants every feature for
operations, and `is_paid` is deliberately false for it.
