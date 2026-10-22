"""Environment codecs: they read payloads only, and prompts contain only authorized information."""

import re
from pathlib import Path
from typing import Any

import pytest

import gao_idl.environments.auction.llm as auction_llm
import gao_idl.environments.pd.llm as pd_llm
from gao_idl.agents import ScriptedAgent
from gao_idl.core.ids import AgentId, ReportId
from gao_idl.core.schemas import ParticipantReport
from gao_idl.environments.auction.llm import AuctionCodec, AuctionReportRenderer
from gao_idl.environments.pd.llm import PDCodec, PDReportRenderer
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.codec import DefaultCodec
from gao_idl.llm.memory import FullTranscript
from gao_idl.llm.mock import MockBackend
from gao_idl.runtime.session import Session
from tests.llm.helpers import ALICE, AUCTION, BOB, PD, play

SENTINEL_ALICE, SENTINEL_BOB = 7777.5, 4242.25
FORBIDDEN = ("ledger", "store", "session", "outcome")


@pytest.mark.parametrize("module", [auction_llm, pd_llm])
def test_environment_codec_sources_never_mention_the_forbidden_internals(module: Any) -> None:
    source = Path(module.__file__).read_text().lower()
    assert [w for w in FORBIDDEN if w in source] == []


def test_environment_codecs_import_nothing_internal() -> None:
    for module in (auction_llm, pd_llm):
        imports = re.findall(r"^(?:from|import) (\S+)", Path(module.__file__).read_text(), re.M)
        assert all(
            m.startswith(("gao_idl.core.schemas", "gao_idl.llm", "json")) for m in imports
        ), imports


def auction_requests(**values: float) -> Any:
    session = Session.start(AUCTION, {"values": values}, 0)
    session.advance()
    return session


@pytest.mark.parametrize("codec", [DefaultCodec(), AuctionCodec()])
def test_default_codec_renders_only_authorized_fields(codec: DefaultCodec) -> None:
    session = auction_requests(alice=SENTINEL_ALICE, bob=SENTINEL_BOB)
    (request,) = session.pending_for(BOB)
    messages = codec.render(request, [], [])
    text = "\n".join(m.content for m in messages)
    assert str(SENTINEL_BOB) in text  # their own value is shown ...
    assert str(SENTINEL_ALICE) not in text and "7777" not in text  # ... alice's never is
    assert "alice" in text  # the observation does name the other bidder; nothing more


def test_nothing_of_the_other_bidders_value_reaches_any_prompt_in_a_whole_run() -> None:
    ma = MockBackend([{"amount": 7000.0}])
    mb = MockBackend([{"amount": 3000.0}])
    a = LLMAgent(
        ALICE, ma, AuctionCodec(), FullTranscript(), report_renderer=AuctionReportRenderer()
    )
    b = LLMAgent(BOB, mb, AuctionCodec(), FullTranscript(), report_renderer=AuctionReportRenderer())
    session = Session.start(
        AUCTION, {"values": {"alice": SENTINEL_ALICE, "bob": SENTINEL_BOB}, "reveal_bids": True}, 3
    )
    from gao_idl.runtime.runner import run
    from tests.aio import run_test

    run_test(run(session, {ALICE: a, BOB: b}))
    assert not session.failed
    seen_by_bob = "\n".join(m.content for c in mb.calls for m in c.messages)
    seen_by_alice = "\n".join(m.content for c in ma.calls for m in c.messages)
    assert "7777" not in seen_by_bob and "4242" not in seen_by_alice
    # what each agent remembers afterwards (the result report included) is just as clean
    mem_bob = "\n".join(m.content for m in b._memory.messages())
    assert "Result:" in mem_bob and "7777" not in mem_bob
    assert "All bids" in mem_bob and "7000" in mem_bob  # revealed bids are authorized information


def test_auction_prompt_contents() -> None:
    session = Session.start(
        AUCTION, {"values": {"alice": 9.5, "bob": 4.0}, "rule": "first_price"}, 0
    )
    session.advance()
    (request,) = session.pending_for(ALICE)
    system, task = (m.content for m in AuctionCodec().render(request, [], []))
    assert "alice" in system and "sealed-bid" in system
    assert "9.5" in task and "own bid" in task and "first_price" in task
    assert "Other bidders: bob" in task
    assert "only the winner and the price paid" in task
    assert '{"amount": number}' in task and "JSON only" in task
    assert "reserve" not in task  # not in the observation, so not in the prompt
    assert "utility" not in task and "payoff" not in task  # no invented payoff rule


