# Q1 — Live-truth status (M1–M6) and deploy verification

**Branch:** `main` · **local HEAD:** `6f6d8fb` · **remote main:** `6f6d8fb`
**Live:** https://buildscope-xppz.onrender.com
**Deploy marker:** `<meta name="buildscope-release">` = `RENDER_GIT_COMMIT[:7]` (`config.py:104`, `ops/start.sh:59`)

## 1. Deploy status — RESOLVED (manual deploy, 2026-10-10T23:51Z)

Auto-deploy did **not** fire for the WP3 commits that had been on `main` since 20:41Z
(the live service stayed pinned at `fc7b0e3`). Using the Render API
(`RENDER_API_KEY`), a deploy was triggered explicitly:

```
POST /v1/services/srv-db3u3jij9qps73ffa4vg/deploys  {"clearCache":"do_not_clear"}
-> 201  deploy dep-db5cv47lk1mc739idn70  commit 6f6d8fb  status build_in_progress
-> 23:52:47Z  status live
```

| Signal | After deploy | Meaning |
|---|---|---|
| Service | `srv-db3u3jij9qps73ffa4vg` ("Buildscope", web_service, branch `main`, plan free) | correct service |
| Live release marker | `6f6d8fb98e977aae412faf9050f6a613dc106e80` | **new build live** |
| Live `/healthz` | `status ok`, `projects 1789`, `permits 6195` | self-seeded after ~2.5 min (diskless free plan rebuilds each deploy) |
| `/companies` role control | `All Roles (127)` / `Property Owner / Developer (127)` | **M5 live** |
| `/opportunities/…` source link | `<span class="src-domain">aca-prod.accela.com</span>` | **M4 live** |
| `/opportunities` clean title | `It is 16,297 SF` | **M3 live** |
| `/static/js/controls.js` | `200`, 10521 bytes | **M1/M2 assets live** |
| All 16 listed routes | `200` | no route regression |

The local `origin/main` ref remains `fc7b0e3` (deliberately untouched, standing rule).

Note: the free plan has no disk, so every deploy starts empty and self-seeds — the
`empty` → `ok` transition (~2–3 min) is expected, not an error.

### Follow-up (ops)

Auto-deploy is enabled in the blueprint yet did not observe the push. Until the
cause is understood, deployments of `main` need an explicit trigger: either a
Render deploy hook called from CI, or `POST /v1/services/<id>/deploys` with an
API key. Without that, `main` can drift ahead of the live site (it did for ~70
min this session).

## 2. M1–M6 milestone status

Rendered from the WP3 local gates (`audit/live/M1_*`, `audit/wp3/probe.json`) and the full suite (`1276 passed`).

| ID | Milestone | Status | Evidence |
|---|---|---|---|
| M1 | City picker / mobile filter sheet | **PASS (live)** | `controls.js` served live (200, 10521 b); options 17, all 44 px (`M1_gates.txt`); `M1_probe.txt` comboboxes=5, keyboard search works. |
| M2 | Filter controls (`controls.js`) | **PASS (live)** | `/static/js/controls.js` 200 live; no console errors desktop/mobile (`M1_probe.txt`). |
| M3 | Clean headline titles (`clean_title`) | **PASS (live)** | live `/opportunities` renders `It is 16,297 SF`; project 560 verified; filter applied in 8 templates. |
| M4 | Domain source links + card overflow fix | **PASS (live)** | live detail renders `<span class="src-domain">aca-prod.accela.com</span>`; 0 horizontal overflow on all 57 route/viewport combos (`audit/wp3/probe.json`). |
| M5 | Real company role counts | **PASS (live)** | live `/companies` shows `All Roles (127)` / `Property Owner / Developer (127)`; `test_role_counts_reports_only_present_roles` green. |
| M6 | Build/release marker | **PASS (live)** | live `<meta name="buildscope-release">` = `6f6d8fb9…` (`config.py:104`). |

**Gate summary (local):** suite 1276 passed · axe 0 violations / 16 routes · 0 horizontal overflow / 57 combos · tap targets 200 sub-44px (tracked F-10) · CSP + nonce present · no secrets tracked.

## 3. Post-deploy verification script

Run after the Render rebuild completes. Success = exit 0.

```bash
python3 - <<'PY'
import re, sys, urllib.request
B = "https://buildscope-xppz.onrender.com"
def get(p):
    return urllib.request.urlopen(B + p, timeout=30).read().decode("utf-8", "ignore")
home = get("/")
rel = re.search(r'buildscope-release" content="([^"]*)"', home)
rel = rel.group(1) if rel else ""
print("release marker:", rel or "MISSING")
checks = {
    "marker present": bool(rel),
    "M5 role counts on /companies": "All Roles (" in get("/companies"),
}
for k, v in checks.items():
    print(("PASS" if v else "FAIL"), k)
sys.exit(0 if all(checks.values()) else 1)
PY
```

Expected `release marker` value after deploy: the sha of `204a2fc` (or the deploy's `RENDER_GIT_COMMIT`).

## 4. Open items carried from the audit

* F-17 deploy stall (this doc).
* F-01/F-02 stat-consistency (home vs `/healthz` vs Trends) — the single highest-risk item.
* F-10 200 sub-44px tap targets.
* F-08 GC/architect data absent dataset-wide.
