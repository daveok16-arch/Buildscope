"""The single source of truth for public headline statistics.

Every figure the site presents as a headline — "Public projects", "Permit records",
"Active jurisdictions" and the rest — is computed here, once per refresh, in one place, and
stored in ``market_stat_snapshot``. Routes and templates read the stored row; they never run
their own ``COUNT(*)`` for a headline number.

Why a snapshot rather than a live count:

* **Truthfulness under a moving dataset.** The refresh loop ingests and re-assembles on a
  thread while visitors read. A live ``COUNT(*)`` taken by the home page and another taken by
  ``/api/statistics`` seconds later can be answers about two different datasets, which is the
  drift this module exists to remove. The snapshot freezes one consistent set of numbers for a
  whole refresh cycle.
* **One definition per word.** "Record" means public permit rows, "project" means a public
  assembled project, and each is computed once so no two pages can disagree about the same
  word.
* **An honest as-of time.** Each row carries ``computed_at`` (when it was computed) and
  ``last_observed`` (the newest observation timestamp in the data), so a figure can say
  "as of <time>" instead of implying live freshness.

This module is in the application layer but is the *only* place there that issues the headline
``COUNT(*)`` queries. It reads assembled tables; it does not classify, score or assemble.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config import MarketConfig, TradeConfig
from ..db import Database

#: The `market_stat_snapshot` table is declared in `db.py`'s `APP_SCHEMA` alongside every other
#: application table, so all app tables have one definition. This module owns the *computation*
#: and the *contract*; the storage shape lives with the rest of the schema.

#: Canonical metric keys and their human labels and one-line definitions. A single definition
#: per metric is what lets the UI label a number truthfully. Order is display order.
METRIC_DEFINITIONS: dict[str, tuple[str, str]] = {
    "public_projects": (
        "Public projects",
        "Assembled commercial projects the directory may show (excludes closed and "
        "unverified work).",
    ),
    "projects_with_mechanical_evidence": (
        "Projects with mechanical evidence",
        "Public projects carrying a Tier 1 (mechanical permit) or Tier 2 (documented scope) "
        "signal, all time.",
    ),
    "permit_records": (
        "Permit records",
        "Public permit rows linked to a public project. This is the canonical \"record\" count.",
    ),
    "linked_permits": (
        "Linked permits",
        "Permit rows linked to any assembled project, including projects not publicly "
        "discoverable.",
    ),
    "active_jurisdictions": (
        "Cities with at least one public project",
        "Distinct in-market cities that hold at least one public project. Exact rule: "
        "COUNT(DISTINCT city) over public projects whose city is a configured market city "
        "(an alias spelling counts as its configured city). City names not declared for the "
        "market are reported separately in `out_of_market_cities`, never counted.",
    ),
    "configured_jurisdictions": (
        "Configured cities",
        "Cities declared for the market in config/markets.yaml. This is configuration intent, "
        "not observed data, and is always >= the public count.",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_metrics(db: Database, market: MarketConfig, trade: TradeConfig) -> dict[str, Any]:
    """Compute every stored headline metric in one pass.

    Entirely SELECT-based and side-effect free apart from reading. Callers persist the result
    through :func:`write_snapshot`.
    """
    from ..procurement import CLOSED, CONFIRMED_OPEN, EVIDENCE_FOUND, NOT_VERIFIED

    public_cls = ("HIGH", "MEDIUM")
    discoverable = (CONFIRMED_OPEN, EVIDENCE_FOUND, NOT_VERIFIED)

    # The public predicate, spelled out once. The same clause is used for every public count,
    # so the numbers cannot drift from each other within a snapshot.
    public_where = "p.classification IN (?, ?) AND p.procurement_status IN (?, ?, ?)"
    public_params = (*public_cls, *discoverable)

    evidence_clause, evidence_params = trade.evidence_clause("p")
    strong_clause, strong_params = trade.strong_evidence_clause("p")

    def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
        row = db.conn.execute(sql, params).fetchone()
        return int(row[0] or 0)

    projects_total = scalar("SELECT COUNT(*) FROM project p")
    public_projects = scalar(f"SELECT COUNT(*) FROM project p WHERE {public_where}", public_params)
    high = scalar(
        f"SELECT COUNT(*) FROM project p WHERE {public_where} AND p.classification = 'HIGH'",
        public_params,
    )
    medium = scalar(
        f"SELECT COUNT(*) FROM project p WHERE {public_where} AND p.classification = 'MEDIUM'",
        public_params,
    )
    with_mechanical = (
        scalar(
            f"SELECT COUNT(*) FROM project p WHERE {public_where} AND {evidence_clause}",
            (*public_params, *evidence_params),
        )
        if evidence_clause
        else 0
    )
    tier1 = (
        scalar(
            f"SELECT COUNT(*) FROM project p WHERE {public_where} AND {strong_clause}",
            (*public_params, *strong_params),
        )
        if strong_clause
        else 0
    )

    # Active jurisdictions: restricted to the market's configured cities so an out-of-market
    # city name in the data ("Nevada") cannot inflate the count. Any such city is reported
    # separately rather than hidden. Declared aliases (a source spelling such as "Mckinney" for
    # "McKinney") count as the configured city, so a real jurisdiction is not lost to spelling.
    configured = list(market.city_names)
    accepted = list(configured)
    for city in market.cities:
        accepted.extend(city.aliases)
    placeholders = ",".join("?" for _ in accepted) or "NULL"
    in_market = scalar(
        f"SELECT COUNT(DISTINCT p.city) FROM project p "
        f"WHERE {public_where} AND p.city IN ({placeholders})",
        (*public_params, *accepted),
    )
    all_public_cities = [
        r[0]
        for r in db.conn.execute(
            f"SELECT DISTINCT p.city FROM project p WHERE {public_where} AND p.city IS NOT NULL",
            public_params,
        ).fetchall()
        if r[0]
    ]
    accepted_lower = {c.strip().lower() for c in accepted}
    # Cities that hold a public project but are not declared for this market. `out_of_market_*`
    # names what the list is: a configured city is *in* the market; a city seen in the data but
    # not declared is *out of* it. The earlier `jurisdictions_excluded_*` name was ambiguous
    # about which side of that line it described.
    out_of_market_cities = sorted(
        c for c in all_public_cities if c.strip().lower() not in accepted_lower
    )

    # Records. `linked_permits` is every permit attached to a project; `permit_records` is the
    # subset attached to a public project — the canonical public "record".
    linked_permits = scalar("SELECT COUNT(DISTINCT pp.permit_id) FROM project_permit pp")
    permit_records = scalar(
        f"SELECT COUNT(DISTINCT pp.permit_id) FROM project_permit pp "
        f"JOIN project p ON p.id = pp.project_id WHERE {public_where}",
        public_params,
    )
    # Retained for existing API consumers: the total permit rows ingested.
    permit_records_all = scalar("SELECT COUNT(*) FROM permit")
    evidence_records = scalar("SELECT COUNT(*) FROM evidence")

    # The ingest funnel (Item 5): raw permits landed -> linked to a project -> public projects.
    funnel = {
        "permits_landed": permit_records_all,
        "permits_linked": linked_permits,
        "permits_dropped": max(permit_records_all - linked_permits, 0),
        "public_projects": public_projects,
    }
    rows = db.conn.execute(
        "SELECT pm.source_id AS source_id, COUNT(*) AS landed,"
        "       SUM(CASE WHEN pp.permit_id IS NOT NULL THEN 1 ELSE 0 END) AS linked"
        "  FROM permit pm"
        "  LEFT JOIN project_permit pp ON pp.permit_id = pm.id"
        " GROUP BY pm.source_id ORDER BY pm.source_id"
    ).fetchall()
    # A source id is a config key, not a name a reader recognises; resolve the publisher name so
    # a funnel row can say "Fort Worth Permits" rather than "fort_worth_permits". A source id
    # with no config entry keeps its id (never hidden) so an unmatched source is visible.
    from ..config import load_sources

    try:
        source_names = {sid: s.name for sid, s in load_sources().items()}
    except Exception:
        source_names = {}
    funnel["by_source"] = [
        {
            "source_id": r["source_id"],
            "source_name": source_names.get(r["source_id"], r["source_id"] or "unknown"),
            "landed": int(r["landed"] or 0),
            "linked": int(r["linked"] or 0),
            "dropped": max(int(r["landed"] or 0) - int(r["linked"] or 0), 0),
        }
        for r in rows
    ]

    last_observed_row = db.conn.execute(
        "SELECT MAX(updated_at) AS last_observed FROM project p"
    ).fetchone()
    last_observed = last_observed_row["last_observed"] if last_observed_row else None

    metrics: dict[str, Any] = {
        # Canonical headline metrics.
        "public_projects": public_projects,
        "projects_with_mechanical_evidence": with_mechanical,
        "permit_records": permit_records,
        "linked_permits": linked_permits,
        "active_jurisdictions": in_market,
        "configured_jurisdictions": len(configured),
        # Supporting / compatibility keys.
        "projects_total": projects_total,
        "high": high,
        "medium": medium,
        "tier1": tier1,
        "evidence_records": evidence_records,
        "permit_records_all": permit_records_all,
        # Back-compat aliases so existing API consumers and templates keep working; each is the
        # same value as its canonical key, never a separate computation.
        "projects_public": public_projects,
        "with_mechanical": with_mechanical,
        "cities": in_market,
        # Reconciliation reporting.
        "out_of_market_cities": out_of_market_cities,
        "out_of_market_cities_count": len(out_of_market_cities),
        # Back-compat alias for the previous, ambiguous name. Same value, never recomputed.
        "jurisdictions_excluded": out_of_market_cities,
        "jurisdictions_excluded_count": len(out_of_market_cities),
        # Ingest funnel.
        "funnel": funnel,
        "last_observed": last_observed,
    }
    return metrics


def write_snapshot(
    db: Database, market: MarketConfig, trade: TradeConfig, *, metrics: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compute (unless supplied) and persist one snapshot row for market+trade.

    Uses an upsert keyed on ``(market_id, trade_id)`` so exactly one current row exists per
    market and trade, and commits so readers see it immediately.
    """
    import json

    metrics = metrics if metrics is not None else compute_metrics(db, market, trade)
    computed_at = _now()
    db.conn.execute(
        """
        INSERT INTO market_stat_snapshot (market_id, trade_id, metrics, computed_at, last_observed)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(market_id, trade_id) DO UPDATE SET
            metrics = excluded.metrics,
            computed_at = excluded.computed_at,
            last_observed = excluded.last_observed
        """,
        (
            market.id,
            trade.id,
            json.dumps(metrics),
            computed_at,
            metrics.get("last_observed"),
        ),
    )
    db.conn.commit()
    metrics = dict(metrics)
    metrics["computed_at"] = computed_at
    return metrics


