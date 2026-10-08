"""First-party search analytics.

Exercises the real database path: events are written with `record_analytics` and read back
through the reporting queries. The privacy property under test is that a query event carries a
per-visit session token and no user identity.
"""

from __future__ import annotations

from oppintel.app.accounts import record_analytics
from oppintel.app.search_analytics import (
    analytics_report,
    filter_usage,
    search_totals,
    session_depth,
    top_queries,
    zero_result_queries,
)


def _record(db, **kw):
    record_analytics(db, "search_performed", **kw)


def test_zero_result_queries_are_reported(fixture_db):
    _record(fixture_db, query_text="aircraft hangar", result_count=0)
    _record(fixture_db, query_text="aircraft hangar", result_count=0)
    _record(fixture_db, query_text="office tower", result_count=12)
    rows = zero_result_queries(fixture_db)
    assert [r["term"] for r in rows] == ["aircraft hangar"]
    assert rows[0]["searches"] == 2


def test_top_queries_aggregate_case_and_whitespace(fixture_db):
    _record(fixture_db, query_text="Office  Tower", result_count=5)
    _record(fixture_db, query_text="office tower", result_count=7)
    rows = top_queries(fixture_db)
    assert rows[0]["term"] == "office tower"
    assert rows[0]["searches"] == 2
    assert rows[0]["avg_results"] == 6.0


def test_filter_usage_counts_each_filter(fixture_db):
    _record(fixture_db, query_text="a", result_count=1, filter_summary="city,query")
    _record(fixture_db, query_text="b", result_count=1, filter_summary="city,mechanical_only")
    usage = {r["filter"]: r["uses"] for r in filter_usage(fixture_db)}
    assert usage == {"city": 2, "query": 1, "mechanical_only": 1}


def test_search_totals_reports_zero_result_rate(fixture_db):
    _record(fixture_db, query_text="a", result_count=0, session_id="s1")
    _record(fixture_db, query_text="b", result_count=3, session_id="s1")
    _record(fixture_db, query_text="c", result_count=3, session_id="s2")
    totals = search_totals(fixture_db)
    assert totals["searches"] == 3
    assert totals["zero_results"] == 1
    assert totals["zero_result_rate"] == 0.333
    assert totals["sessions"] == 2


def test_session_depth_groups_events_by_visit(fixture_db):
    for _ in range(3):
        _record(fixture_db, query_text="x", result_count=1, session_id="deep")
    _record(fixture_db, query_text="y", result_count=1, session_id="shallow")
    depth = {r["session_id"]: r["events"] for r in session_depth(fixture_db)}
    assert depth == {"deep": 3, "shallow": 1}


def test_query_text_is_bounded_and_whitespace_collapsed(fixture_db):
    _record(fixture_db, query_text="  lots   of\nspace  ", result_count=1)
    row = fixture_db.conn.execute(
        "SELECT query_text FROM analytics_event ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["query_text"] == "lots of space"


def test_analytics_report_is_complete(fixture_db):
    _record(fixture_db, query_text="q", result_count=2, session_id="s", filter_summary="query")
    report = analytics_report(fixture_db)
    assert report["totals"]["searches"] == 1
    assert report["window_days"] == 90
    assert set(report) == {
        "window_days", "totals", "top_queries", "zero_result_queries", "filter_usage",
        "session_depth",
    }


def test_search_event_carries_no_user_identity(client, app_db):
    # A search performed through the HTTP layer records a session token and no user id.
    from oppintel.db import Database

    # "industrial" is a structured term the interpreter rewrites into a project-type filter; the
    # raw words must still be what analytics records.
    client.get("/opportunities?q=industrial")
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        row = db.conn.execute(
            "SELECT * FROM analytics_event WHERE event_name = 'search_performed' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        db.close()
    assert row["query_text"] == "industrial"
    assert row["session_id"]
    columns = row.keys()
    assert "user_id" not in columns
    assert "ip_address" not in columns
