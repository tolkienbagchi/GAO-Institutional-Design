"""The adapter protocol and the episode objects. Adapters see requests and reports, nothing else."""

from typing import Any, Literal, Protocol, runtime_checkable

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


@runtime_checkable
class ExternalRecorder(Protocol):
    """An adapter that obtained answers from an external service (e.g. a language model).

    After each ``act`` (finished, failed or cancelled) the runner drains it and writes every item
    to the session's input log as an ``ExternalResponse`` *before* it submits the action, so a
    replay needs no external call. Each item is a JSON-able dict with a ``"decision_id"`` key naming
    the decision it belongs to (one agent may have several decisions in flight); the runner removes
    that key and records the rest as the payload. Draining empties the buffer.
    """

    def drain_external(self) -> list[dict[str, Any]]:
        """The items collected since the last call, oldest first."""
        ...
