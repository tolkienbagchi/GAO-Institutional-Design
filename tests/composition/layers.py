"""Small layers and connectors used to build composite test environments."""

from dataclasses import replace
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


def counter_layer(name: str = "count", limit: int = 3) -> SimpleLayer:
    """Automatic layer: ``N(n)`` -> ``N(n+1)``; exits ``again`` while n+1 < limit, else ``stop``."""

    def export(cfg: Config, outcome: BaseModel) -> Exit:
        n = N.model_validate(outcome.model_dump()).n
        return Exit(port=Port("again" if n < limit else "stop"), record={"n": n})

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=N),
        exits={Port("again"): N, Port("stop"): N},
        outcome=lambda cfg, actions, rng, store: N(n=N.model_validate(cfg.state).n + 1),
        export=export,
    )


def n_feedback() -> Connector:
    """``N`` record -> ``N`` state (the loop-back connector for ``counter_layer``)."""

    def fn(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        return Config(state={"n": rec["n"]}, roles={})

    return Connector(fn, N)


def accumulate_layer(name: str = "acc", threshold: int = 10) -> SimpleLayer:
    """``decision_layer`` with exits ``again`` / ``stop`` (stop once the payload >= threshold)."""

    def export(cfg: Config, out: BaseModel) -> Exit:
        rec = Rec.model_validate(out.model_dump())
        port = "stop" if rec.payload >= threshold else "again"
        return Exit(port=Port(port), record=rec.model_dump())

    return replace(
        decision_layer(name), exits={Port("again"): Rec, Port("stop"): Rec}, export=export
    )


class Hist(BaseModel):
    n: int
    hist: list[int]


def stash_layer(name: str = "stash", limit: int = 4) -> SimpleLayer:
    """Loop body that accumulates ``n`` into store key ``hist`` (iteration 0 creates it).

    Exits ``again`` with ``{"n"}`` until n+1 reaches ``limit``, then ``stop`` with the history.
    """

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        n = N.model_validate(cfg.state).n
        hist = [] if n == 0 else store.get("hist")
        hist = [*hist, n]
        store.set("hist", hist)
        return Hist(n=n + 1, hist=hist)

    def export(cfg: Config, out: BaseModel) -> Exit:
        h = Hist.model_validate(out.model_dump())
        if h.n < limit:
            return Exit(port=Port("again"), record={"n": h.n})
        return Exit(port=Port("stop"), record=h.model_dump())

    return SimpleLayer(
        name=name,
        entry=Contract(
            state_schema=N, store_reads=frozenset({"hist"}), store_writes=frozenset({"hist"})
        ),
        exits={Port("again"): N, Port("stop"): Hist},
        outcome=outcome,
        export=export,
    )


def snoop_layer(name: str = "snoop", mode: str = "read") -> SimpleLayer:
    """Deliberately broken: touches store key ``secret`` without declaring it."""

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        if mode == "read":
            store.get("secret")
        else:
            store.set("secret", 1)
        return N()

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=N),
        exits={Port("done"): N},
        outcome=outcome,
        export=lambda cfg, out: Exit(port=Port("done"), record={"n": 0}),
    )


def writer_layer(name: str = "writer", key: str = "shared", value: int = 42) -> SimpleLayer:
    """Automatic layer that writes ``value`` to store key ``key`` and exits ``done``."""

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        store.set(key, value)
        return N(n=value)

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=N, store_writes=frozenset({key})),
        exits={Port("done"): N},
        outcome=outcome,
        export=lambda cfg, out: Exit(port=Port("done"), record=out.model_dump()),
    )


def reader_layer(name: str = "reader", key: str = "shared") -> SimpleLayer:
    """Automatic layer that reads store key ``key`` and exits ``done`` with ``{"n": value}``."""

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        return N(n=store.get(key))

    return SimpleLayer(
        name=name,
        entry=Contract(state_schema=N, store_reads=frozenset({key})),
        exits={Port("done"): N},
        outcome=outcome,
        export=lambda cfg, out: Exit(port=Port("done"), record=out.model_dump()),
    )


def no_agents(cfg: Config) -> dict[str, Config]:
    """Splitter function for automatic branches: every branch gets ``N(0)`` and no roles."""
    return {name: Config(state={"n": 0}, roles={}) for name in cfg.state["branches"]}


def fixed_decision_layer(name: str = "dec") -> SimpleLayer:
    """``decision_layer`` with a deterministic ``observe`` (no draws), so it is path-independent."""

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        return Obs(start=Seed.model_validate(cfg.state).start, draw=0)

    return replace(decision_layer(name), observe=observe)


def n_to_seed(roles: dict[str, str]) -> Connector:
    """``N`` record -> ``Seed`` state (start = n) with the given role assignment."""
    assignment = {RoleId(r): AgentId(a) for r, a in roles.items()}

    def fn(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        return Config(state={"start": rec["n"]}, roles=dict(assignment))

    return Connector(fn, Seed)
