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
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

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
    # A period too: "DWELLING UNITS . INCLUDING" is the same artifact, not a decimal.
    text = re.sub(r"\s+([,;:.])", r"\1", text)
    return text


def _collapse_repeated_punctuation(text: str) -> str:
    """Reduce runs of the same punctuation mark to a single mark and tidy the spacing.

    "X TEAM ,,, " is a source artifact, not prose. A run of slashes becomes a spaced slash so it
    reads as a clause separator ("PB26-12945/// Addition" -> "PB26-12945 / Addition"), which lets
    the reference-clause strip below see the boundary.
    """
    text = re.sub(r"\s*/{2,}\s*", " / ", text)
    text = _REPEATED_PUNCT_RE.sub(r"\1", text)
    # A mark left standing between two spaces ("a , b") is a stray separator, not punctuation.
    text = re.sub(r"\s+([,;:/|])\s+", r" \1 ", text)
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


#: A leading internal marker that a source prepends to a work description: "QTEAM", "X TEAM",
#: "Q-Team". Matched at the start only. The separator after it is handled separately, because the
#: same marker is published as "QTEAM MEETING TBD -", "X TEAM ///" and "X-Team ,,,".
_MARKER_TOKEN_RE = re.compile(r"^\s*(?:q\s*-?\s*team|x\s*-?\s*team)\b", re.IGNORECASE)

#: Administration words that follow a marker and are not part of the identity.
_MARKER_NOTE_RE = re.compile(r"^(?:meeting|inhouse|in-house|tbd|tba)\b[\s,;:/\-–—]*", re.IGNORECASE)

#: A date (with an optional time parenthetical) that follows "MEETING", as in "MEETING 10/27/2026
#: (9AM) -". Consumed so the marker strip reaches the real title.
_MARKER_DATE_RE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?\s*(?:\([^)]*\))?[\s,;:/\-–—]*")

#: Non-team administration markers, each requiring a separator so the word is not removed from a
#: real name that merely begins with it.
_NON_TEAM_PREFIXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:pre[-\s]?application|pre[-\s]?app)\s*meeting\s*[\-–—:/,]{1,}\s*",
               re.IGNORECASE),
    re.compile(r"^\s*meeting\s*(?:tbd|tba)?\s*[\-–—:/,]{1,}\s*", re.IGNORECASE),
)

#: Runs of three or more identical punctuation marks ("X TEAM ,,, "), a source artifact rather
#: than prose. Collapsed to a single mark (or removed when it stands alone between spaces).
_REPEATED_PUNCT_RE = re.compile(r"([,;:/\\|.\-–—])\1{2,}")

#: Words that, present in a cleaned title, mark it as an internal note rather than an identity.
_NOTE_WORDS: frozenset[str] = frozenset({"meeting", "tbd", "tba", "inhouse", "in-house"})

#: A compact permit number ("PB26-12945", "PO26-02663"): a short letter prefix plus a digit run.
_PERMIT_REF_RE = re.compile(r"^[A-Za-z]{1,4}\d{2,4}[-/]\d{2,6}$")

#: A leading cross-reference phrase inside a clause: "Refer to PB26-12945", "see PO26-02663", or a
#: bare permit number at the start of a clause. Only the reference phrase is removed, so scope text
#: that follows it ("Refer to PB26-13456 THE PROJECT CONSISTS OF ...") is kept.
_LEADING_REF_RE = re.compile(
    r"^(?:(?:refer(?:\s+to)?|see|per|cf|re)\s+)?"
    r"[A-Za-z]{1,4}\d{2,4}[-/]\d{2,6}\b[\s.,;:/\-–—]*",
    re.IGNORECASE,
)

#: How many marker/administration/date rounds the prefix strip will run before giving up.
_MARKER_SCAN_STEPS = 4

#: A lowercase letter immediately followed by a digit ("is16,297"). The two tokens ran together
#: when the source concatenated them. Restricted to a lower-case letter so an identifier such as
#: "PB25-09094" (upper-case letter + digit) and a capacity ("240V") are never split, and `x` is
#: excluded so a dimension ("12x24x24", "2x4") is not broken.
_LETTER_DIGIT_RE = re.compile(r"([a-wyz])(\d)")

