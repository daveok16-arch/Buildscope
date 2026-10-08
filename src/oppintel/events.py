"""Formal event-driven intelligence model for BuildScope.

Every material fact, state transition, and detected difference in the intelligence layer
or customer workspace emits an immutable, idempotent event.

Events serve as the authoritative audit trail and notification triggers:
- No event = no alert.
- No change = no notification.
- Every alert directly points to a verified underlying event.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BuildScopeEvent:
    """Base class for all BuildScope intelligence and workspace events."""

    event_id: str
    event_type: str
    project_id: int | None
    timestamp: datetime
    source_id: str | None = None
    source_record_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""

    def __post_init__(self) -> None:
        if not self.payload_hash and self.payload:
            canonical = json.dumps(self.payload, sort_keys=True, default=str)
            calculated = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # Object is frozen, so set via object.__setattr__
            object.__setattr__(self, "payload_hash", calculated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "timestamp": self.timestamp.isoformat(),
            "source_id": self.source_id,
            "source_record_key": self.source_record_key,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True)
class ProjectCreatedEvent(BuildScopeEvent):
    """Emitted when a new commercial project entity is assembled from source records."""

    @classmethod
    def create(
        cls,
        project_id: int,
        project_name: str | None,
        address: str | None,
        city: str | None,
        source_id: str | None = None,
        source_record_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProjectCreatedEvent:
        payload = {
            "project_name": project_name,
            "address": address,
            "city": city,
            **(details or {}),
        }
        event_id = hashlib.sha256(f"created:{project_id}:{address}:{city}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="ProjectCreated",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class ProjectUpdatedEvent(BuildScopeEvent):
    """Emitted when an existing project's facts are updated from new permit filings."""

    @classmethod
    def create(
        cls,
        project_id: int,
        field_name: str,
        previous_value: Any,
        current_value: Any,
        source_id: str | None = None,
        source_record_key: str | None = None,
    ) -> ProjectUpdatedEvent:
        payload = {
            "field_name": field_name,
            "previous_value": str(previous_value) if previous_value is not None else None,
            "current_value": str(current_value) if current_value is not None else None,
        }
        raw_key = f"updated:{project_id}:{field_name}:{previous_value}->{current_value}:{utcnow().strftime('%Y-%m-%d')}"
        event_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="ProjectUpdated",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class EvidenceAddedEvent(BuildScopeEvent):
    """Emitted when direct factual evidence (permit, filing, inspection) is attached to a project."""

    @classmethod
    def create(
        cls,
        project_id: int,
        field_name: str,
        value: str | None,
        evidence_tier: int | None,
        excerpt: str | None,
        source_id: str,
        source_record_key: str | None = None,
    ) -> EvidenceAddedEvent:
        payload = {
            "field_name": field_name,
            "value": value,
            "evidence_tier": evidence_tier,
            "excerpt": excerpt,
        }
        event_id = hashlib.sha256(f"evidence_add:{project_id}:{field_name}:{source_id}:{source_record_key}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="EvidenceAdded",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class EvidenceChangedEvent(BuildScopeEvent):
    """Emitted when a previously recorded evidence item is revised by a source."""

    @classmethod
    def create(
        cls,
        project_id: int,
        field_name: str,
        previous_excerpt: str | None,
        current_excerpt: str | None,
        source_id: str,
        source_record_key: str | None = None,
    ) -> EvidenceChangedEvent:
        payload = {
            "field_name": field_name,
            "previous_excerpt": previous_excerpt,
            "current_excerpt": current_excerpt,
        }
        event_id = hashlib.sha256(f"evidence_change:{project_id}:{field_name}:{source_id}:{source_record_key}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="EvidenceChanged",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class ProjectStatusChangedEvent(BuildScopeEvent):
    """Emitted when the official permit or construction status moves."""

    @classmethod
    def create(
        cls,
        project_id: int,
        previous_status: str | None,
        current_status: str | None,
        source_id: str | None = None,
        source_record_key: str | None = None,
    ) -> ProjectStatusChangedEvent:
        payload = {
            "previous_status": previous_status,
            "current_status": current_status,
        }
        event_id = hashlib.sha256(f"status:{project_id}:{previous_status}->{current_status}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="ProjectStatusChanged",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class ProcurementChangedEvent(BuildScopeEvent):
    """Emitted when public procurement or bidding status transitions."""

    @classmethod
    def create(
        cls,
        project_id: int,
        previous_state: str | None,
        current_state: str | None,
        source_id: str | None = None,
        source_record_key: str | None = None,
    ) -> ProcurementChangedEvent:
        payload = {
            "previous_state": previous_state,
            "current_state": current_state,
        }
        event_id = hashlib.sha256(f"procurement:{project_id}:{previous_state}->{current_state}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="ProcurementChanged",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=source_record_key,
            payload=payload,
        )


@dataclass(frozen=True)
class TradeEvidenceAddedEvent(BuildScopeEvent):
    """Emitted when strong trade-specific evidence (e.g. Mechanical/HVAC permit) is identified."""

    @classmethod
    def create(
        cls,
        project_id: int,
        trade: str,
        tier: int,
        permit_number: str | None,
        excerpt: str | None,
        source_id: str,
    ) -> TradeEvidenceAddedEvent:
        payload = {
            "trade": trade,
            "tier": tier,
            "permit_number": permit_number,
            "excerpt": excerpt,
        }
        event_id = hashlib.sha256(f"trade_evidence:{project_id}:{trade}:{tier}:{permit_number}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="TradeEvidenceAdded",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            source_record_key=permit_number,
            payload=payload,
        )


@dataclass(frozen=True)
class ProjectClosedEvent(BuildScopeEvent):
    """Emitted when a project reaches completed, finaled, or void state."""

    @classmethod
    def create(
        cls,
        project_id: int,
        reason: str | None = None,
        source_id: str | None = None,
    ) -> ProjectClosedEvent:
        payload = {"reason": reason}
        event_id = hashlib.sha256(f"closed:{project_id}:{reason}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="ProjectClosed",
            project_id=project_id,
            timestamp=utcnow(),
            source_id=source_id,
            payload=payload,
        )


@dataclass(frozen=True)
class WatchTriggeredEvent(BuildScopeEvent):
    """Emitted when a user watch is evaluated and detects qualifying differences."""

    @classmethod
    def create(
        cls,
        project_id: int,
        user_id: int,
        change_summary: str,
        change_id: int | None = None,
    ) -> WatchTriggeredEvent:
        payload = {
            "user_id": user_id,
            "change_id": change_id,
            "change_summary": change_summary,
        }
        event_id = hashlib.sha256(f"watch:{user_id}:{project_id}:{change_id}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="WatchTriggered",
            project_id=project_id,
            timestamp=utcnow(),
            payload=payload,
        )


@dataclass(frozen=True)
class AlertCreatedEvent(BuildScopeEvent):
    """Emitted when an in-app alert is generated for a user based on a verified event."""

    @classmethod
    def create(
        cls,
        project_id: int,
        user_id: int,
        alert_kind: str,
        title: str,
        underlying_event_id: str | None = None,
    ) -> AlertCreatedEvent:
        payload = {
            "user_id": user_id,
            "alert_kind": alert_kind,
            "title": title,
            "underlying_event_id": underlying_event_id,
        }
        event_id = hashlib.sha256(f"alert:{user_id}:{project_id}:{alert_kind}:{underlying_event_id}".encode("utf-8")).hexdigest()[:24]
        return cls(
            event_id=event_id,
            event_type="AlertCreated",
            project_id=project_id,
            timestamp=utcnow(),
            payload=payload,
        )
