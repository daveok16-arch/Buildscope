"""Intelligence graph derivation.

This is where the pieces — identity resolution, locations, trades, documents, entities and
events — are derived from what the pipeline already stores, and written into the durable
tables. It is deliberately a *derivation* layer: it reads permits, projects, evidence and
detected changes, and writes relationships. It never invents a value. A project with no
mechanical evidence gets no scope event; a name that cannot be resolved gets no entity; an
address with no street number gets a location row with a null building key rather than a
guess.

Two entry points:

* `derive_for_project` — used by the pipeline for each project it assembles, so the graph
  stays current as new records arrive.
* `run_backfill` — used once to build the graph for the projects that already exist, with a
  report of what was considered, linked, and left unresolved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import classify_trade
from .db import Database
from .grouping import building_key
from .identity import ENTITY_COMPANY, ENTITY_PERSON, resolve_name, select_display_name
from .intel_events import (
    discovery_event,
    event_from_change,
    events_from_documents,
    events_from_evidence,
    events_from_parties,
    events_from_permits,
    order_timeline,
)
from .models import normalize_address

#: Party roles carried on a project, and whether the role names a person or a company when the
#: name itself is ambiguous. A general contractor or architect on a permit is a company far
#: more often than not; an owner may be either, and the name shape decides.
_ROLE_ENTITY_HINT = {
    "owner": None,
    "developer": ENTITY_COMPANY,
    "general_contractor": ENTITY_COMPANY,
    "architect": ENTITY_COMPANY,
    "engineer": ENTITY_COMPANY,
    "mechanical_contractor": ENTITY_COMPANY,
}

#: The project columns that carry a party name, paired with the role they denote. Used to pick
#: up roles that were asserted on the project but did not produce a `project_party` row.
_PROJECT_PARTY_COLUMNS = (
    ("owner", "owner"),
    ("developer", "developer"),
    ("general_contractor", "general_contractor"),
    ("architect", "architect"),
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_key(name: str | None) -> str | None:
    """The canonical key for a name, or None when it does not resolve."""
    resolved = resolve_name(name) if name else None
    return resolved.canonical_key if resolved else None


@dataclass
class DerivationResult:
    """What one project's derivation produced."""

    project_id: int
    entity_resolutions: int = 0
    entity_links: int = 0
    trades: int = 0
    documents: int = 0
    events_created: int = 0
    unresolved_names: list[str] = field(default_factory=list)


