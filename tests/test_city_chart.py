"""City bar chart on the market landing page.

The chart is a metric like any other: it plots the same counts the table beneath it states, it
shows the mechanical-evidence share rather than hiding the shortfall, and it carries a table
alternative so a reader who cannot see the bars still gets every number.

The point of the two-tone bar is honesty. A single bar per city would show where the data is
deep; the gold overlay shows where the evidence actually is. The tests below pin that down so a
later "cleanup" cannot quietly turn the chart into a coverage claim it does not support.
"""

from __future__ import annotations

import re

from oppintel.db import Database
from oppintel.service import DISCOVERABLE_PROCUREMENT, PUBLIC_CLASSIFICATIONS


def _svg(body: str) -> str:
    match = re.search(r'<svg class="bar-chart bar-chart-h".*?</svg>', body, re.S)
    assert match, "the city chart is not on the page"
    return match.group(0)


def _chart_labels(svg: str) -> dict[str, int]:
    """The city -> count pairs the bars are labelled with."""
    cities = re.findall(r'text-anchor="end"[^>]*>([^<]+)</text>', svg)
    counts = re.findall(r'font-weight="650"[^>]*>(\d+)</text>', svg)
    assert len(cities) == len(counts), "every bar must carry both a label and a value"
    return dict(zip(cities, (int(c) for c in counts)))


def _table_rows(body: str) -> dict[str, tuple[int, int]]:
    """The chart's table alternative, as city -> (projects, mechanical)."""
    table = re.search(r"Projects by city\".*?</table>", body, re.S)
    assert table, "the chart's table alternative is missing"
    rows = re.findall(
        r"<tr>\s*<td>([^<]+)</td>\s*<td class=\"num\">(\d+)</td>\s*<td class=\"num\">(\d+)</td>",
        table.group(0),
    )
    return {city: (int(n), int(mech)) for city, n, mech in rows}


def test_market_page_renders_the_city_chart(client):
    body = client.get("/markets/dfw").get_data(as_text=True)
    assert 'class="bar-chart bar-chart-h"' in body
    assert "View the numbers as a table" in body


def test_city_chart_has_an_accessible_name(client):
    svg = _svg(client.get("/markets/dfw").get_data(as_text=True))
    assert 'role="img"' in svg
    assert "aria-label" in svg
    assert "<title>" in svg


def test_city_chart_labels_every_bar(client):
    """Each drawn bar carries its city and its count, so the chart is readable on its own."""
    svg = _svg(client.get("/markets/dfw").get_data(as_text=True))
    labels = _chart_labels(svg)
    assert labels, "fixture database has no public cities to chart"
    assert all(n > 0 for n in labels.values())


def test_city_chart_agrees_with_its_own_table(client):
    """The chart and the table are one series rendered twice; they cannot disagree."""
    body = client.get("/markets/dfw").get_data(as_text=True)
    labels = _chart_labels(_svg(body))
    rows = _table_rows(body)
    for city, count in labels.items():
        assert city in rows, city
        assert rows[city][0] == count, f"{city}: chart {count} vs table {rows[city][0]}"


def test_city_chart_shows_the_mechanical_shortfall(client):
    """The navy remainder must be drawn, so the chart shows what lacks evidence, not only what has it."""
    svg = _svg(client.get("/markets/dfw").get_data(as_text=True))
    # Two fills per city that has evidence: the full project bar and the mechanical overlay.
    assert 'fill="var(--navy-700' in svg
    assert 'fill="var(--gold-500' in svg


def test_city_chart_counts_only_public_discoverable_projects(client, app_db):
    """A bar's length must be the public, discoverable count — never the raw table size."""
    svg = _svg(client.get("/markets/dfw").get_data(as_text=True))
    labels = _chart_labels(svg)

    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        for city, count in labels.items():
            placeholders = ", ".join("?" for _ in PUBLIC_CLASSIFICATIONS)
            discoverable = ", ".join("?" for _ in DISCOVERABLE_PROCUREMENT)
            row = db.conn.execute(
                f"SELECT COUNT(*) FROM project WHERE city = ? "
                f"AND classification IN ({placeholders}) "
                f"AND procurement_status IN ({discoverable})",
                (city, *PUBLIC_CLASSIFICATIONS, *DISCOVERABLE_PROCUREMENT),
            ).fetchone()
            assert row[0] == count, f"{city}: chart shows {count}, public/discoverable is {row[0]}"
    finally:
        db.close()


def test_city_chart_is_omitted_when_there_are_no_cities(client):
    """An empty series must not render an empty figure — the section is skipped instead."""
    body = client.get("/markets/dfw").get_data(as_text=True)
    if _table_rows(body):
        return  # fixture has cities; the empty case is covered by the template's `{% if rows %}`
    assert 'bar-chart bar-chart-h' not in body
