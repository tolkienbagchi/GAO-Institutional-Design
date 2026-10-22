"""The four PB layers: nomination, eligibility, approval voting, and the funding factory."""

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId, role_sort_key
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import DefaultAction, DropRole, Fail, TimeoutPolicy
from gao_idl.environments.pb.rules import within_budget
from gao_idl.environments.pb.schemas import (
    BallotsExit,
    EligibilityOutcome,
    EligibilityReport,
    EmptyExit,
    FundingOutcome,
    FundingPrivate,
    FundingPublic,
    FundingReport,
    Nomination,
    NominationObs,
    NominationOutcome,
    NominationState,
    Project,
    ProjectBrief,
    ProjectsExit,
    Rejection,
    VotingObs,
    VotingOutcome,
    VotingReceipt,
    VotingState,
    ballot_schema,
)

PROPOSED, ELIGIBLE, VOTED, DONE, NONE = (
    Port("proposed"),
    Port("eligible"),
    Port("voted"),
    Port("done"),
    Port("none"),
)

RuleFn = Callable[[Sequence[Project], Mapping[AgentId, Sequence[str]], float], list[str]]


def _brief(projects: Sequence[Project]) -> list[ProjectBrief]:
    return [ProjectBrief(id=p.id, title=p.title, cost=p.cost) for p in projects]


def _public(voters: Sequence[AgentId], proposers: Sequence[AgentId]) -> set[AgentId]:
    return set(voters) | set(proposers)


T = TypeVar("T", bound=BaseModel)


def _typed(model: type[T], value: BaseModel) -> T:
    if not isinstance(value, model):
        raise TypeError(f"expected {model.__name__}, got {type(value).__name__}")
    return value


# ------------------------------------------------------------------ stage 1: nomination


def nomination_layer(timeout: TimeoutPolicy = DropRole()) -> SimpleLayer:  # noqa: B008
    """Roles ``proposer:i``, one nomination (title, cost) each.

    ``timeout`` is ``DropRole`` (the proposer simply does not nominate) or ``Fail``. A
    ``DefaultAction`` is refused: a made-up nomination would be an invented project. Ids ``p{i}``
    go to the nominations in sorted ``(proposer, title)`` order. With no nomination the layer
    exits ``none`` and tells every voter and proposer so in a public report.
    """
    if isinstance(timeout, DefaultAction):
        raise ContractError("nomination cannot use DefaultAction: it would invent a project")

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = NominationState.model_validate(cfg.state)
        return NominationObs(
            budget=state.budget, max_cost=state.max_cost, n_voters=len(state.voters)
        )

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        return Nomination

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        proposals = sorted(
            ((cfg.roles[role], _typed(Nomination, action)) for role, action in actions.items()),
            key=lambda pa: (pa[0], pa[1].title),
        )
        return NominationOutcome(
            projects=[
                Project(id=f"p{i}", title=n.title, cost=n.cost, proposer=agent)
                for i, (agent, n) in enumerate(proposals)
            ]
        )

    def export(cfg: Config, out: BaseModel) -> Exit:
        state = NominationState.model_validate(cfg.state)
        projects = _typed(NominationOutcome, out).projects
        if not projects:
            empty = EmptyExit(
                reason="no_nominations", voters=state.voters, proposers=state.proposers
            )
            return Exit(port=NONE, record=empty.model_dump(mode="json"))
        record = ProjectsExit(
            budget=state.budget,
            max_cost=state.max_cost,
            voters=state.voters,
            proposers=state.proposers,
            projects=projects,
        )
        return Exit(port=PROPOSED, record=record.model_dump(mode="json"))

    def audience(cfg: Config, out: BaseModel) -> set[AgentId]:
        if _typed(NominationOutcome, out).projects:
            return set()
        state = NominationState.model_validate(cfg.state)
        return _public(state.voters, state.proposers)

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        return EligibilityReport(status="none", eligible=[], rejected=[])

    return SimpleLayer(
        name="nomination",
        entry=Contract(state_schema=NominationState),
        exits={PROPOSED: ProjectsExit, NONE: EmptyExit},
        timeout=timeout,
        observe=observe,
        action_schema=action_schema,
        outcome=outcome,
        export=export,
        audience=audience,
        report=report,
    )


# ------------------------------------------------------------------ stage 2: eligibility


def _rejection(project: Project, budget: float, max_cost: float) -> Rejection | None:
    over_budget, over_max = project.cost > budget, project.cost > max_cost
    if over_budget and over_max:
        return Rejection(id=project.id, reason="over_budget_and_max_cost")
    if over_budget:
        return Rejection(id=project.id, reason="over_budget")
    if over_max:
        return Rejection(id=project.id, reason="over_max_cost")
    return None


def eligibility_layer() -> SimpleLayer:
    """Automatic screen: drop projects costing more than the budget or more than ``max_cost``.

    Exits ``eligible`` with the survivors (ids unchanged) or ``none`` if nobody survives. Either
    way it delivers one public report to every voter and proposer, identical for all of them.
    """

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = ProjectsExit.model_validate(cfg.state)
        eligible: list[Project] = []
        rejected: list[Rejection] = []
        for p in state.projects:
            if (r := _rejection(p, state.budget, state.max_cost)) is None:
                eligible.append(p)
            else:
                rejected.append(r)
        return EligibilityOutcome(eligible=eligible, rejected=rejected)

    def export(cfg: Config, out: BaseModel) -> Exit:
        state = ProjectsExit.model_validate(cfg.state)
        eligible = _typed(EligibilityOutcome, out).eligible
        if not eligible:
            empty = EmptyExit(
                reason="no_eligible_projects", voters=state.voters, proposers=state.proposers
            )
            return Exit(port=NONE, record=empty.model_dump(mode="json"))
        record = state.model_copy(update={"projects": eligible})
        return Exit(port=ELIGIBLE, record=record.model_dump(mode="json"))

    def audience(cfg: Config, out: BaseModel) -> set[AgentId]:
        state = ProjectsExit.model_validate(cfg.state)
        return _public(state.voters, state.proposers)

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        result = _typed(EligibilityOutcome, out)
        return EligibilityReport(
            status="eligible" if result.eligible else "none",
            eligible=_brief(result.eligible),
            rejected=result.rejected,
        )

    return SimpleLayer(
        name="eligibility",
        entry=Contract(state_schema=ProjectsExit),
        exits={ELIGIBLE: ProjectsExit, NONE: EmptyExit},
        outcome=outcome,
        export=export,
        audience=audience,
        report=report,
    )


