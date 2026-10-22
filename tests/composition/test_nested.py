import json
import re
from typing import Any

import numpy as np
import pytest

from gao_idl.composition import contracts
from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId, Port
from gao_idl.core.paths import Branch, LoopIter
from gao_idl.core.schemas import Exit
from gao_idl.runtime.session import Session
from gao_idl.testing import (
    _run,
    _trace_without_t,
    assert_arrival_invariant,
    assert_replay_equivalent,
)
from tests.composition import envs

ALICE, BOB = AgentId("alice"), AgentId("bob")
X = {ALICE: 1, BOB: 2}


def play(s: Session, rounds: int) -> None:
    """Answer every pending decision, ``rounds`` times (each stage of each iteration is a round)."""
    for _ in range(rounds):
        s.advance()
        for req in s.pending():
            s.submit(req.agent_id, req.decision_id, {"x": X[req.agent_id]})
    s.advance()


def requested_ids(s: Session) -> list[str]:
    return [e.request.decision_id for e in s.trace if isinstance(e, ev.DecisionRequested)]


def test_nested_paths_in_ids() -> None:
    s = Session.start(envs.LOOP_PAR_SEQ, {"max_iter": 3}, 2)
    play(s, rounds=6)  # 3 iterations x 2 stages
    ids = requested_ids(s)
    expected = [
        f"loop/loop[{i}]/branch[{b}]/{stage}#player"
        for i in range(3)
        for stage in ("first", "second")
        for b in ("A", "B")
    ]
    assert sorted(ids) == sorted(expected)
    assert len(set(ids)) == len(ids) == 12  # path-based ids never repeat, across iterations too
    assert all(re.search(r"loop\[\d\]", d) and "branch[" in d for d in ids)
    # decision requests carry the structured path too (the branch element replaces the Seq's name)
    paths = [e.request.path for e in s.trace if isinstance(e, ev.DecisionRequested)]
    assert ["loop", LoopIter(1), Branch("B"), "first"] in paths
    # alice: 2 decisions x 3 iterations; each decision layer credits 1 per agent
    assert s.ledger.utility(ALICE) == 6.0 and s.ledger.utility(BOB) == 6.0


def test_nested_run_ends_through_the_truncated_port_and_replays() -> None:
    s = Session.start(envs.LOOP_PAR_SEQ, {"max_iter": 3}, 2)
    play(s, rounds=6)
    assert s.finished and not s.failed
    # per iteration: alice's two decisions add 1 + 1 to start 0, bob's add 2 + 2
    assert s.result == Exit(
        port=Port("truncated"),
        record={
            "iterations": 3,
            "last_exit": {
                "A": {"port": "done", "record": {"payload": 2}},
                "B": {"port": "done", "record": {"payload": 4}},
            },
        },
    )
    assert [e.kind for e in s.trace].count("truncated") == 1
    assert_replay_equivalent(s)
    again = Session.restore(json.loads(json.dumps(s.snapshot())))
    assert ev.dump(again.trace) == ev.dump(s.trace)


def test_nested_pending_is_both_branches_at_once_and_restores_mid_run() -> None:
    s = Session.start(envs.LOOP_PAR_SEQ, {"max_iter": 3}, 2)
    play(s, rounds=3)  # iteration 0 done, iteration 1 first stage done
    pending = {r.decision_id: r.agent_id for r in s.pending()}
    assert pending == {
        "loop/loop[1]/branch[A]/second#player": ALICE,
        "loop/loop[1]/branch[B]/second#player": BOB,
    }
    partial = Session.restore(s.snapshot())
    assert {r.decision_id for r in partial.pending()} == set(pending)
    assert ev.dump(partial.trace) == ev.dump(s.trace)


def test_nested_structure_is_described_and_checked() -> None:
    process, _ = envs.loop_par_seq()
    contracts.check(process)
    tree: dict[str, Any] = contracts.describe(process)
    json.dumps(tree)
    assert tree["kind"] == "loop" and tree["body"]["kind"] == "par"
    branch = tree["body"]["branches"]["A"]
    assert branch["kind"] == "seq" and branch["first"]["name"] == "first"
    assert sorted(tree["exits"]) == ["truncated"]


def test_random_schedules_replay_to_the_same_trace() -> None:
    """Random submission order and random advance() placement: replaying the input log must give
    exactly the original trace and ledger (invariant 3), whatever the batching."""
    for seed in range(25):
        rng = np.random.default_rng(seed)
        s = Session.start(envs.LOOP_PAR_SEQ, {"max_iter": 2}, seed)
        s.advance()
        while not s.finished:
            pending = s.pending()
            if pending and rng.random() < 0.7:
                req = pending[int(rng.integers(len(pending)))]
                s.submit(req.agent_id, req.decision_id, {"x": X[req.agent_id]})
            if rng.random() < 0.5 or not s.pending():
                s.advance()
        s.check_determinism()
        restored = Session.restore(s.snapshot())
        assert ev.dump(restored.trace) == ev.dump(s.trace), seed
        assert restored.result == s.result


def test_staggered_answers_with_multi_step_branches_replay_exactly() -> None:
    """Regression: bob's branch moves on to its second layer (several Continue steps) before alice
    answers. Replay batches both answers, and must not let alice's input cut into the middle of
    bob's branch settling."""
    s = Session.start(envs.LOOP_PAR_SEQ, {"max_iter": 1}, 2)
    s.advance()
    by_agent = {r.agent_id: r for r in s.pending()}
    s.submit(BOB, by_agent[BOB].decision_id, {"x": 2})
    s.advance()  # B/first completes and B/second is issued; A is still on its first layer
    assert {r.decision_id for r in s.pending()} == {
        "loop/loop[0]/branch[A]/first#player",
        "loop/loop[0]/branch[B]/second#player",
    }
    s.submit(ALICE, by_agent[ALICE].decision_id, {"x": 1})
    s.advance()
    s.check_determinism()
    order = [e.path[-2:] for e in s.trace if e.kind == "layer_entered"]
    assert order.index([Branch("B"), "second"]) < order.index([Branch("A"), "second"])


ACTIONS = {ALICE: {"x": 1}, BOB: {"x": 2}}


def test_nested_arrival_projection_invariant() -> None:
    """Loop(Par(Seq)): each iteration has two stages and the agents answer in either order."""
    assert_arrival_invariant(envs.LOOP_PAR_SEQ, {"max_iter": 3}, 2, ACTIONS)


def test_par_global_trace_follows_submission_order_but_projections_do_not() -> None:
    a = _run(envs.PAR_PLAYERS, {}, 1, ACTIONS, (ALICE, BOB))
    b = _run(envs.PAR_PLAYERS, {}, 1, ACTIONS, (BOB, ALICE))
    assert _trace_without_t(a) != _trace_without_t(b)  # why exact trace equality cannot be asked
    assert_arrival_invariant(envs.PAR_PLAYERS, {}, 1, ACTIONS)


def test_arrival_invariant_detects_order_dependence_outside_branches() -> None:
    with pytest.raises(AssertionError, match="outside every branch"):
        assert_arrival_invariant(envs.ORDER_LEAK, {}, 1, ACTIONS)


def test_arrival_invariant_detects_order_dependence_inside_a_branch() -> None:
    acts = {**ACTIONS, AgentId("carol"): {"x": 3}}
    with pytest.raises(AssertionError, match="under branch root"):
        assert_arrival_invariant(envs.ORDER_LEAK, {"in_branch": True}, 1, acts)
