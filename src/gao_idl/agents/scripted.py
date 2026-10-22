"""ScriptedAgent: a plain function from (request, reports so far) to an action.

Stability: ``ScriptedAgent`` is public (re-exported by ``gao_idl``); the rest of this module is
internal.
"""

from collections.abc import Callable
from typing import Any

from gao_idl.agents.base import EpisodeContext, EpisodeEnd
from gao_idl.core.ids import AgentId, ReportId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport

Policy = Callable[[DecisionRequest, list[ParticipantReport]], dict[str, Any]]


class ScriptedAgent:
    """Calls ``policy(request, reports)``; keeps only its own report history (deduplicated).

    ``reports`` is a copy, oldest first, of the reports delivered to this agent in this episode
    (cleared at episode start unless ``keep_episode_memory``).
    """

    def __init__(self, policy: Policy) -> None:
        self._policy = policy
        self._agent: AgentId | None = None
        self._reports: list[ParticipantReport] = []
        self._seen: set[ReportId] = set()

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """Remember which agent this is; forget earlier episodes unless asked to keep them."""
        self._agent = ctx.agent_id
        if not ctx.keep_episode_memory:
            self._reports, self._seen = [], set()

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """Apply the policy."""
        return self._policy(request, list(self._reports))

    async def observe(self, report: ParticipantReport) -> None:
        """Append the report unless this ``report_id`` was seen; ValueError if not ours."""
        if self._agent is not None and report.agent_id != self._agent:
            raise ValueError(f"report {report.report_id} is addressed to {report.agent_id}")
        if report.report_id not in self._seen:
            self._seen.add(report.report_id)
            self._reports.append(report)

    async def episode_end(self, end: EpisodeEnd) -> None:
        """Nothing to clean up."""
