"""Turning a ``DecisionRequest`` into a prompt and a reply into an action dict.

``render`` can only use what its signature carries: the request (observation, action schema,
layer, role, agent id), the agent's own memory and the rejection reasons of earlier attempts.
There is no way to hand it a session, a ledger, an outcome or another agent's data.

Render contract: the returned list is ``[system messages..., *memory, turn messages...]``. The
agent stores ``render(request, [], [])`` minus its leading system messages as the "turn" in its
memory, so a codec must render the same turn for the same request.

Stability: ``CodecParseError``, ``DecisionCodec``, ``DefaultCodec``, ``DefaultReportRenderer``,
``ReportRenderer`` are public (exported by ``gao_idl.llm``); the rest of this module is
internal.
"""

import json
from typing import Any, Protocol

from gao_idl.core.errors import GaoError
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message, Response


class CodecParseError(GaoError):
    """The reply contains no usable JSON object."""


class DecisionCodec(Protocol):
    """Prompt construction and reply parsing for one kind of environment."""

    def render(
        self, request: DecisionRequest, memory: list[Message], rejections: list[str]
    ) -> list[Message]:
        """The messages to send for ``request``."""
        ...

    def parse(self, response: Response, request: DecisionRequest) -> dict[str, Any]:
        """The action dict in ``response`` (not validated); raises ``CodecParseError``."""
        ...


class ReportRenderer(Protocol):
    """Renders a report addressed to the agent as one message; reads the payload only."""

    def render_report(self, report: ParticipantReport) -> Message:
        """The message that tells the model about ``report``."""
        ...


class DefaultReportRenderer:
    """``Report (<layer>): <payload as JSON>``."""

    def render_report(self, report: ParticipantReport) -> Message:
        """The payload as compact JSON, labelled with the layer that produced it."""
        body = json.dumps(report.payload, sort_keys=True)
        return Message(role="user", content=f"Report ({report.layer}): {body}")


def first_json_object(text: str) -> dict[str, Any]:
    """The first balanced ``{...}`` in ``text`` (string-aware), loaded as a JSON object."""
    start = text.find("{")
    if start < 0:
        raise CodecParseError("no JSON object found in the reply")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : i + 1])
                except ValueError as e:
                    raise CodecParseError(f"the JSON object is not valid JSON: {e}") from None
                if not isinstance(value, dict):
                    raise CodecParseError("the reply is not a JSON object")
                return value
    raise CodecParseError("the JSON object in the reply is not closed")


class DefaultCodec:
    """System prompt, the observation and the action's JSON Schema as JSON, "reply with JSON only".

    Environment codecs subclass this and override ``system_prompt`` / ``task_prompt``.
    """

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is playing and the reply format."""
        return (
            f"You are agent {request.agent_id}, acting in the role {request.role!r} of the "
            f"layer {request.layer!r}. Decide using only the information you are given. "
            "Reply with a single JSON object that follows the given JSON Schema, and nothing else."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """What the model is shown for this decision: the observation and the schema."""
        return (
            f"Observation:\n{json.dumps(request.observation, sort_keys=True)}\n\n"
            f"Your action must follow this JSON Schema:\n"
            f"{json.dumps(request.action_schema, sort_keys=True)}\n\n"
            "Reply with JSON only."
        )

    def render(
        self, request: DecisionRequest, memory: list[Message], rejections: list[str]
    ) -> list[Message]:
        """``[system] + memory + [task, plus the reasons earlier attempts were rejected]``."""
        task = self.task_prompt(request)
        if rejections:
            lines = "\n".join(f"- {r}" for r in rejections)
            task += (
                f"\n\nYour previous reply was rejected:\n{lines}\n"
                "Reply again with corrected JSON only."
            )
        return [
            Message(role="system", content=self.system_prompt(request)),
            *memory,
            Message(role="user", content=task),
        ]

    def parse(self, response: Response, request: DecisionRequest) -> dict[str, Any]:
        """``response.parsed`` if the server gave one, else the first JSON object in the text."""
        if response.parsed is not None:
            return response.parsed
        return first_json_object(response.text)
