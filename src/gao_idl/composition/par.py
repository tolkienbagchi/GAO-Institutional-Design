"""Par: independent parallel branches over disjoint agents, joined when all have exited."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, ValidationError

from gao_idl.composition.process import (
    Continue,
    Ctx,
    Exited,
    FailedResult,
    Frame,
    Pending,
    Process,
    StepResult,
)
from gao_idl.composition.routing import check_name, owns
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.layer import Contract
from gao_idl.core.paths import Branch, child
from gao_idl.core.schemas import Config, DecisionRequest, Exit, FrozenModel


class OpenState(BaseModel):
    """A state schema that accepts any fields (default ``input_schema`` of DisjointSplitter)."""

    model_config = ConfigDict(extra="allow")


class Splitter(Protocol):
    """Maps a Par's entry Config to one Config per branch."""

    @property
    def input_schema(self) -> type[BaseModel]:
        """Schema the entry ``Config.state`` must satisfy."""
        ...

    def split(self, cfg: Config) -> dict[str, Config]:
        """Pure function of ``cfg``; keys must be exactly the Par's branch names."""
        ...


class Joiner(Protocol):
    """Combines the exits of all branches into the Par's single exit."""

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """The Par's exit ports (a Par exits only through its joiner)."""
        ...

    def join(self, exits: dict[str, Exit]) -> Exit:
        """Pure function of the branch exits, given in sorted branch order."""
        ...


@dataclass(frozen=True)
class DisjointSplitter:
    """Wraps ``fn(cfg) -> {branch: Config}`` and rejects any agent appearing in two branches."""

    fn: Callable[[Config], dict[str, Config]]
    input_schema: type[BaseModel] = OpenState

    def split(self, cfg: Config) -> dict[str, Config]:
        """Run ``fn``; raise ContractError if an agent is assigned to more than one branch."""
        parts = self.fn(cfg)
        owner: dict[AgentId, str] = {}
        for name in sorted(parts):
            for agent in sorted(set(parts[name].roles.values())):
                if owner.setdefault(agent, name) != name:
                    raise ContractError(
                        f"agent {agent!r} appears in branches {owner[agent]!r} and {name!r}; "
                        "DisjointSplitter requires disjoint agent sets"
                    )
        return dict(parts)


@dataclass(frozen=True)
class DictJoiner:
    """Exits on ``port`` with record ``{branch_name: exit.record}``, checked against ``schema``."""

    port: Port
    schema: type[BaseModel]

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """``{port: schema}``."""
        return {self.port: self.schema}

    def join(self, exits: dict[str, Exit]) -> Exit:
        """Collect the branch records; ContractError if they violate ``schema``."""
        record = {name: exit_.record for name, exit_ in exits.items()}
        try:
            self.schema.model_validate(record)
        except ValidationError as e:
            raise ContractError(f"joined record violates {self.schema.__name__}: {e}") from e
        return Exit(port=self.port, record=record)


Input = Annotated[ev.Submitted | ev.TimedOut, Field(discriminator="kind")]


class ParFrame(FrozenModel):
    """Data-only state of a Par: one nested frame per branch, plus the exits seen so far.

    ``backlog`` holds inputs not yet applied (they are applied one per step, in log order, so the
    trace depends only on the input log and not on how it was batched into ``advance()`` calls);
    ``failed`` makes failure sticky. Both are additions to the brief's fields.
    """

    children: dict[str, SerializeAsAny[Frame]]
    exits: dict[str, Exit] = {}
    backlog: list[Input] = []
    failed: str | None = None


def _narrow(frame: Frame) -> ParFrame:
    if not isinstance(frame, ParFrame):
        raise TypeError(f"Par cannot run a frame of type {type(frame).__name__}")
    return frame


