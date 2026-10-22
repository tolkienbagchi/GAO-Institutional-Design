"""Composite environment factories used only by tests (addressable via ``env_ref`` strings)."""

from typing import Any

from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config
from tests.composition.layers import (
    Agents,
    N,
    accumulate_layer,
    choice_layer,
    counter_layer,
    decision_layer,
    n_feedback,
    no_agents,
    reader_layer,
    rec_to_seed,
    snoop_layer,
    split_agents,
    stash_layer,
    step_layer,
    to_n,
    writer_layer,
)

SEQ_CHOICE = "tests.composition.envs:seq_choice"
SEQ_DECISIONS = "tests.composition.envs:seq_decisions"
PAR_PLAYERS = "tests.composition.envs:par_players"
LOOP_COUNTER = "tests.composition.envs:loop_counter"
LOOP_DECISIONS = "tests.composition.envs:loop_decisions"
LOOP_STASH = "tests.composition.envs:loop_stash"
PAR_SHARED_STORE = "tests.composition.envs:par_shared_store"
SNOOP = "tests.composition.envs:snoop"
LOOP_PAR_SEQ = "tests.composition.envs:loop_par_seq"
SEQ_STORE_CONNECTOR = "tests.composition.envs:seq_store_connector"


def seq_choice(port: str = "a", payload: int = 5) -> tuple[Process, Config]:
    """``choose`` exits on a/b; only ``a`` is routed (to ``step``), ``b`` is re-exported."""
    seq = Seq(Primitive(choice_layer()), {Port("a"): (to_n(), Primitive(step_layer()))}, name="seq")
    return seq, Config(state={"port": port, "payload": payload}, roles={})


def seq_decisions(start: int = 0) -> tuple[Process, Config]:
    """alice decides in ``first``; her payload seeds bob's decision in ``second``."""
    seq = Seq(
        Primitive(decision_layer("first")),
        {Port("done"): (rec_to_seed({"player": "bob"}), Primitive(decision_layer("second")))},
        name="seq",
    )
    cfg = Config(state={"start": start}, roles={RoleId("player"): AgentId("alice")})
    return seq, cfg


def par_players(
    agents: dict[str, list[str]] | None = None, order: list[str] | None = None
) -> tuple[Process, Config]:
    """One decision branch per entry of ``agents`` (default A: alice, B: bob), joined on ``done``.

    ``order`` is the dict insertion order of the branches (the Par must not depend on it).
    """
    agents = agents or {"A": ["alice"], "B": ["bob"]}
    names = order or list(agents)
    par = Par(
        {b: Primitive(decision_layer(b)) for b in names},
        DisjointSplitter(split_agents, Agents),
        DictJoiner(Port("done"), OpenState),
    )
    return par, Config(state={"agents": agents}, roles={})


def loop_counter(
    max_iter: int = 10, on_limit: str = "truncate", limit: int = 3
) -> tuple[Process, Config]:
    """Automatic counter: feeds ``again`` back until n hits ``limit``, then exits ``finished``."""
    assert on_limit in ("truncate", "fail")
    loop = Loop(
        Primitive(counter_layer(limit=limit)),
        feedback={Port("again"): n_feedback()},
        until={Port("stop"): Port("finished")},
        max_iter=max_iter,
        on_limit=on_limit,  # type: ignore[arg-type]
    )
    return loop, Config(state={"n": 0}, roles={})


def loop_decisions(threshold: int = 10, max_iter: int = 10) -> tuple[Process, Config]:
    """alice adds her action to a running total each iteration until it reaches ``threshold``."""
    loop = Loop(
        Primitive(accumulate_layer(threshold=threshold)),
        feedback={Port("again"): rec_to_seed({"player": "alice"})},
        until={Port("stop"): Port("finished")},
        max_iter=max_iter,
        on_limit="truncate",
    )
    return loop, Config(state={"start": 0}, roles={RoleId("player"): AgentId("alice")})


def loop_stash(max_iter: int = 10, limit: int = 4) -> tuple[Process, Config]:
    """Loop whose body appends to a store list each iteration (store persists across iterations)."""
    loop = Loop(
        Primitive(stash_layer(limit=limit)),
        feedback={Port("again"): n_feedback()},
        until={Port("stop"): Port("finished")},
        max_iter=max_iter,
        on_limit="fail",
    )
    return loop, Config(state={"n": 0}, roles={})


def par_shared_store(writer: str = "A", reader: str = "B") -> tuple[Process, Config]:
    """Two automatic branches: ``writer`` writes store key ``shared``, ``reader`` reads it."""
    par = Par(
        {writer: Primitive(writer_layer()), reader: Primitive(reader_layer())},
        DisjointSplitter(no_agents, OpenState),
        DictJoiner(Port("done"), OpenState),
    )
    return par, Config(state={"branches": [writer, reader]}, roles={})


def snoop(mode: str = "read") -> tuple[Process, Config]:
    """A single layer that touches an undeclared store key."""
    return Primitive(snoop_layer(mode=mode)), Config(state={"n": 0}, roles={})


def seq_store_connector(mode: str = "read") -> tuple[Process, Config]:
    """A Seq whose connector touches an undeclared store key."""

    def fn(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        if mode == "read":
            store.get("secret")
        else:
            store.set("secret", 1)
        return Config(state={"n": 0}, roles={})

    seq = Seq(
        Primitive(choice_layer()),
        {Port("a"): (Connector(fn, N), Primitive(step_layer()))},
        name="seq",
    )
    return seq, Config(state={"port": "a", "payload": 1}, roles={})


def loop_par_seq(max_iter: int = 3) -> tuple[Process, Config]:
    """``Loop(Par(Seq(...)))``: each iteration, alice and bob each decide twice in their own branch.

    The loop never terminates normally; it is cut off by ``max_iter`` (reserved ``truncated`` port).
    """
    agents = {"A": ["alice"], "B": ["bob"]}

    def branch(agent: str) -> Seq:
        return Seq(
            Primitive(decision_layer("first")),
            {Port("done"): (rec_to_seed({"player": agent}), Primitive(decision_layer("second")))},
            name=f"seq_{agent}",
        )

    par = Par(
        {"A": branch("alice"), "B": branch("bob")},
        DisjointSplitter(split_agents, Agents),
        DictJoiner(Port("again"), OpenState),
    )
    again = Connector(lambda rec, ledger, store: Config(state={"agents": agents}, roles={}), Agents)
    loop = Loop(par, {Port("again"): again}, {}, max_iter=max_iter, on_limit="truncate")
    return loop, Config(state={"agents": agents}, roles={})
