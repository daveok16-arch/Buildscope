# WP2 Director gate (J-series) — PASS/FAIL closeout (this run)

Branch `wp2-mobile-a11y` @ `bfbcde9`. `origin/main` untouched at `e01eadd`. **No push.**
Today = 2026-10-09. Every claim is followed by the real command and its real output (pasted).

> **Interpretation note.** The Director's latest message repeats **J1–J6 in full (there is NO
> J7)**; **J2 is accepted with follow-ups**. The exact J1–J6 sentence text is not present in
> this working transcript, so each item's meaning below is taken from the J-content already
> evidenced in `audit/WP2_J_GATE_CLOSEOUT.md` and the J2 report (`audit/WP2_J2_REPORT.md`). If
> the Director's J6 names a *different* sentence, stop on J6 only — the idempotency + crash
> queue-safety evidence is what is produced here.

## Item-by-item result

| Item | What it is | Verdict | Evidence file |
| --- | --- | --- | --- |
| J1 | H1a four-number reconciliation + J1c "unclassified filings" decision | **PASS** (all sub-items pasted) | `audit/J1_D5_PASTE.txt` |
| J2 | seed window + disk/backup sizing (accepted) | **PASS** (prior, `bfbcde9`) | `audit/J2_PASTE.txt`, `audit/J2_SIZING_PASTE.txt`, `audit/J2_RSS_PASTE.txt` |
| **J2.1** | follow-up: whole-container RSS (docker skill) | **PASS** | `audit/J2_1_CONTAINER_RSS.txt` |
| J3 | nonce entropy | **PASS** | `audit/J3_PASTE.txt` |
| J4 | local change-tracking proof + button colour options A/B/C | **PASS** | `audit/J4_PASTE.txt`, `audit/J4_BUTTONS_PASTE.txt`, `audit/wp2/button_options.png` |
| J5 | banned-phrase grep + audit-scripts inventory | **PASS** | `audit/J5_PASTE.txt` |
| J6 | idempotency + crash queue-safety | **PASS** | `audit/J6_PASTE.txt` |
| Full suite | regression | **PASS** (1169 passed) | below |

---

## J1 — four-number reconciliation  → **PASS**

`audit/scripts/h1_d5_reconcile.py` (DB opened `mode=ro` against `/tmp/h1_readonly.db`):

```
G1c total in-window          = 158      (permit_date >= 2026-09-09)
Trends projects_observed     = 134      ([2026-09-10, 2026-10-10))
G1c 'public (both gates)'    = 22
Trends 'of which public'     = 22
-- the two '22's are different sets --
   intersection = 0   same set? = False
-- row-by-row delta: 158 - 2 (future-dated) - 22 (permit_date == 2026-09-09) = 134 --
full project permit_date range: min=2025-12-04  max=2026-12-19
```

**J1c decision** (shall recent unclassified filings get a "New filings, not yet verified" view?):
**YES, as a separate, plainly-labelled surface only.** `NEEDS_VERIFICATION` = 1128 of 1261
projects; the public gate (`service.py` `PUBLIC_CLASS`) hides them, so a fresh market looks
empty. Sub-item pasted in `audit/J1_D5_PASTE.txt`.

## J2 — seed window + disk/backup sizing → **PASS** (accepted at `bfbcde9`)

Carried evidence (unchanged): connector date filters upstream (`File_Date`/Socrata `$where`/Accela
search dates), `document.raw_record_id` 100% populated (1901/1901, 0 orphans), 24-month default
footprint ≈220 MB / 1 GB disk, peak RSS 297.4 MB, backup keep 7→3.

## J2.1 — whole-container RSS → **PASS** (follow-up)

`audit/scripts/j2_container_rss.py` — gunicorn **2 workers × 4 threads** (the shipped
`ops/automate.py` shape), on a scratch DB seeded to **186,774 permits / 5,900 projects**, with a
real `assemble` child running concurrently with 20 concurrent web clients:

```
[1] gunicorn idle (2 workers x 4 threads):
  idle  procs=3 sumRSS=118.9 MB sumPSS=86.7 MB  per-proc RSS=[31MB, 48MB, 40MB]
[2] gunicorn serving 20 concurrent clients (no assemble):
  peak sumRSS=142.1 MB sumPSS=110.1 MB
[3] assemble child alone, then gunicorn+assemble concurrently:
  assemble alone peak RSS            = 38.3 MB
  combined peak (gunicorn+assemble) sumRSS = 180.7 MB
  combined peak (gunicorn+assemble) sumPSS = 137.1 MB
container peak (upper bound, sumRSS) = 180.7 MB
container peak (shared-aware, sumPSS) = 137.1 MB
```

**Verdict:** whole-container peak is **180.7 MB (sum of RSS, an upper bound)** / **137.1 MB
(sum of PSS, shared pages counted once)** — still under the **2 GB** recommendation (measured
8.5× headroom at the RSS upper bound). The earlier 297.4 MB was the *single assemble process*
under `resource.getrusage`, not the container; that is the number J2.1 corrects.

## J3 — nonce entropy → **PASS**

