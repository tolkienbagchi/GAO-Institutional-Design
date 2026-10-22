"""Environment factories used only by tests (addressable via ``env_ref`` strings)."""

from dataclasses import replace
from typing import Any

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import (
    Continue,
    Ctx,
    Exited,
    Frame,
    Pending,
    Process,
    StepResult,
)
from gao_idl.core import events as ev
from gao_idl.core.errors import UnknownDecision
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, Empty, SimpleLayer
from gao_idl.core.schemas import Config, Exit, FrozenModel
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
CONTINUE_TWICE = "tests.runtime.envs:continue_twice"


def bidding_default_timeout(values: dict[str, float]) -> tuple[Process, Config]:
    """Bidding where a timed-out bidder bids 0."""
    _, cfg = build_bidding_only(values)
    layer = replace(
        bidding_layer(), timeout=DefaultAction(), default_action=lambda cfg, role: Bid(amount=0)
    )
    return Primitive(layer), cfg


def bidding_capped(values: dict[str, float]) -> tuple[Process, Config]:
    """Bidding where bids above 10 are infeasible; a timed-out bidder bids 0."""
    _, cfg = build_bidding_only(values)
    layer = replace(
        bidding_layer(),
        timeout=DefaultAction(),
        default_action=lambda cfg, role: Bid(amount=0),
        feasible=lambda cfg, role, action: isinstance(action, Bid) and action.amount <= 10,
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


class StubFrame(FrozenModel):
    n: int = 0
    inputs_seen: list[int] = []


class ContinueTwice:
    """A stub Process that returns ``Continue`` twice and then exits (no decisions)."""

    name = "stub"
    entry = Contract(state_schema=Empty)
    exits: dict[Port, type[BaseModel]] = {Port("done"): Empty}

    def start(self, cfg: Config, ctx: Ctx) -> Frame:
        return StubFrame()

    def validate(
        self, frame: Frame, decision_id: Any, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        raise UnknownDecision(decision_id)

    def step(self, frame: Frame, inputs: list[Any], ctx: Ctx) -> tuple[Frame, StepResult]:
        assert isinstance(frame, StubFrame)
        ctx.trace.emit(ev.LayerEntered(t=ctx.clock.tick(), path=list(ctx.path)))
        nxt = StubFrame(n=frame.n + 1, inputs_seen=[*frame.inputs_seen, len(inputs)])
        if frame.n < 2:
            return nxt, Continue()
        return nxt, Exited(exit=Exit(port=Port("done"), record={"steps": nxt.n}))


def continue_twice() -> tuple[Process, Config]:
    """Environment factory for :class:`ContinueTwice`."""
    return ContinueTwice(), Config(state={}, roles={})


class ContinueN(ContinueTwice):
    """A stub Process that returns ``Continue`` ``limit`` times (forever if None), then exits."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit

    def step(self, frame: Frame, inputs: list[Any], ctx: Ctx) -> tuple[Frame, StepResult]:
        assert isinstance(frame, StubFrame)
        nxt = StubFrame(n=frame.n + 1)
        if self.limit is None or frame.n < self.limit:
            return nxt, Continue()
        return nxt, Exited(exit=Exit(port=Port("done"), record={"steps": nxt.n}))


def continue_n(limit: int | None = None) -> tuple[Process, Config]:
    """Environment factory for :class:`ContinueN`; ``limit=None`` never reaches quiescence."""
    return ContinueN(limit), Config(state={}, roles={})


class Stalled(ContinueTwice):
    """A deliberately broken Process: it is never finished and waits on nothing."""

    def step(self, frame: Frame, inputs: list[Any], ctx: Ctx) -> tuple[Frame, StepResult]:
        return frame, Pending(requests=[])


def stalled() -> tuple[Process, Config]:
    """Environment factory for :class:`Stalled`."""
    return Stalled(), Config(state={}, roles={})
