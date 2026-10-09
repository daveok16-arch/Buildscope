"""Trend Radar tests.

Run against real SQLite databases built from synthetic permits — no mocks. The unit tests
exercise the pure period/metric arithmetic; the integration tests build a small database with
known dates and assert the counts, the future-date exclusion, the no-change exclusion, and the
"no comparison without history" rule.
"""

from __future__ import annotations

from datetime import date, timedelta


from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.search_index import rebuild_index
from oppintel.trends import (
    DEFAULT_WINDOW,
    MIN_OBSERVATIONS_FOR_TREND,
    Metric,
    Period,
    WINDOW_DAYS,
    build_trend_report,
    format_period,
    resolve_window,
)


# --- period arithmetic ---------------------------------------------------------

def test_window_is_exactly_n_calendar_days():
    period = resolve_window("7d", today=date(2026, 10, 7))
    assert period.days == 7
    assert period.start == date(2026, 10, 1)
    assert period.end == date(2026, 10, 8)


def test_window_includes_today_and_excludes_tomorrow():
    period = resolve_window("7d", today=date(2026, 10, 7))
    assert period.contains(date(2026, 10, 7))
    assert not period.contains(date(2026, 10, 8))


def test_previous_period_is_the_same_length_and_abuts_the_current_one():
    period = resolve_window("30d", today=date(2026, 10, 7))
    previous = period.previous()
    assert previous.days == period.days
    assert previous.end == period.start


def test_unknown_window_falls_back_to_the_default():
    assert resolve_window("nonsense", today=date(2026, 10, 7)) == resolve_window(
        DEFAULT_WINDOW, today=date(2026, 10, 7)
    )


def test_format_period_names_the_last_included_day():
    label = format_period(Period(date(2026, 10, 1), date(2026, 10, 8)))
    assert label == "2026-10-01 to 2026-10-07"


# --- metric arithmetic ---------------------------------------------------------

def _metric(value: int, previous: int | None) -> Metric:
    return Metric(key="k", label="K", definition="d", value=value, previous=previous)


def test_zero_baseline_has_no_ratio_rather_than_infinite():
    assert _metric(5, 0).change_ratio is None


def test_direction_reads_the_comparison():
    assert _metric(5, 3).direction == "up"
    assert _metric(3, 5).direction == "down"
    assert _metric(4, 4).direction == "flat"
    assert _metric(4, None).direction == "unknown"


# --- database-backed report ----------------------------------------------------

def _permit(number: str, permit_date: date, *, description: str = "New construction of an office building") -> Permit:
    return Permit(
        source_id="fort_worth_permits",
        permit_number=number,
        natural_key=number,
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=permit_date,
        status="Issued",
        address=f"{abs(hash(number)) % 900 + 100} ROSS AVE",
        city="Fort Worth",
        state="TX",
        work_description=description,
        land_use="OFFICE BUILDING",
        is_commercial=True,
        job_value=1_000_000.0,
        square_footage=20_000.0,
        source_url=f"https://example.gov/{number}",
        source_date=permit_date,
    )


def _db(tmp_path, permits) -> Database:
    db = Database(tmp_path / "trends.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for p in permits:
        db.upsert_permit(p, normalize_address(p.address))
    db.commit()
    pipeline.assemble_and_classify()
    rebuild_index(db)
    return db


def test_future_dated_permit_is_excluded_and_reported(tmp_path):
    today = date(2026, 10, 7)
    db = _db(tmp_path, [
        _permit("PAST", date(2026, 10, 5)),
        _permit("FUTURE", date(2026, 12, 1)),
    ])
    report = build_trend_report(db, window="30d", today=today)
    projects = report.metric_index["projects_observed"]
    # Only the past-dated permit falls in the window.
    assert projects.value == 1
    # The future-dated record is not silently dropped: it is counted as excluded.
    assert projects.excluded_future == 1
    db.close()


def test_future_date_inside_the_window_is_counted_as_excluded(tmp_path):
    """A permit dated between today and the window's end cannot evidence filed work."""
    # The window ends tomorrow; a permit dated tomorrow is inside [start, end) but after today.
    today = date(2026, 10, 7)
    db = _db(tmp_path, [_permit("TOMORROW", date(2026, 10, 8))])
    report = build_trend_report(db, window="30d", today=today)
    assert report.metric_index["projects_observed"].value == 0
    assert report.metric_index["projects_observed"].excluded_future == 1
    db.close()


def test_first_observation_is_not_counted_as_a_change(tmp_path):
    """A fresh assembly records `new_project` for everything; none of it is a *change*."""
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1)), _permit("B", date(2026, 10, 2))])
    # Pin the ingestion timestamps so the assertion does not depend on the wall clock.
    db.conn.execute("UPDATE project SET created_at = '2026-10-03T00:00:00+00:00'")
    db.conn.commit()
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    assert report.metric_index["projects_changed"].value == 0
    # They are, however, newly observed.
    assert report.metric_index["projects_newly_observed"].value == 2
    db.close()


