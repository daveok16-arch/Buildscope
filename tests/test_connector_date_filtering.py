"""Connector `since` handling for the two historical Dallas sources.

Phase 12 defect A: `dallas_gis_permits` accepted a `since` argument and never used it — the
ArcGIS query was hard-coded `where=1=1`, so a bounded crawl still downloaded all history. The
Socrata connector built a predicate only when a bound was supplied, and the pipeline never
supplied one for a historical source, so its filter was dead code; it also used a strict `>`
that dropped rows dated exactly on the boundary.

These tests pin the corrected contract for both connectors, with the HTTP layer mocked so no
external service is contacted:

* the bound reaches the source's own server-side date filter where the source supports one;
* a client-side backstop drops a row that is *provably* out of window;
* a row the source left undated (or dated in an unknown format) is kept, not silently dropped;
* an absent or unusable bound widens the request rather than filtering on a wrong date.
"""

from __future__ import annotations

from datetime import date

from oppintel.config import load_sources
from oppintel.connectors.base import coerce_since, within_since
from oppintel.connectors.dallas_permits import (
    DallasGisPermitsConnector,
    DallasPermitsSocrataConnector,
)

BOUND = date(2024, 10, 9)


# --- the shared helpers ---------------------------------------------------------

def test_coerce_since_accepts_a_date_and_an_iso_string():
    assert coerce_since(date(2024, 10, 9)) == date(2024, 10, 9)
    assert coerce_since("2024-10-09") == date(2024, 10, 9)


def test_coerce_since_returns_none_for_absent_or_unusable_values():
    """A malformed bound must widen the request, never filter on a guess."""
    assert coerce_since(None) is None
    assert coerce_since("") is None
    assert coerce_since("not-a-date") is None


def test_within_since_is_inclusive_of_the_boundary():
    assert within_since("2024-10-09", BOUND) is True
    assert within_since("2024-10-08", BOUND) is False


def test_within_since_returns_none_for_an_undated_value():
    """None means "cannot be shown to be out of window", which the caller must keep."""
    assert within_since(None, BOUND) is None
    assert within_since("", BOUND) is None
    assert within_since("whenever", BOUND) is None


def test_within_since_with_no_bound_keeps_everything():
    assert within_since("1999-01-01", None) is True
    assert within_since(None, None) is True


# --- dallas_gis_permits (ArcGIS) ------------------------------------------------

def _gis_connector(max_pages: int = 5) -> DallasGisPermitsConnector:
    return DallasGisPermitsConnector(load_sources()["dallas_gis_permits"], {"max_pages": max_pages})


def _gis_feature(permit_no: str, issue_date: str | None) -> dict:
    return {
        "attributes": {
            "PERMIT_No": permit_no,
            "PERMIT_TYPE": "COMMERCIAL",
            "ACTIVITY": "BUILD",
            "ISSUE_DATE": issue_date,
        }
    }


def test_gis_puts_the_bound_into_the_arcgis_where_clause():
    """The bound must cut the download at the source, not trim rows after they arrive."""
    captured: list[dict] = []
    conn = _gis_connector()
    conn.get_json = lambda url, params=None: (captured.append(dict(params or {})), {"features": []})[1]

    list(conn.fetch_raw(since=BOUND))
    assert captured, "connector issued no request"
    assert captured[0]["where"] == "ISSUE_DATE >= timestamp '2024-10-09 00:00:00'"


def test_gis_without_a_bound_requests_everything():
    captured: list[dict] = []
    conn = _gis_connector()
    conn.get_json = lambda url, params=None: (captured.append(dict(params or {})), {"features": []})[1]

    list(conn.fetch_raw(since=None))
    assert captured[0]["where"] == "1=1"


def test_gis_accepts_a_string_bound_like_the_cli_passes():
    captured: list[dict] = []
    conn = _gis_connector()
    conn.get_json = lambda url, params=None: (captured.append(dict(params or {})), {"features": []})[1]

    list(conn.fetch_raw(since="2024-10-09"))
    assert "2024-10-09" in captured[0]["where"]


def test_gis_client_backstop_drops_a_provably_old_row_but_keeps_undated_and_current():
    """A source that ignores the predicate still cannot leak an out-of-window row into the store."""
    conn = _gis_connector()
    conn.get_json = lambda url, params=None: {
        "features": [
            _gis_feature("NEW", "2025-01-15"),
            _gis_feature("OLD", "2019-05-01"),
            _gis_feature("UNDATED", None),
        ]
    }

    keys = [raw.natural_key.split("::")[0] for raw in conn.fetch_raw(since=BOUND)]
    assert "NEW" in keys, "an in-window permit must be yielded"
    assert "UNDATED" in keys, "an undated permit cannot be shown to be out of window, so it is kept"
    assert "OLD" not in keys, "a permit dated before the bound must be dropped"


# --- dallas_permits_socrata -----------------------------------------------------

def _socrata_connector(max_pages: int = 5) -> DallasPermitsSocrataConnector:
    return DallasPermitsSocrataConnector(
        load_sources()["dallas_permits_socrata"], {"max_pages": max_pages}
    )


def _socrata_row(permit_number: str, issued_date: str | None) -> dict:
    return {
        "permit_number": permit_number,
        "permit_type": "COMMERCIAL",
        "issued_date": issued_date,
    }


def test_socrata_puts_the_bound_into_the_where_clause_inclusively():
    """`>=`, not `>`: a strict comparison dropped rows dated exactly on the boundary."""
    captured: list[dict] = []
    conn = _socrata_connector()
    conn.get_json = lambda url, params=None: (captured.append(dict(params or {})), [])[1]

    list(conn.fetch_raw(since=BOUND))
    assert captured[0]["$where"] == "issued_date >= '2024-10-09T00:00:00.000'"


def test_socrata_without_a_bound_omits_the_where_clause():
    captured: list[dict] = []
    conn = _socrata_connector()
    conn.get_json = lambda url, params=None: (captured.append(dict(params or {})), [])[1]

    list(conn.fetch_raw(since=None))
    assert "$where" not in captured[0]


def test_socrata_client_backstop_drops_a_provably_old_row_but_keeps_undated():
    conn = _socrata_connector()
    conn.get_json = lambda url, params=None: [
        _socrata_row("NEW", "2025-01-15T00:00:00.000"),
        _socrata_row("OLD", "2019-05-01T00:00:00.000"),
        _socrata_row("UNDATED", None),
    ]

    keys = [raw.natural_key.split("::")[0] for raw in conn.fetch_raw(since=BOUND)]
    assert "NEW" in keys
    assert "UNDATED" in keys
    assert "OLD" not in keys


def test_socrata_still_paginates_and_stops_at_a_short_page():
    """The fix must not disturb the existing paging contract."""
    calls: list[int] = []

    def fake_get_json(url, params=None):
        offset = int((params or {}).get("$offset", 0))
        calls.append(offset)
        # A full first page, then a short page that ends the crawl.
        if offset == 0:
            return [_socrata_row(f"P{i}", "2025-01-15T00:00:00.000") for i in range(3)]
        return [_socrata_row("P9", "2025-01-15T00:00:00.000")]

    conn = _socrata_connector()
    conn.page_size = 3
    conn.get_json = fake_get_json
    rows = list(conn.fetch_raw(since=BOUND))
    assert len(rows) == 4
    assert calls == [0, 3], "the crawl must advance by page size and stop on the short page"
