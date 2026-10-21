"""Environment factories used only by tests (addressable via ``env_ref`` strings)."""

from dataclasses import replace
from typing import Any

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, Empty, SimpleLayer
from gao_idl.core.schemas import Config
from gao_idl.core.timeout import DefaultAction, DropRole
from gao_idl.environments.auction import build_bidding_only
from gao_idl.environments.auction.bidding import Bid, BiddingState, bidding_layer

BIDDING = "gao_idl.environments.auction:build_bidding_only"
DEFAULT_TIMEOUT = "tests.runtime.envs:bidding_default_timeout"
DROP_TIMEOUT = "tests.runtime.envs:bidding_drop_timeout"
LEAKY = "tests.runtime.envs:bidding_leaky"
NONDETERMINISTIC = "tests.runtime.envs:bidding_nondeterministic"
DOUBLE_ROLE = "tests.runtime.envs:bidding_double_role"
AUTOMATIC = "tests.runtime.envs:automatic_observer"


def bidding_default_timeout(values: dict[str, float]) -> tuple[Process, Config]:
    """Bidding where a timed-out bidder bids 0."""
    _, cfg = build_bidding_only(values)
    layer = replace(
        bidding_layer(), timeout=DefaultAction(), default_action=lambda cfg, role: Bid(amount=0)
    )
    return Primitive(layer), cfg


def bidding_drop_timeout(values: dict[str, float]) -> tuple[Process, Config]:
    """Bidding where a timed-out bidder simply does not bid."""
    _, cfg = build_bidding_only(values)
    return Primitive(replace(bidding_layer(), timeout=DropRole())), cfg


class LeakyObs(BaseModel):
    values: dict[AgentId, float]


def bidding_leaky(values: dict[str, float]) -> tuple[Process, Config]:
    """Deliberately broken bidding: every bidder is shown everyone's value."""

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        return LeakyObs(values=BiddingState.model_validate(cfg.state).values)

    _, cfg = build_bidding_only(values)
    return Primitive(replace(bidding_layer(), observe=observe)), cfg


_calls = {"n": 0}


class CounterObs(BaseModel):
    n: int


def bidding_nondeterministic(values: dict[str, float]) -> tuple[Process, Config]:
    """Deliberately broken bidding: observation depends on hidden global state."""

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        _calls["n"] += 1
        return CounterObs(n=_calls["n"])

    _, cfg = build_bidding_only(values)
    return Primitive(replace(bidding_layer(), observe=observe)), cfg


def bidding_double_role(agent: str = "alice") -> tuple[Process, Config]:
    """One agent holding two bidder roles (pending decisions are keyed by role, not agent)."""
    _, cfg = build_bidding_only({agent: 4.0})
    roles = {RoleId("bidder:0"): AgentId(agent), RoleId("bidder:1"): AgentId(agent)}
    return Primitive(bidding_layer()), Config(state=cfg.state, roles=roles)


class Note(BaseModel):
    msg: str


def automatic_observer() -> tuple[Process, Config]:
    """A layer with no roles that reports to a fixed audience."""

    def report(cfg: Config, outcome: BaseModel, who: AgentId, rng: Generator) -> BaseModel | None:
        return Note(msg="hello")

    layer = SimpleLayer(
        name="auto",
        entry=Contract(state_schema=Empty),
        exits={Port("done"): Empty},
        audience=lambda cfg, outcome: {AgentId("observer")},
        report=report,
    )
    return Primitive(layer), Config(state={}, roles={})


def params(**values: float) -> dict[str, Any]:
    """Shorthand for ``{"values": {...}}``."""
    return {"values": dict(values)}
