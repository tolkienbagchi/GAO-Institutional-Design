"""Backend for the Anthropic Messages API (``POST /v1/messages``), over plain HTTP.

No provider SDK: the same ``httpx`` transport shape as :mod:`gao_idl.llm.openai_compat`, which this
module reuses (``HttpReply``, ``TransportFailure``, ``httpx_transport``, the retry status set), so
importing it never needs ``httpx``. The credential is fetched per request from a callable, lives
only in a local variable and the ``x-api-key`` header, and is scrubbed from every error message and
from ``Response.raw``.

Structured output uses ``output_config.format`` (``json_schema``), the one mechanism of this
backend: no tool use, no deprecated ``output_format``, no ``structured-outputs-*`` beta header. The
request schema is passed through :func:`~gao_idl.llm.schema_transform.to_anthropic_schema` (the API
supports a subset of JSON Schema); the agent still validates replies against the original schema.

Stability: ``AnthropicBackend`` is public (exported by ``gao_idl.llm``); the rest of this module
is internal.
"""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from gao_idl.llm.backend import BackendError, Message, Response, Sleep, scrub
from gao_idl.llm.openai_compat import (
    RETRYABLE_STATUS,
    HttpReply,
    Transport,
    TransportFailure,
    _scrub_json,
    httpx_transport,
)
from gao_idl.llm.schema_transform import to_anthropic_schema

_SECRET_HEADER_WORDS = ("auth", "key", "token", "secret")
_NO_PARSE_STOP_REASONS = frozenset({"refusal", "max_tokens"})


class AnthropicBackend:
    """Messages API client with structured output and transport retries.

    * System messages are joined (in order, with a blank line) into the top-level ``system``
      string; the others become ``messages`` entries, consecutive same-role ones merged because the
      API requires alternation.
    * ``response_schema`` given: sends ``output_config={"format": {"type": "json_schema",
      "schema": ...}}`` (the schema transformed unless ``schema_transform=False``) and parses the
      first ``text`` content block as JSON into ``Response.parsed``; text that is not a JSON object
      leaves ``parsed=None`` for the codec's text path. A ``stop_reason`` of ``refusal`` or
      ``max_tokens`` always leaves ``parsed=None`` (the reason is in ``Response.raw``).
    * 429, 529 (overloaded), other 5xx and transport failures are retried ``max_retries`` times
      (up to ``max_retries + 1`` requests) with delays ``backoff_s * 2**k``; then ``BackendError``.
      Other 4xx (400, 401, 403, ...) fail at once.
    """

    def __init__(
        self,
        model: str,
        credential: Callable[[], SecretStr],
        *,
        base_url: str = "https://api.anthropic.com",
        api_version: str = "2023-06-01",
        extra_headers: dict[str, str] | None = None,
        schema_transform: bool = True,
        max_retries: int = 3,
        backoff_s: float = 0.5,
        sleep: Sleep = asyncio.sleep,
        transport: Transport = httpx_transport,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._model = model
        self._credential = credential
        self._api_version = api_version
        self._extra = dict(extra_headers or {})
        self._transform = schema_transform
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._transport = transport

    def __repr__(self) -> str:
        return f"AnthropicBackend(url={self._url!r}, model={self._model!r})"

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        """One Messages API call (see the class docstring for structure and retries)."""
        system, turns = split_messages(messages)
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns,
        }
        if system:
            body["system"] = system
        if response_schema is not None:
            schema = to_anthropic_schema(response_schema) if self._transform else response_schema
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        started = time.monotonic()
        reply, secrets = await self._send(body, timeout_s)
        latency = time.monotonic() - started
        if reply.status >= 400:
            raise BackendError(f"HTTP {reply.status}: {scrub(reply.text, secrets)[:300]}")
        return self._parse(reply, response_schema is not None, latency, secrets)

    # ------------------------------------------------------------------ internals

    def _headers(self) -> tuple[dict[str, str], tuple[str, ...]]:
        """Request headers and the secret values to scrub (the credential is fetched here)."""
        value = self._credential().get_secret_value()
        headers = {
            "x-api-key": value,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
            **self._extra,
        }
        secrets = [
            v
            for k, v in self._extra.items()
            if any(w in k.lower() for w in _SECRET_HEADER_WORDS) and v
        ]
        return headers, (value, *secrets)

    async def _send(
        self, body: dict[str, Any], timeout_s: float
    ) -> tuple[HttpReply, tuple[str, ...]]:
        """POST with the transport retry policy; the credential is fetched for every request."""
        last = "no attempt"
        for k in range(self._max_retries + 1):
            headers, secrets = self._headers()
            try:
                reply = await self._transport(self._url, headers, body, timeout_s)
            except TransportFailure as e:
                last = f"transport failure: {scrub(str(e), secrets)}"
            except Exception as e:  # a transport we do not control: treat as retryable, scrubbed
                last = f"transport failure: {type(e).__name__}: {scrub(str(e), secrets)}"
            else:
                if reply.status not in RETRYABLE_STATUS:  # 429, 529 and 5xx retry
                    return reply, secrets
                last = f"HTTP {reply.status}: {scrub(reply.text, secrets)[:300]}"
            if k < self._max_retries:
                await self._sleep(self._backoff_s * 2**k)
        raise BackendError(f"giving up after {self._max_retries + 1} requests; last: {last}")

    def _parse(
        self, reply: HttpReply, structured: bool, latency: float, secrets: tuple[str, ...]
    ) -> Response:
        try:
            data = json.loads(reply.text)
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise TypeError("content is not a list")
        except (ValueError, KeyError, TypeError):
            raise BackendError(f"malformed response: {scrub(reply.text, secrets)[:300]}") from None
        text = next(
            (b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"), ""
        )
        if not isinstance(text, str):
            raise BackendError("response text block is not a string")
        stop_reason = data.get("stop_reason")
        parsed: dict[str, Any] | None = None
        if structured and stop_reason not in _NO_PARSE_STOP_REASONS:
            try:
                candidate = json.loads(text)
            except ValueError:
                candidate = None
            parsed = candidate if isinstance(candidate, dict) else None
        usage_in = data.get("usage") or {}
        usage = {
            k: v
            for k in ("input_tokens", "output_tokens")
            if isinstance(v := usage_in.get(k), int) and not isinstance(v, bool)
        }
        raw = _scrub_json(data, secrets)  # never carries the credential
        if stop_reason in _NO_PARSE_STOP_REASONS:
            raw["stop_reason"] = stop_reason  # recorded explicitly for the agent's metadata
        return Response(
            text=text,
            parsed=parsed,
            usage=usage,
            latency_s=latency,
            model=str(data.get("model", self._model)),
            raw=raw,
        )


def split_messages(messages: list[Message]) -> tuple[str, list[dict[str, str]]]:
    """System messages joined into one string; the rest with consecutive same-role ones merged."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns: list[dict[str, str]] = []
    for m in messages:
        if m.role == "system":
            continue
        if turns and turns[-1]["role"] == m.role:
            turns[-1]["content"] += "\n\n" + m.content
        else:
            turns.append({"role": m.role, "content": m.content})
    if not turns:
        raise BackendError("a Messages API request needs at least one user or assistant message")
    return system, turns
