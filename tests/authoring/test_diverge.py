"""Divergence search finds seeded ambiguities, and only those."""

from pathlib import Path

import pytest

from gao_idl.authoring import (
    diverge,
    edge_scripts,
    load_choices,
    render_choice,
    save_choices,
    to_choice_cards,
)
from tests.authoring.envs import AUCTION_A, AUCTION_A2, AUCTION_B, AUCTION_C, AUCTION_D

PARAMS = {"values": {"alice": 10.0, "bob": 7.0, "carol": 4.0}, "reserve": 2.0}


def _scripts() -> list[dict[str, list[dict[str, float] | None]]]:
    return edge_scripts(AUCTION_A, PARAMS, 11, 200)  # type: ignore[return-value]


def test_identical_candidates_never_diverge() -> None:
    assert diverge([AUCTION_A, AUCTION_A2], PARAMS, [11], _scripts()) == []


def test_seeded_ambiguities_are_found_and_nothing_else() -> None:
    found = diverge([AUCTION_A, AUCTION_B, AUCTION_C], PARAMS, [11], _scripts())
    groupings = {tuple(tuple(g) for g in d.groups) for d in found}
    tie = ((AUCTION_A, AUCTION_C), (AUCTION_B,))
    reserve = ((AUCTION_A, AUCTION_B), (AUCTION_C,))
    both = ((AUCTION_A,), (AUCTION_B,), (AUCTION_C,))
    assert {tie, reserve} <= groupings <= {tie, reserve, both}
    for d in found:
        bids = [e[0]["amount"] for e in d.script.values() if e[0] is not None]
        groups = tuple(tuple(g) for g in d.groups)
        top = max(bids)
        if groups in (tie, both):  # only a tie at the top can separate the tie-breaks
            assert bids.count(top) >= 2 and top >= 2.0
        if groups in (reserve, both):  # only a runner-up below the reserve separates the rules
            assert top >= 2.0 and sorted(bids)[-2] < 2.0
        assert d.kind == "utilities" and "payoff of" in d.first_difference


def test_pairs_find_exactly_their_own_ambiguity() -> None:
    (tie,) = diverge([AUCTION_A, AUCTION_B], PARAMS, [11], _scripts())
    (reserve,) = diverge([AUCTION_A, AUCTION_C], PARAMS, [11], _scripts())
    assert tie.groups == [[AUCTION_A], [AUCTION_B]]
    assert reserve.found[AUCTION_A] is not None and reserve.found[AUCTION_C] is not None
    assert reserve.found[AUCTION_A].utilities != reserve.found[AUCTION_C].utilities


def test_a_candidate_with_another_interface_is_reported_once() -> None:
    found = diverge([AUCTION_A, AUCTION_D], PARAMS, [11], _scripts())
    assert [d.kind for d in found] == ["interface"]
    assert found[0].found[AUCTION_D] is None and "SchemaError" in found[0].problems[AUCTION_D]


def test_needs_two_distinct_candidates() -> None:
    with pytest.raises(ValueError, match="two distinct"):
        diverge([AUCTION_A, AUCTION_A], PARAMS, [11], [])


def test_choice_cards_ask_without_naming_the_implementations(tmp_path: Path) -> None:
    found = diverge([AUCTION_A, AUCTION_B, AUCTION_C], PARAMS, [11], _scripts())
    cards = to_choice_cards(found)
    assert len(cards) == len(found) and all(len(c.options) >= 2 for c in cards)
    text = "\n".join(render_choice(c) for c in cards)
    assert "Which did you mean?" in text and "**Option A.**" in text and "**Option B.**" in text
    assert "alice: chooses amount = " in text and "ends with a payoff of" in text
    first = render_choice(cards[0])
    shared = first.split("**The same in every option.**")[1].split("**Option A.**")[0]
    option_a = first.split("**Option A.**")[1].split("**Option B.**")[0]
    assert shared.strip() and option_a.strip()
    assert not set(shared.strip().splitlines()) & set(option_a.strip().splitlines())
    assert "tests.authoring" not in text and "build_auction" not in text
    assert "Not yet answered." in text
    path = tmp_path / "choices.json"
    save_choices(path, cards)
    assert load_choices(path) == cards
    assert render_choice(cards[0]) == render_choice(load_choices(path)[0])