def _table_exists(db: Database, name: str) -> bool:
    row = db.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def read_snapshot(
    db: Database, market: MarketConfig, trade: TradeConfig
) -> dict[str, Any]:
    """Return the current snapshot for market+trade. **Never writes.**

    This is the read path and it runs inside a page request, so it must not open a write
    transaction: a write here would take the database write lock during a GET, competing with
    the refresh loop, and (before this change) meant a page load could persist a row. When the
    stored row is absent or is missing a canonical metric key, the values are *computed* and
    returned without being stored. Persisting happens out of the request path, at application
    startup and at the end of each refresh cycle, via :func:`heal_snapshot`.
    """
    import json

    if not _table_exists(db, "market_stat_snapshot"):
        # A database that predates this table and has not been migrated: compute without
        # persisting rather than fail, so a read is never a 500.
        return compute_metrics(db, market, trade)

    row = db.conn.execute(
        "SELECT metrics, computed_at, last_observed FROM market_stat_snapshot"
        " WHERE market_id = ? AND trade_id = ?",
        (market.id, trade.id),
    ).fetchone()
    if row is None:
        # No stored row yet (a fresh database before the startup heal, or a test that did not
        # seed one). Return a complete canonical set so every surface agrees, without storing.
        return compute_metrics(db, market, trade)

    try:
        metrics = json.loads(row["metrics"])
    except (TypeError, ValueError):
        metrics = compute_metrics(db, market, trade)
    metrics = dict(metrics)

    # A stored snapshot is not automatically current: when a canonical metric is added (or
    # changes meaning) the rows written by the previous code are missing the new key, and a
    # reader would serve `None` for a figure every other surface publishes. Recompute so the
    # reader always sees a complete set of figures; the row itself is healed out of the request
    # path (see `heal_snapshot`), so a GET never writes.
    if any(key not in metrics for key in METRIC_DEFINITIONS):
        return compute_metrics(db, market, trade)

    metrics["computed_at"] = row["computed_at"]
    metrics["last_observed"] = row["last_observed"] or metrics.get("last_observed")
    return metrics


