"""Runner: drives a Session with a population of adapters, one coroutine, no locks.

Everything that touches the session (``advance``, ``submit``, ``timeout``) happens in the single
coroutine ``run``; adapters run as tasks that only return an action. Adapters are handed deep
copies of ``DecisionRequest`` / ``ParticipantReport`` objects addressed to their own agent, plus the
episode start/end notices, and nothing else (invariant 1).
"""

import asyncio
import copy
import itertools
import logging
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd, ExternalRecorder
from gao_idl.core import events as ev
from gao_idl.core.errors import AgentFailed, Infeasible, SchemaError
from gao_idl.core.ids import AgentId, DecisionId, ReportId
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport
from gao_idl.runtime.session import Session

logger = logging.getLogger(__name__)

TRUNCATED_PORT = "truncated"
MAX_ATTEMPTS = 2  # an infeasible or malformed answer is asked for once more, then timed out


class RunnerPolicy(FrozenModel):
    """How the runner treats time and what it discloses.

    ``deadline_seconds``: per decision, measured with the runner's clock from the first dispatch
    (a retry does not extend it); ``None`` waits forever. ``disclose_final_utility``: whether
    ``EpisodeEnd.final_utility`` carries the agent's total utility. That is a disclosure decision:
    it is the only place the runner reads the ledger, and only when this flag is set.
    """

    deadline_seconds: float | None = None
    disclose_final_utility: bool = False


@dataclass
class _Flight:
    """A dispatched decision: its request, the adapter task, when it started, and which attempt."""

    request: DecisionRequest
    task: "asyncio.Task[dict[str, Any]]"
    started: float
    attempt: int
    seq: int


