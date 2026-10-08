"""Project events: the documented lifecycle of a project, derived from evidence.

An event records something that a source actually documents — a permit filed, a scope
identified, a company named, a detected revision. It is never emitted because a pipeline pass
ran: if nothing changed, no event exists. That rule is what makes the timeline trustworthy, so
it is enforced structurally here by deriving every event from a concrete source fact or a
detected change, and by giving every event a deterministic identity.

The identity (`event_uid`) is a digest of the fact the event describes, so re-deriving the
same fact — which happens on every assembly pass — produces the same uid and the insert is a
no-op. Two different facts never collide, and the same fact never duplicates.

This module is the durable successor to the dataclasses in `events.py`. Those describe event
*shapes*; this one derives and identifies real events from stored evidence. `events.py` is left
in place rather than deleted, because it is a published model with its own tests, but the
pipeline derives events here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Iterable

# --- event vocabulary ---------------------------------------------------------
# Matches the documented lifecycle. A controlled vocabulary so the timeline, the API and any
# future alert all name the same event the same way.

PROJECT_DISCOVERED = "PROJECT_DISCOVERED"
PLANNING_RECORDED = "PLANNING_RECORDED"
PERMIT_RECORDED = "PERMIT_RECORDED"
SCOPE_IDENTIFIED = "SCOPE_IDENTIFIED"
TRADE_IDENTIFIED = "TRADE_IDENTIFIED"
COMPANY_IDENTIFIED = "COMPANY_IDENTIFIED"
PERSON_IDENTIFIED = "PERSON_IDENTIFIED"
DOCUMENT_ADDED = "DOCUMENT_ADDED"
PROJECT_REVISED = "PROJECT_REVISED"
INSPECTION_RECORDED = "INSPECTION_RECORDED"
PROCUREMENT_SIGNAL_RECORDED = "PROCUREMENT_SIGNAL_RECORDED"
PROJECT_CLOSED = "PROJECT_CLOSED"

EVENT_TYPES: tuple[str, ...] = (
    PROJECT_DISCOVERED,
    PLANNING_RECORDED,
    PERMIT_RECORDED,
    SCOPE_IDENTIFIED,
    TRADE_IDENTIFIED,
    COMPANY_IDENTIFIED,
    PERSON_IDENTIFIED,
    DOCUMENT_ADDED,
    PROJECT_REVISED,
    INSPECTION_RECORDED,
    PROCUREMENT_SIGNAL_RECORDED,
    PROJECT_CLOSED,
)

#: Permit subtypes/keywords that make a permit a planning record rather than a construction
#: permit. Kept small and literal; a planning record is a documented pre-construction step.
_PLANNING_MARKERS = ("planning", "plat", "zoning", "site plan", "preliminary", "variance")

#: Status keywords that make a permit an inspection record.
_INSPECTION_MARKERS = ("inspection", "inspect")


def _digest(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:32]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _payload(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def event_uid(project_id: int, event_type: str, *discriminators: Any) -> str:
    """Deterministic identity for an event, so re-deriving a fact is idempotent."""
    return _digest(project_id, event_type, *discriminators)


# --- derivation from stored facts ---------------------------------------------

def _is_planning(permit: dict[str, Any]) -> bool:
    text = " ".join(
        str(permit.get(field) or "")
        for field in ("permit_type", "permit_subtype", "work_description")
    ).lower()
    return any(marker in text for marker in _PLANNING_MARKERS)


def _is_inspection(permit: dict[str, Any]) -> bool:
    text = " ".join(
        str(permit.get(field) or "") for field in ("permit_type", "permit_subtype")
    ).lower()
    return any(marker in text for marker in _INSPECTION_MARKERS)


def events_from_permits(project_id: int, permits: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """PERMIT_RECORDED, PLANNING_RECORDED and INSPECTION_RECORDED events from permit rows.

    Every event is anchored to the permit's own number and source, and dated by the permit's
    date where the source states one. No event is created for a permit that does not exist.
    """
    events: list[dict[str, Any]] = []
    for permit in permits:
        number = permit.get("permit_number") or permit.get("natural_key")
        if not number:
            continue
        source_id = permit.get("source_id")
        occurred = _iso(permit.get("permit_date") or permit.get("source_date"))
        recorded = _iso(permit.get("updated_at")) or _iso(datetime.now(timezone.utc))

        if _is_planning(permit):
            event_type = PLANNING_RECORDED
        elif _is_inspection(permit):
            event_type = INSPECTION_RECORDED
        else:
            event_type = PERMIT_RECORDED

        events.append(
            {
                "event_uid": event_uid(project_id, event_type, source_id, number),
                "project_id": project_id,
                "event_type": event_type,
                "occurred_at": occurred,
                "recorded_at": recorded,
                "source_id": source_id,
                "source_url": permit.get("source_url"),
                "source_record_key": permit.get("natural_key"),
                "summary": _permit_summary(event_type, permit),
                "payload": _payload(
                    {
                        "permit_number": permit.get("permit_number"),
                        "permit_type": permit.get("permit_type"),
                        "status": permit.get("status"),
                        "city": permit.get("city"),
                    }
                ),
            }
        )
    return events


def _permit_summary(event_type: str, permit: dict[str, Any]) -> str:
    number = permit.get("permit_number") or permit.get("natural_key")
    kind = permit.get("permit_type") or "Permit"
    if event_type == PLANNING_RECORDED:
        return f"Planning record {number} ({kind}) recorded."
    if event_type == INSPECTION_RECORDED:
        return f"Inspection record {number} ({kind}) recorded."
    return f"{kind} {number} recorded."


def events_from_evidence(
    project_id: int, evidence_rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """SCOPE_IDENTIFIED and TRADE_IDENTIFIED events from mechanical-scope evidence.

    Derived only from an `evidence` row that actually supports a mechanical claim, so a
    project with no such evidence gets no scope event. This is the timeline entry that says
    "mechanical scope identified", and it is anchored to the exact evidence row.
    """
    events: list[dict[str, Any]] = []
    for ev in evidence_rows:
        if ev.get("field_name") != "mechanical_hvac_evidence":
            continue
        tier = ev.get("tier")
        source_id = ev.get("source_id")
        record_key = ev.get("source_record_key")
        occurred = _iso(ev.get("source_date") or ev.get("observed_at"))
        recorded = _iso(ev.get("observed_at")) or _iso(datetime.now(timezone.utc))
        scope_uid = event_uid(project_id, SCOPE_IDENTIFIED, source_id, record_key, tier)
        trade_uid = event_uid(project_id, TRADE_IDENTIFIED, source_id, record_key, tier)
        base = {
            "occurred_at": occurred,
            "recorded_at": recorded,
            "source_id": source_id,
            "source_url": ev.get("source_url"),
            "source_record_key": record_key,
        }
        events.append(
            {
                **base,
                "event_uid": scope_uid,
                "project_id": project_id,
                "event_type": SCOPE_IDENTIFIED,
                "summary": f"Mechanical scope identified at evidence tier {tier}.",
                "payload": _payload({"tier": tier, "excerpt": ev.get("excerpt")}),
            }
        )
        events.append(
            {
                **base,
                "event_uid": trade_uid,
                "project_id": project_id,
                "event_type": TRADE_IDENTIFIED,
                "summary": f"Trade identified from permit scope at evidence tier {tier}.",
                "payload": _payload({"trade": "commercial_hvac", "tier": tier}),
            }
        )
    return events


def events_from_parties(
    project_id: int, parties: Iterable[dict[str, Any]], *, canonical_key_for
) -> list[dict[str, Any]]:
    """COMPANY_IDENTIFIED / PERSON_IDENTIFIED events from resolved project parties.

    The canonical key is supplied by the caller so identity resolution stays in one place.
    A party whose name cannot be resolved produces no event, because an event must name a
    real entity.
    """
    events: list[dict[str, Any]] = []
    for party in parties:
        name = party.get("name")
        key = canonical_key_for(name) if name else None
        if not key:
            continue
        entity_type = key.split("|", 1)[0]
        role = party.get("role")
        source_id = party.get("source_id")
        event_type = PERSON_IDENTIFIED if entity_type == "person" else COMPANY_IDENTIFIED
        events.append(
            {
                "event_uid": event_uid(project_id, event_type, key, role),
                "project_id": project_id,
                "event_type": event_type,
                "occurred_at": None,
                "recorded_at": _iso(datetime.now(timezone.utc)),
                "source_id": source_id,
                "source_url": party.get("source_url"),
                "source_record_key": None,
                "summary": f"{name} identified as {str(role).replace('_', ' ')}.",
                "payload": _payload({"entity_key": key, "role": role, "name": name}),
            }
        )
    return events


def events_from_documents(
    project_id: int, documents: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """DOCUMENT_ADDED events, one per source record a project's facts were drawn from."""
    events: list[dict[str, Any]] = []
    for doc in documents:
        source_id = doc.get("source_id")
        record_key = doc.get("source_record_key")
        doc_type = doc.get("document_type")
        events.append(
            {
                "event_uid": event_uid(project_id, DOCUMENT_ADDED, source_id, record_key, doc_type),
                "project_id": project_id,
                "event_type": DOCUMENT_ADDED,
                "occurred_at": _iso(doc.get("source_date")),
                "recorded_at": _iso(doc.get("captured_at")) or _iso(datetime.now(timezone.utc)),
                "source_id": source_id,
                "source_url": doc.get("source_url"),
                "source_record_key": record_key,
                "summary": f"Source document recorded: {doc.get('title') or record_key}.",
                "payload": _payload({"document_type": doc_type}),
            }
        )
    return events


#: Change kinds mapped onto the durable event vocabulary. A change the detector recorded is a
#: real, evidenced revision, so it becomes PROJECT_REVISED (or a more specific type).
_CHANGE_EVENT_TYPE = {
    "procurement_changed": PROCUREMENT_SIGNAL_RECORDED,
    "status_changed": PROJECT_REVISED,
    "project_closed": PROJECT_CLOSED,
}


def event_from_change(change: dict[str, Any]) -> dict[str, Any] | None:
    """A PROJECT_REVISED (or more specific) event from one detected change row.

    The change row is already a record of an observed difference, so this does not invent an
    event: it re-expresses a detected change in the durable timeline. A change with no
    detected timestamp cannot be placed on the timeline and is skipped.
    """
    project_id = change.get("project_id")
    detected = _iso(change.get("detected_at"))
    if project_id is None or not detected:
        return None
    kind = change.get("change_kind") or "evidence_updated"
    event_type = _CHANGE_EVENT_TYPE.get(kind, PROJECT_REVISED)
    return {
        "event_uid": event_uid(
            int(project_id), event_type, kind, change.get("field_name"), detected
        ),
        "project_id": int(project_id),
        "event_type": event_type,
        "occurred_at": detected,
        "recorded_at": detected,
        "source_id": change.get("source_id"),
        "source_url": change.get("source_url"),
        "source_record_key": None,
        "summary": change.get("summary") or f"Project revised ({kind}).",
        "payload": _payload(
            {
                "change_kind": kind,
                "field_name": change.get("field_name"),
                "previous_value": change.get("previous_value"),
                "current_value": change.get("current_value"),
            }
        ),
    }


