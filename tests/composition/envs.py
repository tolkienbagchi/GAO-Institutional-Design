"""Composite environment factories used only by tests (addressable via ``env_ref`` strings)."""

from gao_idl.composition.loop import Loop
from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config
from tests.composition.layers import (
    Agents,
    accumulate_layer,
    choice_layer,
    counter_layer,
    decision_layer,
    n_feedback,
    rec_to_seed,
    split_agents,
    step_layer,
    to_n,
)

SEQ_CHOICE = "tests.composition.envs:seq_choice"
SEQ_DECISIONS = "tests.composition.envs:seq_decisions"
PAR_PLAYERS = "tests.composition.envs:par_players"
LOOP_COUNTER = "tests.composition.envs:loop_counter"
LOOP_DECISIONS = "tests.composition.envs:loop_decisions"


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
