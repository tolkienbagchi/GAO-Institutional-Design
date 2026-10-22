"""``AnthropicBackend`` through a fake transport (the real httpx path is skipped without httpx)."""

import json
from typing import Any

import pytest
from pydantic import SecretStr

from gao_idl.core import events as ev
from gao_idl.environments.auction.bidding import Bid
from gao_idl.environments.auction.llm import AuctionCodec, AuctionReportRenderer
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.anthropic import AnthropicBackend, split_messages
from gao_idl.llm.backend import BackendError, Message
from gao_idl.llm.openai_compat import HttpReply, TransportFailure
from gao_idl.llm.replay import ReplayBackend
from gao_idl.llm.schema_transform import to_anthropic_schema
from tests.aio import run_test
from tests.llm.helpers import ALICE, AUCTION, BOB, bidders, play
from tests.llm.test_backend import FakeTransport, Sleeps

SECRET = "sk-ant-TESTSECRET"
SCHEMA = Bid.model_json_schema()


def msg(text: str, stop_reason: str = "end_turn", **extra: Any) -> HttpReply:
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-x-1",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 11, "output_tokens": 7, "service_tier": "standard"},
        **extra,
    }
    return HttpReply(200, json.dumps(body))


def make(
    replies: list[HttpReply | Exception], **kw: Any
) -> tuple[AnthropicBackend, FakeTransport, Sleeps]:
    transport, sleeps = FakeTransport(replies), Sleeps()
    kw.setdefault("sleep", sleeps)
    b = AnthropicBackend("claude-x", lambda: SecretStr(SECRET), transport=transport, **kw)
    return b, transport, sleeps


def complete(b: Any, messages: list[Message], schema: dict[str, Any] | None = None) -> Any:
    return run_test(b.complete(messages, schema, temperature=0.0, max_tokens=16, timeout_s=3.0))


USER = [Message(role="user", content="u")]


