"""Loop: re-run a body process, feeding selected exit ports back, until an ``until`` port fires."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, SerializeAsAny

from gao_idl.composition.connector import Connector
from gao_idl.composition.process import (
    Continue,
    Ctx,
    Exited,
    FailedResult,
    Frame,
    Process,
    StepResult,
)
from gao_idl.composition.routing import check_name, owns, store_failure_reason
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.layer import Contract
from gao_idl.core.paths import LoopIter, child
from gao_idl.core.schemas import Config, Exit, FrozenModel

TRUNCATED = Port("truncated")
"""Reserved exit port of every Loop; only truncation ever uses it."""


class TruncatedExit(BaseModel):
    """Record of the reserved ``truncated`` port."""

    iterations: int
    last_exit: dict[str, Any]


class LoopFrame(FrozenModel):
    """Data-only state of a Loop: iteration index and the body's nested frame.

    ``exited_port`` / ``exited_record`` park the body's exit until the next step handles it;
    ``result`` / ``failed`` make termination idempotent (nothing is re-emitted if stepped again).
    The last four are additions to the brief's ``(i, child)``.
    """

    i: int
    child: SerializeAsAny[Frame]
    exited_port: Port | None = None
    exited_record: dict[str, Any] = {}
    result: Exit | None = None
    failed: str | None = None


def _narrow(frame: Frame) -> LoopFrame:
    if not isinstance(frame, LoopFrame):
        raise TypeError(f"Loop cannot run a frame of type {type(frame).__name__}")
    return frame


@dataclass(frozen=True, eq=False)
class Loop:
    """Run ``body`` at ``child(path, LoopIter(i))``, ``i = 0, 1, …``.

    Every exit port of the body is in exactly one of ``feedback`` (connector builds the next
    iteration's entry Config) or ``until`` (the loop exits, on the mapped port, with the same
    record). If feeding back would start iteration ``max_iter``: ``on_limit="truncate"`` emits
    ``Truncated`` and exits on the reserved port ``"truncated"`` with
    ``{"iterations": max_iter, "last_exit": record}``; ``"fail"`` emits ``Failed`` and fails.
    Normal termination never uses the reserved port. The feedback connector is not run at the
    limit. The Loop emits no other trace events of its own.
    """

    body: Process
    feedback: dict[Port, Connector]
    until: dict[Port, Port]
    max_iter: int
    on_limit: Literal["truncate", "fail"]
    name: str = "loop"
    _entry: Contract = field(init=False, repr=False)
    _exits: dict[Port, type[BaseModel]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "feedback", dict(self.feedback))
        object.__setattr__(self, "until", dict(self.until))
        from gao_idl.composition import contracts  # lazy: contracts imports the composites

        contracts.check(self)
        object.__setattr__(self, "_exits", self.compute_exits())
        entry = self.body.entry
        reads, writes = set(entry.store_reads), set(entry.store_writes)
        for connector in self.feedback.values():
            reads |= connector.store_reads
            writes |= connector.store_writes
        object.__setattr__(
            self,
            "_entry",
            Contract(
                state_schema=entry.state_schema,
                roles=entry.roles,
                store_reads=frozenset(reads),
                store_writes=frozenset(writes),
            ),
        )

    # ------------------------------------------------------------ construction

    def _children(self) -> list[tuple[str, Process]]:
        """The body, as the single ``(name, process)`` pair."""
        return [(self.body.name, self.body)]

    def validate_wiring(self) -> None:
        """Local (non-recursive) wiring checks; raises ContractError. Called by contracts.check."""
        check_name("loop", self.name)
        if isinstance(self.max_iter, bool) or not isinstance(self.max_iter, int):
            raise ContractError(f"loop {self.name!r}: max_iter must be an int")
        if self.max_iter < 1:
            raise ContractError(f"loop {self.name!r}: max_iter must be >= 1, got {self.max_iter}")
        if self.on_limit not in ("truncate", "fail"):
            raise ContractError(f"loop {self.name!r}: on_limit must be 'truncate' or 'fail'")
        exits = set(self.body.exits)
        both = sorted(set(self.feedback) & set(self.until))
        if both:
            raise ContractError(f"loop {self.name!r}: ports {both} are in both feedback and until")
        unknown = sorted((set(self.feedback) | set(self.until)) - exits)
        if unknown:
            raise ContractError(
                f"loop {self.name!r}: ports {unknown} are not exits of {self.body.name!r} "
                f"(exits: {sorted(exits)})"
            )
        unrouted = sorted(exits - set(self.feedback) - set(self.until))
        if unrouted:
            raise ContractError(
                f"loop {self.name!r}: body exit ports {unrouted} are in neither feedback nor until"
            )
        if TRUNCATED in self.until.values():
            raise ContractError(
                f"loop {self.name!r}: {TRUNCATED!r} is reserved for truncation and cannot be an "
                "until target"
            )
        for port, connector in sorted(self.feedback.items()):
            if connector.output_schema is not self.body.entry.state_schema:
                raise ContractError(
                    f"loop {self.name!r}: feedback connector for port {port!r} outputs "
                    f"{connector.output_schema.__name__} but {self.body.name!r} expects "
                    f"{self.body.entry.state_schema.__name__}"
                )

    def compute_exits(self) -> dict[Port, type[BaseModel]]:
        """Exit ports and schemas implied by the wiring; ContractError on conflicting schemas."""
        exits: dict[Port, type[BaseModel]] = {}
        for port in sorted(self.until):
            target, schema = self.until[port], self.body.exits[port]
            if target in exits and exits[target] is not schema:
                raise ContractError(
                    f"loop {self.name!r}: until target {target!r} has conflicting schemas "
                    f"{exits[target].__name__} and {schema.__name__}"
                )
            exits[target] = schema
        exits[TRUNCATED] = TruncatedExit
        return exits

    # ---------------------------------------------------------------- Process

    @property
    def entry(self) -> Contract:
        """The body's entry contract, with the store scope widened by the feedback connectors."""
        return self._entry

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """The ``until`` targets plus the reserved ``truncated`` port."""
        return self._exits

    def start(self, cfg: Config, ctx: Ctx) -> LoopFrame:
        """Start iteration 0 under ``child(ctx.path, LoopIter(0))``; emits nothing."""
        frame = self.body.start(cfg, ctx.with_path(child(ctx.path, LoopIter(0))))
        return LoopFrame(i=0, child=frame)

    def validate(
        self, frame: Frame, decision_id: DecisionId, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        """Delegate to the body of the current iteration."""
        f = _narrow(frame)
        if f.failed is not None or f.result is not None or f.exited_port is not None:
            raise UnknownDecision(decision_id)
        return self.body.validate(f.child, decision_id, agent_id, action)

    def step(
        self, frame: Frame, inputs: list[ev.Submitted | ev.TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        """Do one thing: step the body once, or handle its exit (feed back, exit, or truncate)."""
        f = _narrow(frame)
        if f.failed is not None:
            return f, FailedResult(reason=f.failed)
        if f.result is not None:
            return f, Exited(exit=f.result)
        if f.exited_port is not None:
            if inputs:
                raise UnknownDecision(inputs[0].decision_id)
            return self._handle_exit(f, ctx)
        iter_path = child(ctx.path, LoopIter(f.i))
        for inp in inputs:
            if not owns(iter_path, inp.decision_id):
                raise UnknownDecision(inp.decision_id)
        new_child, result = self.body.step(f.child, inputs, ctx.with_path(iter_path))
        f = f.model_copy(update={"child": new_child})
        if isinstance(result, Exited):
            parked = f.model_copy(
                update={"exited_port": result.exit.port, "exited_record": result.exit.record}
            )
            return parked, Continue()
        if isinstance(result, FailedResult):
            return f.model_copy(update={"failed": result.reason}), result
        return f, result  # Pending or Continue

    def _handle_exit(self, f: LoopFrame, ctx: Ctx) -> tuple[Frame, StepResult]:
        assert f.exited_port is not None
        port, record = f.exited_port, f.exited_record
        if port in self.until:
            done = Exit(port=self.until[port], record=record)
            return f.model_copy(update={"result": done}), Exited(exit=done)
        next_i = f.i + 1
        if next_i >= self.max_iter:
            reason = f"loop {self.name!r} reached max_iter={self.max_iter}"
            if self.on_limit == "fail":
                ctx.trace.emit(ev.Failed(t=ctx.clock.tick(), path=list(ctx.path), reason=reason))
                return f.model_copy(update={"failed": reason}), FailedResult(reason=reason)
            ctx.trace.emit(ev.Truncated(t=ctx.clock.tick(), path=list(ctx.path), reason=reason))
            cut = Exit(port=TRUNCATED, record={"iterations": next_i, "last_exit": record})
            return f.model_copy(update={"result": cut}), Exited(exit=cut)
        try:
            cfg = self.feedback[port].apply(record, ctx)
        except (KeyError, PermissionError) as e:
            reason = store_failure_reason(f"feedback connector for port {port!r}", e)
            ctx.trace.emit(ev.Failed(t=ctx.clock.tick(), path=list(ctx.path), reason=reason))
            return f.model_copy(update={"failed": reason}), FailedResult(reason=reason)
        child_ctx = ctx.with_path(child(ctx.path, LoopIter(next_i)))
        return LoopFrame(i=next_i, child=self.body.start(cfg, child_ctx)), Continue()
