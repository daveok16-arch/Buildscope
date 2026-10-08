"""Monitoring integrity.

Change detection and alerts are the platform's strongest claim: "this changed, and here is the
evidence." A claim like that is only worth anything if it can be audited, so this module reads
the monitoring tables and reports whether the invariants still hold.

The invariants, stated so a reader can check them:

* Every alert points at an event. An alert whose `change_id` is null must be a first-match
  alert; any other kind with a null change is fabricated and is reported as a fault.
* No change is stranded. A watched project's `last_seen_change_id` must not lag behind the
  newest change for that project, or a real change was silently never considered for an alert.
* No change is duplicated. The detector runs once per assembly pass; two identical change rows
  for the same project, field, kind and timestamp mean the pass ran twice over the same data,
  which would double every alert.
* A change that cites a source link must cite the source the project itself cites, so the
  timeline cannot point at a different record than the opportunity page.

Nothing here mutates data. It is a read-only audit, surfaced on the operations view so the
invariant is observable in production rather than only asserted in tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .changes import NOTIFIABLE_KINDS
from .db import Database

#: Severity levels, matching the data-quality vocabulary so both reports read the same way.
SEVERITY_FAULT = "fault"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def change_volume(db: Database, *, days: int = 30) -> dict[str, Any]:
    """Change counts by kind, split into notifiable and routine corrections."""
    rows = db.conn.execute(
        """
        SELECT change_kind, COUNT(*) AS n
          FROM project_change
         WHERE detected_at >= datetime('now', ?)
         GROUP BY change_kind
         ORDER BY n DESC
        """,
        (f"-{int(days)} days",),
    ).fetchall()
    by_kind = {r["change_kind"]: int(r["n"]) for r in rows}
    notifiable = sum(n for k, n in by_kind.items() if k in NOTIFIABLE_KINDS)
    total = sum(by_kind.values())
    return {
        "window_days": days,
        "total": total,
        "notifiable": notifiable,
        "routine": total - notifiable,
        "by_kind": by_kind,
    }


def watch_summary(db: Database) -> dict[str, Any]:
    """How many projects are watched, and how many watches have seen a change."""
    row = db.conn.execute(
        """
        SELECT COUNT(*) AS watches,
               SUM(CASE WHEN w.last_seen_change_id IS NOT NULL THEN 1 ELSE 0 END) AS advanced
          FROM watched_opportunity w
        """
    ).fetchone()
    watches = int(row["watches"] or 0)
    advanced = int(row["advanced"] or 0)
    return {
        "watches": watches,
        "watches_with_changes": advanced,
        "watches_without_changes": watches - advanced,
    }


def stranded_changes(db: Database, *, limit: int = 100) -> list[dict[str, Any]]:
    """Watched projects whose recorded changes have not all been considered for an alert.

    `last_seen_change_id` below the newest change id for the same project means the alert pass
    stopped early, so a real change never produced an alert. This is the one fault that would
    silently swallow a notification, which is why it is checked first.
    """
    rows = db.conn.execute(
        """
        SELECT w.user_id, w.project_id, w.last_seen_change_id,
               MAX(c.id) AS newest_change_id,
               COUNT(c.id) AS changes
          FROM watched_opportunity w
          JOIN project_change c ON c.project_id = w.project_id
         GROUP BY w.user_id, w.project_id
        HAVING COALESCE(w.last_seen_change_id, 0) < MAX(c.id)
         ORDER BY newest_change_id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "user_id": int(r["user_id"]),
            "project_id": int(r["project_id"]),
            "last_seen_change_id": r["last_seen_change_id"],
            "newest_change_id": int(r["newest_change_id"]),
            "changes": int(r["changes"]),
        }
        for r in rows
    ]


def duplicate_changes(db: Database, *, limit: int = 100) -> list[dict[str, Any]]:
    """Identical change rows for the same project, field, kind and timestamp.

    One is a real detection; the rest mean the detector processed the same snapshot twice, and
    every duplicate would raise a duplicate alert.
    """
    rows = db.conn.execute(
        """
        SELECT project_id, field_name, change_kind, detected_at, COUNT(*) AS n
          FROM project_change
         GROUP BY project_id, field_name, change_kind, detected_at
        HAVING COUNT(*) > 1
         ORDER BY n DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "project_id": int(r["project_id"]),
            "field_name": r["field_name"],
            "change_kind": r["change_kind"],
            "detected_at": r["detected_at"],
            "duplicates": int(r["n"]),
        }
        for r in rows
    ]


def mismatched_source_links(db: Database, *, limit: int = 100) -> list[dict[str, Any]]:
    """Changes whose source link disagrees with the project's own source link.

    Both describe the same public record, so a disagreement means the timeline would send a
    reader to a different page than the opportunity does.
    """
    rows = db.conn.execute(
        """
        SELECT c.id, c.project_id, c.source_url AS change_url, p.source_url AS project_url
          FROM project_change c
          JOIN project p ON p.id = c.project_id
         WHERE c.source_url IS NOT NULL AND p.source_url IS NOT NULL
           AND c.source_url <> p.source_url
         ORDER BY c.id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def monitoring_report(db: Database, *, days: int = 30) -> dict[str, Any]:
    """The full monitoring audit: volumes plus every integrity finding."""
    stranded = stranded_changes(db)
    duplicates = duplicate_changes(db)
    mismatched = mismatched_source_links(db)

    findings: list[dict[str, Any]] = []
    if stranded:
        findings.append(
            {
                "severity": SEVERITY_FAULT,
                "issue": "stranded_changes",
                "count": len(stranded),
                "detail": (
                    "Watched projects have recorded changes that were never considered for an "
                    "alert. A notification was missed."
                ),
            }
        )
    if duplicates:
        findings.append(
            {
                "severity": SEVERITY_WARNING,
                "issue": "duplicate_changes",
                "count": len(duplicates),
                "detail": (
                    "Identical change rows share a project, field, kind and timestamp, which "
                    "means the detector ran twice over one snapshot."
                ),
            }
        )
    if mismatched:
        findings.append(
            {
                "severity": SEVERITY_WARNING,
                "issue": "mismatched_source_links",
                "count": len(mismatched),
                "detail": (
                    "A change cites a source link that differs from the project's, so the "
                    "timeline would point at a different record than the opportunity page."
                ),
            }
        )

    return {
        "window_days": days,
        "volume": change_volume(db, days=days),
        "watches": watch_summary(db),
        "stranded_changes": stranded,
        "duplicate_changes": duplicates,
        "mismatched_source_links": mismatched,
        "findings": findings,
        "healthy": not findings,
    }
