"""Verdicts from the page: applied only to the exact behaviour a person saw.

Everything here works on temporary copies. No file under ``examples/`` is ever changed.
"""

import difflib
import json
import shutil
from pathlib import Path

import pytest

from gao_idl.authoring import Card, load_cards, load_spec, run_card, save_cards
from gao_idl.cli import main
from gao_idl.review import (
    Verdicts,
    apply_verdicts,
    load_verdicts,
    make_bundle,
    outcome_hash,
    save_spec,
)
from tests.authoring import envs
from tests.authoring.test_cards import _tie
from tests.review.test_bundle import tiny_auction_spec
from tests.test_cli import _cli

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "examples" / "specs" / "auction.json"
CARDS = ROOT / "examples" / "cards" / "auction.json"


def _verdicts(spec_title: str, cards: list[dict[str, str]], **extra: object) -> Verdicts:
    return Verdicts.model_validate(
        {
            "bundle_version": 1,
            "spec_title": spec_title,
            "reviewer": "Ann Example",
            "cards": cards,
            "assumptions": extra.get("assumptions", []),
        }
    )


def _hashes() -> dict[str, str]:
    bundle = make_bundle(load_spec(SPEC), load_cards(CARDS))
    return {v.card.id: v.outcome_hash for v in bundle.cards}


def test_two_confirmed_and_one_rejected_import_cleanly_then_verify_fails_on_the_rejection(
    tmp_path: Path,
) -> None:
    cards_path = tmp_path / "cards.json"
    shutil.copy(CARDS, cards_path)
    hashes = _hashes()
    verdicts = _verdicts(
        load_spec(SPEC).title,
        [
            {"id": "truthful", "verdict": "confirmed", "outcome_hash": hashes["truthful"]},
            {"id": "tie-top", "verdict": "confirmed", "outcome_hash": hashes["tie-top"]},
            {
                "id": "no-bid",
                "verdict": "rejected",
                "note": "a missing bid should count as zero",
                "outcome_hash": hashes["no-bid"],
            },
        ],
    )
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(verdicts.model_dump_json(indent=1))
    done = _cli("cards", "import", str(cards_path), str(verdicts_path), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "3 cards updated from 3 verdicts by Ann Example" in done.stdout
    cards = {c.id: c for c in load_cards(cards_path)}
    assert [cards[i].status for i in ("truthful", "tie-top", "no-bid")] == [
        "confirmed",
        "confirmed",
        "rejected",
    ]
    assert cards["no-bid"].note == "a missing bid should count as zero"
    assert {cards[i].reviewer for i in ("truthful", "tie-top", "no-bid")} == {"Ann Example"}
    assert sum(c.status == "proposed" for c in cards.values()) == 10
    assert all(c.reviewer == "" for c in cards.values() if c.status == "proposed")
    verify = _cli("cards", "verify", str(cards_path), cwd=tmp_path)
    assert verify.returncode == 1
    problems = verify.stderr.strip().splitlines()
    assert problems == ["no-bid: rejected outcome is still what the environment does"]
    assert all(c.status == "proposed" for c in load_cards(CARDS))  # the shipped file is untouched


def test_a_changed_tie_break_makes_a_matching_file_import_with_a_stale_outcome_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    card = _tie()
    cards_path = tmp_path / "cards.json"
    save_cards(cards_path, [card])
    spec = tiny_auction_spec()
    seen = make_bundle(spec, [card]).cards[0].outcome_hash
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        _verdicts(
            spec.title, [{"id": "tie", "verdict": "confirmed", "outcome_hash": seen}]
        ).model_dump_json()
    )
    monkeypatch.setattr(envs, "TIE", "last")  # the environment now breaks ties the other way
    assert main(["cards", "import", str(cards_path), str(verdicts_path)]) == 1
    err = capsys.readouterr().err
    assert "tie" in err and "stale outcome" in err
    (after,) = load_cards(cards_path)
    assert after == card  # left alone
    monkeypatch.setattr(envs, "TIE", "first")
    assert main(["cards", "import", str(cards_path), str(verdicts_path)]) == 0
    (after,) = load_cards(cards_path)
    assert after.status == "confirmed" and after.expect == run_card(card)  # frozen on import


def test_refusals_are_listed_and_leave_cards_alone() -> None:
    cards = load_cards(CARDS)
    title = load_spec(SPEC).title
    h = _hashes()
    verdicts = _verdicts(
        title,
        [
            {"id": "no-such", "verdict": "confirmed", "outcome_hash": "0" * 64},
            {"id": "truthful", "verdict": "rejected", "note": "  ", "outcome_hash": h["truthful"]},
            {"id": "tie-top", "verdict": "confirmed", "outcome_hash": "f" * 64},
            {"id": "overbid", "verdict": "confirmed", "outcome_hash": h["overbid"]},
            {"id": "overbid", "verdict": "rejected", "note": "x", "outcome_hash": h["overbid"]},
        ],
    )
    new, spec, problems = apply_verdicts(cards, load_spec(SPEC), verdicts)
    assert new == cards and spec == load_spec(SPEC)
    text = "\n".join(problems)
    assert len(problems) == 4
    assert "no-such: no such card" in text
    assert "truthful: rejected without a note" in text
    assert "tie-top: what the card does has changed" in text
    assert "overbid: more than one verdict" in text


def test_a_file_for_another_spec_is_refused_whole() -> None:
    cards = load_cards(CARDS)
    h = _hashes()
    verdicts = _verdicts(
        "Some other design",
        [{"id": "truthful", "verdict": "confirmed", "outcome_hash": h["truthful"]}],
    )
    new, _, problems = apply_verdicts(cards, load_spec(SPEC), verdicts)
    assert new == cards and len(problems) == 1 and "nothing applied" in problems[0]


def test_agreed_choices_are_confirmed_disputed_ones_listed(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    cards_path = tmp_path / "cards.json"
    shutil.copy(SPEC, spec_path)
    shutil.copy(CARDS, cards_path)
    spec = load_spec(spec_path)
    first, second = spec.assumptions[0], spec.assumptions[1]
    verdicts = _verdicts(
        spec.title,
        [],
        assumptions=[
            {"index": 0, "text": first.text, "verdict": "agreed"},
            {"index": 1, "text": second.text, "verdict": "disputed", "note": "count it as zero"},
            {"index": 2, "text": "edited since", "verdict": "agreed"},
        ],
    )
    path = tmp_path / "verdicts.json"
    path.write_text(verdicts.model_dump_json())
    done = _cli(
        "cards", "import", str(cards_path), str(path), "--spec", str(spec_path), cwd=tmp_path
    )
    assert done.returncode == 1  # the edited choice
    assert "choice 2: the spec has no such choice" in done.stderr
    assert f"choice 1 disputed: {second.text} Note: count it as zero" in done.stdout
    after = load_spec(spec_path)
    assert [a.confirmed for a in after.assumptions[:3]] == [True, False, False]
    diff = [
        line
        for line in difflib.unified_diff(
            SPEC.read_text().splitlines(), spec_path.read_text().splitlines(), lineterm="", n=0
        )
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    text = json.dumps(first.text)
    assert diff == [f'-   "text": {text}', f'+   "text": {text},', '+   "confirmed": true']
    assert load_cards(cards_path) == load_cards(CARDS)  # no card verdicts: cards untouched
    assert all(not a.confirmed for a in load_spec(SPEC).assumptions)  # shipped spec untouched


def test_without_a_spec_choices_are_not_applied(tmp_path: Path) -> None:
    cards_path = tmp_path / "cards.json"
    shutil.copy(CARDS, cards_path)
    spec = load_spec(SPEC)
    verdicts = _verdicts(
        spec.title,
        [],
        assumptions=[{"index": 0, "text": spec.assumptions[0].text, "verdict": "agreed"}],
    )
    path = tmp_path / "verdicts.json"
    path.write_text(verdicts.model_dump_json())
    assert load_verdicts(path) == verdicts
    done = _cli("cards", "import", str(cards_path), str(path), cwd=tmp_path)
    assert done.returncode == 1 and "no --spec given" in done.stderr


def test_a_stale_card_can_be_rejected_but_not_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    from gao_idl.authoring import freeze

    card: Card = freeze(_tie())
    spec = tiny_auction_spec()
    monkeypatch.setattr(envs, "TIE", "last")
    now = outcome_hash(run_card(card))  # the page shows what the card does now
    yes = _verdicts(spec.title, [{"id": "tie", "verdict": "confirmed", "outcome_hash": now}])
    new, _, problems = apply_verdicts([card], spec, yes)
    assert new == [card] and "cannot be confirmed" in problems[0]
    no = _verdicts(
        spec.title,
        [{"id": "tie", "verdict": "rejected", "note": "alice wins", "outcome_hash": now}],
    )
    (rejected,), _, problems = apply_verdicts([card], spec, no)
    assert problems == [] and rejected.status == "rejected"
    assert rejected.expect == run_card(card)  # the rejected behaviour is the one recorded


def test_save_spec_keeps_the_shipped_layout(tmp_path: Path) -> None:
    for name in ("auction", "pd", "vcm"):
        source = ROOT / "examples" / "specs" / f"{name}.json"
        out = tmp_path / f"{name}.json"
        save_spec(out, load_spec(source))
        assert out.read_text() == source.read_text()
