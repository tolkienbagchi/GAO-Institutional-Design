"""HumanTransportAgent: an in-process stand-in for a UI; actions arrive via ``provide``."""

import asyncio
from typing import Any

from gao_idl.agents.base import EpisodeContext, EpisodeEnd
from gao_idl.core.errors import UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, ReportId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport


class HumanTransportAgent:
    """``act`` waits on a future that outside code resolves with ``provide(decision_id, action)``.

    ``inbox`` collects the reports delivered to this agent, deduplicated by ``report_id`` (a
    resumed run may deliver the same report again). Several requests may be outstanding at once.
    """

    def __init__(self) -> None:
        self.inbox: list[ParticipantReport] = []
        self._seen: set[ReportId] = set()
        self._agent: AgentId | None = None
        self._waiting: dict[DecisionId, tuple[DecisionRequest, asyncio.Future[dict[str, Any]]]] = {}
        self._arrived = asyncio.Event()
        self.closed = False

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """Remember which agent this is; drop earlier reports unless ``keep_episode_memory``."""
        self._agent = ctx.agent_id
        self.closed = False
        if not ctx.keep_episode_memory:
            self.inbox, self._seen = [], set()

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """Wait until ``provide`` answers this request; the request is outstanding meanwhile."""
        if request.decision_id in self._waiting:
            raise RuntimeError(f"{request.decision_id} is already outstanding")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[request.decision_id] = (request, future)
        self._arrived.set()
        try:
            return await future
        except asyncio.CancelledError:  # deadline or dead runner: withdraw the request
            self._waiting.pop(request.decision_id, None)
            raise
        finally:
            self._waiting.pop(request.decision_id, None)

    async def observe(self, report: ParticipantReport) -> None:
        """Append to ``inbox`` unless its ``report_id`` is there; ValueError if not ours."""
        if self._agent is not None and report.agent_id != self._agent:
            raise ValueError(f"report {report.report_id} is addressed to {report.agent_id}")
        if report.report_id not in self._seen:
            self._seen.add(report.report_id)
            self.inbox.append(report)

    async def episode_end(self, end: EpisodeEnd) -> None:
        """The runner is done (or dead): close the transport, ``provide`` now raises."""
        self.closed = True

    # ---------------------------------------------------------- the "UI" side

    def outstanding(self) -> list[DecisionRequest]:
        """Requests waiting for an answer, oldest first."""
        return [request for request, _ in self._waiting.values()]

    async def wait_outstanding(self) -> list[DecisionRequest]:
        """Wait (without sleeping) until at least one request is outstanding; return them."""
        while not self._waiting:
            self._arrived.clear()
            await self._arrived.wait()
        return self.outstanding()

    def provide(self, decision_id: DecisionId, action: dict[str, Any]) -> None:
        """Answer an outstanding request.

        RuntimeError if the episode has ended (the runner is gone); UnknownDecision if there is no
        outstanding request with this id.
        """
        if self.closed:
            raise RuntimeError("runner is gone")
        entry = self._waiting.get(decision_id)
        if entry is None or entry[1].done():
            raise UnknownDecision(decision_id)
        del self._waiting[decision_id]  # answered now, not when ``act`` next gets to run
        entry[1].set_result(dict(action))
