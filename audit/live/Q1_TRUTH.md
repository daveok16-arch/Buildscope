# Q1 — Live-truth status (M1–M6) and deploy verification

**Branch:** `main` · **local HEAD:** `204a2fc` · **remote main:** `204a2fc`
**Live:** https://buildscope-xppz.onrender.com
**Deploy marker:** `<meta name="buildscope-release">` = `RENDER_GIT_COMMIT[:7]` (`config.py:104`, `ops/start.sh:59`)
**Poll started:** 2026-10-10T23:37Z · **poller:** `/tmp/poll_release.py` → `/tmp/poll_release.log`

## 1. Deploy status

| Signal | Value | Meaning |
|---|---|---|
| Remote `refs/heads/main` | `204a2fceec60dc1f8e0031633e00f233805314a9` | push landed (`8f2e8ae..204a2fc`) |
| Live release marker | **absent** (polls 23:37Z–23:38Z = `none`) | live is serving a build **without** the marker — i.e. pre-WP3 |
| Live `/healthz` | `projects: 1789, permits: 6195` | live DB (self-seeded), different population from local fixture |
| Live `/companies` role control | absent | M5 control not present → **old build** |
| Local `origin/main` ref | `fc7b0e3` | deliberately untouched (standing rule) |

**Blocked:** no one can trigger the deploy from this sandbox — there is no `RENDER_API_KEY` / deploy-hook secret in the environment, and `render.yaml` sets `autoDeploy: true` (Render must observe the push itself). The feature commits this session (`a794bb4`, `8f2e8ae`, `204a2fc`) have sat on `main` for ~60 min without the live instance rebuilding, so **Render auto-deploy is not firing** (F-17 in the audit). Founder action required: Render Dashboard → the `buildscope-preview` service → **Manual Deploy → Deploy latest commit**, or add a deploy hook and a `RENDER_API_KEY` so CI can trigger it.

## 2. M1–M6 milestone status

Rendered from the WP3 local gates (`audit/live/M1_*`, `audit/wp3/probe.json`) and the full suite (`1276 passed`).

| ID | Milestone | Status | Evidence |
|---|---|---|---|
| M1 | City picker / mobile filter sheet | **PASS (local)** | `audit/live/M1_public_mobile_picker.png`; options 17, all 44 px (`M1_gates.txt`); `M1_probe.txt` comboboxes=5, keyboard search works. |
| M2 | Filter controls (`controls.js`) | **PASS (local)** | `src/oppintel/app/static/js/controls.js` loaded; no console errors desktop/mobile (`M1_probe.txt`). |
| M3 | Clean headline titles (`clean_title`) | **PASS (local)** | `clean_title('…It is16,297 SF')` → `'… It is 16,297 SF'`; project 560 verified; filter applied in 8 templates. |
| M4 | Domain source links + card overflow fix | **PASS (local)** | `domain_of(...)` → `mapit.fortworthtexas.gov`; 0 horizontal overflow on all 57 route/viewport combos (`audit/wp3/probe.json`). |
| M5 | Real company role counts | **PASS (local)** | `role_counts()` → `{'owner': 99}`; template shows `All Roles (99)` / `Property Owner / Developer (99)`; `test_role_counts_reports_only_present_roles` green. |
| M6 | Build/release marker | **PASS (wired), PENDING (live)** | marker code (`config.py:104`) present; surface absent live until the deploy completes. |

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
