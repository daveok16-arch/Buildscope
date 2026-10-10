# WP1.1 verification scripts

Read-only inspection scripts used to reconcile the WP1.1 figures (A1–A7). They open the
database, run `SELECT`/`COUNT` queries, and start the app from a **copy** of the database.
None of them write to the shipped `data/oppintel.db`.

## Prerequisites

Every script needs the vendored dependencies and the package on the path:

```bash
export PYTHONPATH="vendor/python:src"
```

The Python scripts read `/tmp/populated.db`. That path is a scratch copy of the shipped
database; create it first (this never mutates `data/oppintel.db`):

```bash
cp data/oppintel.db /tmp/populated.db
```

The shell scripts copy the database themselves and use `/tmp/a5*` working directories.

## Scripts

| Script | What it proves | How to run |
| --- | --- | --- |
| `a4.py` | Groups permits by `(source_id, permit_number)` to size the duplicate surface | `PYTHONPATH="vendor/python:src" python audit/scripts/a4.py` |
| `a4_final.py` | Reconciles those groups against WP1's seven true-duplicate groups | `PYTHONPATH="vendor/python:src" python audit/scripts/a4_final.py` |
| `a4_rows.py` | Prints the permits behind each disputed group | `PYTHONPATH="vendor/python:src" python audit/scripts/a4_rows.py` |
| `a4b.py` | Duplicate-group histogram by address and date spread | `PYTHONPATH="vendor/python:src" python audit/scripts/a4b.py` |
| `a4c.py` | Same histogram, distinct permit-number only | `PYTHONPATH="vendor/python:src" python audit/scripts/a4c.py` |
| `a4d.py` | Resolves the seven true-duplicate groups | `PYTHONPATH="vendor/python:src" python audit/scripts/a4d.py` |
| `a4e.py` | The seven groups as `(permit_number, address_key, permit_date)` triples | `PYTHONPATH="vendor/python:src" python audit/scripts/a4e.py` |
| `a7.py` | `project_change` composition: total and per `change_kind` | `PYTHONPATH="vendor/python:src" python audit/scripts/a7.py` |
| `a7b.py` | Starts the service against a populated DB and reads the change feed | `PYTHONPATH="vendor/python:src" python audit/scripts/a7b.py` |
| `a5.sh` | Starts the app from a copied DB three times, restarting between runs | `bash audit/scripts/a5.sh` |
| `a5_final.sh` | Three-process start on the WP1 branch (`RENDER=true`) | `bash audit/scripts/a5_final.sh` |
| `a5_firstboot.sh` | Empty mounted disk must start (`200`, `status:"empty"`) and self-seed | `bash audit/scripts/a5_firstboot.sh` |
| `a5_sidebyside.sh` | Captures the headline figures from three separate runs | `bash audit/scripts/a5_sidebyside.sh` |
| `a5b.sh` | Same, printing each run's headline figures for comparison | `bash audit/scripts/a5b.sh` |
| `a5c.sh` | First boot, empty disk vs already-populated disk | `bash audit/scripts/a5c.sh` |
| `a5d.sh` | Start against an explicitly configured `OPPINTEL_DB` path | `bash audit/scripts/a5d.sh` |
| `c1_jurisdiction.py` | Prints the configured city list, the observed in-market count and the excluded out-of-market city, so the 19/15/14/18 figures can be told apart | `PYTHONPATH="vendor/python:src" python audit/scripts/c1_jurisdiction.py` |
| `wp1_d1_soak.py` | Runs concurrent readers and web writes against an ingest writer for 60s and reports lock errors (D1) | `PYTHONPATH="vendor/python:src" python audit/scripts/wp1_d1_soak.py` |
| `wp1_d5_trends.py` | Trends vs the public feed: last-30-day counts by classification/procurement, the gate for non-public rows, 10 samples, and the public permit-date month spread (D5) | `PYTHONPATH="vendor/python:src" python audit/scripts/wp1_d5_trends.py` |

## WP2 UI verification scripts

These need the app already running on `http://127.0.0.1:12000` (see the local run note in
`AGENTS.md`) and Playwright + Chromium. They are read-only: they load pages and read the DOM.

| Script | What it proves | How to run |
| --- | --- | --- |
| `wp2_overflow.py` | Horizontal-overflow check (`scrollWidth > innerWidth`) over the public routes at 390px | `python audit/scripts/wp2_overflow.py` |
| `wp2_overflow2.py` | Same check over a wider route list | `python audit/scripts/wp2_overflow2.py` |
| `wp2_tap_targets.py` | Lists interactive controls below 44px on the feed routes | `python audit/scripts/wp2_tap_targets.py` |
| `wp2_tap_targets_routes.py` | 44px audit across every public route with touch emulation | `python audit/scripts/wp2_tap_targets_routes.py` |
| `wp2_axe_all.py` | axe-core scan; prints only serious/critical violations | `python audit/scripts/wp2_axe_all.py` |
| `wp2_axe_run.py` | axe-core scan with the full violation dump | `python audit/scripts/wp2_axe_run.py` |
| `wp2_measure.py` | Layout probe: element widths/overflow at a given viewport | `python audit/scripts/wp2_measure.py` |
| `wp2_header_probe.py` | Reports the computed style of the header controls at 390px | `python audit/scripts/wp2_header_probe.py` |
| `wp2_drawer_focus.py` | Opens the mobile drawer and reports the focused element (focus-management check) | `python audit/scripts/wp2_drawer_focus.py` |
| `wp2_screenshots.py` | Captures full-page screenshots to `audit/wp2/` at three viewports | `python audit/scripts/wp2_screenshots.py` |

## WP1 render scripts

| Script | What it proves | How to run |
| --- | --- | --- |
| `wp1_render_check.py` | Renders routes through the Flask test client against a DB copy and greps the HTML for a figure | `python audit/scripts/wp1_render_check.py <repo-root> <db-path>` |
| `wp1_render_routes.py` | Renders the public routes and prints the headline figures each shows | `python audit/scripts/wp1_render_routes.py` |

## Notes

* The shell scripts bind a local port and start a short-lived server; run them from the
  repository root. Each cleans its own `/tmp/a5*` directory at the start.
* These are audit instruments, not tests. The durable regression coverage lives in
  `tests/` (`test_stat_snapshot.py`, `test_trends.py`, `test_db_concurrency.py`).
