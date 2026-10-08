"""Market coverage intelligence.

The property under test is the mission's hard rule: a market must never be described as
covered when its connector has never run. Each of the six states is exercised by driving a real
SQLite database into the condition it names.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from oppintel.config import MarketConfig, load_markets
from oppintel.coverage import (
    FRESHNESS_WINDOW_DAYS,
    STATE_CONFIGURED,
    STATE_CURRENT,
    STATE_INGESTED,
    STATE_RECORDS_HELD,
    STATE_SOURCE_ENABLED,
    STATE_VERIFIED,
    all_market_coverage,
    coverage_summary,
    market_coverage,
)
from oppintel.db import Database

TODAY = date(2026, 10, 7)


@pytest.fixture
def empty_db(tmp_path):
    """A schema-only database, so each coverage state can be reached from a known start."""
    db = Database(tmp_path / "coverage.db")
    db.init_schema()
    db.init_app_schema()
    yield db
    db.close()


def _market(market_id: str) -> MarketConfig:
    return load_markets()[market_id]


def _ensure_source(db, source_id: str = "fort_worth_permits"):
    db.conn.execute(
        """
        INSERT OR IGNORE INTO source (id, name, kind, updated_at)
        VALUES (?, 'City of Fort Worth Development Permits', 'arcgis', ?)
        """,
        (source_id, TODAY.isoformat()),
    )
    db.conn.commit()


def _insert_permit(db, *, city: str, permit_date: date, source_id: str = "fort_worth_permits"):
    _ensure_source(db, source_id)
    db.conn.execute(
        """
        INSERT INTO permit (source_id, natural_key, permit_number, permit_type, permit_date,
            status, address, city, state, work_description, is_commercial, updated_at)
        VALUES (?, ?, ?, 'Commercial Building Permit', ?, 'Issued', '1 TEST ST', ?, 'TX',
                'Test', 1, ?)
        """,
        (source_id, f"K{permit_date}{city}", f"N{permit_date}{city}", permit_date.isoformat(),
         city, permit_date.isoformat()),
    )
    db.conn.commit()


def _insert_project(db, *, city: str):
    db.conn.execute(
        """
        INSERT INTO project (project_key, trade, address, city, state, created_at, updated_at)
        VALUES (?, 'commercial_hvac', '1 TEST ST', ?, 'TX', ?, ?)
        """,
        (f"pk-{city}", city, TODAY.isoformat(), TODAY.isoformat()),
    )
    db.conn.commit()


def test_placeholder_market_is_configured_not_served(empty_db):
    # Houston is present in markets.yaml with no sources and is never ingested.
    cov = market_coverage(empty_db, _market("houston"), today=TODAY)
    assert cov.state == STATE_CONFIGURED
    assert not cov.is_serving
    assert cov.records == 0


def test_market_with_enabled_source_but_no_runs_is_source_enabled(empty_db):
    cov = market_coverage(empty_db, _market("dfw"), today=TODAY)
    assert cov.state == STATE_SOURCE_ENABLED
    assert cov.enabled_sources > 0
    assert not cov.is_serving


def test_completed_run_without_records_is_ingested(empty_db):
    _ensure_source(empty_db)
    empty_db.conn.execute(
        """
        INSERT INTO ingest_run (source_id, started_at, finished_at, status)
        VALUES ('fort_worth_permits', ?, ?, 'ok')
        """,
        (TODAY.isoformat(), TODAY.isoformat()),
    )
    empty_db.conn.commit()
    cov = market_coverage(empty_db, _market("dfw"), today=TODAY)
    assert cov.state == STATE_INGESTED
    assert any("connector answers" in n for n in cov.notes)


def test_records_outside_window_are_records_held(empty_db):
    stale = date(TODAY.year - 3, 1, 1)
    _insert_permit(empty_db, city="Fort Worth", permit_date=stale)
    _insert_project(empty_db, city="Fort Worth")
    cov = market_coverage(empty_db, _market("dfw"), today=TODAY)
    assert cov.state == STATE_RECORDS_HELD
    # Records are still served; they are simply stale, which the state and notes state plainly.
    assert cov.is_serving
    assert any("freshness window" in n for n in cov.notes)


def test_fresh_records_in_active_market_are_verified(empty_db):
    _insert_permit(empty_db, city="Fort Worth", permit_date=TODAY)
    _insert_project(empty_db, city="Fort Worth")
    cov = market_coverage(empty_db, _market("dfw"), today=TODAY)
    assert cov.state == STATE_VERIFIED
    assert cov.is_serving


def test_fresh_records_in_inactive_market_are_current_not_verified(empty_db):
    # A market that holds fresh records but is not the active market is current, not verified:
    # "verified" is reserved for the market the application actually serves.
    market = dataclasses.replace(_market("dfw"), id="not_active")
    _insert_permit(empty_db, city="Fort Worth", permit_date=TODAY)
    _insert_project(empty_db, city="Fort Worth")
    cov = market_coverage(empty_db, market, today=TODAY)
    assert cov.state == STATE_CURRENT


def test_all_market_coverage_puts_serving_markets_first(empty_db):
    _insert_permit(empty_db, city="Fort Worth", permit_date=TODAY)
    _insert_project(empty_db, city="Fort Worth")
    markets = all_market_coverage(empty_db, today=TODAY)
    assert markets[0].market_id == "dfw"
    assert markets[0].is_serving
    assert any(not m.is_serving for m in markets[1:])


def test_coverage_summary_counts(empty_db):
    _insert_permit(empty_db, city="Fort Worth", permit_date=TODAY)
    _insert_project(empty_db, city="Fort Worth")
    summary = coverage_summary(empty_db, today=TODAY)
    assert summary["markets_configured"] >= 3
    assert summary["markets_serving"] == 1
    assert summary["markets_verified"] == 1


def test_markets_page_does_not_imply_coverage_it_lacks(client):
    """The public markets page must distinguish a served market from a planned one.

    Regression: the page previously inferred coverage from `market.active` alone, which is a
    configuration flag, not evidence that any record exists.
    """
    body = client.get("/markets").get_data(as_text=True)
    assert "Serving Data" in body
    # Houston and Austin are configured but never ingested.
    assert "Planned Market" in body
    assert "no connector implemented" in body or "Planned Market" in body


def test_healthz_reports_coverage_state(client):
    payload = client.get("/healthz").get_json()
    assert payload["status"] in ("ok", "empty")
    assert "coverage" in payload
    assert payload["coverage"]["markets_configured"] >= 3
    assert payload["coverage"]["markets_serving"] >= 1


def test_freshness_window_is_consistent_with_classifier():
    # The classifier's recency window and coverage's freshness window must agree, so "current"
    # means the same thing on a coverage page and on an opportunity.
    from oppintel.config import active_trade

    assert int(active_trade().scoring.get("recent_permit_days", 180)) == FRESHNESS_WINDOW_DAYS
