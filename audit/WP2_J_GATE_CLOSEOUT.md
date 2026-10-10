# WP2 Director gate 3 (J-series) — PASS/FAIL closeout

Branch `wp2-mobile-a11y` @ `9a77887`. `origin/main` untouched at `e01eadd`. No push performed.
Every claim below is followed by the real command and its real output.

| Item | Verdict |
| --- | --- |
| J1a — revert `render.yaml` to main | **PASS** |
| J1b/c — hosting-neutral docs + safeguard | **PASS** |
| J2 — button option B | **PASS** |
| J3 — re-run evidence I1–I6 | **PASS** |
| J4 — local change-tracking proof | **PASS** |
| J5 — deploy-day runbook | **PASS** |
| J6 — idempotency + crash queue-safety tests | **PASS** |
| Full suite | **PASS** (1158 passed) |

---

## J1a — `render.yaml` restored to main

```
$ git --no-pager diff main -- render.yaml
[empty]

$ git rev-parse origin/main  ->  e01eadde4bdf76194fcb4f24822f7ee41ed844c2
$ git rev-parse main         ->  e01eadde4bdf76194fcb4f24822f7ee41ed844c2
```

The file is byte-identical to main: free plan, disk block commented out, no `/var/data` vars.

## J1b/c — host-neutral docs and safeguard

```
$ grep -niE "Starter plan|oppintel-data|/var/data|Free plan|paid plan" \
    AGENTS.md docs/deployment.md audit/DEPLOY_RUNBOOK.md
[no matches]

$ grep -n "Hosting requirements\|Persistent storage requirement\|one instance only\|2 GB RAM" \
    AGENTS.md docs/deployment.md
AGENTS.md:158:### Hosting requirements
AGENTS.md:170:* **one instance only** — SQLite is a single writer, so two instances must not share the file;
AGENTS.md:171:* at least **2 GB RAM** (the D6 full-seed measurement; see `docs/deployment.md`).
AGENTS.md:176:### Persistent storage requirement
docs/deployment.md:37:Hosting requirements:
docs/deployment.md:47:* **one instance only** ...
docs/deployment.md:48:* at least **2 GB RAM** and a **recommended 5 GB persistent disk** ...
```

Start script reads `FOREGROUND` before overwriting it and its failure message is host-neutral:
`ops/start.sh` lines 23–49 (`git diff main -- ops/start.sh` in the appendix). 20 deployment
tests pass (`20 passed in 0.70s`).

## J2 — button option B (dark navy on brand gold)

`src/oppintel/app/static/css/app.css:721-740`:

```css
.btn-primary { background: var(--gold-500); color: var(--navy-950) !important;
               border-color: var(--gold-500); }
.btn-primary:hover { background: var(--gold-400); border-color: var(--gold-400);
                     color: var(--navy-950) !important; }
.btn-primary:focus-visible { outline: 2px solid var(--navy-950); outline-offset: 2px; }
```

Contrast (WCAG relative-luminance formula, computed in-repo):

```
navy-950 #060b17 text on gold-500 #d97706 : 6.17   (AA small text)
navy-950 #060b17 text on gold-400 #f59e0b : 9.16   (hover)
navy-950 focus ring vs white page         : 19.66
```

Computed styles read from the live DOM (`audit/scripts/j2_button_shots.py`): default
`rgb(217,119,6)` bg / `rgb(6,11,23)` text; hover `rgb(245,158,11)`; focus
`rgb(6,11,23) 2px solid` — at 1440×900 and 390×844, hero / header / signup.

Zero-violation axe gate still green:

```
$ python -m pytest tests/test_accessibility.py -q -k axe
56 passed, 112 deselected in 55.10s
```

Screenshots: `audit/wp2/j2_{1440,390}_{header_get_access,home_hero,signup}.png`.

## J3 — re-run evidence I1–I6

* **I1** D5 reconciliation — `audit/scripts/h1_d5_reconcile.py`: G1c public = 22, Trends public
  = 22, delta explained (2 future-dated + 22 boundary rows).
* **I2** sizing — `audit/scripts/h2_estimate.py` + `audit/scripts/h2_assemble_rss.py` (wording
  host-neutral now: "a 512 MB host is NOT safe …").
* **I3** cache matrix — `audit/scripts/i3_cache_matrix.py`: every HTML/JSON route
  `private, no-store`; `/robots.txt` and `/sitemap.xml` `public, max-age=86400`; static assets
  `public, max-age=31536000`; CSP nonce differs per request.
* **I4** flake transfer — focus-trap tests, 20 loops under 8 busy `yes` processes:
  `I4 loops: PASS=20 FAIL=0 of 20`.
* **I6** banned-grep (`h3_banned_grep.py`), copy-honesty (`18 passed`), locks
  (`6 passed in 61.39s`).

## J4 — local change-tracking proof

`audit/scripts/j4_change_tracking.py` (assemble twice on a scratch copy where one permit's scope
text is amended):

```
pass1 change kinds: {'evidence_updated': 1, 'new_project': 1261}
pass2 change kinds: {'classification_changed': 1, 'evidence_updated': 5, 'new_project': 1261}
project_change rows with change_kind <> 'new_project' = 6
  #1263 project 64 | classification_changed/classification : 'MEDIUM' -> 'NEEDS_VERIFICATION'
  #1265 project 64 | evidence_updated/mechanical_evidence_tier : '2' -> None
  #1266 project 64 | evidence_updated/project_name : '...chiropractic office...' -> 'interior finish out; scope text amended'
```

## J5 — deploy-day runbook

`audit/DEPLOY_DAY.md` created: numbered checklist A–F (pre-deploy → provision → first boot →
verify → change-tracking proof → rollback) plus a "Moving to real hosting" section. No host,
plan or price is named. Deploy guidance follows any host meeting the requirements.

## J6 — idempotency + crash queue-safety

Idempotency (`audit/scripts/j6_idempotency.py`):

```
(a) assemble idempotency
    after pass 1 : permit 4190, raw 4239, project 1261, change 1262, snapshot 1261
    after pass 2 : unchanged
    after pass 3 : unchanged
(b) ingest idempotency (replay same source twice)
    after replay 1 : permit 4584, raw 4637, project 1275, change 1276, snapshot 1275
    after replay 2 : unchanged
```

Crash queue-safety, two new regression tests in `tests/test_db_concurrency.py`:

```
$ python -m pytest tests/test_db_concurrency.py -k "killed or orphaned"
2 passed, 6 deselected in 0.46s
```

They prove a `SIGKILL` mid-ingest leaves (1) the writer lock immediately free, (2) the committed
permit durable, (3) an orphaned `ingest_run` visible and closable — i.e. a recoverable queue, no
stale lock.

## Full suite

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
1158 passed in 244.55s
```

(+2 vs the 1156 from PART 1 = the two new crash-safety tests.)

## Appendix — command log

```
git --no-pager diff main -- render.yaml                 # -> empty
git rev-parse origin/main                               # -> e01eadd…
python audit/scripts/h2_estimate.py                     # sizing
python audit/scripts/h1_d5_reconcile.py /tmp/h1_readonly.db
python audit/scripts/i3_cache_matrix.py
python audit/scripts/j2_button_shots.py
python audit/scripts/j4_change_tracking.py
python audit/scripts/j6_idempotency.py
pytest tests/test_deployment.py -q                      # 20 passed
pytest tests/test_accessibility.py -q -k axe            # 56 passed
pytest tests/test_db_concurrency.py -k "killed or orphaned"   # 2 passed
pytest tests/ -q                                        # 1158 passed
```
