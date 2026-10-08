# BuildScope Phase 1A — Critical Authentication Remediation

**Scope:** Remove the demonstrated authentication bypass before any other work. No redesign, no
data-model change, no database migration, no broad refactor.

**Result:** The bypass is closed, verified Firebase authentication is the only Google path, existing
accounts cannot be claimed by email, password-reset tokens are no longer disclosed, 20 new security
regression tests pass, the full suite is 551 passing / 0 failing, and the original exploit was
reproduced live and now fails safely.

---

## 1. The vulnerable code path removed

**File:** `src/oppintel/app/main.py`, route `auth_google` (`/auth/google`, methods GET and POST).

Before, the handler did all of this with no verification:

```python
email = (request.args.get("email") or request.form.get("email") or "").strip()
google_id = f"google_{hashlib.sha256(email.encode()).hexdigest()[:16]}"
user, is_new = g.accounts.authenticate_or_link_google(email, google_id, display_name, ...)
_start_session(user.id)          # <-- authenticated session for an attacker-chosen email
```

A client-supplied `email` (query parameter or form field) was treated as proof of identity, a
`google_id` was *derived from that email*, and a session was started. `GET
/auth/google?email=<victim>` signed the caller in as the victim.

**Second, deeper flaw (also removed):** `AccountService.authenticate_or_link_google` in
`src/oppintel/app/accounts.py` looked the account up **by email** and attached the Google identity
to it. Even a genuinely verified Google identity carrying a victim's email would therefore have
claimed the victim's password account. Fixing only the route would have left a takeover via the
"verified" path; both layers were fixed.

**Third issue (also removed):** `forgot_password` generated a token and **rendered the reset link,
including the token, on the page** for any known email (`main.py` set `reset_link`, and
`templates/account/forgot_password.html` printed it). Anyone who knew a victim's email could read a
working reset token from the response.

## 2. The replacement authentication path

```
Browser (Firebase SDK, signInWithPopup)      templates/account/signin.html  (UNCHANGED)
        │  Google ID token (JWT)
        ▼
POST /auth/firebase-verify                   main.py: auth_firebase_verify
        │
        ▼
_verify_firebase_id_token(id_token)          main.py  (new, single trust boundary)
        │  1. Google tokeninfo  → verifies signature, iss, exp, email_verified
        │  2. Firebase Identity Toolkit → verifies uid against the project
        │  3. audience check (FIREBASE_PROJECT_ID or projectId)
        │  4. email_verified must be true
        ▼
AccountService.authenticate_or_link_google   accounts.py  (rewritten)
        │  match by google_id (trusted subject) first
        │  refuse to claim an existing account by email
        ▼
BuildScope session (Flask signed cookie, user id only)
```

- **`/auth/google` is now inert.** It authenticates nobody: it flashes an informational message
  and redirects to `/signin`. Any `email`, `display_name` or `next` supplied to it is ignored. It
  was kept as a redirect (not deleted) purely so existing links do not 404; it is not a
  compatibility authentication path.
- Identity is derived **only** from the verified token. No email parameter, form field,
  client-supplied uid, hidden field, or unsigned claim is trusted anywhere in the auth flow.

## 3. Account linking

`authenticate_or_link_google` now:

1. Matches an account by its linked **`google_id`** (the identity provider's own subject) — never
   by email — so a returning Google user signs in to the account they linked.
2. If no `google_id` matches but an account exists with that email, it **raises `AuthError`** and
   creates nothing. Knowing a victim's address is not sufficient to claim their account. Linking
   Google to an existing password account is an authenticated action and is deliberately not part
   of this flow.
3. If no account exists at all, it creates a fresh account bound to the verified `google_id`.

An attacker cannot reassign an existing account, and the victim's `google_id` and `password_hash`
are left untouched.

## 4. Password reset

- The route still generates a token for a real account, but **never renders, logs or returns it**.
  The template block that displayed `reset_link` was removed.
- Tokens remain: 1-hour expiry (`create_password_reset_token`), single-use
  (`reset_password_with_token` nulls `reset_token` and its expiry on success), and never written to
  logs. Delivery must be out of band (email) once a mailer exists.
- `verify_reset_token` rejects expired tokens; a used token is rejected because it is cleared.

## 5. Firebase configuration / rotation implications

`firebase-applet-config.json` was **not deleted and was not modified** — the frontend needs it to
initialise the Firebase SDK, and `firebase_config` is still injected into templates (unchanged).

- The committed **`apiKey` (`AIza…`) is a Firebase *web* API key, not a server secret.** It is
  shipped to browsers by design and serves only to identify the project (`firebase.initializeApp`)
  and to call the Identity Toolkit lookup endpoint. **It does not authenticate a user on its own**
  and it is no longer sufficient to mint a session. Treat it as public configuration.
- **No credential must be rotated to close the takeover**, because the takeover did not depend on
  the key. The key is still worth hardening: it should be restricted in Google Cloud Console by
  **HTTP referrer** (your domains) and **API restriction** (Identity Toolkit / Token Service), and,
  ideally, moved out of the repository into an environment variable
  (`FIREBASE_CONFIG_PATH` is already supported) with the file removed from version control.
- **Rotation, if you choose to rotate anyway:** rotate the *web API key* in Firebase Console
  (Project settings → General → Web API Key) and update `firebase-applet-config.json` / the
  `FIREBASE_CONFIG_PATH` target. **Do not rotate `oAuthClientId`** without also updating Firebase
  Authorized Domains, or Google sign-in will break. No Firebase *service-account* private key exists
  in the repository.
- **Repository history:** the repository has **no commits** (`git log` → "does not have any commits
  yet"), so there is no prior history containing the key to rewrite. If this tree is pushed, add
  `firebase-applet-config.json` to `.gitignore` first.

## 6. Tests added

**New file:** `tests/test_auth_security.py` — 20 tests.

| Req | Test(s) |
|---|---|
| A | `test_original_exploit_get_auth_google_email_does_not_authenticate`, `test_original_exploit_post_auth_google_email_does_not_authenticate`, `test_auth_google_redirects_to_signin_and_ignores_identity` |
| B | `test_any_known_email_does_not_authenticate` |
| C | `test_valid_firebase_token_authenticates_the_correct_account`, `test_valid_token_signs_in_returning_google_user` |
| D | `test_invalid_token_fails` |
| E | `test_expired_token_fails` |
| F | `test_tampered_token_fails` |
| G | `test_verified_google_identity_cannot_hijack_existing_account`, `test_service_refuses_email_match_for_existing_account` |
| H | `test_logout_invalidates_the_session` |
| I | `test_forgot_password_never_exposes_a_reset_token`, `test_reset_token_is_not_in_any_ordinary_page` |
| J | `test_expired_reset_token_is_rejected` |
| K | `test_used_reset_token_is_rejected` |
| — | `test_missing_token_fails`, `test_unverified_email_claim_is_refused`, `test_service_matches_returning_google_identity_by_uid`, `test_reset_changes_the_password_for_real` |

The Firebase network boundary is not bypassed: tests replace only the two outbound HTTP calls of
`_verify_firebase_id_token`, so audience, `email_verified` and subject validation all run for real.
No other test file was modified.

## 7. Tests executed and result

```
python -m pytest tests/test_auth_security.py -q      →  20 passed
python -m pytest tests/ -q                           →  551 passed, 0 failed (66.66s)
```

551 = 531 pre-existing + 20 new. **Zero regressions** in the intelligence or application suites.

> Environment note: `test_app_admin.py::test_admin_page_exposes_no_source_url_or_credential`
> fails if the shell exports `BASE_URL` (as my live-server shell did), because `AppConfig.base_url`
> reads it and the admin page then renders an absolute URL. This is not an application defect and
> not related to the auth change — it passes with `BASE_URL` unset (verified: 551 passed in a clean
> environment, and the same test passes in isolation with `env -u BASE_URL`).

## 8. Original exploit — reproduced live, now fails safely

Against the running server (fixed code):

```
1) victim signs up (email + password)              → 302, victim authenticated
2) GET  /auth/google?email=phase1a-victim@…        → 302 → /signin ; attacker /api/me: authenticated:false
3) POST /auth/google with victim email             → 403 (CSRF: no token, hostile cross-site form)
                                                     attacker /api/me: authenticated:false
4) POST /auth/firebase-verify with a forged token  → 401 {"ok":false} ; attacker: authenticated:false
5) POST /forgot-password                           → response contains no "Reset link", no token
```

The attacker is not authenticated in any case. **PASS.**

## 9. Unresolved / noted

- **Password-reset delivery is not implemented.** Tokens are generated but not emailed (there is no
  mailer). This is unchanged behaviour and out of Phase 1A scope; the security defect (token
  disclosed on-page) is fixed. With no mailer, reset is effectively operator-only.
- **Link-Google-to-existing-account is not offered.** Users who signed up with a password cannot
  currently link Google; that requires an authenticated linking flow and is intentionally deferred.
- **`firebase_config` is still passed to templates** (required by `signin.html` to initialise the
  SDK). It exposes the public web config, as before; that is expected Firebase behaviour.
- **Pre-existing, unrelated to this phase:** `/companies?q=…&city=…` returns HTTP 500 (recorded in
  `app_error`; a string-surgery bug in `companies.py`). Not touched — outside scope.
- **Data repaired:** two orphan `user_preference` rows (user_ids 8 and 9, left by the Phase 1 audit's
  raw-SQL account cleanup) were removed, restoring `app_user`/`user_preference` to 7/7. This was a
  data-state artefact of my own earlier audit action, not application code.

## 10. Exact git diff scope

The repository has **no baseline commit**, so scope is reported by changed-file set and mtimes
(all changed files carry a 01:41–01:44 timestamp; the entire intelligence layer remains at 23:58).

**Modified (3):**
| File | Change |
|---|---|
| `src/oppintel/app/main.py` | Removed `hashlib` import; hoisted `_load_firebase_config` to module level; added `FirebaseVerificationError`, `_expected_firebase_audience`, `_verify_firebase_id_token`; `/auth/google` made inert; `/auth/firebase-verify` uses the verifier; removed `reset_link` from `forgot_password` |
| `src/oppintel/app/accounts.py` | Rewrote `authenticate_or_link_google` to match by `google_id` and refuse claiming an existing account by email; extracted `_create_google_account` |
| `src/oppintel/app/templates/account/forgot_password.html` | Removed the reset-link/token display block |

**Added (1):**
| File | Change |
|---|---|
| `tests/test_auth_security.py` | 20 security regression tests (A–K + extras) |

**Untouched (verified by mtime):** `provenance.py`, `classify.py`, `normalize.py`, `assemble.py`,
`changes.py`, `procurement.py`, `service.py`, `db.py`, `search_index.py`, all `connectors/*`,
`security.py`, `entitlements.py`, `workflow.py`, `alerts.py`, `config/*`, and every other file.

**Runtime data:** `data/oppintel.db` grew via the normal background refresh loop
(now 3,062 projects / 11,966 permits) — expected automation behaviour, not a code change.

## 11. Acceptance criteria

- [x] `/auth/google` email takeover is impossible (live-verified)
- [x] verified Firebase authentication works (test C, plus live verifier path exercised)
- [x] existing accounts cannot be claimed (test G, service unit test)
- [x] password-reset tokens are not disclosed (test I, live-verified)
- [x] relevant security regression tests exist (`tests/test_auth_security.py`, 20 tests)
- [x] original exploit test fails safely (live reproduction + test A)
- [x] existing intelligence tests remain passing (551 passed, 0 failed)
- [x] no unrelated product/UI redesign occurred (3 files, all auth-related)
- [x] diff contains only security-related changes/tests
- [x] all modified files listed

**Phase 1A complete. Stopping here; not proceeding to Phase 2.**