@dataclass
class BackfillReport:
    """Counts for a backfill pass, reported so the result is auditable."""

    projects_considered: int = 0
    projects_linked: int = 0
    projects_skipped: int = 0
    entity_resolutions: int = 0
    entity_links: int = 0
    trades_written: int = 0
    documents_written: int = 0
    events_created: int = 0
    locations_written: int = 0
    unresolved_names: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "projects_considered": self.projects_considered,
            "projects_linked": self.projects_linked,
            "projects_skipped": self.projects_skipped,
            "entity_resolutions": self.entity_resolutions,
            "entity_links": self.entity_links,
            "trades_written": self.trades_written,
            "documents_written": self.documents_written,
            "events_created": self.events_created,
            "locations_written": self.locations_written,
            "unresolved_names": self.unresolved_names,
            "reasons": dict(sorted(self.reasons.items())),
        }

    def _note(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


# --- derivation ---------------------------------------------------------------

def derive_project_trades(
    project: dict[str, Any], permits: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The trades a project carries, from documented permit text only.

    A project may carry several trades. The primary trade is mechanical/HVAC when the project
    has mechanical evidence, otherwise the trade of the earliest dated permit — the permit
    that first describes the work. A project whose text names no trade gets an empty list: an
    unknown trade is not a default to fill in.
    """
    found: dict[str, str] = {}
    ordered_permits = sorted(
        permits, key=lambda p: (str(p.get("permit_date") or ""), str(p.get("permit_number") or ""))
    )
    for permit in ordered_permits:
        # The permit type and subtype are the authoritative signal — a "Commercial Plumbing
        # Permit" is a plumbing record whatever the description says. The description is only
        # consulted when the type names no trade, so a generic phrase such as "new construction"
        # in the description cannot override the permit's own classification.
        type_text = " ".join(
            str(permit.get(field) or "") for field in ("permit_type", "permit_subtype")
        )
        trade_id = classify_trade(type_text)
        if trade_id is None:
            description_text = " ".join(
                str(permit.get(field) or "")
                for field in ("work_description", "land_use", "specific_use")
            )
            trade_id = classify_trade(description_text)
        if trade_id and trade_id not in found:
            found[trade_id] = permit.get("permit_number") or permit.get("natural_key") or ""

    if not found:
        return []

    primary: str | None = None
    if project.get("mechanical_evidence_tier") in (1, 2) and "hvac_mechanical" in found:
        primary = "hvac_mechanical"
    else:
        # The first trade seen in date order is the primary for a project without mechanical
        # evidence.
        primary = next(iter(found))

    return [
        {
            "trade_id": trade_id,
            "is_primary": trade_id == primary,
            "confidence": "documented",
            "evidence_source": source_key or None,
        }
        for trade_id, source_key in found.items()
    ]


def derive_project_location(project: dict[str, Any], permits: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Normalize a project's location without fabricating anything.

    The raw address is preserved verbatim. The normalized form and building key come from the
    existing normalizers, which only fold case, punctuation and suite designators — they do
    not invent a value. No coordinates are produced: there is no geocoder here, so latitude
    and longitude stay null rather than guessed.
    """
    address = project.get("address")
    city = project.get("city")
    zip_code = None
    for permit in permits:
        if permit.get("zip_code"):
            zip_code = permit["zip_code"]
            break
    return {
        "raw_address": address,
        "normalized_address": normalize_address(address),
        "building_key": building_key(address, city),
        "city": city,
        "state": project.get("state"),
        "zip_code": zip_code,
        "latitude": None,
        "longitude": None,
        "geocode_source": None,
        "geocode_confidence": None,
        "jurisdiction": city,
        "location_precision": project.get("location_precision"),
    }


def _collect_party_rows(
    project: dict[str, Any], parties: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every (role, name) a project carries, from party rows and project columns.

    Party rows are authoritative. The project columns are read as a fallback so a role that
    was asserted on the project but never produced a party row is still captured, without
    double-counting a role that has one.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for party in parties:
        name = party.get("name")
        role = party.get("role")
        if not name or not role:
            continue
        key = (role, name.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "role": role,
                "name": name,
                "source_id": party.get("source_id"),
                "source_url": party.get("source_url"),
                "excerpt": party.get("excerpt"),
            }
        )
    for column, role in _PROJECT_PARTY_COLUMNS:
        name = project.get(column)
        if not name:
            continue
        key = (role, str(name).strip().lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {"role": role, "name": name, "source_id": None, "source_url": project.get("source_url"), "excerpt": None}
        )
    return rows


def resolve_project_entities(
    db: Database, project_id: int, party_rows: list[dict[str, Any]]
) -> tuple[int, int, list[str]]:
    """Resolve a project's parties to entities and record the relationships.

    Returns (entity_resolutions, links_created, unresolved_names). A name that resolves to no
    canonical key is left unresolved and reported, never coerced into an entity. The first
    count is the number of (party, entity) resolutions, not the number of distinct entities
    created: an entity seen on many projects is resolved once per project.
    """
    entities_touched = 0
    links = 0
    unresolved: list[str] = []
    for row in party_rows:
        resolved = resolve_name(row["name"])
        if resolved is None:
            unresolved.append(row["name"])
            continue
        entity_id = db.upsert_entity(
            entity_type=resolved.entity_type,
            canonical_key=resolved.canonical_key,
            display_name=select_display_name([resolved.raw]),
        )
        entities_touched += 1
        db.add_entity_name(
            entity_id,
            name=resolved.raw,
            normalized_name=resolved.normalized,
            source_id=row.get("source_id"),
        )
        before = db.conn.total_changes
        db.link_entity_project(
            entity_id,
            project_id,
            role=row["role"],
            source_id=row.get("source_id"),
            source_url=row.get("source_url"),
            excerpt=row.get("excerpt"),
        )
        if db.conn.total_changes > before:
            links += 1
    return entities_touched, links, unresolved


def derive_for_project(
    db: Database,
    project_id: int,
    *,
    project: dict[str, Any],
    permits: list[dict[str, Any]],
    parties: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> DerivationResult:
    """Derive and persist the full intelligence graph for one project.

    Idempotent: trades, locations and documents are upserted, entities are keyed, and events
    are keyed by uid, so running this again over unchanged data changes nothing.
    """
    result = DerivationResult(project_id=project_id)

    # Location.
    db.upsert_project_location(project_id, derive_project_location(project, permits))

    # Trades.
    trades = derive_project_trades(project, permits)
    db.replace_project_trades(project_id, trades)
    result.trades = len(trades)

    # Entities.
    party_rows = _collect_party_rows(project, parties)
    touched, links, unresolved = resolve_project_entities(db, project_id, party_rows)
    result.entity_resolutions = touched
    result.entity_links = links
    result.unresolved_names = unresolved

    # Documents: ensure each contributing source record is recorded (upsert_project already
    # writes these; this covers the backfill path and any document the pipeline did not see).
    for doc in documents:
        db.upsert_document(
            project_id,
            source_id=doc.get("source_id"),
            document_type=doc.get("document_type") or "permit_record",
            title=doc.get("title"),
            source_url=doc.get("source_url"),
            source_record_key=doc.get("source_record_key"),
            raw_record_id=doc.get("raw_record_id"),
            captured_at=doc.get("captured_at"),
            source_date=doc.get("source_date"),
        )

    # Events.
    events = _derive_events(project_id, project, permits, evidence, party_rows, documents, changes)
    result.events_created = db.insert_events(events)
    return result


def _derive_events(
    project_id: int,
    project: dict[str, Any],
    permits: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    party_rows: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every evidenced event for a project, de-duplicated and ordered.

    Each source of events is anchored to a real stored fact, so a project with no permits, no
    mechanical evidence, no parties and no detected change yields only its discovery event —
    and a re-run yields none, because the discovery event's uid is stable.
    """
    events: list[dict[str, Any]] = [discovery_event(project)]
    events.extend(events_from_permits(project_id, permits))
    events.extend(events_from_evidence(project_id, evidence))
    events.extend(events_from_parties(project_id, party_rows, canonical_key_for=_canonical_key))
    events.extend(events_from_documents(project_id, documents))
    for change in changes:
        event = event_from_change(change)
        if event is not None:
            events.append(event)

    # De-duplicate by uid, keeping the first, so the same fact derived twice is one event.
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        unique.setdefault(event["event_uid"], event)
    return order_timeline(unique.values())


# --- backfill -----------------------------------------------------------------

def run_backfill(db: Database, *, batch_size: int = 500) -> BackfillReport:
    """Build the intelligence graph for every project that already exists.

    Every value written is derived from stored data, so nothing is invented. The report names
    what was considered, linked, skipped, and left unresolved, so the result can be checked
    against the record counts rather than taken on trust.
    """
    report = BackfillReport()
    project_ids = db.project_ids()

    for project_id in project_ids:
        report.projects_considered += 1
        project = db.project_row(project_id)
        if project is None:
            report.projects_skipped += 1
            report._note("project_row_missing")
            continue

        permits = db.permits_for_project(project_id)
        if not permits:
            # A project with no linked permit cannot have its facts traced; it is skipped
            # rather than given a graph derived from nothing.
            report.projects_skipped += 1
            report._note("no_linked_permits")
            continue

        parties = [
            dict(r)
            for r in db.conn.execute(
                "SELECT * FROM project_party WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        evidence = [
            dict(r)
            for r in db.conn.execute(
                "SELECT * FROM evidence WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        # Archive the existing evidence immutably and record the source documents behind it.
        # Both are idempotent, so a re-run adds nothing; on the first pass this is what makes
        # the already-assembled projects' history durable rather than only current.
        seen_at = _utcnow()
        db.append_evidence_history(project_id, evidence, seen_at=seen_at)
        documents_touched = db.record_documents_from_evidence(project_id, evidence)

        documents = db.documents_for_project(project_id)
        changes = db.changes_for_project(project_id, limit=200)

        result = derive_for_project(
            db, project_id,
            project=project, permits=permits, parties=parties,
            evidence=evidence, documents=documents, changes=changes,
        )

        report.projects_linked += 1
        report.entity_resolutions += result.entity_resolutions
        report.entity_links += result.entity_links
        report.trades_written += result.trades
        report.documents_written += documents_touched
        report.events_created += result.events_created
        report.locations_written += 1
        report.unresolved_names += len(result.unresolved_names)

        if report.projects_considered % batch_size == 0:
            db.commit()

    db.commit()
    return report


# --- integrity ----------------------------------------------------------------

def integrity_report(db: Database) -> dict[str, Any]:
    """Read-only checks over the intelligence graph, for the operations view.

    Each check is a query for a condition that must never hold: orphaned rows, events without
    an anchor, coordinates without provenance. The report names any that are present rather
    than silently repairing them.
    """
    def count(sql: str, params: tuple = ()) -> int:
        return int(db.conn.execute(sql, params).fetchone()[0])

    findings: list[dict[str, Any]] = []

    orphan_evidence = count(
        """
        SELECT COUNT(*) FROM evidence e
         WHERE NOT EXISTS (SELECT 1 FROM project p WHERE p.id = e.project_id)
        """
    )
    orphan_entity_links = count(
        """
        SELECT COUNT(*) FROM entity_project ep
         WHERE NOT EXISTS (SELECT 1 FROM project p WHERE p.id = ep.project_id)
            OR NOT EXISTS (SELECT 1 FROM entity e WHERE e.id = ep.entity_id)
        """
    )
    orphan_events = count(
        """
        SELECT COUNT(*) FROM project_event ev
         WHERE NOT EXISTS (SELECT 1 FROM project p WHERE p.id = ev.project_id)
        """
    )
    duplicate_event_uids = count(
        """
        SELECT COUNT(*) FROM (
            SELECT event_uid FROM project_event GROUP BY event_uid HAVING COUNT(*) > 1
        )
        """
    )
    coords_without_provenance = count(
        """
        SELECT COUNT(*) FROM project_location
         WHERE (latitude IS NOT NULL OR longitude IS NOT NULL)
           AND (geocode_source IS NULL OR TRIM(geocode_source) = '')
        """
    )
    invalid_trades = count(
        """
        SELECT COUNT(*) FROM project_trade
         WHERE NOT EXISTS (SELECT 1 FROM project p WHERE p.id = project_trade.project_id)
        """
    )
    # Evidence history must never lose a source URL that the current evidence row carries.
    history_missing_url = count(
        """
        SELECT COUNT(*) FROM evidence_history h
         JOIN evidence e ON e.project_id = h.project_id AND e.field_name = h.field_name
                       AND e.value = h.value
         WHERE h.source_url IS NULL AND e.source_url IS NOT NULL
        """
    )
    # A project whose evidence has no immutable archive row is an un-migrated project.
    projects_without_history = count(
        """
        SELECT COUNT(*) FROM project p
         WHERE EXISTS (SELECT 1 FROM evidence e WHERE e.project_id = p.id)
           AND NOT EXISTS (SELECT 1 FROM evidence_history h WHERE h.project_id = p.id)
        """
    )

    checks = [
        ("orphan_evidence", orphan_evidence, "fault",
         "Evidence rows whose project no longer exists."),
        ("orphan_entity_links", orphan_entity_links, "fault",
         "Entity relationships pointing at a missing project or entity."),
        ("orphan_events", orphan_events, "fault",
         "Events whose project no longer exists."),
        ("duplicate_event_uids", duplicate_event_uids, "fault",
         "Two events share an identity, which would double a timeline entry."),
        ("coords_without_provenance", coords_without_provenance, "fault",
         "Stored coordinates with no recorded geocode source."),
        ("invalid_trades", invalid_trades, "fault",
         "Trade rows whose project no longer exists."),
        ("history_missing_url", history_missing_url, "warning",
         "An archived observation dropped a source URL the current evidence row still carries."),
        ("projects_without_history", projects_without_history, "warning",
         "Projects whose evidence has no immutable archive row yet."),
    ]
    for name, value, severity, detail in checks:
        if value:
            findings.append({"check": name, "severity": severity, "count": value, "detail": detail})

    return {
        "counts": {
            "entities": count("SELECT COUNT(*) FROM entity"),
            "entity_names": count("SELECT COUNT(*) FROM entity_name"),
            "entity_links": count("SELECT COUNT(*) FROM entity_project"),
            "locations": count("SELECT COUNT(*) FROM project_location"),
            "documents": count("SELECT COUNT(*) FROM document"),
            "events": count("SELECT COUNT(*) FROM project_event"),
            "evidence_history": count("SELECT COUNT(*) FROM evidence_history"),
            "project_trades": count("SELECT COUNT(*) FROM project_trade"),
        },
        "findings": findings,
        "healthy": not findings,
    }
