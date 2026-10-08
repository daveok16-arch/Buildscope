"""Full-text search index over projects.

Search runs against an FTS5 table rather than `LIKE` against the permit tables. Two reasons:

1. **Speed.** A public search box hits this on every request. Scanning 22,000 permit rows per
   search would not stay fast as the dataset grows.
2. **Separation.** The index is derived data that can be dropped and rebuilt at any time,
   which keeps the intelligence schema authoritative.

Only text a customer would actually search is indexed. Nothing here invents or alters a
project fact: the index holds copies of existing columns purely to make them findable, and
every result is re-read from `project` before display, so a page never renders indexed text as
the source of truth.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime, timezone

from .config import VocabularyGroup
from .db import Database

log = logging.getLogger(__name__)

#: Indexed columns, in insert order. Kept in one place so the rebuild and the query agree.
INDEXED_COLUMNS = (
    "project_id",
    "project_name",
    "address",
    "city",
    "permit_number",
    "work_description",
    "owner",
    "project_type",
)

#: The project columns those index fields are populated from.
#:
#: The work description lives on the permits that formed the project. SQLite rejects
#: `GROUP_CONCAT(DISTINCT x, sep)` — a DISTINCT aggregate takes exactly one argument — so the
#: separator is applied by concatenating distinct values with a plain comma and normalising
#: the punctuation afterwards. The text is only indexed for matching, never displayed.
SOURCE_QUERY = """
SELECT p.id AS project_id,
       p.project_name,
       p.address,
       p.city,
       p.permit_number,
       (SELECT REPLACE(GROUP_CONCAT(DISTINCT pm.work_description), ',', ' ')
          FROM permit pm
          JOIN project_permit pp ON pp.permit_id = pm.id
         WHERE pp.project_id = p.id
           AND pm.work_description IS NOT NULL) AS work_description,
       p.owner,
       p.project_type
  FROM project p
"""


def rebuild_index(db: Database, *, batch_size: int = 500) -> int:
    """Rebuild the search index from scratch. Returns the number of projects indexed.

    A full rebuild rather than incremental updates: the whole index is cheap to construct
    relative to an ingestion run, and a rebuild cannot drift from the project table the way a
    sequence of partial updates can.
    """
    started = datetime.now(timezone.utc)
    db.conn.execute("DELETE FROM project_search")
    db.conn.commit()

    total = 0
    cursor = db.conn.execute(SOURCE_QUERY)
    batch: list[tuple] = []
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            batch.append(
                tuple(
                    "" if row[column] is None else str(row[column])
                    for column in INDEXED_COLUMNS
                )
            )
        db.conn.executemany(
            f"INSERT INTO project_search ({', '.join(INDEXED_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in INDEXED_COLUMNS)})",
            batch,
        )
        total += len(batch)
        batch.clear()
        db.conn.commit()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("Rebuilt search index: %d projects in %.1fs", total, elapsed)
    return total


def index_count(db: Database) -> int:
    return int(db.conn.execute("SELECT COUNT(*) FROM project_search").fetchone()[0])


#: A word is a run of letters, digits and interior apostrophes. FTS5's unicode61 tokenizer
#: folds case and splits on everything else, so this mirrors it closely enough to match the
#: same text; the apostrophe is kept so a term is not split mid-word.
_WORD_RE = re.compile(r"[a-z0-9']+")


def tokenize_query(text: str) -> list[str]:
    """The words of a query, lower-cased to match the vocabulary's keys."""
    return _WORD_RE.findall((text or "").lower())


def expand_terms(
    text: str, vocabulary: Sequence[VocabularyGroup] | None = None
) -> list[list[str]]:
    """Group a query's words into alternative-sets, expanding known terms to their synonyms.

    Returns a list of groups in query order. A plain word is its own single-member group; a
    word that names a configured term expands to every equivalent term in that term's group,
    and the words of a matched multi-word synonym are consumed so the phrase is not also
    searched as unrelated words.

    Alternatives are whole *phrases*, not loose words: expanding "hvac" to the mechanical
    group must not also search "scope" and "work" just because "mechanical scope" contains
    them. Longest terms are matched first so "air handling unit" wins over "air".
    """
    tokens = tokenize_query(text)
    if not tokens:
        return []

    groups = list(vocabulary or ())
    index: dict[str, VocabularyGroup] = {}
    for group in groups:
        for term in group.terms:
            index.setdefault(term, group)
    # Longest phrase first, so a multi-word synonym is preferred over a shorter overlapping one.
    known = sorted(index, key=lambda t: (-len(tokenize_query(t)), t))

    expanded: list[list[str]] = []
    i = 0
    while i < len(tokens):
        matched = False
        for term in known:
            term_tokens = tokenize_query(term)
            if term_tokens and tokens[i:i + len(term_tokens)] == term_tokens:
                expanded.append(list(index[term].terms))
                i += len(term_tokens)
                matched = True
                break
        if not matched:
            expanded.append([tokens[i]])
            i += 1
    return expanded


def _phrase_clause(phrase: str) -> str:
    """One FTS5 clause for a word or phrase, prefix-matched on its final token."""
    words = tokenize_query(phrase)
    if not words:
        return ""
    if len(words) == 1:
        return f'"{words[0]}"*'
    return f'"{" ".join(words)}"*'


def quote_for_fts(
    text: str, vocabulary: Sequence[VocabularyGroup] | None = None
) -> str:
    """Turn user input into a safe FTS5 query string, expanding configured synonyms.

    User input is not valid FTS5 syntax on its own: an unbalanced quote or a bare ``AND``
    raises an OperationalError, which would surface as a 500. Every term is quoted, so the
    result is always well-formed, and terms are prefix-matched so "ross" finds "ROSS AVE".

    A term whose group has more than one equivalent becomes an OR-group, so ``ahu`` also finds
    ``air handling unit``::

        ahu replacement -> ("ahu"* OR "air handler"* OR "air handling unit"*) AND "replacement"*

    Expansion only ever *widens* a search — the matched term stays in its own OR-group — so a
    query that worked before the vocabulary existed returns at least the same results.

    This is a safety and ergonomics measure, not a security boundary: the value is still bound
    as a query parameter.
    """
    clauses: list[str] = []
    for alternatives in expand_terms(text, vocabulary):
        # Two spellings that tokenize the same way ("split-system" and "split system") are one
        # clause, not two, so the expansion stays readable and the OR-list has no dead entries.
        seen: set[tuple[str, ...]] = set()
        phrases: list[str] = []
        for phrase in alternatives:
            key = tuple(tokenize_query(phrase))
            if key and key not in seen:
                seen.add(key)
                phrases.append(phrase)
        if not phrases:
            continue
        if len(phrases) == 1:
            clauses.append(_phrase_clause(phrases[0]))
        else:
            ors = " OR ".join(c for c in (_phrase_clause(p) for p in phrases) if c)
            clauses.append(f"({ors})")
    return " AND ".join(clauses)