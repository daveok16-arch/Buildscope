"""Raw-archive retention policy for `data/raw/*.jsonl`.

The landed JSONL files are the replay and audit trail for every ingest (`connectors/base.py`
writes them; `pipeline._replay` reads them back). They are append-only and unbounded: a full
seed writes ~197 MB and a recurring refresh adds ~7.6 MB every six hours, so without a policy
the archive grows ~916 MB/month.

This module is **inert by default**. Nothing is deleted or compressed unless the `retention`
block in `config/sources.yaml` declares it. Two rules keep a misconfiguration from destroying
evidence:

* a file is deleted only when every configured rule agrees it is old — it is beyond the newest
  ``keep_last`` files **and** older than ``keep_days``. A rule that keeps a file wins;
* compression (``gzip_after_days``) only ever rewrites a file to gzip, never removes it.

The plan is a pure function of the file list and the settings, so it can be printed and reviewed
before it is applied (`oppintel prune-raw` prints it; `--apply` acts on it).
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import RetentionSettings

#: `{source_id}_{YYYYMMDDTHHMMSS}.jsonl` (optionally `.gz`). The source id may itself contain
#: underscores, so the timestamp is matched from the end.
_NAME_RE = re.compile(r"^(?P<source>.+)_(?P<stamp>\d{8}T\d{6})\.jsonl(?P<gz>\.gz)?$")


@dataclass(frozen=True)
class RetentionSettings:
    """The `retention` block. All fields default to "do nothing"."""

    enabled: bool = False
    keep_last: int | None = None
    keep_days: float | None = None
    gzip_after_days: float | None = None


@dataclass
class RetentionPlan:
    """What a run would do, split so the caller can inspect before acting."""

    keep: list[Path] = field(default_factory=list)
    compress: list[Path] = field(default_factory=list)
    delete: list[Path] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.compress or self.delete)


def parse_name(path: Path) -> tuple[str, str] | None:
    """(source_id, stamp) for a landed file, or None when the name does not match."""
    match = _NAME_RE.match(path.name)
    if not match:
        return None
    return match.group("source"), match.group("stamp")


def _age_days(path: Path, now: float) -> float:
    return (now - path.stat().st_mtime) / 86400.0


def plan_prune(
    files: list[Path],
    settings: RetentionSettings,
    *,
    now: float | None = None,
) -> RetentionPlan:
    """Decide keep/compress/delete for ``files`` under ``settings``.

    ``keep_last`` is applied per source (the newest N files of each source are kept).
    ``keep_days`` keeps anything newer than N days. A file survives if *either* keeps it, so
    deletion needs both rules to agree. ``gzip_after_days`` compresses aged files that survive.
    """
    now = time.time() if now is None else now
    plan = RetentionPlan()

    by_source: dict[str, list[Path]] = {}
    for path in files:
        parsed = parse_name(path)
        if parsed is None:
            # A file we do not recognise is never touched.
            plan.keep.append(path)
            continue
        by_source.setdefault(parsed[0], []).append(path)

    for source_files in by_source.values():
        # Newest first by mtime, so keep_last is deterministic even if two runs share a stamp.
        ordered = sorted(source_files, key=lambda p: p.stat().st_mtime, reverse=True)
        for index, path in enumerate(ordered):
            kept_by_count = settings.keep_last is not None and index < settings.keep_last
            age = _age_days(path, now)
            kept_by_age = settings.keep_days is not None and age <= settings.keep_days

            # With no rule at all, everything is kept (retention disabled).
            no_delete_rule = settings.keep_last is None and settings.keep_days is None
            if no_delete_rule or kept_by_count or kept_by_age:
                plan.keep.append(path)
                if (
                    settings.gzip_after_days is not None
                    and age >= settings.gzip_after_days
                    and not path.name.endswith(".gz")
                ):
                    plan.compress.append(path)
            else:
                plan.delete.append(path)
    return plan


def apply_plan(plan: RetentionPlan, *, settings: RetentionSettings) -> dict[str, int]:
    """Carry out a plan. Refuses to delete unless retention is explicitly enabled."""
    if (plan.delete or plan.compress) and not settings.enabled:
        raise RuntimeError(
            "retention is not enabled; set `retention.enabled: true` in config/sources.yaml "
            "before applying a plan"
        )
    counts = {"compressed": 0, "deleted": 0}
    for path in plan.compress:
        target = path.with_name(path.name + ".gz")
        tmp = target.with_name(target.name + ".tmp")
        with path.open("rb") as src, gzip.open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, target)
        path.unlink()
        counts["compressed"] += 1
    for path in plan.delete:
        path.unlink()
        counts["deleted"] += 1
    return counts
