"""Deterministic scripted backend for tests.

Stability: ``MockBackend``, ``MockCall``, ``MockExhausted`` are public (exported by
``gao_idl.llm``); the rest of this module is internal.
"""

import json
from collections.abc import Callable
from typing import Any

from gao_idl.core.schemas import FrozenModel
from gao_idl.llm.backend import Message, Response, current_decision

Item = str | dict[str, Any] | Exception
Script = list[Item] | Callable[[list[Message]], Item]


class MockExhausted(Exception):
    """The script ran out. Deliberately not a ``BackendError``: a test bug must not be retried."""


class MockCall(FrozenModel):
    """What the backend was asked."""

    messages: list[Message]
    response_schema: dict[str, Any] | None
    temperature: float
    max_tokens: int
    timeout_s: float
    decision_id: str | None


class MockBackend:
    """Serves ``script`` in order (or calls it with the messages); ``Exception`` items are raised.

    A ``dict`` item is a structured reply (``parsed`` set, text is its JSON); a ``str`` item is a
    plain text reply (``parsed=None``), which exercises the codec's text-parsing path.
    """

    def __init__(self, script: Script) -> None:
        self._script = script
        self._next = 0
        self.calls: list[MockCall] = []

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        """Record the call, then return or raise the next scripted item."""
        self.calls.append(
            MockCall(
                messages=list(messages),
                response_schema=response_schema,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                decision_id=current_decision.get(),
            )
        )
        if callable(self._script):
            item = self._script(messages)
        else:
            if self._next >= len(self._script):
                raise MockExhausted(f"mock script exhausted after {len(self._script)} replies")
            item = self._script[self._next]
            self._next += 1
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, sort_keys=True)
        prompt_words = sum(len(m.content.split()) for m in messages)
        return Response(
            text=text,
            parsed=None if isinstance(item, str) else item,
            usage={"prompt_tokens": prompt_words, "completion_tokens": len(text.split())},
            latency_s=0.0,
            model="mock",
        )
