#!/usr/bin/env python3
"""D1 evidence: concurrent readers + web-style writes against a long ingest writer.

Runs for a fixed duration (default 60s) against a real on-disk SQLite database and reports
whether any operation raised "database is locked". Prints the counters so the run proves it did
work, not just that nothing happened.

    PYTHONPATH=src python audit/scripts/wp1_d1_soak.py --seconds 60
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from oppintel.db import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="d1soak-"))
    path = tmp / "soak.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    db.conn.execute(
        "INSERT INTO source (id, name, kind, market_coverage, updated_at) "
        "VALUES ('s1', 'Source', 'arcgis', 'DFW', '2026-10-09T00:00:00+00:00')"
    )
    db.conn.commit()
    db.close()

    errors: list[str] = []
    stop = threading.Event()
    counters = {"ingest_commits": 0, "reads": 0, "web_writes": 0}
    duration = args.seconds

    def ingest_writer() -> None:
        writer = Database(path)
        try:
            end = time.monotonic() + duration
            i = 0
            while time.monotonic() < end:
                writer.conn.execute("BEGIN")
                for _ in range(25):
                    writer.conn.execute(
                        "INSERT INTO permit (source_id, natural_key, permit_number, "
                        "updated_at) VALUES ('s1', ?, ?, '2026-10-09T00:00:00+00:00')",
                        (f"k{i}", f"P{i}"),
                    )
                    i += 1
                writer.conn.commit()
                counters["ingest_commits"] += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ingest: {exc!r}")
        finally:
            writer.close()

    def reader() -> None:
        conn = Database(path)
        try:
            while not stop.is_set():
                conn.conn.execute("SELECT COUNT(*) FROM permit").fetchone()
                counters["reads"] += 1
                time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {exc!r}")
        finally:
            conn.close()

    def web_write() -> None:
        conn = Database(path)
        try:
            n = 0
            while not stop.is_set():
                conn.conn.execute(
                    "INSERT INTO analytics_event (event_name, created_at) VALUES ('soak', ?)",
                    (f"2026-10-09T00:00:{n % 60:02d}+00:00",),
                )
                conn.conn.commit()
                counters["web_writes"] += 1
                n += 1
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"web: {exc!r}")
        finally:
            conn.close()

    threads = [threading.Thread(target=ingest_writer)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    threads += [threading.Thread(target=web_write) for _ in range(2)]
    started = time.monotonic()
    for t in threads:
        t.start()
    time.sleep(duration)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - started

    locked = [e for e in errors if "database is locked" in e]
    print(f"duration:            {elapsed:.1f}s")
    print(f"ingest commits:      {counters['ingest_commits']}")
    print(f"reader queries:      {counters['reads']}")
    print(f"web writes:          {counters['web_writes']}")
    print(f"errors:              {len(errors)}")
    print(f"'database is locked': {len(locked)}")
    for e in errors:
        print("  ", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
