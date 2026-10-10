"""WP3 M2 — home metric band reads one snapshot and animates without inventing a number.

The band's contract: every figure comes from `stat_snapshot` (one source of truth), the exact
value is present in the HTML (so it is correct with JS off), and the count-up animates to that
same value (`data-count`). A missing field is never rendered as a fabricated number.
"""

from __future__ import annotations

import re

from conftest_app import build_database

from oppintel.app.config import AppConfig
from oppintel.app.main import create_app


def _client(tmp_path):
    db_path = tmp_path / "m2.db"
    build_database(db_path).close()
    cfg = AppConfig(database_path=db_path, secret_key="m2-secret", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def test_metric_band_has_four_cells(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    assert 'class="metric-band"' in body
    assert body.count('class="metric-cell"') == 4


def test_count_up_target_matches_rendered_value(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    pairs = re.findall(r'data-count="(\d+)"[^>]*>([^<]+)<', body)
    assert pairs, "no data-count figures found"
    for target, rendered in pairs:
        assert target == rendered.replace(",", "").strip(), (target, rendered)


def test_metric_labels_and_definitions_present(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    for label in ("Public projects", "With mechanical evidence", "Permit records",
                  "Cities with projects"):
        assert label in body, label
    # the definitions travel with the figures as tooltips (one definitions table); the
    # attribute is HTML-escaped, so check a quote-free head of each definition.
    assert 'title="Public permit rows linked' in body
    assert 'title="Assembled commercial projects' in body


def test_band_states_its_as_of_and_links_to_trends(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    assert "as of" in body
    assert "/trends" in body
