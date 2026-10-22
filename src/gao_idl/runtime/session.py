"""Resumable session: event-sourced execution of one Process.

The input log is the only source of truth. Everything else (frame, pending set, trace, ledger) is a
fold of the log and is rebuilt exactly by ``Session.replay``.
"""

import copy
import json
from typing import Any

from gao_idl.composition.process import (
    Continue,
    Ctx,
    Exited,
    FailedResult,
    Frame,
    Pending,
    Process,
)
from gao_idl.core import events as ev
from gao_idl.core.errors import (
    DeterminismError,
    GaoError,
    SchemaError,
    SessionFinished,
    UnknownDecision,
)
from gao_idl.core.events import InputEvent, TraceEvent
from gao_idl.core.ids import AgentId, DecisionId
from gao_idl.core.ledger import Ledger
from gao_idl.core.paths import Path
from gao_idl.core.schemas import Config, DecisionRequest, Exit, ParticipantReport
from gao_idl.core.store import Store
from gao_idl.core.trace import LogicalClock
from gao_idl.runtime.registry import resolve

MAX_CONSECUTIVE_CONTINUES = 10_000


class _TraceList:
    """Trace sink appending to a list."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


class Session:
    """A resumable run of one environment. Submissions take effect on the next ``advance()``."""

    def __init__(self, env_ref: str, params: dict[str, Any], seed: int) -> None:
        process, cfg = resolve(env_ref)(**params)
        self._env_ref = env_ref
        self._params = copy.deepcopy(params)
        self._seed = seed
        self._process: Process = process
        self._entry_cfg: Config = cfg
        self._clock = LogicalClock()
        self._sink = _TraceList()
        self._ledger = Ledger()
        self._store = Store(self._clock)
        self._path: Path = (process.name,)
        self._ctx = Ctx(
            root_seed=seed,
            path=self._path,
            ledger=self._ledger,
            store=self._store,
            trace=self._sink,
            clock=self._clock,
        )
        self._log: list[InputEvent] = [
            ev.Started(env_ref=env_ref, params=copy.deepcopy(params), seed=seed)
        ]
        self._frame: Frame = process.start(cfg, self._ctx)
        self._inbox: list[ev.Submitted | ev.TimedOut] = []
        self._pending: dict[DecisionId, DecisionRequest] = {}
        self._finished = False
        self._failed = False
        self._result: Exit | None = None

    # ------------------------------------------------------------ construction

    @classmethod
    def start(cls, env_ref: str, params: dict[str, Any], seed: int) -> "Session":
        """Begin a new session; call ``advance()`` to issue the first decision requests."""
        return cls(env_ref, params, seed)

    @classmethod
    def replay(
        cls, env_ref: str, params: dict[str, Any], seed: int, log: list[InputEvent]
    ) -> "Session":
        """Rebuild a session by folding ``log``; the result has been advanced to quiescence."""
        session = cls(env_ref, params, seed)
        rest = list(log)
        head = rest[0] if rest else None
        if isinstance(head, ev.Started):
            rest.pop(0)
            if (head.env_ref, head.params, head.seed) != (env_ref, params, seed):
                raise DeterminismError("log's Started event does not match env_ref/params/seed")
        for event in rest:
            if isinstance(event, ev.Submitted):
                if event.decision_id not in session._pending:
                    session.advance()
                try:
                    session.submit(event.agent_id, event.decision_id, event.action)
                except GaoError as e:
                    raise DeterminismError(
                        f"replayed submission for {event.decision_id} was rejected: "
                        f"{type(e).__name__}: {e}"
                    ) from e
                continue
            session.advance()
            if isinstance(event, ev.TimedOut):
                try:
                    session.timeout(event.decision_id)
                except GaoError as e:
                    raise DeterminismError(
                        f"replayed timeout for {event.decision_id} failed: {type(e).__name__}: {e}"
                    ) from e
            elif isinstance(event, ev.ExternalResponse):
                session.record_external(event.decision_id, event.payload)
            else:
                raise DeterminismError(f"cannot replay {event.kind!r} event in M0")
        session.advance()
        return session

    # --------------------------------------------------------------- driving

    def advance(self) -> list[TraceEvent]:
        """Run the process on queued inputs until it must wait or ends; return new trace events."""
        if self._finished:
            return []
        start = len(self._sink.events)
        inputs = list(self._inbox)
        continues = 0
        while True:
            frame, result = self._process.step(self._frame, inputs, self._ctx)
            self._frame = frame
            self._inbox.clear()  # consumed by the first step; later steps get no inputs
            inputs = []
            if not isinstance(result, Continue):
                break
            continues += 1
            if continues >= MAX_CONSECUTIVE_CONTINUES:
                raise RuntimeError("process did not reach quiescence")
        if isinstance(result, Pending):
            self._pending = {r.decision_id: r for r in result.requests}
        elif isinstance(result, Exited):
            self._pending = {}
            self._finished = True
            self._result = result.exit
        elif isinstance(result, FailedResult):
            self._pending = {}
            self._finished = True
            self._failed = True
            self._result = None
        return list(self._sink.events[start:])

    def submit(self, agent_id: AgentId, decision_id: DecisionId, action: dict[str, Any]) -> None:
        """Queue a valid action; an invalid one emits ``ActionRejected``, raises, is not logged."""
        request = self._pending.get(decision_id)
        try:
            if self._finished:
                raise SessionFinished("session is finished")
            if request is None:
                raise UnknownDecision(decision_id)
            try:
                json.dumps(action, allow_nan=False)
            except (TypeError, ValueError) as e:
                raise SchemaError(f"action is not strictly JSON-able: {e}") from e
            self._process.validate(self._frame, decision_id, agent_id, action)
        except GaoError as e:
            # Rejections don't tick the clock or enter the input log (replay can't reproduce them).
            path = list(request.path) if request is not None else list(self._path)
            self._sink.emit(
                ev.ActionRejected(
                    t=self._clock.now,
                    path=path,
                    decision_id=decision_id,
                    agent_id=agent_id,
                    reason=type(e).__name__,
                )
            )
            raise
        submitted = ev.Submitted(
            decision_id=decision_id, agent_id=agent_id, action=copy.deepcopy(action)
        )
        self._log.append(submitted)
        self._inbox.append(submitted)
        del self._pending[decision_id]

    def timeout(self, decision_id: DecisionId) -> None:
        """Queue a timeout for a pending decision; the layer's TimeoutPolicy resolves it."""
        if self._finished:
            raise SessionFinished("session is finished")
        if decision_id not in self._pending:
            raise UnknownDecision(decision_id)
        timed_out = ev.TimedOut(decision_id=decision_id)
        self._log.append(timed_out)
        self._inbox.append(timed_out)
        del self._pending[decision_id]

    def record_external(self, decision_id: DecisionId, payload: dict[str, Any]) -> None:
        """Record an external response in the input log; no other effect in M0."""
        self._log.append(
            ev.ExternalResponse(decision_id=decision_id, payload=copy.deepcopy(payload))
        )

    # --------------------------------------------------------------- queries

    def pending(self) -> list[DecisionRequest]:
        """Decisions awaiting a submission or timeout."""
        return list(self._pending.values())

    def pending_for(self, agent_id: AgentId) -> list[DecisionRequest]:
        """Pending decisions addressed to ``agent_id``."""
        return [r for r in self._pending.values() if r.agent_id == agent_id]

    def reports_for(self, agent_id: AgentId) -> list[ParticipantReport]:
        """Reports delivered to ``agent_id`` so far, in delivery order."""
        return [i for i in self._ledger.information(agent_id) if isinstance(i, ParticipantReport)]

    @property
    def finished(self) -> bool:
        """Whether the process exited or failed."""
        return self._finished

    @property
    def failed(self) -> bool:
        """Whether the process failed (``result`` is then None)."""
        return self._failed

    @property
    def result(self) -> Exit | None:
        """The exit, if the process exited."""
        return self._result

    @property
    def input_log(self) -> list[InputEvent]:
        """Copy of the input log (the source of truth)."""
        return list(self._log)

    @property
    def trace(self) -> list[TraceEvent]:
        """Copy of the trace so far."""
        return list(self._sink.events)

    @property
    def ledger(self) -> Ledger:
        """Utility and information ledger."""
        return self._ledger

    # ---------------------------------------------------------- persistence

    def snapshot(self) -> dict[str, Any]:
        """JSON-able snapshot: environment reference plus the input log."""
        return {
            "env_ref": self._env_ref,
            "params": copy.deepcopy(self._params),
            "seed": self._seed,
            "log": ev.dump(self._log),
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "Session":
        """Rebuild a session from ``snapshot`` by replaying its log."""
        loaded = ev.load(snapshot["log"])
        log: list[InputEvent] = []
        for e in loaded:
            if not isinstance(e, ev.INPUT_EVENT_TYPES):
                raise ValueError(f"snapshot log contains a non-input event: {e.kind!r}")
            log.append(e)
        return cls.replay(snapshot["env_ref"], snapshot["params"], snapshot["seed"], log)

    def check_determinism(self) -> None:
        """Replay the input log and require an identical trace and ledger, else DeterminismError.

        ``action_rejected`` events are excluded: rejections are not in the input log, so replay
        cannot reproduce them. Raises ValueError if inputs are queued but not yet advanced, since
        the replay (which always advances) would then legitimately differ.
        """
        if self._inbox:
            raise ValueError("call advance() before check_determinism()")
        other = Session.replay(self._env_ref, self._params, self._seed, self.input_log)
        mine = [d for d in ev.dump(self._sink.events) if d["kind"] != "action_rejected"]
        theirs = [d for d in ev.dump(other._sink.events) if d["kind"] != "action_rejected"]
        for i, (a, b) in enumerate(zip(mine, theirs, strict=False)):
            if a != b:
                raise DeterminismError(f"trace differs from replay at index {i}: {a} != {b}")
        if len(mine) != len(theirs):
            raise DeterminismError(
                f"trace length {len(mine)} != replay length {len(theirs)}; "
                f"first differing index {min(len(mine), len(theirs))}"
            )
        if self._ledger.snapshot() != other._ledger.snapshot():
            raise DeterminismError("ledger differs from replay")
