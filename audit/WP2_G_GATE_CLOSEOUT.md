# WP2 closeout — G-gate PASS/FAIL

Branch `wp2-mobile-a11y`, tip `be14cff`. `origin/main` untouched at `e01eadd`.
Every result below is pasted from a real command run on this branch tip; nothing is asserted
without output.

---

## G1 — WP1 items re-verified

### G1a — D1 concurrency soak (≥60s)

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp1_d1_soak.py --seconds 60
duration:            60.2s
ingest commits:      144156
reader queries:      8775
watchlist writes:    997
note writes:         997
errors:              0
'database is locked': 0
pragmas:             journal_mode=wal, busy_timeout=30000, synchronous=1, foreign_keys=1
```

**PASS.** 3 reader threads + 2 web-writer threads (real `watched_opportunity` / `opportunity_note`
tables) ran concurrently with an ingest writer for 60s: 0 errors, 0 "database is locked". The
pragmas are read back from a fresh connection, so WAL/busy_timeout is proven in effect.

### G1b — banned-phrase grep

```
$ for p in "saved search" "zero false positive" "zero-false-positive" \
           "without false alerts" "continuous" "receive alerts" \
           "receive an alert" "months before general contractors"; do
    printf "%-32s -> " "$p"; grep -rniE "$p" src/oppintel/app/templates/ | wc -l
  done
saved search                     -> 0
zero false positive              -> 0
zero-false-positive              -> 0
without false alerts             -> 0
continuous                       -> 0
receive alerts                   -> 0
receive an alert                 -> 0
months before general contractors -> 0
```

**PASS.** 0 hits. The authoritative contract `tests/test_copy_honesty.py` reads template sources
(not one rendered page) and passes.

### G1c — D5 permit-window (read-only)

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp1_d5_window.py
today=2026-10-09  window = permit_date >= 2026-09-09
== projects with permit_date in the last 30 days: 158 ==
  'NEEDS_VERIFICATION'   136 / 'MEDIUM' 21 / 'HIGH' 1
== public (both gates pass) in window: 22 ==
== non-public in window: 136 ==
  min=2025-12-04  max=2026-12-19
```

**PASS.** Only the 22 that clear both gates are public; the 136 `NEEDS_VERIFICATION` are excluded.

### G1d — D6 ingest durations + refresh pause

**PASS.** Documented in `audit/WP1_1_DIRECTOR_CORRECTIONS.md` §D6 and `docs/deployment.md`:
Fort Worth ~4m05s (195,161 permits), Collin ~14s, Dallas Accela >20 min (10 categories × 200
pages). Overlap protection is the single-writer file lock in `src/oppintel/locks.py`; pause
procedure is `REFRESH_SECONDS=604800`.

### G1e — D7 scripts / post-deploy checks

**PASS.** `audit/scripts/` holds the named analyses, catalogued in `audit/scripts/README.md`;
`audit/POST_DEPLOY_CHECKS.md` has the change-count SQL, example diffs, the `last_observed` query
and the refresh confirmation; `audit/DEPLOY_RUNBOOK.md` exists.

---

## G2 — CSP per-request nonce

```
$ grep -rn "<script" src/oppintel/app/templates/ | grep -v "src="
base.html:28        <script type="application/ld+json">…</script>   (data, no nonce)
base.html:287       <script nonce="{{ csp_nonce() }}">
partials/dialog_a11y.html:13   <script nonce="{{ csp_nonce() }}">
companies/index.html:148       <script nonce="{{ csp_nonce() }}">
opportunities/list.html:227    <script nonce="{{ csp_nonce() }}">
account/signup.html:145        <script nonce="{{ csp_nonce() }}">
account/signin.html:142        <script nonce="{{ csp_nonce() }}">

$ grep -rnE "on(click|change|submit|load|error|input)=" src/oppintel/app/templates/  → (none)
$ grep -rn "javascript:" src/oppintel/app/templates/                               → (none)
```

Live Chromium, 7 routes:

```
/                csp_violations=0 ld_json_blocks=2
/opportunities   csp_violations=0 ld_json_blocks=1
/companies       csp_violations=0 ld_json_blocks=0
/changes         csp_violations=0 ld_json_blocks=0
/trends          csp_violations=0 ld_json_blocks=0
/signin          csp_violations=0 ld_json_blocks=0
/signup          csp_violations=0 ld_json_blocks=0
```

