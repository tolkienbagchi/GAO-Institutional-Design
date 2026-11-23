"""Playing a library entry with a cast of scripted policies, inside a request and within caps.

``play_cast`` starts a session of the entry's environment (the ``env_ref`` of its spec: never one
a client names), drives it with the synchronous runner and one ``ScriptedAgent`` per agent, and
returns the snapshot and summary of each seed. Each decision is answered by the cast's policy for
the role kind of the role it is addressed to. A ``Budget`` caps the decisions of one run and the
time of the whole request; when a cap is hit the run is abandoned and ``CapExceeded`` is raised,
so nothing is stored. A run is deterministic given ``(entry, params, cast, seed)``.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gao_idl.agents.base import AgentAdapter
from gao_idl.agents.scripted import ScriptedAgent
from gao_idl.authoring.cards import Story, tell_session
from gao_idl.authoring.spec import EnvSpec
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.runtime.runner import run
from gao_idl.runtime.session import Session
from gao_idl.service.library import Cast, Library

MAX_SEEDS = 10
MAX_DECISIONS = 2000
MAX_SECONDS = 10.0


class CapExceeded(Exception):
    """A run request went over one of the caps; nothing of it is kept."""


class RunError(Exception):
    """The environment could not be run with these parameters (the message is plain words)."""


@dataclass
class Budget:
    """What one run request may still spend: decisions per run, seconds in total."""

    clock: Callable[[], float]
    max_decisions: int = MAX_DECISIONS
    max_seconds: float = MAX_SECONDS
    started: float = 0.0
    decisions: int = 0
    hit: str | None = None
    task: "asyncio.Task[Session] | None" = field(default=None, repr=False)

    def start(self) -> None:
        """Start the request's clock."""
        self.started = self.clock()

    def check_time(self) -> None:
        """``CapExceeded`` if the request has used up its seconds."""
        if self.clock() - self.started > self.max_seconds:
            raise CapExceeded(f"the runs took longer than {self.max_seconds:g} seconds in total")

    def spend(self) -> None:
        """Count one decision; on a cap, remember why and cancel the run being driven.

        The decision's own task is cancelled too (``CancelledError``), so nothing is left with
        an unretrieved exception; ``play_cast`` turns the cancellation into ``CapExceeded``.
        """
        self.decisions += 1
        try:
            if self.decisions > self.max_decisions:
                raise CapExceeded(f"a run needed more than {self.max_decisions} decisions")
            self.check_time()
        except CapExceeded as e:
            if self.hit is None:
                self.hit = str(e)
            if self.task is not None:
                self.task.cancel()
            raise asyncio.CancelledError from None


def role_kind(role: str) -> str:
    """The kind of a role id: the text before the first ``:`` (``bidder:0`` -> ``bidder``)."""
    return role.split(":", 1)[0]


def _policy(library: Library, cast: Cast, budget: Budget) -> Any:
    policies = {kind: library.policy(ref) for kind, ref in cast.policies.items()}

    def policy(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
        budget.spend()
        chosen = policies.get(role_kind(str(request.role)))
        if chosen is None:  # a role kind the spec does not declare: the agent does not act
            raise RunError(f"the cast has no policy for role {request.role!r}")
        return chosen(request, reports)

    return policy


class _Adapters(dict[AgentId, AgentAdapter]):
    """One ``ScriptedAgent`` per agent, made the first time the runner asks for that agent.

    Which agents an environment has is known only once it runs (the PD's entry config names
    none), so the mapping fills itself through ``get``, the only way the runner looks one up.
    """

    def __init__(self, make: Callable[[], AgentAdapter]) -> None:
        super().__init__()
        self._make = make

    def get(self, key: AgentId, default: Any = None) -> AgentAdapter:
        """The agent's adapter, made now if it is new."""
        if key not in self:
            self[key] = self._make()
        return self[key]


def _drive(session: Session, adapters: _Adapters, budget: Budget) -> None:
    async def main() -> None:
        task = asyncio.ensure_future(run(session, adapters))
        budget.task = task
        try:
            await task
        except asyncio.CancelledError:
            if budget.hit is not None:
                raise CapExceeded(budget.hit) from None
            raise
        finally:
            budget.task = None
        if budget.hit is not None:  # the cap was hit on the run's last decision
            raise CapExceeded(budget.hit)

    asyncio.run(main())


def summarize_session(session: Session) -> dict[str, Any]:
    """The summary of a run: exit port, whether it failed, and each agent's total payoff."""
    agents = sorted(session.ledger.snapshot()["increments"])
    result = session.result
    return {
        "finished": session.finished,
        "failed": session.failed,
        "exit_port": None if result is None else str(result.port),
        "payoffs": {a: session.ledger.utility(AgentId(a)) for a in agents},
    }


def play_cast(
    library: Library,
    spec: EnvSpec,
    params: dict[str, Any],
    cast: Cast,
    seeds: list[int],
    budget: Budget,
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    """``(seed, snapshot, summary)`` for each seed, or ``CapExceeded`` / ``RunError``.

    ``params`` must already be checked (``resolve_params``); the environment is ``spec.env_ref``.
    """
    if len(seeds) > MAX_SEEDS:
        raise CapExceeded(f"at most {MAX_SEEDS} seeds per request")
    budget.start()
    out = []
    for seed in seeds:
        budget.decisions = 0
        budget.check_time()
        try:
            session = Session.start(spec.env_ref, params, seed)
        except (GaoError, ValueError, TypeError) as e:
            raise RunError(f"the environment cannot be built with these parameters: {e}") from e
        adapters = _Adapters(lambda: ScriptedAgent(_policy(library, cast, budget)))
        try:
            _drive(session, adapters, budget)
        except CapExceeded:
            raise
        except (GaoError, ValueError, RuntimeError) as e:
            raise RunError(f"the run with seed {seed} stopped: {type(e).__name__}: {e}") from e
        budget.check_time()
        out.append((seed, session.snapshot(), summarize_session(session)))
    return out


def replay_run(spec: EnvSpec, params: dict[str, Any], seed: int, log: list[Any]) -> Story:
    """The story of a stored run, replayed from its input log with the library's ``env_ref``."""
    session = Session.restore({"env_ref": spec.env_ref, "params": params, "seed": seed, "log": log})
    return tell_session(session, spec)
