"""Data-quality detection.

The operations view needs to state what is wrong with the data, and the only honest way to do
that is to measure it. This module inspects stored permits and projects and records the
conditions it can demonstrate: a permit with no address, a date in the future, a project with
no mechanical evidence sitting in a mechanical directory, a source that returned nothing.

Nothing here repairs or imputes. A missing field is recorded as missing and stays missing in
the project record, which is what keeps "Not verified" meaningful rather than decorative.

The checks run at the end of an assembly pass and replace the previous pass's findings, so the
operations view describes the current dataset rather than accumulating stale complaints.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .dates import VERDICT_ANCIENT, VERDICT_FUTURE_IMPLAUSIBLE, validate_date
from .db import Database

#: Issue severities, ordered worst first for display.
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

#: Retained for reference: the original flat tolerance, which the semantic model replaced. A
#: future *occurrence* date is implausible the moment it is observed, so it is now flagged
#: regardless of distance rather than only beyond this window.
FUTURE_DATE_TOLERANCE_DAYS = 365

#: Issue types, as a controlled vocabulary so the operations view and the tests agree.
MISSING_ADDRESS = "missing_address"
MISSING_PERMIT_DATE = "missing_permit_date"
FUTURE_PERMIT_DATE = "future_permit_date"
ANCIENT_PERMIT_DATE = "ancient_permit_date"
NON_POSITIVE_VALUE = "non_positive_value"
UNMATCHED_SOURCE = "unmatched_source"
PROJECT_WITHOUT_EVIDENCE = "project_without_evidence"
DISCOVERABLE_WITHOUT_EVIDENCE = "discoverable_without_evidence"


def detect_quality_issues(db: Database, permits: list[Any]) -> int:
    """Record data-quality issues for the current dataset. Returns the count written.

    The previous pass's issues are cleared first, because an issue that has been fixed should
    disappear from the report. The result is a snapshot of what is wrong now.
    """
    db.clear_quality_issues()
    count = 0
    today = date.today()

    for permit in permits:
        source_id = getattr(permit, "source_id", None)
        number = getattr(permit, "permit_number", None) or getattr(permit, "natural_key", "")
        if not getattr(permit, "address", None):
            db.record_quality_issue(
                MISSING_ADDRESS, SEVERITY_MEDIUM,
                f"Permit {number} has no address, so it cannot be placed in a market.",
                source_id=source_id,
            )
            count += 1
        if getattr(permit, "permit_date", None) is None:
            db.record_quality_issue(
                MISSING_PERMIT_DATE, SEVERITY_LOW,
                f"Permit {number} has no permit date. Freshness cannot be established for it.",
                source_id=source_id,
            )
            count += 1
        else:
            # The semantic model decides whether the value is a problem. A permit date records
            # something that has already happened, so *any* future value is implausible — the
            # old flat +365-day tolerance let a near-future date through unflagged, which is
            # exactly the value that makes a project look current when it is not.
            verdict = validate_date("permit_date", permit.permit_date, observed=today)
            if verdict.verdict == VERDICT_FUTURE_IMPLAUSIBLE:
                db.record_quality_issue(
                    FUTURE_PERMIT_DATE, SEVERITY_MEDIUM,
                    f"Permit {number} is dated {permit.permit_date}, which is later than the "
                    f"observation date. {verdict.reason}",
                    source_id=source_id,
                )
                count += 1
            elif verdict.verdict == VERDICT_ANCIENT:
                db.record_quality_issue(
                    ANCIENT_PERMIT_DATE, SEVERITY_LOW,
                    f"Permit {number} is dated {permit.permit_date}. {verdict.reason}",
                    source_id=source_id,
                )
                count += 1

        # A source date is the moment the record was published. It shares the occurrence
        # semantics of a permit date, so a future source date is checked the same way rather
        # than being left unflagged.
        source_date = getattr(permit, "source_date", None)
        if source_date is not None:
            verdict = validate_date("source_date", source_date, observed=today)
            if verdict.verdict == VERDICT_FUTURE_IMPLAUSIBLE:
                db.record_quality_issue(
                    FUTURE_PERMIT_DATE, SEVERITY_MEDIUM,
                    f"Permit {number} carries a source date of {source_date} that is later than "
                    f"the observation date. {verdict.reason}",
                    source_id=source_id,
                )
                count += 1
        value = getattr(permit, "job_value", None)
        if value is not None and value <= 0:
            db.record_quality_issue(
                NON_POSITIVE_VALUE, SEVERITY_MEDIUM,
                f"Permit {number} declares a value of {value}, which is not usable as a "
                f"project value.",
                source_id=source_id,
            )
            count += 1

    # Source-level checks, computed from the stored rows rather than the crawl counters.
    known = {r["id"] for r in db.conn.execute("SELECT id FROM source").fetchall()}
    for row in db.conn.execute(
        "SELECT DISTINCT source_id FROM permit WHERE source_id IS NOT NULL"
    ).fetchall():
        if row["source_id"] not in known:
            db.record_quality_issue(
                UNMATCHED_SOURCE, SEVERITY_HIGH,
                f"Permits reference source {row['source_id']!r}, which has no configuration "
                f"entry. Its provenance cannot be described.",
                source_id=row["source_id"],
            )
            count += 1

    # Project-level checks: a project with no evidence rows cannot support any claim.
    for row in db.conn.execute(
        """
        SELECT p.id, p.classification FROM project p
         WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.project_id = p.id)
        """
    ).fetchall():
        db.record_quality_issue(
            PROJECT_WITHOUT_EVIDENCE, SEVERITY_HIGH,
            f"Project {row['id']} holds no evidence rows, so none of its fields is citable.",
            project_id=int(row["id"]),
        )
        count += 1

    # The most serious product-level inconsistency: a project offered in a trade directory
    # without the evidence that directory requires. Detected here so it is visible rather
    # than silently trusted.
    from .config import active_trade

    trade = active_trade()
    discovery = trade.discovery or {}
    field = discovery.get("evidence_field")
    values = discovery.get("evidence_values") or []
    # Only meaningful while discovery is evidence-gated. On the commercial base a discoverable
    # project without trade evidence is expected and is labelled "Trade not verified" on every
    # listing, so flagging it would report the intended behaviour as a defect. This guard must
    # skip only this final check: returning from the function here skipped the commit below and
    # silently discarded every finding recorded above.
    if not discovery.get("discover_commercial_base") and field and values and field.isidentifier():
        placeholders = ",".join("?" for _ in values)
        rows = db.conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM project
             WHERE classification IN ('HIGH', 'MEDIUM')
               AND procurement_status IN ('Confirmed open', 'Evidence found, status unclear')
               AND ({field} IS NULL OR {field} NOT IN ({placeholders}))
            """,
            list(values),
        ).fetchone()
        missing = int(rows["n"] or 0)
        if missing:
            db.record_quality_issue(
                DISCOVERABLE_WITHOUT_EVIDENCE, SEVERITY_HIGH,
                f"{missing} discoverable project(s) carry no evidence for the active trade's "
                f"configured evidence field. The directory filter should have excluded them.",
            )
            count += 1

    # Persist before returning on every path. The caller (`assemble_and_classify`) commits the
    # assembly transaction *before* this runs, so without this commit the findings live only in
    # the writer's connection and every reader — /admin/data, `flask report-quality` — sees an
    # empty table and reports "no open issues" no matter what is wrong with the data.
    db.conn.commit()
    return count
