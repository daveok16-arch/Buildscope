"""A cross-process single-writer lock for the heavy pipeline jobs.

SQLite (even in WAL mode) serializes writers, and the refresh loop, the CLI ingest and a
one-time seed are all long-running writers over the *same* file. Running two of them at once
does not corrupt anything, but it wastes work (both re-fetch and re-assemble the same source
pages) and it holds the write lock for long stretches, which is what a web request's short
write then has to wait behind.

WAL + ``busy_timeout`` already make a short web write wait rather than fail. This lock is the
coarser guarantee: at most one *pipeline* writer (ingest/assemble/seed/refresh) runs at a time,
across processes. A second pipeline writer blocks until the first finishes instead of starting
a duplicate pass.

Implementation: an advisory ``flock`` on a sibling lock file. It is released automatically when
the process exits or the file handle closes, so a crashed job cannot leave the lock stuck. On a
platform without ``fcntl`` (not our target) it degrades to a no-op rather than failing.

Two mechanisms, because a job spans several subprocesses:

* :func:`writer_lock` — the advisory lock, used by the in-process writers (``Pipeline.run``,
  ``assemble``, ``build-search-index``, ``monitor``). Re-entrant per thread, so a nested acquire
  (``Pipeline.run`` calling ``assemble_and_classify``) is a no-op rather than a deadlock.
* :func:`job_lock` — used by the *supervisor* (``refresh_once`` / a seed run) around the whole
  multi-step job. The child processes are told the lock is already held (``OPPINTEL_LOCK_HELD=1``)
  so they do not take it again and deadlock against their own parent.
"""

from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # POSIX only; Render and the container are Linux.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

#: How long a caller waits for the pipeline lock before giving up. A full refresh (ingest +
#: assemble + index + monitor) can take many minutes, so a seed that arrives mid-refresh
#: waits rather than aborting.
DEFAULT_LOCK_TIMEOUT = float(os.environ.get("OPPINTEL_LOCK_TIMEOUT", "3600"))

#: Set in the environment of a child process whose parent already holds the job lock, so the
#: child does not re-acquire it (which would block against its own parent).
HELD_ENV = "OPPINTEL_LOCK_HELD"

#: Per-thread acquisition depth, keyed by lock path, so a nested acquire in the same thread is
#: re-entrant instead of deadlocking on a second open file description.
_local = threading.local()


def _depth() -> dict[str, int]:
    d = getattr(_local, "depth", None)
    if d is None:
        d = {}
        _local.depth = d
    return d


def lock_path_for(db_path: str | Path) -> Path:
    """The lock file that guards writers of ``db_path``.

    A sibling of the database (``<db>.writelock``) so two deployments pointed at the same
    database file share one lock, and a deployment pointed at a different file does not.
    """
    return Path(str(db_path) + ".writelock")


class WriterLockBusy(RuntimeError):
    """Raised when the pipeline lock could not be acquired inside the timeout."""


@contextmanager
def writer_lock(
    db_path: str | Path,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    poll: float = 0.5,
) -> Iterator[bool]:
    """Hold the single pipeline-writer lock for ``db_path`` for the duration of the block.

    Re-entrant per thread, and a no-op in a process that inherited the job lock
    (``OPPINTEL_LOCK_HELD=1``). Yields ``True`` when the lock is held by this call chain and
    ``False`` when locking is unavailable; the body always runs. Raises :class:`WriterLockBusy`
    if another process holds it for longer than ``timeout``.
    """
    path = lock_path_for(db_path)
    key = str(path)

    if fcntl is None or os.environ.get(HELD_ENV) == "1":  # pragma: no cover - non-POSIX / child
        yield False
        return

    depths = _depth()
    if depths.get(key, 0) > 0:  # re-entrant: this thread already holds it
        depths[key] += 1
        try:
            yield True
        finally:
            depths[key] -= 1
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise WriterLockBusy(
                        f"another pipeline writer holds {path} (waited {timeout:.0f}s)"
                    ) from exc
                time.sleep(poll)
        # Record the holder for a human reading the file, not for correctness.
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} acquired={time.time():.0f}\n")
            handle.flush()
        except OSError:
            pass
        depths[key] = 1
        try:
            yield True
        finally:
            depths.pop(key, None)
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def job_lock(db_path: str | Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[bool]:
    """Hold the pipeline lock across a multi-step job whose steps are child processes.

    The supervisor (``refresh_once``) and a one-time seed wrap their whole run in this and set
    ``OPPINTEL_LOCK_HELD=1`` in each child's environment, so the child's own :func:`writer_lock`
    is a no-op. That keeps the job atomic — a second refresh or seed waits for the first to
    finish entirely — without the parent and child deadlocking over the same lock file.
    """
    with writer_lock(db_path, timeout=timeout) as held:
        yield held
