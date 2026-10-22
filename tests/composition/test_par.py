from typing import Any

import pytest

from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, UnknownDecision, WrongAgent
from gao_idl.core.ids import AgentId, DecisionId, Port, RoleId
from gao_idl.core.paths import Branch
from gao_idl.core.schemas import Config, Exit
from gao_idl.runtime.session import Session
from gao_idl.testing import _run_env, assert_replay_equivalent
from tests.composition import envs
from tests.composition.layers import Agents, choice_layer, decision_layer, split_agents

ALICE, BOB = AgentId("alice"), AgentId("bob")
DA, DB = DecisionId("par/branch[A]#player"), DecisionId("par/branch[B]#player")


def fresh(params: dict[str, Any] | None = None, seed: int = 1) -> Session:
    s = Session.start(envs.PAR_PLAYERS, params or {}, seed)
    s.advance()
    return s


def completed_paths(s: Session) -> list[list[Any]]:
    return [ev.dump([e])[0]["path"] for e in s.trace if e.kind == "layer_completed"]


def test_par_independent_progress() -> None:
    s = fresh()
    (ra,) = s.pending_for(ALICE)
    (rb,) = s.pending_for(BOB)
    s.submit(ALICE, ra.decision_id, {"x": 2})
    s.advance()
    assert completed_paths(s) == [["par", {"branch": "A"}]]  # A is done ...
    assert not s.finished and s.pending() == [rb]  # ... B is still pending, request unchanged
    s.submit(BOB, rb.decision_id, {"x": 3})
    s.advance()
    assert s.result == Exit(
        port=Port("done"),
        record={
            "A": {"port": "done", "record": {"payload": 2}},
            "B": {"port": "done", "record": {"payload": 3}},
        },
    )
    assert len([e for e in s.trace if e.kind == "layer_completed"]) == 2  # A was not re-run
    assert_replay_equivalent(s)


def test_par_disjoint_splitter_rejects_overlap() -> None:
    with pytest.raises(ContractError, match="alice"):
        Session.start(envs.PAR_PLAYERS, {"agents": {"A": ["alice"], "B": ["alice"]}}, 1)
    splitter = DisjointSplitter(split_agents, Agents)
    cfg = Config(state={"agents": {"A": ["x"], "B": ["x"]}}, roles={})
    with pytest.raises(ContractError, match="disjoint"):
        splitter.split(cfg)
    ok = splitter.split(Config(state={"agents": {"A": ["x", "x"], "B": ["y"]}}, roles={}))
    assert sorted(ok) == ["A", "B"]  # one agent in several roles of the same branch is fine


def test_par_pending_keyed_by_path_role() -> None:
    s = fresh()
    assert {r.role for r in s.pending()} == {RoleId("player")}  # same role name in both branches
    assert sorted(r.decision_id for r in s.pending()) == [DA, DB]
    assert DA != DB
    assert [r.decision_id for r in s.pending_for(ALICE)] == [DA]
    # an agent cannot answer the other branch's decision
    with pytest.raises(WrongAgent):
        s.submit(BOB, DA, {"x": 1})


def test_one_agent_with_several_pending_decisions_inside_par() -> None:
    s = fresh({"agents": {"A": ["alice", "alice"], "B": ["bob"]}})
    ids = [r.decision_id for r in s.pending_for(ALICE)]
    assert ids == ["par/branch[A]#player:0", "par/branch[A]#player:1"]
    for did in ids:
        s.submit(ALICE, DecisionId(did), {"x": 1})
    s.submit(BOB, DB, {"x": 5})
    s.advance()
    assert s.result == Exit(
        port=Port("done"),
        record={
            "A": {"port": "done", "record": {"payload": 2}},
            "B": {"port": "done", "record": {"payload": 5}},
        },
    )


def test_par_steps_branches_in_sorted_order_whatever_the_insertion_order() -> None:
    def run(order: list[str]) -> list[dict[str, Any]]:
        s = Session.start(envs.PAR_PLAYERS, {"order": order}, 5)
        s.advance()
        s.submit(BOB, DB, {"x": 1})
        s.submit(ALICE, DA, {"x": 2})
        s.advance()
        return ev.dump(s.trace)

    assert run(["A", "B"]) == run(["B", "A"])
    first = [d["path"] for d in run(["B", "A"]) if d["kind"] == "layer_entered"]
    assert first == [["par", {"branch": "A"}], ["par", {"branch": "B"}]]


def test_trace_depends_on_log_order_not_on_advance_boundaries() -> None:
    """B answers first, in its own advance(); replaying the log must reproduce the same trace."""
    staggered = fresh(seed=9)
    staggered.submit(BOB, DB, {"x": 1})
    staggered.advance()
    staggered.submit(ALICE, DA, {"x": 2})
    staggered.advance()
    staggered.check_determinism()  # replay batches both submissions into one advance()
    batched = fresh(seed=9)
    batched.submit(BOB, DB, {"x": 1})
    batched.submit(ALICE, DA, {"x": 2})
    batched.advance()
    assert ev.dump(staggered.trace) == ev.dump(batched.trace)
    order = [d["path"][-1] for d in ev.dump(staggered.trace) if d["kind"] == "layer_completed"]
    assert order == [{"branch": "B"}, {"branch": "A"}]  # log order, not sorted order