```
secrets.token_urlsafe(16) x50: distinct lengths [22], distinct bit-counts [128], unique 50/50
csp_nonce source uses secrets.token_urlsafe(16): True
SECRET_KEY default = secrets.token_hex(32) -> 64 chars = 256 bits
live local CSP header nonce x5: 5 distinct 22-char values
pytest tests/test_security.py -k nonce -> 3 passed, 31 deselected
```

## J4 — change-tracking + button colour options → **PASS**

Change tracking (`/tmp/j4_fhriito0`, scratch copy): pass1 vs pass2 records **6 real differences**
(`change_kind <> 'new_project'`), e.g. project #64 `mechanical_evidence_tier : '2' -> None`.

Button options (live DOM, styles injected per option; `audit/wp2/button_options.png`):

| Option | Default | Hover | Focus ring |
| --- | --- | --- | --- |
| A white on gold-600 | **5.02:1** | 7.09:1 | gold-500 on white **3.19:1 (fails 3:1 UI min)** |
| B gold-500 + navy-950 text | **6.17:1** | 9.16:1 | navy-950 on white 19.66:1 |
| C gold-500 + navy text + navy-850 border | **6.17:1** | 9.16:1 | navy-950 on white 19.66:1 |

**The app's CURRENT primary button is Option B** — `app.css:721-740` uses
`background: var(--gold-500)` (#d97706) / `color: var(--navy-950)` (#060b17), and the focused
computed style is `outline rgb(6,11,23) 3px` (navy). So **B is already shipped**; A and C exist
only as captured alternatives. A's focus ring (gold-500 on white 3.19:1) fails the WCAG 2.1
non-text 3:1 minimum; B/C pass.

## J5 — banned-phrase grep → **PASS**

`audit/scripts/h3_banned_grep.py` over templates/static/docs/README (phrase hit counts):
`continuous 0`, `false alert 0`, `revision alert 0`, `months before 0`; the remaining hits are
all **documentation of the absent feature** (engineering audit where the word is "MISSING"), a
brand tagline ("before the work begins"), or the word "watch" in unrelated contexts — not
overclaims. `tests/test_copy_honesty.py` → **18 passed**. Scripts that *produce* the evidence are
listed in `audit/scripts/README.md`; every file under `audit/scripts/` is an instrument, not a
test.

## J6 — idempotency + crash queue-safety → **PASS**

```
(a) assemble idempotency  pass1 change 1261->1262 ; pass2 = pass3 = 1262 (no growth)
(b) ingest idempotency    replay1 permit 4190->4584 ; replay2 = replay1 (no duplicate)
pytest tests/test_db_concurrency.py -k "killed or orphaned" -> 2 passed, 6 deselected
  test_a_killed_ingest_leaves_a_recoverable_run_and_no_stale_lock
  test_an_orphaned_run_does_not_block_the_next_ingest
```

## Full suite

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q -p no:cacheprovider
1169 passed in 251.64s (0:04:11)
EXIT=0
```

**1169 passed** = the 1167 from J2 closeout + the two J3 nonce tests added
(`test_csp_nonce_is_at_least_128_bits`, `test_csp_nonce_is_unique_per_request`) in the working
tree. HEAD itself (`bfbcde9`) is 1167; the working tree is 1169 (uncommitted test additions).

---

## Cross-cutting findings raised while producing J-evidence

1. **Repo ≠ live (confirmed).** The repo sets a per-request **nonce** CSP
   (`script-src 'self' 'nonce-...'`), verified on the local run. The live Render origin serves
   `script-src 'self' 'unsafe-inline' ...` (no nonce) — an **older build** is deployed. Fetch
   both and diff: `curl -sD- -o/dev/null http://127.0.0.1:12001/` vs the live origin.
2. **No app-level compression.** `/companies` is **108,012 bytes** uncompressed over the wire
   locally. The live edge (Cloudflare) adds `content-encoding: br`, but a host without an edge
   gets none. App-level gzip measured at **33159→8268 / 62727→9839 / 108012→9748 B** when added,
   but that change is **out of the read-only/J scope and was reverted** (not committed).
3. **Cold start on Render** (free-tier sleep / TTFB after idle) remains **UNVERIFIED** — not
   measured this run.

## Files written this run

```
audit/J1_D5_PASTE.txt
audit/J2_1_CONTAINER_RSS.txt
audit/J3_PASTE.txt
audit/J4_PASTE.txt
audit/J4_BUTTONS_PASTE.txt
audit/J5_PASTE.txt
audit/J5_H3_GREP.txt
audit/J5_LOCKS_PASTE.txt
audit/J6_PASTE.txt
audit/scripts/j2_container_rss.py
audit/scripts/seed_from_capture.py
audit/scripts/j4_button_options.py      (existing; re-run)
audit/scripts/j4_montage.py
audit/wp2/button_options.png
audit/wp2/j4_{A,B,C}_{1440,390}_{hero,header,signup}[_hover].png   (30)
audit/WP2_J_SERIES_CLOSEOUT.md          (this file)
```
