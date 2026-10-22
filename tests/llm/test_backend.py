"""Backends: the mock, and the OpenAI-compatible client driven through a fake transport."""

import json
import os
import subprocess
import sys
from typing import Any

import pytest
from pydantic import SecretStr

from gao_idl.llm.backend import BackendError, Message, scrub
from gao_idl.llm.mock import MockBackend, MockExhausted
from gao_idl.llm.openai_compat import HttpReply, OpenAICompatBackend, TransportFailure
from tests.aio import run_test

MSGS = [Message(role="system", content="s"), Message(role="user", content="u")]
SCHEMA = {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}


def complete(b: Any, schema: dict[str, Any] | None = None) -> Any:
    return run_test(
        b.complete(MSGS, schema, temperature=0.0, max_tokens=16, timeout_s=3.0),
    )


# ------------------------------------------------------------------------- mock


def test_mock_serves_in_order_records_calls_and_raises_exceptions() -> None:
    m = MockBackend(["text reply", {"amount": 5}, BackendError("boom")])
    first = complete(m)
    second = complete(m, SCHEMA)
    assert (first.text, first.parsed) == ("text reply", None)
    assert (second.parsed, json.loads(second.text)) == ({"amount": 5}, {"amount": 5})
    with pytest.raises(BackendError, match="boom"):
        complete(m)
    with pytest.raises(MockExhausted):
        complete(m)
    assert len(m.calls) == 4
    assert m.calls[1].response_schema == SCHEMA and m.calls[0].response_schema is None
    assert (m.calls[0].messages, m.calls[0].temperature, m.calls[0].max_tokens) == (MSGS, 0.0, 16)


def test_mock_callable_script_sees_the_messages() -> None:
    m = MockBackend(lambda messages: {"echo": len(messages)})
    assert complete(m).parsed == {"echo": 2}


# ------------------------------------------------------------- OpenAI-compatible


class FakeTransport:
    """Replies from a list (a ``HttpReply`` is returned, an exception raised); records requests."""

    def __init__(self, replies: list[HttpReply | Exception]) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    async def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float
    ) -> HttpReply:
        self.requests.append((url, dict(headers), json.loads(json.dumps(body)), timeout_s))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(content: str, **extra: Any) -> HttpReply:
    body = {
        "model": "m-1",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "details": {"x": 1}},
        **extra,
    }
    return HttpReply(200, json.dumps(body))


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def backend(
    replies: list[HttpReply | Exception], **kw: Any
) -> tuple[OpenAICompatBackend, FakeTransport, Sleeps]:
    transport, sleeps = FakeTransport(replies), Sleeps()
    kw.setdefault("sleep", sleeps)
    return OpenAICompatBackend("http://x/v1/", "m-1", transport=transport, **kw), transport, sleeps


