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
    heal_snapshot,
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


def test_app_startup_populates_the_snapshot(app_db):
    """The startup heal creates the row, so a read is never a 500 on a fresh database."""
    assert _snapshot_row(app_db) is not None


def test_request_never_writes_the_snapshot(app_db, client):
    """D2: a page request must not persist a snapshot row, even on a fresh database.

    The heal moved out of the request path, so a GET computes values (never 500) but the row
    is only created by startup / refresh, which run under the writer lock.
    """
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        market, trade = active_market(), active_trade()
        db.conn.execute(
            "DELETE FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
            (market.id, trade.id),
        )
        db.conn.commit()
        # A request renders the figures from a computed fallback, without storing anything.
        body = client.get("/").get_data(as_text=True)
        assert body  # 200 with content
        assert _snapshot_row(app_db) is None, "a GET must not create the snapshot row"
    finally:
        db.close()


def test_startup_heal_writes_the_snapshot_outside_requests(tmp_path):
    """D2: the startup heal creates/repairs the row with no request involved."""
    from tests.conftest_app import build_database

    db_path = tmp_path / "heal.db"
    db = build_database(db_path)
    db.close()

    # Seed a snapshot, then simulate a pre-upgrade row missing a canonical key.
    db = Database(db_path)
    market, trade = active_market(), active_trade()
    write_snapshot(db, market, trade)
    stored = json.loads(
        db.conn.execute(
            "SELECT metrics FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
            (market.id, trade.id),
        ).fetchone()["metrics"]
    )
    stored.pop("configured_jurisdictions", None)
    db.conn.execute(
        "UPDATE market_stat_snapshot SET metrics=? WHERE market_id=? AND trade_id=?",
        (json.dumps(stored), market.id, trade.id),
    )
    db.conn.commit()
    db.close()

    from oppintel.app.config import AppConfig
    from oppintel.app.main import create_app

    create_app(AppConfig(database_path=db_path, secret_key="k", debug=True))

    healed = json.loads(
        Database(db_path).conn.execute(
            "SELECT metrics FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
            (market.id, trade.id),
        ).fetchone()["metrics"]
    )
    assert "configured_jurisdictions" in healed


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


def test_jurisdiction_count_is_identical_on_every_surface(app_db, client):
    """'Cities with at least one public project' is one number on home, /markets, /api, meta and /healthz."""
    api = client.get("/api/statistics").get_json()["statistics"]
    jurisdictions = api["active_jurisdictions"]

    home = client.get("/").get_data(as_text=True)
    health = client.get("/healthz").get_json()
    markets = client.get("/markets").get_data(as_text=True)

    # The home meta description and the home stat strip both carry the same figure.
    assert f"{jurisdictions} " in home  # e.g. "15 Dallas–Fort Worth cities with at least one…"
    assert health["cities_with_public_projects"] == jurisdictions
    assert f"{jurisdictions} cities with at least one public project" in markets
    # The configured count is configuration intent, never smaller than the observed count.
    assert api["configured_jurisdictions"] >= jurisdictions


def test_snapshot_is_reused_rather_than_recomputed(app_db, client):
    """Two reads return the same computed_at: the second did not recompute."""
    first = client.get("/api/statistics").get_json()["statistics"]["computed_at"]
    second = client.get("/api/statistics").get_json()["statistics"]["computed_at"]
    assert first == second


def test_heal_snapshot_repairs_a_row_missing_a_new_canonical_key(app_db, client):
    """A snapshot written before a metric existed is recomputed and rewritten out of band.

    Deploying a new canonical metric must not leave the live snapshot (written by the previous
    code) serving `None` for it. `heal_snapshot` (startup / refresh) rewrites the row.
    """
    client.get("/")  # ensure a snapshot exists
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        market, trade = active_market(), active_trade()
        # Simulate the pre-upgrade row: drop a canonical key from the stored JSON.
        row = db.conn.execute(
            "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
            (market.id, trade.id),
        ).fetchone()
        stored = json.loads(row["metrics"])
        stored.pop("configured_jurisdictions", None)
        db.conn.execute(
            "UPDATE market_stat_snapshot SET metrics = ? WHERE market_id = ? AND trade_id = ?",
            (json.dumps(stored), market.id, trade.id),
        )
        db.conn.commit()

        metrics = heal_snapshot(db, market, trade)
        assert "configured_jurisdictions" in metrics
        # The heal persisted, so the next reader sees the key without recomputing.
        healed = json.loads(
            db.conn.execute(
                "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
                (market.id, trade.id),
            ).fetchone()["metrics"]
        )
        assert "configured_jurisdictions" in healed
    finally:
        db.close()


