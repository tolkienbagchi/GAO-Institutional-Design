"""Bernoulli bandit: one role picks an arm, an automatic settlement draws and reports the reward.

The reward is disclosed to the player in the settlement report as ``own_reward`` (and credited to
the ledger as the same amount). Horizon 1: one pull per session.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import math
from functools import cache
from typing import Any

from numpy.random import Generator
from pydantic import BaseModel, Field, create_model

from gao_idl.composition.connector import Connector
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit, FrozenModel
from gao_idl.core.store import StoreView

PLAYER = RoleId("player")
PULLED, DONE = Port("pulled"), Port("done")


class PullState(FrozenModel):
    """Entry state of the arm choice: the (fixed) success probability of every arm."""

    means: list[float]


class PullObs(FrozenModel):
    """What the player sees: how many arms there are (not their means)."""

    n_arms: int


class PullExit(FrozenModel):
    """Exit record of the arm choice, handed to the settlement."""

    agent: AgentId
    arm: int
    means: list[float]


class SettleState(FrozenModel):
    """Entry state of the settlement: who pulled which arm, and the arms' means."""

    agent: AgentId
    arm: int
    means: list[float]


class SettleOutcome(FrozenModel):
    """The drawn reward (0.0 or 1.0)."""

    agent: AgentId
    arm: int
    reward: float


class BanditReport(FrozenModel):
    """What the player learns: the arm they pulled and their reward."""

    arm: int
    own_reward: float


@cache
def pull_action(n_arms: int) -> type[BaseModel]:
    """The action model ``{"arm": int in [0, n_arms - 1]}`` (cached per ``n_arms``)."""
    model: type[BaseModel] = create_model(
        "PullAction",
        __base__=FrozenModel,
        arm=(int, Field(ge=0, le=n_arms - 1, strict=True)),
    )
    return model


def _pull_layer() -> SimpleLayer:
    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        return PullObs(n_arms=len(PullState.model_validate(cfg.state).means))

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        return pull_action(len(PullState.model_validate(cfg.state).means))

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = PullState.model_validate(cfg.state)
        arm = int(actions[PLAYER].model_dump()["arm"])
        return PullExit(agent=cfg.roles[PLAYER], arm=arm, means=state.means)

    return SimpleLayer(
        name="pull",
        entry=Contract(state_schema=PullState, roles={PLAYER: {}}),
        exits={PULLED: PullExit},
        observe=observe,
        action_schema=action_schema,
        outcome=outcome,
        export=lambda cfg, out: Exit(port=PULLED, record=out.model_dump(mode="json")),
    )


def _settle_layer() -> SimpleLayer:
    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = SettleState.model_validate(cfg.state)
        reward = 1.0 if float(rng.random()) < state.means[state.arm] else 0.0
        return SettleOutcome(agent=state.agent, arm=state.arm, reward=reward)

    def out_model(out: BaseModel) -> SettleOutcome:
        if not isinstance(out, SettleOutcome):
            raise TypeError(f"expected SettleOutcome, got {type(out).__name__}")
        return out

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        o = out_model(out)
        return BanditReport(arm=o.arm, own_reward=o.reward) if recipient == o.agent else None

    return SimpleLayer(
        name="settle",
        entry=Contract(state_schema=SettleState),
        exits={DONE: SettleOutcome},
        outcome=outcome,
        utilities=lambda cfg, out: {out_model(out).agent: out_model(out).reward},
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
        audience=lambda cfg, out: {out_model(out).agent},
        report=report,
    )


def build_bandit(means: list[float], agent: str = "agent") -> tuple[Process, Config]:
    """Environment factory: ``means[i]`` is the success probability of arm ``i`` (reward 0 or 1)."""
    if not means or any(
        isinstance(m, bool)
        or not isinstance(m, int | float)
        or not math.isfinite(m)
        or not 0 <= m <= 1
        for m in means
    ):
        raise ContractError(f"means must be a non-empty list of probabilities, got {means!r}")
    if not agent:
        raise ContractError(f"invalid agent id {agent!r}")

    def to_settle(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        return Config(state=rec, roles={})

    process = Seq(
        Primitive(_pull_layer()),
        {PULLED: (Connector(to_settle, SettleState), Primitive(_settle_layer()))},
        name="bandit",
    )
    state = PullState(means=[float(m) for m in means]).model_dump(mode="json")
    return process, Config(state=state, roles={PLAYER: AgentId(agent)})