# ------------------------------------------------------------------ stage 3: approval voting


def voting_layer() -> SimpleLayer:
    """Roles ``voter:i``: each approves a subset of the eligible projects.

    The action schema is generated from the entry state by :func:`ballot_schema`, so the
    ``DecisionRequest`` carries an ``enum`` of exactly the eligible ids. The timeout policy is
    ``Fail``. The only report is a private receipt of the voter's own ballot.
    """

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = VotingState.model_validate(cfg.state)
        own: dict[str, float] | None = None
        if state.private_values is not None and cfg.roles[role] in state.private_values:
            ids = {p.id for p in state.projects}
            own = {k: v for k, v in state.private_values[cfg.roles[role]].items() if k in ids}
        return VotingObs(budget=state.budget, projects=state.projects, own_values=own)

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        return ballot_schema(cfg.state["projects"])

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        ballots: dict[AgentId, list[str]] = {}
        for role in sorted(actions, key=role_sort_key):
            approve: list[str] = list(actions[role].model_dump()["approve"])
            ballots[cfg.roles[role]] = sorted(approve, key=role_sort_key)
        return VotingOutcome(ballots=ballots)

    def export(cfg: Config, out: BaseModel) -> Exit:
        state = VotingState.model_validate(cfg.state)
        record = BallotsExit(
            budget=state.budget,
            projects=state.projects,
            ballots=_typed(VotingOutcome, out).ballots,
            private_values=state.private_values,
            proposers=state.proposers,
        )
        return Exit(port=VOTED, record=record.model_dump(mode="json"))

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        return VotingReceipt(own_ballot=_typed(VotingOutcome, out).ballots[recipient])

    return SimpleLayer(
        name="voting",
        entry=Contract(state_schema=VotingState),
        exits={VOTED: BallotsExit},
        timeout=Fail(),
        observe=observe,
        action_schema=action_schema,
        outcome=outcome,
        export=export,
        report=report,
    )


# ------------------------------------------------------------------ stage 4: funding


def funding_layer(name: str, fn: RuleFn, *, reveal_ballots: bool = False) -> SimpleLayer:
    """An automatic funding layer running the pure rule ``fn``; all rules share this one factory.

    The entry contract is :class:`BallotsExit` whatever the rule, so layers are interchangeable.
    The public report (identical for every voter and proposer) lists the funded projects, the
    money spent and the rule; it includes the ballots only if ``reveal_ballots``. A voter with
    private values gets, in a separate ``private`` field, the sum of their values over the funded
    projects (projects they gave no value count 0). Approval counts and other rule details go into
    the outcome (the trace), never into a report. A rule that overspends raises ``ContractError``.
    """

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = BallotsExit.model_validate(cfg.state)
        funded = sorted(fn(state.projects, state.ballots, state.budget), key=role_sort_key)
        known = {p.id for p in state.projects}
        if (bad := [i for i in funded if i not in known]) or len(set(funded)) != len(funded):
            raise ContractError(f"rule {name!r} returned invalid ids: {funded} ({bad})")
        if not within_budget(state.projects, funded, state.budget):
            raise ContractError(f"rule {name!r} overspent the budget {state.budget}")
        by_id = {p.id: p for p in state.projects}
        counts = Counter(i for ballot in state.ballots.values() for i in ballot)
        details: dict[str, Any] = {
            "n_voters": len(state.ballots),
            "approval_counts": {p.id: counts.get(p.id, 0) for p in state.projects},
        }
        return FundingOutcome(
            funded=funded,
            spent=math.fsum(by_id[i].cost for i in funded),
            rule=name,
            details=details,
        )

    def export(cfg: Config, out: BaseModel) -> Exit:
        return Exit(port=DONE, record=_typed(FundingOutcome, out).model_dump(mode="json"))

    def audience(cfg: Config, out: BaseModel) -> set[AgentId]:
        state = BallotsExit.model_validate(cfg.state)
        return _public(list(state.ballots), state.proposers)

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        state = BallotsExit.model_validate(cfg.state)
        result = _typed(FundingOutcome, out)
        by_id = {p.id: p for p in state.projects}
        public = FundingPublic(
            rule=result.rule,
            budget=state.budget,
            funded=_brief([by_id[i] for i in result.funded]),
            spent=result.spent,
            ballots=dict(state.ballots) if reveal_ballots else None,
        )
        private: FundingPrivate | None = None
        if state.private_values is not None and recipient in state.private_values:
            values = state.private_values[recipient]
            private = FundingPrivate(
                own_value_of_funded=math.fsum(values.get(i, 0.0) for i in result.funded)
            )
        return FundingReport(public=public, private=private)

    return SimpleLayer(
        name=f"funding_{name}",
        entry=Contract(state_schema=BallotsExit),
        exits={DONE: FundingOutcome},
        outcome=outcome,
        export=export,
        audience=audience,
        report=report,
    )
