from typing import Any

import pytest

from gao_idl.composition.connector import Connector
from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config, Exit
from gao_idl.environments.auction import build_bidding_only
from gao_idl.runtime.session import Session
from gao_idl.testing import (
    _drive,
    _registered,
    assert_branch_rng_independence,
    assert_substitution_equivalent,
)
from tests.composition import envs
from tests.composition.layers import (
    Agents,
    Seed,
    decision_layer,
    fixed_decision_layer,
    n_feedback,
    n_to_seed,
    split_agents,
    step_layer,
    to_n,
)

ALICE, BOB = AgentId("alice"), AgentId("bob")
DONE = Port("done")


def seq(a: Process, c: Connector, b: Process, name: str = "seq") -> Seq:
    return Seq(a, {DONE: (c, b)}, name=name)


def bracketings() -> dict[str, Process]:
    """All five bracketings of s1 -> s2 -> dec -> s3 (three automatic layers, one decision)."""
    s1, s2, dec, s3 = (
        Primitive(step_layer("s1")),
        Primitive(step_layer("s2")),
        Primitive(fixed_decision_layer("dec")),
        Primitive(step_layer("s3")),
    )
    c12, c2d, cd3 = n_feedback(), n_to_seed({"player": "alice"}), to_n()
    return {
        "(((s1 s2) dec) s3)": seq(seq(seq(s1, c12, s2), c2d, dec), cd3, s3),
        "((s1 (s2 dec)) s3)": seq(seq(s1, c12, seq(s2, c2d, dec)), cd3, s3),
        "((s1 s2) (dec s3))": seq(seq(s1, c12, s2, "left"), c2d, seq(dec, cd3, s3, "right")),
        "(s1 ((s2 dec) s3))": seq(s1, c12, seq(seq(s2, c2d, dec), cd3, s3)),
        "(s1 (s2 (dec s3)))": seq(s1, c12, seq(s2, c2d, seq(dec, cd3, s3))),
    }


def run_adhoc(process: Process, cfg: Config, actions: dict[AgentId, dict[str, Any]]) -> Session:
    with _registered(process, cfg) as (ref, params):
        s = Session.start(ref, params, 5)
        _drive(s, actions)
        s.check_determinism()  # replay needs the registry entry, so check inside the block
    return s


def test_seq_associativity() -> None:
    cfg = Config(state={"n": 0}, roles={})
    actions = {ALICE: {"x": 3}}
    variants = bracketings()
    reference = next(iter(variants.values()))
    # s1: 0 -> 1, s2: 1 -> 2, dec: 2 + 3 = 5, s3: 5 -> 6
    s = run_adhoc(reference, cfg, actions)
    assert s.result == Exit(port=DONE, record={"n": 6}) and s.ledger.utility(ALICE) == 1.0
    for name, other in variants.items():
        assert_substitution_equivalent(reference, other, cfg, 5, actions)
        t = run_adhoc(other, cfg, actions)
        assert t.result == s.result, name
    # the bracketings really do issue different path-based ids (only the stripped views agree)
    paths = {
        name: [r.decision_id for r in run_adhoc_requests(proc, cfg)]
        for name, proc in variants.items()
    }
    assert len({tuple(v) for v in paths.values()}) >= 3


def run_adhoc_requests(process: Process, cfg: Config) -> list[Any]:
    with _registered(process, cfg) as (ref, params):
        s = Session.start(ref, params, 5)
        s.advance()
        return s.pending()


def test_primitive_vs_composite_substitution() -> None:
    cfg = Config(state={"n": 1}, roles={})
    wrapped = Seq(Primitive(step_layer()), {})
    assert wrapped.exits == Primitive(step_layer()).exits
    assert_substitution_equivalent(Primitive(step_layer()), wrapped, cfg, 3, {})
    values = {"alice": 9.0, "bob": 7.0}
    bare, bcfg = build_bidding_only(values)
    assert_substitution_equivalent(
        bare, Seq(bare, {}), bcfg, 3, {ALICE: {"amount": 8.0}, BOB: {"amount": 6.0}}
    )


def test_substitution_helper_detects_differences() -> None:
    cfg = Config(state={"n": 1}, roles={})
    with pytest.raises(AssertionError, match="traces differ"):
        assert_substitution_equivalent(
            Primitive(step_layer("x")), Primitive(step_layer("y")), cfg, 3, {}
        )
    other_cfg = Config(state={"n": 1}, roles={})
    different_exit = Seq(Primitive(step_layer("x")), {})
    different_exit_layer = Primitive(step_layer("x"))
    assert_substitution_equivalent(different_exit, different_exit_layer, other_cfg, 3, {})


def test_substitution_is_only_for_deterministic_layers() -> None:
    """Random streams are keyed by path, so wrapping a layer that draws changes what it draws."""
    cfg = Config(state={"start": 0}, roles={RoleId("player"): ALICE})
    bare, wrapped = Primitive(decision_layer("d")), Seq(Primitive(decision_layer("d")), {})
    with pytest.raises(AssertionError, match="information streams of 'alice'"):
        assert_substitution_equivalent(bare, wrapped, cfg, 3, {ALICE: {"x": 1}})
    fixed = Primitive(fixed_decision_layer("d"))
    assert_substitution_equivalent(fixed, Seq(fixed, {}), cfg, 3, {ALICE: {"x": 1}})
    assert Seed.model_validate(cfg.state).start == 0


AGENT_OF = {"A": "alice", "B": "bob"}
ACTIONS = {ALICE: {"x": 1}, BOB: {"x": 2}}


def build_par_env(names: tuple[str, ...]) -> tuple[Process, Config]:
    return envs.par_players({n: [AGENT_OF[n]] for n in names})


def test_par_branch_rng_independence() -> None:
    assert_branch_rng_independence(build_par_env, 11, actions=ACTIONS)
    assert_branch_rng_independence(build_par_env, 11, actions=ACTIONS, branch="B", other="A")

    # non-vacuous: A's observation contains a random draw, and it depends on the seed
    def draw(seed: int) -> int:
        process, cfg = build_par_env(("A",))
        with _registered(process, cfg) as (ref, params):
            s = Session.start(ref, params, seed)
            s.advance()
            return int(s.pending()[0].observation["draw"])

    assert draw(11) != draw(12)


def test_branch_rng_independence_detects_a_dependence_on_siblings() -> None:
    def split_leaky(cfg: Config) -> dict[str, Config]:
        parts = split_agents(cfg)
        n_branches = len(Agents.model_validate(cfg.state).agents)
        return {b: Config(state={"start": n_branches}, roles=c.roles) for b, c in parts.items()}

    def leaky(names: tuple[str, ...]) -> tuple[Process, Config]:
        par = Par(
            {n: Primitive(decision_layer(n)) for n in names},
            DisjointSplitter(split_leaky, Agents),
            DictJoiner(DONE, OpenState),
        )
        return par, Config(state={"agents": {n: [AGENT_OF[n]] for n in names}}, roles={})

    with pytest.raises(AssertionError, match="differs|differ at index"):
        assert_branch_rng_independence(leaky, 11, actions=ACTIONS)
