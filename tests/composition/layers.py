"""Small layers and connectors used to build composite test environments."""

from typing import Any

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.connector import Connector
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView


class N(BaseModel):
    n: int = 0


class Rec(BaseModel):
    payload: int


class Pick(BaseModel):
    port: str
    payload: int


class Seed(BaseModel):
    start: int = 0


class Act(BaseModel):
    x: int


class Obs(BaseModel):
    start: int
    draw: int


class Done(BaseModel):
    """Exit record of ``Counter``: the count reached."""

    count: int


def step_layer(name: str = "step") -> SimpleLayer:
    """Automatic layer: ``N(n)`` in, ``N(n+1)`` out through ``done``."""
    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=N),
        exits={Port("done"): N},
        outcome=lambda cfg, actions, rng, store: N(n=N.model_validate(cfg.state).n + 1),
        export=lambda cfg, outcome: Exit(port=Port("done"), record=outcome.model_dump(mode="json")),
    )


def choice_layer(name: str = "choose") -> SimpleLayer:
    """Automatic layer that exits through the port named in its state, with ``Rec`` as record."""

    def export(cfg: Config, outcome: BaseModel) -> Exit:
        pick = Pick.model_validate(cfg.state)
        return Exit(port=Port(pick.port), record={"payload": pick.payload})

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=Pick),
        exits={Port("a"): Rec, Port("b"): Rec},
        export=export,
    )


def decision_layer(name: str = "decide") -> SimpleLayer:
    """One round: every role submits ``Act(x)``; payload = start + sum of x; utility = own x.

    ``observe`` draws from the layer's rng (so RNG-stream independence is observable).
    """

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        return Obs(start=Seed.model_validate(cfg.state).start, draw=int(rng.integers(0, 10**9)))

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        total = sum(a.x for a in actions.values() if isinstance(a, Act))
        return Rec(payload=Seed.model_validate(cfg.state).start + total)

    def utilities(cfg: Config, out: BaseModel) -> dict[AgentId, float]:
        return {agent: 1.0 for agent in sorted(set(cfg.roles.values()))}

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=Seed),
        exits={Port("done"): Rec},
        observe=observe,
        action_schema=lambda cfg, role: Act,
        outcome=outcome,
        utilities=utilities,
        export=lambda cfg, out: Exit(port=Port("done"), record=out.model_dump(mode="json")),
    )


def to_n() -> Connector:
    """``Rec`` record -> ``N`` state, no roles."""

    def fn(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        return Config(state={"n": rec["payload"]}, roles={})

    return Connector(fn, N)


def rec_to_seed(roles: dict[str, str]) -> Connector:
    """``Rec`` record -> ``Seed`` state (start = payload) with the given role assignment."""
    assignment = {RoleId(r): AgentId(a) for r, a in roles.items()}

    def fn(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        return Config(state={"start": rec["payload"]}, roles=dict(assignment))

    return Connector(fn, Seed)


class Agents(BaseModel):
    """Entry state of the test Par: branch name -> agents holding roles in that branch."""

    agents: dict[str, list[str]]


def split_agents(cfg: Config) -> dict[str, Config]:
    """One ``Seed`` config per branch; role ``player`` (or ``player:i`` for several agents)."""
    state = Agents.model_validate(cfg.state)
    out: dict[str, Config] = {}
    for branch, agents in state.agents.items():
        roles = {
            RoleId("player" if len(agents) == 1 else f"player:{i}"): AgentId(a)
            for i, a in enumerate(agents)
        }
        out[branch] = Config(state={"start": 0}, roles=roles)
    return out
