"""Seq: run ``first``, then route its exit port to a continuation through a connector.

Stability: ``Seq`` is public (re-exported by ``gao_idl``); the rest of this module is internal.
"""

from dataclasses import dataclass, field
from typing import Any

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
from gao_idl.core.paths import child
from gao_idl.core.schemas import Config, Exit, FrozenModel
from gao_idl.core.store import StoreScopeError


class SeqFrame(FrozenModel):
    """Data-only state of a Seq (the running child's frame is nested).

    ``stage`` is 0 while ``first`` runs and 1 while a continuation runs; ``route`` is the port of
    ``first`` that selected it. ``exited_port`` / ``exited_record`` hold the running child's exit
    until the next step handles it (one thing per step). ``route``, ``exited_record`` and
    ``failed`` are additions to the brief's fields.
    """

    stage: int
    child: SerializeAsAny[Frame]
    child_name: str
    exited_port: Port | None = None
    exited_record: dict[str, Any] = {}
    route: Port | None = None
    failed: str | None = None


def _narrow(frame: Frame) -> SeqFrame:
    if not isinstance(frame, SeqFrame):
        raise TypeError(f"Seq cannot run a frame of type {type(frame).__name__}")
    return frame


@dataclass(frozen=True, eq=False)
class Seq:
    """``first`` then, per exit port, a connector and a continuation.

    A port of ``first`` that is not routed is re-exported unchanged. ``exits`` is the union of the
    unrouted ports of ``first`` and all exits of all continuations. Children run at
    ``child(ctx.path, child.name)``; the Seq itself emits no trace events.
    """

    first: Process
    routes: dict[Port, tuple[Connector, Process]]
    name: str = "seq"
    _entry: Contract = field(init=False, repr=False)
    _exits: dict[Port, type[BaseModel]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "routes", dict(self.routes))
        from gao_idl.composition import contracts  # lazy: contracts imports the composites

        contracts.check(self)
        object.__setattr__(self, "_exits", self.compute_exits())
        reads, writes = self._store_scope()
        entry = self.first.entry
        object.__setattr__(
            self,
            "_entry",
            Contract(
                state_schema=entry.state_schema,
                roles=entry.roles,
                store_reads=reads,
                store_writes=writes,
            ),
        )

    # ------------------------------------------------------------ construction

    def _children(self) -> list[tuple[str, Process]]:
        """Ordered ``(name, process)`` pairs: ``first``, then continuations by port."""
        return [(self.first.name, self.first)] + [
            (self.routes[p][1].name, self.routes[p][1]) for p in sorted(self.routes)
        ]

    def validate_wiring(self) -> None:
        """Local (non-recursive) wiring checks; raises ContractError. Called by contracts.check."""
        check_name("seq", self.name)
        for name, _ in self._children():
            check_name("seq child", name)
        for port in sorted(self.routes):
            connector, cont = self.routes[port]
            if port not in self.first.exits:
                raise ContractError(
                    f"seq {self.name!r}: route for port {port!r}, which "
                    f"{self.first.name!r} never exits through (exits: {sorted(self.first.exits)})"
                )
            if connector.output_schema is not cont.entry.state_schema:
                raise ContractError(
                    f"seq {self.name!r}: connector for port {port!r} outputs "
                    f"{connector.output_schema.__name__} but {cont.name!r} expects "
                    f"{cont.entry.state_schema.__name__}"
                )
            if cont.name == self.first.name:
                raise ContractError(
                    f"seq {self.name!r}: continuation for port {port!r} has the same name as the "
                    f"first process ({cont.name!r}); decision ids would collide"
                )

    def compute_exits(self) -> dict[Port, type[BaseModel]]:
        """Exit ports and schemas implied by the wiring; ContractError on conflicting schemas."""
        exits: dict[Port, type[BaseModel]] = {}

        def add(port: Port, schema: type[BaseModel], origin: str) -> None:
            if port in exits and exits[port] is not schema:
                raise ContractError(
                    f"seq {self.name!r}: port {port!r} has conflicting schemas "
                    f"{exits[port].__name__} and {schema.__name__} (from {origin!r})"
                )
            exits[port] = schema

        for port, schema in self.first.exits.items():
            if port not in self.routes:
                add(port, schema, self.first.name)
        for route in sorted(self.routes):
            cont = self.routes[route][1]
            for port, schema in cont.exits.items():
                add(port, schema, cont.name)
        return exits

    def _store_scope(self) -> tuple[frozenset[str], frozenset[str]]:
        reads = set(self.first.entry.store_reads)
        writes = set(self.first.entry.store_writes)
        for connector, cont in self.routes.values():
            reads |= connector.store_reads | cont.entry.store_reads
            writes |= connector.store_writes | cont.entry.store_writes
        return frozenset(reads), frozenset(writes)

    # ---------------------------------------------------------------- Process

    @property
    def entry(self) -> Contract:
        """``first``'s entry contract, with the store scope of everything in the Seq."""
        return self._entry

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """Unrouted ports of ``first`` plus all continuation exits."""
        return self._exits

    def _current(self, f: SeqFrame) -> Process:
        if f.stage == 0:
            return self.first
        if f.route is None or f.route not in self.routes:
            raise ValueError(f"seq frame at stage {f.stage} has no valid route")
        return self.routes[f.route][1]

    def start(self, cfg: Config, ctx: Ctx) -> SeqFrame:
        """Start ``first`` under ``child(ctx.path, first.name)``; emits nothing."""
        name = self.first.name
        frame = self.first.start(cfg, ctx.with_path(child(ctx.path, name)))
        return SeqFrame(stage=0, child=frame, child_name=name)

    def validate(
        self, frame: Frame, decision_id: DecisionId, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        """Delegate to the running child."""
        f = _narrow(frame)
        if f.failed is not None or f.exited_port is not None:
            raise UnknownDecision(decision_id)
        return self._current(f).validate(f.child, decision_id, agent_id, action)

    def step(
        self, frame: Frame, inputs: list[ev.Submitted | ev.TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        """Do one thing: step the running child once, or handle its exit."""
        f = _narrow(frame)
        if f.failed is not None:
            return f, FailedResult(reason=f.failed)
        if f.exited_port is not None:
            if inputs:
                raise UnknownDecision(inputs[0].decision_id)
            return self._handle_exit(f, ctx)
        child_path = child(ctx.path, f.child_name)
        for inp in inputs:
            if not owns(child_path, inp.decision_id):
                raise UnknownDecision(inp.decision_id)
        new_child, result = self._current(f).step(f.child, inputs, ctx.with_path(child_path))
        f = f.model_copy(update={"child": new_child})
        if isinstance(result, Exited):
            parked = f.model_copy(
                update={"exited_port": result.exit.port, "exited_record": result.exit.record}
            )
            return parked, Continue()
        if isinstance(result, FailedResult):
            return f.model_copy(update={"failed": result.reason}), result
        return f, result  # Pending or Continue

    def _handle_exit(self, f: SeqFrame, ctx: Ctx) -> tuple[Frame, StepResult]:
        assert f.exited_port is not None
        exit_ = Exit(port=f.exited_port, record=f.exited_record)
        if f.stage == 0 and exit_.port in self.routes:
            connector, cont = self.routes[exit_.port]
            try:
                cfg = connector.apply(exit_.record, ctx)
            except StoreScopeError as e:
                reason = store_failure_reason(f"connector for port {exit_.port!r}", e)
                ctx.trace.emit(ev.Failed(t=ctx.clock.tick(), path=list(ctx.path), reason=reason))
                return f.model_copy(update={"failed": reason}), FailedResult(reason=reason)
            child_ctx = ctx.with_path(child(ctx.path, cont.name))
            nxt = SeqFrame(
                stage=1, child=cont.start(cfg, child_ctx), child_name=cont.name, route=exit_.port
            )
            return nxt, Continue()
        return f, Exited(exit=exit_)