def test_heal_snapshot_is_idempotent_when_current(app_db, client):
    """A current row is not rewritten, so a restart does not churn `computed_at`."""
    client.get("/")
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        market, trade = active_market(), active_trade()
        before = db.conn.execute(
            "SELECT computed_at FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
            (market.id, trade.id),
        ).fetchone()["computed_at"]
        heal_snapshot(db, market, trade)
        after = db.conn.execute(
            "SELECT computed_at FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
            (market.id, trade.id),
        ).fetchone()["computed_at"]
        assert before == after
    finally:
        db.close()


def test_read_snapshot_never_writes_even_when_a_key_is_missing(app_db, client):
    """D2: the default read path is read-only, even when the row is missing a canonical key."""
    client.get("/")
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        market, trade = active_market(), active_trade()
        row = db.conn.execute(
            "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
            (market.id, trade.id),
        ).fetchone()
        stored = json.loads(row["metrics"])
        stored.pop("configured_jurisdictions", None)
        db.conn.execute(
            "UPDATE market_stat_snapshot SET metrics = ? WHERE market_id = ? AND trade_id = ?",
            (json.dumps(stored), market.id, trade.id),
        )
        db.conn.commit()

        metrics = read_snapshot(db, market, trade)
        assert "configured_jurisdictions" in metrics
        still_stored = json.loads(
            db.conn.execute(
                "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
                (market.id, trade.id),
            ).fetchone()["metrics"]
        )
        assert "configured_jurisdictions" not in still_stored
    finally:
        db.close()


def test_parallel_requests_do_not_write_a_stale_snapshot(app_db):
    """D2: N parallel page requests against a row missing a key write nothing and never 500."""
    import threading

    client = app_db.test_client()
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        market, trade = active_market(), active_trade()
        row = db.conn.execute(
            "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
            (market.id, trade.id),
        ).fetchone()
        stored = json.loads(row["metrics"])
        stored.pop("configured_jurisdictions", None)
        db.conn.execute(
            "UPDATE market_stat_snapshot SET metrics = ? WHERE market_id = ? AND trade_id = ?",
            (json.dumps(stored), market.id, trade.id),
        )
        db.conn.commit()
    finally:
        db.close()

    errors: list[str] = []
    paths = ["/", "/api/statistics", "/markets", "/healthz"]

    def hit(i: int) -> None:
        try:
            resp = client.get(paths[i % len(paths)])
            assert resp.status_code in (200, 302), resp.status_code
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=hit, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    # The stored row still lacks the key: no request handler persisted a heal.
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        stored_after = json.loads(
            db.conn.execute(
                "SELECT metrics FROM market_stat_snapshot WHERE market_id=? AND trade_id=?",
                (market.id, trade.id),
            ).fetchone()["metrics"]
        )
        assert "configured_jurisdictions" not in stored_after, (
            "a request handler wrote the snapshot"
        )
    finally:
        db.close()


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
    for city in metrics["out_of_market_cities"]:
        assert city.lower() not in configured


def test_out_of_market_cities_rename_matches_the_old_alias(fixture_db):
    """D4: the renamed key says which side of the market line it describes, and is the same value."""
    market, trade = active_market(), active_trade()
    metrics = compute_metrics(fixture_db, market, trade)
    assert metrics["out_of_market_cities_count"] == len(metrics["out_of_market_cities"])
    # Back-compat alias for the previous ambiguous name is the same list, never recomputed.
    assert metrics["jurisdictions_excluded"] == metrics["out_of_market_cities"]
    assert metrics["jurisdictions_excluded_count"] == metrics["out_of_market_cities_count"]


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
