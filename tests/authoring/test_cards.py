"""Cards: freezing, confirming, rejecting, rendering, and the shipped card files."""

from pathlib import Path

import pytest

from gao_idl.authoring import (
    Card,
    freeze,
    load_cards,
    render_card,
    run_card,
    save_cards,
    verify_cards,
)
from gao_idl.testing import assert_cards
from tests.authoring import envs
from tests.authoring.envs import TINY_AUCTION, ULTIMATUM, ultimatum_spec

ROOT = Path(__file__).resolve().parents[2]


def _tie() -> Card:
    return Card(
        id="tie",
        title="Two equal top bids",
        why="Separates tie-breaking by name from any other rule.",
        env_ref=TINY_AUCTION,
        params={"values": {"alice": 9.0, "bob": 9.0}},
        seed=0,
        script={"alice": [{"amount": 5.0}], "bob": [{"amount": 5.0}]},
    )


def test_run_card_records_ending_payoffs_and_reports() -> None:
    got = run_card(_tie())
    assert got.exit_port == "done" and not got.failed
    assert got.utilities == {"alice": 4.0, "bob": 0.0}
    assert got.reports["bob"] == [{"winner": "alice", "price": 5.0}]


def test_a_confirmed_card_fails_when_the_behaviour_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = freeze(_tie()).model_copy(update={"status": "confirmed"})
    assert verify_cards([card]) == []
    monkeypatch.setattr(envs, "TIE", "last")
    (problem,) = verify_cards([card])
    assert "tie" in problem and "no longer reproduced" in problem


def test_a_rejected_card_fails_until_the_behaviour_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = freeze(_tie()).model_copy(update={"status": "rejected", "note": "bob should win"})
    (problem,) = verify_cards([card])
    assert "still" in problem
    monkeypatch.setattr(envs, "TIE", "last")
    assert verify_cards([card]) == []


def test_proposed_cards_are_ignored_and_ids_are_unique() -> None:
    card = _tie()
    assert verify_cards([card]) == []
    assert verify_cards([card, card]) == ["card id 'tie' is used more than once"]
    unfrozen = card.model_copy(update={"status": "confirmed"})
    assert verify_cards([unfrozen]) == ["tie: confirmed but has no recorded outcome"]


def test_round_trip_and_assert_cards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "cards.json"
    cards = [freeze(_tie()).model_copy(update={"status": "confirmed"})]
    save_cards(path, cards)
    assert load_cards(path) == cards
    assert_cards(path)
    monkeypatch.setattr(envs, "TIE", "last")
    with pytest.raises(AssertionError, match="tie"):
        assert_cards(path)


def test_render_card_tells_the_story_in_plain_words() -> None:
    card = Card(
        id="silent",
        title="The responder says nothing",
        why="Separates silence-as-rejection from silence-as-acceptance.",
        env_ref=ULTIMATUM,
        params={"pie": 10.0, "cost": 1.0},
        seed=0,
        script={"ann": [{"offer": 4.0}], "ben": [None]},
    )
    text = render_card(freeze(card), ultimatum_spec())
    assert "ann chooses offer = 4." in text
    assert "ben does not act and is left out of this step." in text
    assert "The proposer names the responder's share." in text
    assert "- ann, after step response: accepted = no, own_payoff = 0" in text
    assert "the run ends through `done`" in text
    assert render_card(freeze(card), ultimatum_spec()) == text
    for jargon in ("DecisionRequest", "decision_id", "Primitive", "#"):
        assert jargon not in text.replace("###", "")


def test_render_card_flags_a_stale_recorded_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    card = freeze(_tie())
    monkeypatch.setattr(envs, "TIE", "last")
    assert "the recorded outcome is not what happens now" in render_card(card)


@pytest.mark.parametrize("name", ["auction", "pd", "vcm"])
def test_shipped_cards_are_proposed_frozen_and_current(name: str) -> None:
    cards = load_cards(ROOT / "examples" / "cards" / f"{name}.json")
    assert 10 <= len(cards) <= 15
    assert len({c.id for c in cards}) == len(cards)
    for card in cards:
        assert card.status == "proposed", "only a person confirms or rejects a card"
        assert card.why and card.expect is not None
        assert run_card(card) == card.expect, card.id
