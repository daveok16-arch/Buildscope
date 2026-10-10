"""Display transforms (WP3 K2).

Source text arrives shouted and whitespace-damaged; these tests pin the conservative rules:
only shouting is re-cased, acronyms and identifiers survive, real prose is untouched, and a
missing field is reported once rather than repeated.
"""

from __future__ import annotations

import pytest

from oppintel.app.display import (
    clean_title,
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


# --- clean_title: display-layer headline cleanup (WP3 M3/M4) -------------------

def test_clean_title_rejoins_a_figure_run_into_a_word():
    # The Monticello dossier title: "is16,297" is a run-together figure, not a word.
    assert clean_title("2 Story Restaurant in Uptown on McKinney Ave. It is16,297 SF") == (
        "2 Story Restaurant in Uptown on McKinney Ave. It is 16,297 SF"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        # A leading meeting-note marker is not part of the identity.
        ("QTEAM MEETING TBD - Ground-up new construction of an office building.",
         "Ground-up new construction of an office building."),
        ("QTEAM INHOUSE - Property Manager Remodel - SHELL: MEP and Grease Trap Install",
         "Property Manager Remodel - SHELL: MEP and Grease Trap Install"),
    ],
)
def test_clean_title_strips_a_leading_meeting_note(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_reduces_a_verbose_description_to_its_first_sentence():
    raw = (
        "QTEAM MEETING TBD - Ground-up new construction of a single-story office and warehouse "
        "building totaling 10,500 square feet. The building consists of a pre-engineered metal "
        "building (PEMB) structure."
    )
    assert clean_title(raw) == (
        "Ground-up new construction of a single-story office and warehouse building totaling "
        "10,500 square feet."
    )


def test_clean_title_does_not_split_a_thousands_figure():
    # "2,765 SF" must survive: the comma is inside a number, not a clause boundary.
    raw = (
        "Standard Review: Ground-up construction of a new 2,765 SF Jack in the Box restaurant, "
        "including architectural, structural, mechanical and plumbing work for the site."
    )
    assert "2,765 SF" in clean_title(raw)


def test_clean_title_does_not_split_an_abbreviation():
    raw = (
        "DSST-DISD-STAR-Todd/Young School: The Dr. Frederick Douglass Todd/Whitney M. Young "
        "project is a new ground-up PreK-8 school building for the district."
    )
    assert clean_title(raw).startswith("DSST-DISD-STAR-Todd/Young School: The Dr. Frederick")


def test_clean_title_leaves_an_identifier_and_a_capacity_untouched():
    assert clean_title("PERMIT PB25-09094 REMODEL") == "Permit PB25-09094 Remodel"
    assert clean_title("INSTALL 240V 40A CIRCUIT") == "Install 240V 40A Circuit"


def test_clean_title_never_invents_a_value():
    assert clean_title(None) == ""
    assert clean_title("   ") == ""


def test_clean_title_keeps_a_short_honest_name_whole():
    # A compact title is not a prose description; it must not be truncated.
    assert clean_title("MURPHY MARKETPLACE - WEST ADDITION") == "Murphy Marketplace - West Addition"
