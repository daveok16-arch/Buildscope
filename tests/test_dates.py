"""Semantic date validation.

The behaviour under test is the distinction the product depends on: a future date is judged by
what its field means, not by the date alone.
"""

from datetime import date

from oppintel.dates import (
    KIND_OCCURRENCE,
    KIND_OBSERVATION,
    KIND_PLANNED,
    KIND_UNKNOWN,
    VERDICT_ANCIENT,
    VERDICT_FUTURE_IMPLAUSIBLE,
    VERDICT_FUTURE_PLAUSIBLE,
    VERDICT_OK,
    is_usable_occurrence,
    occurrence_is_future,
    parse_iso_date,
    semantic_kind,
    validate_date,
)

OBSERVED = date(2026, 10, 7)


def test_kind_from_field_name():
    assert semantic_kind("permit_date") == KIND_OCCURRENCE
    assert semantic_kind("source_date") == KIND_OCCURRENCE
    assert semantic_kind("issued_at") == KIND_OCCURRENCE
    assert semantic_kind("expiration_date") == KIND_PLANNED
    assert semantic_kind("scheduled_inspection") == KIND_PLANNED
    assert semantic_kind("retrieval_date") == KIND_OBSERVATION
    assert semantic_kind("last_verified") == KIND_OBSERVATION
    assert semantic_kind("mystery_date") == KIND_UNKNOWN


def test_missing_date_is_not_a_problem():
    verdict = validate_date("permit_date", None, observed=OBSERVED)
    assert verdict.verdict == VERDICT_OK
    assert not verdict.is_problem
    assert not verdict.is_future


def test_past_occurrence_is_ok():
    verdict = validate_date("permit_date", date(2026, 9, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_OK


def test_future_occurrence_is_implausible():
    verdict = validate_date("permit_date", date(2027, 1, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_FUTURE_IMPLAUSIBLE
    assert verdict.is_problem
    assert verdict.is_future


def test_future_planned_date_is_plausible_within_horizon():
    verdict = validate_date("expiration_date", date(2027, 1, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_FUTURE_PLAUSIBLE
    assert not verdict.is_problem


def test_future_planned_date_beyond_horizon_is_implausible():
    verdict = validate_date("expiration_date", date(2040, 1, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_FUTURE_IMPLAUSIBLE
    assert verdict.is_problem


def test_unknown_kind_future_date_is_implausible():
    # An unknown field is judged as an occurrence: the conservative choice, because treating a
    # future date as harmless could let a bad value through as a real filing.
    verdict = validate_date("mystery_date", date(2027, 1, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_FUTURE_IMPLAUSIBLE


def test_ancient_date_is_flagged():
    verdict = validate_date("permit_date", date(1990, 1, 1), observed=OBSERVED)
    assert verdict.verdict == VERDICT_ANCIENT
    assert verdict.is_problem


def test_observation_field_may_be_slightly_ahead():
    # Clock skew between the source and this host should not manufacture a defect.
    verdict = validate_date("retrieval_date", date(2026, 10, 8), observed=OBSERVED)
    assert verdict.verdict == VERDICT_FUTURE_PLAUSIBLE


def test_explicit_kind_overrides_name():
    verdict = validate_date(
        "permit_date", date(2027, 1, 1), observed=OBSERVED, kind=KIND_PLANNED
    )
    assert verdict.verdict == VERDICT_FUTURE_PLAUSIBLE


def test_is_usable_occurrence():
    assert is_usable_occurrence(date(2026, 1, 1), observed=OBSERVED)
    assert is_usable_occurrence(OBSERVED, observed=OBSERVED)
    assert not is_usable_occurrence(date(2027, 1, 1), observed=OBSERVED)
    assert not is_usable_occurrence(None, observed=OBSERVED)


# --- stored-value helpers (read-time labelling) --------------------------------

def test_parse_iso_date_handles_stored_strings():
    assert parse_iso_date("2026-12-19") == date(2026, 12, 19)
    assert parse_iso_date("2026-12-19T10:00:00Z") == date(2026, 12, 19)
    assert parse_iso_date(None) is None
    assert parse_iso_date("") is None
    assert parse_iso_date("not-a-date") is None
    # A partial value is malformed, not silently guessed.
    assert parse_iso_date("2026-1") is None


def test_occurrence_is_future_compares_against_today():
    assert occurrence_is_future("2027-01-01", observed=OBSERVED)
    assert not occurrence_is_future("2026-10-07", observed=OBSERVED)
    assert not occurrence_is_future("2026-01-01", observed=OBSERVED)
    # Absence and malformed values are not "future": they are a separate, honest gap/defect.
    assert not occurrence_is_future(None, observed=OBSERVED)
    assert not occurrence_is_future("not-a-date", observed=OBSERVED)


# --- SQL guard ----------------------------------------------------------------

def test_usable_occurrence_sql_keeps_null_and_past_and_drops_future():
    """The predicate that guards every "newest permit" query is written once, so it is tested
    once. It keeps a NULL date (an honest gap) and any date up to the bound day, and drops only
    a date strictly after it."""
    import sqlite3

    from oppintel.dates import usable_occurrence_sql

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE permit (permit_date TEXT)")
    conn.executemany(
        "INSERT INTO permit VALUES (?)",
        [("2026-09-30",), ("2026-10-07",), ("2026-12-19",), (None,)],
    )
    predicate = usable_occurrence_sql("")
    kept = [
        r[0]
        for r in conn.execute(
            f"SELECT permit_date FROM permit WHERE {predicate} ORDER BY permit_date",
            ("2026-10-09",),
        )
    ]
    assert kept == [None, "2026-09-30", "2026-10-07"]
    conn.close()
