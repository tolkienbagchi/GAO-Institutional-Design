"""Data objects of the repeated prisoner's dilemma."""

from typing import Any, Literal

from pydantic import RootModel, field_validator, model_serializer

from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.schemas import FrozenModel

PAYOFF_KEYS = frozenset({"CC", "CD", "DC", "DD"})
STANDARD_PAYOFFS: dict[str, tuple[float, float]] = {
    "CC": (3.0, 3.0),
    "CD": (0.0, 5.0),
    "DC": (5.0, 0.0),
    "DD": (1.0, 1.0),
}
"""Payoffs as ``(row, col)``; the key is the row's move followed by the column's."""


class PDState(FrozenModel):
    """Entry state of one pair's round.

    ``history`` is an addition to the brief: ``Layer.observe`` has no store access, so the round's
    connector copies the pair's history out of the store (``history_key``) into the state. Each
    entry maps both agents of this pair to their move in one past round, oldest first.
    """

    payoffs: dict[str, tuple[float, float]]
    pair_id: str
    round_index: int
    history_key: str
    history: list[dict[str, str]] = []
    disclose_payoffs: bool = True
    """Whether observations carry the payoff table (the factory's ``disclose_payoffs``)."""

    @field_validator("payoffs")
    @classmethod
    def _four_profiles(cls, v: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        if set(v) != PAYOFF_KEYS:
            raise ValueError(f"payoffs must have exactly the keys {sorted(PAYOFF_KEYS)}")
        return v


class PDObs(FrozenModel):
    """What a player sees: the round, their history with this opponent, and maybe the payoffs.

    ``payoffs`` (the table as ``{"CC": (row, col), ...}``, the same as the state's) is present only
    when the environment discloses it; otherwise the key is absent from the dumped observation,
    not ``None``, so codecs and prompts see no placeholder.
    """

    round_index: int
    own_history: list[tuple[str, str]]  # (own, other) per past round, this pair only
    payoffs: dict[str, tuple[float, float]] | None = None

    @model_serializer(mode="wrap")
    def _omit_undisclosed_payoffs(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("payoffs") is None:
            data.pop("payoffs", None)
        return data


class PDAction(FrozenModel):
    """Cooperate or defect."""

    move: Literal["C", "D"]


class PDOutcome(FrozenModel):
    """Moves by role and payoffs by agent."""

    moves: dict[RoleId, str]
    payoffs: dict[AgentId, float]


class PDReport(FrozenModel):
    """What a player learns after the round: this pair's moves and their own payoff."""

    round_index: int
    own_move: str
    other_move: str
    own_payoff: float


class PDRoundExit(FrozenModel):
    """Exit record of a pair's round.

    ``round_index`` is an addition to the brief: the round counter needs it and the join record
    carries nothing else from which to recover it.
    """

    pair_id: str
    moves: dict[AgentId, str]
    round_index: int


class BranchExit(FrozenModel):
    """One branch of the joined record, as ``DictJoiner`` builds it."""

    port: str
    record: PDRoundExit


class RoundJoin(RootModel[dict[str, BranchExit]]):
    """Joined record of all pairs of a round: ``{branch: {"port": ..., "record": ...}}``."""


class PDRoundInput(FrozenModel):
    """Entry state of a round: its index and the stored history of the pairs playing it."""

    round_index: int
    histories: dict[str, list[dict[str, str]]] = {}


class RoundCounterState(FrozenModel):
    """Entry state of the round counter: what each pair played this round."""

    round_index: int
    rounds: int
    pairs: dict[str, dict[str, str]]  # pair_id -> {agent: move}


class RoundSummary(FrozenModel):
    """Exit record of the round counter (ports ``again`` and ``finished``)."""

    round_index: int
    rounds: int
    pairs: dict[str, dict[str, str]]