def test_request_shape() -> None:
    b, transport, _ = make([msg('{"amount": 5}')], extra_headers={"anthropic-beta": "x-1"})
    messages = [
        Message(role="system", content="s1"),
        Message(role="user", content="u1"),
        Message(role="system", content="s2"),
        Message(role="user", content="u2"),  # consecutive user messages are merged
        Message(role="assistant", content="a1"),
        Message(role="user", content="u3"),
    ]
    complete(b, messages, SCHEMA)
    ((url, headers, body, timeout),) = transport.requests
    assert url == "https://api.anthropic.com/v1/messages" and timeout == 3.0
    assert headers == {
        "x-api-key": SECRET,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "anthropic-beta": "x-1",
    }
    assert body["system"] == "s1\n\ns2"
    assert body["messages"] == [
        {"role": "user", "content": "u1\n\nu2"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u3"},
    ]
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("claude-x", 0.0, 16)
    fmt = body["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": to_anthropic_schema(SCHEMA)}
    assert '"minimum"' not in json.dumps(fmt["schema"]) and fmt["schema"] != SCHEMA
    assert not {"output_format", "tools", "tool_choice"} & body.keys()
    assert "anthropic-beta" in headers and "structured-outputs" not in headers["anthropic-beta"]


def test_no_schema_means_no_output_config_and_no_system_when_absent() -> None:
    b, transport, _ = make([msg('{"amount": 5}')])
    r = complete(b, USER)
    body = transport.requests[0][2]
    assert "output_config" not in body and "system" not in body and r.parsed is None


def test_schema_transform_can_be_switched_off_and_base_url_api_version_are_configurable() -> None:
    transport = FakeTransport([msg("{}")])
    b = AnthropicBackend(
        "m",
        lambda: SecretStr(SECRET),
        base_url="http://proxy/",
        api_version="2099-01-01",
        schema_transform=False,
        transport=transport,
    )
    complete(b, USER, SCHEMA)
    url, headers, body, _ = transport.requests[0]
    assert url == "http://proxy/v1/messages" and headers["anthropic-version"] == "2099-01-01"
    assert body["output_config"]["format"]["schema"] == SCHEMA


def test_a_request_with_only_system_messages_is_an_error() -> None:
    with pytest.raises(BackendError, match="at least one"):
        split_messages([Message(role="system", content="s")])


def test_parsed_from_text_block() -> None:
    reply = HttpReply(
        200,
        json.dumps(
            {
                "model": "claude-x-1",
                "content": [
                    {"type": "thinking", "thinking": "hm"},
                    {"type": "text", "text": '{"amount": 5}'},
                    {"type": "text", "text": "ignored"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 11, "output_tokens": 7, "service_tier": "x"},
            }
        ),
    )
    b, _, _ = make([reply, msg("I bid five"), msg("[1, 2]")])
    r = complete(b, USER, SCHEMA)
    assert (r.text, r.parsed, r.model) == ('{"amount": 5}', {"amount": 5}, "claude-x-1")
    assert r.usage == {"input_tokens": 11, "output_tokens": 7}
    assert complete(b, USER, SCHEMA).parsed is None  # not JSON: the codec's text path handles it
    assert complete(b, USER, SCHEMA).parsed is None  # JSON but not an object


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_leaves_parsed_none(stop_reason: str) -> None:
    b, _, _ = make([msg('{"amount": 5}', stop_reason=stop_reason)])
    r = complete(b, USER, SCHEMA)
    assert r.parsed is None and r.raw["stop_reason"] == stop_reason
    assert r.text == '{"amount": 5}'


def test_retry_on_529_then_success() -> None:
    b, transport, sleeps = make(
        [HttpReply(529, "overloaded"), HttpReply(429, "slow"), TransportFailure("T"), msg("{}")]
    )
    r = complete(b, USER, SCHEMA)
    assert r.parsed == {} and len(transport.requests) == 4
    assert sleeps.delays == [0.5, 1.0, 2.0]


def test_retries_are_bounded() -> None:
    b, transport, _ = make([HttpReply(500, "boom")] * 4)
    with pytest.raises(BackendError, match="giving up after 4 requests"):
        complete(b, USER)
    assert len(transport.requests) == 4


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_no_retry_on_client_errors(status: int) -> None:
    b, transport, sleeps = make([HttpReply(status, '{"error": {"message": "nope"}}')])
    with pytest.raises(BackendError, match=f"HTTP {status}"):
        complete(b, USER, SCHEMA)
    assert len(transport.requests) == 1 and sleeps.delays == []


def test_timeout_becomes_backend_error() -> None:
    b, transport, sleeps = make([TransportFailure("ReadTimeout")] * 4)
    with pytest.raises(BackendError, match="ReadTimeout"):
        complete(b, USER)
    assert len(transport.requests) == 4 and len(sleeps.delays) == 3


def test_malformed_reply_is_a_backend_error() -> None:
    b, _, _ = make([HttpReply(200, "<html>"), HttpReply(200, '{"content": "x"}')])
    for _ in range(2):
        with pytest.raises(BackendError, match="malformed"):
            complete(b, USER)


def test_credential_is_fetched_per_request() -> None:
    keys = iter(["sk-ant-one", "sk-ant-two"])
    transport = FakeTransport([HttpReply(529, ""), msg("{}")])
    b = AnthropicBackend("m", lambda: SecretStr(next(keys)), transport=transport, sleep=Sleeps())
    complete(b, USER)
    assert [r[1]["x-api-key"] for r in transport.requests] == ["sk-ant-one", "sk-ant-two"]


def test_secret_never_in_raw_or_errors() -> None:
    # a server that echoes the request headers back, in an error and in a good reply
    class Echo:
        def __init__(self, status: int) -> None:
            self.status = status

        async def __call__(
            self, url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float
        ) -> HttpReply:
            echoed = {"headers": headers, "x-api-key": headers["x-api-key"]}
            if self.status == 500:
                return HttpReply(500, json.dumps(echoed) + f" x-api-key: {SECRET}")
            data = json.loads(msg('{"amount": 2}').text)
            data["debug"] = echoed
            return HttpReply(200, json.dumps(data))

    failing = AnthropicBackend("m", lambda: SecretStr(SECRET), transport=Echo(500), sleep=Sleeps())
    with pytest.raises(BackendError) as info:
        complete(failing, USER)
    assert SECRET not in str(info.value) and SECRET not in repr(info.value)
    assert SECRET not in repr(failing)

    echoing = AnthropicBackend("m", lambda: SecretStr(SECRET), transport=Echo(200))
    r = complete(echoing, USER, SCHEMA)
    assert r.parsed == {"amount": 2} and SECRET not in json.dumps(r.raw)
    assert SECRET not in r.model_dump_json()

    # through an agent: the failed calls and the good ones, in metadata, snapshot and the log
    for backend in (failing, echoing):
        agent = LLMAgent(ALICE, backend, record_text=True, record_raw=True)
        session = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: agent, BOB: _bob()})
        assert SECRET not in json.dumps([m.model_dump() for m in agent.metadata()])
        assert SECRET not in json.dumps(session.snapshot())
        assert SECRET not in json.dumps(ev.dump(session.trace))


