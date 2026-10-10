"""Semantic date validation.

A date on its own is ambiguous. "2031-04-01" is a data error when it is an *issue* date — the
city cannot have issued a permit that has not been filed — but it is entirely ordinary when it
is an *expiration* date or a planned completion. Treating every date the same, as the first
version of the quality check did, cannot tell those apart, so it either under-reports a real
defect or flags a correct value.

This module gives a stored date field a semantic kind, then judges a value against that kind
and against the moment it was observed. It never rewrites the value: the source's own date is
preserved exactly, and the verdict is a separate statement about it.

The distinction the product needs is:

* a date that has already happened (the normal case);
* a date in the near future that the field's semantics make plausible — a permit expiry, a
  planned start, a scheduled inspection;
* a date in the future that the field's semantics make impossible — an issue or filing date,
  because a record cannot be filed after it is observed;
* a date so far in the past it is more likely a parse artefact than a real filing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: How far ahead a *planned* date may reasonably sit. Public construction programmes rarely
#: publish a firm milestone more than a few years out; beyond this the value is more likely a
#: typo (a wrong decade) than a genuine schedule.
PLANNED_HORIZON_DAYS = 3 * 365

#: How far back a *historical* date may sit before it stops being a usable opportunity. A
#: permit issued before this is not evidence of current work.
HISTORICAL_HORIZON_DAYS = 20 * 365

# --- semantic kinds -----------------------------------------------------------

#: A date that records something that has already occurred at the source: an issue, filing,
#: application or permit date. It cannot legitimately be later than the observation moment.
KIND_OCCURRENCE = "occurrence"
#: A date that names a future moment by design: an expiration, a scheduled inspection, a
#: planned start or completion.
KIND_PLANNED = "planned"
#: The moment the record was retrieved or last verified.
KIND_OBSERVATION = "observation"
#: A date whose meaning is not known. Judged leniently, because the wrong rule is worse than
#: no rule.
KIND_UNKNOWN = "unknown"

#: Field-name fragments mapped to a semantic kind. Matched by substring so a new column that
#: ends in `_date` inherits a sensible default rather than silently becoming KIND_UNKNOWN.
_KIND_HINTS: tuple[tuple[str, str], ...] = (
    ("expir", KIND_PLANNED),
    ("planned", KIND_PLANNED),
    ("scheduled", KIND_PLANNED),
    ("target", KIND_PLANNED),
    ("complete_by", KIND_PLANNED),
    ("retrieval", KIND_OBSERVATION),
    ("observed", KIND_OBSERVATION),
    ("verified", KIND_OBSERVATION),
    ("fetched", KIND_OBSERVATION),
    ("updated", KIND_OBSERVATION),
    ("issue", KIND_OCCURRENCE),
    ("issued", KIND_OCCURRENCE),
    ("filed", KIND_OCCURRENCE),
    ("filing", KIND_OCCURRENCE),
    ("application", KIND_OCCURRENCE),
    ("submitted", KIND_OCCURRENCE),
    ("permit_date", KIND_OCCURRENCE),
    ("source_date", KIND_OCCURRENCE),
    ("status_date", KIND_OCCURRENCE),
)

# --- verdicts -----------------------------------------------------------------

VERDICT_OK = "ok"
VERDICT_FUTURE_PLAUSIBLE = "future_plausible"
VERDICT_FUTURE_IMPLAUSIBLE = "future_implausible"
VERDICT_ANCIENT = "ancient"


@dataclass(frozen=True)
class DateVerdict:
    """What can be said about one stored date value."""

    field_name: str
    kind: str
    value: date | None
    observed: date
    verdict: str
    reason: str

    @property
    def is_problem(self) -> bool:
        return self.verdict in (VERDICT_FUTURE_IMPLAUSIBLE, VERDICT_ANCIENT)

    @property
    def is_future(self) -> bool:
        return self.value is not None and self.value > self.observed


def semantic_kind(field_name: str) -> str:
    """The semantic kind of a stored date field, from its name."""
    name = (field_name or "").strip().lower()
    for fragment, kind in _KIND_HINTS:
        if fragment in name:
            return kind
    return KIND_UNKNOWN


def validate_date(
    field_name: str,
    value: date | None,
    *,
    observed: date,
    kind: str | None = None,
) -> DateVerdict:
    """Judge one date value against its field's semantics and the observation date.

    A missing value is not a problem: an absent date is an honest gap, not an error. Only a
    present value is judged.
    """
    resolved_kind = kind or semantic_kind(field_name)
    if value is None:
        return DateVerdict(
            field_name=field_name, kind=resolved_kind, value=None, observed=observed,
            verdict=VERDICT_OK, reason="No date recorded; treated as missing, not as an error.",
        )

    # A future date is judged by what the field means, never by the date alone.
    if value > observed:
        lead = (value - observed).days
        if resolved_kind in (KIND_PLANNED, KIND_OBSERVATION):
            if lead <= PLANNED_HORIZON_DAYS:
                return DateVerdict(
                    field_name=field_name, kind=resolved_kind, value=value, observed=observed,
                    verdict=VERDICT_FUTURE_PLAUSIBLE,
                    reason=(
                        f"{field_name} is {lead} day(s) ahead of the observation date. The "
                        f"field names a forward-looking moment, so this is plausible."
                    ),
                )
            return DateVerdict(
                field_name=field_name, kind=resolved_kind, value=value, observed=observed,
                verdict=VERDICT_FUTURE_IMPLAUSIBLE,
                reason=(
                    f"{field_name} is {lead} day(s) ahead of the observation date, beyond the "
                    f"{PLANNED_HORIZON_DAYS}-day horizon for a forward-looking field. Treat as "
                    f"a likely data error."
                ),
            )
        # An occurrence cannot be observed before it happens. The record would have to describe
        # an event that has not occurred, so the product must not present it as a past filing.
        return DateVerdict(
            field_name=field_name, kind=resolved_kind, value=value, observed=observed,
            verdict=VERDICT_FUTURE_IMPLAUSIBLE,
            reason=(
                f"{field_name} is {lead} day(s) after the observation date, but the field "
                f"records something that has already happened. A record cannot be filed after "
                f"it is observed, so this value is not evidence of a past event."
            ),
        )

    if (observed - value).days > HISTORICAL_HORIZON_DAYS:
        return DateVerdict(
            field_name=field_name, kind=resolved_kind, value=value, observed=observed,
            verdict=VERDICT_ANCIENT,
            reason=(
                f"{field_name} is more than {HISTORICAL_HORIZON_DAYS} days before the "
                f"observation date, so it is too old to evidence current work."
            ),
        )

    return DateVerdict(
        field_name=field_name, kind=resolved_kind, value=value, observed=observed,
        verdict=VERDICT_OK, reason=f"{field_name} is consistent with the observation date.",
    )


def is_usable_occurrence(value: date | None, *, observed: date) -> bool:
    """True when an occurrence date can be treated as a filing that has already happened.

    Used by classification: only a date that is not in the future may earn "filed recently",
    because a future date cannot describe a filing.
    """
    return value is not None and value <= observed


def parse_iso_date(value: object) -> date | None:
    """Parse a stored ISO date (``YYYY-MM-DD`` prefix) to a ``date``, or None.

    Stored dates are ISO strings; only the date part is meaningful here. A value that is not a
    well-formed calendar date returns None rather than raising, so a caller judging a stored
    field never has to guess whether a malformed value is "old" or "future".
    """
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def usable_occurrence_sql(alias: str = "", *, placeholder: str = "?") -> str:
    """A SQL predicate that keeps only a *usable* occurrence date: missing, or not in the future.

    A permit dated after the observation day cannot describe a filing that has already happened,
    so it must not be allowed to set a "newest permit" boundary, sort to the top of a
    most-recent list, or fill a freshness banner. Every such query needs the same guard, so it
    is written once here rather than re-derived per query and drifting.

    The observation day is bound as a parameter (``placeholder``) by the caller, so the caller
    controls "today" and a test can pin it. A NULL date passes the guard: an absent date is an
    honest gap, and SQL ``MAX``/``ORDER BY`` already ignore it.
    """
    column = f"{alias}.permit_date" if alias else "permit_date"
    return f"({column} IS NULL OR {column} <= {placeholder})"


def occurrence_is_future(value: object, *, observed: date | None = None) -> bool:
    """True when a stored occurrence date (a permit/filing date) is after the observation day.

    A permit cannot be filed after the record describing it was observed, so a future value is
    unverifiable as a filing rather than simply "new". A missing or malformed value is not
    future (it is an honest gap or a separate defect), so the caller must test for absence
    itself.
    """
    parsed = parse_iso_date(value)
    if parsed is None:
        return False
    return parsed > (observed or date.today())
