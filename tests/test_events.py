"""Tests for the formal BuildScope event-driven intelligence model."""

from __future__ import annotations

from datetime import datetime, timezone

from oppintel.events import (
    BuildScopeEvent,
    ProjectCreatedEvent,
    ProjectUpdatedEvent,
    EvidenceAddedEvent,
    EvidenceChangedEvent,
    ProjectStatusChangedEvent,
    ProcurementChangedEvent,
    TradeEvidenceAddedEvent,
    ProjectClosedEvent,
    WatchTriggeredEvent,
    AlertCreatedEvent,
)


def test_project_created_event_is_idempotent():
    evt1 = ProjectCreatedEvent.create(
        project_id=101,
        project_name="Plano Medical Plaza",
        address="7800 Preston Rd",
        city="Plano",
        source_id="collin_cad_permits",
    )
    evt2 = ProjectCreatedEvent.create(
        project_id=101,
        project_name="Plano Medical Plaza",
        address="7800 Preston Rd",
        city="Plano",
        source_id="collin_cad_permits",
    )
    assert evt1.event_id == evt2.event_id
    assert evt1.event_type == "ProjectCreated"
    assert evt1.project_id == 101
    assert evt1.payload["city"] == "Plano"
    assert evt1.payload_hash != ""


def test_evidence_added_event():
    evt = EvidenceAddedEvent.create(
        project_id=202,
        field_name="mechanical_evidence_tier",
        value="1",
        evidence_tier=1,
        excerpt="Commercial Mechanical - 4 Rooftop RTUs",
        source_id="dallas_accela_permits",
        source_record_key="MEC-2026-00456",
    )
    assert evt.event_type == "EvidenceAdded"
    assert evt.project_id == 202
    assert evt.payload["evidence_tier"] == 1
    assert "RTUs" in evt.payload["excerpt"]
    assert evt.to_dict()["event_type"] == "EvidenceAdded"


def test_trade_evidence_added_event():
    evt = TradeEvidenceAddedEvent.create(
        project_id=303,
        trade="commercial_hvac",
        tier=1,
        permit_number="M-2026-0099",
        excerpt="Chilled water piping and air handler replacement",
        source_id="fort_worth_permits",
    )
    assert evt.event_type == "TradeEvidenceAdded"
    assert evt.payload["trade"] == "commercial_hvac"
    assert evt.payload["tier"] == 1


def test_alert_created_event_references_underlying_event():
    evt = AlertCreatedEvent.create(
        project_id=404,
        user_id=10,
        alert_kind="mechanical_evidence_added",
        title="Mechanical evidence added to monitored project",
        underlying_event_id="evt-789abc",
    )
    assert evt.event_type == "AlertCreated"
    assert evt.payload["underlying_event_id"] == "evt-789abc"
    assert evt.payload["user_id"] == 10
