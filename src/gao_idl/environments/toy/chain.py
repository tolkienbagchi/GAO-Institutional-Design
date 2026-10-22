"""Deterministic three-state chain: ``left`` / ``right`` for ``horizon`` steps (default 3).

States are 0, 1, 2 and the player starts in 0. ``right`` moves one state up, ``left`` one state
down, both clamped to [0, 2]. The reward is 1 exactly on the transition state 1 --right--> state 2
(reaching state 2 by going right twice from the start) and 0 otherwise, so the best possible
return within 3 steps is 1. The observation is the state index. After every step the player is
told the step's reward (report field ``own_reward``). The horizon is a ``Loop`` whose body leaves
through ``done`` on the last step; the loop limit is never reached.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any, Literal

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit, FrozenModel
from gao_idl.core.store import StoreView

PLAYER = RoleId("player")
AGAIN, DONE = Port("again"), Port("done")
GOAL = 2


class ChainState(FrozenModel):
    """Entry state of one step: where the player is and which step this is."""

    position: int
    step: int
    horizon: int


class ChainObs(FrozenModel):
    """What the player sees: the state index."""

    state: int


class ChainAction(FrozenModel):
    """Move down or up the chain."""

    move: Literal["left", "right"]


class ChainStepOutcome(FrozenModel):
    """Result of one step; also the exit record through ``again`` / ``done``."""

    position: int
    step: int
    horizon: int
    reward: float


class ChainReport(FrozenModel):
    """What the player learns after a step: the new state and the step's reward."""

    own_reward: float
    position: int


def _outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
) -> BaseModel:
    state = ChainState.model_validate(cfg.state)
    action = actions[PLAYER]
    if not isinstance(action, ChainAction):
        raise TypeError(f"expected ChainAction, got {type(action).__name__}")
    nxt = min(GOAL, state.position + 1) if action.move == "right" else max(0, state.position - 1)
    reward = 1.0 if (state.position == GOAL - 1 and action.move == "right") else 0.0
    return ChainStepOutcome(position=nxt, step=state.step, horizon=state.horizon, reward=reward)


def _step_outcome(out: BaseModel) -> ChainStepOutcome:
    if not isinstance(out, ChainStepOutcome):
        raise TypeError(f"expected ChainStepOutcome, got {type(out).__name__}")
    return out


def _export(cfg: Config, out: BaseModel) -> Exit:
    o = _step_outcome(out)
    port = DONE if o.step + 1 >= o.horizon else AGAIN
    return Exit(port=port, record=o.model_dump(mode="json"))


def _report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
    o = _step_outcome(out)
    return ChainReport(own_reward=o.reward, position=o.position)


def chain_step_layer() -> SimpleLayer:
    """One step of the chain: role ``player``, exits ``again`` or (on the last step) ``done``."""
    return SimpleLayer(
        name="chain_step",
        entry=Contract(state_schema=ChainState, roles={PLAYER: {}}),
        exits={AGAIN: ChainStepOutcome, DONE: ChainStepOutcome},
        observe=lambda cfg, role, rng: ChainObs(
            state=ChainState.model_validate(cfg.state).position
        ),
        action_schema=lambda cfg, role: ChainAction,
        outcome=_outcome,
        utilities=lambda cfg, out: {cfg.roles[PLAYER]: _step_outcome(out).reward},
        export=_export,
        report=_report,
    )


def build_chain(horizon: int = 3, agent: str = "agent") -> tuple[Process, Config]:
    """Environment factory: the chain played for ``horizon`` steps by ``agent``."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ContractError(f"horizon must be an int >= 1, got {horizon!r}")
    if not agent:
        raise ContractError(f"invalid agent id {agent!r}")
    roles = {PLAYER: AgentId(agent)}

    def next_step(rec: dict[str, Any], ledger: Any, store: StoreView) -> Config:
        out = ChainStepOutcome.model_validate(rec)
        state = ChainState(position=out.position, step=out.step + 1, horizon=out.horizon)
        return Config(state=state.model_dump(mode="json"), roles=roles)

    loop = Loop(
        Primitive(chain_step_layer()),
        {AGAIN: Connector(next_step, ChainState)},
        {DONE: DONE},
        max_iter=horizon,
        on_limit="fail",
        name="chain",
    )
    first = ChainState(position=0, step=0, horizon=horizon)
    return loop, Config(state=first.model_dump(mode="json"), roles=roles)
