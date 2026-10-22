"""The adapter protocol and the episode objects. Adapters see requests and reports, nothing else."""

from typing import Any, Literal, Protocol

from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport


class EpisodeContext(FrozenModel):
    """What an adapter is told when an episode (one session run) starts."""

    agent_id: AgentId
    env_ref: str
    seed: int
    keep_episode_memory: bool = False
    keep_conversation: bool = False
    keep_parameters: bool = True


class EpisodeEnd(FrozenModel):
    """What an adapter is told when the episode ends.

    ``final_utility`` is only filled in if the runner is configured with
    ``disclose_final_utility=True``: telling an agent its total utility is a disclosure decision,
    so it is off by default and ``None`` otherwise.
    """

    agent_id: AgentId
    terminal: Literal["normal", "truncated", "failed"]
    final_utility: float | None = None


class AgentAdapter(Protocol):
    """Anything that supplies actions: scripted, random, human transport, LLM.

    An adapter receives only ``DecisionRequest`` and ``ParticipantReport`` objects addressed to its
    own agent (plus the episode context/end); it never gets the session, ledger, store or config.
    """

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """The episode begins."""
        ...

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """Return a JSON action for ``request``."""
        ...

    async def observe(self, report: ParticipantReport) -> None:
        """A report addressed to this agent was delivered."""
        ...

    async def episode_end(self, end: EpisodeEnd) -> None:
        """The episode is over."""
        ...
