"""Opportunity service: the boundary between the intelligence data and the web application.

Every read the public site performs goes through this module. It exists so that no route,
template or frontend helper ever writes SQL against the intelligence tables, and so the
eligibility, procurement and grouping rules that govern what may be shown publicly are applied
in exactly one place.

What this layer does **not** do: it does not classify, score, assemble or evaluate evidence.
Those decisions are already made and stored by the intelligence pipeline. The service reads
them. If a rule about what counts as an opportunity changes, it changes in
`eligibility.py` / `classify.py`, not here.

Two rules are enforced at this boundary rather than being left to the UI:

* **Public by default excludes closed and unverified work.** A public directory listing a
  finished building as an opportunity would be a false claim, so the filter lives here.
* **Missing information is returned as None, never as a placeholder.** Rendering
  "Not verified" is the template's job; the service must not invent a value to make a
  template simpler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .config import (
    MarketConfig,
    TradeConfig,
    load_search_vocabulary,
    load_sources,
    type_slug,
)
from .dates import occurrence_is_future
from .db import Database
from .eligibility import evaluate
from .grouping import building_key, group_projects, sibling_info_for
from .procurement import CLOSED, EVIDENCE_FOUND, NOT_VERIFIED, CONFIRMED_OPEN
from .search_index import explain_matches, quote_for_fts

#: Classifications the public site may show, in display order.
PUBLIC_CLASSIFICATIONS = ("HIGH", "MEDIUM")

#: Procurement states that permit public *discovery*. A project is discoverable unless it is
#: known to be finished or dead: "we do not know the procurement status" is not the same as
#: "there is nothing to procure", and treating the two as one hid every record whose source
#: publishes no status. Collin CAD is exactly that case — it reports an issue date and no
#: status at all — so its records were unreachable even though the work is recent.
#:
#: A CLOSED project is still reachable by direct URL, where its status is stated plainly.
DISCOVERABLE_PROCUREMENT = (CONFIRMED_OPEN, EVIDENCE_FOUND, NOT_VERIFIED)

#: Sort options exposed to the public. Deliberately factual orderings only: there is no
#: "best opportunity" sort, because that would be an invented judgement.
#
#: "Most recent permit" orders by the most recent *usable* permit date: a permit dated after
#: today is a defect (see `dates.usable_occurrence_sql`) and must not sort to the top as if it
#: were the newest filing. `date('now')` is SQLite's UTC day, matching the observation day the
#: rest of the pipeline uses, and keeps the order expression free of bound parameters so the
#: sort stays a static, testable string.
_SORT_DATE = "CASE WHEN p.permit_date <= date('now') THEN p.permit_date END"


def _recent_order(alias: str = "p") -> str:
    """An ORDER BY that ranks projects by their most recent *usable* permit date.

    A permit dated after today is a defect and must not rank as the newest filing, so it is
    excluded from the ordering expression (it sorts last, after real dates). The id breaks ties
    deterministically.
    """
    date_expr = (
        f"CASE WHEN {alias}.permit_date <= date('now') THEN {alias}.permit_date END"
    )
    return f"{date_expr} DESC NULLS LAST, {alias}.id"


SORT_OPTIONS = {
    "recent": ("Most recent permit", _recent_order("p")),
    "updated": ("Recently updated", "p.updated_at DESC, p.id"),
    "status": (
        "Project status",
        f"p.project_status ASC, {_recent_order('p')}",
    ),
}

DEFAULT_SORT = "recent"

PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@dataclass
class OpportunityFilters:
    """Filters accepted from the public UI.

    Every field is optional and every field maps to a stored column. Nothing here is derived
    or scored, so a filter can never disagree with the record it filters on.
    """

    q: str | None = None
    city: str | None = None
    project_type: str | None = None
    classification: str | None = None
    procurement_status: str | None = None
    market: str | None = None
    trade: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    include_unverified: bool = False
    #: Value band, applied to the declared project value. A project with no declared value is
    #: not excluded by a band, because "we do not know the value" is not "the value is low".
    min_value: float | None = None
    max_value: float | None = None
    #: Structural filters. Each maps to a stored column, never to a derived judgement.
    mechanical_only: bool = False
    #: Only records whose stored `updated_at` is within this many days.
    freshness_days: int | None = None
    #: Restrict to an explicit id set. Used by the account views (saved, watching, pipeline),
    #: which resolve their own id list rather than re-implementing a query.
    project_ids: tuple[int, ...] | None = None
    sort: str = DEFAULT_SORT
    page: int = 1
    page_size: int = PAGE_SIZE

    def normalised(self) -> OpportunityFilters:
        """Clamp values so a hand-edited query string cannot cause a large or invalid query."""
        page = max(1, int(self.page or 1))
        size = int(self.page_size or PAGE_SIZE)
        size = max(1, min(size, MAX_PAGE_SIZE))

        def band(value: float | None) -> float | None:
            if value is None:
                return None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None

        days = self.freshness_days
        try:
            days = int(days) if days is not None else None
        except (TypeError, ValueError):
            days = None
        if days is not None and days <= 0:
            days = None

        ids: tuple[int, ...] | None = None
        if self.project_ids is not None:
            cleaned: list[int] = []
            for item in self.project_ids:
                try:
                    cleaned.append(int(item))
                except (TypeError, ValueError):
                    continue
            ids = tuple(sorted(set(cleaned)))

        return OpportunityFilters(
            q=(self.q or "").strip() or None,
            city=(self.city or "").strip() or None,
            project_type=(self.project_type or "").strip() or None,
            classification=(self.classification or "").strip() or None,
            procurement_status=(self.procurement_status or "").strip() or None,
            market=(self.market or "").strip() or None,
            trade=(self.trade or "").strip() or None,
            date_from=(self.date_from or "").strip() or None,
            date_to=(self.date_to or "").strip() or None,
            include_unverified=bool(self.include_unverified),
            min_value=band(self.min_value),
            max_value=band(self.max_value),
            mechanical_only=bool(self.mechanical_only),
            freshness_days=days,
            project_ids=ids,
            sort=self.sort if self.sort in SORT_OPTIONS else DEFAULT_SORT,
            page=page,
            page_size=size,
        )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def as_query_string(self, **overrides: Any) -> str:
        """Rebuild the filter set as a query string, for pagination and canonical links."""
        params: dict[str, Any] = {
            "q": self.q,
            "city": self.city,
            "project_type": self.project_type,
            "classification": self.classification,
            "procurement_status": self.procurement_status,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "sort": self.sort if self.sort != DEFAULT_SORT else None,
        }
        if self.include_unverified:
            params["include_unverified"] = "1"
        if self.mechanical_only:
            params["mechanical_only"] = "1"
        if self.freshness_days:
            params["freshness_days"] = self.freshness_days
        params.update(overrides)
        pairs = [f"{k}={_quote(str(v))}" for k, v in params.items() if v not in (None, "")]
        return "&".join(pairs)


def _quote(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


@dataclass
class OpportunityPage:
    """One page of results plus everything a template needs to render the pagination."""

    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = PAGE_SIZE
    filters: OpportunityFilters = field(default_factory=OpportunityFilters)

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 1
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def first_index(self) -> int:
        return 0 if not self.items else self.offset + 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def last_index(self) -> int:
        return self.offset + len(self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items


class OpportunityService:
    """Read access to projects, scoped to what may be shown publicly."""

    def __init__(
        self, db: Database, market: MarketConfig, trade: TradeConfig,
        *, viewer_id: int | None = None,
    ):
        self.db = db
        self.market = market
        self.trade = trade
        #: The signed-in account, when there is one. Used only to annotate records with that
        #: account's own relationship to them (saved, watched, pipeline stage). It never
        #: changes which records are returned, so a signed-in view is not a different dataset.
        self.viewer_id = viewer_id

    # --- filters --------------------------------------------------------------

    def available_cities(self) -> list[str]:
        """Cities that actually hold discoverable opportunities.

        Sourced from the database rather than the market config, so the filter never offers a
        city with nothing behind it.
        """
        rows = self.db.conn.execute(
            """
            SELECT city, COUNT(*) AS n
              FROM project
             WHERE classification IN (?, ?)
               AND procurement_status IN (?, ?, ?)
               AND city IS NOT NULL
             GROUP BY city
             ORDER BY n DESC, city
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchall()
        return [r["city"] for r in rows]

    def available_project_types(self) -> list[str]:
        rows = self.db.conn.execute(
            """
            SELECT project_type, COUNT(*) AS n
              FROM project
             WHERE classification IN (?, ?)
               AND procurement_status IN (?, ?, ?)
               AND project_type IS NOT NULL
             GROUP BY project_type
             ORDER BY n DESC, project_type
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchall()
        return [r["project_type"] for r in rows]

    def procurement_options(self) -> list[str]:
        """Procurement states present among public projects, in the order they should read."""
        rows = self.db.conn.execute(
            """
            SELECT DISTINCT procurement_status FROM project
             WHERE classification IN (?, ?) AND procurement_status IS NOT NULL
            """,
            PUBLIC_CLASSIFICATIONS,
        ).fetchall()
        present = {r["procurement_status"] for r in rows}
        order = (CONFIRMED_OPEN, EVIDENCE_FOUND, NOT_VERIFIED, CLOSED)
        return [state for state in order if state in present]

    # --- query building -------------------------------------------------------

    def _trade_evidence_clause(self, filters: OpportunityFilters) -> tuple[str | None, list[Any]]:
        """The clause that scopes results to the active trade's own evidence.

        This is what stops an HVAC directory listing a project with no mechanical activity.
        The requirement comes from `trades.yaml`, so a future plumbing or electrical trade
        declares its own evidence field rather than needing new application code.

        Relaxed when the visitor explicitly asks for a wider set, or when the trade is
        configured to discover on the commercial base. In the second case every listing carries
        a trade-signal badge, so a project with no mechanical evidence is shown as
        trade-unverified rather than presented as an HVAC opportunity. That distinction is the
        whole point: the directory may widen, but it may not claim a trade the record does not
        support.
        """
        if filters.include_unverified or filters.procurement_status or filters.mechanical_only:
            return None, []
        if (self.trade.discovery or {}).get("discover_commercial_base"):
            return None, []
        field = (self.trade.discovery or {}).get("evidence_field")
        values = (self.trade.discovery or {}).get("evidence_values") or []
        if not field or not values:
            return None, []
        # A column name cannot be bound as a parameter, so it is validated instead. This is
        # the only place an identifier from configuration reaches SQL.
        if not re.fullmatch(r"[a-z_]+", str(field)):
            raise ValueError(f"Invalid discovery evidence_field: {field!r}")
        placeholders = ",".join("?" for _ in values)
        return f"p.{field} IN ({placeholders})", list(values)

    def _base_where(self, filters: OpportunityFilters) -> tuple[str, list[Any]]:
        """Build the WHERE clause for a public listing.

        Returns SQL and parameters separately; every user value is bound, never interpolated.
        """
        clauses: list[str] = ["p.classification IN (?, ?)"]
        params: list[Any] = list(PUBLIC_CLASSIFICATIONS)

        # Discovery hides only work known to be finished or dead. The opt-in shows everything,
        # including closed records, with the status stated on each listing.
        if filters.include_unverified:
            clauses.append("p.procurement_status IS NOT NULL")
        else:
            clauses.append("p.procurement_status IN (?, ?, ?)")
            params.extend(DISCOVERABLE_PROCUREMENT)

        trade_clause, trade_params = self._trade_evidence_clause(filters)
        if trade_clause:
            clauses.append(trade_clause)
            params.extend(trade_params)

        if filters.city:
            clauses.append("p.city = ?")
            params.append(filters.city)
        if filters.project_type:
            clauses.append("p.project_type = ?")
            params.append(filters.project_type)
        if filters.classification:
            clauses.append("p.classification = ?")
            params.append(filters.classification)
        if filters.procurement_status:
            clauses.append("p.procurement_status = ?")
            params.append(filters.procurement_status)
        if filters.date_from:
            clauses.append("p.permit_date >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append("p.permit_date <= ?")
            params.append(filters.date_to)
        # A value band only excludes records that *declare* a value outside it. A project with
        # no declared value is left in, because an unknown value is not a low value, and
        # dropping it would silently hide opportunities the source simply does not price.
        if filters.min_value is not None:
            clauses.append("(p.estimated_project_value IS NULL OR p.estimated_project_value >= ?)")
            params.append(filters.min_value)
        if filters.max_value is not None:
            clauses.append("(p.estimated_project_value IS NULL OR p.estimated_project_value <= ?)")
            params.append(filters.max_value)
        if filters.mechanical_only:
            field = (self.trade.discovery or {}).get("evidence_field")
            if field and re.fullmatch(r"[a-z_]+", str(field)):
                clauses.append(f"p.{field} IS NOT NULL")
        if filters.freshness_days:
            clauses.append("p.updated_at >= ?")
            params.append(
                (
                    datetime.now(timezone.utc)
                    - timedelta(days=int(filters.freshness_days))
                ).isoformat()
            )
        if filters.project_ids is not None:
            if not filters.project_ids:
                clauses.append("1 = 0")
            else:
                placeholders = ",".join("?" for _ in filters.project_ids)
                clauses.append(f"p.id IN ({placeholders})")
                params.extend(filters.project_ids)

        return " AND ".join(clauses), params

    def _search_ids(self, term: str) -> list[int] | None:
        """Project ids matching a free-text term, or None when no term was given.

        The term is expanded through the active trade's configured vocabulary, so a search for
        "ahu" also finds "air handling unit". Expansion only widens the match.
        """
        vocabulary = load_search_vocabulary().for_trade(self.trade.id)
        query = quote_for_fts(term, vocabulary)
        if not query:
            return None
        try:
            rows = self.db.conn.execute(
                "SELECT project_id FROM project_search WHERE project_search MATCH ? ORDER BY rank",
                (query,),
            ).fetchall()
        except Exception:
            # A malformed FTS expression must degrade to "no results", not a 500.
            return []
        return [int(r["project_id"]) for r in rows]

    # --- listing --------------------------------------------------------------

    def list_opportunities(self, filters: OpportunityFilters | None = None) -> OpportunityPage:
        """Page of opportunities matching the filters, with a total for pagination."""
        filters = (filters or OpportunityFilters()).normalised()
        where, params = self._base_where(filters)

        if filters.q:
            ids = self._search_ids(filters.q)
            if not ids:
                return OpportunityPage(items=[], total=0, page=filters.page,
                                       page_size=filters.page_size, filters=filters)
            placeholders = ",".join("?" for _ in ids)
            where += f" AND p.id IN ({placeholders})"
            params.extend(ids)

        total = int(
            self.db.conn.execute(
                f"SELECT COUNT(*) FROM project p WHERE {where}", params
            ).fetchone()[0]
        )

        order_by = SORT_OPTIONS[filters.sort][1]
        rows = self.db.conn.execute(
            f"""
            SELECT p.*, s.slug
              FROM project p
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE {where}
             ORDER BY {order_by}
             LIMIT ? OFFSET ?
            """,
            params + [filters.page_size, filters.offset],
        ).fetchall()

        items = self.annotate_viewer(self.annotate_siblings([self.decorate(dict(r)) for r in rows]))
        if filters.q:
            self.attach_search_reasons(items, filters.q)
        return OpportunityPage(
            items=items, total=total, page=filters.page, page_size=filters.page_size,
            filters=filters,
        )

    def attach_search_reasons(self, items: list[dict[str, Any]], query: str) -> None:
        """Attach, to each item, the reasons it matched the query.

        The work-description text is read from the index in one query for the whole page rather
        than per result, and the reasons are computed from the words the user typed (see
        ``explain_matches``). A project whose words all matched only via the widened FTS
        expression gets no reasons — which is honest, because no displayable field contains the
        typed term — while the record still appears, because expansion only ever widens recall.
        """
        if not items:
            return
        ids = [int(item["id"]) for item in items if item.get("id") is not None]
        descriptions: dict[int, str] = {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            try:
                for row in self.db.conn.execute(
                    f"SELECT CAST(project_id AS INTEGER) AS pid, work_description "
                    f"FROM project_search WHERE CAST(project_id AS INTEGER) IN ({placeholders})",
                    ids,
                ).fetchall():
                    descriptions[int(row["pid"])] = row["work_description"]
            except Exception:
                # Reasons are presentation only; a failure here must not break the listing.
                descriptions = {}
        for item in items:
            fields = dict(item)
            fields["work_description"] = descriptions.get(int(item["id"]), "")
            item["search_reasons"] = explain_matches(query, fields)

    def decorate(self, project: dict[str, Any]) -> dict[str, Any]:
        """Add presentation-only fields to a project row.

        These are formatting helpers and relationship flags derived from stored values, never
        new facts. The provenance of every factual field continues to come from `evidence`.
        """
        project["city_slug"] = self.market.city_slug(project.get("city"))
        project["evidence_label"] = self.evidence_label(project)
        project["trade_signal"] = self.trade_signal_label(project)
        project["has_mechanical_evidence"] = project.get("mechanical_evidence_tier") in (1, 2)
        project["last_verified_display"] = self.format_month(project.get("last_verified"))
        project["permit_date_display"] = self.format_date(project.get("permit_date"))
        # A permit dated after today cannot describe a filing that has already happened. The
        # value is preserved exactly (the source states it), but it is flagged so a template
        # labels it "Unverified date" instead of presenting a future date as a filing date.
        project["permit_date_is_future"] = occurrence_is_future(project.get("permit_date"))
        project["is_closed"] = project.get("procurement_status") == CLOSED
        project["building_key"] = building_key(project.get("address"), project.get("city"))

        # Whether this record would pass the customer-brief criteria. Shown as a badge so the
        # public list and the customer brief cannot tell different stories about a project.
        project["is_report_eligible"] = evaluate(_SqliteRow(project)).eligible
        return project

    def annotate_siblings(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mark which results share a base address with another result in the same page.

        Two suites of one building are genuinely separate permits, so both belong in a complete
        directory — hiding one would be its own kind of dishonesty. What must not happen is that
        they read as two unrelated buildings. Each is therefore flagged, and the card states the
        relationship, so a visitor cannot mistake one tower's suites for two opportunities.

        This runs per page rather than across the whole result set: a sibling on page 4 is not
        presented next to this one, so there is nothing to disambiguate.
        """
        counts: dict[str, int] = {}
        for item in items:
            key = item.get("building_key")
            if key:
                counts[key] = counts.get(key, 0) + 1
        for item in items:
            key = item.get("building_key")
            item["sibling_count"] = max(0, counts.get(key, 1) - 1) if key else 0
            item["shares_building"] = item["sibling_count"] > 0
        return items

    def annotate_viewer(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach the viewer's own relationship to each item, in one query per relation.

        Batched deliberately: annotating per card would issue three queries per row and turn a
        page of twenty into sixty, which is exactly the kind of cost that makes a product feel
        slow. With no viewer this is a no-op, so the public directory pays nothing for it.
        """
        if not self.viewer_id or not items:
            return items
        ids = [int(item["id"]) for item in items if item.get("id") is not None]
        if not ids:
            return items
        placeholders = ",".join("?" for _ in ids)

        saved = {
            int(r["project_id"])
            for r in self.db.conn.execute(
                f"SELECT project_id FROM saved_opportunity "
                f"WHERE user_id = ? AND project_id IN ({placeholders})",
                [self.viewer_id] + ids,
            ).fetchall()
        }
        watched = {
            int(r["project_id"])
            for r in self.db.conn.execute(
                f"SELECT project_id FROM watched_opportunity "
                f"WHERE user_id = ? AND project_id IN ({placeholders})",
                [self.viewer_id] + ids,
            ).fetchall()
        }
        stages = {
            int(r["project_id"]): r["stage"]
            for r in self.db.conn.execute(
                f"SELECT project_id, stage FROM pipeline_entry "
                f"WHERE user_id = ? AND project_id IN ({placeholders})",
                [self.viewer_id] + ids,
            ).fetchall()
        }
        tag_counts: dict[int, int] = {}
        for r in self.db.conn.execute(
            f"SELECT project_id, COUNT(*) AS n FROM opportunity_tag "
            f"WHERE user_id = ? AND project_id IN ({placeholders}) GROUP BY project_id",
            [self.viewer_id] + ids,
        ).fetchall():
            tag_counts[int(r["project_id"])] = int(r["n"])

        for item in items:
            pid = int(item["id"])
            item["is_saved"] = pid in saved
            item["is_watched"] = pid in watched
            item["stage"] = item.get("stage") or stages.get(pid)
            item["tag_count"] = tag_counts.get(pid, 0)
        return items

    @staticmethod
    def evidence_label(project: dict[str, Any]) -> str:
        tier = project.get("mechanical_evidence_tier")
        if tier == 1:
            return "Confirmed mechanical permit"
        if tier == 2:
            return "Mechanical scope on record"
        return "No mechanical evidence"

    def trade_signal_label(self, project: dict[str, Any]) -> str:
        """The trade-signal badge shown on every listing.

        Needed because discovery may run on the commercial base, where a listed project can
        have no trade evidence at all. The badge is what keeps that honest: the record is shown,
        but it is never labelled as a trade opportunity the data does not support. The wording
        comes from the trade profile's own evidence field, so a second trade renames it without
        an application change.
        """
        tier = project.get("mechanical_evidence_tier")
        strong = self.trade.strong_evidence_value
        if tier is not None and strong is not None and tier == strong:
            return "Mechanical permit on file"
        if tier is not None and tier in self.trade.evidence_values:
            return "Mechanical evidence found"
        return "Trade not verified"

    @staticmethod
    def format_month(value: Any) -> str:
        if not value:
            return "Not verified"
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            text = str(value)
            return text[:7] if len(text) >= 7 else text
        return parsed.strftime("%B %Y")

    @staticmethod
    def format_date(value: Any) -> str:
        if not value:
            return "Not verified"
        try:
            return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
        except ValueError:
            return str(value)[:10]

    # --- single opportunity ---------------------------------------------------

    def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Retrieve one opportunity by its public slug, with everything the page needs.

        Returns None when the slug is unknown so the caller can render a 404 rather than an
        empty page.
        """
        row = self.db.conn.execute(
            """
            SELECT p.*, s.slug
              FROM project p
              JOIN project_slug s ON s.project_id = p.id
             WHERE s.slug = ?
            """,
            (slug,),
        ).fetchone()
        if row is None:
            return None
        project = self.decorate(dict(row))
        project["permits"] = self.permits_for(project["id"])
        project["sources"] = self.sources_for(project["id"])
        project["evidence"] = self.evidence_for(project["id"])
        project["field_status"] = self.field_status_for(project)
        project["discrepancies"] = self.discrepancies_for(project)
        project["sibling"] = self.sibling_info_for(project)
        self.annotate_viewer([project])
        return project

    def related_for(self, project: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
        """Other discoverable opportunities in the same city.

        Ordered by the same factual rules as the directory. Offers nothing about which is
        "better"; it is simply nearby work in the same city.
        """
        rows = self.db.conn.execute(
            """
            SELECT p.*, s.slug
              FROM project p
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE p.city = ?
               AND p.id <> ?
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
             ORDER BY {order}
             LIMIT ?
            """.format(order=_recent_order("p")),
            (project.get("city"), project["id"])
            + PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT + (limit,),
        ).fetchall()
        return self.annotate_siblings([self.decorate(dict(r)) for r in rows])

    def sibling_info_for(self, project: dict[str, Any]):
        """Whether other public projects share this base street address.

        Reported rather than merged: a shared street number is a hint that two records belong to
        one building, not proof. The visitor is told the relationship is uncertain.
        """
        key = project.get("building_key")
        if not key:
            from .grouping import SiblingInfo

            return SiblingInfo(building_key=None)
        rows = self.db.conn.execute(
            """
            SELECT id, address, city FROM project
             WHERE classification IN (?, ?)
               AND procurement_status IN (?, ?, ?)
               AND city IS NOT NULL
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchall()
        groups = group_projects(
            rows,
            address_getter=lambda r: r["address"],
            city_getter=lambda r: r["city"],
            id_getter=lambda r: r["id"],
        )
        return sibling_info_for(project["id"], groups, id_getter=lambda r: r["id"])

    def permits_for(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT pm.*, s.name AS source_display_name
              FROM permit pm
              JOIN project_permit pp ON pp.permit_id = pm.id
              LEFT JOIN source s ON s.id = pm.source_id
             WHERE pp.project_id = ?
             ORDER BY pm.permit_date, pm.permit_number
            """,
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def sources_for(self, project_id: int) -> list[dict[str, Any]]:
        """Distinct source records behind a project, each with a resolvable URL.

        A customer must be able to open the underlying record. Where a source publishes no
        per-record URL, that is stated rather than hidden.
        """
        rows = self.db.conn.execute(
            """
            SELECT source_name, source_url, source_record_key,
                   MIN(source_date) AS source_date,
                   COUNT(DISTINCT field_name) AS fields_supported
              FROM evidence
             WHERE project_id = ?
             GROUP BY source_name, source_url, source_record_key
             ORDER BY source_name, source_record_key
            """,
            (project_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["source_name"], row["source_record_key"] or "")
            if key in seen:
                continue
            seen.add(key)
            item = dict(row)
            item["has_url"] = bool(item.get("source_url"))
            out.append(item)
        return out

    def evidence_for(self, project_id: int) -> dict[str, list[dict[str, Any]]]:
        """Evidence grouped by field, which is what the detail page renders."""
        rows = self.db.conn.execute(
            """
            SELECT e.*, s.market_coverage AS coverage
              FROM evidence e
              LEFT JOIN source s ON s.id = e.source_id
             WHERE e.project_id = ?
             ORDER BY e.field_name, e.id
            """,
            (project_id,),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["field_name"], []).append(dict(row))
        return grouped

    def field_status_for(self, project: dict[str, Any]) -> dict[str, str]:
        """Verdict per displayed field, so a template never decides what is verified."""
        from .report_generator import BRIEF_FIELDS
        from .reporting import field_verdict

        out: dict[str, str] = {}
        for name, _label in BRIEF_FIELDS:
            value = project.get(name)
            verdict, _evidence = field_verdict(self.db, project["id"], name, value)
            out[name] = verdict
        return out

    def discrepancies_for(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        import json

        return json.loads(project.get("discrepancies") or "[]")

    # --- statistics -----------------------------------------------------------

    def market_statistics(self) -> dict[str, Any]:
        """Real counts for the homepage and landing pages, read from the stats snapshot.

        The figures come from `app/stat_snapshot.py`, which computes them once per refresh and
        stores them, so a page cannot disagree with the API about the same word and a visitor
        cannot see two different totals seconds apart. The keys and semantics are unchanged for
        callers; only the computation moved to one place.
        """
        from .app.stat_snapshot import read_snapshot

        metrics = read_snapshot(self.db, self.market, self.trade)
        if metrics:
            return metrics
        # No snapshot and the table is absent (a database that predates it): fall back to the
        # equivalent computation so a read still works during an upgrade window.
        return self.statistics_for(where="1=1", params=[])

    def _evidence_predicate(self, alias: str = "p") -> str:
        """A literal predicate for the trade's evidence, with values inlined.

        Used only for `SUM(CASE WHEN ...)` and similar expressions where a bound parameter
        cannot appear. The values come from the trade profile and are validated integers, so
        inlining them is safe; the column name is validated by `TradeConfig.evidence_field`.
        """
        clause, values = self.trade.evidence_clause(alias)
        if not clause:
            return "0"
        # With no alias the helper still returns a qualified name; drop the leading dot.
        rendered = clause.lstrip(".") if not alias else clause
        for value in values:
            if not isinstance(value, (int, float)):
                raise ValueError(f"Evidence values must be numeric, got {value!r}")
            rendered = rendered.replace("?", str(value), 1)
        return rendered

    def statistics_for(self, *, where: str, params: list[Any]) -> dict[str, Any]:
        """Counts scoped to a subset of projects, plus the market-wide record totals.

        `scoped` applies the caller's WHERE clause and its bound parameters. `global_` runs
        against the whole database and binds nothing, which is what the record totals need —
        binding the scope parameters to a query with no placeholders raises at runtime.

        The evidence predicates come from the trade profile rather than a literal column, so a
        second trade declares its own evidence instead of needing new application code.
        """
        public_suffix = " AND classification IN (?, ?) AND procurement_status IN (?, ?, ?)"
        public_params = list(params) + list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT)

        evidence_clause, evidence_params = self.trade.evidence_clause("p")
        strong_clause, strong_params = self.trade.strong_evidence_clause("p")

        def scoped(sql: str) -> int:
            row = self.db.conn.execute(sql, tuple(params)).fetchone()
            return int(row[0] or 0)

        def public(sql: str, extra: list[Any] | None = None) -> int:
            row = self.db.conn.execute(
                sql, tuple(public_params) + tuple(extra or [])
            ).fetchone()
            return int(row[0] or 0)

        def global_(sql: str) -> int:
            row = self.db.conn.execute(sql).fetchone()
            return int(row[0] or 0)

        public_where = f"{where}{public_suffix}"
        return {
            "projects_total": scoped(f"SELECT COUNT(*) FROM project p WHERE {where}"),
            "projects_public": public(f"SELECT COUNT(*) FROM project p WHERE {public_where}"),
            "high": public(
                f"SELECT COUNT(*) FROM project p WHERE {public_where} AND p.classification = 'HIGH'"
            ),
            "medium": public(
                f"SELECT COUNT(*) FROM project p WHERE {public_where} AND p.classification = 'MEDIUM'"
            ),
            "with_mechanical": (
                public(
                    f"SELECT COUNT(*) FROM project p WHERE {public_where} AND {evidence_clause}",
                    evidence_params,
                )
                if evidence_clause
                else 0
            ),
            "tier1": (
                public(
                    f"SELECT COUNT(*) FROM project p WHERE {public_where} AND {strong_clause}",
                    strong_params,
                )
                if strong_clause
                else 0
            ),
            "cities": public(
                f"SELECT COUNT(DISTINCT p.city) FROM project p WHERE {public_where}"
            ),
            "permit_records": global_("SELECT COUNT(*) FROM permit"),
            "evidence_records": global_("SELECT COUNT(*) FROM evidence"),
        }

    def city_statistics(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            f"""
            SELECT city, COUNT(*) AS n,
                   SUM(CASE WHEN {self._evidence_predicate("")} THEN 1 ELSE 0 END) AS mech,
                   SUM(CASE WHEN classification='HIGH' THEN 1 ELSE 0 END) AS high
              FROM project
             WHERE classification IN (?, ?) AND procurement_status IN (?, ?, ?)
               AND city IS NOT NULL
             GROUP BY city
             ORDER BY n DESC, city
            """,
            tuple(PUBLIC_CLASSIFICATIONS) + tuple(DISCOVERABLE_PROCUREMENT),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["slug"] = self.market.city_slug(item["city"])
            item["name"] = item["city"]
            item["count"] = item["n"]
            item["mechanical_count"] = item["mech"]
            out.append(item)
        return out

    def type_statistics(self, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT project_type, COUNT(*) AS n,
                   SUM(CASE WHEN classification='HIGH' THEN 1 ELSE 0 END) AS high
              FROM project
             WHERE classification IN (?, ?) AND procurement_status IN (?, ?, ?)
               AND project_type IS NOT NULL
             GROUP BY project_type
             ORDER BY n DESC, project_type
             LIMIT ?
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT + (limit,),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["name"] = item["project_type"]
            item["count"] = item["n"]
            out.append(item)
        return out

    def statistics_for_type(self, project_type: str) -> dict[str, Any]:
        """Counts for one project type, for a project-type landing page.

        Every figure is a count of discoverable rows of that type, so a page is only ever built
        on data the directory actually holds.
        """
        return self.statistics_for(
            where="p.project_type = ? AND p.classification IN (?, ?) "
                  "AND p.procurement_status IN (?, ?, ?)",
            params=[project_type] + list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT),
        )

    def project_type_slug_map(self) -> dict[str, str]:
        """Every discoverable project type, mapped to a URL slug.

        Derived from the database rather than a fixed list, so a page exists only for a type
        that has real records behind it.
        """
        slugs: dict[str, str] = {}
        for row in self.db.conn.execute(
            """
            SELECT DISTINCT project_type FROM project
             WHERE classification IN (?, ?) AND procurement_status IN (?, ?, ?)
               AND project_type IS NOT NULL
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchall():
            name = row["project_type"]
            slugs[type_slug(name)] = name
        return slugs

    def cities_for_type(self, project_type: str, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT city, COUNT(*) AS n FROM project
             WHERE project_type = ? AND classification IN (?, ?)
               AND procurement_status IN (?, ?, ?) AND city IS NOT NULL
             GROUP BY city ORDER BY n DESC, city LIMIT ?
            """,
            [project_type] + list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT)
            + [limit],
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["slug"] = self.market.city_slug(item["city"])
            out.append(item)
        return out

    def data_freshness(self) -> dict[str, Any]:
        """When the underlying data was last collected.

        Public pages state this so a visitor is never led to believe the information is live.
        """
        row = self.db.conn.execute(
            "SELECT MAX(retrieval_date) AS latest, MAX(updated_at) AS updated FROM source_coverage"
        ).fetchone()
        latest = row["latest"] if row else None
        first_observed = self.db.conn.execute(
            "SELECT MIN(created_at) FROM project"
        ).fetchone()[0]
        # A real difference is a change between collection runs, not a record's first
        # appearance. The /changes page states this count so an empty feed is explained.
        diff_row = self.db.conn.execute(
            "SELECT COUNT(*) FROM project_change WHERE change_kind <> 'new_project'"
        ).fetchone()
        return {
            "retrieval_date": latest,
            "display": self.format_month(latest),
            "first_observed": first_observed,
            "first_observed_display": self.format_month(first_observed[:10] if first_observed else None),
            "real_diff_count": int(diff_row[0] or 0),
            "sources": self.db.conn.execute(
                """
                SELECT s.name, c.earliest_date, c.latest_date, c.retrieval_date,
                       c.record_count, c.pagination_notes, s.market_coverage
                  FROM source s LEFT JOIN source_coverage c ON c.source_id = s.id
                 ORDER BY COALESCE(c.record_count, 0) DESC, s.name
                """
            ).fetchall(),
        }

    def latest_verified_display(self) -> str:
        return self.format_month(
            self.db.conn.execute("SELECT MAX(last_verified) FROM project").fetchone()[0]
        )

    # --- saved opportunities --------------------------------------------------

    def saved_count(self, user_id: int) -> int:
        return int(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM saved_opportunity WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        )

    def is_saved(self, user_id: int | None, project_id: int) -> bool:
        if not user_id:
            return False
        return bool(
            self.db.conn.execute(
                "SELECT 1 FROM saved_opportunity WHERE user_id = ? AND project_id = ?",
                (user_id, project_id),
            ).fetchone()
        )

    # --- account-scoped reads -------------------------------------------------

    def projects_by_ids(self, project_ids: list[int]) -> list[dict[str, Any]]:
        """Load a set of projects by id, preserving the caller's order.

        Used by the account views, which already hold an ordered id list (most recently saved,
        watched or moved) and must not have that order re-sorted by a query. Only discoverable
        records are returned; a closed or unverified project stays out of a working list, and
        the caller is not told why, because the account view is not a moderation surface.
        """
        if not project_ids:
            return []
        ordered = [int(i) for i in project_ids]
        placeholders = ",".join("?" for _ in ordered)
        rows = self.db.conn.execute(
            f"""
            SELECT p.*, s.slug
              FROM project p
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE p.id IN ({placeholders})
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
            """,
            ordered + list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT),
        ).fetchall()
        by_id = {int(r["id"]): self.decorate(dict(r)) for r in rows}
        ordered_items = [by_id[i] for i in ordered if i in by_id]
        return self.annotate_viewer(self.annotate_siblings(ordered_items))

    def projects_for_slugs(self, slugs: list[str]) -> list[dict[str, Any]]:
        """Load projects by slug, preserving order and skipping unknown or non-public slugs."""
        if not slugs:
            return []
        placeholders = ",".join("?" for _ in slugs)
        rows = self.db.conn.execute(
            f"""
            SELECT p.*, s.slug
              FROM project p
              JOIN project_slug s ON s.project_id = p.id
             WHERE s.slug IN ({placeholders})
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
            """,
            list(slugs) + list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT),
        ).fetchall()
        by_slug = {r["slug"]: self.decorate(dict(r)) for r in rows}
        return [by_slug[s] for s in slugs if s in by_slug]

    # --- monitoring -----------------------------------------------------------

    def timeline_for(self, project_id: int) -> list[dict[str, Any]]:
        """Comprehensive intelligence timeline distinguishing source facts from interpretations.

        Combines:
        1. Official municipal permit filings (RECORDED_EVENT)
        2. Detected changes between assembly passes (RECORDED_EVENT)
        3. BuildScope intelligence deductions (BUILDSCOPE_INTERPRETATION)
        """
        timeline: list[dict[str, Any]] = []

        # 1. Historical permit filings
        permits = self.db.conn.execute(
            """
            SELECT p.*, s.name as source_name_resolved
              FROM permit p
              JOIN project_permit pp ON pp.permit_id = p.id
              LEFT JOIN source s ON s.id = p.source_id
             WHERE pp.project_id = ?
             ORDER BY p.permit_date ASC NULLS LAST, p.id ASC
            """,
            (project_id,),
        ).fetchall()

        for p in permits:
            p_date = p["permit_date"] or p["source_date"] or "Initial"
            p_type = p["permit_type"] or "Permit"
            timeline.append({
                "date": p_date,
                "detected_at": str(p_date),
                "event_kind": "RECORDED_EVENT",
                "title": f"{p_type} Filed",
                "summary": p["work_description"] or f"Permit record #{p['permit_number']} recorded in {p['city'] or 'jurisdiction'}.",
                "source_name": p["source_name_resolved"] or p["source_id"],
                "source_url": p["source_url"],
                "permit_number": p["permit_number"],
                "is_interpretation": False,
            })

        # 2. Detected differences between passes
        changes = self.db.changes_for_project(project_id, limit=30)
        for c in changes:
            timeline.append({
                "date": c["detected_at"][:10] if c.get("detected_at") else "Recent",
                "detected_at": c.get("detected_at") or "Recent",
                "event_kind": "RECORDED_EVENT",
                "title": c["change_kind"].replace("_", " ").title(),
                "summary": c["summary"],
                "source_name": c.get("source_name") or "Municipal Source",
                "source_url": c.get("source_url"),
                "is_interpretation": False,
            })

        # 3. BuildScope Intelligence Interpretations
        proj_row = self.db.conn.execute(
            "SELECT * FROM project WHERE id = ?", (project_id,)
        ).fetchone()

        if proj_row:
            tier = proj_row["mechanical_evidence_tier"]
            if tier == 1:
                timeline.append({
                    "date": proj_row["permit_date"] or "Active",
                    "detected_at": str(proj_row["permit_date"] or "Active"),
                    "event_kind": "BUILDSCOPE_INTERPRETATION",
                    "title": "Direct Mechanical Subcontracting Evidence",
                    "summary": (
                        "First-class mechanical permit identified on municipal record. "
                        "Confirms documented mechanical/HVAC contractor scope."
                    ),
                    "source_name": "BuildScope Intelligence Engine",
                    "source_url": None,
                    "is_interpretation": True,
                })
            elif tier == 2:
                timeline.append({
                    "date": proj_row["permit_date"] or "Active",
                    "detected_at": str(proj_row["permit_date"] or "Active"),
                    "event_kind": "BUILDSCOPE_INTERPRETATION",
                    "title": "Mechanical Scope Language Detected",
                    "summary": (
                        "Trade-specific mechanical keywords identified in scope description text. "
                        "Indicates mechanical equipment or distribution requirements."
                    ),
                    "source_name": "BuildScope Intelligence Engine",
                    "source_url": None,
                    "is_interpretation": True,
                })

        # Sort reverse chronologically
        timeline.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        return timeline

    # --- intelligence graph ---------------------------------------------------

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        """One project by internal id, with its permits, evidence and graph relationships.

        The id-based sibling of `get_by_slug`, exposed so the intelligence graph is reachable
        through the same boundary the rest of the application uses rather than by touching the
        database directly.
        """
        row = self.db.conn.execute(
            """
            SELECT p.*, s.slug
              FROM project p
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        project = self.decorate(dict(row))
        project["permits"] = self.permits_for(project_id)
        project["sources"] = self.sources_for(project_id)
        project["evidence"] = self.evidence_for(project_id)
        project["field_status"] = self.field_status_for(project)
        project["discrepancies"] = self.discrepancies_for(project)
        return project

    def get_project_evidence(self, project_id: int, *, field_name: str | None = None) -> list[dict[str, Any]]:
        """The immutable evidence history for a project, newest observation first.

        This is the archive, not the current supporting set: every observation ever made is
        retained, so a fact that changed value shows both the old and the new value rather
        than only the latest.
        """
        return self.db.evidence_history_for(project_id, field_name=field_name)

    def get_project_events(self, project_id: int) -> list[dict[str, Any]]:
        """The durable, evidenced event timeline for a project, oldest first."""
        return self.db.events_for_project(project_id)

    def get_project_companies(self, project_id: int) -> list[dict[str, Any]]:
        """Company entities resolved on a project, with the role each was observed in."""
        return [
            e for e in self.db.entities_for_project(project_id)
            if e.get("entity_type") == "company"
        ]

    def get_project_people(self, project_id: int) -> list[dict[str, Any]]:
        return [
            e for e in self.db.entities_for_project(project_id)
            if e.get("entity_type") == "person"
        ]

    def get_project_documents(self, project_id: int) -> list[dict[str, Any]]:
        """Source documents a project's facts were drawn from."""
        return self.db.documents_for_project(project_id)

    def get_project_location(self, project_id: int) -> dict[str, Any] | None:
        return self.db.location_for_project(project_id)

    def get_project_trades(self, project_id: int) -> list[dict[str, Any]]:
        """Trades the project carries, classified from documented permit text."""
        return self.db.trades_for_project(project_id)

    def search_companies(
        self, *, q: str | None = None, role: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search resolved company entities by name, optionally filtered by role.

        Reads the entity graph, so results are the *resolved* companies rather than raw
        spellings. A role filter selects entities observed in that role on at least one
        project.
        """
        clauses: list[str] = ["e.entity_type = 'company'"]
        params: list[Any] = []
        if q and q.strip():
            clauses.append("LOWER(e.display_name) LIKE ?")
            params.append(f"%{q.strip().lower()}%")
        if role:
            clauses.append(
                "EXISTS (SELECT 1 FROM entity_project ep WHERE ep.entity_id = e.id AND ep.role = ?)"
            )
            params.append(role)
        where = " AND ".join(clauses)
        rows = self.db.conn.execute(
            f"""
            SELECT e.id, e.display_name,
                   COUNT(DISTINCT ep.project_id) AS project_count,
                   GROUP_CONCAT(DISTINCT ep.role) AS roles
              FROM entity e
              LEFT JOIN entity_project ep ON ep.entity_id = e.id
             WHERE {where}
             GROUP BY e.id
             ORDER BY project_count DESC, e.display_name
             LIMIT ?
            """,
            tuple(params) + (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["roles"] = sorted((item.get("roles") or "").split(",")) if item.get("roles") else []
            out.append(item)
        return out

    def search_projects(
        self, *, q: str | None = None, trade_id: str | None = None,
        city: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search discoverable projects, optionally filtered by trade and city.

        Built on the same public rules as the directory (classification and procurement), and
        extended with the trade graph. Returns ids and display fields only; a caller loads a
        full record through `get_project`, which reapplies the same rules.
        """
        clauses: list[str] = [
            "p.classification IN (?, ?)",
            "p.procurement_status IN (?, ?, ?)",
        ]
        params: list[Any] = list(PUBLIC_CLASSIFICATIONS) + list(DISCOVERABLE_PROCUREMENT)
        if city:
            clauses.append("p.city = ?")
            params.append(city)
        if trade_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM project_trade pt WHERE pt.project_id = p.id AND pt.trade_id = ?)"
            )
            params.append(trade_id)
        if q and q.strip():
            ids = self._search_ids(q)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            clauses.append(f"p.id IN ({placeholders})")
            params.extend(ids)
        where = " AND ".join(clauses)
        rows = self.db.conn.execute(
            f"""
            SELECT p.id, p.project_name, p.address, p.city, p.project_type,
                   p.classification, p.procurement_status, p.permit_date, s.slug
              FROM project p
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE {where}
             ORDER BY {_recent_order("p")}
             LIMIT ?
            """,
            tuple(params) + (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def changes_for(self, project_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Recorded changes for one project, newest first.

        Read straight from the change table, so the timeline shows only events the detector
        actually observed. A project with no changes has an empty timeline and the page says so
        rather than inventing an initial event.
        """
        return self.db.changes_for_project(project_id, limit=limit)

    def recent_changes(
        self, *, limit: int = 20, days: int = 30, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Changes across all public projects in the recent window, newest first.

        Restricted to projects that are publicly discoverable, so the "recently updated"
        section cannot surface a project the directory itself withholds. ``offset`` supports a
        paged feed; the matching total is `recent_changes_count`.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.conn.execute(
            """
            SELECT c.*, s.slug, p.project_name, p.address, p.city, p.classification,
                   p.procurement_status
              FROM project_change c
              JOIN project p ON p.id = c.project_id
              LEFT JOIN project_slug s ON s.project_id = p.id
             WHERE c.detected_at >= ?
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
             ORDER BY c.id DESC
             LIMIT ? OFFSET ?
            """,
            (cutoff,) + PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT + (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_changes_count(self, *, days: int = 30) -> int:
        """How many changes match the public recent-changes filter.

        The count applies the same public predicate as `recent_changes`, so a page's headline
        ("N detected changes") is the size of the result set and not the size of one page. This
        is what fixes the count that stopped at the page limit.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        row = self.db.conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM project_change c
              JOIN project p ON p.id = c.project_id
             WHERE c.detected_at >= ?
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
            """,
            (cutoff,) + PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchone()
        return int(row["n"] or 0)

    def recent_changes_breakdown(self, *, days: int = 30) -> dict[str, int]:
        """Counts by `change_kind` over the same public filter as `recent_changes_count`.

        The feed must not describe a first observation as a detected difference: `new_project`
        is the moment a record entered the system, not a difference between two passes. The
        breakdown lets the page say so honestly instead of summing both into "changes".
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.conn.execute(
            """
            SELECT c.change_kind AS kind, COUNT(*) AS n
              FROM project_change c
              JOIN project p ON p.id = c.project_id
             WHERE c.detected_at >= ?
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
             GROUP BY c.change_kind
            """,
            (cutoff,) + PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT,
        ).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}

    def changed_project_ids(self, *, limit: int = 20, days: int = 30) -> list[int]:
        """Distinct project ids with a recent change, most recently changed first."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.conn.execute(
            """
            SELECT c.project_id, MAX(c.id) AS last_id
              FROM project_change c
              JOIN project p ON p.id = c.project_id
             WHERE c.detected_at >= ?
               AND p.classification IN (?, ?)
               AND p.procurement_status IN (?, ?, ?)
             GROUP BY c.project_id
             ORDER BY last_id DESC
             LIMIT ?
            """,
            (cutoff,) + PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT + (limit,),
        ).fetchall()
        return [int(r["project_id"]) for r in rows]

    def recent_discoverable_ids(self, *, limit: int = 200) -> list[int]:
        """Ids of the most recently dated discoverable projects.

        Exposed so the dashboard's candidate pool comes from the same public rules as the
        directory rather than from a second query written in the web layer. Returns ids only;
        the caller loads full records through `projects_by_ids`, which reapplies the public
        filter, so a project cannot slip through by being listed here.
        """
        rows = self.db.conn.execute(
            f"""
            SELECT id FROM project p
             WHERE classification IN (?, ?)
               AND procurement_status IN (?, ?, ?)
             ORDER BY {_recent_order("p")}
             LIMIT ?
            """,
            PUBLIC_CLASSIFICATIONS + DISCOVERABLE_PROCUREMENT + (limit,),
        ).fetchall()
        return [int(r["id"]) for r in rows]


class _SqliteRow:
    """Adapts a plain dict to the row interface `eligibility.evaluate` expects."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def keys(self):
        return self._data.keys()

    def __contains__(self, key: str) -> bool:
        return key in self._data