def test_auction_report_renderer_sentences() -> None:
    def rep(payload: dict[str, Any]) -> ParticipantReport:
        return ParticipantReport(
            report_id=ReportId("auction/bidding@alice:0"),
            path=["auction"],
            layer="x",
            agent_id=AgentId("alice"),
            payload=payload,
        )

    r = AuctionReportRenderer()
    assert r.render_report(rep({"status": "bid accepted", "own_bid": 5.0})).content == (
        "Your bid of 5.0 was accepted."
    )
    won = r.render_report(
        rep(
            {
                "winner": "bob",
                "winning_payment": 4.0,
                "own_bid": 3.0,
                "own_payment": 0.0,
                "own_utility": 0.0,
            }
        )
    ).content
    assert "bob won and paid 4.0" in won and "All bids" not in won
    revealed = r.render_report(
        rep(
            {
                "winner": "bob",
                "winning_payment": 4.0,
                "own_bid": 3.0,
                "own_payment": 0.0,
                "own_utility": 0.0,
                "all_bids": {"alice": 3.0, "bob": 5.0},
            }
        )
    ).content
    assert '{"alice": 3.0, "bob": 5.0}' in revealed
    unsold = r.render_report(rep({"status": "unsold", "reserve": 8.0, "own_bid": 3.0})).content
    assert "not sold" in unsold and "8.0" in unsold
    assert (
        r.render_report(rep({"something": "else"})).content == 'Report (x): {"something": "else"}'
    )


def test_pd_prompt_shows_this_pairs_history_and_no_payoff_table() -> None:
    pd_params = {
        "agents": ["alice", "bob", "carol", "dave"],
        "rounds": 3,
        "matching": "round_robin",
        "disclose_payoffs": False,
    }
    mock = MockBackend([{"move": "C"}, {"move": "D"}, {"move": "C"}])
    agent = LLMAgent(ALICE, mock, PDCodec(), FullTranscript(), report_renderer=PDReportRenderer())
    same = lambda move: ScriptedAgent(lambda request, reports: {"move": move})  # noqa: E731
    session = play(
        PD,
        pd_params,
        {ALICE: agent, BOB: same("D"), AgentId("carol"): same("C"), AgentId("dave"): same("D")},
    )
    assert not session.failed
    prompts = [c.messages[-1].content for c in mock.calls]
    assert "You have not played this opponent before." in prompts[0]
    # round-robin with 4 players: a new opponent every round, so no round has a history
    assert all("You have not played this opponent before." in p for p in prompts)
    assert [f"This is round {i} (counting from 0)." in p for i, p in enumerate(prompts)] == [
        True
    ] * 3
    everything = "\n".join(m.content for c in mock.calls for m in c.messages)
    for other in ("bob", "carol", "dave"):
        assert other not in everything  # no opponent identity, no other pair's data
    assert "matrix" not in everything.lower() and "payoff table" not in everything.lower()
    assert "Round 0 result: you played C, the other player played" in everything


def test_pd_prompt_history_grows_for_a_fixed_pair() -> None:
    mock = MockBackend([{"move": "C"}, {"move": "D"}, {"move": "C"}])
    agent = LLMAgent(ALICE, mock, PDCodec(), None, report_renderer=PDReportRenderer())
    bob = ScriptedAgent(lambda request, reports: {"move": "D"})
    play(
        PD, {"agents": ["alice", "bob"], "rounds": 3, "matching": "fixed"}, {ALICE: agent, BOB: bob}
    )
    prompts = [c.messages[-1].content for c in mock.calls]
    assert "not played this opponent" in prompts[0]
    assert "round 0: C, D" in prompts[1] and "round 1:" not in prompts[1]
    assert "round 0: C, D" in prompts[2] and "round 1: D, D" in prompts[2]


def test_pd_report_renderer_falls_back_to_json_for_other_payloads() -> None:
    report = ParticipantReport(
        report_id=ReportId("x@alice:0"),
        path=["x"],
        layer="pd_round",
        agent_id=AgentId("alice"),
        payload={"other": 1},
    )
    assert PDReportRenderer().render_report(report).content == 'Report (pd_round): {"other": 1}'


def test_the_sentinel_check_is_not_vacuous() -> None:
    """Control: against an environment that leaks, the same check does find the sentinel."""
    session = Session.start(
        "tests.runtime.envs:bidding_leaky",
        {"values": {"alice": SENTINEL_ALICE, "bob": SENTINEL_BOB}},
        0,
    )
    session.advance()
    (request,) = session.pending_for(BOB)
    text = "\n".join(m.content for m in DefaultCodec().render(request, [], []))
    assert "7777.5" in text


def _first_pd_prompt(**extra: object) -> str:
    pd_params = {"agents": ["alice", "bob"], "rounds": 2, **extra}
    mock = MockBackend([{"move": "C"}, {"move": "C"}])
    agent = LLMAgent(ALICE, mock, PDCodec(), None, report_renderer=PDReportRenderer())
    same = ScriptedAgent(lambda request, reports: {"move": "D"})
    session = play(PD, pd_params, {ALICE: agent, BOB: same})
    assert not session.failed
    return "\n".join(m.content for m in mock.calls[0].messages)


def test_pd_prompt_has_the_four_payoff_pairs_when_disclosed() -> None:
    prompt = _first_pd_prompt()
    assert "Payoffs (your move, their move: your payoff, their payoff):" in prompt
    for line in ("C, C: 3, 3", "C, D: 0, 5", "D, C: 5, 0", "D, D: 1, 1"):
        assert line in prompt


def test_pd_prompt_has_no_digit_from_the_table_when_not_disclosed() -> None:
    prompt = _first_pd_prompt(disclose_payoffs=False)
    assert "Payoffs (" not in prompt
    stripped = "\n".join(ln for ln in prompt.splitlines() if not ln.startswith("This is round"))
    assert not any(d in stripped for d in "0135"), stripped
