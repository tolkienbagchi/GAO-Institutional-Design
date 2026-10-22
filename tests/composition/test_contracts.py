import json
from dataclasses import replace
from typing import Any

import pytest

from gao_idl.composition import contracts
from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.seq import Seq
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import Port
from gao_idl.core.layer import Contract
from tests.composition import envs
from tests.composition.layers import N, choice_layer, step_layer, to_n


def nested_seq() -> Seq:
    inner = Seq(Primitive(choice_layer("c1")), {Port("a"): (to_n(), Primitive(step_layer("s1")))})
    return Seq(inner, {Port("done"): (to_n(), Primitive(step_layer("s2")))}, name="outer")


def test_check_accepts_every_test_environment() -> None:
    for factory in (
        envs.seq_choice,
        envs.seq_decisions,
        envs.par_players,
        envs.loop_counter,
        envs.loop_decisions,
    ):
        process, _ = factory()
        contracts.check(process)


def test_check_recurses_into_children() -> None:
    outer = nested_seq()
    contracts.check(outer)
    inner = outer.first
    assert isinstance(inner, Seq)
    object.__setattr__(
        inner, "name", "bad/name"
    )  # corrupt a descendant behind the constructor's back
    with pytest.raises(ContractError, match="reserved"):
        contracts.check(outer)


def test_constructors_run_the_check() -> None:
    # the wiring error is reported by the constructor, not at run time
    with pytest.raises(ContractError):
        Seq(Primitive(choice_layer()), {Port("zzz"): (to_n(), Primitive(step_layer()))})


def test_describe_is_json_and_shows_structure() -> None:
    tree = contracts.describe(nested_seq())
    json.dumps(tree)  # JSON-able
    assert tree["kind"] == "seq" and tree["name"] == "outer"
    assert tree["first"]["kind"] == "seq" and tree["first"]["first"]["name"] == "c1"
    assert sorted(tree["exits"]) == ["b", "done"]
    assert tree["entry"]["state_schema"] == {"name": "Pick", "fields": ["payload", "port"]}
    route = tree["routes"]["done"]
    assert route["connector"]["output_schema"]["name"] == "N"
    assert route["then"]["name"] == "s2" and route["then"]["kind"] == "primitive"
    assert route["then"]["timeout"] == "fail"


def test_describe_par_and_loop() -> None:
    par = contracts.describe(envs.par_players()[0])
    assert par["kind"] == "par" and sorted(par["branches"]) == ["A", "B"]
    assert par["joiner"] == {
        "type": "DictJoiner",
        "exits": {"done": {"name": "OpenState", "fields": []}},
    }
    loop: dict[str, Any] = contracts.describe(envs.loop_counter(max_iter=7)[0])
    assert (loop["kind"], loop["max_iter"], loop["on_limit"]) == ("loop", 7, "truncate")
    assert loop["until"] == {"stop": "finished"} and sorted(loop["exits"]) == [
        "finished",
        "truncated",
    ]
    assert loop["body"]["name"] == "count" and list(loop["feedback"]) == ["again"]


def test_entry_store_scope_is_the_union_over_the_tree() -> None:
    scoped = replace(
        step_layer("scoped"),
        entry=Contract(state_schema=N, store_reads=frozenset({"a"}), store_writes=frozenset({"b"})),
    )
    conn = Connector(to_n().fn, N, store_reads=frozenset({"c"}), store_writes=frozenset({"d"}))
    seq = Seq(Primitive(choice_layer()), {Port("a"): (conn, Primitive(scoped))})
    assert seq.entry.store_reads == {"a", "c"} and seq.entry.store_writes == {"b", "d"}
    tree = contracts.describe(seq)
    assert tree["entry"]["store_reads"] == ["a", "c"] and tree["entry"]["store_writes"] == [
        "b",
        "d",
    ]
    assert tree["routes"]["a"]["connector"]["store_reads"] == ["c"]
    loop = Loop(
        Primitive(scoped),
        {Port("done"): Connector(to_n().fn, N, store_reads=frozenset({"e"}))},
        {},
        max_iter=2,
        on_limit="fail",
    )
    assert loop.entry.store_reads == {"a", "e"} and loop.entry.store_writes == {"b"}