def test_no_comparison_when_no_history_predates_the_previous_period(tmp_path):
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1))])
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    for m in report.metrics:
        assert m.comparison_available is False
        assert m.previous is None
        assert m.direction == "unknown"
    db.close()


def test_comparison_appears_once_history_reaches_back(tmp_path):
    """When created_at predates the previous period, a comparison is offered."""
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1))])
    old = (date(2026, 10, 7) - timedelta(days=120)).isoformat()
    db.conn.execute("UPDATE project SET created_at = ?", (old,))
    db.conn.commit()
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    assert report.metric_index["projects_observed"].comparison_available is True
    assert report.metric_index["projects_observed"].previous == 0
    db.close()


def test_metric_counts_are_kept_separate(tmp_path):
    """There is no combined 'opportunity' figure; each metric is its own count."""
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1), description="mechanical remodel of suite")])
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    keys = {m.key for m in report.metrics}
    assert {"projects_observed", "permits_observed", "projects_with_trade_scope",
            "projects_changed", "projects_newly_observed",
            "projects_with_strong_evidence"} <= keys
    assert "opportunities_total" not in keys
    db.close()


def test_report_states_its_own_limits(tmp_path):
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1))])
    report = build_trend_report(db, window="7d", today=date(2026, 10, 7))
    assert report.notes
    assert any("records BuildScope holds" in n for n in report.notes)
    assert any("never added together" in n for n in report.notes)
    db.close()


def test_empty_window_is_flagged_insufficient(tmp_path):
    db = _db(tmp_path, [_permit("A", date(2026, 1, 1))])
    report = build_trend_report(db, window="7d", today=date(2026, 10, 7))
    assert report.insufficient is True
    db.close()


def test_malformed_date_is_excluded_and_counted(tmp_path):
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1))])
    db.conn.execute("UPDATE project SET permit_date = 'not-a-date'")
    db.conn.commit()
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    assert report.malformed_dates == 1
    assert report.metric_index["projects_observed"].value == 0
    assert any("not a well-formed calendar date" in n for n in report.notes)
    db.close()


def test_window_days_are_the_documented_ones():
    # `all` maps to 0: it is the whole dataset, not a trailing period. It was added so the
    # page can show the dataset's own totals under an explicit "cumulative" label.
    assert WINDOW_DAYS == {"7d": 7, "30d": 30, "90d": 90, "all": 0}
    assert MIN_OBSERVATIONS_FOR_TREND >= 1


def test_all_time_window_covers_the_whole_dataset():
    period = resolve_window("all", today=date(2026, 10, 7))
    assert period.is_all_time is True
    assert period.contains(date(2026, 10, 7))
    assert period.contains(date(2019, 1, 1))
    # No previous period exists for an all-time window.
    assert period.previous().is_all_time is True
    assert period.previous().days == 0


