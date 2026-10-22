"""Composite environment factories used only by tests (addressable via ``env_ref`` strings)."""

from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config
from tests.composition.layers import (
    Agents,
    choice_layer,
    decision_layer,
    rec_to_seed,
    split_agents,
    step_layer,
    to_n,
)

SEQ_CHOICE = "tests.composition.envs:seq_choice"
SEQ_DECISIONS = "tests.composition.envs:seq_decisions"
PAR_PLAYERS = "tests.composition.envs:par_players"


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
