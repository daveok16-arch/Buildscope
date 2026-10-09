"""Public copy states only what is verifiably true today.

A marketing line is a claim, and a claim the product cannot honour is a defect of the same kind
as a wrong number. This module pins the honesty contract:

* no template may promise a feature the product does not have ("saved searches"), guarantee an
  outcome ("zero false positives"), or describe collection as continuous when it is periodic;
* the change feed must state its own limitation on the page while real differences are zero.

The check reads the template *sources* rather than one rendered page, so a claim reintroduced in
a template that a test does not visit is still caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "oppintel" / "app" / "templates"

#: Phrases that assert something the product does not do. Each entry is (phrase, why).
BANNED_OVERCLAIMS: list[tuple[str, str]] = [
    ("saved search", "saved searches are not a built feature"),
    ("zero-false-positive", "an outcome guarantee the pipeline cannot make"),
    ("zero false positive", "an outcome guarantee the pipeline cannot make"),
    ("without false alerts", "an outcome guarantee the pipeline cannot make"),
    ("continuous", "collection is periodic, not continuous"),
    ("months before general contractors", "an unsupported timing claim"),
    ("commands buildscope to monitor the parcel", "implies per-parcel continuous monitoring"),
    ("organize active pursuits with your team", "no multi-user pursuit coordination exists"),
    ("coordinate team business development", "no multi-user pursuit coordination exists"),
    ("teams can bookmark", "organization membership is not exposed in any web route"),
    ("receive alerts", "delivery is not built; alerts are in-app and event-driven"),
    ("receive an alert", "delivery is not built; alerts are in-app and event-driven"),
]


def _template_texts() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(TEMPLATES)), path.read_text(encoding="utf-8").lower())
        for path in sorted(TEMPLATES.rglob("*.html"))
    ]


@pytest.mark.parametrize("phrase,why", BANNED_OVERCLAIMS)
def test_no_template_contains_a_banned_overclaim(phrase, why):
    offenders = [name for name, text in _template_texts() if phrase in text]
    assert offenders == [], f"'{phrase}' found in {offenders}: {why}"


def test_changes_page_states_the_honest_recording_rule(client):
    """The change feed says what it actually records, not what a reader might assume."""
    body = client.get("/changes").get_data(as_text=True)
    assert "We record every permit we observe and flag differences between collection runs." in body


def test_changes_page_shows_before_and_after_values(client):
    """A change entry is shown as a before/after pair, not a single summary string."""
    body = client.get("/changes").get_data(as_text=True)
    for header in ("Before", "After"):
        assert f">{header}<" in body


def test_changes_page_explains_a_zero_difference_feed(client):
    """While no real difference exists, the page says so rather than implying live churn."""
    body = client.get("/changes").get_data(as_text=True)
    assert "Collection began" in body
    assert "none have been recorded yet" in body.lower()


def test_signup_does_not_promise_saved_searches(client):
    body = client.get("/signup").get_data(as_text=True).lower()
    assert "saved search" not in body