def test_occurrence_metrics_are_cumulative_only_in_the_all_time_window(tmp_path):
    db = _db(tmp_path, [_permit("A", date(2026, 10, 1))])
    all_time = build_trend_report(db, window="all", today=date(2026, 10, 7))
    assert all_time.metric_index["projects_observed"].cumulative is True
    # Occurrence metrics declare they are only meaningful for a trailing period, so the page
    # can hide them under "all" rather than show a period figure with no period.
    assert "all" not in all_time.metric_index["projects_changed"].windows
    # A stock metric (evidence held) is meaningful for every window including "all".
    assert "all" in all_time.metric_index["projects_with_strong_evidence"].windows
    ranged = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    assert ranged.metric_index["projects_observed"].cumulative is False
    db.close()


# --- HTTP ---------------------------------------------------------------------

def test_trends_page_renders(client):
    resp = client.get("/trends")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Trend Radar" in body


def test_trends_page_accepts_a_window(client):
    resp = client.get("/trends?window=7d")
    assert resp.status_code == 200


def test_trends_page_ignores_an_unknown_window(client):
    resp = client.get("/trends?window=bogus")
    assert resp.status_code == 200


def test_trends_api_returns_definitions_with_values(client):
    resp = client.get("/api/trends")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["metrics"]
    for m in payload["metrics"]:
        assert m["definition"]
        assert "value" in m


def test_ingestion_window_newest_permit_excludes_future_dates(tmp_path):
    """The "records span" end date must not be a future-dated permit.

    A permit dated after today is a quality defect, and reporting it as the newest record held
    would state a filing date that cannot exist. The window reports the newest *usable* permit
    date instead.
    """
    db = _db(tmp_path, [
        _permit("PAST", date(2026, 10, 5)),
        _permit("FUTURE", date(2026, 12, 19)),
    ])
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    assert report.ingestion_window["newest_permit_date"] == "2026-10-05"
    db.close()


def test_every_metric_carries_a_basis_line(tmp_path):
    """Each card must name the population and period it counts, so two cards cannot be read
    as comparable when they are not (the source of the 'new projects vs public projects'
    confusion)."""
    db = _db(tmp_path, [])
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    for m in report.metrics:
        assert m.basis, f"{m.key} has no basis line"
        assert "period" in m.basis or "time" in m.basis
    db.close()


def test_new_projects_basis_says_it_ignores_classification(tmp_path):
    """'New commercial projects' is a window count over permit dates, not the home 'public
    projects' figure. The basis line must say so explicitly."""
    db = _db(tmp_path, [])
    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    basis = report.metric_index["projects_observed"].basis
    assert "regardless of classification" in basis
    db.close()


def test_new_projects_card_reports_the_public_subset(tmp_path):
    """D5: the 'new projects' card carries a second line naming the discoverable subset, so the
    all-observed headline is not read as the public count."""
    public = _permit("M1", date(2026, 9, 15), description="Mechanical remodel of spec suite")
    non_public = Permit(
        source_id="fort_worth_permits",
        permit_number="N1",
        natural_key="N1",
        permit_type="Commercial Building Permit",
        permit_subtype="commercial_building",
        permit_date=date(2026, 9, 16),
        status="Issued",
        address="20 OTHER ST",
        city="Fort Worth",
        state="TX",
        work_description="Tenant improvement with no mechanical work",
        land_use="OFFICE BUILDING",
        is_commercial=True,
        job_value=500_000.0,
        source_url="https://example.gov/N1",
        source_date=date(2026, 9, 16),
    )
    db = _db(tmp_path, [public, non_public])
    # The classifier is deliberately generous on a synthetic record, so force one project to a
    # non-discoverable classification to guarantee a strict subset deterministically.
    db.conn.execute(
        "UPDATE project SET classification = 'NEEDS_VERIFICATION' "
        "WHERE project_name = 'Tenant improvement with no mechanical work'"
    )
    db.conn.commit()

    report = build_trend_report(db, window="30d", today=date(2026, 10, 7))
    metric = report.metric_index["projects_observed"]
    assert metric.value == 2
    assert metric.secondary is not None
    label, value = metric.secondary
    assert label == "of which public"
    assert value == 1  # only the mechanical project is discoverable
    assert 0 < value < metric.value  # a strict subset, not the whole headline
    db.close()
