"""Display transforms (WP3 K2).

Source text arrives shouted and whitespace-damaged; these tests pin the conservative rules:
only shouting is re-cased, acronyms and identifiers survive, real prose is untouched, and a
missing field is reported once rather than repeated.
"""

from __future__ import annotations

import pytest

from oppintel.app.display import (
    has_value,
    join_missing,
    present_fields,
    titlecase,
)


# --- titlecase: shouting is re-cased -------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("WINDMILL HILL ADDITION", "Windmill Hill Addition"),
        ("MARCELLA GREEN ELEMENTARY SCHOOL NO.9", "Marcella Green Elementary School NO.9"),
        ("PROSPER MIDDLE SCHOOL NO 2 ADDITION", "Prosper Middle School No 2 Addition"),
        ("SHADOWBEND PHASE 2", "Shadowbend Phase 2"),
        ("CUSTER ROAD ADDITION", "Custer Road Addition"),
    ],
)
def test_shouting_is_title_cased(raw, expected):
    assert titlecase(raw) == expected


# --- titlecase: acronyms and codes survive -------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HVAC REPLACEMENT AT RTU-1", "HVAC Replacement at RTU-1"),
        ("NEW AHU AND VAV SYSTEM", "New AHU and VAV System"),
        ("100 CRESCENT CT, TX", "100 Crescent Ct, TX"),
    ],
)
def test_acronyms_and_units_are_preserved(raw, expected):
    assert titlecase(raw) == expected


def test_identifier_with_digits_is_untouched():
    assert titlecase("PERMIT PB25-09094 REMODEL") == "Permit PB25-09094 Remodel"


def test_installed_capacity_is_untouched():
    # "240V" and "40A" carry digits, so they are not re-cased.
    assert titlecase("INSTALL 240V 40A CIRCUIT") == "Install 240V 40A Circuit"


# --- titlecase: real prose is left alone ---------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "MCR Novem Wealth Management - Commercial Interior Remodel - 3,678 USF/4,289 RSF",
        "New construction of a dumpster enclosure to the oil change building PB25-09094",
        "REPLACE 25 WINDOWS(LIKE FOR LIKE)",  # mixed: has a lowercase-free shape but prose intent
    ],
)
def test_prose_with_lowercase_is_not_re_cased(raw):
    # A string with lowercase letters is treated as intentional; titlecase("REPLACE...") has no
    # lowercase so it *is* shouting -> re-cased, which is why it is asserted separately below.
    if any(ch.islower() for ch in raw):
        assert titlecase(raw) == raw

    else:
        assert titlecase(raw).startswith("Replace")


# --- titlecase: whitespace cleanup ---------------------------------------------

def test_whitespace_and_newlines_collapse():
    assert titlecase("Install (1)\r\nLevel Two\r\nCharging") == "Install (1) Level Two Charging"


def test_space_before_comma_is_removed():
    assert titlecase("100 CRESCENT CT , 550") == "100 Crescent Ct, 550"


def test_missing_value_renders_empty():
    assert titlecase(None) == ""
    assert titlecase("   ") == ""


# --- has_value ------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("Not verified", False),
        ("0", True),
        (0, True),
        ("Fort Worth", True),
    ],
)
def test_has_value(value, expected):
    assert has_value(value) is expected


# --- present_fields -------------------------------------------------------------

def test_present_fields_splits_verified_from_missing():
    present, missing = present_fields(
        [
            ("Declared value", "$2,500,000"),
            ("Project type", None),
            ("Building scale", "Not verified"),
            ("Permit date", "10 Sep 2026"),
        ]
    )
    assert [label for label, _ in present] == ["Declared value", "Permit date"]
    assert missing == ["Project type", "Building scale"]


def test_join_missing_is_a_human_list():
    assert join_missing(["Declared value"]) == "Declared value"
    assert join_missing(["Declared value", "Building scale"]) == (
        "Declared value and Building scale"
    )
    assert join_missing(["A", "B", "C"]) == "A, B and C"
    assert join_missing([]) == ""
