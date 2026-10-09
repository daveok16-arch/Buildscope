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

## Notes

* The shell scripts bind a local port and start a short-lived server; run them from the
  repository root. Each cleans its own `/tmp/a5*` directory at the start.
* These are audit instruments, not tests. The durable regression coverage lives in
  `tests/` (`test_stat_snapshot.py`, `test_trends.py`, `test_db_concurrency.py`).
