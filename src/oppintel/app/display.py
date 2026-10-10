"""Presentation transforms for source text.

Source records arrive as raw municipal data: project names and addresses are frequently
shouted in all caps ("WINDMILL HILL ADDITION"), work descriptions carry embedded newlines and
``" , "`` spacing, and trade shorthand (HVAC, AHU, RTU, TX) must survive whatever we do to the
rest of the string.

Everything here is a display-only transform. It never changes a stored value and never runs on
the intelligence layer — a project name is normalised for the page, not in the database. The
rules are deliberately conservative:

* **Only shouting is rewritten.** A string that already contains lowercase words is left as the
  source wrote it, because "MCR Novem Wealth Management - Commercial Interior Remodel" is
  correct prose and title-casing it would be damage, not polish.
* **Acronyms and codes are preserved.** A curated set (per trade) is upper-cased; a token that
  contains a digit (``PB25-09094``, ``240V``) is left untouched so identifiers are never mangled.
* **Missing stays missing.** :func:`present_fields` drops unverified fields so a card can
  collapse them into one honest summary line rather than repeat "Not verified" in every slot.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

#: Tokens that are acronyms or brand-cased words and must stay upper-cased. Trade terms first
#: (so HVAC/AHU/RTU survive), then geography and measurement units that appear in names and
#: addresses. Kept here rather than in config because these are display conventions, not market
#: or trade configuration.
_ACRONYMS: frozenset[str] = frozenset(
    {
        # Mechanical / trade equipment and systems.
        "hvac", "ahu", "rtu", "vav", "vrv", "fcu", "ptac", "wshp", "doas", "mua", "erv", "hru",
        "ems", "bas", "vfd", "dhw", "cdw", "mep", "cfm", "gpm", "psi", "btu", "kva", "kw",
        "led", "hvacr", "hvl", "cc", # noqa: E501
        # Professional / administrative abbreviations.
        "ada", "nsf", "usf", "rsf", "gsf", "sf", "rfi", "pco", "rfp", "gc", "cm", "aor", "osf",
        # Geography.
        "tx", "tx.", "usa", "dfw", "us",
        # Technology / general.
        "it", "api", "ui", "ux", "sql", "csv", "pdf", "cctv", "av", "a/v", "ip", "poe", "ev",
        "ups", "plc", "gis", "cad", "ai", "ml", "id", "ids", "3d", "2d",
    }
)

#: Words kept lower-case in the middle of a title (unless they are the first or last word).
_SMALL_WORDS: frozenset[str] = frozenset(
    {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the",
     "to", "up", "via", "vs", "with", "from"}
)

#: A token that is only digits/punctuation (a house number, a value) is never re-cased.
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace/newlines and fix a space left before sentence punctuation."""
    text = re.sub(r"\s+", " ", text).strip()
    # Municipal exports often produce "100 CRESCENT CT , 550" — remove the space before the comma.
    text = re.sub(r"\s+([,;:])", r"\1", text)
    return text


def _is_shouting(text: str) -> bool:
    """Whether a string is written in all caps.

    A string with any lower-case letter is treated as intentional prose and left alone; only a
    string whose letters are effectively all upper-case is re-cased. Characters without case
    (digits, punctuation) do not count either way.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / len(letters) >= 0.9


def _recase_word(word: str, *, is_first: bool, is_last: bool) -> str:
    """Re-case one alphabetic token, preserving acronyms, identifiers and small words."""
    if not word:
        return word
    # A token with a digit ("240V", "PB25") or an unusual shape is an identifier: leave it.
    if any(ch.isdigit() for ch in word):
        return word
    lowered = word.lower()
    if lowered in _ACRONYMS:
        return word.upper()
    if lowered in _SMALL_WORDS and not (is_first or is_last):
        return lowered
    # Re-case word fragments split by apostrophes: O'BRIEN -> O'Brien.
    parts = re.split(r"([’'])", word)
    out = []
    for part in parts:
        if part in ("'", "’") or part == "":
            out.append(part)
        else:
            out.append(part[:1].upper() + part[1:].lower())
    return "".join(out)


def _titlecase_shouting(text: str) -> str:
    """Title-case a shouting string, re-casing only the runs of letters.

    The string is walked with ``_WORD_RE`` so punctuation, slashes and parentheses are kept
    verbatim; only the matched alphabetic words are re-cased. A word that sits inside a
    whitespace-delimited token containing a digit (``PB25-09094``, ``240V``, ``40A``) is an
    identifier and is left exactly as the source wrote it.
    """
    matches = list(_WORD_RE.finditer(text))
    if not matches:
        return text

    # Character ranges of tokens that carry a digit, so an identifier is never re-cased.
    identifier_ranges: list[tuple[int, int]] = [
        (token.start(), token.end())
        for token in re.finditer(r"\S+", text)
        if any(ch.isdigit() for ch in token.group(0))
    ]

    def in_identifier(pos: int) -> bool:
        return any(start <= pos < end for start, end in identifier_ranges)

    out: list[str] = []
    cursor = 0
    last = len(matches) - 1
    for index, match in enumerate(matches):
        out.append(text[cursor:match.start()])
        if in_identifier(match.start()):
            out.append(match.group(0))
        else:
            out.append(
                _recase_word(match.group(0), is_first=(index == 0), is_last=(index == last))
            )
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def titlecase(value: Any) -> str:
    """Normalise display text: collapse whitespace and title-case only shouting strings.

    Returns an empty string for a missing value so a template can decide how to render the gap
    (:func:`present_fields` does that for facts).
    """
    if value is None:
        return ""
    text = _collapse_whitespace(str(value))
    if not text:
        return ""
    if not _is_shouting(text):
        return text
    return _titlecase_shouting(text)


def has_value(value: Any) -> bool:
    """Whether a value is present and not one of the "missing" sentinels.

    Centralised so a template never has to compare against the literal "Not verified"; the one
    place the sentinel is defined is ``or_na``/this predicate.
    """
    if value is None:
        return False
    if isinstance(value, str) and value.strip() in ("", "Not verified"):
        return False
    return True


def present_fields(
    fields: Sequence[tuple[str, Any]]
) -> tuple[list[tuple[str, Any]], list[str]]:
    """Split ``(label, value)`` pairs into the verified pairs and the missing labels.

    A card shows the verified facts and one summary line naming what is missing, rather than a
    placeholder in every slot. The missing labels are still reported, so nothing is hidden — the
    absence is stated once instead of repeated.
    """
    present = [(label, value) for label, value in fields if has_value(value)]
    missing = [label for label, value in fields if not has_value(value)]
    return present, missing


def join_missing(labels: Iterable[str]) -> str:
    """A human list of missing field labels: "Declared value", "Declared value and scale"."""
    items = list(labels)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def split_facts(
    fields: Iterable[tuple[str, Any, Any]]
) -> tuple[list[tuple[str, Any, str]], list[str]]:
    """Split ``(label, raw_value, rendered)`` triples for a facts block.

    A card renders the verified facts as a definition list and names the missing ones once.
    ``raw_value`` decides presence (so a "Not verified" that a filter produced is still
    missing); ``rendered`` is what the present rows show.
    """
    present: list[tuple[str, Any, str]] = []
    missing: list[str] = []
    for label, raw, rendered in fields:
        if has_value(raw):
            present.append((label, raw, rendered))
        else:
            missing.append(label)
    return present, missing
