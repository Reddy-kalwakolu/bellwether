# tests/bellwether/context/retrieval/test_tokenize.py
"""The tokenizer that makes hybrid retrieval work — originals kept, parts added."""

from __future__ import annotations

from bellwether.context.retrieval.tokenize import tokenize


def test_snake_case_keeps_the_whole_and_the_parts() -> None:
    tokens = tokenize("budget_micros")
    assert "budget_micros" in tokens
    assert "budget" in tokens
    assert "micros" in tokens


def test_camel_case_splits_and_keeps_the_whole() -> None:
    tokens = tokenize("AdDecisionService")
    assert "addecisionservice" in tokens
    assert "ad" in tokens
    assert "decision" in tokens
    assert "service" in tokens


def test_route_splits_on_slash_and_hyphen() -> None:
    tokens = tokenize("POST /ad-request")
    assert "post" in tokens
    assert "ad-request" in tokens
    assert "ad" in tokens
    assert "request" in tokens


def test_stopwords_are_dropped() -> None:
    assert "the" not in tokenize("the budget")
    assert "budget" in tokenize("the budget")


def test_single_characters_are_dropped() -> None:
    assert tokenize("a b budget") == ["budget"]


def test_is_deterministic() -> None:
    assert tokenize("BudgetMicros enforced") == tokenize("BudgetMicros enforced")


def test_a_leading_underscore_name_keeps_its_whole_form() -> None:
    # The corpus ingests Python source, where `__init__`, `__main__` and private
    # helpers are everywhere. Matching from the first alphanumeric character would
    # silently reduce these to "init" and lose the identifier that was asked for.
    assert "__init__" in tokenize("__init__")
    assert "_keep" in tokenize("_keep")


def test_trailing_sentence_punctuation_is_still_stripped() -> None:
    assert "budget" in tokenize("the budget.")
    assert "budget." not in tokenize("the budget.")


def test_exact_token_contributes_more_frequency_than_its_parts() -> None:
    # The whole plus both parts: three tokens from one identifier. This is what
    # gives an exact-identifier query its edge over a merely-related chunk.
    assert len(tokenize("budget_micros")) == 3


def test_empty_text_is_empty() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []
