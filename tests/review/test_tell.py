"""Shared and own parts of what a step tells, and short labels for payloads."""

from pathlib import Path

from gao_idl.authoring import load_cards, tell_card
from gao_idl.authoring.cards import Told
from gao_idl.review import ToldRow, split_told, summarize

ROOT = Path(__file__).resolve().parents[2]


def _step(name: str, card_id: str, layer: str) -> list[Told]:
    card = next(
        c for c in load_cards(ROOT / "examples" / "cards" / f"{name}.json") if c.id == card_id
    )
    (step,) = [s for s in tell_card(card).steps if s.layer == layer]
    return step.told


def test_auction_settlement_has_one_everyone_row_and_three_own_rows() -> None:
    rows = split_told(_step("auction", "tie-top", "settlement"))
    everyone, *own = rows
    assert everyone.to == []
    assert summarize(everyone.payload) == "winner = alice, winning_payment = 8"
    assert [r.to for r in own] == [["alice"], ["bob"], ["carol"]]
    assert all("own_payment" in r.payload and "winner" not in r.payload for r in own)
    assert "own_payment = 8" in summarize(own[0].payload)


def test_carbon_market_audit_reveals_once() -> None:
    rows = split_told(_step("vcm", "audited", "audit"))
    assert rows[0].to == [] and "revealed" in rows[0].payload
    assert all("revealed" not in r.payload for r in rows[1:])
    told_to = {a for r in rows[1:] for a in r.to}
    assert not told_to & {"s0", "s1", "s2", "s3"}  # sellers have nothing of their own left


def test_a_single_recipient_gets_no_everyone_row() -> None:
    rows = split_told([Told(agent="ann", payload={"a": 1, "b": 2})])
    assert rows == [ToldRow(to=["ann"], payload={"a": 1, "b": 2})]
    assert split_told([]) == []


def test_identical_remainders_share_a_row_in_natural_order() -> None:
    told = [
        Told(agent="p10", payload={"pub": 1, "mine": 0}),
        Told(agent="p2", payload={"pub": 1, "mine": 0}),
        Told(agent="p1", payload={"pub": 1, "mine": 5}),
    ]
    rows = split_told(told)
    assert rows == [
        ToldRow(to=[], payload={"pub": 1}),
        ToldRow(to=["p1"], payload={"mine": 5}),
        ToldRow(to=["p2", "p10"], payload={"mine": 0}),
    ]


def test_an_empty_remainder_gives_no_row_and_all_identical_gives_one_row() -> None:
    told = [Told(agent="a", payload={"x": [1, 2]}), Told(agent="b", payload={"x": [1, 2]})]
    assert split_told(told) == [ToldRow(to=[], payload={"x": [1, 2]})]
    told = [Told(agent="a", payload={"x": 1, "y": 2}), Told(agent="b", payload={"x": 1})]
    assert split_told(told) == [
        ToldRow(to=[], payload={"x": 1}),
        ToldRow(to=["a"], payload={"y": 2}),
    ]
    # a field missing from one recipient is nobody's shared field
    told = [Told(agent="a", payload={"x": 1}), Told(agent="b", payload={"y": 1})]
    assert [r.to for r in split_told(told)] == [["a"], ["b"]]


def test_summarize_scalars_first_nested_by_size_numbers_as_cards_show_them() -> None:
    payload = {
        "projects": {"s0": {}, "s1": {}},
        "price": 8.0,
        "bids": [],
        "ok": True,
        "x": 0.1234567891,
    }
    expected = "price = 8, ok = yes, x = 0.123457, projects: 2 entries, bids: none"
    assert summarize(payload, limit=100) == expected
    assert summarize({"one": [5]}) == "one: 1 entry"
    assert summarize({}) == "nothing"
    assert "{" not in summarize({"deep": {"a": {"b": 1}}})


def test_summarize_writes_small_nested_values_out() -> None:
    assert summarize({"all_bids": {"alice": 8, "bob": 8.0}}) == "all_bids: (alice = 8, bob = 8)"
    order = {"s0": {"price": 8.84, "qty": 3}, "s1": None}
    assert summarize(order) == "s1 = none, s0: (price = 8.84, qty = 3)"
    table = {
        "round": 0,
        "revealed": {"s0": {"report": 0.9, "q": 0.4}, "s1": {"report": 0.5, "q": 0.5}},
    }
    assert summarize(table, limit=200) == (
        "round = 0, revealed.s0: (report = 0.9, q = 0.4), revealed.s1: (report = 0.5, q = 0.5)"
    )
    assert (
        summarize(table, limit=40) == "round = 0, revealed: 2 entries"
    )  # all of a table or its size
    five = {"fills": {f"s{i}": i for i in range(5)}}
    assert summarize(five) == "fills: 5 entries"


def test_summarize_cuts_on_a_pair_boundary() -> None:
    payload = {f"field{i}": i for i in range(20)}
    text = summarize(payload, limit=40)
    assert text.endswith(", …") and len(text) <= 40
    assert text == "field0 = 0, field1 = 1, field2 = 2, …"
    assert summarize({"a": 1, "b": 2}, limit=60) == "a = 1, b = 2"


def test_summarize_cuts_inside_a_pair_only_when_the_first_is_too_long() -> None:
    assert summarize({"note": "x" * 100}, limit=20) == "note = " + "x" * 12 + "…"
    text = summarize({"note": "x" * 50, "b": 1}, limit=58)
    assert text == "note = " + "x" * 50 + ", …"  # first pair kept whole
