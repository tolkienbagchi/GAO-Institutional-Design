"""Scripted play: entries in order, repetition of the last entry, timeouts, errors."""

import pytest

from gao_idl import ScriptedAgent, Session, run_sync
from gao_idl.authoring.play import play, script_of
from gao_idl.core.ids import AgentId
from tests.authoring.envs import ULTIMATUM

AUCTION = "gao_idl.environments.auction:build_auction"
PD = "gao_idl.environments.pd:build_repeated_pd"


def test_auction_outcome() -> None:
    params = {"values": {"alice": 10.0, "bob": 7.0, "carol": 4.0}, "reserve": 2.0}
    script = {"alice": [{"amount": 10}], "bob": [{"amount": 7}], "carol": [{"amount": 3.5}]}
    session = play(AUCTION, params, 3, script)
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "done"
    assert session.ledger.utility(AgentId("alice")) == 3.0  # pays bob's 7
    session.check_determinism()


def test_entries_are_used_in_order_and_the_last_repeats() -> None:
    script = {"a": [{"move": "C"}, {"move": "D"}], "b": [{"move": "D"}]}
    session = play(PD, {"agents": ["a", "b"], "rounds": 3}, 7, script)
    reports = [r.payload for r in session.reports_for(AgentId("b"))]
    assert [r["other_move"] for r in reports] == ["C", "D", "D"]
    assert [r["own_move"] for r in reports] == ["D", "D", "D"]


def test_none_triggers_each_timeout_policy() -> None:
    failed = play(ULTIMATUM, {}, 0, {"ann": [None], "ben": [{"accept": True}]})
    assert failed.failed
    dropped = play(ULTIMATUM, {}, 0, {"ann": [{"offer": 4.0}], "ben": [None]})
    assert not dropped.failed and dropped.ledger.utility(AgentId("ben")) == 0.0
    assert any(e.kind == "role_dropped" for e in dropped.trace)
    params = {"mutant": "default_timeout"}
    defaulted = play(ULTIMATUM, params, 0, {"ann": [{"offer": 4.0}], "ben": [None]})
    assert defaulted.ledger.utility(AgentId("ben")) == 4.0  # the default accepts
    assert any(e.kind == "action_defaulted" for e in defaulted.trace)


def test_missing_agent_and_bad_action() -> None:
    with pytest.raises(ValueError, match="ben"):
        play(ULTIMATUM, {}, 0, {"ann": [{"offer": 4.0}]})
    with pytest.raises(Exception, match="Infeasible|infeasible|offer"):
        play(ULTIMATUM, {}, 0, {"ann": [{"offer": 40.0}], "ben": [None]})


def test_script_of_reproduces_a_run_driven_by_adapters() -> None:
    params = {"agents": ["a", "b", "c", "d"], "rounds": 3, "matching": "round_robin"}
    session = Session.start(PD, params, 5)
    moves = {"a": "C", "b": "D", "c": "C", "d": "D"}
    run_sync(
        session,
        {AgentId(a): ScriptedAgent(lambda req, reps, m=m: {"move": m}) for a, m in moves.items()},
    )
    again = play(PD, params, 5, script_of(session))
    assert again.ledger.snapshot() == session.ledger.snapshot()
    assert again.result == session.result