#: A verbose work description longer than this is reduced to its first sentence. Below it, the
#: whole (short) text is kept, so a compact title is never truncated.
_SUMMARISE_MIN_LENGTH = 140

#: Abbreviations and single-letter initials whose trailing period is not a sentence end. Checked
#: before a period is treated as a boundary, so "The Dr. Frederick Douglass Todd/Whitney M. Young
#: project ..." is not cut after "Dr.".
_ABBREVIATIONS: frozenset[str] = frozenset(
    {"dr", "mr", "mrs", "ms", "st", "jr", "sr", "no", "inc", "ltd", "co", "corp", "ave",
     "blvd", "rd", "ste", "fl", "bldg", "sq", "mt", "ft", "dept", "univ", "vs", "etc"}
)

#: A comma or semicolon that is *not* inside a number ("2,765", "1,500,000"). Used so a clause
#: split never breaks a thousands-separated figure.
_CLAUSE_SPLIT_RE = re.compile(r"(?<!\d)[,;](?!\s*\d)|(?<!\d)\s[-–—]\s")


def _first_sentence(text: str) -> str:
    """The leading sentence of ``text``, treating an abbreviation's period as not a boundary."""
    for match in re.finditer(r"(?<=[.!?])\s+", text):
        before = text[: match.start()].rstrip()
        last_word = re.split(r"[\s/]", before)[-1].strip(".").lower()
        if last_word in _ABBREVIATIONS or len(last_word) == 1:
            continue
        return before
    return text.strip()


def _summarise_verbose_title(text: str) -> str:
    """Reduce a long prose work description to a headline when the source record allows it.

    A municipal work description can run to several sentences ("Ground-up new construction of a
    single-story office and warehouse building totaling 10,500 square feet. The building
    consists of ..."). The first sentence carries the identity; the rest is specification. Only
    applied to a genuinely long description and only across a safe boundary, so a compact title
    or an abbreviation never produces a truncated fragment.
    """
    if len(text) < _SUMMARISE_MIN_LENGTH:
        return text
    stripped = text.strip()
    head = _first_sentence(stripped)
    if head != stripped and 50 <= len(head) < len(stripped):
        return head
    # No usable sentence break: reduce a run-on to its leading clause. The split protects a
    # thousands-separated figure, and a substantial lead clause is required.
    lead = _CLAUSE_SPLIT_RE.split(stripped, maxsplit=1)[0].strip()
    if 50 <= len(lead) < len(stripped):
        return lead
    return text


def _strip_title_prefix(text: str) -> str:
    """Drop a leading meeting/administration marker that is not part of the project identity.

    "QTEAM MEETING TBD - Ground-up ..." -> "Ground-up ...". Only a marker at the very start is
    removed; the same words inside a description are left alone. A marker may be followed by
    administration words ("MEETING", "INHOUSE"), a meeting date ("MEETING 10/27/2026 (9AM)"), and
    a run of separators ("-", "///", ",,,"), in any combination, so the strip loops until none of
    those is left. The separator is optional: a bare "X TEAM EV Chargers" loses just the marker.
    """
    stripped = text.lstrip()
    for _ in range(_MARKER_SCAN_STEPS):
        marker = _MARKER_TOKEN_RE.match(stripped)
        if marker:
            stripped = stripped[marker.end():].lstrip()
        stripped = _MARKER_NOTE_RE.sub("", stripped, count=1).lstrip()
        stripped = _MARKER_DATE_RE.sub("", stripped, count=1).lstrip()
        stripped = re.sub(r"^[\-–—:/,;]{1,}\s*", "", stripped)
        if not (_MARKER_TOKEN_RE.match(stripped) or _MARKER_NOTE_RE.match(stripped)):
            break
    for pattern in _NON_TEAM_PREFIXES:
        stripped = pattern.sub("", stripped, count=1)
    return stripped


def _looks_like_a_note(text: str) -> bool:
    """Whether a cleaned title still reads as an internal note rather than a project identity.

    True when it is empty, names a note word ("meeting", "TBD", "inhouse"), or is too short to
    carry an identity. The caller then falls back to the work description or the address rather
    than publishing the note. Deliberately conservative: real prose and permit-number-shaped names
    are never classified as notes.
    """
    tokens = [t.strip(".,;:-/()") for t in text.split()]
    if not tokens:
        return True
    if any(token.lower() in _NOTE_WORDS for token in tokens):
        return True
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 4:
        return True
    return False


