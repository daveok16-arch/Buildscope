"""First-party search and product analytics.

Answers the questions the product needs in order to improve itself, from rows it already
stores rather than from a third-party script:

* What do visitors search for?
* Which searches return nothing, and therefore name a gap in coverage or in the vocabulary?
* Which filters are used, and which are never used?
* How far do visitors get through the funnel in one visit?

Privacy is unchanged from `record_analytics`: the source rows carry no user id, no IP address
and no user agent. A `session_id` groups one visit's events; it is random, short-lived, and
cannot be resolved to a person. Nothing here re-identifies a visitor, and no query text is ever
rendered on a public page — this module feeds the operator view only.
"""

from __future__ import annotations

from typing import Any

from ..db import Database

#: A search that returned nothing is the most actionable signal in the data, so it is surfaced
#: first. Kept as a named constant so the query and the report agree.
ZERO_RESULT = "zero_result"


def top_queries(db: Database, *, limit: int = 25, days: int = 90) -> list[dict[str, Any]]:
    """The most frequent search terms, with their average result count."""
    rows = db.conn.execute(
        """
        SELECT LOWER(TRIM(query_text)) AS term,
               COUNT(*) AS searches,
               AVG(COALESCE(result_count, 0)) AS avg_results,
               SUM(CASE WHEN COALESCE(result_count, 0) = 0 THEN 1 ELSE 0 END) AS zero_results
          FROM analytics_event
         WHERE event_name = 'search_performed'
           AND query_text IS NOT NULL AND TRIM(query_text) <> ''
           AND created_at >= datetime('now', ?)
         GROUP BY term
         ORDER BY searches DESC, term
         LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    ).fetchall()
    return [
        {
            "term": r["term"],
            "searches": int(r["searches"]),
            "avg_results": round(float(r["avg_results"] or 0), 1),
            "zero_results": int(r["zero_results"] or 0),
        }
        for r in rows
    ]


def zero_result_queries(db: Database, *, limit: int = 25, days: int = 90) -> list[dict[str, Any]]:
    """Searches that returned nothing. Each is a coverage or vocabulary gap to investigate."""
    rows = db.conn.execute(
        """
        SELECT LOWER(TRIM(query_text)) AS term, COUNT(*) AS searches
          FROM analytics_event
         WHERE event_name = 'search_performed'
           AND query_text IS NOT NULL AND TRIM(query_text) <> ''
           AND COALESCE(result_count, 0) = 0
           AND created_at >= datetime('now', ?)
         GROUP BY term
         ORDER BY searches DESC, term
         LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    ).fetchall()
    return [{"term": r["term"], "searches": int(r["searches"])} for r in rows]


def filter_usage(db: Database, *, days: int = 90) -> list[dict[str, Any]]:
    """How often each filter was applied.

    `filter_summary` holds a comma-joined, sorted list of the filter names a search used, so a
    group-by over its parts reports usage without a second table.
    """
    rows = db.conn.execute(
        """
        SELECT filter_summary FROM analytics_event
         WHERE event_name = 'search_performed'
           AND filter_summary IS NOT NULL AND TRIM(filter_summary) <> ''
           AND created_at >= datetime('now', ?)
        """,
        (f"-{int(days)} days",),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for name in str(row["filter_summary"]).split(","):
            name = name.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return [
        {"filter": name, "uses": n}
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def search_totals(db: Database, *, days: int = 90) -> dict[str, Any]:
    """Headline search figures for the operator dashboard."""
    row = db.conn.execute(
        """
        SELECT COUNT(*) AS searches,
               SUM(CASE WHEN COALESCE(result_count, 0) = 0 THEN 1 ELSE 0 END) AS zero_results,
               COUNT(DISTINCT NULLIF(TRIM(LOWER(query_text)), '')) AS distinct_terms,
               COUNT(DISTINCT session_id) AS sessions
          FROM analytics_event
         WHERE event_name = 'search_performed'
           AND created_at >= datetime('now', ?)
        """,
        (f"-{int(days)} days",),
    ).fetchone()
    searches = int(row["searches"] or 0)
    zero = int(row["zero_results"] or 0)
    return {
        "searches": searches,
        "zero_results": zero,
        "zero_result_rate": round(zero / searches, 3) if searches else 0.0,
        "distinct_terms": int(row["distinct_terms"] or 0),
        "sessions": int(row["sessions"] or 0),
    }


def session_depth(db: Database, *, days: int = 90, limit: int = 20) -> list[dict[str, Any]]:
    """Events per session, so a visit that searched once can be told from one that explored."""
    rows = db.conn.execute(
        """
        SELECT session_id, COUNT(*) AS events,
               MIN(created_at) AS first_seen, MAX(created_at) AS last_seen
          FROM analytics_event
         WHERE session_id IS NOT NULL
           AND created_at >= datetime('now', ?)
         GROUP BY session_id
         ORDER BY events DESC, first_seen
         LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    ).fetchall()
    return [
        {
            "session_id": r["session_id"],
            "events": int(r["events"]),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
        }
        for r in rows
    ]


def analytics_report(db: Database, *, days: int = 90) -> dict[str, Any]:
    """Everything the operator view needs, from one call."""
    return {
        "window_days": days,
        "totals": search_totals(db, days=days),
        "top_queries": top_queries(db, days=days),
        "zero_result_queries": zero_result_queries(db, days=days),
        "filter_usage": filter_usage(db, days=days),
        "session_depth": session_depth(db, days=days),
    }
