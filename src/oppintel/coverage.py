"""Market coverage intelligence.

A market being *listed* in configuration is not the same as a market being *served*. The
mission requires the platform never to imply coverage it does not have, so this module computes,
for each configured market, which of six states it is actually in:

1. ``configured``          — present in `markets.yaml`, with no enabled source.
2. ``source_enabled``      — has at least one enabled source, but no ingest run has completed.
3. ``ingested``            — at least one source has completed a run, but no records are held.
4. ``records_held``        — records exist, but their newest coverage is stale.
5. ``current``             — records exist and coverage reaches the freshness window.
6. ``verified``            — as ``current``, plus the market is the active market and a
                             source was retrieved within the freshness window.

The state is derived from stored rows, never from configuration intent, so a market cannot be
described as covered when its connector has never run. The distinction between states 3 and 4
matters most: a source that answers but returns nothing is a different problem from a source
that has never been reached, and the two require different remedies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .config import MarketConfig, SourceConfig, load_markets, load_sources

#: Coverage states, ordered from least to most substantiated.
STATE_CONFIGURED = "configured"
STATE_SOURCE_ENABLED = "source_enabled"
STATE_INGESTED = "ingested"
STATE_RECORDS_HELD = "records_held"
STATE_CURRENT = "current"
STATE_VERIFIED = "verified"

COVERAGE_STATES = (
    STATE_CONFIGURED,
    STATE_SOURCE_ENABLED,
    STATE_INGESTED,
    STATE_RECORDS_HELD,
    STATE_CURRENT,
    STATE_VERIFIED,
)

#: Human labels. Deliberately plain: each states what is true, not what is hoped for.
STATE_LABELS: dict[str, str] = {
    STATE_CONFIGURED: "Configured, no source enabled",
    STATE_SOURCE_ENABLED: "Source enabled, not yet ingested",
    STATE_INGESTED: "Ingested, no records held",
    STATE_RECORDS_HELD: "Records held, coverage stale",
    STATE_CURRENT: "Current coverage",
    STATE_VERIFIED: "Verified current coverage",
}

#: A market's newest coverage within this window is treated as current. 180 days matches the
#: recency window the classifier already uses, so "current" means the same thing in both places.
FRESHNESS_WINDOW_DAYS = 180


@dataclass
class SourceCoverage:
    """What is known about one source's contribution to a market."""

    source_id: str
    name: str
    enabled: bool
    has_connector: bool
    records: int
    latest_date: str | None
    retrieval_date: str | None
    is_fresh: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "enabled": self.enabled,
            "has_connector": self.has_connector,
            "records": self.records,
            "latest_date": self.latest_date,
            "retrieval_date": self.retrieval_date,
            "is_fresh": self.is_fresh,
        }


@dataclass
class MarketCoverage:
    """The computed coverage position of one market."""

    market_id: str
    slug: str
    name: str
    state: str
    label: str
    configured_sources: int
    enabled_sources: int
    sources_with_connector: int
    records: int
    latest_record_date: str | None
    latest_retrieval_date: str | None
    cities_configured: int
    cities_with_records: int
    notes: list[str] = field(default_factory=list)
    sources: list[SourceCoverage] = field(default_factory=list)

    @property
    def is_serving(self) -> bool:
        """True when the market actually serves opportunity data, not merely configuration."""
        return self.state in (STATE_RECORDS_HELD, STATE_CURRENT, STATE_VERIFIED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "slug": self.slug,
            "name": self.name,
            "state": self.state,
            "label": self.label,
            "is_serving": self.is_serving,
            "configured_sources": self.configured_sources,
            "enabled_sources": self.enabled_sources,
            "sources_with_connector": self.sources_with_connector,
            "records": self.records,
            "latest_record_date": self.latest_record_date,
            "latest_retrieval_date": self.latest_retrieval_date,
            "cities_configured": self.cities_configured,
            "cities_with_records": self.cities_with_records,
            "notes": self.notes,
            "sources": [s.to_dict() for s in self.sources],
        }


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _connector_ids() -> set[str]:
    """Source ids that have an implemented connector.

    Imported lazily so this module stays importable in an environment where the connector
    package's HTTP dependencies are absent.
    """
    try:
        from .connectors import connector_ids

        return set(connector_ids())
    except Exception:  # pragma: no cover - defensive: a registry error must not break coverage
        return set()


