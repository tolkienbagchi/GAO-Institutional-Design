from typing import Any

import pytest
from pydantic import BaseModel

from gao_idl.composition.connector import Connector
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.routing import check_name, owns
from gao_idl.composition.seq import Seq
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.runtime.session import Session
from gao_idl.testing import assert_replay_equivalent
from tests.composition import envs
from tests.composition.layers import (
    N,
    Rec,
    choice_layer,
    step_layer,
    to_n,
)

ALICE, BOB = AgentId("alice"), AgentId("bob")


def kinds(s: Session) -> list[str]:
    return [e.kind for e in s.trace]


def test_seq_routing_and_reexport() -> None:
    routed = Session.start(envs.SEQ_CHOICE, {"port": "a", "payload": 5}, 1)
    routed.advance()
    assert routed.result == Exit(port=Port("done"), record={"n": 6})
    layers = [e.path for e in routed.trace if e.kind == "layer_entered"]
    assert layers == [["seq", "choose"], ["seq", "step"]]

    other = Session.start(envs.SEQ_CHOICE, {"port": "b", "payload": 5}, 1)
    other.advance()
    assert other.finished and not other.failed
    assert other.result == Exit(port=Port("b"), record={"payload": 5})  # identical record
    assert [e.path for e in other.trace if e.kind == "layer_entered"] == [["seq", "choose"]]


def test_seq_exits_are_the_union_of_unrouted_and_continuation_ports() -> None:
    seq, _ = envs.seq_choice()
    assert seq.exits == {Port("b"): Rec, Port("done"): N}
    assert seq.entry.state_schema is choice_layer().entry.state_schema


def test_seq_contract_mismatch_raises() -> None:
    wrong = Connector(lambda rec, led, store: Config(state={}, roles={}), Rec)  # not N
    with pytest.raises(ContractError, match="outputs Rec but 'step' expects N"):
        Seq(Primitive(choice_layer()), {Port("a"): (wrong, Primitive(step_layer()))})


def test_seq_rejects_route_for_a_port_first_never_uses() -> None:
    with pytest.raises(ContractError, match="never exits through"):
        Seq(Primitive(choice_layer()), {Port("zzz"): (to_n(), Primitive(step_layer()))})


def test_seq_rejects_conflicting_exit_schemas() -> None:
    class Other(BaseModel):
        z: int = 0

    # first re-exports "b" as Rec; the continuation also exits through "b" but as Other
    cont = SimpleLayer(
        name="other",
        entry=Contract(state_schema=N),
        exits={Port("b"): Other},
        export=lambda cfg, out: Exit(port=Port("b"), record={}),
    )
    with pytest.raises(ContractError, match="conflicting schemas"):
        Seq(Primitive(choice_layer()), {Port("a"): (to_n(), Primitive(cont))})


def test_seq_rejects_continuation_named_like_first() -> None:
    with pytest.raises(ContractError, match="same name"):
        Seq(Primitive(choice_layer("x")), {Port("a"): (to_n(), Primitive(step_layer("x")))})


def test_seq_rejects_reserved_characters_in_names() -> None:
    with pytest.raises(ContractError, match="reserved"):
        Seq(Primitive(choice_layer("a/b")), {})
    with pytest.raises(ContractError, match="reserved"):
        Seq(Primitive(choice_layer()), {}, name="x#y")


def test_seq_runs_decision_layers_in_order_with_path_ids() -> None:
    s = Session.start(envs.SEQ_DECISIONS, {"start": 10}, 3)
    s.advance()
    (req,) = s.pending()
    assert (req.decision_id, req.agent_id, req.path) == (
        "seq/first#player",
        ALICE,
        ["seq", "first"],
    )
    assert s.pending_for(BOB) == []  # bob's decision does not exist until `first` completes
    s.submit(ALICE, req.decision_id, {"x": 4})
    s.advance()
    (req2,) = s.pending()
    assert (req2.decision_id, req2.agent_id) == ("seq/second#player", BOB)
    assert req2.observation["start"] == 14  # the connector carried first's payload
    s.submit(BOB, req2.decision_id, {"x": 1})
    s.advance()
    assert s.result == Exit(port=Port("done"), record={"payload": 15})
    assert s.ledger.utility(ALICE) == 1.0 and s.ledger.utility(BOB) == 1.0
    assert_replay_equivalent(s)


def test_seq_rejects_a_decision_id_of_a_child_that_is_not_running() -> None:
    s = Session.start(envs.SEQ_DECISIONS, {}, 3)
    s.advance()
    with pytest.raises(UnknownDecision):
        s.submit(BOB, DecisionId("seq/second#player"), {"x": 1})


def test_seq_step_rejects_foreign_inputs() -> None:
    seq, cfg = envs.seq_decisions()
    s = Session.start(envs.SEQ_DECISIONS, {}, 3)
    ctx = s._ctx  # white-box: drive the process directly
    frame = seq.start(cfg, ctx)
    frame, _ = seq.step(frame, [], ctx)
    foreign = ev.Submitted(decision_id=DecisionId("other/x#player"), agent_id=ALICE, action={})
    with pytest.raises(UnknownDecision):
        seq.step(frame, [foreign], ctx)


def test_seq_frame_is_json_dumpable_and_nests_the_child() -> None:
    seq, cfg = envs.seq_decisions()
    s = Session.start(envs.SEQ_DECISIONS, {}, 3)
    frame = seq.start(cfg, s._ctx)
    dumped: dict[str, Any] = frame.model_dump(mode="json")
    assert dumped["stage"] == 0 and dumped["child"]["phase"] == "init"


def test_seq_emits_no_events_of_its_own() -> None:
    s = Session.start(envs.SEQ_CHOICE, {"port": "a"}, 1)
    s.advance()
    assert kinds(s) == ["layer_entered", "layer_completed", "exited"] * 2
    assert all(e.path[0] == "seq" and len(e.path) == 2 for e in s.trace)  # only children's paths


def test_owns_checks_the_path_boundary() -> None:
    assert owns(("seq", "a"), DecisionId("seq/a#r"))
    assert owns(("seq", "a"), DecisionId("seq/a/b#r"))
    assert not owns(("seq", "a"), DecisionId("seq/ab#r"))
    assert not owns(("seq", "a"), DecisionId("seq#r"))
    assert owns(("seq", "a"), DecisionId("seq/a#r/with#hash"))


def test_check_name() -> None:
    check_name("x", "fine_name-1")
    for bad in ("", "a/b", "a#b", "a@b", "a[b", "a]b"):
        with pytest.raises(ContractError):
            check_name("x", bad)