def _snapshot_is_current(db: Database, market: MarketConfig, trade: TradeConfig) -> bool:
    """True when a stored row exists and carries every canonical metric key."""
    import json

    if not _table_exists(db, "market_stat_snapshot"):
        return False
    row = db.conn.execute(
        "SELECT metrics FROM market_stat_snapshot WHERE market_id = ? AND trade_id = ?",
        (market.id, trade.id),
    ).fetchone()
    if row is None:
        return False
    try:
        metrics = json.loads(row["metrics"])
    except (TypeError, ValueError):
        return False
    return all(key in metrics for key in METRIC_DEFINITIONS)


def heal_snapshot(db: Database, market: MarketConfig, trade: TradeConfig) -> dict[str, Any]:
    """Recompute and persist a snapshot only when it is missing or stale.

    Called out of the request path — at application startup and at the end of each refresh
    cycle — so a deployed upgrade that adds a canonical metric repairs the stored row without a
    visitor's page load doing the write. Idempotent: a current row is returned unchanged, so a
    restart does not churn ``computed_at``.
    """
    if _snapshot_is_current(db, market, trade):
        return read_snapshot(db, market, trade)
    return write_snapshot(db, market, trade)


def metric_label(key: str) -> str:
    """The display label for a canonical metric key."""
    return METRIC_DEFINITIONS.get(key, (key, ""))[0]


def metric_definition(key: str) -> str:
    """The one-line definition for a canonical metric key."""
    return METRIC_DEFINITIONS.get(key, (key, ""))[1]
