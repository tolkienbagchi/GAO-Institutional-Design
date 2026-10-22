"""Participatory budgeting: nomination, eligibility, approval voting, funding.

``Seq(nomination) -> Seq(eligibility) -> Seq(voting) -> funding``. The ballot schema of the voting
layer is generated from the eligible projects; the funding layer is one of four interchangeable
automatic layers (:data:`FUNDING`), so swapping the rule is a one-line change.
"""

import math
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from gao_idl.composition.connector import Connector, LedgerReadOnly
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.schemas import Config
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import DropRole, TimeoutPolicy
from gao_idl.environments.pb.layers import (
    ELIGIBLE,
    PROPOSED,
    VOTED,
    eligibility_layer,
    funding_layer,
    nomination_layer,
    voting_layer,
)
from gao_idl.environments.pb.rules import RULE_FUNCTIONS
from gao_idl.environments.pb.schemas import (
    RULES,
    BallotsExit,
    NominationState,
    ProjectsExit,
    VotingState,
)

__all__ = ["FUNDING", "RULES", "build_pb"]

FUNDING = RULE_FUNCTIONS  # rule name -> pure funding rule; the single place the rule is chosen


def _agents(label: str, agents: Any) -> list[AgentId]:
    if not isinstance(agents, list | tuple) or any(not isinstance(a, str) or not a for a in agents):
        raise ContractError(f"{label} must be a list of non-empty agent ids, got {agents!r}")
    if len(set(agents)) != len(agents):
        raise ContractError(f"{label} contains duplicates: {agents!r}")
    return sorted(AgentId(a) for a in agents)


def _positive(label: str, x: Any) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) or x <= 0:
        raise ContractError(f"{label} must be a finite number > 0, got {x!r}")
    return float(x)


def _private_values(
    private_values: Any, voters: list[AgentId]
) -> dict[AgentId, dict[str, float]] | None:
    if private_values is None:
        return None
    if not isinstance(private_values, dict):
        raise ContractError(f"private_values must be a dict or None, got {private_values!r}")
    out: dict[AgentId, dict[str, float]] = {}
    for agent, values in private_values.items():
        if agent not in voters:
            raise ContractError(f"private_values names {agent!r}, who is not a voter")
        if not isinstance(values, dict) or any(
            isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
            for v in values.values()
        ):
            raise ContractError(f"private values of {agent!r} must map project ids to numbers")
        out[AgentId(agent)] = {str(k): float(v) for k, v in values.items()}
    return out


def build_pb(
    proposers: list[str],
    voters: list[str],
    budget: float,
    max_cost: float,
    rule: Literal["greedy", "knapsack", "mes", "phragmen"],
    private_values: dict[str, dict[str, float]] | None = None,
    reveal_ballots: bool = False,
    timeout_nomination: TimeoutPolicy | dict[str, Any] = DropRole(),  # noqa: B008 (frozen model)
) -> tuple[Process, Config]:
    """Environment factory for the four-stage PB institution; the root ``Seq`` is named ``pb``.

    Proposers get roles ``proposer:i`` and voters ``voter:i`` in sorted agent-id order; one agent
    may be both. ``private_values`` maps voter -> project id (``p0``... as assigned by nomination)
    -> value, and is invisible to everyone but that voter. ``timeout_nomination`` (a policy, or its
    JSON dict form) is ``DropRole`` by default; ``Fail`` is allowed, ``DefaultAction`` is refused.
    Exits: ``done`` (a ``FundingOutcome``) or ``none`` (``EmptyExit``, no funding).
    """
    prop_ids, voter_ids = _agents("proposers", proposers), _agents("voters", voters)
    if not voter_ids:
        raise ContractError("an institution needs at least one voter")
    if rule not in RULES:
        raise ContractError(f"unknown funding rule {rule!r} (expected one of {RULES})")
    if not isinstance(reveal_ballots, bool):
        raise ContractError(f"reveal_ballots must be a bool, got {reveal_ballots!r}")
    values = _private_values(private_values, voter_ids)
    try:
        policy: TimeoutPolicy = TypeAdapter(TimeoutPolicy).validate_python(timeout_nomination)
    except ValidationError as e:
        raise ContractError(f"invalid timeout policy {timeout_nomination!r}: {e}") from e
    state = NominationState(
        budget=_positive("budget", budget),
        max_cost=_positive("max_cost", max_cost),
        voters=voter_ids,
        proposers=prop_ids,
        rule=rule,
    )

    def to_eligibility(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        return Config(state=rec, roles={})

    def to_voting(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        screened = ProjectsExit.model_validate(rec)
        vs = VotingState(
            budget=screened.budget,
            projects=screened.projects,
            voters=screened.voters,
            private_values=values,
            proposers=screened.proposers,
        )
        roles = {RoleId(f"voter:{i}"): a for i, a in enumerate(screened.voters)}
        return Config(state=vs.model_dump(mode="json"), roles=roles)

    def to_funding(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        return Config(state=rec, roles={})

    funding = Primitive(funding_layer(rule, FUNDING[rule], reveal_ballots=reveal_ballots))
    voting = Seq(
        Primitive(voting_layer()),
        {VOTED: (Connector(to_funding, BallotsExit), funding)},
        name="after_eligibility",
    )
    screening = Seq(
        Primitive(eligibility_layer()),
        {ELIGIBLE: (Connector(to_voting, VotingState), voting)},
        name="after_nomination",
    )
    root = Seq(
        Primitive(nomination_layer(policy)),
        {PROPOSED: (Connector(to_eligibility, ProjectsExit), screening)},
        name="pb",
    )
    roles = {RoleId(f"proposer:{i}"): a for i, a in enumerate(prop_ids)}
    return root, Config(state=state.model_dump(mode="json"), roles=roles)
