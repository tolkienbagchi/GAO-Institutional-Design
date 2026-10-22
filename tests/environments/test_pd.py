"""Repeated prisoner's dilemma: payoffs, private per-pair histories, termination, replay."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, DecisionId
from gao_idl.core.schemas import DecisionRequest
from gao_idl.environments.pd import STANDARD_PAYOFFS, build_repeated_pd, pair_id, pairs_for_round
from gao_idl.runtime.session import Session
from gao_idl.testing import assert_arrival_invariant

ENV = "gao_idl.environments.pd:build_repeated_pd"
FOUR = ["alice", "bob", "carol", "dave"]

Policy = Callable[[DecisionRequest], str]


def params(agents: list[str], rounds: int, matching: str = "fixed", **extra: Any) -> dict[str, Any]:
    return {"agents": agents, "rounds": rounds, "matching": matching, **extra}


def scripted(moves: dict[str, str]) -> Policy:
    """Agent -> string of moves indexed by round (the round comes from the observation)."""

    def policy(req: DecisionRequest) -> str:
        return moves[req.agent_id][req.observation["round_index"]]

    return policy


PATTERNS = {"alice": "CDCD", "bob": "DDCC", "carol": "CCDD", "dave": "DCDC"}


def answer(session: Session, policy: Policy, only: set[str] | None = None) -> None:
    for req in session.pending():
        if only is None or req.agent_id in only:
            session.submit(req.agent_id, req.decision_id, {"move": policy(req)})


def drive(session: Session, policy: Policy) -> list[DecisionRequest]:
    """Run to completion; return every request seen, in order."""
    seen: list[DecisionRequest] = []
    while True:
        session.advance()
        if session.finished:
            return seen
        seen.extend(session.pending())
        answer(session, policy)


def start(p: dict[str, Any], seed: int = 7) -> Session:
    session = Session.start(ENV, p, seed)
    session.advance()
    return session


def info(session: Session, agent: str) -> str:
    return json.dumps(
        [x.model_dump(mode="json") for x in session.ledger.information(AgentId(agent))]
    )


# ------------------------------------------------------------------ payoffs


@pytest.mark.parametrize("profile", ["CC", "CD", "DC", "DD"])
def test_payoff_profiles(profile: str) -> None:
    session = start(params(["alice", "bob"], 1))
    drive(session, lambda req: profile[0] if req.agent_id == "alice" else profile[1])
    row, col = STANDARD_PAYOFFS[profile]
    assert session.ledger.utility(AgentId("alice")) == row
    assert session.ledger.utility(AgentId("bob")) == col
    for agent, own, other, pay in (
        ("alice", profile[0], profile[1], row),
        ("bob", profile[1], profile[0], col),
    ):
        reports = session.reports_for(AgentId(agent))
        assert [r.payload for r in reports] == [
            {"round_index": 0, "own_move": own, "other_move": other, "own_payoff": pay}
        ]
    assert session.result is not None and session.result.port == "done"


def test_custom_payoffs_from_json_params() -> None:
    table = {"CC": [2, 2], "CD": [-1, 4], "DC": [4, -1], "DD": [0, 0]}
    session = start(params(["alice", "bob"], 2, payoffs=table))
    drive(session, lambda req: "C" if req.agent_id == "alice" else "D")
    assert session.ledger.utility(AgentId("alice")) == -2.0
    assert session.ledger.utility(AgentId("bob")) == 8.0


def test_bad_payoff_table_is_a_contract_error() -> None:
    with pytest.raises(ContractError):
        build_repeated_pd([AgentId("a"), AgentId("b")], 1, payoffs={"CC": (1, 1)})


# ------------------------------------------------------------------ histories


def test_private_history_persists_across_rounds_fixed_pairs() -> None:
    session = start(params(FOUR, 3))
    seen = drive(session, scripted(PATTERNS))
    by = {(r.agent_id, r.observation["round_index"]): r.observation for r in seen}
    assert by[("alice", 0)]["own_history"] == []
    assert by[("alice", 1)]["own_history"] == [["C", "D"]]
    # round 3 (index 2): alice's first two rounds with bob only; (own, other) per round
    assert by[("alice", 2)]["own_history"] == [["C", "D"], ["D", "D"]]
    assert by[("bob", 2)]["own_history"] == [["D", "C"], ["D", "D"]]
    assert by[("carol", 2)]["own_history"] == [["C", "D"], ["C", "C"]]
    assert "carol" not in json.dumps(by[("alice", 2)])


def test_rematching_history_is_per_pair() -> None:
    assert [pairs_for_round(FOUR, "round_robin", r)[0] for r in range(4)] == [
        ("alice", "dave"),
        ("alice", "carol"),
        ("alice", "bob"),
        ("alice", "dave"),
    ]
    session = start(params(FOUR, 4, "round_robin"))
    seen = drive(session, scripted(PATTERNS))
    alice = {
        r.observation["round_index"]: r.observation["own_history"]
        for r in seen
        if r.agent_id == "alice"
    }
    assert alice[0] == [] and alice[1] == [] and alice[2] == []  # first meetings
    # round index 3: alice meets dave again; only their round-0 game (alice C, dave D) is shown,
    # not her games against carol (round 1) and bob (round 2)
    assert alice[3] == [["C", "D"]]
    dave = {
        r.observation["round_index"]: r.observation["own_history"]
        for r in seen
        if r.agent_id == "dave"
    }
    assert dave[3] == [["D", "C"]]


def test_other_pairs_not_disclosed() -> None:
    """Noninterference: what carol and dave play never reaches alice's or bob's streams."""
    sessions = []
    for carol_dave in ({"carol": "CCDD", "dave": "DCDC"}, {"carol": "DDCC", "dave": "CDCD"}):
        s = start(params(FOUR, 3))
        drive(s, scripted({**PATTERNS, **carol_dave}))
        sessions.append(s)
    a, b = sessions
    for agent in ("alice", "bob"):
        assert info(a, agent) == info(b, agent)
    for agent in ("carol", "dave"):  # the perturbation is real
        assert info(a, agent) != info(b, agent)
    assert "carol" not in info(a, "alice") and "dave" not in info(a, "bob")


# ------------------------------------------------------------------ structure


@pytest.mark.parametrize("matching", ["fixed", "round_robin"])
def test_normal_termination_not_truncation(matching: str) -> None:
    session = start(params(FOUR, 3, matching))
    drive(session, scripted(PATTERNS))
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "done"
    kinds = {e.kind for e in session.trace}
    assert "truncated" not in kinds and "failed" not in kinds
    assert session.result.record["round_index"] == 2 and session.result.record["rounds"] == 3
    session.check_determinism()


@pytest.mark.parametrize("matching", ["fixed", "round_robin"])
def test_all_agents_in_every_round(matching: str) -> None:
    """Population accounting: one pending decision per agent per round, every round."""
    rounds = 4
    session = start(params(FOUR, rounds, matching))
    stages = 0
    while not session.finished:
        pending = session.pending()
        assert len(pending) == len(FOUR)
        assert {r.agent_id for r in pending} == set(FOUR)
        for agent in FOUR:
            assert len(session.pending_for(AgentId(agent))) == 1
        assert {r.observation["round_index"] for r in pending} == {stages}
        answer(session, scripted(PATTERNS))
        session.advance()
        stages += 1
    assert stages == rounds


def test_ids_are_path_based() -> None:
    session = start(params(["alice", "bob"], 2))
    (a, b) = sorted(session.pending(), key=lambda r: r.decision_id)
    assert a.decision_id == DecisionId("pd/loop[0]/round/branch[pair_0]#col")
    assert b.decision_id == DecisionId("pd/loop[0]/round/branch[pair_0]#row")
    assert (b.agent_id, a.agent_id) == ("alice", "bob")  # first listed agent is the row player
    answer(session, scripted({"alice": "CC", "bob": "DD"}))
    session.advance()
    assert all(
        r.decision_id.startswith("pd/loop[1]/round/branch[pair_0]#") for r in session.pending()
    )


def test_replay_partial_restore_mid_round() -> None:
    session = start(params(FOUR, 3, "round_robin"))
    policy = scripted(PATTERNS)
    answer(session, policy)  # round 0 completely
    session.advance()
    answer(session, policy, only={"alice", "carol"})  # round 1: half the agents
    session.advance()
    waiting = sorted(r.decision_id for r in session.pending())
    assert len(waiting) == 2

    restored = Session.restore(json.loads(json.dumps(session.snapshot())))
    assert sorted(r.decision_id for r in restored.pending()) == waiting
    assert ev.dump(restored.trace) == ev.dump(session.trace)
    assert info(restored, "alice") == info(session, "alice")

    for s in (session, restored):
        drive(s, policy)
        s.check_determinism()
    assert ev.dump(restored.trace) == ev.dump(session.trace)
    assert restored.result == session.result
    assert restored.ledger.snapshot() == session.ledger.snapshot()


@pytest.mark.parametrize("matching", ["fixed", "round_robin"])
def test_arrival_invariance_across_pairs(matching: str) -> None:
    actions = {AgentId(a): {"move": m[0]} for a, m in PATTERNS.items()}
    assert_arrival_invariant(ENV, params(FOUR, 3, matching), 3, actions)


# ------------------------------------------------------------------ matching and validation


@pytest.mark.parametrize("n", [2, 4, 6, 8])
def test_round_robin_meets_everyone_once_per_cycle(n: int) -> None:
    agents = [f"a{i}" for i in range(n)]
    met: list[str] = []
    for r in range(n - 1):
        pairs = pairs_for_round(agents, "round_robin", r)
        assert sorted(x for p in pairs for x in p) == sorted(agents)  # a perfect matching
        met += [pair_id(*p) for p in pairs]
    assert len(met) == len(set(met)) == n * (n - 1) // 2
    assert pairs_for_round(agents, "round_robin", n - 1) == pairs_for_round(
        agents, "round_robin", 0
    )


@pytest.mark.parametrize(
    "agents, rounds",
    [
        (["a", "b", "c"], 2),
        ([], 1),
        (["a"], 1),
        (["a", "a"], 1),
        (["a~b", "c"], 1),
        (["a", "b"], 0),
    ],
)
def test_bad_populations_and_rounds_are_contract_errors(agents: list[str], rounds: int) -> None:
    with pytest.raises(ContractError):
        build_repeated_pd([AgentId(a) for a in agents], rounds, "round_robin")


def test_unknown_matching_is_a_contract_error() -> None:
    with pytest.raises(ContractError):
        build_repeated_pd([AgentId("a"), AgentId("b")], 1, "random")  # type: ignore[arg-type]
