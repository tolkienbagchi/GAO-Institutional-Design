"""Tutorial 1: define a layer. One round of a public-goods game."""

from typing import Any

from numpy.random import Generator
from pydantic import BaseModel, Field, create_model

from gao_idl import (
    Config,
    Contract,
    DefaultAction,
    Primitive,
    RandomAgent,
    Session,
    SimpleLayer,
    run_sync,
)
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Exit

AGAIN, DONE = Port("again"), Port("done")


class PGState(BaseModel):
    """Entry state: endowment per role, the pot multiplier and where we are in a repeated game."""

    endowments: dict[str, float]
    multiplier: float
    round: int = 0
    rounds: int = 1


class PGObs(BaseModel):
    """What a player sees: its own endowment and the rules, never anybody's contribution."""

    endowment: float
    multiplier: float
    players: int
    round: int


class PGOutcome(BaseModel):
    """The resolved round. It is also the exit record, so a connector can read it."""

    contributions: dict[str, float]
    share: float
    payoffs: dict[str, float]
    endowments: dict[str, float]
    multiplier: float
    round: int
    rounds: int


class PGReport(BaseModel):
    """What a player is told afterwards."""

    own_contribution: float
    total_contribution: float
    own_payoff: float


def _state(cfg: Config) -> PGState:
    return PGState.model_validate(cfg.state)


def _action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
    # the bound depends on the state, so the model is built per call
    cap = _state(cfg).endowments[role]
    return create_model("Contribution", amount=(float, Field(ge=0.0, le=cap)))


def _outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: Any
) -> PGOutcome:
    s = _state(cfg)
    given: dict[str, float] = {str(r): float(a.model_dump()["amount"]) for r, a in actions.items()}
    contributions = {r: given.get(r, 0.0) for r in s.endowments}  # a dropped role gives nothing
    share = s.multiplier * sum(contributions.values()) / len(s.endowments)
    payoffs = {r: s.endowments[r] - contributions[r] + share for r in s.endowments}
    return PGOutcome(
        contributions=contributions,
        share=share,
        payoffs=payoffs,
        round=s.round,
        endowments=s.endowments,
        multiplier=s.multiplier,
        rounds=s.rounds,
    )


def _export(cfg: Config, out: BaseModel) -> Exit:
    o = PGOutcome.model_validate(out)
    return Exit(port=AGAIN if o.round + 1 < o.rounds else DONE, record=o.model_dump(mode="json"))


def public_goods_layer(players: int) -> SimpleLayer:
    """One round: every player splits its endowment between keeping and contributing."""
    roles: dict[RoleId, dict[str, Any]] = {RoleId(f"p{i}"): {} for i in range(players)}
    return SimpleLayer(
        name="public_goods",
        entry=Contract(state_schema=PGState, roles=roles),
        exits={AGAIN: PGOutcome, DONE: PGOutcome},
        timeout=DefaultAction(),
        observe=lambda cfg, role, rng: PGObs(
            endowment=_state(cfg).endowments[role],
            multiplier=_state(cfg).multiplier,
            players=players,
            round=_state(cfg).round,
        ),
        action_schema=_action_schema,
        default_action=lambda cfg, role: _action_schema(cfg, role)(amount=0.0),
        outcome=_outcome,
        utilities=lambda cfg, out: {
            cfg.roles[RoleId(r)]: v for r, v in PGOutcome.model_validate(out).payoffs.items()
        },
        export=_export,
        report=lambda cfg, out, who, rng: _report(cfg, PGOutcome.model_validate(out), who),
    )


def _report(cfg: Config, o: PGOutcome, who: AgentId) -> PGReport:
    role = next(r for r, a in cfg.roles.items() if a == who)
    return PGReport(
        own_contribution=o.contributions[role],
        own_payoff=o.payoffs[role],
        total_contribution=sum(o.contributions.values()),
    )


def pg_roles(agents: list[str]) -> dict[RoleId, AgentId]:
    """Role ``p0`` is played by the first agent, and so on."""
    return {RoleId(f"p{i}"): AgentId(a) for i, a in enumerate(agents)}


def build_public_goods(
    agents: list[str], endowment: float = 10.0, multiplier: float = 1.6
) -> tuple[Primitive, Config]:
    """Environment factory (``env_ref = "01_define_a_layer:build_public_goods"``)."""
    roles = pg_roles(agents)
    state = PGState(endowments={r: endowment for r in roles}, multiplier=multiplier)
    return Primitive(public_goods_layer(len(agents))), Config(state=state.model_dump(), roles=roles)


if __name__ == "__main__":
    session = Session.start(
        "01_define_a_layer:build_public_goods", {"agents": ["ann", "bo", "cy"]}, seed=7
    )
    run_sync(session, {AgentId(a): RandomAgent(seed=i) for i, a in enumerate(["ann", "bo", "cy"])})
    print("exit:", session.result.port if session.result else None)
    for agent in ("ann", "bo", "cy"):
        print(agent, round(session.ledger.utility(AgentId(agent)), 2))
