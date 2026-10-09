"""Headline statistics come from one stored snapshot, not per-page counts.

The property under test is that a headline figure is computed once and read everywhere, so
the home page, `/api/statistics`, the market pages and `/healthz` cannot disagree about the
same word. These tests run against the real service over the fixture database.
"""

from __future__ import annotations

import json

from oppintel.app.stat_snapshot import (
    METRIC_DEFINITIONS,
    compute_metrics,
    read_snapshot,
    write_snapshot,
)
from oppintel.config import active_market, active_trade
from oppintel.db import Database


def _snapshot_row(app_db):
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        return db.conn.execute(
            "SELECT * FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
            (active_market().id, active_trade().id),
        ).fetchone()
    finally:
        db.close()


def test_snapshot_table_is_created_by_the_app_schema(app_db):
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_stat_snapshot'"
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "APP_SCHEMA must declare the stats snapshot table"


def test_homepage_populates_the_snapshot(app_db, client):
    """A read is never a 500 on a fresh database: the row is written on first read."""
    client.get("/")
    assert _snapshot_row(app_db) is not None


def test_home_and_api_and_healthz_report_the_same_project_count(app_db, client):
    """One source of truth: the same word means the same number on every surface."""
    home = client.get("/").get_data(as_text=True)
    api = client.get("/api/statistics").get_json()["statistics"]
    health = client.get("/healthz").get_json()

    public = api["public_projects"]
    # The home page renders the public-project figure, comma-formatted.
    assert f"{public:,}" in home
    # The probe reports the all-projects total from the same snapshot.
    assert health["projects"] == api["projects_total"]


def test_snapshot_is_reused_rather_than_recomputed(app_db, client):
    """Two reads return the same computed_at: the second did not recompute."""
    first = client.get("/api/statistics").get_json()["statistics"]["computed_at"]
    second = client.get("/api/statistics").get_json()["statistics"]["computed_at"]
    assert first == second


def test_records_count_is_the_public_linked_permits(fixture_db):
    """'Permit records' is the canonical public count, not every ingested row."""
    market, trade = active_market(), active_trade()
    metrics = compute_metrics(fixture_db, market, trade)
    assert metrics["permit_records"] <= metrics["linked_permits"]
    assert metrics["linked_permits"] <= metrics["permit_records_all"]


def test_active_jurisdictions_counts_only_configured_cities(fixture_db):
    """An out-of-market city name cannot inflate the headline; it is reported separately."""
    market, trade = active_market(), active_trade()
    metrics = compute_metrics(fixture_db, market, trade)
    configured = {c.lower() for c in market.city_names}
    for city in market.city_names:
        assert city  # sanity
    # Every counted city must be a configured one (the count is over configured names).
    assert metrics["active_jurisdictions"] <= len(market.city_names)
    for excluded in metrics["jurisdictions_excluded"]:
        assert excluded.lower() not in configured


def test_funnel_reconciles_landed_to_public(fixture_db):
    market, trade = active_market(), active_trade()
    funnel = compute_metrics(fixture_db, market, trade)["funnel"]
    assert funnel["permits_landed"] == funnel["permits_linked"] + funnel["permits_dropped"]
    assert funnel["by_source"], "the funnel must break down by source"
    # Each source row resolves a publisher name so the funnel is readable, never a config key.
    for row in funnel["by_source"]:
        assert row["source_name"]
        assert row["landed"] == row["linked"] + row["dropped"]


def test_write_snapshot_is_idempotent(fixture_db):
    market, trade = active_market(), active_trade()
    write_snapshot(fixture_db, market, trade)
    write_snapshot(fixture_db, market, trade)
    n = fixture_db.conn.execute(
        "SELECT COUNT(*) FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
        (market.id, trade.id),
    ).fetchone()[0]
    assert n == 1


def test_every_canonical_metric_has_a_definition(fixture_db):
    """A headline without a definition is a number a reader cannot interpret."""
    market, trade = active_market(), active_trade()
    metrics = compute_metrics(fixture_db, market, trade)
    for key in METRIC_DEFINITIONS:
        assert key in metrics, key
        label, definition = METRIC_DEFINITIONS[key]
        assert label and definition


def test_how_it_works_shows_the_ingest_funnel(client):
    """The pipeline description is accountable: the funnel is on the page."""
    body = client.get("/how-it-works").get_data(as_text=True)
    assert "The ingest funnel, stated plainly" in body
    assert "Permits landed" in body
    assert "Dropped (not assembled)" in body


def test_snapshot_json_round_trips(fixture_db):
    market, trade = active_market(), active_trade()
    write_snapshot(fixture_db, market, trade)
    read = read_snapshot(fixture_db, market, trade)
    assert read["public_projects"] == compute_metrics(fixture_db, market, trade)["public_projects"]
    assert isinstance(read["funnel"], dict)