def _strip_reference_clauses(text: str) -> str:
    """Drop a leading permit cross-reference ("Refer to PB26-12945") from a title or clause.

    A work description sometimes opens with a cross-reference to a related permit rather than the
    scope, at the start or in the middle ("Dumpster Enclosure / Refer to PB26-12945 / ..."). Only
    the reference phrase is removed; scope text that follows it in the same clause ("Refer to
    PB26-13456 THE PROJECT CONSISTS OF ...") is kept.
    """
    parts = re.split(r"\s+/\s+", text)
    kept = []
    for part in parts:
        stripped = _LEADING_REF_RE.sub("", part, count=1).lstrip(" .,;:-/").strip()
        if stripped:
            kept.append(stripped)
    return " / ".join(kept) if len(kept) > 1 else (kept[0] if kept else "")


def _repair_run_together_figure(text: str) -> str:
    """Split a figure that a source ran into the preceding word ("is16,297" -> "is 16,297").

    Only a letter directly followed by a digit is touched, so an identifier such as a permit
    number ("PB25-09094") and a capacity ("240V") are never split — those contain a digit but no
    letter-digit boundary that reads as two words.
    """
    return _LETTER_DIGIT_RE.sub(r"\1 \2", text)


def _fallback_title(project: Mapping[str, Any] | None) -> str:
    """A headline for a record whose stored name is an internal note rather than an identity.

    Prefers a usable work description, then the permit type, then the address. Never invents a
    value: every branch reads a field the record actually carries, and an empty result is returned
    so the caller can fall back further.
    """
    if not project:
        return ""
    description = str(project.get("work_description") or "").strip()
    if description:
        cleaned = _repair_run_together_figure(_collapse_whitespace(description))
        if not _looks_like_a_note(cleaned):
            return cleaned
    permit_type = str(project.get("permit_type") or "").strip()
    if permit_type:
        return _repair_run_together_figure(_collapse_whitespace(permit_type))
    return ""


def clean_title(value: Any, project: Mapping[str, Any] | None = None) -> str:
    """Produce a clean headline from a raw project name for the display layer only.

    Applies, in order: whitespace collapse and title-casing (:func:`titlecase`), removal of a
    leading meeting-note marker and of repeated-punctuation artifacts, repair of a figure run into
    the preceding word, removal of a leading permit cross-reference clause, and reduction of an
    over-long prose description to its first sentence. If the result still reads as a note, the
    fallback is the work description, the permit type or the address (from ``project``), then
    "New commercial construction at <address>". The stored value is never changed; this runs at
    render time.

    Returns an empty string for a missing value so a template can fall back to the address.
    """
    text = titlecase(value)
    if text:
        text = _collapse_repeated_punctuation(text)
        text = _strip_title_prefix(text)
        text = _strip_reference_clauses(text)
        text = _repair_run_together_figure(text)
        text = _summarise_verbose_title(text)
        # A separator left dangling at the end ("... Avenue -") reads as an unfinished headline.
        text = re.sub(r"\s*[-–—:;,/]\s*$", "", text).strip()

    if text and not _looks_like_a_note(text):
        return text

    fallback = _fallback_title(project)
    if fallback:
        return fallback
    if project:
        address = str(project.get("address") or "").strip()
        if address:
            return f"New commercial construction at {address}"
    return ""


def domain_of(value: Any) -> str:
    """The registrable-ish host of a URL for display ("https://x.gov/a" -> "x.gov").

    The dossier shows the source's domain next to an "Open source record" link rather than a long
    raw URL that overflows the column. Returns an empty string when no host can be determined, so
    a template can fall back rather than print a broken label.
    """
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


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


def pluralise(count: Any, singular: str, plural: str | None = None) -> str:
    """A counted noun with the verb agreed to it: "1 city", "3 cities".

    One place owns number agreement so a template cannot print "1 cities" or a stray
    "city(s)". ``plural`` defaults to ``singular + "s"``; pass it for irregular nouns
    ("country" -> "countries"). ``count`` may be a string from a template context.
    """
    try:
        n = int(count)
    except (TypeError, ValueError):
        return singular
    word = singular if n == 1 else (plural if plural is not None else singular + "s")
    return f"{n} {word}"


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
