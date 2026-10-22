"""Test-only environment factories for the learning tests (addressed by ``env_ref``)."""

import dataclasses
from typing import Any
from unittest import mock

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.process import Process
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import Config, FrozenModel
from gao_idl.environments.pd import build_repeated_pd
from gao_idl.environments.pd import pd_round_layer as real_pd_round_layer
from gao_idl.environments.pd.layers import _other, _outcome_model
from gao_idl.environments.pd.schemas import PDState


class SilentReport(FrozenModel):
    """A PD report that tells the players the moves but not the payoff."""

    round_index: int
    own_move: str
    other_move: str


def _silent_report(
    cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
) -> BaseModel | None:
    out = _outcome_model(outcome)
    state = PDState.model_validate(cfg.state)
    mine = [role for role, agent in cfg.roles.items() if agent == recipient]
    if len(mine) != 1:
        return None
    return SilentReport(
        round_index=state.round_index,
        own_move=out.moves[mine[0]],
        other_move=out.moves[_other(mine[0])],
    )


def pd_without_payoff_report(**params: Any) -> tuple[Process, Config]:
    """The repeated PD whose reports omit ``own_payoff`` (the ledger still gets the payoffs)."""

    def layer(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(real_pd_round_layer(*args, **kwargs), report=_silent_report)

    with mock.patch("gao_idl.environments.pd.pd_round_layer", layer):
        return build_repeated_pd(**params)
