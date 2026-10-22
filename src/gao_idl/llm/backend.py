"""LLM backend protocol, wire types and the error type shared by all backends.

Nothing here knows about sessions, ledgers or stores: a backend maps messages to a response.
"""

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Literal, Protocol

from gao_idl.core.errors import GaoError
from gao_idl.core.schemas import FrozenModel

REDACTED = "[REDACTED]"
_HEADER = re.compile(
    r"(?i)(authorization|proxy-authorization|x-api-key|api-key)(\"?\s*[:=]\s*)[^\r\n,}]*"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credentials from ``text``: exact secret values, auth header values, bearer tokens."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


class BackendError(GaoError):
    """A backend could not produce a response. The message never carries credentials."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


class Message(FrozenModel):
    """One chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


class Response(FrozenModel):
    """A model reply: its text, the structured form if the server gave one, and accounting."""

    text: str
    parsed: dict[str, Any] | None = None
    usage: dict[str, int] = {}
    latency_s: float = 0.0
    model: str = ""
    raw: dict[str, Any] = {}


class LLMBackend(Protocol):
    """Anything that turns messages into a ``Response`` (HTTP endpoint, mock, recorded replay)."""

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        """One completion; ``response_schema`` asks for structured output if the backend can."""
        ...


current_decision: ContextVar[str | None] = ContextVar("current_decision", default=None)
"""The decision id an ``LLMAgent`` is currently asking about (set around each backend call).

``LLMBackend.complete`` has no decision argument; replay and call recording read it from here. A
``ContextVar`` is per asyncio task, so concurrent ``act`` calls of one agent do not mix.
"""

Sleep = Callable[[float], Awaitable[None]]
