"""DefaultCodec: what a prompt may contain, and how replies are parsed."""

import inspect
import json

import pytest

from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message, Response
from gao_idl.llm.codec import (
    CodecParseError,
    DefaultCodec,
    DefaultReportRenderer,
    first_json_object,
)

SCHEMA = {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}


def request(**obs: object) -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId("auction/bidding#bidder:0"),
        path=["auction", "bidding"],
        layer="bidding",
        role=RoleId("bidder:0"),
        agent_id=AgentId("bob"),
        observation=dict(obs) or {"value": 4.0},
        action_schema=SCHEMA,
    )


def test_render_signature_cannot_carry_anything_but_the_request_memory_and_rejections() -> None:
    names = list(inspect.signature(DefaultCodec.render).parameters)
    assert names == ["self", "request", "memory", "rejections"]


def test_render_layout_system_memory_task() -> None:
    mem = [Message(role="user", content="old turn"), Message(role="assistant", content="{}")]
    out = DefaultCodec().render(request(), mem, [])
    assert [m.role for m in out] == ["system", "user", "assistant", "user"]
    assert out[1:3] == mem
    system, task = out[0].content, out[-1].content
    assert "bob" in system and "bidder:0" in system and "bidding" in system
    assert json.dumps({"value": 4.0}, sort_keys=True) in task
    assert json.dumps(SCHEMA, sort_keys=True) in task
    assert "JSON only" in task and "rejected" not in task


def test_rejections_are_listed_in_the_task_message_only() -> None:
    out = DefaultCodec().render(request(), [], ["first problem", "second problem"])
    assert len(out) == 2
    assert "- first problem" in out[-1].content and "- second problem" in out[-1].content
    assert "rejected" not in out[0].content


def test_render_is_deterministic_and_does_not_mutate_its_inputs() -> None:
    mem = [Message(role="user", content="m")]
    rej = ["r"]
    a = DefaultCodec().render(request(), mem, rej)
    b = DefaultCodec().render(request(), mem, rej)
    assert a == b and mem == [Message(role="user", content="m")] and rej == ["r"]


def test_parse_prefers_the_structured_reply() -> None:
    r = Response(text='{"amount": 1}', parsed={"amount": 2})
    assert DefaultCodec().parse(r, request()) == {"amount": 2}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"amount": 5}', {"amount": 5}),
        ('Sure! Here you go: {"amount": 5} Good luck.', {"amount": 5}),
        ('```json\n{"amount": 5}\n```', {"amount": 5}),
        ('{"a": {"b": {"c": 1}}, "d": [1, {"e": 2}]}', {"a": {"b": {"c": 1}}, "d": [1, {"e": 2}]}),
        (
            '{"note": "a } brace and a \\" quote {", "x": 1} trailing }',
            {"note": 'a } brace and a " quote {', "x": 1},
        ),
        ('{"amount": "5"}', {"amount": "5"}),  # returned unvalidated, never coerced
        ("{}", {}),
    ],
)
def test_parse_extracts_the_first_balanced_object(text: str, expected: dict[str, object]) -> None:
    assert DefaultCodec().parse(Response(text=text), request()) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I bid five",
        "",
        "[1, 2, 3]",
        '{"amount": 5',
        "{amount: 5}",
        "{maybe} then " + '{"amount": 5}',
    ],
)
def test_parse_failures_raise_codec_parse_error(text: str) -> None:
    with pytest.raises(CodecParseError):
        DefaultCodec().parse(Response(text=text), request())


def test_first_json_object_message_names_the_problem() -> None:
    with pytest.raises(CodecParseError, match="not closed"):
        first_json_object('{"a": 1')
    with pytest.raises(CodecParseError, match="no JSON object"):
        first_json_object("nothing")


def test_default_report_renderer_reads_the_payload_and_layer_only() -> None:
    report = ParticipantReport(
        report_id=ReportId("auction/bidding@bob:0"),
        path=["auction", "bidding"],
        layer="bidding",
        agent_id=AgentId("bob"),
        payload={"own_bid": 3.0, "status": "bid accepted"},
    )
    m = DefaultReportRenderer().render_report(report)
    assert m.role == "user"
    assert m.content == 'Report (bidding): {"own_bid": 3.0, "status": "bid accepted"}'
    assert "auction/bidding@bob" not in m.content  # ids are not part of the prompt
