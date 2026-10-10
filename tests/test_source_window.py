"""Source `since` resolution and upstream date filtering.

The one-time seed must be bounded to a rolling window and the bound must reach the source's own
date filter, not just trim rows after download. These tests pin the resolver's precedence and
confirm the shipped configuration actually sets a default window on every current source.
"""

from __future__ import annotations

from datetime import date

from oppintel.config import SourceConfig, load_sources


def _cfg(**kw) -> SourceConfig:
    base = dict(id="s", name="S", publisher="P", kind="arcgis")
    base.update(kw)
    return SourceConfig.from_dict(base)


def test_no_since_means_all_history():
    assert _cfg().resolved_since(date(2026, 10, 9)) is None


def test_explicit_since_wins_over_months():
    c = _cfg(since="2024-01-15", since_months=6)
    assert c.resolved_since(date(2026, 10, 9)) == date(2024, 1, 15)


def test_since_months_is_measured_back_from_today():
    assert _cfg(since_months=24).resolved_since(date(2026, 10, 9)) == date(2024, 10, 9)
    assert _cfg(since_months=12).resolved_since(date(2026, 10, 9)) == date(2025, 10, 9)


def test_since_months_clamps_day_to_shorter_month():
    # 31 March back one month -> 28/29 Feb, never an invalid date.
    assert _cfg(since_months=1).resolved_since(date(2026, 3, 31)) == date(2026, 2, 28)


def test_shipped_sources_default_to_a_24_month_window():
    sources = load_sources()
    for sid in ("fort_worth_permits", "collin_cad_permits", "dallas_accela_permits"):
        assert sources[sid].since_months == 24, sid
        assert sources[sid].resolved_since(date(2026, 10, 9)) == date(2024, 10, 9)


def test_the_bound_reaches_the_arcgis_where_clause():
    """The Fort Worth connector must put `since` into the server-side `where`, not filter rows."""
    from oppintel.connectors.fort_worth_permits import FortWorthPermitsConnector

    captured: list[dict] = []

    def fake_get_json(url, params=None):
        captured.append(dict(params or {}))
        return {"features": []}

    from oppintel.config import load_sources

    conn = FortWorthPermitsConnector(load_sources()["fort_worth_permits"], {"max_pages": 1})
    conn.get_json = fake_get_json  # type: ignore[assignment]
    list(conn.fetch_raw(since=date(2024, 10, 9)))
    assert captured, "connector issued no request"
    assert captured[0]["where"] == "File_Date >= timestamp '2024-10-09 00:00:00'"


def test_pipeline_applies_the_configured_window_when_no_since_is_given(tmp_path, monkeypatch):
    """`Pipeline.ingest_source` resolves `since` from the source config, not just the CLI.

    Without this, `oppintel ingest` with no `--since` would fetch all history even though every
    source declares a 24-month window.
    """
    from oppintel.config import load_sources
    from oppintel.connectors import fort_worth_permits as fw
    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    seen: list[object] = []

    def fake_fetch(self, since=None, batch_size=None):
        seen.append(since)
        return iter(())

    monkeypatch.setattr(fw.FortWorthPermitsConnector, "fetch_raw", fake_fetch)
    monkeypatch.setattr(fw.FortWorthPermitsConnector, "landing_path", lambda self: tmp_path / "raw.jsonl")

    db = Database(tmp_path / "t.db")
    db.init_schema()
    Pipe = Pipeline(db)
    Pipe._register_sources()
    expected = load_sources()["fort_worth_permits"].resolved_since()
    Pipe.ingest_source("fort_worth_permits")
    db.close()

    assert seen == [expected], seen
    assert expected is not None, "a 24-month window must resolve to a date"