def _market_source_ids(market: MarketConfig, sources: dict[str, SourceConfig]) -> list[str]:
    """Source ids configured for a market.

    Falls back to jurisdiction-city matching when a market lists no sources, so a market that
    names cities but relies on a shared connector is not reported as having none.
    """
    if market.sources:
        return [s for s in market.sources if s in sources]
    city_names = {c.name.strip().lower() for c in market.cities}
    matched: list[str] = []
    for source in sources.values():
        if source.jurisdiction_city and source.jurisdiction_city.strip().lower() in city_names:
            matched.append(source.id)
    return matched


def _market_records(db: Any, market: MarketConfig) -> dict[str, Any]:
    """Records held for a market, from stored permits and projects.

    A market is matched by the cities it configures, because that is the only reliable join
    between a geographic market and a permit row. A market whose sources publish no city is
    matched by its sources instead.
    """
    city_names = [c.name for c in market.cities]
    permit_count = 0
    latest_permit = None
    if city_names:
        placeholders = ",".join("?" for _ in city_names)
        row = db.conn.execute(
            f"""
            SELECT COUNT(*) AS n, MAX(permit_date) AS latest
              FROM permit WHERE city IN ({placeholders})
            """,
            city_names,
        ).fetchone()
        permit_count = int(row["n"] or 0)
        latest_permit = row["latest"]

    project_count = 0
    cities_with_records = 0
    if city_names:
        placeholders = ",".join("?" for _ in city_names)
        row = db.conn.execute(
            f"""
            SELECT COUNT(*) AS n, COUNT(DISTINCT city) AS cities
              FROM project WHERE city IN ({placeholders})
            """,
            city_names,
        ).fetchone()
        project_count = int(row["n"] or 0)
        cities_with_records = int(row["cities"] or 0)

    return {
        "permits": permit_count,
        "projects": project_count,
        "latest_permit_date": latest_permit,
        "cities_with_records": cities_with_records,
    }


