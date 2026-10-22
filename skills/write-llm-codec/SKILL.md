---
name: write-llm-codec
description: Write a DecisionCodec and ReportRenderer so a language model can play a role in an existing gao_idl layer, without leaking hidden state into the prompt. Use when an environment needs an LLMAgent and the DefaultCodec prompt is too generic.
---

# Write an LLM codec for an existing layer

## When to use

An environment already exists and a model should play one of its roles. The layer is not
changed. You write two small classes (a codec for decisions, a renderer for reports) and the tests
that show the prompt carries nothing the role was not given. Signatures: `docs/for-agents/llms.md`.

Package note: `gao_idl.llm` is optional (extra `llm`). Nothing outside it imports it, with one
exception: an environment's own `llm.py` (for example `environments/vcm/llm.py`) may, and must be
listed in the import-direction allow-list in `tests/llm/test_integration.py`.

## What a codec may read

`render(request, memory, rejections)` receives only:

- the `DecisionRequest` (observation, action JSON Schema, layer, role, agent id),
- the agent's own `memory` (earlier turns it chose to keep), and
- the reasons earlier attempts at this decision were rejected.

It cannot reach a session, ledger, outcome or another agent's data, and must not try (no globals,
no module-level caches of other agents' requests). `ReportRenderer.render_report(report)` reads the
`ParticipantReport.payload` only. Everything the model knows is therefore something the layer chose
to tell that role, which is what makes the "no hidden digit in the prompt" test possible.

## Steps

1. Read the layer's `observe` model and `report` models. List the observation fields by name.
2. Subclass `DefaultCodec` and override `system_prompt(request)` (who the model is, the objective in
   words, the reply format) and `task_prompt(request)` (the situation, built from
   `request.observation` field by field). Keep `render` and `parse` unless the reply format changes.
3. Reuse the action schema: do not describe the action space by hand. Either end the task prompt
   with the schema JSON (as `DefaultCodec` does) or describe the menu from `observation` fields and
   keep the schema as the authority; local validation is authoritative either way.
4. Subclass `DefaultReportRenderer` and override `render_report` when the default
   `Report (<layer>): {json}` is too raw. Render the payload's own fields; add nothing.
5. Say how payoff is defined in `system_prompt`, in words. The model only learns its payoff from
   report fields.
6. Test it (below), then run it under `MockBackend` through `run_sync` end to end.

## Rendering reports

Reports reach the model as extra turns, in the order delivered, before its next decision. A report
for the previous round is the model's only memory of outcomes beyond `memory`. Do not summarize or
interpret ("you did badly"); state the fields. If a field is optional and absent, say nothing
about it.

## The repair loop

`LLMAgent` asks up to `RepairPolicy.max_attempts` times. A reply that fails parsing or local schema
validation is rejected, and the reason goes into `rejections` for the next `render` (shown by
`DefaultCodec` as "Your previous reply was rejected: ..."). If it keeps failing, the runner turns
that into a timeout and the layer's `TimeoutPolicy` decides what happens. Consequences for you:

- A codec that renders `rejections` itself must render the same turn for the same request
  (`render(request, [], [])` is stored as the turn).
- Do not put the repair text inside the stored turn; only `render`'s trailing message may carry it.
- A `TimeoutPolicy` of `Fail` makes a model that cannot comply abort the session; say so to the
  person when choosing it for a role a model plays.

## `to_anthropic_schema`

`to_anthropic_schema(schema)` returns a copy restricted to what the Messages API accepts:
numeric bounds, `pattern`, string lengths and `maxItems` are removed (each is appended to the
node's `description`), `minItems` above 1 is clamped, and every object gets
`additionalProperties: false`. It is applied to the request schema only. The local validator
still enforces everything, so a model reply that violates a removed bound is rejected and repaired,
not accepted. Do not weaken the validator to fit a provider.

## Test pattern: no digit from hidden state in the prompt

```python
# runnable
import json
import re
from typing import Any

from gao_idl import ScriptedAgent, Session, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest
from gao_idl.llm import DefaultCodec, LLMAgent, MockBackend


class PDCodec(DefaultCodec):
    """Prompt for one repeated prisoner's-dilemma move, from the observation fields only."""

    def system_prompt(self, request: DecisionRequest) -> str:
        return (
            f"You are {request.agent_id} in a repeated prisoner's dilemma. "
            "Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        return (
            "Observation: "
            + json.dumps(request.observation, sort_keys=True)
            + '\nChoose {"move": "C"} or {"move": "D"}. Reply with JSON only.'
        )


seen: list[DecisionRequest] = []


class RecordingCodec(PDCodec):
    def render(self, request: DecisionRequest, memory: Any, rejections: list[str]) -> Any:
        seen.append(request)
        return super().render(request, memory, rejections)


backend = MockBackend(lambda messages: {"move": "C"})
session = Session.start(
    "gao_idl.environments.pd:build_repeated_pd", {"agents": ["a", "b"], "rounds": 3}, 5
)
agents: dict[AgentId, Any] = {
    AgentId("a"): LLMAgent(AgentId("a"), backend, RecordingCodec()),
    AgentId("b"): ScriptedAgent(lambda req, reps: {"move": "D"}),
}
run_sync(session, agents)
assert session.finished and not session.failed and backend.calls


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


# everything agent "a" may legitimately have been told: its observations, schemas and reports
told = " ".join(
    json.dumps([r.observation, r.action_schema]) + r.agent_id + r.layer + str(r.role) for r in seen
) + json.dumps([r.payload for r in session.reports_for(AgentId("a"))])
for call in backend.calls:
    prompt = " ".join(m.content for m in call.messages)
    assert numbers(prompt) <= numbers(told), numbers(prompt) - numbers(told)
print("prompts checked:", len(backend.calls))
```

The comparison allows exactly the numbers in what the agent was given (its observations, schemas
and `session.reports_for(agent_id)`). In a real test also plant a secret with a value that
appears nowhere else (for example `7.31415`) so that any occurrence in a prompt is a leak.

Also test:

- The same request renders the same messages twice (`render(request, [], [])`).
- A hidden quantity changed in the environment params does not change the rendered prompt
  (compare the `MockBackend.calls` of two runs).
- A reply with extra text around the JSON parses (`first_json_object`), and a reply with no JSON
  raises `CodecParseError`.
- `MockBackend` with a first invalid reply followed by a valid one yields one repair call, and the
  second prompt contains the rejection reason.

## Definition of done

1. A codec and renderer exist, the prompt is built from `request` and report payloads only.
2. The tests above pass under `MockBackend`; no network in tests.
3. The module has a `Stability:` paragraph, a `::: module` entry in `docs/api/`, and, if it lives
   in an environment package, an entry in the import allow-list.
4. A real-backend snippet (credentials via a callable returning `SecretStr`, never a literal) is
   shown in the docs, not executed in tests.
