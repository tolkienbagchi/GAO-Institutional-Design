"""Backend for any OpenAI-compatible ``/chat/completions`` endpoint (local server or hosted).

One HTTP shape only; no provider SDKs. ``httpx`` (the ``llm`` extra) is imported lazily by the
default transport, so importing this module never needs it. The credential is fetched per request
from a callable and only ever lives in a local variable and the request header: it is scrubbed
from every error message, and never stored on the backend or copied into ``Response.raw``.

Stability: ``OpenAICompatBackend`` is public (exported by ``gao_idl.llm``); the rest of this
module is internal.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from gao_idl.llm.backend import BackendError, Message, Response, Sleep, scrub

RETRYABLE_STATUS = frozenset({429}) | frozenset(range(500, 600))
_SECRET_HEADER_WORDS = ("auth", "key", "token", "secret")


@dataclass(frozen=True)
class HttpReply:
    """What a transport returns: the status code and the body text."""

    status: int
    text: str


class TransportFailure(Exception):
    """A retryable transport problem (timeout, connection error)."""


Transport = Callable[[str, dict[str, str], dict[str, Any], float], Awaitable[HttpReply]]
"""``transport(url, headers, json_body, timeout_s) -> HttpReply``; raises ``TransportFailure``."""


async def httpx_transport(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float
) -> HttpReply:
    """POST ``body`` as JSON via ``httpx.AsyncClient``; timeouts and connection errors retry."""
    import httpx  # the `llm` extra; imported here so that importing gao_idl.llm never needs it

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.TransportError as e:  # timeouts, connection and read errors
        raise TransportFailure(type(e).__name__) from None  # no request details in the message
    return HttpReply(status=r.status_code, text=r.text)


def _scrub_json(value: Any, secrets: tuple[str, ...]) -> Any:
    """``value`` with every string and key scrubbed; the JSON shape is unchanged."""
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, list):
        return [_scrub_json(v, secrets) for v in value]
    if isinstance(value, dict):
        return {scrub(str(k), secrets): _scrub_json(v, secrets) for k, v in value.items()}
    return value


class OpenAICompatBackend:
    """Chat-completions client with structured output, a text fallback and transport retries.

    * ``response_schema`` given: sends ``response_format={"type": "json_schema", ...,
      "strict": True}``. If the server answers 400 mentioning ``response_format``, the request is
      sent once more without it and ``Response.parsed`` is ``None`` (the codec parses the text).
    * 429, 5xx and timeouts are retried ``max_retries`` times (so up to ``max_retries + 1``
      requests) with delays ``backoff_s * 2**k``; then ``BackendError``. Other 4xx fail at once.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        credential: Callable[[], SecretStr] | None = None,
        extra_headers: dict[str, str] | None = None,
        *,
        max_retries: int = 3,
        backoff_s: float = 0.5,
        sleep: Sleep = asyncio.sleep,
        transport: Transport = httpx_transport,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._credential = credential
        self._extra = dict(extra_headers or {})
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._transport = transport

    def __repr__(self) -> str:
        return f"OpenAICompatBackend(url={self._url!r}, model={self._model!r})"

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        """One chat completion (see the class docstring for retries and the fallback)."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        structured = response_schema is not None
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "action", "schema": response_schema, "strict": True},
            }
        started = time.monotonic()
        reply, secrets = await self._send(body, timeout_s)
        if reply.status == 400 and structured and "response_format" in reply.text:
            del body["response_format"]
            structured = False
            reply, secrets = await self._send(body, timeout_s)
        latency = time.monotonic() - started
        if reply.status >= 400:
            raise BackendError(f"HTTP {reply.status}: {scrub(reply.text, secrets)[:300]}")
        return self._parse(reply, structured, latency, secrets)

    # ------------------------------------------------------------------ internals

    def _headers(self) -> tuple[dict[str, str], tuple[str, ...]]:
        """Request headers and the secret values to scrub (the credential is fetched here)."""
        headers = {"Content-Type": "application/json", **self._extra}
        secrets = [
            v
            for k, v in self._extra.items()
            if any(w in k.lower() for w in _SECRET_HEADER_WORDS) and v
        ]
        if self._credential is not None:
            value = self._credential().get_secret_value()
            headers["Authorization"] = f"Bearer {value}"
            secrets.append(value)
        return headers, tuple(secrets)

    async def _send(
        self, body: dict[str, Any], timeout_s: float
    ) -> tuple[HttpReply, tuple[str, ...]]:
        """POST with the transport retry policy; the credential is fetched for every request.

        Returns the reply and the secret values of the last request (for scrubbing messages).
        """
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
                if reply.status not in RETRYABLE_STATUS:
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
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise BackendError(f"malformed response: {scrub(reply.text, secrets)[:300]}") from None
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise BackendError("response content is not a string")
        parsed: dict[str, Any] | None = None
        if structured:
            try:
                candidate = json.loads(content)
            except ValueError:
                candidate = None
            parsed = candidate if isinstance(candidate, dict) else None
        usage = {
            k: v
            for k, v in (data.get("usage") or {}).items()
            if isinstance(v, int) and not isinstance(v, bool)
        }
        return Response(
            text=content,
            parsed=parsed,
            usage=usage,
            latency_s=latency,
            model=str(data.get("model", self._model)),
            raw=_scrub_json(data, secrets),  # never carries the credential
        )
