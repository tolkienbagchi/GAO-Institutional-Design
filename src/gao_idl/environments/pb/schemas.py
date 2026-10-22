"""Data models of the participatory-budgeting pipeline and the generated ballot schema.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

import math
from functools import lru_cache
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    create_model,
    field_validator,
    model_serializer,
)

from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import FrozenModel

RULES = ("greedy", "knapsack", "mes", "phragmen")


class Project(FrozenModel):
    """A nominated project; ``id`` is ``p{index}`` in sorted ``(proposer, title)`` order."""

    id: str
    title: str
    cost: float
    proposer: AgentId


# ------------------------------------------------------------------ stage 1: nomination


def _check_finite_positive(name: str, v: float) -> float:
    if isinstance(v, bool) or not math.isfinite(v) or v <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {v!r}")
    return v


class NominationState(BaseModel):
    """Entry state of the whole institution (``rule`` is validated, not forwarded)."""

    budget: float
    max_cost: float
    voters: list[AgentId]
    proposers: list[AgentId]
    rule: str

    @field_validator("budget", "max_cost")
    @classmethod
    def _positive(cls, v: float, info: ValidationInfo) -> float:
        return _check_finite_positive(str(info.field_name), v)

    @field_validator("voters", "proposers")
    @classmethod
    def _unique(cls, v: list[AgentId], info: ValidationInfo) -> list[AgentId]:
        if len(set(v)) != len(v):
            raise ValueError(f"{info.field_name} contains duplicates")
        return v

    @field_validator("rule")
    @classmethod
    def _known_rule(cls, v: str) -> str:
        if v not in RULES:
            raise ValueError(f"unknown funding rule {v!r} (expected one of {RULES})")
        return v


class NominationObs(BaseModel):
    """What a proposer sees: the money at stake and the size of the electorate."""

    budget: float
    max_cost: float
    n_voters: int


class Nomination(BaseModel):
    """One proposed project: a title and a finite positive cost (strings and bools refused)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    cost: float = Field(gt=0, allow_inf_nan=False, strict=True)

    @field_validator("cost", mode="before")
    @classmethod
    def _reject_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("cost must be a number, not a bool")
        return v


class NominationOutcome(BaseModel):
    """The nominated projects, with ids assigned in sorted ``(proposer, title)`` order."""

    projects: list[Project]


class ProjectsExit(BaseModel):
    """Exit record of nomination (port ``proposed``) and eligibility (port ``eligible``)."""

    budget: float
    max_cost: float
    voters: list[AgentId]
    proposers: list[AgentId]
    projects: list[Project]


class EmptyExit(BaseModel):
    """Exit record of the ports ``none``: nothing to vote on; carries the public audience."""

    reason: Literal["no_nominations", "no_eligible_projects"]
    voters: list[AgentId]
    proposers: list[AgentId]


# ------------------------------------------------------------------ stage 2: eligibility


class Rejection(BaseModel):
    """A project that failed the cost screen, and why."""

    id: str
    reason: Literal["over_budget", "over_max_cost", "over_budget_and_max_cost"]


class ProjectBrief(BaseModel):
    """The public face of a project: id, title and cost (no proposer)."""

    id: str
    title: str
    cost: float


class EligibilityOutcome(BaseModel):
    """Result of the screen: the surviving projects and the rejections."""

    eligible: list[Project]
    rejected: list[Rejection]


class EligibilityReport(BaseModel):
    """Public report, byte-identical for every recipient (``none`` = nothing eligible)."""

    status: Literal["eligible", "none"]
    eligible: list[ProjectBrief]
    rejected: list[Rejection]


# ------------------------------------------------------------------ stage 3: voting


class VotingState(BaseModel):
    """Entry state of voting. ``proposers`` is an addition to the brief (public audience)."""

    budget: float
    projects: list[Project]
    voters: list[AgentId]
    private_values: dict[AgentId, dict[str, float]] | None = None
    proposers: list[AgentId] = []


class VotingObs(BaseModel):
    """What a voter sees: the eligible projects and (if any) their own project values."""

    budget: float
    projects: list[Project]
    own_values: dict[str, float] | None = None


class _BallotBase(BaseModel):
    """Base of every generated ballot model: no duplicates in ``approve``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("approve", check_fields=False)
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("a ballot may not approve the same project twice")
        return v


@lru_cache(maxsize=256)
def _ballot_model(ids: tuple[str, ...]) -> type[BaseModel]:
    item: Any = Literal[ids]
    return create_model(
        "Ballot",
        __base__=_BallotBase,
        approve=(list[item], Field(description="ids of the approved projects, no repeats")),
    )


def ballot_schema(projects: Any) -> type[BaseModel]:
    """Approval-ballot model generated from ``projects``: ``approve`` is a list of eligible ids.

    ``projects`` is any iterable of :class:`Project` or of their JSON dicts. The JSON Schema of
    the result (an ``enum`` of ids) is never written by hand. Unknown or repeated ids fail
    validation.
    """
    ids = tuple(Project.model_validate(p).id for p in projects)
    if not ids:
        raise ValueError("ballot_schema needs at least one project")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate project ids: {ids}")
    return _ballot_model(ids)


class VotingOutcome(BaseModel):
    """Every voter's ballot, each as its approved ids in ascending id order."""

    ballots: dict[AgentId, list[str]]


class VotingReceipt(BaseModel):
    """Private report to a voter: their own ballot and nothing else."""

    own_ballot: list[str]


# ------------------------------------------------------------------ stage 4: funding


class BallotsExit(BaseModel):
    """Exit record of voting and entry state of every funding layer."""

    budget: float
    projects: list[Project]
    ballots: dict[AgentId, list[str]]
    private_values: dict[AgentId, dict[str, float]] | None = None
    proposers: list[AgentId] = []


class FundingOutcome(BaseModel):
    """The funded ids (ascending), the money spent, the rule's name and rule-specific details."""

    funded: list[str]
    spent: float
    rule: str
    details: dict[str, Any]


def _omit_none(data: dict[str, Any], key: str) -> dict[str, Any]:
    if data.get(key) is None:
        data.pop(key, None)
    return data


class FundingPublic(BaseModel):
    """The part of the funding report that is identical for every recipient.

    ``ballots`` is omitted (not null) unless the institution reveals ballots.
    """

    rule: str
    budget: float
    funded: list[ProjectBrief]
    spent: float
    ballots: dict[AgentId, list[str]] | None = None

    @model_serializer(mode="wrap")
    def _omit_hidden_ballots(self, handler: Any) -> dict[str, Any]:
        return _omit_none(handler(self), "ballots")


class FundingPrivate(BaseModel):
    """Per-recipient addendum: the recipient's own value of the funded set."""

    own_value_of_funded: float


class FundingReport(BaseModel):
    """``public`` is identical for all; ``private`` exists only for voters with private values."""

    public: FundingPublic
    private: FundingPrivate | None = None

    @model_serializer(mode="wrap")
    def _omit_missing_private(self, handler: Any) -> dict[str, Any]:
        return _omit_none(handler(self), "private")