async def run(
    session: Session,
    adapters: dict[AgentId, AgentAdapter],
    policy: RunnerPolicy = RunnerPolicy(),  # noqa: B008 (frozen model, safe as a default)
    clock: Callable[[], float] = time.monotonic,
) -> Session:
    """Drive ``session`` to the end and return it.

    1. ``episode_start`` for every adapter; reports already in the session (a restored or partly
       run one) are delivered first, so a fresh adapter can catch up.
    2. Until finished: ``advance``, deliver new reports, dispatch ``act`` for each newly pending
       request, wait for the first completion or the earliest deadline, then log what an
       ``ExternalRecorder`` adapter drained (``record_external``, always before the submission or
       timeout it led to), then ``submit`` (retry once
       on ``SchemaError`` / ``Infeasible``, then ``timeout``), ``timeout`` on any other adapter
       exception (preceded by an ``agent_failed`` trace event if it is ``AgentFailed``), and
       ``timeout`` (after cancelling the task) on an expired deadline. A session
       that is neither finished nor waiting on any task raises ``RuntimeError("stalled")``.
    3. ``episode_end`` for every adapter (``terminal="failed"`` if the run dies of an exception or
       is cancelled, after the in-flight tasks are cancelled and awaited; the original exception or
       the cancellation is re-raised).

    ``clock`` must run in the same units as real time (it sets the timeout of the wait).
    """
    started = session.input_log[0]
    if not isinstance(started, ev.Started):
        raise RuntimeError("the session's input log does not begin with Started")
    delivered: set[ReportId] = set()
    flights: dict[DecisionId, _Flight] = {}
    seq = itertools.count()

    async def deliver(reports: list[ParticipantReport]) -> None:
        for report in reports:
            if report.report_id in delivered:
                continue
            adapter = adapters.get(report.agent_id)
            if adapter is not None:
                await adapter.observe(report.model_copy(deep=True))
            delivered.add(report.report_id)

    def dispatch(request: DecisionRequest, attempt: int, began: float | None) -> None:
        adapter = adapters.get(request.agent_id)
        if adapter is None:
            raise RuntimeError(f"no adapter for agent {request.agent_id!r} ({request.decision_id})")
        task = asyncio.ensure_future(adapter.act(request.model_copy(deep=True)))
        flights[request.decision_id] = _Flight(
            request, task, clock() if began is None else began, attempt, next(seq)
        )

    def record_external(agent_id: AgentId) -> None:
        """Log what an external-service adapter obtained, before its action is submitted."""
        adapter = adapters.get(agent_id)
        if isinstance(adapter, ExternalRecorder):
            for item in adapter.drain_external():
                payload = copy.deepcopy(item)
                session.record_external(DecisionId(payload.pop("decision_id")), payload)

    async def cancel_all() -> None:
        """Cancel every in-flight ``act`` task and wait until they are gone."""
        tasks = [f.task for f in flights.values() if not f.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        flights.clear()

    try:
        for agent in sorted(adapters):
            await adapters[agent].episode_start(
                EpisodeContext(agent_id=agent, env_ref=started.env_ref, seed=started.seed)
            )
        await deliver([e.report for e in session.trace if isinstance(e, ev.ReportDelivered)])
        while True:
            new = session.advance()
            await deliver([e.report for e in new if isinstance(e, ev.ReportDelivered)])
            if session.finished:
                break
            for request in session.pending():
                if request.decision_id not in flights:
                    dispatch(request, 1, None)
            if not flights:
                raise RuntimeError("stalled: session is not finished and nothing is pending")
            timeout = None
            if policy.deadline_seconds is not None:
                earliest = min(f.started for f in flights.values()) + policy.deadline_seconds
                timeout = max(0.0, earliest - clock())
            await asyncio.wait(
                [f.task for f in flights.values()],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=timeout,
            )
            for flight in sorted((f for f in flights.values() if f.task.done()), key=_by_seq):
                record_external(flight.request.agent_id)
                _settle(session, flights, flight, dispatch)
            if policy.deadline_seconds is not None:
                now = clock()
                expired = [
                    f
                    for f in sorted(flights.values(), key=_by_seq)
                    if not f.task.done() and now - f.started >= policy.deadline_seconds
                ]
                for flight in expired:
                    flight.task.cancel()
                await asyncio.gather(*(f.task for f in expired), return_exceptions=True)
                for flight in expired:
                    del flights[flight.request.decision_id]
                    record_external(flight.request.agent_id)
                    session.timeout(flight.request.decision_id)
    except (Exception, asyncio.CancelledError):
        # Cancel what is in flight first, then tell every adapter the episode failed (also when the
        # runner itself is cancelled); a failing ``episode_end`` is reported as a warning so the
        # original exception, or the cancellation, is the one that propagates.
        await cancel_all()
        for agent in sorted(adapters):
            try:
                end = EpisodeEnd(agent_id=agent, terminal="failed")
                await adapters[agent].episode_end(end)
            except Exception as e:
                warnings.warn(f"episode_end failed for {agent!r}: {e!r}", stacklevel=2)
        raise
    finally:
        await cancel_all()  # any exit, including cancellation: nothing is left running
    terminal = _terminal(session)
    for agent in sorted(adapters):
        utility = session.ledger.utility(agent) if policy.disclose_final_utility else None
        end = EpisodeEnd(agent_id=agent, terminal=terminal, final_utility=utility)
        await adapters[agent].episode_end(end)
    return session


def _by_seq(flight: _Flight) -> int:
    return flight.seq


def _settle(
    session: Session,
    flights: dict[DecisionId, _Flight],
    flight: _Flight,
    dispatch: Callable[[DecisionRequest, int, float | None], None],
) -> None:
    """Submit a finished task's action, or retry once, or time the decision out."""
    request = flight.request
    del flights[request.decision_id]
    try:
        action = flight.task.result()
    except (Exception, asyncio.CancelledError) as e:  # any adapter failure
        logger.warning(
            "adapter for %s failed on %s: %r; timing the decision out",
            request.agent_id,
            request.decision_id,
            e,
        )
        if isinstance(e, AgentFailed):  # the adapter gave up: say why, then let the policy decide
            session.record_agent_failure(request.decision_id, request.agent_id, e.reasons)
        session.timeout(request.decision_id)
        return
    try:
        session.submit(request.agent_id, request.decision_id, action)
    except (SchemaError, Infeasible) as e:
        if flight.attempt < MAX_ATTEMPTS:
            logger.info(
                "%s rejected for %s (%s); asking again", request.decision_id, request.agent_id, e
            )
            dispatch(request, flight.attempt + 1, flight.started)
        else:
            logger.warning(
                "%s rejected twice for %s; timing out", request.decision_id, request.agent_id
            )
            session.timeout(request.decision_id)


def _terminal(session: Session) -> Literal["normal", "truncated", "failed"]:
    """``failed`` / ``truncated`` / ``normal`` from the end state of a finished session."""
    if session.failed:
        return "failed"
    if session.result is not None and session.result.port == TRUNCATED_PORT:
        return "truncated"
    return "normal"


def run_sync(
    session: Session,
    adapters: dict[AgentId, AgentAdapter],
    policy: RunnerPolicy = RunnerPolicy(),  # noqa: B008
) -> Session:
    """``asyncio.run(run(...))`` with the real clock."""
    return asyncio.run(run(session, adapters, policy))
