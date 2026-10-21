"""Primitive: runs a single Layer as a Process (one simultaneous decision round)."""

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from gao_idl.composition.process import Ctx, Exited, FailedResult, Frame, Pending, StepResult
from gao_idl.core import events as ev
from gao_idl.core.errors import (
    ContractError,
    GaoError,
    Infeasible,
    SchemaError,
    UnknownDecision,
    WrongAgent,
)
from gao_idl.core.ids import AgentId, DecisionId, Port, RoleId
from gao_idl.core.layer import Contract, Layer, validate_entry
from gao_idl.core.paths import decision_id as make_decision_id
from gao_idl.core.paths import report_id as make_report_id
from gao_idl.core.rng import stream
from gao_idl.core.schemas import (
    Config,
    DecisionRequest,
    Exit,
    FrozenModel,
    ParticipantReport,
    UtilityIncrement,
)
from gao_idl.core.timeout import DefaultAction, DropRole, Fail


class PrimitiveFrame(FrozenModel):
    """Data-only state of a Primitive; JSON round-trippable.

    ``defaulted`` records roles resolved by ``DefaultAction`` (their action is in ``actions``);
    ``exit`` / ``failed`` make the ``done`` phase idempotent. All three are additions to the brief.
    """

    phase: Literal["init", "collecting", "done"]
    cfg: Config
    requests: dict[DecisionId, DecisionRequest] = {}  # still pending
    actions: dict[RoleId, dict[str, Any]] = {}  # accepted or defaulted, as JSON
    dropped: list[RoleId] = []
    defaulted: list[RoleId] = []
    exit: Exit | None = None
    failed: str | None = None


def _narrow(frame: Frame) -> PrimitiveFrame:
    if not isinstance(frame, PrimitiveFrame):
        raise TypeError(f"Primitive cannot run a frame of type {type(frame).__name__}")
    return frame


