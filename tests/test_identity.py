"""Deterministic entity-resolution tests.

These exercise the pure resolution functions. The property under test throughout is that a
merge happens only on a deterministic signal and that everything else stays separate, because a
false merge is worse than a duplicate.
"""

from __future__ import annotations

from oppintel.identity import (
    ENTITY_COMPANY,
    ENTITY_PERSON,
    classify_entity_type,
    entity_key,
    normalize_entity_name,
    resolve_name,
    select_display_name,
)


def test_legal_form_and_punctuation_fold_to_one_key():
    """Spellings that differ only by legal form, case and punctuation resolve to one key."""
    keys = {
        entity_key("ACME HEALTH LLC"),
        entity_key("Acme Health, L.L.C."),
        entity_key("acme health inc"),
        entity_key("ACME HEALTH CO."),
    }
    assert len(keys) == 1
    assert None not in keys


def test_ampersand_and_and_are_equivalent():
    assert entity_key("Turner & Co") == entity_key("Turner and Co")


def test_distinguishing_words_do_not_merge():
    """A different word is a different entity, even when one name contains the other."""
    assert entity_key("ACME HEALTH") != entity_key("ACME HEALTH PARTNERS")
    assert entity_key("Turner Construction") != entity_key("Turner Construction Group")


def test_similar_but_different_names_stay_separate():
    """No fuzzy matching: near-identical spellings with a distinguishing word stay apart."""
    assert entity_key("Shelton Landmark") != entity_key("Shelton Landmark Foundation")
    assert entity_key("Dallas Mechanical") != entity_key("Dallas Mechanical Services")


def test_a_legal_form_alone_does_merge():
    """The converse: a difference that is only a legal form is not a different entity."""
    assert entity_key("Dallas Medical Center") == entity_key("Dallas Medical Center Inc")


def test_company_and_person_never_collide():
    company = entity_key("SMITH CONSTRUCTION LLC", entity_type=ENTITY_COMPANY)
    person = entity_key("SMITH CONSTRUCTION LLC", entity_type=ENTITY_PERSON)
    assert company != person


def test_last_first_person_order_is_normalized():
    """A "LAST, FIRST" name and the natural order key alike."""
    assert entity_key("Smith, John") == entity_key("John Smith", entity_type=ENTITY_PERSON)


def test_classify_entity_type():
    assert classify_entity_type("Acme Construction LLC") == ENTITY_COMPANY
    assert classify_entity_type("Smith, John") == ENTITY_PERSON
    # A bare name with no legal form defaults to company, the safer default.
    assert classify_entity_type("Ross Avenue Partners") == ENTITY_COMPANY


def test_unusable_names_resolve_to_none():
    assert entity_key("") is None
    assert entity_key("   ") is None
    assert entity_key("N/A") is None
    assert resolve_name("x") is None


def test_leading_article_is_dropped():
    assert entity_key("The Shelton Foundation") == entity_key("Shelton Foundation")


def test_select_display_name_prefers_the_most_complete_spelling():
    assert select_display_name(["Turner", "Turner Construction", "Turner Construction Company"]) == (
        "Turner Construction Company"
    )
    # Deterministic tie-break (lexicographic), not insertion order.
    assert select_display_name(["Beta Co", "Alpha Co"]) == "Alpha Co"


def test_normalize_entity_name_removes_forms_and_punctuation():
    assert normalize_entity_name("ACME HEALTH, L.L.C.") == "acme health"