def test_par_fails_when_a_branch_fails() -> None:
    s = fresh()
    s.timeout(DA)  # decision_layer's policy is Fail
    s.advance()
    assert s.failed and s.result is None and s.pending() == []
    failures = [e for e in s.trace if e.kind == "failed"]
    assert len(failures) == 1  # emitted once, by the branch's Primitive
    assert failures[0].path == ["par", Branch("A")]


def test_par_join_output_is_validated() -> None:
    from pydantic import BaseModel

    class NeedsC(BaseModel):
        C: dict[str, Any]

    par = Par(
        {b: Primitive(decision_layer(b)) for b in ("A", "B")},
        DisjointSplitter(split_agents, Agents),
        DictJoiner(Port("done"), NeedsC),
    )
    assert par.exits == {Port("done"): NeedsC}
    with pytest.raises(ContractError, match="NeedsC"):
        DictJoiner(Port("done"), NeedsC).join({"A": Exit(port=Port("done"), record={})})


def test_par_construction_checks() -> None:
    with pytest.raises(ContractError, match="no branches"):
        Par({}, DisjointSplitter(split_agents, Agents), DictJoiner(Port("done"), OpenState))
    with pytest.raises(ContractError, match="reserved"):
        Par(
            {"a]b": Primitive(decision_layer())},
            DisjointSplitter(split_agents, Agents),
            DictJoiner(Port("done"), OpenState),
        )


def test_par_start_checks_splitter_output_and_entry_state() -> None:
    with pytest.raises(ContractError, match="expected"):
        Session.start(
            envs.PAR_PLAYERS, {"agents": {"A": ["alice"], "B": ["bob"]}, "order": ["A"]}, 1
        )
    par, _ = envs.par_players()
    s = Session.start(envs.PAR_PLAYERS, {}, 1)
    with pytest.raises(ContractError, match="invalid entry state"):
        par.start(Config(state={"agents": 3}, roles={}), s._ctx)


def test_par_unknown_and_foreign_inputs() -> None:
    s = fresh()
    with pytest.raises(UnknownDecision):
        s.submit(ALICE, DecisionId("par/branch[C]#player"), {"x": 1})
    par, _ = envs.par_players()
    ctx = s._ctx
    frame = par.start(Config(state={"agents": {"A": ["alice"], "B": ["bob"]}}, roles={}), ctx)
    with pytest.raises(UnknownDecision):
        par.step(
            frame, [ev.Submitted(decision_id=DecisionId("x#y"), agent_id=ALICE, action={})], ctx
        )


def test_par_frame_is_json_dumpable() -> None:
    par, _ = envs.par_players()
    s = Session.start(envs.PAR_PLAYERS, {}, 1)
    frame = par.start(Config(state={"agents": {"A": ["alice"], "B": ["bob"]}}, roles={}), s._ctx)
    dumped = frame.model_dump(mode="json")
    assert sorted(dumped["children"]) == ["A", "B"]
    assert dumped["children"]["A"]["phase"] == "init"


def test_dict_joiner_keeps_each_branchs_exit_port() -> None:
    from pydantic import BaseModel

    class BranchExit(BaseModel):
        port: str
        record: dict[str, Any]

    class Joined(BaseModel):
        A: BranchExit
        B: BranchExit

    joined = DictJoiner(Port("done"), Joined).join(
        {
            "A": Exit(port=Port("a"), record={"payload": 1}),
            "B": Exit(port=Port("b"), record={"payload": 2}),
        }
    )
    assert joined == Exit(
        port=Port("done"),
        record={
            "A": {"port": "a", "record": {"payload": 1}},
            "B": {"port": "b", "record": {"payload": 2}},
        },
    )


def test_par_branches_that_exit_through_different_ports_stay_distinguishable() -> None:
    def split(cfg: Config) -> dict[str, Config]:
        return {
            "A": Config(state={"port": "a", "payload": 1}, roles={}),
            "B": Config(state={"port": "b", "payload": 2}, roles={}),
        }

    par = Par(
        {b: Primitive(choice_layer()) for b in ("A", "B")},
        DisjointSplitter(split, OpenState),
        DictJoiner(Port("done"), OpenState),
    )
    s = _run_env(par, Config(state={}, roles={}), 1, {})
    assert s.finished and not s.failed
    assert s.result == Exit(
        port=Port("done"),
        record={
            "A": {"port": "a", "record": {"payload": 1}},
            "B": {"port": "b", "record": {"payload": 2}},
        },
    )
