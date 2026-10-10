"""SQLite safety tests.

The refresh thread writes to the database while the web workers may also write (watch, save,
notes, migrations) and read. WAL and a busy timeout are what keep a writer from surfacing as a
"database is locked" error. These tests build a real database on disk and exercise real
concurrent readers against a real writer — no mocks.
"""

from __future__ import annotations

import threading
import time

from oppintel.db import Database


def test_database_enables_wal_and_a_busy_timeout(tmp_path):
    db = Database(tmp_path / "safety.db")
    db.init_schema()
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
    sync = db.conn.execute("PRAGMA synchronous").fetchone()[0]
    fk = db.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 30000
    # NORMAL (1) pairs with WAL: durable across an app crash, no fsync per commit.
    assert sync == 1
    assert fk == 1
    db.close()


def test_concurrent_readers_survive_a_writer(tmp_path):
    path = tmp_path / "concurrent.db"
    db = Database(path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO source (id, name, kind, market_coverage, updated_at) "
        "VALUES ('s1', 'Source', 'arcgis', 'DFW', '2026-10-09T00:00:00+00:00')"
    )
    db.conn.commit()
    db.close()

    errors: list[str] = []
    stop = threading.Event()

    def do_writes() -> None:
        writer = Database(path)
        try:
            for i in range(200):
                writer.conn.execute(
                    "INSERT INTO permit (source_id, natural_key, permit_number, "
                    "updated_at) VALUES ('s1', ?, ?, '2026-10-09T00:00:00+00:00')",
                    (f"k{i}", f"P{i}"),
                )
                writer.conn.commit()
        except Exception as exc:  # pragma: no cover - only on a locking failure
            errors.append(f"writer: {exc}")
        finally:
            writer.close()
            stop.set()

    def do_reads() -> None:
        reader = Database(path)
        try:
            while not stop.is_set():
                reader.conn.execute("SELECT COUNT(*) FROM permit").fetchone()
        except Exception as exc:  # pragma: no cover - only on a locking failure
            errors.append(f"reader: {exc}")
        finally:
            reader.close()

    threads = [threading.Thread(target=do_writes)]
    threads += [threading.Thread(target=do_reads) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors


def test_writer_lock_is_exclusive_across_threads(tmp_path):
    """A second pipeline writer waits for the first rather than starting a duplicate pass."""
    from oppintel.locks import writer_lock

    path = tmp_path / "locked.db"
    events: list[str] = []
    started = threading.Event()

    def first() -> None:
        with writer_lock(path, timeout=5):
            events.append("first-in")
            started.set()
            time.sleep(0.4)
            events.append("first-out")

    def second() -> None:
        started.wait()
        with writer_lock(path, timeout=5):
            events.append("second-in")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    # The second writer must not enter before the first leaves.
    assert events.index("second-in") > events.index("first-out"), events


def test_writer_lock_is_reentrant_in_one_thread(tmp_path):
    """A nested acquire (Pipeline.run -> assemble_and_classify) does not deadlock."""
    from oppintel.locks import writer_lock

    path = tmp_path / "reentrant.db"
    with writer_lock(path, timeout=5):
        with writer_lock(path, timeout=5) as inner:
            assert inner is True  # held, not blocked


def test_a_dead_process_releases_the_writer_lock(tmp_path):
    """Stale-lock recovery: a process killed while holding the lock does not wedge it.

    The lock is an ``flock`` on ``<db>.writelock``. The kernel releases an flock when the
    holding file descriptor is closed, and process death closes it, so a crashed ingest leaves
    no lock behind. This runs a real child process that takes the lock, confirms the parent
    cannot take it while the child lives, kills the child, and confirms the parent acquires it
    without waiting for the timeout.
    """
    import os
    import signal
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    from oppintel.locks import lock_path_for, writer_lock

    repo = Path(__file__).resolve().parents[1]
    path = tmp_path / "stale.db"
    lock_file = lock_path_for(path)
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(repo / 'src')!r})
            from oppintel.locks import writer_lock
            with writer_lock({str(path)!r}, timeout=5):
                print("held", flush=True)
                time.sleep(60)
        """)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "held"
        assert lock_file.exists()
        # While the child holds it, a second writer times out quickly rather than acquiring.
        from oppintel.locks import WriterLockBusy
        try:
            with writer_lock(path, timeout=0.3):
                raise AssertionError("lock was acquired while a live process held it")
        except WriterLockBusy:
            pass
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)

    # The child is dead; the lock must be free immediately (no stale file blocks it).
    t0 = time.monotonic()
    with writer_lock(path, timeout=5) as held:
        assert held is True
    assert time.monotonic() - t0 < 2.0, "acquiring a dead holder's lock should be immediate"


def test_concurrent_readers_and_web_writes_against_an_ingest_writer(tmp_path):
    """D1 soak: readers + short web-style writes survive a long writer with zero lock errors.

    Runs for ~60s against a real on-disk database. The ingest writer holds the write lock for
    a long stretch; readers and the short web writes must wait it out, never raise
    "database is locked".
    """
    path = tmp_path / "soak.db"
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
    ingest_writes = [0]
    web_writes = [0]
    reads = [0]
    DURATION = 60.0

    def ingest_writer() -> None:
        writer = Database(path)
        try:
            end = time.monotonic() + DURATION
            i = 0
            while time.monotonic() < end:
                # One long transaction that mirrors an assembly commit.
                writer.conn.execute("BEGIN")
                for _ in range(25):
                    writer.conn.execute(
                        "INSERT INTO permit (source_id, natural_key, permit_number, "
                        "updated_at) VALUES ('s1', ?, ?, '2026-10-09T00:00:00+00:00')",
                        (f"k{i}", f"P{i}"),
                    )
                    i += 1
                writer.conn.commit()
                ingest_writes[0] += 1
        except Exception as exc:  # pragma: no cover - only on a locking failure
            errors.append(f"ingest: {exc!r}")
        finally:
            writer.close()

    def reader() -> None:
        conn = Database(path)
        try:
            while not stop.is_set():
                conn.conn.execute("SELECT COUNT(*) FROM permit").fetchone()
                reads[0] += 1
                time.sleep(0.01)
        except Exception as exc:  # pragma: no cover
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
                web_writes[0] += 1
                n += 1
                time.sleep(0.05)
        except Exception as exc:  # pragma: no cover
            errors.append(f"web: {exc!r}")
        finally:
            conn.close()

    threads = [threading.Thread(target=ingest_writer)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    threads += [threading.Thread(target=web_write) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(DURATION)
    stop.set()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    # The workload actually ran (a green test with zero work would prove nothing).
    assert ingest_writes[0] > 0 and reads[0] > 0 and web_writes[0] > 0, (
        ingest_writes[0], reads[0], web_writes[0]
    )


# --- J5: crash-during-ingest queue safety --------------------------------------

def _spawn_killed_writer(path) -> None:
    """Start a child that opens a run, commits a permit, then hangs until killed."""
    import os
    import signal
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time, datetime as dt
            sys.path.insert(0, {str(repo / 'src')!r})
            from oppintel.db import Database
            from oppintel.models import Permit, normalize_address
            db = Database({str(path)!r})
            db.init_schema()
            db.conn.execute(
                "INSERT OR IGNORE INTO source (id, name, kind, market_coverage, updated_at) "
                "VALUES ('fort_worth_permits', 'City of Fort Worth', 'arcgis', 'current', "
                "'2026-10-09T00:00:00+00:00')")
            db.conn.commit()
            run_id = db.begin_run("fort_worth_permits")
            db.land_raw("fort_worth_permits", run_id, "PB-CRASH",
                        {{"Permit_No": "PB-CRASH"}}, "hash-crash",
                        dt.datetime.now(dt.timezone.utc))
            db.upsert_permit(Permit(
                source_id="fort_worth_permits", permit_number="PB-CRASH",
                natural_key="PB-CRASH", permit_type="Commercial Building Permit",
                permit_date=dt.date(2026, 9, 1), status="Issued",
                address="9 CRASH AVE", city="Fort Worth", state="TX",
                work_description="crashed ingest", is_commercial=True,
            ), normalize_address("9 CRASH AVE"))
            db.commit()
            print("committed", flush=True)
            time.sleep(60)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "committed"
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)


def test_a_killed_ingest_leaves_a_recoverable_run_and_no_stale_lock(tmp_path):
    """A SIGKILL mid-ingest must not wedge the writer lock or corrupt the run ledger.

    The killed child has committed a permit and an ingest_run row (status 'running') but never
    called ``finish_run``. The crash therefore leaves exactly the state a queue must recover
    from: an orphaned run. The parent proves (1) the writer lock is free immediately, (2) the
    committed permit survives, and (3) the orphaned run is visible so an audit can name it.
    """
    import time as _time

    from oppintel.locks import writer_lock

    path = tmp_path / "crash.db"
    _spawn_killed_writer(path)

    # (1) No stale lock: acquiring is immediate, not a timeout wait.
    t0 = _time.monotonic()
    with writer_lock(path, timeout=5) as held:
        assert held is True
    assert _time.monotonic() - t0 < 2.0, "a dead holder's lock should be free immediately"

    db = Database(path)
    # (2) The committed permit survived the kill.
    assert db.conn.execute(
        "SELECT COUNT(*) FROM permit WHERE natural_key = 'PB-CRASH'"
    ).fetchone()[0] == 1
    # (3) The orphaned run is recorded as running with no finished_at.
    orphan = db.conn.execute(
        "SELECT status, finished_at FROM ingest_run WHERE source_id = 'fort_worth_permits'"
    ).fetchall()
    assert len(orphan) == 1, orphan
    assert orphan[0][0] == "running" and orphan[0][1] is None, orphan
    db.close()


def test_an_orphaned_run_does_not_block_the_next_ingest(tmp_path):
    """After a crash, the run ledger still accepts a new run and can close the orphan."""
    path = tmp_path / "crash2.db"
    _spawn_killed_writer(path)

    db = Database(path)
    orphan_id = int(db.conn.execute(
        "SELECT id FROM ingest_run ORDER BY id LIMIT 1"
    ).fetchone()[0])
    # A new run gets a distinct id: the ledger is append-only, not blocked by the orphan.
    new_id = db.begin_run("fort_worth_permits")
    assert new_id != orphan_id
    # The orphan can be reconciled (closed as an error), which is the recovery path.
    db.finish_run(orphan_id, "error", error="interrupted")
    db.finish_run(new_id, "ok", rows_fetched=1, rows_landed=1, permits_created=1)
    still_running = db.conn.execute(
        "SELECT COUNT(*) FROM ingest_run WHERE status = 'running'"
    ).fetchone()[0]
    assert still_running == 0
    db.close()

