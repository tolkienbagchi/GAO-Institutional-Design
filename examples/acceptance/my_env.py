"""Reference answer to steps 3 and 4 of docs/ACCEPTANCE.md.

A new layer, ``rate`` (every player rates the game from 1 to 5), played after the repeated
prisoner's dilemma: ``Seq(prisoner's dilemma, rate)``.
"""

from typing import Any

from numpy.random import Generator
from pydantic import BaseModel, Field

from gao_idl import Config, Connector, Contract, Primitive, Seq, SimpleLayer
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Exit
from gao_idl.environments.pd import build_repeated_pd

DONE = Port("done")


class RatingState(BaseModel):
    """Entry state: how many rounds were played."""

    rounds: int


class RatingObs(BaseModel):
    """What a rater sees."""

    rounds_played: int


class Rating(BaseModel):
    """The action: a score from 1 to 5."""

    score: int = Field(ge=1, le=5)


class RatingOutcome(BaseModel):
    """All scores, by role; also the exit record."""

    scores: dict[str, int]


class RatingReport(BaseModel):
    """What each rater is told afterwards: the average score."""

    average: float


def _outcome(cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: Any) -> Any:
    return RatingOutcome(scores={str(r): a.model_dump()["score"] for r, a in actions.items()})


def rating_layer(players: int) -> SimpleLayer:
    """One simultaneous round in which every player rates the game."""
    roles: dict[RoleId, dict[str, Any]] = {RoleId(f"rater:{i}"): {} for i in range(players)}
    return SimpleLayer(
        name="rate",
        entry=Contract(state_schema=RatingState, roles=roles),
        exits={DONE: RatingOutcome},
        observe=lambda cfg, role, rng: RatingObs(rounds_played=cfg.state["rounds"]),
        action_schema=lambda cfg, role: Rating,
        outcome=_outcome,
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
        report=lambda cfg, out, who, rng: RatingReport(
            average=sum(RatingOutcome.model_validate(out).scores.values()) / players
        ),
    )


def build(agents: list[str], rounds: int = 3) -> tuple[Seq, Config]:
    """Environment factory: the repeated PD, then the rating layer for the same agents."""
    pd, entry = build_repeated_pd([AgentId(a) for a in agents], rounds)
    raters = {RoleId(f"rater:{i}"): AgentId(a) for i, a in enumerate(sorted(agents))}

    def to_rating(record: dict[str, Any], ledger: Any, store: Any) -> Config:
        return Config(state={"rounds": record["rounds"]}, roles=raters)

    route = (Connector(to_rating, RatingState), Primitive(rating_layer(len(agents))))
    return Seq(pd, {DONE: route}, name="pd_then_rate"), entry