def test_structured_request_shape_and_response_parsing() -> None:
    b, transport, _ = backend([ok('{"amount": 5}')])
    r = complete(b, SCHEMA)
    ((url, _, body, timeout),) = transport.requests
    assert url == "http://x/v1/chat/completions" and timeout == 3.0
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "action", "schema": SCHEMA, "strict": True},
    }
    assert body["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("m-1", 0.0, 16)
    assert (r.text, r.parsed, r.model) == ('{"amount": 5}', {"amount": 5}, "m-1")
    assert r.usage == {"prompt_tokens": 3, "completion_tokens": 2}  # only integer counters


def test_no_schema_means_no_response_format_and_no_parsed() -> None:
    b, transport, _ = backend([ok('{"amount": 5}')])
    r = complete(b, None)
    assert "response_format" not in transport.requests[0][2] and r.parsed is None


def test_400_mentioning_response_format_retries_once_without_it() -> None:
    b, transport, sleeps = backend(
        [HttpReply(400, "unknown field response_format"), ok('{"amount": 2}')]
    )
    r = complete(b, SCHEMA)
    assert len(transport.requests) == 2 and sleeps.delays == []  # not a transport retry
    assert "response_format" in transport.requests[0][2]
    assert "response_format" not in transport.requests[1][2]
    assert (r.text, r.parsed) == ('{"amount": 2}', None)  # the codec parses the text


def test_other_400_is_not_retried() -> None:
    b, transport, _ = backend([HttpReply(400, "bad request: max_tokens too large")])
    with pytest.raises(BackendError, match="HTTP 400"):
        complete(b, SCHEMA)
    assert len(transport.requests) == 1


def test_transport_retries_with_exponential_backoff_then_gives_up() -> None:
    b, transport, sleeps = backend(
        [HttpReply(429, "slow down"), HttpReply(503, "down"), TransportFailure("Timeout")] * 2
    )
    with pytest.raises(BackendError, match="giving up after 4 requests"):
        complete(b)
    assert len(transport.requests) == 4  # 1 request + 3 retries
    assert sleeps.delays == [0.5, 1.0, 2.0]


def test_retry_then_success() -> None:
    b, transport, sleeps = backend([HttpReply(500, "oops"), TransportFailure("T"), ok("fine")])
    assert complete(b).text == "fine"
    assert len(transport.requests) == 3 and sleeps.delays == [0.5, 1.0]


def test_401_fails_at_once() -> None:
    b, transport, sleeps = backend([HttpReply(401, "no")])
    with pytest.raises(BackendError, match="HTTP 401"):
        complete(b)
    assert len(transport.requests) == 1 and sleeps.delays == []


def test_malformed_responses_are_backend_errors() -> None:
    for text in ("not json", "{}", '{"choices": []}', '{"choices":[{"message":{"content":[1]}}]}'):
        b, _, _ = backend([HttpReply(200, text)])
        with pytest.raises(BackendError):
            complete(b)


def test_null_content_is_empty_text() -> None:
    b, _, _ = backend([HttpReply(200, '{"choices":[{"message":{"content":null}}]}')])
    assert complete(b).text == ""


def test_credential_is_fetched_per_request_and_never_leaks() -> None:
    calls: list[int] = []

    def credential() -> SecretStr:
        calls.append(1)
        return SecretStr("sk-TESTSECRET")

    leaky = ok("fine", echo="Authorization: Bearer sk-TESTSECRET")
    b, transport, _ = backend(
        [HttpReply(503, "sk-TESTSECRET rejected"), leaky], credential=credential
    )
    r = complete(b)
    assert len(calls) == 2  # once per request, retries included
    assert [h["Authorization"] for _, h, _, _ in transport.requests] == ["Bearer sk-TESTSECRET"] * 2
    assert "sk-TESTSECRET" not in json.dumps(r.raw) and "sk-TESTSECRET" not in r.model_dump_json()
    assert "sk-TESTSECRET" not in repr(b)
    assert "sk-TESTSECRET" not in json.dumps(
        {k: v for k, v in vars(b).items() if k != "_credential"}, default=str
    )


@pytest.mark.parametrize(
    "failure",
    [
        TransportFailure("failed with Authorization: Bearer sk-TESTSECRET"),
        RuntimeError("request headers {'Authorization': 'Bearer sk-TESTSECRET'}"),
        HttpReply(500, "bad key sk-TESTSECRET"),
        HttpReply(401, "Authorization: Bearer sk-TESTSECRET"),
    ],
)
def test_errors_never_contain_the_credential(failure: HttpReply | Exception) -> None:
    b, _, _ = backend([failure] * 4, credential=lambda: SecretStr("sk-TESTSECRET"))
    with pytest.raises(BackendError) as info:
        complete(b)
    assert "sk-TESTSECRET" not in str(info.value)
    assert "sk-TESTSECRET" not in repr(info.value)


def test_secret_looking_extra_headers_are_scrubbed_too() -> None:
    b, _, _ = backend(
        [HttpReply(500, "x-api-key was KEY123 here")] * 4, extra_headers={"X-Api-Key": "KEY123"}
    )
    with pytest.raises(BackendError) as info:
        complete(b)
    assert "KEY123" not in str(info.value)


def test_scrub_and_backend_error_remove_auth_headers() -> None:
    assert "abc" not in scrub("Authorization: Bearer abc", ())
    assert "abc" not in str(BackendError("headers={'authorization': 'Bearer abc'}"))
    assert scrub("plain text", ("",)) == "plain text"


def test_importing_the_llm_package_never_imports_httpx() -> None:
    code = "import sys, gao_idl.llm, gao_idl.llm.openai_compat; assert 'httpx' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, env=dict(os.environ))