@dataclass(frozen=True, eq=False)
class Par:
    """Branches run under ``child(path, Branch(name))``, are stepped in sorted-name order, and
    advance independently. All must exit before ``join``. Branches share the session store.

    The Par emits no trace events of its own.
    """

    branches: dict[str, Process]
    split: Splitter
    join: Joiner
    name: str = "par"
    _entry: Contract = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "branches", dict(self.branches))
        from gao_idl.composition import contracts  # lazy: contracts imports the composites

        contracts.check(self)
        reads: set[str] = set()
        writes: set[str] = set()
        for proc in self.branches.values():
            reads |= proc.entry.store_reads
            writes |= proc.entry.store_writes
        object.__setattr__(
            self,
            "_entry",
            Contract(
                state_schema=self.split.input_schema,
                store_reads=frozenset(reads),
                store_writes=frozenset(writes),
            ),
        )

    def _children(self) -> list[tuple[str, Process]]:
        """Ordered ``(branch name, process)`` pairs (sorted by name)."""
        return [(n, self.branches[n]) for n in sorted(self.branches)]

    def validate_wiring(self) -> None:
        """Local (non-recursive) wiring checks; raises ContractError. Called by contracts.check."""
        check_name("par", self.name)
        if not self.branches:
            raise ContractError(f"par {self.name!r} has no branches")
        for branch in self.branches:
            check_name("branch", branch)
        if not self.join.exits:
            raise ContractError(f"par {self.name!r}: the joiner declares no exit ports")

    # ---------------------------------------------------------------- Process

    @property
    def entry(self) -> Contract:
        """Entry contract: the splitter's ``input_schema``, store scope = union over branches."""
        return self._entry

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """The joiner's exits."""
        return dict(self.join.exits)

    def _branch_ctx(self, ctx: Ctx, name: str) -> Ctx:
        return ctx.with_path(child(ctx.path, Branch(name)))

    def start(self, cfg: Config, ctx: Ctx) -> ParFrame:
        """Split ``cfg`` and start every branch; emits nothing."""
        try:
            self.split.input_schema.model_validate(cfg.state)
        except ValidationError as e:
            raise ContractError(f"par {self.name!r}: invalid entry state: {e}") from e
        parts = self.split.split(cfg)
        if set(parts) != set(self.branches):
            raise ContractError(
                f"par {self.name!r}: splitter returned branches {sorted(parts)}, "
                f"expected {sorted(self.branches)}"
            )
        children = {
            name: proc.start(parts[name], self._branch_ctx(ctx, name))
            for name, proc in self._children()
        }
        return ParFrame(children=children)

    def validate(
        self, frame: Frame, decision_id: DecisionId, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        """Ask each live branch in turn; only the branch that issued the decision knows it."""
        f = _narrow(frame)
        for name, proc in self._children():
            if name in f.exits:
                continue
            try:
                return proc.validate(f.children[name], decision_id, agent_id, action)
            except UnknownDecision:
                continue
        raise UnknownDecision(decision_id)

    def step(
        self, frame: Frame, inputs: list[ev.Submitted | ev.TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        """Apply at most one input, step every live branch once, or join when all have exited."""
        f = _narrow(frame)
        if f.failed is not None:
            return f, FailedResult(reason=f.failed)
        backlog = [*f.backlog, *inputs]
        live = [n for n, _ in self._children() if n not in f.exits]
        if not live:
            if backlog:
                raise UnknownDecision(backlog[0].decision_id)
            return self._join(f)
        routed: dict[str, list[ev.Submitted | ev.TimedOut]] = {}
        if backlog:
            inp = backlog.pop(0)
            owner = next(
                (n for n in live if owns(child(ctx.path, Branch(n)), inp.decision_id)), None
            )
            if owner is None:
                raise UnknownDecision(inp.decision_id)
            routed[owner] = [inp]
        children = dict(f.children)
        exits = dict(f.exits)
        requests: list[DecisionRequest] = []
        progressed = False
        for name in live:
            new, result = self.branches[name].step(
                children[name], routed.get(name, []), self._branch_ctx(ctx, name)
            )
            children[name] = new
            if isinstance(result, FailedResult):
                reason = f"branch {name!r}: {result.reason}"
                failed = f.model_copy(
                    update={"children": children, "exits": exits, "backlog": [], "failed": reason}
                )
                return failed, FailedResult(reason=reason)
            if isinstance(result, Exited):
                exits[name] = result.exit
            elif isinstance(result, Continue):
                progressed = True
            elif isinstance(result, Pending):
                requests.extend(result.requests)
        nxt = f.model_copy(update={"children": children, "exits": exits, "backlog": backlog})
        if progressed or backlog or len(exits) == len(self.branches):
            return nxt, Continue()
        return nxt, Pending(requests=requests)

    def _join(self, f: ParFrame) -> tuple[Frame, StepResult]:
        exit_ = self.join.join({n: f.exits[n] for n, _ in self._children()})
        if exit_.port not in self.exits:
            raise ContractError(
                f"par {self.name!r}: joiner exited through undeclared {exit_.port!r}"
            )
        return f, Exited(exit=exit_)