def _source_rows(db: Any, source_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not source_ids:
        return {}
    placeholders = ",".join("?" for _ in source_ids)
    rows = db.conn.execute(
        f"""
        SELECT c.source_id, c.record_count, c.latest_date, c.retrieval_date
          FROM source_coverage c WHERE c.source_id IN ({placeholders})
        """,
        source_ids,
    ).fetchall()
    return {r["source_id"]: dict(r) for r in rows}


def _ingest_run_count(db: Any, source_ids: list[str]) -> int:
    if not source_ids:
        return 0
    placeholders = ",".join("?" for _ in source_ids)
    row = db.conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM ingest_run
         WHERE source_id IN ({placeholders}) AND finished_at IS NOT NULL
        """,
        source_ids,
    ).fetchone()
    return int(row["n"] or 0)


def market_coverage(db: Any, market: MarketConfig, *, today: date | None = None) -> MarketCoverage:
    """Compute the coverage position of one market from stored rows."""
    today = today or datetime.now(timezone.utc).date()
    sources_cfg = load_sources()
    connectors = _connector_ids()
    source_ids = _market_source_ids(market, sources_cfg)
    coverage_rows = _source_rows(db, source_ids)
    ingest_runs = _ingest_run_count(db, source_ids)

    source_states: list[SourceCoverage] = []
    enabled = 0
    with_connector = 0
    for sid in source_ids:
        cfg = sources_cfg[sid]
        row = coverage_rows.get(sid) or {}
        latest = row.get("latest_date")
        retrieval = row.get("retrieval_date")
        latest_day = _parse_day(latest)
        retrieval_day = _parse_day(retrieval)
        fresh = bool(
            latest_day is not None
            and (today - latest_day).days <= FRESHNESS_WINDOW_DAYS
        ) or bool(
            retrieval_day is not None
            and (today - retrieval_day).days <= FRESHNESS_WINDOW_DAYS
        )
        if cfg.enabled:
            enabled += 1
        if sid in connectors:
            with_connector += 1
        source_states.append(
            SourceCoverage(
                source_id=sid,
                name=cfg.name,
                enabled=cfg.enabled,
                has_connector=sid in connectors,
                records=int(row.get("record_count") or 0),
                latest_date=latest,
                retrieval_date=retrieval,
                is_fresh=fresh,
            )
        )

    held = _market_records(db, market)
    notes: list[str] = []

    # A market whose sources have no implemented connector is a configuration without an
    # ingest path. Stating that is more useful than reporting a bare "configured".
    if source_ids and with_connector == 0:
        notes.append(
            "No enabled source for this market has an implemented connector, so no records "
            "can be collected yet."
        )
    if source_ids and enabled == 0:
        notes.append("Every source configured for this market is disabled.")

    records = held["permits"]
    latest_day = _parse_day(held["latest_permit_date"])
    is_fresh = bool(
        latest_day is not None and (today - latest_day).days <= FRESHNESS_WINDOW_DAYS
    )

    # Resolve the state by the strongest evidence that holds. Order matters: held records are
    # proof of ingestion, so they are tested before the run counters, and a market cannot be
    # "current" without records nor "verified" unless it is the active market.
    if not source_ids or enabled == 0:
        state = STATE_CONFIGURED
    elif records == 0:
        # No records: distinguish "the source has never run" from "the source ran and returned
        # nothing". The two need different remedies, so they must not collapse into one state.
        state = STATE_INGESTED if (ingest_runs or coverage_rows) else STATE_SOURCE_ENABLED
    elif not is_fresh:
        state = STATE_RECORDS_HELD
    else:
        from .config import active_market

        try:
            active_id = active_market().id
        except Exception:
            active_id = None
        state = STATE_VERIFIED if market.id == active_id else STATE_CURRENT

    if state == STATE_RECORDS_HELD and latest_day is not None:
        notes.append(
            f"Newest record is dated {latest_day.isoformat()}, outside the "
            f"{FRESHNESS_WINDOW_DAYS}-day freshness window."
        )
    if state == STATE_INGESTED:
        notes.append(
            "A source run completed but no records are held for this market's cities. The "
            "connector answers, so the gap is in what the source returns, not connectivity."
        )

    return MarketCoverage(
        market_id=market.id,
        slug=market.slug,
        name=market.name,
        state=state,
        label=STATE_LABELS[state],
        configured_sources=len(source_ids),
        enabled_sources=enabled,
        sources_with_connector=with_connector,
        records=records,
        latest_record_date=held["latest_permit_date"],
        latest_retrieval_date=max(
            (s.retrieval_date for s in source_states if s.retrieval_date),
            default=None,
        ),
        cities_configured=len(market.cities),
        cities_with_records=held["cities_with_records"],
        notes=notes,
        sources=source_states,
    )


def all_market_coverage(db: Any, *, today: date | None = None) -> list[MarketCoverage]:
    """Coverage for every configured market, serving markets first."""
    out = [market_coverage(db, m, today=today) for m in load_markets().values()]
    out.sort(key=lambda c: (not c.is_serving, c.name))
    return out


def coverage_summary(db: Any, *, today: date | None = None) -> dict[str, Any]:
    """A compact summary suitable for a public page or a health payload."""
    markets = all_market_coverage(db, today=today)
    serving = [m for m in markets if m.is_serving]
    return {
        "markets_configured": len(markets),
        "markets_serving": len(serving),
        "markets_verified": sum(1 for m in markets if m.state == STATE_VERIFIED),
        "markets_current": sum(1 for m in markets if m.state == STATE_CURRENT),
        "markets_configured_only": sum(1 for m in markets if m.state == STATE_CONFIGURED),
        "markets": [m.to_dict() for m in markets],
    }
