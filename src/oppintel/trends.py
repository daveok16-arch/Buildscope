"""Trend Radar: defensible change in the records BuildScope actually holds.

The module answers "what changed in the observed records" without ever claiming more than the
rows support. Three rules shape it:

1. **Every metric is defined here, in one place, and its definition is returned with its value.**
   A number without its definition is an invitation to misread it, so the report carries the
   definition text a page renders next to the figure.

2. **Projects, permits, trade-evidence and changes are four different counts and are never
   merged.** A single project can carry several permits; a permit mentioning a trade is not a
   trade-verified project; a change is an observation of movement, not of new work. Collapsing
   them into one "opportunity" total would be the exact dishonesty the product exists to avoid.

3. **A count measures what BuildScope observed and when, not what a city published.** Records
   were ingested over a few days (see ``ingestion_window``), so a "new project" here means a
   permit dated inside the window, and a separate "newly observed" metric measures first
   appearance in the database. The two are labelled differently because they are different.

Future-dated permit dates are excluded from an occurrence metric: a record cannot evidence work
that has not been filed. The exclusion is counted and stated, never silent. A previous-period
comparison is reported only when the database actually holds history reaching back that far;
otherwise the report says the comparison is unavailable rather than printing a 0 that reads as
a decline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .db import Database

#: The label whose window is the whole dataset rather than a trailing period. An occurrence
#: metric over "all" is a cumulative total ("records held"), not a trend, and is labelled so.
ALL_TIME = "all"

#: Named comparison windows, in days. Kept small and fixed so a metric means the same thing
#: wherever it appears. `all` has no day count: it means the entire stored dataset.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("7d", 7),
    ("30d", 30),
    ("90d", 90),
    (ALL_TIME, 0),
)

#: Picking a window is a UI concern; this maps a label back to its day count. `all` maps to 0.
WINDOW_DAYS: dict[str, int] = {label: days for label, days in WINDOWS}

DEFAULT_WINDOW = "30d"

#: An early date that predates any permit a city could publish, used as the lower bound of the
#: "all time" window. It is a sentinel, not a presented value.
_EPOCH = date(1900, 1, 1)

#: A window with fewer observations than this is reported as insufficient for a trend
#: statement. Chosen low deliberately: it screens out "one record appeared" dressed as a trend
#: without suppressing genuine early signal. It is a floor on *confidence of the wording*, not
#: a hidden filter — the count itself is always shown.
MIN_OBSERVATIONS_FOR_TREND = 5

#: Occurrence metrics (a change, a first observation, a permit issued) are only meaningful
#: inside a period, so they are absent from the "all time" view. Stock metrics (projects held,
#: evidence held) are meaningful for every window including "all".
_PERIOD_WINDOWS: tuple[str, ...] = tuple(label for label, days in WINDOWS if days > 0)
_ALL_WINDOWS: tuple[str, ...] = tuple(label for label, _days in WINDOWS)
_OCCURRENCE_WINDOWS = _PERIOD_WINDOWS

#: SQL fragment: a well-formed `YYYY-MM-DD` date. Anything else is treated as missing rather
#: than compared as text, so a malformed value cannot land inside a period by accident.
_VALID_DATE = "p.permit_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"


@dataclass(frozen=True)
class Period:
    """A half-open date interval ``[start, end)`` on the observation calendar."""

    start: date
    end: date
    #: True when the period covers the whole dataset rather than a trailing window. Set by
    #: `resolve_window`; an occurrence metric over an all-time period is a cumulative total.
    is_all_time: bool = False

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def contains(self, value: date) -> bool:
        return self.start <= value < self.end

    def previous(self) -> Period:
        # An all-time period has no previous period to compare against; returning an empty
        # interval keeps the previous-period queries from scanning the whole table for a
        # comparison that cannot exist.
        if self.is_all_time:
            return Period(_EPOCH, _EPOCH, is_all_time=True)
        span = timedelta(days=self.days)
        return Period(self.start - span, self.start)


def resolve_window(window: str | int, *, today: date | None = None) -> Period:
    """The period a window label (or day count) covers, ending today (exclusive of tomorrow).

    The window is inclusive of today, so ``[today - (days-1), today + 1)`` holds exactly
    ``days`` calendar days. This keeps "last 7 days" from silently meaning 8.

    ``all`` (or any non-positive day count) is the whole dataset: it starts at an epoch
    sentinel so the same bounded SQL shape still applies, and its ``is_all_time`` is True so the
    page can label the occurrence figures as cumulative totals rather than a trailing trend.
    """
    if isinstance(window, int):
        days = int(window)
    else:
        days = WINDOW_DAYS.get(str(window), WINDOW_DAYS[DEFAULT_WINDOW])
    end_day = today or datetime.now(timezone.utc).date()
    if days <= 0:
        return Period(_EPOCH, end_day + timedelta(days=1), is_all_time=True)
    days = max(1, days)
    start = end_day - timedelta(days=days - 1)
    return Period(start, end_day + timedelta(days=1))


@dataclass
class Metric:
    """One measured figure, its definition, and its comparison to the previous period."""

    key: str
    label: str
    definition: str
    value: int
    previous: int | None = None
    comparison_available: bool = False
    comparison_note: str | None = None
    excluded_future: int = 0
    #: True when the value is a cumulative total over the whole dataset (the "all time"
    #: window), not an occurrence inside a trailing period. A cumulative total is not a trend,
    #: so the page labels it accordingly rather than showing it as a period figure.
    cumulative: bool = False
    #: The window options this metric is meaningful for. Occurrence metrics (a change, a first
    #: observation) are only meaningful inside a period and are absent from "all".
    windows: tuple[str, ...] = ()

    @property
    def delta(self) -> int | None:
        if self.previous is None:
            return None
        return self.value - self.previous

    @property
    def direction(self) -> str:
        """``up`` / ``down`` / ``flat`` / ``unknown``. ``unknown`` when there is no comparison."""
        if self.previous is None:
            return "unknown"
        d = self.value - self.previous
        if d > 0:
            return "up"
        if d < 0:
            return "down"
        return "flat"

    @property
    def change_ratio(self) -> float | None:
        """Fractional change vs the previous period, or None when the baseline is zero.

        A zero baseline has no meaningful ratio, so it is returned as None rather than as an
        infinite or a fabricated 100%.
        """
        if self.previous is None or self.previous == 0:
            return None
        return (self.value - self.previous) / self.previous


@dataclass
class TrendReport:
    window: str
    period: Period
    previous_period: Period
    metrics: list[Metric] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    malformed_dates: int = 0
    generated_at: str = ""
    ingestion_window: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def metric_index(self) -> dict[str, Metric]:
        return {m.key: m for m in self.metrics}

    @property
    def insufficient(self) -> bool:
        """True when the window holds too few observations to phrase a trend confidently."""
        primary = self.metric_index.get("projects_observed")
        return primary is None or primary.value < MIN_OBSERVATIONS_FOR_TREND


def _count(db: Database, sql: str, params: tuple) -> int:
    return int(db.conn.execute(sql, params).fetchone()[0] or 0)


def _malformed_date_count(db: Database, trade: str) -> int:
    """Permits whose date is present but not a well-formed calendar date."""
    return _count(
        db,
        "SELECT COUNT(*) FROM project p WHERE p.trade = ? "
        "AND p.permit_date IS NOT NULL AND TRIM(p.permit_date) <> '' "
        f"AND NOT ({_VALID_DATE})",
        (trade,),
    )


def _project_metric_sql(select: str, *, period: Period, trade: str, today: date) -> tuple[str, tuple]:
    """SQL for a distinct-project count over a period, excluding future and malformed dates.

    The window is bounded on both sides so the previous-period query reuses the same shape: the
    caller passes the period it wants and the same predicate applies.
    """
    sql = (
        f"SELECT {select} FROM project p "
        "WHERE p.trade = ? "
        f"AND {_VALID_DATE} "
        "AND p.permit_date >= ? AND p.permit_date < ? "
        "AND p.permit_date <= ?"
    )
    params = (trade, period.start.isoformat(), period.end.isoformat(), today.isoformat())
    return sql, params


def _future_excluded(db: Database, *, trade: str, today: date) -> int:
    """This trade's projects dated after today, regardless of window.

    A permit dated in the future cannot evidence work that has already been filed. Such a record
    is never counted by any occurrence metric, and it is reported separately so the exclusion is
    visible rather than implicit in a window bound.
    """
    return _count(
        db,
        "SELECT COUNT(*) FROM project p "
        "WHERE p.trade = ? "
        f"AND {_VALID_DATE} AND p.permit_date > ?",
        (trade, today.isoformat()),
    )


def _coverage_notes(db: Database) -> list[dict]:
    """Per-market coverage states, so a chart can state the limits of its input."""
    try:
        from .coverage import all_market_coverage

        return [
            {
                "market": cov.name,
                "state": cov.state,
                "label": cov.label,
                "records": cov.records,
                "latest_date": cov.latest_record_date,
                "is_serving": cov.is_serving,
            }
            for cov in all_market_coverage(db)
        ]
    except Exception:
        return []


def _ingestion_window(db: Database, *, trade: str, today: date) -> dict:
    """When the database actually collected this trade's records, and from what span of dates.

    Reported alongside the metrics so a reader can tell a change in observed records from a
    change in what a city published. The two are different claims.
    """
    row = db.conn.execute(
        "SELECT MIN(p.created_at) AS first_seen, MAX(p.updated_at) AS last_seen, "
        "MIN(p.permit_date) AS oldest_permit, "
        f"MAX(CASE WHEN {_VALID_DATE} AND p.permit_date <= ? THEN p.permit_date END) "
        "AS newest_permit "
        "FROM project p WHERE p.trade = ?",
        (today.isoformat(), trade),
    ).fetchone()
    if row is None:
        return {}
    # How long BuildScope has actually been collecting. A window longer than this span is
    # reminding the reader that the database is younger than the period, so the figure is a
    # total held, not a complete period.
    span_days = None
    if row["first_seen"] and row["last_seen"]:
        try:
            first = datetime.fromisoformat(str(row["first_seen"]).replace("Z", "+00:00"))
            last = datetime.fromisoformat(str(row["last_seen"]).replace("Z", "+00:00"))
            span_days = max((last.date() - first.date()).days, 0)
        except ValueError:
            span_days = None
    return {
        "first_observed": row["first_seen"],
        "last_observed": row["last_seen"],
        "oldest_permit_date": row["oldest_permit"],
        "newest_permit_date": row["newest_permit"],
        "observation_span_days": span_days,
    }


def build_trend_report(
    db: Database, *, window: str = DEFAULT_WINDOW, trade: str = "commercial_hvac",
    today: date | None = None,
) -> TrendReport:
    """Assemble the Trend Radar report for one window and trade.

    All counts come from stored rows. Nothing is projected, seasonally adjusted, or extrapolated.
    """
    period = resolve_window(window, today=today)
    previous = period.previous()
    reference_day = (today or datetime.now(timezone.utc).date())

    # --- comparability -------------------------------------------------------
    first_seen_row = db.conn.execute(
        "SELECT MIN(p.created_at) AS first_seen FROM project p WHERE p.trade = ?", (trade,)
    ).fetchone()
    first_seen = None
    if first_seen_row and first_seen_row["first_seen"]:
        try:
            first_seen = datetime.fromisoformat(
                str(first_seen_row["first_seen"]).replace("Z", "+00:00")
            ).date()
        except ValueError:
            first_seen = None
    if period.is_all_time:
        comparison_available = False
        comparison_note = (
            "This window is the whole stored dataset, so there is no previous period to "
            "compare against. Occurrence figures here are cumulative totals, not a trend."
        )
    else:
        comparison_available = first_seen is not None and first_seen <= previous.start
        comparison_note = None
        if not comparison_available:
            comparison_note = (
                "No record in this database predates the previous period, so no comparison is "
                "shown. A zero here would read as a decline and would be false."
            )

    # --- the metrics ---------------------------------------------------------
    # 1. Projects whose permit date falls in the window: distinct project count.
    cur_sql, cur_params = _project_metric_sql(
        "COUNT(DISTINCT p.id)", period=period, trade=trade, today=reference_day
    )
    prev_sql, prev_params = _project_metric_sql(
        "COUNT(DISTINCT p.id)", period=previous, trade=trade, today=reference_day
    )
    projects_now = _count(db, cur_sql, cur_params)
    projects_prev = _count(db, prev_sql, prev_params) if comparison_available else None

    projects_observed = Metric(
        key="projects_observed",
        label="New commercial projects",
        definition=(
            "Distinct assembled projects whose permit date falls inside the period. One project "
            "is counted once however many permits it carries."
        ),
        value=projects_now,
        previous=projects_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        excluded_future=_future_excluded(db, trade=trade, today=reference_day),
        cumulative=period.is_all_time,
        windows=_OCCURRENCE_WINDOWS,
    )

    # 2. Permits with a permit date in the window. A project count and a permit count are not
    #    the same number, and this metric is labelled so it cannot be mistaken for one.
    permits_now = _count(
        db,
        "SELECT COUNT(*) FROM permit pm JOIN project_permit pp ON pp.permit_id = pm.id "
        "JOIN project p ON p.id = pp.project_id "
        "WHERE p.trade = ? AND pm.permit_date IS NOT NULL "
        "AND pm.permit_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
        "AND pm.permit_date >= ? AND pm.permit_date < ? AND pm.permit_date <= ?",
        (trade, period.start.isoformat(), period.end.isoformat(), reference_day.isoformat()),
    )
    permits_prev = (
        _count(
            db,
            "SELECT COUNT(*) FROM permit pm JOIN project_permit pp ON pp.permit_id = pm.id "
            "JOIN project p ON p.id = pp.project_id "
            "WHERE p.trade = ? AND pm.permit_date IS NOT NULL "
            "AND pm.permit_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
            "AND pm.permit_date >= ? AND pm.permit_date < ? AND pm.permit_date <= ?",
            (trade, previous.start.isoformat(), previous.end.isoformat(), reference_day.isoformat()),
        )
        if comparison_available
        else None
    )
    permits_observed = Metric(
        key="permits_observed",
        label="Permit records",
        definition=(
            "Permit rows with a permit date inside the period, linked to a project of this "
            "trade. Several permit rows can belong to one project, so this is not a project count."
        ),
        value=permits_now,
        previous=permits_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        cumulative=period.is_all_time,
        windows=_OCCURRENCE_WINDOWS,
    )

    # 3. Projects carrying a classified trade relationship, dated in the window. This is
    #    evidence-of-trade, not evidence-of-opportunity.
    trade_now = _count(
        db,
        "SELECT COUNT(DISTINCT p.id) FROM project p "
        "JOIN project_trade pt ON pt.project_id = p.id "
        "WHERE p.trade = ? AND pt.trade_id IS NOT NULL "
        f"AND {_VALID_DATE} "
        "AND p.permit_date >= ? AND p.permit_date < ? AND p.permit_date <= ?",
        (trade, period.start.isoformat(), period.end.isoformat(), reference_day.isoformat()),
    )
    trade_prev = (
        _count(
            db,
            "SELECT COUNT(DISTINCT p.id) FROM project p "
            "JOIN project_trade pt ON pt.project_id = p.id "
            "WHERE p.trade = ? AND pt.trade_id IS NOT NULL "
            f"AND {_VALID_DATE} "
            "AND p.permit_date >= ? AND p.permit_date < ? AND p.permit_date <= ?",
            (trade, previous.start.isoformat(), previous.end.isoformat(), reference_day.isoformat()),
        )
        if comparison_available
        else None
    )
    trade_scope = Metric(
        key="projects_with_trade_scope",
        label="Projects with classified trade scope",
        definition=(
            "Distinct projects carrying at least one classified trade relationship, dated in "
            "the period. A permit mentioning a trade is not counted here unless the classified "
            "relationship was written."
        ),
        value=trade_now,
        previous=trade_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        cumulative=period.is_all_time,
        windows=_OCCURRENCE_WINDOWS,
    )

    # 4. Projects changed *after* first being observed, in the window. `new_project` is the
    #    record's own first appearance, not a change to something already known, so it is
    #    excluded: counting it would report the entire dataset as "changed" on first assembly.
    changed_now = _count(
        db,
        "SELECT COUNT(DISTINCT pc.project_id) FROM project_change pc "
        "JOIN project p ON p.id = pc.project_id "
        "WHERE p.trade = ? AND pc.change_kind <> 'new_project' "
        "AND pc.detected_at >= ? AND pc.detected_at < ?",
        (trade, period.start.isoformat(), period.end.isoformat()),
    )
    changed_prev = (
        _count(
            db,
            "SELECT COUNT(DISTINCT pc.project_id) FROM project_change pc "
            "JOIN project p ON p.id = pc.project_id "
            "WHERE p.trade = ? AND pc.change_kind <> 'new_project' "
            "AND pc.detected_at >= ? AND pc.detected_at < ?",
            (trade, previous.start.isoformat(), previous.end.isoformat()),
        )
        if comparison_available
        else None
    )
    projects_changed = Metric(
        key="projects_changed",
        label="Projects with a detected change",
        definition=(
            "Distinct previously-observed projects whose stored value changed during the "
            "period. A project's first appearance is not a change and is excluded; it is "
            "reported separately as newly observed."
        ),
        value=changed_now,
        previous=changed_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        cumulative=period.is_all_time,
        windows=_OCCURRENCE_WINDOWS,
    )

    # 5. Projects first observed (ingested) in the window. This measures BuildScope's data
    #    collection, not city publishing activity, and is labelled to say so.
    observed_now = _count(
        db,
        "SELECT COUNT(DISTINCT p.id) FROM project p "
        "WHERE p.trade = ? AND p.created_at >= ? AND p.created_at < ?",
        (trade, period.start.isoformat(), period.end.isoformat()),
    )
    observed_prev = (
        _count(
            db,
            "SELECT COUNT(DISTINCT p.id) FROM project p "
            "WHERE p.trade = ? AND p.created_at >= ? AND p.created_at < ?",
            (trade, previous.start.isoformat(), previous.end.isoformat()),
        )
        if comparison_available
        else None
    )
    newly_observed = Metric(
        key="projects_newly_observed",
        label="Projects first observed",
        definition=(
            "Distinct projects BuildScope first ingested during the period. This measures when "
            "the record entered this database, not when a city published it — the two differ "
            "and are reported separately."
        ),
        value=observed_now,
        previous=observed_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        cumulative=period.is_all_time,
        windows=_OCCURRENCE_WINDOWS,
    )

    # 6. Projects with strong (tier 1 or 2) mechanical evidence, dated in the window.
    strong_now = _count(
        db,
        "SELECT COUNT(DISTINCT p.id) FROM project p "
        "WHERE p.trade = ? AND p.mechanical_evidence_tier IN (1, 2) "
        f"AND {_VALID_DATE} "
        "AND p.permit_date >= ? AND p.permit_date < ? AND p.permit_date <= ?",
        (trade, period.start.isoformat(), period.end.isoformat(), reference_day.isoformat()),
    )
    strong_prev = (
        _count(
            db,
            "SELECT COUNT(DISTINCT p.id) FROM project p "
            "WHERE p.trade = ? AND p.mechanical_evidence_tier IN (1, 2) "
            f"AND {_VALID_DATE} "
            "AND p.permit_date >= ? AND p.permit_date < ? AND p.permit_date <= ?",
            (trade, previous.start.isoformat(), previous.end.isoformat(), reference_day.isoformat()),
        )
        if comparison_available
        else None
    )
    strong_evidence = Metric(
        key="projects_with_strong_evidence",
        label="Projects with strong mechanical evidence",
        definition=(
            "Distinct projects whose mechanical evidence tier is 1 or 2, dated in the period. "
            "Strong evidence of scope is not evidence that a bid is open."
        ),
        value=strong_now,
        previous=strong_prev,
        comparison_available=comparison_available,
        comparison_note=comparison_note,
        cumulative=period.is_all_time,
        windows=_ALL_WINDOWS,
    )

    malformed = _malformed_date_count(db, trade)

    notes: list[str] = [
        "Counts describe the records BuildScope holds and when it observed them, not the full "
        "permit volume of any city.",
        "A project count, a permit count, a trade-evidence count and a change count are "
        "different measurements of the same records and are never added together.",
    ]
    if malformed:
        notes.append(
            f"{malformed} record(s) carry a permit date that is not a well-formed calendar "
            f"date and are excluded from every period count."
        )
    if not comparison_available:
        notes.append(comparison_note or "")

    return TrendReport(
        window=str(window),
        period=period,
        previous_period=previous,
        metrics=[
            projects_observed,
            permits_observed,
            trade_scope,
            projects_changed,
            newly_observed,
            strong_evidence,
        ],
        coverage=_coverage_notes(db),
        malformed_dates=malformed,
        generated_at=datetime.now(timezone.utc).isoformat(),
        ingestion_window=_ingestion_window(db, trade=trade, today=reference_day),
        notes=[n for n in notes if n],
    )


def format_period(period: Period) -> str:
    """A human label for a period, used in the page and in tests."""
    last_day = period.end - timedelta(days=1)
    return f"{period.start.isoformat()} to {last_day.isoformat()}"