def discovery_event(project: dict[str, Any]) -> dict[str, Any]:
    """The PROJECT_DISCOVERED event: the moment a project first entered the system.

    Dated by when BuildScope assembled the project (`created_at`), not by the permit date,
    because discovery is our observation, not a fact about the building.
    """
    project_id = int(project["id"])
    recorded = _iso(project.get("created_at")) or _iso(datetime.now(timezone.utc))
    return {
        "event_uid": event_uid(project_id, PROJECT_DISCOVERED, project.get("project_key")),
        "project_id": project_id,
        "event_type": PROJECT_DISCOVERED,
        "occurred_at": _iso(project.get("permit_date")),
        "recorded_at": recorded,
        "source_id": None,
        "source_url": project.get("source_url"),
        "source_record_key": None,
        "summary": "Project first entered the system.",
        "payload": _payload(
            {"address": project.get("address"), "city": project.get("city")}
        ),
    }


def order_timeline(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order events for a project timeline, oldest first.

    Sorted by the moment the fact happened (`occurred_at`) where the source states one, and by
    when we recorded it otherwise, so an event with no source date still lands in a stable,
    explainable place rather than at the top or bottom arbitrarily.
    """
    def key(event: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(event.get("occurred_at") or event.get("recorded_at") or ""),
            str(event.get("recorded_at") or ""),
            str(event.get("event_uid") or ""),
        )

    return sorted(events, key=key)