class Primitive:
    """A Process that runs one Layer.

    Invariant: every DecisionRequest is issued before any action is accepted, and nothing about an
    action reaches the trace until the layer settles (all roles submitted, defaulted, or dropped).
    """

    def __init__(self, layer: Layer) -> None:
        self.layer = layer

    @property
    def name(self) -> str:
        """The layer's name."""
        return self.layer.name

    @property
    def entry(self) -> Contract:
        """The layer's entry contract."""
        return self.layer.entry

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        """The layer's exit ports."""
        return self.layer.exits

    def start(self, cfg: Config, ctx: Ctx) -> PrimitiveFrame:
        """Initial frame; nothing is emitted until the first ``step``."""
        return PrimitiveFrame(phase="init", cfg=cfg)

    def validate(
        self, frame: Frame, decision_id: DecisionId, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        """Parse ``action`` for a pending decision; see ``Process.validate``."""
        f = _narrow(frame)
        req = f.requests.get(decision_id)
        if req is None:
            raise UnknownDecision(decision_id)
        if req.agent_id != agent_id:
            raise WrongAgent(f"{decision_id} is addressed to {req.agent_id}, not {agent_id}")
        try:
            parsed = self.layer.action_schema(f.cfg, req.role).model_validate(action)
        except ValidationError as e:
            raise SchemaError(str(e)) from e
        if not self.layer.feasible(f.cfg, req.role, parsed):
            raise Infeasible(f"action for {decision_id} is infeasible")
        return parsed

    def step(
        self, frame: Frame, inputs: list[ev.Submitted | ev.TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        """Advance the layer; idempotent once done."""
        f = _narrow(frame)
        if f.phase == "done":
            if f.failed is not None:
                return f, FailedResult(reason=f.failed)
            if f.exit is None:
                raise GaoError("done frame has neither exit nor failure")
            return f, Exited(exit=f.exit)
        if f.phase == "init":
            if inputs:
                raise UnknownDecision(inputs[0].decision_id)
            return self._enter(f, ctx)
        return self._collect(f, inputs, ctx)

    # ------------------------------------------------------------------ phases

    def _enter(self, f: PrimitiveFrame, ctx: Ctx) -> tuple[Frame, StepResult]:
        layer = self.layer
        cfg = validate_entry(layer, f.cfg)
        ctx.trace.emit(ev.LayerEntered(t=ctx.clock.tick(), path=list(ctx.path)))
        if not cfg.roles:
            return self._settle(f.model_copy(update={"cfg": cfg}), ctx)
        requests: dict[DecisionId, DecisionRequest] = {}
        for role in sorted(cfg.roles):
            agent = cfg.roles[role]
            obs = layer.observe(cfg, role, stream(ctx.root_seed, ctx.path, f"observe:{role}"))
            req = DecisionRequest(
                decision_id=make_decision_id(ctx.path, role),
                path=list(ctx.path),
                layer=layer.name,
                role=role,
                agent_id=agent,
                observation=obs.model_dump(mode="json"),
                action_schema=layer.action_schema(cfg, role).model_json_schema(),
            )
            ctx.trace.emit(
                ev.DecisionRequested(t=ctx.clock.tick(), path=list(ctx.path), request=req)
            )
            ctx.ledger.record_information(agent, req)
            requests[req.decision_id] = req
        nxt = f.model_copy(update={"phase": "collecting", "cfg": cfg, "requests": requests})
        return nxt, Pending(requests=list(requests.values()))

    def _collect(
        self, f: PrimitiveFrame, inputs: list[ev.Submitted | ev.TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        layer, cfg = self.layer, f.cfg
        requests = dict(f.requests)
        actions = dict(f.actions)
        dropped = list(f.dropped)
        defaulted = list(f.defaulted)
        for inp in inputs:
            req = requests.get(inp.decision_id)
            if req is None:
                raise UnknownDecision(inp.decision_id)
            if isinstance(inp, ev.Submitted):
                parsed = self.validate(f, inp.decision_id, inp.agent_id, inp.action)  # defensive
                actions[req.role] = parsed.model_dump(mode="json")
            else:
                policy = layer.timeout
                if isinstance(policy, Fail):
                    reason = f"timed out: {inp.decision_id}"
                    ctx.trace.emit(
                        ev.Failed(t=ctx.clock.tick(), path=list(ctx.path), reason=reason)
                    )
                    failed = f.model_copy(
                        update={"phase": "done", "requests": {}, "failed": reason}
                    )
                    return failed, FailedResult(reason=reason)
                if isinstance(policy, DefaultAction):
                    actions[req.role] = layer.default_action(cfg, req.role).model_dump(mode="json")
                    defaulted.append(req.role)
                elif isinstance(policy, DropRole):
                    dropped.append(req.role)
            del requests[inp.decision_id]
        nxt = f.model_copy(
            update={
                "requests": requests,
                "actions": actions,
                "dropped": dropped,
                "defaulted": defaulted,
            }
        )
        if requests:
            return nxt, Pending(requests=list(requests.values()))
        return self._settle(nxt, ctx)

    def _settle(self, f: PrimitiveFrame, ctx: Ctx) -> tuple[Frame, StepResult]:
        """Order is fixed: resolutions, completed, utilities, reports, exited."""
        layer, cfg = self.layer, f.cfg
        path = list(ctx.path)
        # Resolutions are emitted here, in role order, so the trace is a function of the set of
        # resolved actions only (independent of arrival order and of where advance() was called).
        for role in sorted(cfg.roles):
            did = make_decision_id(ctx.path, role)
            t = ctx.clock.tick()
            if role in f.dropped:
                ctx.trace.emit(ev.RoleDropped(t=t, path=path, decision_id=did))
            elif role in f.defaulted:
                ctx.trace.emit(
                    ev.ActionDefaulted(t=t, path=path, decision_id=did, action=f.actions[role])
                )
            else:
                ctx.trace.emit(
                    ev.ActionAccepted(
                        t=t,
                        path=path,
                        decision_id=did,
                        agent_id=cfg.roles[role],
                        action=f.actions[role],
                    )
                )
        parsed: dict[RoleId, BaseModel] = {
            role: layer.action_schema(cfg, role).model_validate(f.actions[role])
            for role in sorted(f.actions)
        }
        view = ctx.store.view(
            layer.entry.store_reads, layer.entry.store_writes, ctx.trace, ctx.path
        )
        outcome = layer.outcome(cfg, parsed, stream(ctx.root_seed, ctx.path, "outcome"), view)
        ctx.trace.emit(
            ev.LayerCompleted(
                t=ctx.clock.tick(),
                path=path,
                layer=layer.name,
                actions={str(role): f.actions[role] for role in sorted(f.actions)},
                outcome=outcome.model_dump(mode="json"),
            )
        )
        for agent, amount in sorted(layer.utilities(cfg, outcome).items()):
            t = ctx.clock.tick()
            inc = UtilityIncrement(agent_id=agent, amount=amount, path=path, t=t)
            ctx.ledger.add_increment(inc)
            ctx.trace.emit(ev.UtilityIncrementEvent(t=t, path=path, increment=inc))
        for recipient in sorted(layer.audience(cfg, outcome)):
            payload = layer.report(
                cfg, outcome, recipient, stream(ctx.root_seed, ctx.path, f"report:{recipient}")
            )
            if payload is None:
                continue
            n = sum(
                1
                for x in ctx.ledger.information(recipient)
                if isinstance(x, ParticipantReport) and x.path == path
            )
            report = ParticipantReport(
                report_id=make_report_id(ctx.path, recipient, n),
                path=path,
                layer=layer.name,
                agent_id=recipient,
                payload=payload.model_dump(mode="json"),
            )
            ctx.trace.emit(ev.ReportDelivered(t=ctx.clock.tick(), path=path, report=report))
            ctx.ledger.record_information(recipient, report)
        exit_ = layer.export(cfg, outcome)
        schema = layer.exits.get(exit_.port)
        if schema is None:
            raise ContractError(
                f"layer {layer.name!r} exported through undeclared port {exit_.port!r}"
            )
        try:
            schema.model_validate(exit_.record)
        except ValidationError as e:
            raise ContractError(
                f"layer {layer.name!r}: exit record violates port schema: {e}"
            ) from e
        ctx.trace.emit(
            ev.Exited(t=ctx.clock.tick(), path=path, port=exit_.port, record=exit_.record)
        )
        done = f.model_copy(update={"phase": "done", "requests": {}, "exit": exit_})
        return done, Exited(exit=exit_)