def _bob() -> Any:
    from tests.llm.helpers import truthful

    return truthful()


def test_auction_runs_with_anthropic_backend_on_a_fake_transport_and_replays_without_calls() -> (
    None
):
    values = bidders(alice=10.0, bob=6.0)
    ta, tb = FakeTransport([msg('{"amount": 7.5}')]), FakeTransport([msg('{"amount": 4}')])
    agents = {
        ALICE: LLMAgent(
            ALICE,
            AnthropicBackend("m", lambda: SecretStr(SECRET), transport=ta),
            AuctionCodec(),
            report_renderer=AuctionReportRenderer(),
        ),
        BOB: LLMAgent(
            BOB,
            AnthropicBackend("m", lambda: SecretStr(SECRET), transport=tb),
            AuctionCodec(),
            report_renderer=AuctionReportRenderer(),
        ),
    }
    session = play(AUCTION, values, agents)
    assert session.finished and not session.failed
    assert len(ta.requests) == 1 and len(tb.requests) == 1
    assert ta.requests[0][2]["system"].startswith("You are alice")
    assert "10.0" in ta.requests[0][2]["messages"][-1]["content"]

    # replay: the recorded replies are served from the log; the transports see nothing new
    replay_a, replay_b = ReplayBackend(session.input_log), ReplayBackend(session.input_log)
    again = play(
        AUCTION,
        values,
        {ALICE: LLMAgent(ALICE, replay_a, AuctionCodec()), BOB: LLMAgent(BOB, replay_b)},
    )
    assert ev.dump(again.trace) == ev.dump(session.trace)
    assert len(ta.requests) == 1 and len(tb.requests) == 1  # no new transport calls
    assert replay_a.served == 1 and replay_b.served == 1


def test_local_validator_stays_authoritative_over_the_transformed_schema() -> None:
    # the API "happily" returns a negative bid; the agent's local check (original schema) repairs
    b, transport, _ = make([msg('{"amount": -1}'), msg('{"amount": 3}')])
    agent = LLMAgent(ALICE, b, AuctionCodec(), report_renderer=AuctionReportRenderer())
    session = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: agent, BOB: _bob()})
    assert session.finished and not session.failed
    assert len(transport.requests) == 2
    assert [r.attempt for r in agent.metadata()] == [1, 2]
    assert agent.metadata()[1].repair_reason is not None
    sent = transport.requests[0][2]["output_config"]["format"]["schema"]
    assert '"minimum"' not in json.dumps(sent)  # the request schema was transformed ...
    assert sent["properties"]["amount"]["description"].endswith("(minimum: 0)")


# ----------------------------------------------- the real httpx path (CI, where httpx exists)


def test_default_transport_over_httpx_mock_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    seen: list[Any] = []

    def handler(request: Any) -> Any:
        seen.append(request)
        return httpx.Response(200, json=json.loads(msg('{"amount": 9}').text))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    r = complete(AnthropicBackend("claude-x", lambda: SecretStr(SECRET)), USER, SCHEMA)
    assert r.parsed == {"amount": 9}
    (request,) = seen
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == SECRET
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_default_transport_timeout_and_529_over_httpx_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    httpx = pytest.importorskip("httpx")
    calls: list[int] = []

    def handler(request: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(529, json={"type": "error"})
        raise httpx.ReadTimeout("timed out", request=request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    b = AnthropicBackend("m", lambda: SecretStr(SECRET), sleep=Sleeps())
    with pytest.raises(BackendError, match="ReadTimeout"):
        complete(b, USER)
    assert len(calls) == 4