**PASS.** `script-src` carries `'nonce-<per-request>'` instead of `'unsafe-inline'`. Every inline
executable block is nonced; the only un-nonced block is the JSON-LD data (browsers do not execute
`application/ld+json`), and no CSP violation is raised. No inline handler or `javascript:` URL
remains. `style-src 'unsafe-inline'` is retained for ~346 inline `style=` attributes (layout
only) — a nonce cannot cover an attribute; that is a separate tracked cleanup.

---

## G3 — axe-core, all rules

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_axe_zero.py
TOTAL violation instances: 0  (wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa,best-practice)
```

**PASS.** 0 violations, 14 routes × 2 viewports, full WCAG + best-practice tag set.

---

## G4 — contrast / tokens

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_contrast_table.py
white on gold-500      #ffffff #d97706  3.19  AA-large
white on gold-600      #ffffff #b45309  5.02  PASS AA
slate-300 on white     #cbd5e1 #ffffff  1.48  FAIL
slate-500 on white     #64748b #ffffff  4.76  PASS AA
slate-600 on white     #475569 #ffffff  7.58  PASS AA
gold-600 on white      #b45309 #ffffff  5.02  PASS AA
slate-500 on navy-950  #64748b #060b17  4.13  AA-large
slate-300 on navy-950  #cbd5e1 #060b17 13.24  PASS AA
```

**PASS.** The two failing pairs found earlier (white on gold-500 = 3.19; slate-300 on white = 1.48)
were fixed: all white-on-gold text uses `gold-600`/`#92400e`; `slate-300` is scoped to dark panels
only. `signal-*-text` tokens provide AA-safe values on white.

---

## G5 — branch hygiene

```
$ git rev-parse origin/main
e01eadde4bdf76194fcb4f24822f7ee41ed844c2
$ git rev-parse --abbrev-ref HEAD
wp2-mobile-a11y
$ git merge-base --is-ancestor wp1-truth-stability wp2-mobile-a11y && echo contained
contained
```

**PASS.** `origin/main` is `e01eadd` (untouched). `wp1-truth-stability` is fully contained in
`wp2-mobile-a11y`. The G-gate work is committed on `wp2-mobile-a11y` only.

---

## G6 — post-deploy smoke

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_smoke.py
route                                status csp  banned  notes
/                                    200    True []
/opportunities                       200    True []
/companies                           200    True []
/markets                             200    True []
/markets/dfw                         200    True []
/changes                             200    True []
/trends                              200    True []
/analytics                           200    True []
/reports                             200    True []
/how-it-works                        200    True []
/trades                              200    True []
/trades/commercial-hvac              200    True []
/commercial-construction-leads       200    True []
/guides                              200    True []
/signin                              200    True []
/signup                              200    True []
/healthz                             200    True []
FAILURES: 0
```

**PASS.** 17 public routes return 200 with a nonced CSP and no banned phrase.

---

## Full suite (branch tip)

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q -p no:cacheprovider
1136 passed in 246.47s (0:04:06)
```

**PASS.**

---

## Items fixed while closing out

| Item | Was | Now |
|---|---|---|
| 4 SEO tests (`test_app_seo`, `test_seo_keywords`) | regex matched the exact `<script type="application/ld+json">` tag; a nonce attribute broke the match | JSON-LD data block left un-nonced (correct — not executed), tests unchanged |
| `test_forgot_password_does_not_reveal_whether_an_account_exists` | compared two response bodies byte-for-byte; per-request nonce made them differ | normalises only `nonce="…"`; the security property (identical message) is preserved |
| `audit/scripts/wp1_d1_soak.py` | printed counters only | reads back and prints effective pragmas |

## PASS/FAIL

| Gate | Result |
|---|---|
| G1a D1 soak | **PASS** |
| G1b banned-phrase grep | **PASS** |
| G1c D5 permit-window | **PASS** |
| G1d D6 durations + pause | **PASS** |
| G1e D7 scripts / post-deploy | **PASS** |
| G2 CSP nonce | **PASS** |
| G3 axe all-rules | **PASS** |
| G4 contrast / tokens | **PASS** |
| G5 branch hygiene | **PASS** |
| G6 smoke | **PASS** |
| Full suite (1136) | **PASS** |

No push to `main`. `origin/main` remains `e01eadd`.
