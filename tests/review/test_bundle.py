"""The bundle: builds for the shipped environments, round-trips, is byte-stable, hashes portably."""

import json
from pathlib import Path

import pytest

from gao_idl.authoring import CardExpect, EnvSpec, freeze, load_cards, load_spec, run_card
from gao_idl.review import Bundle, dump_bundle, load_bundle, make_bundle, outcome_hash
from tests.authoring import envs
from tests.authoring.envs import TINY_AUCTION
from tests.test_cli import _cli

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "examples" / "specs"
CARDS = ROOT / "examples" / "cards"


@pytest.mark.parametrize("name", ["auction", "pd", "vcm"])
def test_shipped_bundles_build_round_trip_and_are_byte_identical(name: str) -> None:
    spec = load_spec(SPECS / f"{name}.json")
    cards = load_cards(CARDS / f"{name}.json")
    bundle = make_bundle(spec, cards)
    text = dump_bundle(bundle)
    assert Bundle.model_validate_json(text) == bundle
    assert dump_bundle(Bundle.model_validate(json.loads(text))) == text
    assert dump_bundle(make_bundle(spec, cards)) == text
    assert bundle.bundle_version == 1 and bundle.made_with
    assert [v.card.id for v in bundle.cards] == [c.id for c in cards]
    assert not any(v.stale for v in bundle.cards)
    assert all(
        v.outcome_hash == outcome_hash(c.expect) for v, c in zip(bundle.cards, cards, strict=True)
    )  # type: ignore[arg-type]
    status = bundle.status
    assert status.runs == len(cards) and status.fuzzed == 0
    assert status.cards == {"proposed": len(cards), "confirmed": 0, "rejected": 0}
    assert status.assumptions_open == sum(not a.confirmed for a in spec.assumptions)
    assert status.unchecked_invariants == [
        i.statement for i in spec.invariants if i.predicate is None
    ]
    for word in ("timestamp", "created", "generated_at"):
        assert word not in text


def test_fuzzed_runs_are_counted_and_checked() -> None:
    spec = load_spec(SPECS / "auction.json")
    cards = load_cards(CARDS / "auction.json")[:2]
    bundle = make_bundle(spec, cards, fuzz=5, seed=3)
    assert bundle.status.runs == 7 and bundle.status.fuzzed == 5
    assert dump_bundle(make_bundle(spec, cards, fuzz=5, seed=3)) == dump_bundle(bundle)


def test_outcome_hash_is_pinned_for_a_carbon_market_card() -> None:
    # The card whose utilities differed in the last bits between Python 3.11 and 3.13 in M8.
    # CI runs this on both versions; the 9-place rounding is what makes them agree.
    card = next(c for c in load_cards(CARDS / "vcm.json") if c.id == "buyer-stays-out")
    pinned = "981598a1c24741439dd6825a925b95a12a3d4b155c89651f3d3031b9c45c30b5"
    assert outcome_hash(run_card(card)) == pinned


def test_outcome_hash_ignores_float_noise_and_key_order_but_not_changes() -> None:
    a = CardExpect(exit_port="done", failed=False, utilities={"x": 0.1 + 0.2, "y": 1.0}, reports={})
    b = CardExpect(exit_port="done", failed=False, utilities={"y": 1.0, "x": 0.3}, reports={})
    c = CardExpect(exit_port="done", failed=False, utilities={"x": 0.31, "y": 1.0}, reports={})
    z = CardExpect(exit_port="done", failed=False, utilities={"x": -0.0, "y": 1.0}, reports={})
    o = CardExpect(exit_port="done", failed=False, utilities={"x": 0.0, "y": 1.0}, reports={})
    assert outcome_hash(a) == outcome_hash(b) != outcome_hash(c)
    assert outcome_hash(z) == outcome_hash(o)
    assert len(outcome_hash(a)) == 64


def test_a_card_whose_behaviour_changed_is_stale_and_hashed_as_it_is_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.authoring.test_cards import _tie

    spec = tiny_auction_spec()
    card = freeze(_tie())
    before = make_bundle(spec, [card]).cards[0]
    assert not before.stale
    monkeypatch.setattr(envs, "TIE", "last")
    after = make_bundle(spec, [card]).cards[0]
    assert after.stale and after.outcome_hash != before.outcome_hash
    assert after.outcome_hash == outcome_hash(run_card(card))


def tiny_auction_spec() -> EnvSpec:
    """A spec for the tiny first-price auction of ``tests.authoring.envs``."""
    return EnvSpec.model_validate(
        {
            "title": "Tiny auction",
            "summary": "Bidders bid once; the highest bid wins and pays its bid.",
            "env_ref": TINY_AUCTION,
            "params": {"values": {"alice": 9.0, "bob": 9.0}},
            "roles": [{"name": "bidder", "description": "bids once", "many": True}],
            "steps": [
                {
                    "name": "bidding",
                    "kind": "decision",
                    "description": "Everyone bids at once.",
                    "chooses": {"bidder": ["amount"]},
                    "told": {"bidder": ["price", "winner"]},
                    "on_no_action": "fail",
                }
            ],
        }
    )


def test_a_card_for_another_environment_is_refused() -> None:
    spec = load_spec(SPECS / "pd.json")
    with pytest.raises(ValueError, match="is for"):
        make_bundle(spec, load_cards(CARDS / "auction.json")[:1])


def test_cli_review_bundle(tmp_path: Path) -> None:
    out = tmp_path / "bundle.json"
    args = ["review", "bundle", str(SPECS / "pd.json"), "--cards", str(CARDS / "pd.json")]
    done = _cli(*args, "--out", str(out), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "11 cards" in done.stdout
    assert load_bundle(out).status.runs == 11
    bad = _cli(
        "review", "bundle", str(tmp_path / "nope.json"), "--cards", "x", "--out", "y", cwd=tmp_path
    )
    assert bad.returncode == 2
