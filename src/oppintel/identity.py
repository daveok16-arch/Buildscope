"""Deterministic entity resolution.

The rule this module enforces is deliberately narrow: two names are the *same entity* only
when they reduce to the same canonical key under a fixed, explainable normalization. Nothing
is merged because two names merely look similar. A false merge presents two real companies as
one and is far more damaging than leaving a duplicate on the books, so the tie always breaks
toward keeping entities separate.

The normalization is deterministic and reversible in intent: it folds the differences a
source itself introduces (case, punctuation, a trailing legal form, the ampersand that
"Turner & Co" and "Turner and Co" spell differently) while leaving every distinguishing word
in place. "ACME HEALTH LLC" and "Acme Health, L.L.C." merge; "ACME HEALTH" and "ACME HEALTH
PARTNERS" do not, because "PARTNERS" is a distinguishing word, not a legal form.

There is no fuzzy matching, no edit distance, and no similarity threshold. Adding one would
be a deliberate future decision with its own review, not a default.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

#: Entity kinds. Only two are modelled for now; the key includes the type, so a company and a
#: person that happen to share a name can never collide.
ENTITY_COMPANY = "company"
ENTITY_PERSON = "person"

#: Legal-form suffixes dropped from a company name before it is keyed. Dropping these is safe
#: because they describe the legal wrapper, not the identity: "Turner Construction Co." and
#: "Turner Construction, Inc." are the same builder. A word that carries identity (GROUP,
#: PARTNERS, HOLDINGS, SERVICES, DEVELOPMENT) is deliberately NOT in this list.
_LEGAL_SUFFIXES = frozenset(
    {
        "llc", "lllp", "llp", "lp", "ltd", "ltda", "inc", "incorporated", "corp",
        "corporation", "co", "company", "plc", "pllc", "pc", "pa", "l l c", "l l p",
        "limited", "limited liability company", "incorporated company",
    }
)

#: Leading articles and honorifics dropped before keying. "The Shelton Landmark Foundation"
#: and "Shelton Landmark Foundation" are the same entity.
_LEADING_NOISE = frozenset({"the"})

#: A name written "LAST, FIRST" (a person) or "LAST, FIRST MIDDLE". Two comma-separated parts
#: where the first is a single token is the shape a county appraisal district uses for people.
_PERSON_LAST_FIRST = re.compile(r"^[A-Za-z'\-]+\s*,\s*[A-Za-z'\-]+(\s+[A-Za-z'\-]+)*$")

#: A trailing individual designator: "SMITH JOHN DBA ...", "JOHN SMITH ESTATE OF".
_ESTATE_MARKERS = ("estate of", "estate", "et al", "etux", "et ux", "et vir")

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class ResolvedName:
    """The deterministic reduction of one observed name."""

    raw: str
    normalized: str
    canonical_key: str
    entity_type: str


def _clean(raw: str) -> str:
    return html.unescape(str(raw)).strip()


def _tokens(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens, with the ampersand folded to "and"."""
    folded = text.lower().replace("&", " and ")
    return _WORD.findall(folded)


#: The legal suffixes above, pre-tokenized, so a multi-word form ("l l c", "limited liability
#: company") is matched as a trailing token sequence rather than a single token.
_LEGAL_SUFFIX_TOKENS = tuple(
    sorted((tuple(s.split()) for s in _LEGAL_SUFFIXES), key=len, reverse=True)
)

#: Names that are noise rather than an entity. A permit field sometimes carries one of these
#: where a name belongs; they must never become an entity.
_NOISE_NAMES = frozenset(
    {"n a", "na", "none", "null", "unknown", "not applicable", "-", "--", "n/a"}
)


def _strip_legal_forms(tokens: list[str]) -> list[str]:
    """Remove a trailing legal form and leading articles from a token list.

    Only a *trailing* legal form is removed, so a company genuinely named "Company Store"
    keeps its first word. The removal repeats so "Acme Holdings Co. Inc." reduces cleanly, and
    a multi-word form such as "L.L.C." is matched as a whole sequence.
    """
    result = list(tokens)
    while result and result[0] in _LEADING_NOISE:
        result.pop(0)
    changed = True
    while changed and result:
        changed = False
        for suffix in _LEGAL_SUFFIX_TOKENS:
            n = len(suffix)
            if n <= len(result) and tuple(result[-n:]) == suffix:
                del result[-n:]
                changed = True
                break
    return result


def classify_entity_type(raw: str) -> str:
    """Whether a name denotes a company or a person.

    A "LAST, FIRST" shape is a person. A name carrying a legal form is a company. Everything
    else defaults to company, which is the common case on a permit record and the safer
    default: a company wrongly typed as a person is easier to correct than the reverse.
    """
    name = _clean(raw)
    if _PERSON_LAST_FIRST.match(name):
        return ENTITY_PERSON
    tokens = _tokens(name)
    if tokens and tokens[-1] in _LEGAL_SUFFIXES:
        return ENTITY_COMPANY
    return ENTITY_COMPANY


def normalize_entity_name(raw: str) -> str:
    """The display-oriented normalization: case and punctuation folded, forms removed."""
    tokens = _strip_legal_forms(_tokens(_clean(raw)))
    return " ".join(tokens)


def entity_key(raw: str, *, entity_type: str | None = None) -> str | None:
    """The deterministic merge identity for a name.

    Returns None when the name reduces to nothing (a sentinel such as "N/A" or a single
    character), so a noise value can never become an entity.
    """
    name = _clean(raw)
    if not name:
        return None
    if name.lower() in _NOISE_NAMES:
        return None
    kind = entity_type or classify_entity_type(name)

    if kind == ENTITY_PERSON:
        # For a person, reorder "LAST, FIRST" to "first last" so both orderings key alike.
        parts = [p.strip() for p in name.split(",", 1)]
        tokens = _tokens(" ".join(reversed(parts))) if len(parts) == 2 else _tokens(name)
        tokens = _strip_legal_forms(tokens)
    else:
        tokens = _strip_legal_forms(_tokens(name))

    if not tokens:
        return None
    joined = " ".join(tokens)
    # A single token of two or fewer characters is not an identifiable entity.
    if len(tokens) == 1 and len(joined) <= 2:
        return None
    return f"{kind}|{joined}"


def resolve_name(raw: str) -> ResolvedName | None:
    """Reduce one observed name to its canonical form, or None when it is not usable."""
    key = entity_key(raw)
    if key is None:
        return None
    kind, _, canonical = key.partition("|")
    return ResolvedName(
        raw=_clean(raw),
        normalized=normalize_entity_name(raw),
        canonical_key=key,
        entity_type=kind,
    )


def select_display_name(candidates: list[str]) -> str:
    """Choose the most complete observed spelling for an entity.

    The longest spelling wins, because it carries the most information ("Turner Construction
    Company" over "Turner"). Ties break lexicographically so the choice is deterministic
    across runs rather than dependent on insertion order.
    """
    cleaned = [_clean(c) for c in candidates if c and _clean(c)]
    if not cleaned:
        return ""
    return sorted(cleaned, key=lambda n: (-len(n), n))[0]


def is_probable_person(raw: str) -> bool:
    """Whether a name looks like a natural person, for the person-entity pass."""
    name = _clean(raw).lower()
    if any(marker in name for marker in _ESTATE_MARKERS):
        return True
    return classify_entity_type(raw) == ENTITY_PERSON
