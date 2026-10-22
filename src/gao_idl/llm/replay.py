"""Serving recorded model replies from a session's input log: replay without any inference.

Stability: ``ReplayBackend``, ``ReplayExhausted`` are public (exported by ``gao_idl.llm``); the
rest of this module is internal.
"""

from collections import deque
from typing import Any

from gao_idl.core import events as ev
from gao_idl.core.errors import DeterminismError
from gao_idl.core.events import InputEvent
from gao_idl.llm.backend import BackendError, Message, Response, current_decision


class ReplayExhausted(DeterminismError):
    """The replay asked for a reply the log does not have (a divergence, not a transport error)."""


class ReplayBackend:
    """An ``LLMBackend`` serving the log's ``ExternalResponse`` events, in order per decision.

    ``LLMAgent`` runs against it exactly as against the live backend: the decision it is asking
    about comes from ``current_decision``; the n-th call for a decision gets the n-th recorded
    payload for it. A recorded transport error is raised again as ``BackendError``. Asking for more
    than was recorded raises ``ReplayExhausted``. No network, no model, no randomness.
    """

    def __init__(self, session_log: list[InputEvent]) -> None:
        self._queues: dict[str, deque[dict[str, Any]]] = {}
        for event in session_log:
            if isinstance(event, ev.ExternalResponse):
                self._queues.setdefault(event.decision_id, deque()).append(dict(event.payload))
        self.served = 0

    def remaining(self) -> int:
        """How many recorded replies have not been served yet."""
        return sum(len(q) for q in self._queues.values())

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        """The next recorded reply for the decision being asked about."""
        did = current_decision.get()
        if did is None:
            raise ReplayExhausted("ReplayBackend called outside an LLMAgent decision")
        queue = self._queues.get(did)
        if not queue:
            raise ReplayExhausted(f"no recorded reply left for decision {did!r}")
        payload = queue.popleft()
        self.served += 1
        if "error" in payload:
            raise BackendError(str(payload["error"]))
        return Response(
            text=payload["text"],
            parsed=payload.get("parsed"),
            usage=payload.get("usage", {}),
            latency_s=payload.get("latency_s", 0.0),
            model=payload.get("model", ""),
            raw=payload.get("raw", {}),
        )
